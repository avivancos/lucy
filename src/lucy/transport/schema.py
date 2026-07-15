"""Control-channel wire schema, version 1 (ADR 0011).

Every control message is a flat JSON object = the :class:`Envelope` header
(``v``, ``type``, ``session_id``, ``turn_id``, ``seq``, ``ts_ms``) merged with a
typed payload for its ``type``. No message ever carries audio bytes (ADR 0004).
All models are ``extra="forbid"``: an unknown field is a hard error, so the
schema is the locked contract every transport adapter and the Rust gateway
build against.
"""

from __future__ import annotations

from typing import Annotated, Dict, List, Literal, NamedTuple, Optional, Type, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from lucy.limits import MAX_CONTROL_DURATION_MS, MAX_CONTROL_TIMESTAMP_MS

ControlTimestamp = Annotated[int, Field(strict=True, ge=0, le=MAX_CONTROL_TIMESTAMP_MS)]
ControlDuration = Annotated[int, Field(strict=True, ge=0, le=MAX_CONTROL_DURATION_MS)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Envelope(_Strict):
    v: int = 1
    type: str
    session_id: str
    turn_id: Optional[str] = None
    seq: int
    ts_ms: ControlTimestamp


ENVELOPE_FIELDS = frozenset(Envelope.model_fields)


# -- upstream payloads (gateway -> python) -----------------------------------


class SessionStarted(_Strict):
    transport: str
    caller: str
    codecs: List[str]
    features: List[str] = Field(default_factory=list)


class VadSpeechStart(_Strict):
    at_ms: ControlTimestamp


class VadSpeechEnd(_Strict):
    at_ms: ControlTimestamp
    speech_ms: ControlDuration


class SttPartial(_Strict):
    text: str
    stability: float = Field(ge=0.0, le=1.0)
    provider: str


class SttFinal(_Strict):
    text: str
    provider: str
    stt_ms: ControlDuration


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
    at_ms: ControlTimestamp
    during: Literal["speaking", "thinking"]
    utterance_id: Optional[str] = None


class TransportMetrics(_Strict):
    jitter_ms: float
    rtt_ms: float
    packet_loss: float


class SessionEnded(_Strict):
    reason: str


RECORDING_FEATURE = "recording"
RecordingLeg = Literal["caller", "agent", "mixed"]
OPAQUE_RECORDING_REF_PATTERN = r"^[A-Za-z0-9._:-]+$"
OPAQUE_RECORDING_REF_MAX_LENGTH = 128
PHONE_LIKE_RECORDING_REF_MIN_DIGITS = 8


def validate_opaque_recording_ref(value: str) -> str:
    if sum(character.isdigit() for character in value) >= (
        PHONE_LIKE_RECORDING_REF_MIN_DIGITS
    ):
        raise ValueError("recording references cannot contain phone-like values")
    return value


OpaqueRecordingRef = Annotated[
    str,
    Field(
        min_length=1,
        max_length=OPAQUE_RECORDING_REF_MAX_LENGTH,
        pattern=OPAQUE_RECORDING_REF_PATTERN,
    ),
    AfterValidator(validate_opaque_recording_ref),
]
RecordingContainer = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]*$")
]


class RecordingStarted(_Strict):
    recording_id: OpaqueRecordingRef
    leg: RecordingLeg
    blob_id: OpaqueRecordingRef
    consent_ref: OpaqueRecordingRef


class RecordingUploaded(_Strict):
    recording_id: OpaqueRecordingRef
    leg: RecordingLeg
    blob_id: OpaqueRecordingRef
    upload_url_ref: OpaqueRecordingRef
    duration_ms: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    container: RecordingContainer
    consent_ref: OpaqueRecordingRef


class RecordingFailed(_Strict):
    recording_id: OpaqueRecordingRef
    error_code: str
    retryable: bool = False


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


class RealtimeConnect(_Strict):
    provider: str
    model: str


class RealtimeToolResult(_Strict):
    call_id: str
    output_json: str


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


class RecordingStart(_Strict):
    recording_id: OpaqueRecordingRef
    leg: RecordingLeg
    blob_id: OpaqueRecordingRef
    upload_url_ref: OpaqueRecordingRef
    container: RecordingContainer
    consent_ref: OpaqueRecordingRef


class RecordingStop(_Strict):
    recording_id: OpaqueRecordingRef


DownstreamDirective = Union[
    SessionConfigure,
    TtsSpeak,
    TtsCancel,
    TtsStreamEnd,
    RealtimeConnect,
    RealtimeToolResult,
    DtmfSend,
    Transfer,
    Dial,
    Hold,
    SessionEnd,
    RecordingStart,
    RecordingStop,
]


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
    "recording.started": RecordingStarted,
    "recording.uploaded": RecordingUploaded,
    "recording.failed": RecordingFailed,
}

DOWNSTREAM_TYPES: Dict[str, Type[BaseModel]] = {
    "session.configure": SessionConfigure,
    "tts.speak": TtsSpeak,
    "tts.cancel": TtsCancel,
    "tts.stream_end": TtsStreamEnd,
    "realtime.connect": RealtimeConnect,
    "realtime.tool_result": RealtimeToolResult,
    "dtmf.send": DtmfSend,
    "transfer": Transfer,
    "dial": Dial,
    "hold": Hold,
    "session.end": SessionEnd,
    "recording.start": RecordingStart,
    "recording.stop": RecordingStop,
}
DOWNSTREAM_TYPE_BY_MODEL = {model: name for name, model in DOWNSTREAM_TYPES.items()}

MESSAGE_TYPES: Dict[str, Type[BaseModel]] = {**UPSTREAM_TYPES, **DOWNSTREAM_TYPES}


class UnknownControlMessage(ValueError):
    """Raised when a control message names a ``type`` not in the schema."""


class ControlEvent(NamedTuple):
    envelope: Envelope
    payload: BaseModel


def downstream_type(payload: DownstreamDirective) -> str:
    """Return the registered v1 wire name for a downstream directive."""
    type_name = DOWNSTREAM_TYPE_BY_MODEL.get(type(payload))
    if type_name is None:
        raise TypeError("payload is not a registered downstream directive")
    return type_name


def is_downstream_directive(payload: object) -> bool:
    """Return whether ``payload`` is registered on the v1 downstream wire."""
    return type(payload) in DOWNSTREAM_TYPE_BY_MODEL


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
