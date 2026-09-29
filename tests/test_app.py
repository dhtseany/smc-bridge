import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QCheckBox
from smc_bridge import plugins
from smc_bridge.config import Config, KeyAction, Mapping, load, save, validate
from smc_bridge.gui import PluginsDialog, Window


def config(*mappings, **keys):
    """Config with the given leading strip mappings and key actions (key ids use __ for '.')."""
    return Config(list(mappings) + [Mapping() for _ in range(8 - len(mappings))],
                  {key.replace("__", "."): action for key, action in keys.items()})


V1_FILE = """[app]
format_version = 1

[strip1]
name = Desk Mic
volume_cc = 11
pan_cc = 12
mute_cc = 13
solo_cc =
""" + "".join(f"\n[strip{i}]\nname =\nvolume_cc =\npan_cc =\nmute_cc =\nsolo_cc =\n" for i in range(2, 9))


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_conflict_preserves_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "mappings.ini"
            original_config = config(Mapping("Desk Mic 100%", 11, 12))
            save(path, original_config)
            self.assertEqual(load(path), original_config)
            original = path.read_bytes()
            original_config.mappings[1] = Mapping("PC", 12, 15)
            with self.assertRaisesRegex(ValueError, "already used"):
                save(path, original_config)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_mappings(self):
        for mapping in (Mapping("Mic", 128, 1), Mapping("Mic", 1, None), Mapping("", 1, 2), Mapping("Mic", 1, 1)):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate(config(mapping))

    def test_every_key_action_kind_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            expected = config(
                Mapping("Desk Mic", 11, 12),
                strip1__mute=KeyAction("midi", 13),
                transport__record=KeyAction("midi", 40, mode="momentary"),
                transport__play=KeyAction("media", media="play_pause"),
                strip3__select=KeyAction("command", command="notify-send 'SMC 100%' \"a; b\" | cat"),
            )
            save(path, expected)
            self.assertEqual(load(path), expected)

    def test_format_1_mute_solo_migrate_to_toggle_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            path.write_text(V1_FILE)
            self.assertEqual(load(path), config(Mapping("Desk Mic", 11, 12), strip1__mute=KeyAction("midi", 13)))

    def test_invalid_key_actions(self):
        for keys in (
            {"strip9.mute": KeyAction("midi", 13)},
            {"strip1.mute": KeyAction("midi", None)},
            {"strip1.mute": KeyAction("midi", 128)},
            {"strip1.mute": KeyAction("midi", 13, mode="latch")},
            {"transport.play": KeyAction("media", media="shuffle")},
            {"transport.play": KeyAction("command", command="  ")},
            {"transport.play": KeyAction("command", command="a\nb")},
            {"transport.play": KeyAction("launch")},
        ):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                validate(Config(config().mappings, keys))

    def test_key_cc_conflicts_with_strip_and_other_keys(self):
        with self.assertRaisesRegex(ValueError, "already used"):
            validate(config(Mapping("Desk Mic", 11, 12), strip1__mute=KeyAction("midi", 12)))
        with self.assertRaisesRegex(ValueError, "already used"):
            validate(config(strip1__mute=KeyAction("midi", 20), strip1__solo=KeyAction("midi", 20)))

    def test_command_keys_require_a_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            save(path, config(transport__up=KeyAction("command", command="true")))
            self.assertEqual(load(path).keys["transport.up"].command, "true")
            path.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "not writable by others"):
                load(path)
            path.chmod(0o600)
            Path(directory).chmod(0o777)
            try:
                with self.assertRaisesRegex(ValueError, "not writable by others"):
                    load(path)
            finally:
                Path(directory).chmod(0o700)

    def test_plugin_routes_require_a_private_file(self):
        for routed in (
            config(Mapping("VFO A", plugin="hrdctl", target="vfo_a")),
            config(transport__record=KeyAction("plugin", plugin="hrdctl", target="ptt")),
        ):
            with self.subTest(config=routed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "mappings.ini"
                save(path, routed)
                self.assertEqual(load(path), routed)
                path.chmod(0o666)
                with self.assertRaisesRegex(ValueError, "not writable by others to send controls to plugins"):
                    load(path)
                path.chmod(0o600)
                Path(directory).chmod(0o777)
                try:
                    with self.assertRaisesRegex(ValueError, "not writable by others"):
                        load(path)
                finally:
                    Path(directory).chmod(0o700)

    def test_non_command_keys_load_from_shared_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            save(path, config(transport__play=KeyAction("media", media="next")))
            path.chmod(0o666)
            self.assertEqual(load(path).keys["transport.play"].media, "next")


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mappings.ini"
        self.window = Window(self.path)

    def tearDown(self):
        self.window.dirty = self.window.draft_dirty = False
        self.window.close()
        self.temp.cleanup()

    def test_edit_save_restart_and_clear(self):
        window = self.window
        window.enabled.setChecked(True)
        window.name.setText("Desk Mic")
        window.volume.setValue(11)
        window.pan.setValue(12)
        window.save_button.click()
        self.assertEqual(load(self.path).mappings[0], Mapping("Desk Mic", 11, 12))
        restored = Window(self.path)
        self.assertEqual(restored.name.text(), "Desk Mic")
        self.assertTrue(restored.enabled.isChecked())
        restored.close()
        window.enabled.setChecked(False)
        window.save_button.click()
        self.assertEqual(load(self.path).mappings[0], Mapping())

    def test_invalid_edit_blocks_navigation(self):
        window = self.window
        window.enabled.setChecked(True)
        window.name.setText("Mic")
        window.strips[1].button.click()
        self.assertEqual(window.selected, 0)
        self.assertIn("Choose both", window.error.text())
        window.volume.setValue(11)
        window.pan.setValue(12)
        window.strips[1].button.click()
        self.assertEqual(window.selected, 1)
        window.enabled.setChecked(True)
        window.name.setText("PC")
        window.volume.setValue(11)
        window.pan.setValue(14)
        self.assertFalse(window.apply())
        self.assertIn("already used", window.error.text())
        self.assertFalse(window.config.mappings[1].assigned)

    def _assign(self, window, strip_index, name, volume_cc, pan_cc):
        window.select(strip_index)
        window.enabled.setChecked(True)
        window.name.setText(name)
        window.volume.setValue(volume_cc)
        window.pan.setValue(pan_cc)
        self.assertTrue(window.apply())

    def _activate(self, window):
        window.show()
        window.activateWindow()
        self.app.processEvents()

    def test_strip_key_click_selects_key_and_its_strip(self):
        window = self.window
        self._activate(window)
        window.strips[2].key_buttons["strip3.mute"].click()
        self.assertEqual((window.selected, window.selected_key), (2, "strip3.mute"))
        self.assertEqual(window.pages.currentIndex(), 1)
        self.assertTrue(window.action.hasFocus())

    def test_every_key_can_be_given_each_kind_of_action_and_saved(self):
        window = self.window
        window.key_buttons["strip2.mute"].click()
        window.action.setCurrentIndex(window.action.findData("midi"))
        window.key_cc.setValue(30)
        window.key_buttons["transport.record"].click()  # navigating applies the draft
        window.action.setCurrentIndex(window.action.findData("midi"))
        window.key_cc.setValue(31)
        window.mode.setCurrentIndex(window.mode.findData("momentary"))
        window.key_buttons["transport.play"].click()
        window.action.setCurrentIndex(window.action.findData("media"))
        window.media.setCurrentIndex(window.media.findData("next"))
        window.key_buttons["strip8.select"].click()
        window.action.setCurrentIndex(window.action.findData("command"))
        window.command.setText("notify-send hi")
        self.assertTrue(window.save())
        self.assertEqual(load(self.path).keys, {
            "strip2.mute": KeyAction("midi", 30),
            "transport.record": KeyAction("midi", 31, mode="momentary"),
            "transport.play": KeyAction("media", media="next"),
            "strip8.select": KeyAction("command", command="notify-send hi"),
        })
        self.assertTrue(window.key_buttons["transport.play"].property("mapped"))
        self.assertFalse(window.key_buttons["transport.up"].property("mapped"))

    def test_strip_and_key_can_be_sent_to_a_plugin(self):
        window = self.window
        window.show()
        window.select(7)
        window.enabled.setChecked(True)
        window.name.setText("VFO A")
        window.route.setCurrentIndex(window.route.findData("plugin"))
        self.assertFalse(window.strip_form.isRowVisible(window.volume))
        self.assertTrue(window.strip_form.isRowVisible(window.strip_target))
        window.strip_plugin.setCurrentText("hrdctl")
        window.strip_target.setText("vfo_a")
        window.key_buttons["transport.record"].click()
        window.action.setCurrentIndex(window.action.findData("plugin"))
        window.key_plugin.setCurrentText("hrdctl")
        window.key_target.setText("ptt")
        self.assertTrue(window.save())
        saved = load(self.path)
        self.assertEqual(saved.mappings[7], Mapping("VFO A", plugin="hrdctl", target="vfo_a"))
        self.assertEqual(saved.keys, {"transport.record": KeyAction("plugin", plugin="hrdctl", target="ptt")})
        self.assertEqual(window.strips[7].mapping_label.text(), "hrdctl · vfo_a")
        window.select(7)
        self.assertEqual(window.route.currentData(), "plugin")
        self.assertEqual(window.strip_plugin.currentText(), "hrdctl")

    def test_plugins_dialog_toggles_plugins_ini(self):
        settings = Path(self.temp.name) / "plugins.ini"
        plugins.set_enabled(settings, "hrdctl", True)
        dialog = PluginsDialog(settings)
        box = next(box for box in dialog.findChildren(QCheckBox) if box.text().startswith("hrdctl"))
        self.assertTrue(box.isChecked())
        box.setChecked(False)
        self.assertEqual(plugins.load_settings(settings), {"hrdctl": plugins.PluginSettings(False, {})})
        # Not installed, so once off it can't be turned back on from here.
        dialog.close()
        self.assertFalse(PluginsDialog(settings).findChildren(QCheckBox)[0].isEnabled())

    def test_only_the_chosen_actions_fields_are_shown(self):
        window = self.window
        window.show()
        window.key_buttons["transport.up"].click()
        for kind, visible in (("", set()), ("midi", {window.key_cc, window.mode}), ("media", {window.media}), ("command", {window.command})):
            window.action.setCurrentIndex(window.action.findData(kind))
            with self.subTest(kind=kind):
                shown = {w for w in (window.key_cc, window.mode, window.media, window.command) if window.key_form.isRowVisible(w)}
                self.assertEqual(shown, visible)

    def test_invalid_key_blocks_navigation_and_clearing_removes_it(self):
        window = self.window
        self._assign(window, 0, "Desk Mic", 11, 12)
        window.key_buttons["strip1.mute"].click()
        window.action.setCurrentIndex(window.action.findData("midi"))
        window.key_buttons["strip1.solo"].click()
        self.assertEqual(window.selected_key, "strip1.mute")
        self.assertIn("Choose the CC", window.error.text())
        window.key_cc.setValue(12)
        self.assertFalse(window.apply())
        self.assertIn("already used", window.error.text())
        window.key_cc.setValue(13)
        window.strips[1].button.click()
        self.assertEqual((window.selected, window.selected_key), (1, None))
        self.assertEqual(window.config.keys["strip1.mute"], KeyAction("midi", 13))
        window.key_buttons["strip1.mute"].click()
        window.action.setCurrentIndex(window.action.findData(""))
        self.assertTrue(window.apply())
        self.assertNotIn("strip1.mute", window.config.keys)

    def test_corrupt_file_is_protected(self):
        self.path.write_text("not an ini file")
        broken = Window(self.path)
        self.assertFalse(broken.save_button.isEnabled())
        self.assertFalse(broken.save())
        self.assertEqual(self.path.read_text(), "not an ini file")
        broken.close()


if __name__ == "__main__":
    unittest.main()
