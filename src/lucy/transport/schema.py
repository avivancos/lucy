"""Control-channel wire schema, version 1 (ADR 0011).

Every control message is a flat JSON object = the :class:`Envelope` header
(``v``, ``type``, ``session_id``, ``turn_id``, ``seq``, ``ts_ms``) merged with a
typed payload for its ``type``. No message ever carries audio bytes (ADR 0004).
All models are ``extra="forbid"``: an unknown field is a hard error, so the
schema is the locked contract every transport adapter and the Rust gateway
build against.
"""

from __future__ import annotations

from typing import Dict, List, Literal, NamedTuple, Optional, Type

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(_Strict):
    v: int = 1
    type: str
    session_id: str
    turn_id: Optional[str] = None
    seq: int
    ts_ms: int


ENVELOPE_FIELDS = frozenset(Envelope.model_fields)


# -- upstream payloads (gateway -> python) -----------------------------------


class SessionStarted(_Strict):
    transport: str
    caller: str
    codecs: List[str]


class VadSpeechStart(_Strict):
    at_ms: int


class VadSpeechEnd(_Strict):
    at_ms: int
    speech_ms: int


class SttPartial(_Strict):
    text: str
    stability: float = Field(ge=0.0, le=1.0)
    provider: str


class SttFinal(_Strict):
    text: str
    provider: str
    stt_ms: int


class Dtmf(_Strict):
    digit: str


class AmdResult(_Strict):
    outcome: Literal["human", "machine", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)


class TtsPlayback(_Strict):
    utterance_id: str
    state: Literal["started", "mark", "finished", "flushed"]
    mark_chars: int = 0


class BargeIn(_Strict):
    at_ms: int
    during: Literal["speaking", "thinking"]
    utterance_id: Optional[str] = None


class TransportMetrics(_Strict):
    jitter_ms: float
    rtt_ms: float
    packet_loss: float


class SessionEnded(_Strict):
    reason: str


# -- downstream payloads (python -> gateway) ---------------------------------


class SessionConfigure(_Strict):
    stt: str
    tts: str
    vad: str


class TtsSpeak(_Strict):
    utterance_id: str
    text: str
    flush: bool = False


class TtsCancel(_Strict):
    utterance_id: str  # a specific utterance, or the literal "all"


class TtsStreamEnd(_Strict):
    """No more ``tts.speak`` directives follow for the current turn - the
    agent's speech stream is complete or was cancelled. Sent exactly once per
    turn; the dev gateway uses it as the deterministic turn barrier when
    echoing playback (card 64). A real media plane derives the same boundary
    from its own playout queue."""


class DtmfSend(_Strict):
    digits: str


class Transfer(_Strict):
    target: str


class Dial(_Strict):
    target: str
    caller_id: str
    timeout_ms: int


class Hold(_Strict):
    state: Literal["hold", "resume"]
    music: bool = False


class SessionEnd(_Strict):
    reason: str


UPSTREAM_TYPES: Dict[str, Type[BaseModel]] = {
    "session.started": SessionStarted,
    "vad.speech_start": VadSpeechStart,
    "vad.speech_end": VadSpeechEnd,
    "stt.partial": SttPartial,
    "stt.final": SttFinal,
    "dtmf": Dtmf,
    "amd.result": AmdResult,
    "tts.playback": TtsPlayback,
    "barge_in": BargeIn,
    "transport.metrics": TransportMetrics,
    "session.ended": SessionEnded,
}

DOWNSTREAM_TYPES: Dict[str, Type[BaseModel]] = {
    "session.configure": SessionConfigure,
    "tts.speak": TtsSpeak,
    "tts.cancel": TtsCancel,
    "tts.stream_end": TtsStreamEnd,
    "dtmf.send": DtmfSend,
    "transfer": Transfer,
    "dial": Dial,
    "hold": Hold,
    "session.end": SessionEnd,
}

MESSAGE_TYPES: Dict[str, Type[BaseModel]] = {**UPSTREAM_TYPES, **DOWNSTREAM_TYPES}


class UnknownControlMessage(ValueError):
    """Raised when a control message names a ``type`` not in the schema."""


class ControlEvent(NamedTuple):
    envelope: Envelope
    payload: BaseModel


def parse_event(raw: Dict[str, object]) -> ControlEvent:
    """Validate a flat control-message dict into its envelope + typed payload.

    Unknown ``type`` raises :class:`UnknownControlMessage`; any field that is
    neither an envelope field nor a payload field of that type is rejected by
    the payload model (``extra="forbid"``).
    """
    envelope = Envelope(**{k: v for k, v in raw.items() if k in ENVELOPE_FIELDS})
    model = MESSAGE_TYPES.get(envelope.type)
    if model is None:
        raise UnknownControlMessage("unknown control message type: %r" % envelope.type)
    payload = model(**{k: v for k, v in raw.items() if k not in ENVELOPE_FIELDS})
    return ControlEvent(envelope, payload)


def to_wire(envelope: Envelope, payload: BaseModel) -> Dict[str, object]:
    """Flatten an envelope + payload back into a single wire dict."""
    return {**envelope.model_dump(), **payload.model_dump()}
