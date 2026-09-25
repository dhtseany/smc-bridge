import unittest

from smc_bridge.bridge import Bridge, PAN_CENTER, cc_to_pitchbend, pitchbend_to_cc, relative_delta
from smc_bridge.config import Mapping


def eight(*assigned):
    mappings = [Mapping() for _ in range(8)]
    for strip, name, volume_cc, pan_cc in assigned:
        mappings[strip] = Mapping(name, volume_cc, pan_cc)
    return mappings


def eight_with_buttons(strip, name, volume_cc, pan_cc, mute_cc=None, solo_cc=None):
    mappings = [Mapping() for _ in range(8)]
    mappings[strip] = Mapping(name, volume_cc, pan_cc, mute_cc, solo_cc)
    return mappings


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


class BridgeButtonTests(unittest.TestCase):
    def test_mute_press_toggles_on_then_off(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertEqual(bridge.on_mute_button(17, 127), (25, 127))
        self.assertEqual(bridge.on_mute_button(17, 127), (25, 0))

    def test_solo_press_toggles_on_then_off(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertEqual(bridge.on_solo_button(9, 127), (26, 127))
        self.assertEqual(bridge.on_solo_button(9, 127), (26, 0))

    def test_note_off_release_is_ignored(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertIsNone(bridge.on_mute_button(17, 0))
        self.assertEqual(bridge.mute_state[1], False)

    def test_mute_button_with_no_mute_cc_bound_is_ignored(self):
        bridge = Bridge(eight((1, "ICOM 2", 23, 24)))
        self.assertIsNone(bridge.on_mute_button(17, 127))

    def test_mute_button_on_unassigned_strip_is_ignored(self):
        bridge = Bridge(eight())
        self.assertIsNone(bridge.on_mute_button(16, 127))

    def test_mute_button_out_of_range_is_ignored(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertIsNone(bridge.on_mute_button(24, 127))

    def test_mixer_mute_feedback_lights_indicator_and_updates_state(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertEqual(bridge.on_mixer_mute(25, 127), (17, True))
        self.assertTrue(bridge.mute_state[1])
        self.assertEqual(bridge.on_mixer_mute(25, 0), (17, False))
        self.assertFalse(bridge.mute_state[1])

    def test_mixer_solo_feedback_lights_indicator_and_updates_state(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertEqual(bridge.on_mixer_solo(26, 127), (9, True))
        self.assertTrue(bridge.solo_state[1])

    def test_mixer_mute_feedback_unknown_cc_is_ignored(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        self.assertIsNone(bridge.on_mixer_mute(99, 127))

    def test_mute_and_solo_are_independent_state(self):
        bridge = Bridge(eight_with_buttons(1, "ICOM 2", 23, 24, mute_cc=25, solo_cc=26))
        bridge.on_mute_button(17, 127)
        self.assertTrue(bridge.mute_state[1])
        self.assertFalse(bridge.solo_state[1])


if __name__ == "__main__":
    unittest.main()
