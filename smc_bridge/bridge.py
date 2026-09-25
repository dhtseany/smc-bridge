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
# channel 0, note (16 + N-1) (strip 1 = note 16, strip 2 = note 17). Solo
# follows the same fixed-base-plus-strip-index scheme one octave down, by
# analogy with the encoders' CC (16 + N-1) pattern; not separately
# confirmed past strip 1. Select (base note 0) has no jack_mixer control to
# bind to and is not handled here.
MUTE_NOTE_BASE = 16
SOLO_NOTE_BASE = 8

# Unverified: assumes jack_mixer treats its Mute/Solo CC as an absolute
# level (>=64 means on) rather than toggling on any received message. If
# that's wrong, the physical button and jack_mixer's state will diverge
# after the first press. See primer.md open design question 3.
BUTTON_ON_THRESHOLD = 64

# Confirmed on hardware 2026-09-25: the bottom transport row, all Note
# On/Off, channel 0. None of these are wired to any action — reference data
# only. media_control.py (not currently used anywhere) has a parked
# play/pause -> system audio feature built on top of this; see its
# docstring to re-enable.
TRANSPORT_NOTES = {
    94: "play", 93: "pause", 95: "record",
    91: "rewind", 92: "fast_forward",
    46: "bank_left", 47: "bank_right",
    96: "up", 97: "down", 98: "left", 99: "right",
}

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
    """Holds per-strip pan/mute/solo state; mappings can be swapped out live on config reload."""
    mappings: list
    pan_state: list = field(default_factory=lambda: [PAN_CENTER] * 8)
    mute_state: list = field(default_factory=lambda: [False] * 8)
    solo_state: list = field(default_factory=lambda: [False] * 8)

    def _volume_ccs(self):
        return {m.volume_cc: i for i, m in enumerate(self.mappings) if m.assigned}

    def _pan_ccs(self):
        return {m.pan_cc: i for i, m in enumerate(self.mappings) if m.assigned}

    def _mute_ccs(self):
        return {m.mute_cc: i for i, m in enumerate(self.mappings) if m.assigned and m.mute_cc is not None}

    def _solo_ccs(self):
        return {m.solo_cc: i for i, m in enumerate(self.mappings) if m.assigned and m.solo_cc is not None}

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

    def _on_button_press(self, note, base, state):
        strip = note - base
        if not 0 <= strip <= 7:
            return None
        mapping = self.mappings[strip]
        if not mapping.assigned:
            return None
        cc = mapping.mute_cc if base == MUTE_NOTE_BASE else mapping.solo_cc
        if cc is None:
            return None
        state[strip] = not state[strip]
        return cc, 127 if state[strip] else 0

    def on_mute_button(self, note, velocity):
        """Physical Mute button pressed (Note On, velocity > 0) on `note`.

        Toggles this strip's locally tracked mute state and returns
        (mute_cc, cc_value) to send to jack_mixer, or None if unbound,
        unassigned, out of range, or a release (velocity 0 / Note Off).
        """
        if velocity <= 0:
            return None
        return self._on_button_press(note, MUTE_NOTE_BASE, self.mute_state)

    def on_solo_button(self, note, velocity):
        """Physical Solo button pressed (Note On, velocity > 0) on `note`.

        Same behavior as on_mute_button, for the solo_cc/solo_state pair.
        """
        if velocity <= 0:
            return None
        return self._on_button_press(note, SOLO_NOTE_BASE, self.solo_state)

    def on_mixer_mute(self, cc, value):
        """jack_mixer reported CC `cc` = `value` for a mute control.

        Updates stored mute state and returns (note, is_on) to light/unlight
        the physical button's indicator, or None if `cc` is not any strip's
        configured mute_cc.
        """
        strip = self._mute_ccs().get(cc)
        if strip is None:
            return None
        is_on = value >= BUTTON_ON_THRESHOLD
        self.mute_state[strip] = is_on
        return MUTE_NOTE_BASE + strip, is_on

    def on_mixer_solo(self, cc, value):
        """jack_mixer reported CC `cc` = `value` for a solo control.

        Same behavior as on_mixer_mute, for the solo_cc/solo_state pair.
        """
        strip = self._solo_ccs().get(cc)
        if strip is None:
            return None
        is_on = value >= BUTTON_ON_THRESHOLD
        self.solo_state[strip] = is_on
        return SOLO_NOTE_BASE + strip, is_on
