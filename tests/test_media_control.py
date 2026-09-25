"""Tests for the parked play/pause feature (see media_control.py's docstring).

Not currently wired into transport.py; these tests just keep the parked
logic itself verified so it's ready to re-enable later without a rewrite.
"""
import unittest
from unittest.mock import patch

from smc_bridge.media_control import play_pause_action, run_media_action


class PlayPauseActionTests(unittest.TestCase):
    def test_play_and_pause_return_media_actions(self):
        self.assertEqual(play_pause_action(94, 127), "play")
        self.assertEqual(play_pause_action(93, 127), "pause")

    def test_release_is_ignored(self):
        self.assertIsNone(play_pause_action(94, 0))

    def test_recognized_but_unwired_buttons_return_none(self):
        for note in (95, 91, 92, 46, 47, 96, 97, 98, 99):
            with self.subTest(note=note):
                self.assertIsNone(play_pause_action(note, 127))

    def test_unrecognized_note_is_ignored(self):
        self.assertIsNone(play_pause_action(1, 127))


class RunMediaActionTests(unittest.TestCase):
    def test_runs_playerctl_when_installed(self):
        with patch("smc_bridge.media_control.shutil.which", return_value="/usr/bin/playerctl"), \
             patch("smc_bridge.media_control.subprocess.Popen") as popen:
            run_media_action("play")
            self.assertEqual(popen.call_args.args[0], ["playerctl", "play"])

    def test_logs_and_noops_when_missing(self):
        with patch("smc_bridge.media_control.shutil.which", return_value=None), \
             patch("smc_bridge.media_control.subprocess.Popen") as popen, \
             self.assertLogs(level="WARNING"):
            run_media_action("pause")
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
