"""Telemetry event models mirroring the wire protocol v1.

Each event corresponds to a row in ``docs/telemetry-wire-v1.md`` "Event types".
After the first public freeze, wire-v1 changes are additive only; this normative
draft still permits producer/server alignment before card 46. ``to_wire()``
returns the JSON-able payload (including derived cost fields) that exporters
serialize.
"""

from __future__ import annotations

from typing import Annotated, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from lucy.limits import MAX_PRICEBOOK_VERSION_LENGTH, MAX_USAGE_UNITS
from lucy.jurisdiction import IsoCountryCode
from lucy.metrics import CostBreakdown, CostComponent, LatencyWaterfall
from lucy.privacy import contains_sensitive_text
from lucy.providers import ProviderIdentity
from lucy.specs import CpaasTransportMode, CpaasTransportSpec
from lucy.transport.schema import OpaqueRecordingRef, RecordingContainer

WIRE_VERSION = "1"
RAG_RETRIEVAL_SPAN_NAME = "rag.retrieve"
RAG_QUERY_ATTRIBUTE = "rag.query"
RAG_CACHE_HIT_ATTRIBUTE = "rag.cache_hit"
RAG_DEADLINE_EXCEEDED_ATTRIBUTE = "rag.deadline_exceeded"
RAG_PROMPT_GROUNDING_IDS_ATTRIBUTE = "rag.prompt_included_grounding_ids"
RAG_CHUNKS_ATTRIBUTE = "rag.chunks"
NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeFiniteFloat = Annotated[
    float, Field(ge=0.0, le=MAX_USAGE_UNITS, allow_inf_nan=False)
]
CostAttributionKey = Annotated[str, Field(min_length=1, max_length=64)]
PriceBookVersion = Annotated[
    str, Field(min_length=1, max_length=MAX_PRICEBOOK_VERSION_LENGTH)
]


class CpaasCostMetadata(BaseModel):
    """Typed CPaaS dimensions carried through wire-v1 event tags."""

    direction: Literal["inbound", "outbound"]
    provider: CpaasTransportMode
    country_code: IsoCountryCode
    billable_seconds: NonNegativeFiniteFloat
    cost_component: Literal["telephony_cost"] = "telephony_cost"

    def to_tags(self) -> Dict[str, str]:
        seconds = f"{self.billable_seconds:.6f}".rstrip("0").rstrip(".")
        return {
            "telephony.direction": self.direction,
            "telephony.provider": CpaasTransportSpec(
                provider=self.provider
            ).spec_string,
            "telephony.country_code": self.country_code,
            "telephony.billable_seconds": seconds or "0",
            "telephony.cost_component": self.cost_component,
        }


class TelemetryEventBase(BaseModel):
    _export_approval: object = PrivateAttr(default=None)

    event_id: str
    session_id: NonEmptyString
    emitted_at_ms: int = Field(ge=0)
    tags: Dict[str, str] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("event_id must be a valid UUID") from exc
        return value

    def to_wire(self) -> Dict[str, object]:
        return self.model_dump(mode="json")


class SessionStartedEvent(TelemetryEventBase):
    type: Literal["session.started"] = "session.started"
    agent_name: NonEmptyString
    spec_hash: NonEmptyString
    environment: NonEmptyString
    transport: NonEmptyString
    agent_version: Optional[str] = None
    graph_hash: Optional[str] = None
    thread_id: Optional[str] = None


class SessionEndedEvent(TelemetryEventBase):
    type: Literal["session.ended"] = "session.ended"
    reason: NonEmptyString
    duration_ms: int = Field(ge=0)
    billable_audio_minutes: float = Field(ge=0, allow_inf_nan=False)


class TurnEvent(TelemetryEventBase):
    type: Literal["turn"] = "turn"
    turn_id: NonEmptyString
    turn_index: int = Field(ge=0)
    latency_waterfall: LatencyWaterfall
    interrupted: bool = False
    timeout_events: List[str] = Field(default_factory=list)


class SpanEvent(TelemetryEventBase):
    type: Literal["span"] = "span"
    span_id: NonEmptyString
    turn_id: Optional[NonEmptyString] = None
    name: NonEmptyString
    status: Literal["ok", "fallback", "error", "cancelled"]
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    parent_id: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)


class CostEvent(TelemetryEventBase):
    type: Literal["cost"] = "cost"
    cost: CostBreakdown
    turn_id: Optional[str] = None
    pricebook_version: Optional[PriceBookVersion] = None
    attribution: Dict[CostAttributionKey, NonNegativeFiniteFloat] = Field(
        default_factory=dict
    )
    provider_attribution: Dict[CostComponent, ProviderIdentity] = Field(
        default_factory=dict
    )

    def to_wire(self) -> Dict[str, object]:
        data = super().to_wire()
        cost = self.cost.model_dump(mode="json")
        cost["total_cost"] = self.cost.total_cost
        cost["cost_per_minute"] = self.cost.cost_per_minute
        data["cost"] = cost
        return data


class BusinessEvent(TelemetryEventBase):
    type: Literal["business"] = "business"
    funnel_stage: NonEmptyString
    funnel_confidence: float = Field(ge=0.0, le=1.0)
    sentiment_label: NonEmptyString
    sentiment_confidence: float = Field(ge=0.0, le=1.0)
    turn_id: Optional[str] = None


class ToolCallEvent(TelemetryEventBase):
    type: Literal["tool_call"] = "tool_call"
    turn_id: NonEmptyString
    server: NonEmptyString
    tool: NonEmptyString
    allowed: bool
    latency_ms: float = Field(ge=0.0, allow_inf_nan=False)
    error: Optional[str] = None
    arguments: Dict[str, object] = Field(default_factory=dict)


class TranscriptEvent(TelemetryEventBase):
    type: Literal["transcript"] = "transcript"
    turn_id: NonEmptyString
    role: Literal["caller", "agent"]
    text: str


class AudioRefEvent(TelemetryEventBase):
    type: Literal["audio_ref"] = "audio_ref"
    blob_id: OpaqueRecordingRef
    turn_id: Optional[str] = None
    upload_url_requested: bool = False
    recording_id: Optional[OpaqueRecordingRef] = None
    leg: Optional[Literal["caller", "agent", "mixed"]] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    byte_count: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    container: Optional[RecordingContainer] = None
    consent_ref: Optional[OpaqueRecordingRef] = None

    @field_validator("blob_id", "recording_id", "consent_ref")
    @classmethod
    def validate_sensitive_reference(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and contains_sensitive_text(value):
            raise ValueError("recording reference cannot contain sensitive values")
        return value


TelemetryEvent = Union[
    SessionStartedEvent,
    SessionEndedEvent,
    TurnEvent,
    SpanEvent,
    CostEvent,
    BusinessEvent,
    ToolCallEvent,
    TranscriptEvent,
    AudioRefEvent,
]
