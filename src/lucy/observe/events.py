"""Telemetry event models mirroring the wire protocol v1.

Each event corresponds to a row in ``docs/telemetry-wire-v1.md`` "Event types".
Within wire v1, changes are additive only. ``to_wire()`` returns the JSON-able
payload (including derived cost fields) that exporters serialize.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from lucy.metrics import CostBreakdown, LatencyWaterfall

WIRE_VERSION = "1"


class TelemetryEventBase(BaseModel):
    event_id: str
    session_id: str
    emitted_at_ms: int = Field(ge=0)

    def to_wire(self) -> Dict[str, object]:
        return self.model_dump(mode="json")


class SessionStartedEvent(TelemetryEventBase):
    type: Literal["session.started"] = "session.started"
    agent_name: str
    spec_hash: str
    environment: str
    transport: str


class SessionEndedEvent(TelemetryEventBase):
    type: Literal["session.ended"] = "session.ended"
    reason: str
    duration_ms: int = Field(ge=0)
    billable_audio_minutes: float = Field(ge=0)


class TurnEvent(TelemetryEventBase):
    type: Literal["turn"] = "turn"
    turn_id: str
    turn_index: int = Field(ge=0)
    latency_waterfall: LatencyWaterfall
    interrupted: bool = False
    timeout_events: List[str] = Field(default_factory=list)


class SpanEvent(TelemetryEventBase):
    type: Literal["span"] = "span"
    span_id: str
    turn_id: str
    name: str
    status: Literal["ok", "fallback", "error", "cancelled"]
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    parent_id: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)


class CostEvent(TelemetryEventBase):
    type: Literal["cost"] = "cost"
    cost: CostBreakdown
    turn_id: Optional[str] = None

    def to_wire(self) -> Dict[str, object]:
        data = super().to_wire()
        cost = self.cost.model_dump(mode="json")
        cost["total_cost"] = self.cost.total_cost
        cost["cost_per_minute"] = self.cost.cost_per_minute
        data["cost"] = cost
        return data


class BusinessEvent(TelemetryEventBase):
    type: Literal["business"] = "business"
    funnel_stage: str
    funnel_confidence: float = Field(ge=0.0, le=1.0)
    sentiment_label: str
    sentiment_confidence: float = Field(ge=0.0, le=1.0)
    turn_id: Optional[str] = None


class ToolCallEvent(TelemetryEventBase):
    type: Literal["tool_call"] = "tool_call"
    turn_id: str
    server: str
    tool: str
    allowed: bool
    latency_ms: float = Field(ge=0.0)
    error: Optional[str] = None
    arguments: Dict[str, object] = Field(default_factory=dict)


class TranscriptEvent(TelemetryEventBase):
    type: Literal["transcript"] = "transcript"
    turn_id: str
    role: Literal["caller", "agent"]
    text: str


class AudioRefEvent(TelemetryEventBase):
    type: Literal["audio_ref"] = "audio_ref"
    blob_id: str
    turn_id: Optional[str] = None
    upload_url_requested: bool = False


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
