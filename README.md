# SMC Mixer

A Linux desktop mapping interface for the M-Vave/SINCO SMC-Mixer and
jack_mixer. Built with Python 3.10+ and Qt 6 (PySide6).

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

## Launch on Arch Linux

Install the GUI dependency if needed:

```sh
sudo pacman -S python pyside6
```

From this project directory:

```sh
python3 -m smc_mixer
```

In this workspace, the exact commands are:

```sh
cd /ai/projects/smc_mixer_mac2cc
python3 -m smc_mixer
```

Run from a terminal in your graphical desktop session. No web server or build
step is needed. Python and PySide6 are already available in the development
environment used for this initial version.

Alternatively, install `requirements.txt` in a Python virtual environment.

## Try the mapping interface

1. Click a strip's fader, encoder, or channel label.
2. Check **Assign this strip**, enter a channel name, and enter the volume and
   pan CC numbers configured for that channel in jack_mixer.
3. Click **Apply strip mapping**. Switching strips also applies valid edits;
   invalid edits stay visible for correction.
4. Click **Save mappings** to persist all eight strips. Saving includes the
   current editor's changes. Restart to verify restoration.
5. Uncheck **Assign this strip** and apply to clear an assignment.

CC numbers must be 0–127 and unique across all assigned volume and pan controls.
The MIDI channel policy will be established with the transport implementation;
this preview has a single shared CC namespace. Names are descriptive labels;
jack_mixer channel discovery/configuration is not implemented.

**Reload saved** restores the file, prompting before discarding edits. Closing
with unsaved edits offers Save, Discard, or Cancel. An invalid startup file is
reported and protected against overwriting; correct it and click Reload saved.

The human-readable INI file defaults to:

```text
~/.config/smc_mixer_mac2cc/mappings.ini
```

`XDG_CONFIG_HOME` is honored. Use a separate configuration for experiments:

```sh
python3 -m smc_mixer --config /tmp/smc-mixer-demo.ini
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
- Pressing Mute or Solo toggles a locally tracked on/off state for that
  strip and sends it as an absolute CC (127=on, 0=off) to jack_mixer if
  you've bound a Mute CC / Solo CC for that strip (both optional, in the
  GUI's editor panel). jack_mixer's own Mute/Solo CC feedback updates that
  same state and lights the physical button back (Note On/Off). Confirmed
  on hardware: Mute is Note (16 + strip index), channel 0, for at least
  strips 1-2. Solo is assumed to be Note (8 + strip index) by analogy but
  not independently confirmed. **Unverified**: this assumes jack_mixer's
  Mute/Solo CC is an absolute level, not a toggle-on-any-message control. If
  the indicator seems to drift out of sync with jack_mixer's actual state,
  that assumption is the first thing to check (BUTTON_ON_THRESHOLD in
  bridge.py).

**Restarting the daemon?** Manually redo its four patchbay connections
afterward — don't trust an auto-restored link. RaySession/PipeWire can show
a link as connected (both in the canvas and in `pw-link -l`) while it's
actually still pointing at a dead port from the previous process instance,
silently dropping all data. Disconnect and reconnect fresh. Set
`SMC_MIXER_DEBUG=1` before `--headless` to log every MIDI event the bridge
receives if you need to check whether data is actually arriving.

**Transport row (Play/Pause/Record/Rewind/Fast forward/bank/arrows)**: all
11 bottom-row buttons are confirmed and recognized (see TRANSPORT_NOTES in
bridge.py) but none currently do anything — intentional. A play/pause →
system audio feature (via playerctl's MPRIS integration) was built and
confirmed working against real hardware, then parked at the user's request
as an optional add-on for later rather than always-on behavior; see
smc_mixer/media_control.py's docstring for the one-paragraph re-enable
instructions.

Two defaults are still unverified against hardware and easy to flip if wrong
(see `smc_mixer/bridge.py` and `smc_mixer/transport.py`):
which encoder value (1 vs 65) increases pan, and jack_mixer's MIDI channel
for CC feedback (currently assumed to be channel 0).

## Background operation

The GUI and background process are separate. The headless entry point does not
import Qt or require a display. It owns MIDI transport when `python-pyalsa`
is available; see "Live MIDI bridge" above. You can try its lifecycle now:

```sh
python3 -m smc_mixer --headless
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

The supplied unit targets this checkout and `/usr/bin/python3`. If you relocate
the project or use a virtual environment, edit `WorkingDirectory` and `ExecStart`
in `systemd/smc-mixer.service` before installation. Install and enable it with:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/smc-mixer.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now smc-mixer.service
```

Run these commands from the project directory. Stop any manually launched
headless process first. The service runs under your user account, starts with your
user manager (normally at login), and restarts on failure; no system-wide daemon
or root privileges are needed. It is not installed or enabled automatically by
the application. Service configuration follows the
[systemd service documentation](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml).

```sh
systemctl --user status smc-mixer.service
journalctl --user -u smc-mixer.service -f
systemctl --user reload smc-mixer.service
systemctl --user stop smc-mixer.service
# Stop and remove login startup:
systemctl --user disable --now smc-mixer.service
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
