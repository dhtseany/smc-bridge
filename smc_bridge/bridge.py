"""Pure SMC <-> jack_mixer MIDI translation logic, independent of ALSA transport.

Physical layout is fixed by the hardware (see primer.md's SMC MIDI PROTOCOL
FINDINGS): fader N is Pitch Bend channel N-1, encoder N is CC (16 + N-1) on
channel 0, both N in 1..8. Each strip's jack_mixer-side volume_cc/pan_cc is
whatever the user configured for it. Feedback direction is intentionally
naive: the physical fader is always treated as correct, so every fader move
is forwarded unconditionally; the hardware's own LED shows any resulting
mismatch until the user re-touches the fader (see primer.md, "OPEN DESIGN
QUESTION 6 RESOLVED").
"""
from dataclasses import dataclass, field

PITCH_MIN = -8192
PITCH_MAX = 8191
PAN_MIN = 0
PAN_MAX = 127
PAN_CENTER = 64

# Confirmed on hardware 2026-09-25: strip N's Mute button is Note On/Off,
# channel 0, note (16 + N-1) (strip 1 = note 16, strip 2 = note 17). Solo,
# Select and R follow the same fixed-base-plus-strip-index scheme; each is
# confirmed for strip 1 only (primer.md, "Strip buttons").
SELECT_NOTE_BASE = 0
SOLO_NOTE_BASE = 8
MUTE_NOTE_BASE = 16
REC_NOTE_BASE = 24

# Unverified: assumes jack_mixer treats a toggle key's CC as an absolute
# level (>=64 means on) rather than toggling on any received message. If
# that's wrong, the physical button and jack_mixer's state will diverge
# after the first press. See primer.md open design question 3.
BUTTON_ON_THRESHOLD = 64

# Confirmed on hardware 2026-09-25: the bottom transport row, all Note
# On/Off, channel 0.
TRANSPORT_NOTES = {
    94: "play", 93: "pause", 95: "record",
    91: "rewind", 92: "fast_forward",
    46: "bank_left", 47: "bank_right",
    96: "up", 97: "down", 98: "left", 99: "right",
}

STRIP_KEYS = (("select", SELECT_NOTE_BASE), ("solo", SOLO_NOTE_BASE), ("mute", MUTE_NOTE_BASE), ("rec", REC_NOTE_BASE))


def _key_notes():
    notes = {}
    for strip in range(8):
        for kind, base in STRIP_KEYS:
            notes[f"strip{strip + 1}.{kind}"] = base + strip
    for note, name in TRANSPORT_NOTES.items():
        notes[f"transport.{name}"] = note
    return notes


# Every button that sends MIDI (BT and Shift send nothing), by stable key id
# as used in the configuration file: "strip3.mute", "transport.play", ...
KEY_NOTES = _key_notes()
NOTE_KEYS = {note: key for key, note in KEY_NOTES.items()}

# Unverified: which relative-encoder value increases pan is not yet confirmed
# against hardware (primer.md open design question 4). Flip this to change
# direction without touching the rest of the translation logic.
ENCODER_INCREASES_PAN = 1
PAN_STEP = 1


def pitchbend_to_cc(value):
    """Map a Pitch Bend value (-8192..8191) to a MIDI CC value (0..127)."""
    value = max(PITCH_MIN, min(PITCH_MAX, value))
    return round((value - PITCH_MIN) * PAN_MAX / (PITCH_MAX - PITCH_MIN))


def cc_to_pitchbend(value):
    """Inverse of pitchbend_to_cc, for feedback from jack_mixer to the fader."""
    value = max(PAN_MIN, min(PAN_MAX, value))
    return round(value * (PITCH_MAX - PITCH_MIN) / PAN_MAX) + PITCH_MIN


def relative_delta(value):
    """Decode an SMC relative encoder value into a signed step, or 0 if unrecognized."""
    if 1 <= value <= 63:
        magnitude = value
    elif 65 <= value <= 127:
        magnitude = -(value - 64)
    else:
        return 0
    return magnitude if ENCODER_INCREASES_PAN == 1 else -magnitude


@dataclass
class Bridge:
    """Holds per-strip pan state and per-key on/off state; mappings and key
    actions can be swapped out live on config reload."""
    mappings: list
    keys: dict = field(default_factory=dict)
    pan_state: list = field(default_factory=lambda: [PAN_CENTER] * 8)
    key_state: dict = field(default_factory=dict)

    def update_mappings(self, mappings, keys=None):
        """Swap in new mappings (and key actions, if given) on config reload.

        State is tied to the jack_mixer control it tracks, so it is reset to
        its default when that control changes (including becoming
        unassigned); otherwise the next encoder turn or button press would
        carry the old channel's value onto the new one. State for unchanged
        controls is kept.

        Returns [(note, cc)] for momentary MIDI keys that were held down and
        whose action changed: the transport must send cc=0 and unlight the
        note, because the eventual physical release will no longer reach
        the old control.
        """
        releases = []
        mappings = list(mappings)
        for strip, (old, new) in enumerate(zip(self.mappings, mappings)):
            if (old.assigned, old.pan_cc) != (new.assigned, new.pan_cc):
                self.pan_state[strip] = PAN_CENTER
        self.mappings = mappings
        if keys is not None:
            keys = dict(keys)
            for key in list(self.key_state):
                old = self.keys.get(key)
                if old != keys.get(key):
                    if old is not None and old.kind == "midi" and old.mode == "momentary" and self.key_state[key]:
                        releases.append((KEY_NOTES[key], old.cc))
                    del self.key_state[key]
            self.keys = keys
        return releases

    def _volume_ccs(self):
        return {m.volume_cc: i for i, m in enumerate(self.mappings) if m.assigned}

    def _pan_ccs(self):
        return {m.pan_cc: i for i, m in enumerate(self.mappings) if m.assigned}

    def _toggle_key_ccs(self):
        return {a.cc: key for key, a in self.keys.items() if a.kind == "midi" and a.mode == "toggle"}

    def on_fader(self, channel, value):
        """Physical fader `channel` (0-7) moved to pitch-bend `value`.

        Returns (volume_cc, cc_value) to send to jack_mixer, or None if that
        strip has no mapping.
        """
        if not 0 <= channel <= 7:
            return None
        mapping = self.mappings[channel]
        if not mapping.assigned:
            return None
        return mapping.volume_cc, pitchbend_to_cc(value)

    def on_encoder(self, controller, value):
        """Physical encoder sent relative CC `controller` (16-23) = `value`.

        Returns (pan_cc, cc_value) to send to jack_mixer, or None if that
        strip has no mapping or the value carried no recognizable step.
        """
        strip = controller - 16
        if not 0 <= strip <= 7:
            return None
        mapping = self.mappings[strip]
        if not mapping.assigned:
            return None
        delta = relative_delta(value) * PAN_STEP
        if delta == 0:
            return None
        new_pan = max(PAN_MIN, min(PAN_MAX, self.pan_state[strip] + delta))
        self.pan_state[strip] = new_pan
        return mapping.pan_cc, new_pan

    def on_plugin_fader(self, channel, value):
        """Physical fader `channel` (0-7) moved to pitch-bend `value`.

        Returns (plugin, target, level) with level 0.0-1.0 if that strip is
        routed to a plugin, else None.
        """
        if not 0 <= channel <= 7 or not self.mappings[channel].plugin:
            return None
        mapping = self.mappings[channel]
        value = max(PITCH_MIN, min(PITCH_MAX, value))
        return mapping.plugin, mapping.target, (value - PITCH_MIN) / (PITCH_MAX - PITCH_MIN)

    def on_plugin_encoder(self, controller, value):
        """Physical encoder sent relative CC `controller` (16-23) = `value`.

        Returns (plugin, target, delta) if that strip is routed to a plugin
        and the value carried a step, else None. Direction follows
        ENCODER_INCREASES_PAN, as for pan.
        """
        strip = controller - 16
        if not 0 <= strip <= 7 or not self.mappings[strip].plugin:
            return None
        delta = relative_delta(value)
        if delta == 0:
            return None
        return self.mappings[strip].plugin, self.mappings[strip].target, delta

    def on_mixer_volume(self, cc, value):
        """jack_mixer reported CC `cc` = `value`.

        Returns (fader_channel, pitch_value) to send back to the SMC, or None
        if `cc` is not any strip's configured volume_cc.
        """
        strip = self._volume_ccs().get(cc)
        if strip is None:
            return None
        return strip, cc_to_pitchbend(value)

    def on_mixer_pan(self, cc, value):
        """jack_mixer reported CC `cc` = `value` for a pan control.

        Updates the stored pan state so the next relative encoder turn starts
        from the right place; there is no hardware equivalent to send back.
        Returns the strip index updated, or None if `cc` is not any strip's
        configured pan_cc.
        """
        strip = self._pan_ccs().get(cc)
        if strip is None:
            return None
        self.pan_state[strip] = max(PAN_MIN, min(PAN_MAX, value))
        return strip

    def on_key(self, note, velocity):
        """Physical button `note` pressed (velocity > 0) or released (0).

        Returns what the transport should do, or None:
          ("midi", cc, cc_value, is_on) — send cc_value on cc to jack_mixer
              and light (is_on) or unlight the button's LED;
          ("media", action) — a media command (see actions.MEDIA_ACTIONS);
          ("command", command_line) — a shell command;
          ("plugin", plugin, target, pressed) — pass to a plugin.
        Toggle MIDI keys flip on press; momentary MIDI keys send 127 on
        press and 0 on release. Media and command keys fire on press only.
        Plugin keys report both, so a plugin can do push-to-talk.
        """
        key = NOTE_KEYS.get(note)
        action = self.keys.get(key)
        if action is None:
            return None
        pressed = velocity > 0
        if action.kind == "midi":
            if action.mode == "momentary":
                # A release only counts if this action saw the press; a key
                # remapped while held was already released on reload.
                if not pressed and not self.key_state.get(key, False):
                    return None
                is_on = pressed
            elif pressed:
                is_on = not self.key_state.get(key, False)
            else:
                return None
            self.key_state[key] = is_on
            return "midi", action.cc, 127 if is_on else 0, is_on
        if action.kind == "plugin":
            return "plugin", action.plugin, action.target, pressed
        if not pressed:
            return None
        if action.kind == "media":
            return "media", action.media
        if action.kind == "command":
            return "command", action.command
        return None

    def on_mixer_key(self, cc, value):
        """jack_mixer reported CC `cc` = `value` for a toggle key's control.

        Updates stored key state and returns (note, is_on) to light/unlight
        the physical button's LED, or None if `cc` is not any toggle MIDI
        key's CC.
        """
        key = self._toggle_key_ccs().get(cc)
        if key is None:
            return None
        is_on = value >= BUTTON_ON_THRESHOLD
        self.key_state[key] = is_on
        return KEY_NOTES[key], is_on
