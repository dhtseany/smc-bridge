"""PARKED FEATURE — not currently wired into the running bridge.

SMC transport buttons -> system media control (MPRIS via playerctl). Built
and confirmed working against real hardware on 2026-09-25 (Play/Pause
buttons correctly called `playerctl play`/`playerctl pause`), then
deliberately regressed at the user's request — they want it as a later
optional add-on rather than always-on behavior, not because anything was
wrong with it.

To re-enable: in transport.py's Transport._handle_smc, inside the
SEQ_EVENT_NOTEON branch, before the mute/solo handler loop, add:

    from .media_control import play_pause_action, run_media_action
    ...
    action = play_pause_action(note, velocity)
    if action:
        run_media_action(action)
        return

Requires the system package `playerctl` (sudo pacman -S playerctl); not
installed by default. run_media_action() logs a warning and no-ops if it's
missing, rather than crashing.
"""
import logging
import shutil
import subprocess

from .bridge import TRANSPORT_NOTES

LOG = logging.getLogger(__name__)

MEDIA_ACTIONS = {"play", "pause"}


def play_pause_action(note, velocity):
    """Bottom-row transport button pressed (Note On, velocity > 0).

    Returns 'play' or 'pause' for those two buttons, to run via
    playerctl; None for a release, an unrecognized note, or any other
    recognized transport button (record/rewind/fast_forward/bank_left/
    bank_right/up/down/left/right).
    """
    if velocity <= 0:
        return None
    action = TRANSPORT_NOTES.get(note)
    return action if action in MEDIA_ACTIONS else None


def run_media_action(action):
    """Fire-and-forget playerctl call; never blocks or raises into the caller."""
    if shutil.which("playerctl") is None:
        LOG.warning("Transport button pressed (%s) but playerctl is not installed; run: sudo pacman -S playerctl", action)
        return
    try:
        subprocess.Popen(["playerctl", action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        LOG.error("Failed to run playerctl %s: %s", action, error)
