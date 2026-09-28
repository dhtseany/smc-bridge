"""Non-MIDI key actions: media commands and custom shell commands.

Both run on a short-lived background thread so a slow D-Bus call or a
long-running command never stalls MIDI handling. Nothing here raises into
the caller; failures are logged.

Media commands go straight to the active MPRIS player over the D-Bus
session bus (via `busctl`, part of systemd), which is what a keyboard's
media keys end up doing through the desktop. No playerctl needed.
"""
import json
import logging
import subprocess
import threading

LOG = logging.getLogger(__name__)

# Config value -> (label, MPRIS Player method).
MEDIA_ACTIONS = {
    "play_pause": ("Play / Pause", "PlayPause"),
    "next": ("Next track", "Next"),
    "previous": ("Previous track", "Previous"),
    "stop": ("Stop", "Stop"),
}

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
BUSCTL_TIMEOUT = 5


def _busctl(*args):
    result = subprocess.run(
        ["busctl", "--user", "--json=short", *args],
        capture_output=True, text=True, timeout=BUSCTL_TIMEOUT, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def _players():
    reply = _busctl("call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
    return sorted(name for name in reply["data"][0] if name.startswith(MPRIS_PREFIX))


def _status(player):
    try:
        return _busctl("get-property", player, MPRIS_PATH, MPRIS_PLAYER, "PlaybackStatus")["data"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def choose_player(players, status):
    """Prefer a Playing player, then a Paused one, then any; None if there are none."""
    for wanted in ("Playing", "Paused"):
        for player in players:
            if status(player) == wanted:
                return player
    return players[0] if players else None


def _media(action):
    method = MEDIA_ACTIONS[action][1]
    try:
        player = choose_player(_players(), _status)
        if player is None:
            LOG.warning("Media key (%s): no MPRIS media player is running", action)
            return
        _busctl("call", player, MPRIS_PATH, MPRIS_PLAYER, method)
        LOG.info("Media key (%s) sent to %s", action, player[len(MPRIS_PREFIX):])
    except FileNotFoundError:
        LOG.error("Media key (%s): busctl not found (it ships with systemd)", action)
    except subprocess.CalledProcessError as error:
        LOG.error("Media key (%s) failed: %s", action, (error.stderr or "").strip() or error)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        LOG.error("Media key (%s) failed: %s", action, error)


def _command(command):
    try:
        # Own session: a Ctrl-C or SIGHUP aimed at the bridge doesn't reach it.
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        LOG.error("Key command failed to start: %s", error)
        return
    if result.returncode:
        stderr = result.stderr.decode(errors="replace").strip()
        LOG.warning("Key command exited %d: %s%s", result.returncode, command, f" ({stderr})" if stderr else "")


def _background(target, argument):
    threading.Thread(target=target, args=(argument,), name="smc-key-action", daemon=True).start()


def run_media(action):
    """Send media `action` (a MEDIA_ACTIONS key) to the active player, in the background."""
    if action not in MEDIA_ACTIONS:
        LOG.error("Unknown media action %r", action)
        return
    _background(_media, action)


def run_command(command):
    """Run `command` through /bin/sh in the background; its exit is logged, never raised."""
    _background(_command, command)
