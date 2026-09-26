"""ALSA Sequencer transport: four ports bridging the SMC hardware and jack_mixer.

Exposes exactly the four ports described in primer.md's BRIDGE ARCHITECTURE
section, as an ordinary ALSA Sequencer client so PipeWire surfaces them for
manual patching. Never connects itself; the user patches SMC <-> this client
<-> jack_mixer by hand.
"""
import logging
import threading

from pyalsa import alsaseq

from .bridge import Bridge

LOG = logging.getLogger(__name__)

CLIENT_NAME = "SMC Bridge"

# jack_mixer's own MIDI channel for CC feedback is not yet confirmed
# (primer.md open design question 3); channel 0 matches every other
# channel-0 assumption made so far (the encoders are all channel 0 too).
MIXER_MIDI_CHANNEL = 0

_PORT_TYPE = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC | alsaseq.SEQ_PORT_TYPE_APPLICATION
_CAP_IN = alsaseq.SEQ_PORT_CAP_WRITE | alsaseq.SEQ_PORT_CAP_SUBS_WRITE
_CAP_OUT = alsaseq.SEQ_PORT_CAP_READ | alsaseq.SEQ_PORT_CAP_SUBS_READ


class Transport:
    """Owns the ALSA client/ports and a background thread pumping events through a Bridge."""

    def __init__(self, mappings, clientname=CLIENT_NAME):
        self.seq = alsaseq.Sequencer(clientname=clientname)
        self.bridge = Bridge(list(mappings))
        self._lock = threading.Lock()
        self.smc_in = self.seq.create_simple_port("SMC In", _PORT_TYPE, _CAP_IN)
        self.smc_out = self.seq.create_simple_port("SMC Out", _PORT_TYPE, _CAP_OUT)
        self.mixer_in = self.seq.create_simple_port("Mixer In", _PORT_TYPE, _CAP_IN)
        self.mixer_out = self.seq.create_simple_port("Mixer Out", _PORT_TYPE, _CAP_OUT)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="smc-midi", daemon=True)

    @property
    def client_id(self):
        return self.seq.client_id

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def update_mappings(self, mappings):
        with self._lock:
            self.bridge.update_mappings(mappings)

    def _send_controller(self, port, channel, param, value):
        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_CONTROLLER)
        event.source = (self.seq.client_id, port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"control.channel": channel, "control.param": param, "control.value": value})
        self.seq.output_event(event)

    def _send_pitchbend(self, port, channel, value):
        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_PITCHBEND)
        event.source = (self.seq.client_id, port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"control.channel": channel, "control.value": value})
        self.seq.output_event(event)

    def _send_note(self, port, note, is_on):
        # Always Note On (velocity 0 = off), not a genuine Note Off: simple
        # button-LED firmware commonly only reacts to Note On messages and
        # silently ignores real Note Off events. Confirmed necessary on the
        # SMC hardware 2026-09-25 (see primer.md).
        event = alsaseq.SeqEvent(alsaseq.SEQ_EVENT_NOTEON)
        event.source = (self.seq.client_id, port)
        event.dest = (alsaseq.SEQ_ADDRESS_SUBSCRIBERS, 0)
        event.set_data({"note.channel": 0, "note.note": note, "note.velocity": 127 if is_on else 0})
        self.seq.output_event(event)

    def _run(self):
        while not self._stop.is_set():
            try:
                events = self.seq.receive_events(timeout=200, maxevents=32)
            except alsaseq.SequencerError as error:
                LOG.error("MIDI receive error: %s", error)
                continue
            if not events:
                continue
            with self._lock:
                for event in events:
                    self._handle(event)
            self.seq.drain_output()

    def _handle(self, event):
        LOG.debug("event type=%s source=%s dest=%s data=%s", event.type, event.source, event.dest, event.get_data())
        port = event.dest[1]
        if port == self.smc_in:
            self._handle_smc(event)
        elif port == self.mixer_in:
            self._handle_mixer(event)

    def _handle_smc(self, event):
        data = event.get_data()
        if event.type == alsaseq.SEQ_EVENT_PITCHBEND:
            result = self.bridge.on_fader(data["control.channel"], data["control.value"])
            if result:
                cc, cc_value = result
                self._send_controller(self.mixer_out, MIXER_MIDI_CHANNEL, cc, cc_value)
        elif event.type == alsaseq.SEQ_EVENT_CONTROLLER:
            result = self.bridge.on_encoder(data["control.param"], data["control.value"])
            if result:
                cc, cc_value = result
                self._send_controller(self.mixer_out, MIXER_MIDI_CHANNEL, cc, cc_value)
        elif event.type == alsaseq.SEQ_EVENT_NOTEON:
            note, velocity = data["note.note"], data["note.velocity"]
            # Bottom-row transport buttons (see TRANSPORT_NOTES in bridge.py)
            # are recognized in hardware terms but intentionally not acted
            # on here; media_control.py has a parked play/pause feature.
            for handler in (self.bridge.on_mute_button, self.bridge.on_solo_button):
                result = handler(note, velocity)
                if result:
                    cc, cc_value = result
                    self._send_controller(self.mixer_out, MIXER_MIDI_CHANNEL, cc, cc_value)
                    # Light the LED from our own toggle result immediately,
                    # rather than waiting on jack_mixer to echo it back —
                    # that echo may not arrive symmetrically for both
                    # on and off (see primer.md).
                    self._send_note(self.smc_out, note, cc_value >= 64)
                    break

    def _handle_mixer(self, event):
        if event.type != alsaseq.SEQ_EVENT_CONTROLLER:
            return
        data = event.get_data()
        cc, value = data["control.param"], data["control.value"]
        result = self.bridge.on_mixer_volume(cc, value)
        if result:
            channel, pitch = result
            self._send_pitchbend(self.smc_out, channel, pitch)
            return
        if self.bridge.on_mixer_pan(cc, value) is not None:
            return
        result = self.bridge.on_mixer_mute(cc, value)
        if result:
            note, is_on = result
            self._send_note(self.smc_out, note, is_on)
            return
        result = self.bridge.on_mixer_solo(cc, value)
        if result:
            note, is_on = result
            self._send_note(self.smc_out, note, is_on)
