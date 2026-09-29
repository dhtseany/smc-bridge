# SMC Bridge

A bridge app, written in Python, that works between an M-Vave SMC-Mixer
and jack_mixer using mac2CC (MIDI Absolute Control to CC). Linux desktop
mapping GUI plus a headless daemon; built with Python 3.10+ and Qt 6
(PySide6).

License: [GPLv3](LICENSE).

**Current stage:** working eight-strip mapping GUI with persistent configuration,
plus a live MIDI bridge in the headless background process (see "Live MIDI
bridge" below) translating strip 1-8 faders/encoders to/from jack_mixer. The
faders are confirmed **not motorized** (see "Fader feedback behavior" below).
The GUI's own console graphics remain mapping buttons, not live faders or pan
controls — live control happens only through the background process. The
hardware layout follows the supplied SMC-Mixer photo: M/S/R/square buttons
beside each fader, BT and Shift at the upper right, and eleven transport/navigation
buttons along the bottom. These additional buttons are inactive visual references
with descriptive tooltips; unverified functions remain explicitly unverified.

## Install on Arch Linux

Build and install with the supplied `PKGBUILD`:

```sh
makepkg -si
```

This installs the `smc-bridge` command, its `pyside6` dependency, the systemd
user unit, and an **SMC Bridge** launcher with its icon in your desktop's
application menu. `python-pyalsa` (needed for the live MIDI transport,
see "Live MIDI bridge" below) is an optional dependency; install it too if you
want live hardware control rather than just the mapping GUI:

```sh
sudo pacman -S --asdeps python-pyalsa
```

Then launch the GUI:

```sh
smc-bridge
```

## Run from source

Install the GUI dependency if needed:

```sh
sudo pacman -S python pyside6
```

From this project directory:

```sh
python3 -m smc_bridge
```

Run from a terminal in your graphical desktop session. No web server or build
step is needed.

Alternatively, install `requirements.txt` in a Python virtual environment.

## Try the mapping interface

1. Click a strip's fader, encoder, or channel label.
2. Check **Assign this strip**, enter a channel name, and enter the volume and
   pan CC numbers configured for that channel in jack_mixer.
3. Click **Apply**. Switching strips or keys also applies valid edits;
   invalid edits stay visible for correction.
4. Click **Save mappings** to persist all eight strips. Saving includes the
   current editor's changes. Restart to verify restoration.
5. Uncheck **Assign this strip** and apply to clear an assignment.

### Key actions

Every button that sends MIDI can be given an action: the M, S, R and □
buttons on each strip and the eleven transport-row buttons (43 in all; BT
and Shift send no MIDI). Click a button, choose what happens **When
pressed**, and Apply. Buttons with an action are highlighted.

- **MIDI CC to jack_mixer**: sends a CC, as Mute and Solo do. *Toggle*
  flips on/off with each press (127/0), lights the button's LED while on,
  and follows jack_mixer's feedback on the same CC. *Momentary* sends 127
  while held and 0 on release.
- **Media command**: Play / Pause, Next track, Previous track or Stop, sent
  to the active media player over MPRIS (the same thing a keyboard's media
  keys trigger). A player that is Playing is preferred, then one that is
  Paused. Uses `busctl`, which ships with systemd; nothing else to install.
- **Shell command**: runs a one-line command with `/bin/sh -c`, as you, in
  the background daemon, once per press. Output is discarded; a non-zero
  exit is logged with its stderr. The daemon runs as a systemd user service,
  so commands get that environment (see `systemctl --user show-environment`).
  Anything a command starts belongs to the service, and stopping or
  restarting the service stops it too. Commands only load from a
  configuration file (and directory) that you own and that no one else can
  write to.

CC numbers must be 0–127 and unique across all assigned volume, pan and key
MIDI controls.
The MIDI channel policy will be established with the transport implementation;
this preview has a single shared CC namespace. Names are descriptive labels;
jack_mixer channel discovery/configuration is not implemented.

**Reload saved** restores the file, prompting before discarding edits. Closing
with unsaved edits offers Save, Discard, or Cancel. An invalid startup file is
reported and protected against overwriting; correct it and click Reload saved.

The human-readable INI file defaults to:

```text
~/.config/smc_bridge/mappings.ini
```

`XDG_CONFIG_HOME` is honored. Use a separate configuration for experiments:

```sh
python3 -m smc_bridge --config /tmp/smc-bridge-demo.ini
```

## Development checks

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

Next milestone: verify strip 1's live translation against real hardware
(fader -> volume, encoder -> pan, and the feedback path), then confirm
encoder direction and generalize confidence to all eight strips. The four
MIDI ports and the translation logic are implemented; see "Live MIDI bridge"
below.

## Live MIDI bridge

The headless background process is now a real ALSA Sequencer MIDI client
named `SMC Bridge` (shortened from an earlier, longer client name that was
truncated illegibly in RaySession's patchbay columns), exposing four ports
for manual PipeWire patching:

```
SMC In      <- patch from SINCO SMC-Mixer-Master (capture)
SMC Out     -> patch to   SINCO SMC-Mixer-Master (playback)
Mixer In    <- patch from jack_mixer (midi out)
Mixer Out   -> patch to   jack_mixer (midi in)
```

It requires `python-pyalsa` (already present on Arch as a `python`
dependency chain; install with `sudo pacman -S python-pyalsa` if missing).
If it's unavailable, the daemon falls back to its configuration-only preview
mode and says so in the log — it never crashes for lack of it.

Translation is deliberately simple:

- Every physical fader move is forwarded immediately as the configured
  volume CC to jack_mixer. There is **no pickup/soft-takeover logic** — the
  physical fader is always treated as correct. See "Fader feedback behavior"
  below for why that's sufficient.
- Every physical encoder turn nudges a stored per-strip pan value (starting
  centered at 64 on daemon startup) and forwards the new absolute value as
  the configured pan CC.
- jack_mixer volume-CC feedback is translated back to Pitch Bend and sent to
  `SMC Out`.
- jack_mixer pan-CC feedback updates the stored pan state only (no hardware
  output — encoders have no absolute position to display).
- Pressing a button runs its key action (see "Key actions" above). A
  toggle MIDI key keeps a locally tracked on/off state and sends it as an
  absolute CC (127=on, 0=off); jack_mixer's feedback on that CC updates the
  same state and lights the physical button back (Note On/Off). Confirmed
  on hardware: Mute is Note (16 + strip index), channel 0, for at least
  strips 1-2. Solo (8 +), Select/□ (0 +) and R (24 +) are confirmed for
  strip 1 and assumed for the rest by the same pattern. **Unverified**: this
  assumes jack_mixer's Mute/Solo CC is an absolute level, not a
  toggle-on-any-message control. If the indicator seems to drift out of sync
  with jack_mixer's actual state, that assumption is the first thing to
  check (BUTTON_ON_THRESHOLD in bridge.py).

**Restarting the daemon?** Manually redo its four patchbay connections
afterward — don't trust an auto-restored link. RaySession/PipeWire can show
a link as connected (both in the canvas and in `pw-link -l`) while it's
actually still pointing at a dead port from the previous process instance,
silently dropping all data. Disconnect and reconnect fresh. Set
`SMC_BRIDGE_DEBUG=1` before `--headless` to log every MIDI event the bridge
receives if you need to check whether data is actually arriving.

**Configuration format**: files saved by 1.0.x (format 1, with Mute/Solo
CCs on each strip) still load; those CCs become toggle MIDI actions on the
strip's M and S keys, and the file is written as format 2 on the next save.
Each key with an action is a `[key:<id>]` section, for example:

```ini
[key:strip1.mute]
action = midi
cc = 13
mode = toggle

[key:transport.play]
action = media
media = play_pause

[key:transport.up]
action = command
command = notify-send "SMC" "Up pressed"
```

Key ids are `strip1`–`strip8` with `.mute`, `.solo`, `.rec` or `.select`,
and `transport.` with `play`, `pause`, `record`, `rewind`, `fast_forward`,
`bank_left`, `bank_right`, `up`, `down`, `left` or `right`.

Two defaults are still unverified against hardware and easy to flip if wrong
(see `smc_bridge/bridge.py` and `smc_bridge/transport.py`):
which encoder value (1 vs 65) increases pan, and jack_mixer's MIDI channel
for CC feedback (currently assumed to be channel 0).

## Background operation

The GUI and background process are separate. The headless entry point does not
import Qt or require a display. It owns MIDI transport when `python-pyalsa`
is available; see "Live MIDI bridge" above. You can try its lifecycle now:

```sh
python3 -m smc_bridge --headless
```

Ctrl+C stops this foreground invocation. The process reloads saved configuration
within about one second; SIGHUP also requests a reload. Invalid edits or a deleted
file retain the last valid mappings. An invalid file at startup causes an error.
Only one background process per user is permitted, even with different config
paths. The lock is released automatically when the process exits.

Opening or closing the GUI does not start or stop the background process.
**Apply** edits the GUI session; **Save mappings** publishes those edits to the
background process through the shared file. Both modes accept `--config`.

### Run as a systemd user service

Installing the `PKGBUILD` (see "Install on Arch Linux" above) places the unit
at `/usr/lib/systemd/user/smc-bridge.service` automatically — skip straight to
`systemctl --user enable --now smc-bridge.service` below.

Running from source instead? The supplied unit expects `smc-bridge` on `PATH`
(e.g. installed with `pip install .` or in an active virtualenv); edit
`ExecStart` in `systemd/smc-bridge.service` if you're using a virtual
environment's own `python3 -m smc_bridge --headless`. Install and enable it
with:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/smc-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now smc-bridge.service
```

Stop any manually launched headless process first. The service runs under
your user account, starts with your user manager (normally at login), and
restarts on failure; no system-wide daemon or root privileges are needed. It
is not installed or enabled automatically by the application. Service
configuration follows the
[systemd service documentation](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml).

```sh
systemctl --user status smc-bridge.service
journalctl --user -u smc-bridge.service -f
systemctl --user reload smc-bridge.service
systemctl --user stop smc-bridge.service
# Stop and remove login startup:
systemctl --user disable --now smc-bridge.service
```

The default mapping file is the same as the GUI's. If your desktop terminal and
user service have different `XDG_CONFIG_HOME` values, pass the same absolute
`--config /path/to/mappings.ini` to the GUI and the unit's `ExecStart` command.
The current GUI does not display service status; use the status/log commands above.

## Fader feedback behavior

**Confirmed by physical test: the SMC faders are not motorized.** Sending
Pitch Bend to fader 1 blinks its LED instead of moving it, matching the
[manufacturer manual](https://manualf.oss-cn-hongkong.aliyuncs.com/manual/MIDI-KEYBOARD/SMC-MIXER_M-VAVE-manual.pdf)
(English page 05) description of a position-mismatch indicator.

Consequence: when jack_mixer's stored volume and the fader's actual physical
position disagree (e.g. right after the daemon starts, before that fader has
been touched this session), the LED blinks. The accepted workflow is simply
to nudge the fader until it stops — the bridge does not attempt any
pickup/soft-takeover reconciliation itself.

## License

Copyright (C) 2026 Sean Snell

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option)
any later version. See [LICENSE](LICENSE) for the full text.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE.
