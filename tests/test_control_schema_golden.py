import json
from pathlib import Path

from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.golden import canonical_dumps, golden_messages, write_golden
from lucy.transport.schema import (
    MESSAGE_TYPES,
    Envelope,
    SttFinal,
    TtsSpeak,
    TtsStreamEnd,
    parse_event,
    to_wire,
)

ROOT = Path(__file__).parents[1]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "control_schema"


def test_every_schema_payload_model_has_a_golden_message():
    assert set(golden_messages()) == set(MESSAGE_TYPES)
    assert len(golden_messages()) == 27


def test_golden_messages_round_trip_through_parse_event():
    for message in golden_messages().values():
        event = parse_event(message)
        assert to_wire(event.envelope, event.payload) == message


def test_committed_golden_files_match_regenerated_canonical_bytes(tmp_path):
    regenerated = write_golden(tmp_path)
    committed = sorted(GOLDEN_DIR.glob("*.json"))
    assert len(regenerated) == len(committed) == len(MESSAGE_TYPES)
    for actual in regenerated:
        expected = GOLDEN_DIR / actual.name
        assert actual.read_bytes() == expected.read_bytes()


async def test_dev_gateway_emissions_canonicalize_and_reparse_byte_identical():
    gateway = LocalGatewaySimulator(booking_happy_path(), ManualClock())
    events = []
    async for event in gateway.events():
        events.append(event)
        if isinstance(event.payload, SttFinal):
            await gateway.send(
                Envelope(
                    type="tts.speak",
                    session_id=event.envelope.session_id,
                    turn_id=event.envelope.turn_id,
                    seq=0,
                    ts_ms=0,
                ),
                TtsSpeak(utterance_id="utt", text="Hello.", flush=True),
            )
            await gateway.send(
                Envelope(
                    type="tts.stream_end",
                    session_id=event.envelope.session_id,
                    turn_id=event.envelope.turn_id,
                    seq=0,
                    ts_ms=0,
                ),
                TtsStreamEnd(),
            )

    for event in events:
        message = to_wire(event.envelope, event.payload)
        encoded = canonical_dumps(message)
        reparsed = parse_event(json.loads(encoded))
        assert canonical_dumps(to_wire(reparsed.envelope, reparsed.payload)) == encoded
