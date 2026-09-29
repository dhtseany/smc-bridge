"""Headless lifecycle, configuration owner, and (when pyalsa is available) live MIDI transport."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import stat
import tempfile
import threading

from .config import STRIP_CONTROLS, load
from .plugins import PluginHost, load_settings, settings_path

LOG = logging.getLogger(__name__)

try:
    from .transport import Transport
except ImportError:
    Transport = None


class InstanceLock:
    """One background bridge per user, regardless of configuration path."""
    def __enter__(self):
        base = os.environ.get("XDG_RUNTIME_DIR")
        directory = Path(base) / "smc_bridge" if base else Path(tempfile.gettempdir()) / f"smc_bridge-{os.getuid()}"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError(f"Runtime directory must be private and owned by you: {directory}")
        fd = os.open(directory / "bridge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise RuntimeError("A background bridge is already running for this user.") from None
        return self

    def __exit__(self, *_):
        # Keep the inode: unlinking a lock file permits races with other starters.
        self.stream.close()


class ConfigurationState:
    def __init__(self, path):
        self.path = path
        self.config = None
        self.last_signature = None

    def refresh(self, initial=False, force=False):
        try:
            info = self.path.stat()
            signature = (info.st_ino, info.st_mtime_ns, info.st_size)
        except OSError as error:
            signature = (type(error).__name__, str(error))
        if not initial and not force and signature == self.last_signature:
            return False
        self.last_signature = signature
        try:
            config = self._load(initial)
        except (OSError, ValueError) as error:
            if initial:
                raise
            LOG.error("Configuration reload rejected: %s", error)
            return False
        self.config = config
        LOG.info("Loaded %s from %s", self._describe(config), self.path)
        return True

    def _load(self, initial):
        # Missing files are acceptable only at first launch. Deleting the
        # file while running must not silently clear all active mappings.
        if not initial and not self.path.exists():
            raise ValueError("Configuration is missing; retaining the last valid mappings.")
        return load(self.path)

    def _describe(self, config):
        routes = [getattr(m, c) for m in config.mappings for c in STRIP_CONTROLS]
        to_mixer = sum(r is not None and r.kind == "midi" for r in routes)
        to_plugins = sum(r is not None and r.kind == "plugin" for r in routes)
        return f"{to_mixer} faders/encoders to jack_mixer, {to_plugins} to plugins and {len(config.keys)} key actions"


class PluginSettingsState(ConfigurationState):
    """plugins.ini. Deleting it is a way to turn every plugin off, so a missing file is fine."""

    def __init__(self, path):
        super().__init__(path)
        self.config = {}

    def _load(self, initial):
        return load_settings(self.path)

    def _describe(self, settings):
        enabled = sorted(name for name, s in settings.items() if s.enabled)
        return f"plugin settings (enabled: {', '.join(enabled) or 'none'})"


def run(path, plugins=True):
    """Run the bridge until signalled. `plugins`=False is safe mode: no plugin is loaded."""
    level = logging.DEBUG if os.environ.get("SMC_BRIDGE_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    stop = threading.Event()
    reload_requested = threading.Event()
    old_handlers = {}
    try:
        with InstanceLock():
            state = ConfigurationState(path)
            state.refresh(initial=True)
            host = PluginHost(enabled=plugins)
            plugin_state = PluginSettingsState(settings_path(path))
            if plugins:
                try:
                    plugin_state.refresh(initial=True)
                except (OSError, ValueError) as error:
                    # A bad plugins.ini must not take the mixer down with it.
                    LOG.error("Plugins disabled until plugins.ini is fixed: %s", error)
                host.configure(plugin_state.config)
            else:
                LOG.warning("Safe mode: plugins are turned off (--no-plugins or SMC_BRIDGE_NO_PLUGINS)")
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                old_handlers[number] = signal.getsignal(number)
                signal.signal(number, lambda signum, frame: reload_requested.set() if signum == signal.SIGHUP else stop.set())
            transport = None
            if Transport is not None:
                try:
                    transport = Transport(state.config, plugins=host)
                    transport.start()
                    LOG.warning(
                        "MIDI transport active: client %r (id %d), ports SMC In/Out, Mixer In/Out. PID %d",
                        transport.seq.clientname, transport.client_id, os.getpid(),
                    )
                except Exception as error:  # pyalsa present but ALSA unavailable (no /dev/snd, no permission, ...)
                    LOG.error("MIDI transport unavailable (%s); falling back to setup preview.", error)
                    transport = None
            if transport is None:
                LOG.warning("Headless setup preview running; no MIDI ports or motor control. PID %d", os.getpid())
            try:
                while not stop.wait(1):
                    force = reload_requested.is_set()
                    reload_requested.clear()
                    if state.refresh(force=force) and transport is not None:
                        transport.update_config(state.config)
                    if plugins and (plugin_state.refresh(force=force) or force):
                        host.configure(plugin_state.config, retry_failed=force)
            finally:
                if transport is not None:
                    transport.stop()
                host.stop()
            LOG.info("Background process stopped cleanly")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        LOG.error("Cannot start background process: %s", error)
        return 1
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
