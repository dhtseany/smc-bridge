"""Optional plugins: extra destinations for strips and keys, such as a radio.

A plugin is a separately installed Python package that registers an entry
point in the "smc_bridge.plugins" group, naming a Plugin subclass (or any
class with the same methods):

    [project.entry-points."smc_bridge.plugins"]
    hrdctl = "smc_bridge_hrdctl:HrdPlugin"

Plugins are off until enabled in plugins.ini, next to the mappings file:

    [hrdctl]
    enabled = yes
    host = 192.168.1.20
    port = 7809

Everything in a plugin's section except `enabled` is handed to it as its
settings. A disabled plugin is never imported.

Each enabled plugin runs on its own thread, fed through a bounded queue, so
a slow network call or a hung connection never stalls MIDI; if it falls
behind, events for it are dropped. A plugin that fails to start, or fails
MAX_FAILURES events in a row, is turned off until it is re-enabled, its
settings change, the daemon is reloaded (SIGHUP) or restarted.
"""
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass, field
import importlib.metadata
import logging
from pathlib import Path
import queue
import re
import threading

from .config import PLUGIN_NAME, check_private, write_atomic

LOG = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "smc_bridge.plugins"
SETTINGS_FILE = "plugins.ini"
MAX_FAILURES = 5
QUEUE_SIZE = 256
STOP_TIMEOUT = 3
_SECTION = re.compile(r"\s*\[([^\]]*)\]")
_ENABLED = re.compile(r"\s*enabled\s*[=:]", re.IGNORECASE)


class Plugin:
    """Base class for plugins. Every method runs on the plugin's own thread.

    Override what you need; the defaults do nothing. Exceptions are logged,
    never raised into the bridge. Handle transient trouble (the remote end
    restarting, say) inside the plugin, by reconnecting: repeated failures
    turn the plugin off.
    """

    def __init__(self, settings):
        """`settings`: the plugin's plugins.ini section, as str -> str, minus `enabled`."""
        self.settings = settings

    def start(self):
        """Connect or otherwise get ready. Raising here leaves the plugin off."""

    def stop(self):
        """Disconnect. Called once, after the last event."""

    def on_key(self, target, pressed):
        """A key routed to `target` went down (pressed=True) or up (False)."""

    def on_fader(self, target, level):
        """A fader routed to `target` moved to `level`, 0.0 (bottom) to 1.0 (top)."""

    def on_encoder(self, target, delta):
        """An encoder routed to `target` turned `delta` steps (negative is the other way)."""


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool = False
    options: dict = field(default_factory=dict)


def settings_path(config_path):
    """plugins.ini lives beside the mappings file it goes with."""
    return Path(config_path).parent / SETTINGS_FILE


def installed():
    """Installed plugins by name, as entry points (nothing is imported)."""
    return {entry.name: entry for entry in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}


def _parse(text):
    parser = ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
        settings = {}
        for name in parser.sections():
            if not PLUGIN_NAME.match(name):
                raise ValueError(f"[{name}]: plugin names are lowercase letters, digits, - and _.")
            section = parser[name]
            enabled = section.getboolean("enabled", fallback=False)
            settings[name] = PluginSettings(enabled, {key: value for key, value in section.items() if key != "enabled"})
        return settings
    except (ConfigError, ValueError) as error:
        raise ValueError(f"Invalid plugin configuration: {error}") from error


def load_settings(path):
    """Read plugins.ini; a missing file means no plugins are enabled."""
    if not path.exists():
        return {}
    settings = _parse(path.read_text(encoding="utf-8"))
    check_private(path, "to use plugins")
    return settings


def set_enabled(path, name, enabled):
    """Turn plugin `name` on or off in plugins.ini, keeping its settings and any comments."""
    if not PLUGIN_NAME.match(name):
        raise ValueError("Plugin names are lowercase letters, digits, - and _.")
    if path.exists():
        check_private(path, "to use plugins")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []
    value = f"enabled = {'yes' if enabled else 'no'}\n"
    section = header = None
    for index, line in enumerate(lines):
        match = _SECTION.match(line)
        if match:
            section = match.group(1).strip()
            if section == name and header is None:
                header = index
        elif section == name and _ENABLED.match(line):
            lines[index] = value
            break
    else:
        if header is not None:
            lines.insert(header + 1, value)
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines += (["\n"] if lines else []) + [f"[{name}]\n", value]
    text = "".join(lines)
    _parse(text)
    write_atomic(path, lambda stream: stream.write(text))


class _Runner:
    """One enabled plugin: its thread, its queue and its failure count."""

    def __init__(self, name, entry, settings):
        self.name = name
        self.settings = settings
        self.failed = False
        self._entry = entry
        self._queue = queue.Queue(QUEUE_SIZE)
        self._stopping = threading.Event()
        self._dropping = False
        self._thread = threading.Thread(target=self._run, name=f"smc-plugin-{name}", daemon=True)

    def start(self):
        self._thread.start()

    def post(self, method, *args):
        if self.failed or self._stopping.is_set():
            return
        try:
            self._queue.put_nowait((method, args))
            self._dropping = False
        except queue.Full:
            if not self._dropping:
                LOG.warning("Plugin %s is not keeping up; dropping its events", self.name)
                self._dropping = True

    def stop(self):
        self._stopping.set()
        self._thread.join(timeout=STOP_TIMEOUT)
        if self._thread.is_alive():
            LOG.error("Plugin %s did not stop within %d s; abandoning its thread", self.name, STOP_TIMEOUT)

    def _run(self):
        # Importing happens here too, so a slow or broken import can't hold
        # up the daemon either.
        try:
            plugin = self._entry.load()(dict(self.settings.options))
            plugin.start()
        except Exception:
            LOG.exception("Plugin %s failed to start; it stays off until re-enabled or reloaded", self.name)
            self.failed = True
            return
        LOG.info("Plugin %s started", self.name)
        failures = 0
        try:
            while not self._stopping.is_set():
                try:
                    method, args = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    getattr(plugin, method)(*args)
                    failures = 0
                except Exception:
                    failures += 1
                    LOG.exception("Plugin %s failed in %s%r", self.name, method, args)
                    if failures >= MAX_FAILURES:
                        LOG.error(
                            "Plugin %s failed %d times in a row; turning it off until it is re-enabled or reloaded",
                            self.name, failures,
                        )
                        self.failed = True
                        break
        finally:
            try:
                plugin.stop()
            except Exception:
                LOG.exception("Plugin %s failed to stop cleanly", self.name)
            LOG.info("Plugin %s stopped", self.name)


class PluginHost:
    """Starts and stops enabled plugins, and passes routed events to them.

    Posting an event never blocks and never raises, so the MIDI thread can
    call key(), fader() and encoder() directly.
    """

    def __init__(self, enabled=True, discover=installed):
        self.enabled = enabled
        self._discover = discover
        self._runners = {}

    def configure(self, settings, retry_failed=False):
        """Start newly enabled plugins, stop disabled ones, restart any whose settings changed.

        A plugin that turned itself off stays off unless `retry_failed`.
        """
        wanted = {name: s for name, s in settings.items() if s.enabled} if self.enabled else {}
        for name, runner in list(self._runners.items()):
            if wanted.get(name) != runner.settings or (retry_failed and runner.failed):
                del self._runners[name]
                runner.stop()
        missing = set(wanted) - set(self._runners)
        if not missing:
            return
        available = self._discover()
        for name in sorted(missing):
            if name not in available:
                LOG.error("Plugin %s is enabled in plugins.ini but not installed", name)
                continue
            runner = _Runner(name, available[name], wanted[name])
            self._runners[name] = runner
            runner.start()

    def stop(self):
        self.configure({})

    def status(self):
        """{name: "running" | "failed"} for enabled, installed plugins."""
        return {name: "failed" if runner.failed else "running" for name, runner in self._runners.items()}

    def key(self, plugin, target, pressed):
        self._post(plugin, "on_key", target, pressed)

    def fader(self, plugin, target, level):
        self._post(plugin, "on_fader", target, level)

    def encoder(self, plugin, target, delta):
        self._post(plugin, "on_encoder", target, delta)

    def _post(self, plugin, method, *args):
        runner = self._runners.get(plugin)
        if runner is None:
            LOG.debug("Plugin %s is not running; ignoring %s%r", plugin, method, args)
            return
        runner.post(method, *args)
