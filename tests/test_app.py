import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QMessageBox
from smc_bridge import plugins
from smc_bridge.config import CCConflict, Config, KeyAction, Mapping, Route, load, save, validate
from smc_bridge.gui import Control, PluginsPanel, SettingsDialog, Window, app_version


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

    def test_cc_conflict_names_both_controls_and_either_can_be_unset(self):
        clash = config(mixer("Mic", 11, 12), strip1__solo=KeyAction("midi", 12))
        with self.assertRaises(CCConflict) as caught:
            validate(clash)
        self.assertEqual((caught.exception.cc, caught.exception.first, caught.exception.second), (12, "strip1.encoder", "strip1.solo"))
        self.assertIn("already used by strip 1 encoder", str(caught.exception))
        self.assertEqual(clash.without("strip1.solo"), config(mixer("Mic", 11, 12)))
        self.assertEqual(clash.without("strip1.encoder").mappings[0], mixer("Mic", 11))
        self.assertEqual(clash.without("strip1.encoder").without("strip1.fader").mappings[0], Mapping("Mic"))
        self.assertEqual(clash.keys, {"strip1.solo": KeyAction("midi", 12)})  # copies, not in place

    def test_pan_cc_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            volume_only = config(mixer("Mixed strip", 19))
            save(path, volume_only)
            loaded = load(path)
            self.assertEqual(loaded, volume_only)
            self.assertIsNone(loaded.mappings[0].encoder)

    def test_format_3_strips_migrate_to_independent_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            path.write_text(
                "[app]\nformat_version = 3\n\n[strip1]\nname = Mic\nvolume_cc = 11\npan_cc = 12\n\n"
                "[strip2]\nname = Channel A\nvolume_cc =\npan_cc =\nplugin = example\ntarget = target_a\n"
                + "".join(f"\n[strip{i}]\nname =\nvolume_cc =\npan_cc =\n" for i in range(3, 9))
            )
            path.chmod(0o600)
            self.assertEqual(load(path), config(mixer("Mic", 11, 12), plugin_strip("Channel A", "example", "target_a")))

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
            config(plugin_strip("Channel A", "example", "target_a")),
            config(transport__record=KeyAction("plugin", plugin="example", target="button")),
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
        # Plugins installed on this machine must not change what the GUI shows.
        installed = patch("smc_bridge.plugins.installed", return_value={})
        installed.start()
        self.addCleanup(installed.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mappings.ini"
        # Stand in for the Replace dialog: record what was asked, answer self.replace.
        self.replace = False
        self.asked = []
        replacer = patch.object(Window, "confirm_replace", lambda window, cc, other: self.asked.append((cc, other)) or self.replace)
        replacer.start()
        self.addCleanup(replacer.stop)
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
        self.assertEqual(load(self.path).mappings[0], Mapping("Desk Mic"))
        window.name.setText("")
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
        window.name.setText("Mixed strip")
        self._route(window.fader, "midi", 19)
        self._route(window.encoder, "plugin", plugin="example", target="target_a")
        self.assertTrue(window.fader.form.isRowVisible(window.fader.cc))
        self.assertFalse(window.fader.form.isRowVisible(window.fader.target))
        self.assertFalse(window.encoder.form.isRowVisible(window.encoder.cc))
        self.assertTrue(window.encoder.form.isRowVisible(window.encoder.target))
        self.assertTrue(window.apply())
        self.assertEqual(window.strips[0].mapping_label.text(), "CC 19 · example")
        window.select(1)
        window.name.setText("Spare")
        self._route(window.encoder, "midi", 20)
        window.save_button.click()
        saved = load(self.path)
        self.assertEqual(saved.mappings[0], Mapping("Mixed strip", Route.midi(19), Route.to_plugin("example", "target_a")))
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
        self.assertTrue(window.strip_section.isVisible())
        self.assertEqual(window.editor_title.text(), "Strip 03")
        self.assertIs(window.key_editor, window.strip_keys["mute"])
        self.assertTrue(all(editor.isVisible() for editor in window.strip_keys.values()))
        self.assertTrue(window.key_editor.action.hasFocus())

    def test_strip_view_edits_fader_encoder_and_all_buttons_together(self):
        window = self.window
        self._activate(window)
        window.strips[0].key_buttons["strip1.mute"].click()
        window.name.setText("Mixed strip")
        self._route(window.fader, "midi", 19)
        self._route(window.encoder, "plugin", plugin="example", target="target_a")
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.key_editor.cc.setValue(40)
        # Another button on the same strip is in the same panel: nothing is applied yet.
        window.strips[0].key_buttons["strip1.solo"].click()
        self.assertTrue(window.draft_dirty)
        self.assertIs(window.key_editor, window.strip_keys["solo"])
        self.assertTrue(window.key_editor.action.hasFocus())
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("media"))
        window.strips[1].button.click()
        self.assertEqual((window.selected, window.selected_key), (1, None))
        self.assertEqual(window.config.mappings[0], Mapping("Mixed strip", Route.midi(19), Route.to_plugin("example", "target_a")))
        self.assertEqual(window.config.keys, {"strip1.mute": KeyAction("midi", 40), "strip1.solo": KeyAction("media", media="play_pause")})
        self.assertIsNone(window.key_editor)

    def test_invalid_button_blocks_leaving_the_strip(self):
        window = self.window
        window.strips[0].key_buttons["strip1.rec"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.strips[0].key_buttons["strip1.mute"].click()
        self.assertEqual(window.selected_key, "strip1.mute")
        window.strips[1].button.click()
        self.assertEqual(window.selected, 0)
        self.assertIn("Strip 01 · R button: choose the CC", window.error.text())

    def test_conflicting_cc_offers_to_unset_the_other_control(self):
        window = self.window
        self._assign(window, 1, "PC", 11, 12)
        window.key_buttons["strip2.mute"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.key_editor.cc.setValue(20)
        self.assertTrue(window.apply())
        window.key_buttons["strip1.mute"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.key_editor.cc.setValue(20)
        self.assertFalse(window.apply())  # declined
        self.assertEqual(self.asked, [(20, "strip2.mute")])
        self.assertIn("already used", window.error.text())
        self.assertEqual(window.config.keys, {"strip2.mute": KeyAction("midi", 20)})
        self.replace = True
        self.assertTrue(window.apply())
        self.assertEqual(window.config.keys, {"strip1.mute": KeyAction("midi", 20)})
        self.assertFalse(window.key_buttons["strip2.mute"].property("mapped"))
        self.assertIn("Unset Strip 02 · Mute", window.feedback.text())

    def test_replacing_a_strips_only_route_unsets_just_that_route(self):
        window = self.window
        window.select(1)
        window.name.setText("PC")
        self._route(window.fader, "midi", 11)
        window.strip_keys["mute"].action.setCurrentIndex(window.strip_keys["mute"].action.findData("midi"))
        window.strip_keys["mute"].cc.setValue(30)
        self.assertTrue(window.apply())
        self.replace = True
        window.select(0)
        window.name.setText("Mic")
        self._route(window.encoder, "midi", 11)
        self.assertTrue(window.apply())
        self.assertEqual(self.asked, [(11, "strip2.fader")])
        self.assertEqual(window.config.mappings[1], Mapping("PC"))
        self.assertEqual(window.config.keys["strip2.mute"], KeyAction("midi", 30))
        self.assertEqual(window.strips[1].button.text(), "PC")
        self.assertEqual(window.strips[1].mapping_label.text(), "— · —")
        self.assertEqual(window.config.mappings[0], Mapping("Mic", None, Route.midi(11)))

    def test_clash_within_the_draft_is_not_offered(self):
        window = self.window
        self.replace = True
        window.name.setText("Mic")
        self._route(window.fader, "midi", 11)
        self._route(window.encoder, "midi", 11)
        self.assertFalse(window.apply())
        self.assertEqual(self.asked, [])
        self.assertIn("already used", window.error.text())

    def test_reset_all_unsets_everything_until_saved(self):
        window = self.window
        self._assign(window, 0, "Desk Mic", 11, 12)
        window.key_buttons["transport.play"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("media"))
        self.assertTrue(window.save())
        with patch("smc_bridge.gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            window.reset_all()
        self.assertEqual(window.config.mappings[0], mixer("Desk Mic", 11, 12))
        with patch("smc_bridge.gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Reset):
            window.reset_all()
        self.assertEqual(window.config, Config())
        self.assertTrue(window.dirty)
        window.select(0)
        self.assertEqual(window.name.text(), "")
        self.assertEqual(window.fader.kind.currentData(), "")
        self.assertEqual(load(self.path).mappings[0], mixer("Desk Mic", 11, 12))  # not written yet
        self.assertTrue(window.save())
        self.assertEqual(load(self.path), Config())

    def test_transport_key_shows_only_the_key(self):
        window = self.window
        window.show()
        window.key_buttons["transport.play"].click()
        self.assertFalse(window.strip_section.isVisible())
        self.assertIs(window.key_editor, window.transport_key)
        self.assertEqual(window.editor_title.text(), "Play")
        window.strips[4].button.click()
        self.assertTrue(window.strip_section.isVisible())
        self.assertFalse(window.transport_key.isVisible())
        self.assertEqual((window.selected, window.selected_key), (4, None))

    def test_every_key_can_be_given_each_kind_of_action_and_saved(self):
        window = self.window
        window.key_buttons["strip2.mute"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.key_editor.cc.setValue(30)
        window.key_buttons["transport.record"].click()  # navigating applies the draft
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.key_editor.cc.setValue(31)
        window.key_editor.mode.setCurrentIndex(window.key_editor.mode.findData("momentary"))
        window.key_buttons["transport.play"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("media"))
        window.key_editor.media.setCurrentIndex(window.key_editor.media.findData("next"))
        window.key_buttons["strip8.select"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("command"))
        window.key_editor.command.setText("notify-send hi")
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
        window.name.setText("Channel A")
        self._route(window.fader, "plugin", plugin="example", target="target_a")
        self._route(window.encoder, "plugin", plugin="example", target="target_a")
        window.key_buttons["transport.record"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("plugin"))
        window.key_editor.plugin.setCurrentText("example")
        window.key_editor.target.setText("button")
        self.assertTrue(window.save())
        saved = load(self.path)
        self.assertEqual(saved.mappings[7], plugin_strip("Channel A", "example", "target_a"))
        self.assertEqual(saved.keys, {"transport.record": KeyAction("plugin", plugin="example", target="button")})
        self.assertEqual(window.strips[7].mapping_label.text(), "example · example")
        window.select(7)
        self.assertEqual(window.encoder.kind.currentData(), "plugin")
        self.assertEqual(window.fader.plugin.currentText(), "example")

    def test_plugins_panel_toggles_plugins_ini(self):
        settings = Path(self.temp.name) / "plugins.ini"
        plugins.set_enabled(settings, "example", True)
        panel = PluginsPanel(settings)
        box = next(box for box in panel.findChildren(QCheckBox) if box.text().startswith("example"))
        self.assertTrue(box.isChecked())
        box.setChecked(False)
        self.assertEqual(plugins.load_settings(settings), {"example": plugins.PluginSettings(False, {})})
        # Not installed, so once off it can't be turned back on from here.
        self.assertFalse(PluginsPanel(settings).findChildren(QCheckBox)[0].isEnabled())

    def test_settings_dialog_has_about_then_plugins_and_room_for_close(self):
        dialog = SettingsDialog(self.path, self.window)
        self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())], ["About", "Plugins"])
        self.assertEqual(dialog.tabs.currentIndex(), SettingsDialog.ABOUT)
        about = dialog.tabs.widget(SettingsDialog.ABOUT)
        self.assertEqual(about.version.text(), f"Version {app_version()}")
        self.assertIsInstance(dialog.tabs.widget(SettingsDialog.PLUGINS), PluginsPanel)
        dialog.show()
        self.app.processEvents()
        close = dialog.findChild(QDialogButtonBox).buttons()[0]
        self.assertGreaterEqual(close.height(), close.sizeHint().height())
        # Content needs less than the minimum size, leaving slack for a
        # client-side title bar.
        self.assertLess(dialog.layout().minimumSize().height(), dialog.minimumHeight())
        dialog.close()

    def test_version_comes_from_this_source_tree(self):
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        self.assertIn(f'version = "{app_version()}"', text)

    def test_only_the_chosen_actions_fields_are_shown(self):
        window = self.window
        window.show()
        window.key_buttons["transport.up"].click()
        for kind, visible in (("", set()), ("midi", {window.key_editor.cc, window.key_editor.mode}), ("media", {window.key_editor.media}), ("command", {window.key_editor.command})):
            window.key_editor.action.setCurrentIndex(window.key_editor.action.findData(kind))
            with self.subTest(kind=kind):
                shown = {w for w in (window.key_editor.cc, window.key_editor.mode, window.key_editor.media, window.key_editor.command) if window.key_editor.form.isRowVisible(w)}
                self.assertEqual(shown, visible)

    def test_invalid_key_blocks_navigation_and_clearing_removes_it(self):
        window = self.window
        self._assign(window, 0, "Desk Mic", 11, 12)
        window.key_buttons["strip1.mute"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData("midi"))
        window.strips[1].button.click()
        self.assertEqual((window.selected, window.selected_key), (0, "strip1.mute"))
        self.assertIn("choose the CC", window.error.text())
        window.key_editor.cc.setValue(12)  # the strip's own encoder: not offered for replacing
        self.assertFalse(window.apply())
        self.assertIn("already used", window.error.text())
        window.key_editor.cc.setValue(13)
        window.strips[1].button.click()
        self.assertEqual((window.selected, window.selected_key), (1, None))
        self.assertEqual(window.config.keys["strip1.mute"], KeyAction("midi", 13))
        window.key_buttons["strip1.mute"].click()
        window.key_editor.action.setCurrentIndex(window.key_editor.action.findData(""))
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
