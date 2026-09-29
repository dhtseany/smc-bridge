import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QCheckBox
from smc_bridge import plugins
from smc_bridge.config import Config, KeyAction, Mapping, Route, load, save, validate
from smc_bridge.gui import Control, PluginsDialog, Window


def mixer(name, volume_cc, pan_cc=None):
    """A strip whose fader (and encoder, if given a CC) go to jack_mixer."""
    return Mapping(name, Route.midi(volume_cc), None if pan_cc is None else Route.midi(pan_cc))


def plugin_strip(name, plugin, target):
    """A strip whose fader and encoder both go to one plugin target."""
    return Mapping(name, Route.to_plugin(plugin, target), Route.to_plugin(plugin, target))


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
            original_config = config(mixer("Desk Mic 100%", 11, 12))
            save(path, original_config)
            self.assertEqual(load(path), original_config)
            original = path.read_bytes()
            original_config.mappings[1] = mixer("PC", 12, 15)
            with self.assertRaisesRegex(ValueError, "already used"):
                save(path, original_config)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_mappings(self):
        for mapping in (mixer("Mic", 128, 1), Mapping("Mic", Route("pan", cc=1)), mixer("", 1, 2), mixer("Mic", 1, 1)):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate(config(mapping))

    def test_pan_cc_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            volume_only = config(mixer("FT-710 Rx", 19))
            save(path, volume_only)
            loaded = load(path)
            self.assertEqual(loaded, volume_only)
            self.assertIsNone(loaded.mappings[0].encoder)

    def test_format_3_strips_migrate_to_independent_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            path.write_text(
                "[app]\nformat_version = 3\n\n[strip1]\nname = Mic\nvolume_cc = 11\npan_cc = 12\n\n"
                "[strip2]\nname = VFO A\nvolume_cc =\npan_cc =\nplugin = hrdctl\ntarget = vfo_a\n"
                + "".join(f"\n[strip{i}]\nname =\nvolume_cc =\npan_cc =\n" for i in range(3, 9))
            )
            path.chmod(0o600)
            self.assertEqual(load(path), config(mixer("Mic", 11, 12), plugin_strip("VFO A", "hrdctl", "vfo_a")))

    def test_every_key_action_kind_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            expected = config(
                mixer("Desk Mic", 11, 12),
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
            self.assertEqual(load(path), config(mixer("Desk Mic", 11, 12), strip1__mute=KeyAction("midi", 13)))

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
            validate(config(mixer("Desk Mic", 11, 12), strip1__mute=KeyAction("midi", 12)))
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
            config(plugin_strip("VFO A", "hrdctl", "vfo_a")),
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

    def _route(self, editor, kind, cc=None, plugin="", target=""):
        editor.kind.setCurrentIndex(editor.kind.findData(kind))
        if cc is not None:
            editor.cc.setValue(cc)
        editor.plugin.setCurrentText(plugin)
        editor.target.setText(target)

    def test_edit_save_restart_and_clear(self):
        window = self.window
        window.name.setText("Desk Mic")
        self._route(window.fader, "midi", 11)
        self._route(window.encoder, "midi", 12)
        window.save_button.click()
        self.assertEqual(load(self.path).mappings[0], mixer("Desk Mic", 11, 12))
        restored = Window(self.path)
        self.assertEqual(restored.name.text(), "Desk Mic")
        self.assertEqual(restored.fader.kind.currentData(), "midi")
        self.assertEqual(restored.encoder.cc.value(), 12)
        restored.close()
        self._route(window.fader, "")
        self._route(window.encoder, "")
        window.save_button.click()
        self.assertEqual(load(self.path).mappings[0], Mapping())

    def test_invalid_edit_blocks_navigation(self):
        window = self.window
        window.name.setText("Mic")
        self._route(window.fader, "midi")
        window.strips[1].button.click()
        self.assertEqual(window.selected, 0)
        self.assertIn("Choose the CC number the fader sends", window.error.text())
        window.fader.cc.setValue(11)
        window.strips[1].button.click()
        self.assertEqual(window.selected, 1)
        window.name.setText("PC")
        self._route(window.encoder, "midi", 11)
        self.assertFalse(window.apply())
        self.assertIn("already used", window.error.text())
        self.assertFalse(window.config.mappings[1].used)

    def test_fader_and_encoder_are_routed_independently(self):
        window = self.window
        window.show()
        window.name.setText("FT-710 Rx")
        self._route(window.fader, "midi", 19)
        self._route(window.encoder, "plugin", plugin="hrdctl", target="vfo_a")
        self.assertTrue(window.fader.form.isRowVisible(window.fader.cc))
        self.assertFalse(window.fader.form.isRowVisible(window.fader.target))
        self.assertFalse(window.encoder.form.isRowVisible(window.encoder.cc))
        self.assertTrue(window.encoder.form.isRowVisible(window.encoder.target))
        self.assertTrue(window.apply())
        self.assertEqual(window.strips[0].mapping_label.text(), "CC 19 · hrdctl")
        window.select(1)
        window.name.setText("Spare")
        self._route(window.encoder, "midi", 20)
        window.save_button.click()
        saved = load(self.path)
        self.assertEqual(saved.mappings[0], Mapping("FT-710 Rx", Route.midi(19), Route.to_plugin("hrdctl", "vfo_a")))
        self.assertEqual(saved.mappings[1], Mapping("Spare", None, Route.midi(20)))
        self.assertEqual(window.strips[1].mapping_label.text(), "— · CC 20")

    def test_clicking_a_control_focuses_its_editor(self):
        window = self.window
        self._activate(window)
        window.strips[3].findChildren(Control)[1].click()  # the fader
        self.assertEqual(window.selected, 3)
        self.assertTrue(window.fader.kind.hasFocus())
        window.strips[3].findChildren(Control)[0].click()  # the encoder
        self.assertTrue(window.encoder.kind.hasFocus())

    def _assign(self, window, strip_index, name, volume_cc, pan_cc):
        window.select(strip_index)
        window.name.setText(name)
        self._route(window.fader, "midi", volume_cc)
        self._route(window.encoder, "midi", pan_cc)
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
        window.select(7)
        window.name.setText("VFO A")
        self._route(window.fader, "plugin", plugin="hrdctl", target="vfo_a")
        self._route(window.encoder, "plugin", plugin="hrdctl", target="vfo_a")
        window.key_buttons["transport.record"].click()
        window.action.setCurrentIndex(window.action.findData("plugin"))
        window.key_plugin.setCurrentText("hrdctl")
        window.key_target.setText("ptt")
        self.assertTrue(window.save())
        saved = load(self.path)
        self.assertEqual(saved.mappings[7], plugin_strip("VFO A", "hrdctl", "vfo_a"))
        self.assertEqual(saved.keys, {"transport.record": KeyAction("plugin", plugin="hrdctl", target="ptt")})
        self.assertEqual(window.strips[7].mapping_label.text(), "hrdctl · hrdctl")
        window.select(7)
        self.assertEqual(window.encoder.kind.currentData(), "plugin")
        self.assertEqual(window.fader.plugin.currentText(), "hrdctl")

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
