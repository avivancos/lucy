import pytest
from pydantic import ValidationError

from lucy.transport.schema import (
    ENVELOPE_FIELDS,
    MESSAGE_TYPES,
    BargeIn,
    Envelope,
    SttFinal,
    SttPartial,
    TtsPlayback,
    TtsSpeak,
    UnknownControlMessage,
    parse_event,
    to_wire,
)


def _wire(type_: str, payload, **env):
    env.setdefault("session_id", "sess_1")
    env.setdefault("seq", 1)
    env.setdefault("ts_ms", 1000)
    return to_wire(Envelope(type=type_, **env), payload)


def test_every_message_type_round_trips():
    # one representative payload per registered type, built from its own model
    samples = {
        "stt.partial": SttPartial(text="he", stability=0.4, provider="local"),
        "stt.final": SttFinal(text="hello", provider="local", stt_ms=40),
        "tts.playback": TtsPlayback(utterance_id="u1", state="started", mark_chars=0),
        "barge_in": BargeIn(at_ms=1200, during="speaking", utterance_id="u1"),
        "tts.speak": TtsSpeak(utterance_id="u1", text="hi there", flush=True),
    }
    for type_, payload in samples.items():
        wire = _wire(type_, payload, turn_id="t1")
        event = parse_event(wire)
        assert event.envelope.type == type_
        assert event.envelope.session_id == "sess_1"
        assert event.payload == payload
        assert to_wire(event.envelope, event.payload) == wire


def test_registry_covers_every_declared_type():
    # sanity: the round-trip test above should be extendable to all types
    assert {"stt.partial", "stt.final", "tts.playback", "barge_in", "tts.speak"} <= set(
        MESSAGE_TYPES
    )
    assert len(MESSAGE_TYPES) >= 15  # upstream + downstream families


def test_unknown_type_raises():
    wire = {"v": 1, "type": "totally.made.up", "session_id": "s", "seq": 1, "ts_ms": 1}
    with pytest.raises(UnknownControlMessage):
        parse_event(wire)


def test_extra_payload_field_is_rejected():
    wire = _wire("stt.final", SttFinal(text="hi", provider="local", stt_ms=10))
    wire["surprise"] = "nope"
    with pytest.raises(ValidationError):
        parse_event(wire)


def test_extra_envelope_field_is_rejected():
    with pytest.raises(ValidationError):
        Envelope(type="stt.final", session_id="s", seq=1, ts_ms=1, bogus=True)


def test_version_field_present_and_defaults_to_one():
    env = Envelope(type="stt.final", session_id="s", seq=1, ts_ms=1)
    assert env.v == 1
    assert "v" in ENVELOPE_FIELDS
    parsed = parse_event(_wire("stt.final", SttFinal(text="x", provider="p", stt_ms=1)))
    assert parsed.envelope.v == 1


def test_stability_and_state_are_constrained():
    with pytest.raises(ValidationError):
        SttPartial(text="x", stability=1.5, provider="p")  # 0..1
    with pytest.raises(ValidationError):
        TtsPlayback(utterance_id="u", state="bogus", mark_chars=0)  # literal
    with pytest.raises(ValidationError):
        BargeIn(at_ms=1, during="dancing", utterance_id=None)  # literal
