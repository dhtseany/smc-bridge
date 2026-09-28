import subprocess
import threading
import unittest
from unittest.mock import patch

from smc_bridge import actions


def run_inline(target, argument):
    target(argument)


class ChoosePlayerTests(unittest.TestCase):
    def test_prefers_playing_then_paused_then_first(self):
        status = {"org.mpris.MediaPlayer2.a": "Stopped", "org.mpris.MediaPlayer2.b": "Paused", "org.mpris.MediaPlayer2.c": "Playing"}.get
        players = ["org.mpris.MediaPlayer2.a", "org.mpris.MediaPlayer2.b", "org.mpris.MediaPlayer2.c"]
        self.assertEqual(actions.choose_player(players, status), "org.mpris.MediaPlayer2.c")
        self.assertEqual(actions.choose_player(players[:2], status), "org.mpris.MediaPlayer2.b")
        self.assertEqual(actions.choose_player(players[:1], status), "org.mpris.MediaPlayer2.a")
        self.assertIsNone(actions.choose_player([], status))


@patch("smc_bridge.actions._background", run_inline)
class RunMediaTests(unittest.TestCase):
    def _busctl(self, calls, names, playing=None):
        def fake(*args):
            calls.append(args)
            if args[-1] == "ListNames":
                return {"type": "as", "data": [names]}
            if args[-1] == "PlaybackStatus":
                return {"type": "s", "data": "Playing" if args[1] == playing else "Paused"}
            return None
        return fake

    def test_sends_mpris_method_to_chosen_player(self):
        calls = []
        names = ["org.freedesktop.DBus", "org.mpris.MediaPlayer2.firefox", "org.mpris.MediaPlayer2.spotify"]
        with patch("smc_bridge.actions._busctl", self._busctl(calls, names, playing="org.mpris.MediaPlayer2.spotify")):
            actions.run_media("next")
        self.assertEqual(calls[-1], ("call", "org.mpris.MediaPlayer2.spotify", actions.MPRIS_PATH, actions.MPRIS_PLAYER, "Next"))

    def test_no_player_logs_and_sends_nothing(self):
        calls = []
        with patch("smc_bridge.actions._busctl", self._busctl(calls, ["org.freedesktop.DBus"])), self.assertLogs(level="WARNING"):
            actions.run_media("play_pause")
        self.assertEqual([c[-1] for c in calls], ["ListNames"])

    def test_busctl_failures_are_logged_not_raised(self):
        for error in (FileNotFoundError("busctl"), subprocess.CalledProcessError(1, "busctl", stderr="boom"), subprocess.TimeoutExpired("busctl", 5)):
            with self.subTest(error=type(error).__name__), patch("smc_bridge.actions._busctl", side_effect=error), self.assertLogs(level="ERROR"):
                actions.run_media("stop")

    def test_unknown_action_is_rejected(self):
        with patch("smc_bridge.actions._background") as background, self.assertLogs(level="ERROR"):
            actions.run_media("shuffle")
        background.assert_not_called()


@patch("smc_bridge.actions._background", run_inline)
class RunCommandTests(unittest.TestCase):
    def test_runs_through_shell_and_logs_failure(self):
        with self.assertLogs(level="WARNING") as logs:
            actions.run_command("echo oops >&2; exit 3")
        self.assertIn("exited 3", logs.output[0])
        self.assertIn("oops", logs.output[0])

    def test_successful_command_is_quiet(self):
        with patch.object(actions.LOG, "warning") as warning:
            actions.run_command("true")
        warning.assert_not_called()


class BackgroundTests(unittest.TestCase):
    def test_actions_do_not_block_the_caller(self):
        release = threading.Event()
        with patch("smc_bridge.actions._command", lambda command: release.wait(5)):
            actions.run_command("sleep")
            self.assertFalse(release.is_set())
        release.set()


if __name__ == "__main__":
    unittest.main()
