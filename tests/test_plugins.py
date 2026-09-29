import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from smc_bridge import plugins
from smc_bridge.bridge import Bridge, KEY_NOTES
from smc_bridge.config import Config, KeyAction, Mapping, Route, load, save, validate
from smc_bridge.daemon import PluginSettingsState
from smc_bridge.plugins import Plugin, PluginHost, PluginSettings, load_settings, set_enabled


def mixer(name, volume_cc, pan_cc=None):
    """A strip whose fader (and encoder, if given a CC) go to jack_mixer."""
    return Mapping(name, Route.midi(volume_cc), None if pan_cc is None else Route.midi(pan_cc))


def plugin_strip(name, plugin, target):
    """A strip whose fader and encoder both go to one plugin target."""
    return Mapping(name, Route.to_plugin(plugin, target), Route.to_plugin(plugin, target))


def wait_for(condition, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Recorder(Plugin):
    instances = []

    def __init__(self, settings):
        super().__init__(settings)
        self.events = []
        self.stopped = threading.Event()
        Recorder.instances.append(self)

    def on_key(self, target, pressed):
        self.events.append(("key", target, pressed))

    def on_fader(self, target, level):
        self.events.append(("fader", target, level))

    def stop(self):
        self.stopped.set()


class BrokenStart(Plugin):
    def start(self):
        raise ConnectionRefusedError("remote end is down")


class AlwaysFails(Plugin):
    def on_key(self, target, pressed):
        raise RuntimeError("bad")


class Entry:
    """Stands in for an importlib.metadata entry point."""
    def __init__(self, cls):
        self.cls = cls
        self.loads = 0

    def load(self):
        self.loads += 1
        return self.cls


def enabled(**options):
    return PluginSettings(True, options)


class SettingsFileTests(unittest.TestCase):
    def test_enable_creates_then_toggles_keeping_settings_and_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugins.ini"
            set_enabled(path, "example", True)
            self.assertEqual(load_settings(path), {"example": PluginSettings(True, {})})
            with path.open("a") as stream:
                stream.write("# the host machine\nhost = example.test\nport = 1234\n")
            set_enabled(path, "example", False)
            set_enabled(path, "other", True)
            text = path.read_text()
            self.assertIn("# the host machine", text)
            self.assertEqual(load_settings(path), {
                "example": PluginSettings(False, {"host": "example.test", "port": "1234"}),
                "other": PluginSettings(True, {}),
            })

    def test_enable_adds_the_flag_to_a_section_without_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugins.ini"
            path.write_text("[example]\nhost = example.test\n")
            path.chmod(0o600)
            set_enabled(path, "example", True)
            self.assertEqual(load_settings(path), {"example": enabled(host="example.test")})

    def test_missing_file_means_nothing_enabled(self):
        self.assertEqual(load_settings(Path("/nonexistent/plugins.ini")), {})

    def test_invalid_names_and_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugins.ini"
            with self.assertRaises(ValueError):
                set_enabled(path, "Bad Name", True)
            for text in ("[Bad Name]\nenabled = yes\n", "[example]\nenabled = maybe\n", "junk"):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_settings(path)

    def test_file_others_can_write_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugins.ini"
            set_enabled(path, "example", True)
            path.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "not writable by others"):
                load_settings(path)


class HostTests(unittest.TestCase):
    def setUp(self):
        Recorder.instances = []

    def host(self, **classes):
        entries = {name: Entry(cls) for name, cls in classes.items()}
        return PluginHost(discover=lambda: entries), entries

    def test_enabled_plugin_gets_settings_and_events_and_is_stopped(self):
        host, _ = self.host(fake=Recorder)
        host.configure({"fake": enabled(host="example.test")})
        self.assertTrue(wait_for(lambda: Recorder.instances))
        plugin = Recorder.instances[0]
        self.assertEqual(plugin.settings, {"host": "example.test"})
        host.key("fake", "button", True)
        host.fader("fake", "level", 0.5)
        host.key("nothing", "button", True)
        self.assertTrue(wait_for(lambda: len(plugin.events) == 2))
        self.assertEqual(plugin.events, [("key", "button", True), ("fader", "level", 0.5)])
        host.stop()
        self.assertTrue(plugin.stopped.is_set())
        self.assertEqual(host.status(), {})

    def test_disabled_plugins_are_never_imported(self):
        host, entries = self.host(fake=Recorder)
        host.configure({"fake": PluginSettings(False, {})})
        self.assertEqual(entries["fake"].loads, 0)
        self.assertEqual(host.status(), {})

    def test_safe_mode_starts_nothing(self):
        entries = {"fake": Entry(Recorder)}
        host = PluginHost(enabled=False, discover=lambda: entries)
        host.configure({"fake": enabled()})
        self.assertEqual(entries["fake"].loads, 0)

    def test_disabling_or_changing_settings_stops_or_restarts(self):
        host, _ = self.host(fake=Recorder)
        host.configure({"fake": enabled(port="1")})
        self.assertTrue(wait_for(lambda: len(Recorder.instances) == 1))
        host.configure({"fake": enabled(port="2")})
        self.assertTrue(wait_for(lambda: len(Recorder.instances) == 2))
        self.assertTrue(Recorder.instances[0].stopped.is_set())
        host.configure({"fake": PluginSettings(False, {"port": "2"})})
        self.assertTrue(Recorder.instances[1].stopped.is_set())
        self.assertEqual(host.status(), {})

    def test_plugin_that_fails_to_start_stays_off_until_retried(self):
        host, entries = self.host(fake=BrokenStart)
        with self.assertLogs("smc_bridge.plugins", level="ERROR"):
            host.configure({"fake": enabled()})
            self.assertTrue(wait_for(lambda: host.status() == {"fake": "failed"}))
        host.configure({"fake": enabled()})
        self.assertEqual(entries["fake"].loads, 1)
        with self.assertLogs("smc_bridge.plugins", level="ERROR"):
            host.configure({"fake": enabled()}, retry_failed=True)
            self.assertTrue(wait_for(lambda: entries["fake"].loads == 2 and host.status() == {"fake": "failed"}))
        host.stop()

    def test_repeated_failures_turn_the_plugin_off(self):
        host, _ = self.host(fake=AlwaysFails)
        host.configure({"fake": enabled()})
        with self.assertLogs("smc_bridge.plugins", level="ERROR") as logs:
            for _ in range(plugins.MAX_FAILURES + 3):
                host.key("fake", "button", True)
            self.assertTrue(wait_for(lambda: host.status() == {"fake": "failed"}))
        self.assertTrue(any("turning it off" in line for line in logs.output))
        host.stop()

    def test_enabled_but_not_installed_is_logged(self):
        host, _ = self.host()
        with self.assertLogs("smc_bridge.plugins", level="ERROR"):
            host.configure({"fake": enabled()})
        self.assertEqual(host.status(), {})


class RoutingTests(unittest.TestCase):
    def test_plugin_strip_sends_fader_and_encoder_to_plugin_not_mixer(self):
        mappings = [Mapping() for _ in range(8)]
        mappings[7] = plugin_strip("Channel A", "example", "target_a")
        bridge = Bridge(mappings)
        self.assertIsNone(bridge.on_fader(7, 0))
        self.assertEqual(bridge.on_plugin_fader(7, 8191), ("example", "target_a", 1.0))
        self.assertEqual(bridge.on_plugin_fader(7, -9000), ("example", "target_a", 0.0))
        self.assertEqual(bridge.on_plugin_encoder(23, 1), ("example", "target_a", 1))
        self.assertEqual(bridge.on_plugin_encoder(23, 65), ("example", "target_a", -1))
        self.assertIsNone(bridge.on_plugin_encoder(23, 64))
        self.assertIsNone(bridge.on_plugin_fader(0, 0))
        self.assertIsNone(bridge.on_plugin_encoder(16, 1))

    def test_fader_to_mixer_and_encoder_to_plugin_on_one_strip(self):
        mappings = [Mapping() for _ in range(8)]
        mappings[0] = Mapping("Mixed strip", Route.midi(19), Route.to_plugin("example", "target_a"))
        mappings[1] = Mapping("AF", Route.to_plugin("example", "level"), Route.midi(21))
        bridge = Bridge(mappings)
        self.assertEqual(bridge.on_fader(0, 8191), (19, 127))
        self.assertIsNone(bridge.on_plugin_fader(0, 8191))
        self.assertIsNone(bridge.on_encoder(16, 1))
        self.assertEqual(bridge.on_plugin_encoder(16, 1), ("example", "target_a", 1))
        self.assertIsNone(bridge.on_fader(1, 8191))
        self.assertEqual(bridge.on_plugin_fader(1, 8191), ("example", "level", 1.0))
        self.assertEqual(bridge.on_encoder(17, 1)[0], 21)
        self.assertIsNone(bridge.on_plugin_encoder(17, 1))

    def test_plugin_key_reports_press_and_release(self):
        bridge = Bridge([Mapping() for _ in range(8)], {"transport.record": KeyAction("plugin", plugin="example", target="button")})
        note = KEY_NOTES["transport.record"]
        self.assertEqual(bridge.on_key(note, 127), ("plugin", "example", "button", True))
        self.assertEqual(bridge.on_key(note, 0), ("plugin", "example", "button", False))


class ConfigTests(unittest.TestCase):
    def test_plugin_routes_roundtrip(self):
        mappings = [mixer("Mic", 11, 12), plugin_strip("Channel A", "example", "target_a")] + [Mapping() for _ in range(6)]
        config = Config(mappings, {"transport.record": KeyAction("plugin", plugin="example", target="button")})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            save(path, config)
            self.assertEqual(load(path), config)

    def test_invalid_plugin_routes(self):
        for mapping in (
            Mapping("Channel", Route("plugin", target="target_a")),
            plugin_strip("Channel", "Example", "target_a"),
            plugin_strip("Channel", "example", " "),
            plugin_strip("", "example", "target_a"),
            Mapping("Channel", None, Route("wire", target="target_a")),
        ):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate(Config([mapping] + [Mapping() for _ in range(7)]))
        for action in (KeyAction("plugin", plugin="", target="button"), KeyAction("plugin", plugin="example", target="a\nb")):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate(Config(keys={"transport.play": action}))


class DaemonTests(unittest.TestCase):
    def test_plugin_settings_follow_the_file_and_a_missing_file_turns_all_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugins.ini"
            state = PluginSettingsState(path)
            state.refresh(initial=True)
            self.assertEqual(state.config, {})
            set_enabled(path, "example", True)
            self.assertTrue(state.refresh())
            self.assertEqual(state.config, {"example": enabled()})
            path.write_text("junk")
            with self.assertLogs(level="ERROR"):
                self.assertFalse(state.refresh())
            self.assertEqual(state.config, {"example": enabled()})
            path.unlink()
            self.assertTrue(state.refresh())
            self.assertEqual(state.config, {})


class CommandLineTests(unittest.TestCase):
    def run_cli(self, directory, *args):
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        return subprocess.run(
            [sys.executable, "-m", "smc_bridge", "--config", str(Path(directory) / "mappings.ini"), *args],
            capture_output=True, text=True, env=environment, timeout=30,
        )

    def test_enable_requires_an_installed_plugin_but_disable_and_list_work(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory, "--enable-plugin", "not-installed")
            self.assertEqual(result.returncode, 1)
            self.assertIn("not installed", result.stderr)
            result = self.run_cli(directory, "--disable-plugin", "no-such-plugin", "--list-plugins")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r"no-such-plugin\s+disabled\s+not installed")
            self.assertEqual(load_settings(Path(directory) / "plugins.ini"), {"no-such-plugin": PluginSettings(False, {})})


if __name__ == "__main__":
    unittest.main()
