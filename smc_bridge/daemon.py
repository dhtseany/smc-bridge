"""Headless lifecycle, configuration owner, and (when pyalsa is available) live MIDI transport."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import stat
import tempfile
import threading

from .config import load

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
        self.mappings = None
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
            # Missing files are acceptable only at first launch. Deleting the
            # file while running must not silently clear all active mappings.
            if not initial and not self.path.exists():
                raise ValueError("Configuration is missing; retaining the last valid mappings.")
            mappings = load(self.path)
        except (OSError, ValueError) as error:
            if initial:
                raise
            LOG.error("Configuration reload rejected: %s", error)
            return False
        self.mappings = mappings
        LOG.info("Loaded %d assigned strips from %s", sum(m.assigned for m in mappings), self.path)
        return True


def run(path):
    level = logging.DEBUG if os.environ.get("SMC_BRIDGE_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    stop = threading.Event()
    reload_requested = threading.Event()
    old_handlers = {}
    try:
        with InstanceLock():
            state = ConfigurationState(path)
            state.refresh(initial=True)
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                old_handlers[number] = signal.getsignal(number)
                signal.signal(number, lambda signum, frame: reload_requested.set() if signum == signal.SIGHUP else stop.set())
            transport = None
            if Transport is not None:
                try:
                    transport = Transport(state.mappings)
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
                        transport.update_mappings(state.mappings)
            finally:
                if transport is not None:
                    transport.stop()
            LOG.info("Background process stopped cleanly")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        LOG.error("Cannot start background process: %s", error)
        return 1
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
