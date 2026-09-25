import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication
from smc_mixer.config import Mapping, load, save, validate
from smc_mixer.gui import Window


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_conflict_preserves_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "mappings.ini"
            mappings = [Mapping("Desk Mic 100%", 11, 12)] + [Mapping() for _ in range(7)]
            save(path, mappings)
            self.assertEqual(load(path), mappings)
            original = path.read_bytes()
            mappings[1] = Mapping("PC", 12, 15)
            with self.assertRaisesRegex(ValueError, "already used"):
                save(path, mappings)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_mappings(self):
        for mapping in (Mapping("Mic", 128, 1), Mapping("Mic", 1, None), Mapping("", 1, 2), Mapping("Mic", 1, 1)):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                validate([mapping] + [Mapping() for _ in range(7)])

    def test_mute_solo_cc_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            mappings = [Mapping("Desk Mic", 11, 12, 13, 14)] + [Mapping() for _ in range(7)]
            save(path, mappings)
            self.assertEqual(load(path), mappings)

    def test_mute_solo_cc_optional_when_assigned(self):
        mappings = [Mapping("Desk Mic", 11, 12)] + [Mapping() for _ in range(7)]
        validate(mappings)  # no mute/solo CC assigned; must not raise

    def test_mute_solo_cc_rejected_when_unassigned(self):
        with self.assertRaisesRegex(ValueError, "assign the strip"):
            validate([Mapping(mute_cc=13)] + [Mapping() for _ in range(7)])

    def test_mute_solo_cc_conflicts_with_other_controls(self):
        mappings = [Mapping("Desk Mic", 11, 12, mute_cc=12)] + [Mapping() for _ in range(7)]
        with self.assertRaisesRegex(ValueError, "already used"):
            validate(mappings)


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
        self.assertEqual(load(self.path)[0], Mapping("Desk Mic", 11, 12))
        restored = Window(self.path)
        self.assertEqual(restored.name.text(), "Desk Mic")
        self.assertTrue(restored.enabled.isChecked())
        restored.close()
        window.enabled.setChecked(False)
        window.save_button.click()
        self.assertEqual(load(self.path)[0], Mapping())

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
        self.assertFalse(window.mappings[1].assigned)

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

    def test_mute_button_click_selects_strip_and_focuses_mute_field(self):
        window = self.window
        self._assign(window, 2, "ICOM", 30, 31)
        window.select(0)
        self._activate(window)
        window.strips[2].mute_button.click()
        self.assertEqual(window.selected, 2)
        self.assertTrue(window.mute.isEnabled())
        self.assertTrue(window.mute.hasFocus())

    def test_solo_button_click_selects_strip_and_focuses_solo_field(self):
        window = self.window
        self._assign(window, 3, "PC", 40, 41)
        window.select(0)
        self._activate(window)
        window.strips[3].solo_button.click()
        self.assertEqual(window.selected, 3)
        self.assertTrue(window.solo.isEnabled())
        self.assertTrue(window.solo.hasFocus())

    def test_mute_button_click_on_already_selected_strip_still_focuses(self):
        window = self.window
        self._assign(window, 0, "Desk Mic", 11, 12)
        self._activate(window)
        window.volume.setFocus()
        window.strips[0].mute_button.click()
        self.assertEqual(window.selected, 0)
        self.assertTrue(window.mute.hasFocus())

    def test_mute_button_click_on_unassigned_strip_selects_without_crashing(self):
        window = self.window
        window.strips[2].mute_button.click()
        self.assertEqual(window.selected, 2)
        self.assertFalse(window.mute.isEnabled())

    def test_corrupt_file_is_protected(self):
        self.path.write_text("not an ini file")
        broken = Window(self.path)
        self.assertFalse(broken.save_button.isEnabled())
        self.assertFalse(broken.save())
        self.assertEqual(self.path.read_text(), "not an ini file")
        broken.close()


if __name__ == "__main__":
    unittest.main()
