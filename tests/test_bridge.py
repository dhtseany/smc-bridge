import unittest

from smc_bridge.bridge import Bridge, KEY_NOTES, PAN_CENTER, cc_to_pitchbend, pitchbend_to_cc, relative_delta
from smc_bridge.config import KeyAction, Mapping, Route


def mixer(name, volume_cc, pan_cc=None):
    """A strip whose fader (and encoder, if given a CC) go to jack_mixer."""
    return Mapping(name, Route.midi(volume_cc), None if pan_cc is None else Route.midi(pan_cc))



def eight(*assigned):
    mappings = [Mapping() for _ in range(8)]
    for strip, name, volume_cc, pan_cc in assigned:
        mappings[strip] = mixer(name, volume_cc, pan_cc)
    return mappings


MUTE_SOLO = {"strip2.mute": KeyAction("midi", 25), "strip2.solo": KeyAction("midi", 26)}


class ScalingTests(unittest.TestCase):
    def test_pitchbend_to_cc_endpoints_and_center(self):
        self.assertEqual(pitchbend_to_cc(-8192), 0)
        self.assertEqual(pitchbend_to_cc(8191), 127)
        self.assertEqual(pitchbend_to_cc(0), 64)

    def test_pitchbend_to_cc_clamps_out_of_range(self):
        self.assertEqual(pitchbend_to_cc(-9000), 0)
        self.assertEqual(pitchbend_to_cc(9000), 127)

    def test_cc_to_pitchbend_roundtrips_endpoints(self):
        self.assertEqual(cc_to_pitchbend(0), -8192)
        self.assertEqual(cc_to_pitchbend(127), 8191)

    def test_relative_delta_matches_observed_hardware_values(self):
        self.assertEqual(relative_delta(1), 1)
        self.assertEqual(relative_delta(65), -1)
        self.assertEqual(relative_delta(63), 63)
        self.assertEqual(relative_delta(127), -63)

    def test_relative_delta_unrecognized_value_is_zero(self):
        self.assertEqual(relative_delta(0), 0)
        self.assertEqual(relative_delta(64), 0)
        self.assertEqual(relative_delta(128), 0)


class BridgeFaderTests(unittest.TestCase):
    def test_fader_move_forwards_to_configured_volume_cc(self):
        bridge = Bridge(eight((0, "Desk Mic", 11, 12)))
        self.assertEqual(bridge.on_fader(0, 0), (11, 64))

    def test_fader_move_on_unassigned_strip_is_ignored(self):
        bridge = Bridge(eight())
        self.assertIsNone(bridge.on_fader(0, 0))

    def test_fader_channel_out_of_range_is_ignored(self):
        bridge = Bridge(eight((0, "Desk Mic", 11, 12)))
        self.assertIsNone(bridge.on_fader(8, 0))


class BridgeEncoderTests(unittest.TestCase):
    def test_encoder_starts_centered_and_accumulates(self):
        bridge = Bridge(eight((0, "Desk Mic", 11, 12)))
        self.assertEqual(bridge.pan_state[0], PAN_CENTER)
        cc, value = bridge.on_encoder(16, 1)
        self.assertEqual((cc, value), (12, PAN_CENTER + 1))
        cc, value = bridge.on_encoder(16, 1)
        self.assertEqual((cc, value), (12, PAN_CENTER + 2))
        cc, value = bridge.on_encoder(16, 65)
        self.assertEqual((cc, value), (12, PAN_CENTER + 1))

    def test_strip_without_pan_cc_ignores_encoder_but_keeps_fader(self):
        bridge = Bridge(eight((0, "Mixed strip", 19, None)))
        self.assertIsNone(bridge.on_encoder(16, 1))
        self.assertEqual(bridge.on_fader(0, 8191), (19, 127))
        self.assertIsNone(bridge.on_mixer_pan(12, 20))
        self.assertEqual(bridge.on_mixer_volume(19, 127)[0], 0)

    def test_encoder_clamps_at_pan_bounds(self):
        bridge = Bridge(eight((0, "Desk Mic", 11, 12)))
        bridge.pan_state[0] = 127
        cc, value = bridge.on_encoder(16, 1)
        self.assertEqual((cc, value), (12, 127))
        bridge.pan_state[0] = 0
        cc, value = bridge.on_encoder(16, 65)
        self.assertEqual((cc, value), (12, 0))

    def test_encoder_on_unassigned_strip_is_ignored(self):
        bridge = Bridge(eight())
        self.assertIsNone(bridge.on_encoder(16, 1))

    def test_encoder_controller_out_of_range_is_ignored(self):
        bridge = Bridge(eight((0, "Desk Mic", 11, 12)))
        self.assertIsNone(bridge.on_encoder(24, 1))

    def test_encoder_maps_by_physical_position_not_by_cc_number(self):
        bridge = Bridge(eight((3, "PC", 20, 21)))
        cc, value = bridge.on_encoder(19, 1)
        self.assertEqual((cc, value), (21, PAN_CENTER + 1))


class BridgeMixerFeedbackTests(unittest.TestCase):
    def test_mixer_volume_feedback_maps_back_to_fader_channel(self):
        bridge = Bridge(eight((2, "Jabra", 30, 31)))
        self.assertEqual(bridge.on_mixer_volume(30, 127), (2, 8191))

    def test_mixer_volume_feedback_unknown_cc_is_ignored(self):
        bridge = Bridge(eight((2, "Jabra", 30, 31)))
        self.assertIsNone(bridge.on_mixer_volume(99, 64))

    def test_mixer_pan_feedback_updates_state_with_no_hardware_output(self):
        bridge = Bridge(eight((2, "Jabra", 30, 31)))
        strip = bridge.on_mixer_pan(31, 20)
        self.assertEqual(strip, 2)
        self.assertEqual(bridge.pan_state[2], 20)

    def test_mixer_pan_feedback_then_relative_turn_starts_from_new_position(self):
        bridge = Bridge(eight((2, "Jabra", 30, 31)))
        bridge.on_mixer_pan(31, 20)
        cc, value = bridge.on_encoder(18, 1)
        self.assertEqual((cc, value), (31, 21))


class KeyLayoutTests(unittest.TestCase):
    def test_every_midi_button_has_a_unique_note(self):
        self.assertEqual(len(KEY_NOTES), 43)
        self.assertEqual(len(set(KEY_NOTES.values())), 43)

    def test_strip_and_transport_notes_match_hardware(self):
        self.assertEqual(KEY_NOTES["strip1.mute"], 16)
        self.assertEqual(KEY_NOTES["strip2.mute"], 17)
        self.assertEqual(KEY_NOTES["strip1.solo"], 8)
        self.assertEqual(KEY_NOTES["strip1.select"], 24)
        self.assertEqual(KEY_NOTES["strip1.rec"], 0)
        self.assertEqual(KEY_NOTES["transport.play"], 94)
        self.assertEqual(KEY_NOTES["transport.right"], 99)


class BridgeKeyTests(unittest.TestCase):
    def test_mute_press_toggles_on_then_off(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        self.assertEqual(bridge.on_key(17, 127), ("midi", 25, 127, True))
        self.assertEqual(bridge.on_key(17, 127), ("midi", 25, 0, False))

    def test_solo_press_toggles_on_then_off(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        self.assertEqual(bridge.on_key(9, 127), ("midi", 26, 127, True))
        self.assertEqual(bridge.on_key(9, 127), ("midi", 26, 0, False))

    def test_toggle_release_is_ignored(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        self.assertIsNone(bridge.on_key(17, 0))
        self.assertFalse(bridge.key_state.get("strip2.mute", False))

    def test_momentary_sends_on_while_held(self):
        bridge = Bridge(eight(), {"transport.record": KeyAction("midi", 40, mode="momentary")})
        self.assertEqual(bridge.on_key(95, 127), ("midi", 40, 127, True))
        self.assertEqual(bridge.on_key(95, 0), ("midi", 40, 0, False))
        self.assertEqual(bridge.on_key(95, 127), ("midi", 40, 127, True))

    def test_media_and_command_fire_on_press_only(self):
        bridge = Bridge(eight(), {
            "transport.play": KeyAction("media", media="play_pause"),
            "strip1.select": KeyAction("command", command="notify-send hi"),
        })
        self.assertEqual(bridge.on_key(94, 127), ("media", "play_pause"))
        self.assertIsNone(bridge.on_key(94, 0))
        select = KEY_NOTES["strip1.select"]
        self.assertEqual(bridge.on_key(select, 127), ("command", "notify-send hi"))
        self.assertIsNone(bridge.on_key(select, 0))

    def test_unassigned_and_unknown_keys_are_ignored(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        self.assertIsNone(bridge.on_key(16, 127))  # strip 1 mute: no action
        self.assertIsNone(bridge.on_key(60, 127))  # not a button on the SMC

    def test_mixer_feedback_lights_indicator_and_updates_state(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        self.assertEqual(bridge.on_mixer_key(25, 127), (17, True))
        self.assertTrue(bridge.key_state["strip2.mute"])
        self.assertEqual(bridge.on_mixer_key(25, 0), (17, False))
        self.assertEqual(bridge.on_mixer_key(26, 127), (9, True))
        # A toggle press after feedback continues from the reported state.
        self.assertEqual(bridge.on_key(9, 127), ("midi", 26, 0, False))

    def test_mixer_feedback_ignores_unknown_and_momentary_ccs(self):
        bridge = Bridge(eight(), {"transport.record": KeyAction("midi", 40, mode="momentary")})
        self.assertIsNone(bridge.on_mixer_key(99, 127))
        self.assertIsNone(bridge.on_mixer_key(40, 127))

    def test_mute_and_solo_are_independent_state(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        bridge.on_key(17, 127)
        self.assertTrue(bridge.key_state["strip2.mute"])
        self.assertFalse(bridge.key_state.get("strip2.solo", False))


class UpdateMappingsTests(unittest.TestCase):
    def test_remapped_pan_starts_from_center_not_old_channels_value(self):
        # Review review-96df2ff3e4: stale pan state leaked onto the new channel.
        bridge = Bridge(eight((0, "Mic", 10, 11)))
        bridge.on_encoder(16, 63)
        self.assertEqual(bridge.pan_state[0], 127)
        bridge.update_mappings(eight((0, "Guitar", 20, 21)))
        self.assertEqual(bridge.on_encoder(16, 1), (21, PAN_CENTER + 1))

    def test_unchanged_mapping_keeps_pan_state(self):
        bridge = Bridge(eight((0, "Mic", 10, 11)))
        bridge.on_encoder(16, 10)
        bridge.update_mappings(eight((0, "Renamed", 10, 11)))
        self.assertEqual(bridge.pan_state[0], PAN_CENTER + 10)

    def test_unassigned_then_reassigned_strip_resets_pan(self):
        bridge = Bridge(eight((0, "Mic", 10, 11)))
        bridge.on_encoder(16, 20)
        bridge.update_mappings(eight())
        bridge.update_mappings(eight((0, "Mic", 10, 11)))
        self.assertEqual(bridge.pan_state[0], PAN_CENTER)

    def test_changed_key_actions_reset_state_independently(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        bridge.on_key(17, 127)
        bridge.on_key(9, 127)
        bridge.update_mappings(eight(), {**MUTE_SOLO, "strip2.mute": KeyAction("midi", 22)})
        self.assertNotIn("strip2.mute", bridge.key_state)
        self.assertTrue(bridge.key_state["strip2.solo"])
        self.assertEqual(bridge.on_key(17, 127), ("midi", 22, 127, True))

    def test_held_momentary_key_removed_on_reload_is_released(self):
        # Review review-e2e6d855b2: the old CC stayed at 127 forever.
        bridge = Bridge(eight(), {"transport.record": KeyAction("midi", 40, mode="momentary")})
        bridge.on_key(95, 127)
        self.assertEqual(bridge.update_mappings(eight(), {}), [(95, 40)])
        self.assertIsNone(bridge.on_key(95, 0))

    def test_held_momentary_key_remapped_on_reload_releases_old_cc_only(self):
        bridge = Bridge(eight(), {"transport.record": KeyAction("midi", 40, mode="momentary")})
        bridge.on_key(95, 127)
        releases = bridge.update_mappings(eight(), {"transport.record": KeyAction("midi", 41, mode="momentary")})
        self.assertEqual(releases, [(95, 40)])
        self.assertIsNone(bridge.on_key(95, 0))  # never sent 41=127, so no 41=0 either
        self.assertEqual(bridge.on_key(95, 127), ("midi", 41, 127, True))
        self.assertEqual(bridge.on_key(95, 0), ("midi", 41, 0, False))

    def test_reload_releases_nothing_when_unchanged_released_or_toggle(self):
        keys = {"transport.record": KeyAction("midi", 40, mode="momentary"), **MUTE_SOLO}
        bridge = Bridge(eight(), keys)
        bridge.on_key(95, 127)
        bridge.on_key(17, 127)  # toggle on: jack_mixer owns that state, nothing to release
        self.assertEqual(bridge.update_mappings(eight(), keys), [])
        self.assertEqual(bridge.on_key(95, 0), ("midi", 40, 0, False))
        self.assertEqual(bridge.update_mappings(eight(), {}), [])

    def test_stray_momentary_release_is_ignored(self):
        bridge = Bridge(eight(), {"transport.record": KeyAction("midi", 40, mode="momentary")})
        self.assertIsNone(bridge.on_key(95, 0))

    def test_update_without_keys_keeps_key_actions(self):
        bridge = Bridge(eight(), MUTE_SOLO)
        bridge.on_key(17, 127)
        bridge.update_mappings(eight((0, "Mic", 10, 11)))
        self.assertEqual(bridge.on_key(17, 127), ("midi", 25, 0, False))


if __name__ == "__main__":
    unittest.main()
