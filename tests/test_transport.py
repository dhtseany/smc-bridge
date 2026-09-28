import time
import unittest
from unittest.mock import patch

from smc_bridge.config import Config, KeyAction, Mapping

try:
    from pyalsa import alsaseq
    from smc_bridge.transport import Transport
except ImportError:
    alsaseq = None
    Transport = None


def eight(*assigned, keys=None):
    mappings = [Mapping() for _ in range(8)]
    for strip, name, volume_cc, pan_cc in assigned:
        mappings[strip] = Mapping(name, volume_cc, pan_cc)
    return Config(mappings, dict(keys or {}))


@unittest.skipIf(Transport is None, "pyalsa is not installed")
class TransportTests(unittest.TestCase):
    def setUp(self):
        try:
            self.transport = Transport(eight((0, "Desk Mic", 11, 12)), clientname="smc-bridge-test")
        except alsaseq.SequencerError as error:
            self.skipTest(f"ALSA sequencer unavailable: {error}")
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.listener = alsaseq.Sequencer(clientname="smc-bridge-test-listener")
        cap = alsaseq.SEQ_PORT_CAP_WRITE | alsaseq.SEQ_PORT_CAP_SUBS_WRITE
        port_type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
        self.listen_port = self.listener.create_simple_port("listen", port_type, cap)

    def _connect(self, source_client, source_port, dest_client, dest_port):
        self.listener.connect_ports((source_client, source_port), (dest_client, dest_port))

    _RELEVANT_TYPES = (
        alsaseq.SEQ_EVENT_CONTROLLER, alsaseq.SEQ_EVENT_PITCHBEND,
        alsaseq.SEQ_EVENT_NOTEON, alsaseq.SEQ_EVENT_NOTEOFF,
    ) if alsaseq else ()

    def _wait_for_event(self, timeout=2.0):
        events = self._wait_for_events(1, timeout=timeout)
        return events[0] if events else None

    def _wait_for_events(self, count, timeout=2.0):
        collected = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(collected) < count:
            for event in self.listener.receive_events(timeout=100, maxevents=8):
                if event.type in self._RELEVANT_TYPES:
                    collected.append(event)
        return collected

    def test_ports_are_created_with_expected_names(self):
        info = self.transport.seq.connection_list()
        ports = next(entry[2] for entry in info if entry[1] == self.transport.client_id)
        port_names = {p[0] for p in ports}
        self.assertEqual(port_names, {"SMC In", "SMC Out", "Mixer In", "Mixer Out"})

    def test_fader_move_reaches_mixer_out_as_volume_cc(self):
        self._connect(self.transport.client_id, self.transport.mixer_out, self.listener.client_id, self.listen_port)
        source = alsaseq.Sequencer(clientname="smc-bridge-test-source")
        cap_out = alsaseq.SEQ_PORT_CAP_READ | alsaseq.SEQ_PORT_CAP_SUBS_READ
        port_type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
        out_port = source.create_simple_port("fader", port_type, cap_out)
        source.connect_ports((source.client_id, out_port), (self.transport.client_id, self.transport.smc_in))

        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_PITCHBEND)
        event.source = (source.client_id, out_port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"control.channel": 0, "control.value": 0})
        source.output_event(event)
        source.drain_output()

        received = self._wait_for_event()
        self.assertIsNotNone(received, "expected a translated CC event on Mixer Out")
        self.assertEqual(received.type, alsaseq.SEQ_EVENT_CONTROLLER)
        data = received.get_data()
        self.assertEqual(data["control.param"], 11)
        self.assertEqual(data["control.value"], 64)

    def test_mixer_volume_feedback_reaches_smc_out_as_pitchbend(self):
        self._connect(self.transport.client_id, self.transport.smc_out, self.listener.client_id, self.listen_port)
        source = alsaseq.Sequencer(clientname="smc-bridge-test-mixer")
        cap_out = alsaseq.SEQ_PORT_CAP_READ | alsaseq.SEQ_PORT_CAP_SUBS_READ
        port_type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
        out_port = source.create_simple_port("mixer-out", port_type, cap_out)
        source.connect_ports((source.client_id, out_port), (self.transport.client_id, self.transport.mixer_in))

        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_CONTROLLER)
        event.source = (source.client_id, out_port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"control.channel": 0, "control.param": 11, "control.value": 127})
        source.output_event(event)
        source.drain_output()

        received = self._wait_for_event()
        self.assertIsNotNone(received, "expected a translated pitch-bend event on SMC Out")
        self.assertEqual(received.type, alsaseq.SEQ_EVENT_PITCHBEND)
        data = received.get_data()
        self.assertEqual(data["control.channel"], 0)
        self.assertEqual(data["control.value"], 8191)

    def test_mute_press_lights_led_immediately_without_mixer_feedback(self):
        transport = Transport(
            eight((0, "Desk Mic", 11, 12), keys={"strip1.mute": KeyAction("midi", 13), "strip1.solo": KeyAction("midi", 14)}),
            clientname="smc-bridge-test-buttons",
        )
        self.addCleanup(transport.stop)
        transport.start()
        self._connect(transport.client_id, transport.mixer_out, self.listener.client_id, self.listen_port)
        self._connect(transport.client_id, transport.smc_out, self.listener.client_id, self.listen_port)
        source = alsaseq.Sequencer(clientname="smc-bridge-test-button-press")
        cap_out = alsaseq.SEQ_PORT_CAP_READ | alsaseq.SEQ_PORT_CAP_SUBS_READ
        port_type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
        out_port = source.create_simple_port("mute-button", port_type, cap_out)
        source.connect_ports((source.client_id, out_port), (transport.client_id, transport.smc_in))

        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_NOTEON)
        event.source = (source.client_id, out_port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"note.channel": 0, "note.note": 16, "note.velocity": 127})
        source.output_event(event)
        source.drain_output()

        received = self._wait_for_events(2)
        types = [event.type for event in received]
        self.assertEqual(types, [alsaseq.SEQ_EVENT_CONTROLLER, alsaseq.SEQ_EVENT_NOTEON], received)
        self.assertEqual(received[0].get_data()["control.param"], 13)
        self.assertEqual(received[0].get_data()["control.value"], 127)
        self.assertEqual(received[1].get_data()["note.note"], 16)
        self.assertEqual(received[1].get_data()["note.velocity"], 127)

        # Second press must turn the LED off as Note On velocity 0, never a
        # genuine Note Off: simple button-LED firmware (confirmed on the
        # real SMC) only reacts to Note On messages.
        event.set_data({"note.channel": 0, "note.note": 16, "note.velocity": 127})
        source.output_event(event)
        source.drain_output()
        received = self._wait_for_events(2)
        types = [event.type for event in received]
        self.assertEqual(types, [alsaseq.SEQ_EVENT_CONTROLLER, alsaseq.SEQ_EVENT_NOTEON], received)
        self.assertEqual(received[0].get_data()["control.value"], 0)
        self.assertEqual(received[1].get_data()["note.velocity"], 0)

    def _send_note(self, transport, note, velocity, event_type=None):
        source = alsaseq.Sequencer(clientname="smc-bridge-test-key-press")
        cap_out = alsaseq.SEQ_PORT_CAP_READ | alsaseq.SEQ_PORT_CAP_SUBS_READ
        port_type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
        out_port = source.create_simple_port("key", port_type, cap_out)
        source.connect_ports((source.client_id, out_port), (transport.client_id, transport.smc_in))
        event = alsaseq.SeqEvent(event_type or alsaseq.SEQ_EVENT_NOTEON)
        event.source = (source.client_id, out_port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"note.channel": 0, "note.note": note, "note.velocity": velocity})
        source.output_event(event)
        source.drain_output()

    def _transport_with_keys(self, keys):
        transport = Transport(eight(keys=keys), clientname="smc-bridge-test-keys")
        self.addCleanup(transport.stop)
        transport.start()
        self._connect(transport.client_id, transport.mixer_out, self.listener.client_id, self.listen_port)
        self._connect(transport.client_id, transport.smc_out, self.listener.client_id, self.listen_port)
        return transport

    def test_unassigned_buttons_are_inert(self):
        self._connect(self.transport.client_id, self.transport.mixer_out, self.listener.client_id, self.listen_port)
        self._connect(self.transport.client_id, self.transport.smc_out, self.listener.client_id, self.listen_port)
        with patch("smc_bridge.transport.actions.run_media") as media, patch("smc_bridge.transport.actions.run_command") as command:
            for note in (94, 93, 95, 91, 92, 46, 47, 96, 97, 98, 99, 0, 8, 16, 24):
                self._send_note(self.transport, note, 127)
            received = self._wait_for_events(1, timeout=0.5)
            self.assertEqual(received, [])
            media.assert_not_called()
            command.assert_not_called()

    def test_media_and_command_keys_run_their_actions(self):
        transport = self._transport_with_keys({
            "transport.play": KeyAction("media", media="play_pause"),
            "transport.up": KeyAction("command", command="notify-send hi"),
        })
        with patch("smc_bridge.transport.actions.run_media") as media, patch("smc_bridge.transport.actions.run_command") as command:
            self._send_note(transport, 94, 127)
            self._send_note(transport, 94, 0)
            self._send_note(transport, 96, 127)
            self._wait_for_events(1, timeout=0.5)
            media.assert_called_once_with("play_pause")
            command.assert_called_once_with("notify-send hi")

    def test_momentary_key_releases_on_note_off(self):
        transport = self._transport_with_keys({"transport.record": KeyAction("midi", 40, mode="momentary")})
        self._send_note(transport, 95, 127)
        received = self._wait_for_events(2)
        self.assertEqual([e.get_data().get("control.value", e.get_data().get("note.velocity")) for e in received], [127, 127])
        self._send_note(transport, 95, 64, alsaseq.SEQ_EVENT_NOTEOFF)
        received = self._wait_for_events(2)
        self.assertEqual(received[0].get_data()["control.param"], 40)
        self.assertEqual(received[0].get_data()["control.value"], 0)
        self.assertEqual(received[1].type, alsaseq.SEQ_EVENT_NOTEON)
        self.assertEqual(received[1].get_data()["note.velocity"], 0)


    def test_reload_releases_held_momentary_key_on_old_cc(self):
        transport = self._transport_with_keys({"transport.record": KeyAction("midi", 40, mode="momentary")})
        self._send_note(transport, 95, 127)
        self.assertEqual(len(self._wait_for_events(2)), 2)
        transport.update_config(eight(keys={"transport.record": KeyAction("midi", 41, mode="momentary")}))
        received = self._wait_for_events(2)
        self.assertEqual([e.type for e in received], [alsaseq.SEQ_EVENT_CONTROLLER, alsaseq.SEQ_EVENT_NOTEON])
        self.assertEqual((received[0].get_data()["control.param"], received[0].get_data()["control.value"]), (40, 0))
        self.assertEqual((received[1].get_data()["note.note"], received[1].get_data()["note.velocity"]), (95, 0))
        self._send_note(transport, 95, 0)
        self.assertEqual(self._wait_for_events(1, timeout=0.5), [])


if __name__ == "__main__":
    unittest.main()
