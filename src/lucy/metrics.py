"""Metrics, sentiment, funnel, and cost accounting."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Dict, Optional

from pydantic import BaseModel, Field, model_validator

from lucy.specs import FunnelStage, SentimentLabel

if TYPE_CHECKING:
    from lucy.observe import Tracer


class CostBreakdown(BaseModel):
    stt_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    llm_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tts_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    telephony_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    rag_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    mcp_tool_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    infra_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    billable_audio_minutes: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_derived_values_are_finite(self) -> "CostBreakdown":
        if not math.isfinite(self.total_cost) or not math.isfinite(
            self.cost_per_minute
        ):
            raise ValueError("derived cost values must be finite")
        return self

    @property
    def total_cost(self) -> float:
        return (
            self.stt_cost
            + self.llm_cost
            + self.tts_cost
            + self.telephony_cost
            + self.rag_cost
            + self.mcp_tool_cost
            + self.infra_cost
        )

    @property
    def cost_per_minute(self) -> float:
        return self.total_cost / self.billable_audio_minutes


class SentimentScore(BaseModel):
    label: SentimentLabel
    confidence: float = Field(ge=0.0, le=1.0)
    model: str


class FunnelEvent(BaseModel):
    session_id: str
    stage: FunnelStage
    confidence: float = Field(ge=0.0, le=1.0)
    crm_payload: Dict[str, str] = Field(default_factory=dict)


class CrmMetricEvent(BaseModel):
    session_id: str
    lead_id: str
    sentiment: SentimentScore
    funnel: FunnelEvent
    emitted_at_ms: int = Field(ge=0)

    @property
    def crm_ready_payload(self) -> Dict[str, str]:
        return {
            "session_id": self.session_id,
            "lead_id": self.lead_id,
            "sentiment": self.sentiment.label.value,
            "funnel_stage": self.funnel.stage.value,
            "confidence": str(self.funnel.confidence),
        }


class LatencyWaterfall(BaseModel):
    stt_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    rag_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    llm_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    mcp_tools_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tts_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    transport_ms: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    @property
    def total_ms(self) -> float:
        return (
            self.stt_ms
            + self.rag_ms
            + self.llm_ms
            + self.mcp_tools_ms
            + self.tts_ms
            + self.transport_ms
        )


class RealtimeMetricEvent(BaseModel):
    event_id: str
    session_id: str
    lead_id: str
    sentiment: SentimentScore
    funnel: FunnelEvent
    cost: CostBreakdown
    emitted_at_ms: int = Field(ge=0)

    def dashboard_payload(self) -> Dict[str, object]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "lead_id": self.lead_id,
            "sentiment": self.sentiment.model_dump(mode="json"),
            "funnel": self.funnel.model_dump(mode="json"),
            "crm": {
                "session_id": self.session_id,
                "lead_id": self.lead_id,
                "funnel_stage": self.funnel.stage.value,
                "sentiment": self.sentiment.label.value,
            },
            "cost": {
                **self.cost.model_dump(mode="json"),
                "total_cost": self.cost.total_cost,
                "cost_per_minute": self.cost.cost_per_minute,
            },
            "emitted_at_ms": self.emitted_at_ms,
        }


def emit_cost(
    cost: CostBreakdown,
    *,
    session_id: str,
    turn_id: Optional[str] = None,
    tracer: Optional["Tracer"] = None,
) -> None:
    """Emit a ``cost`` telemetry event for a session (and optionally a turn).

    Resolves the process-global tracer when none is injected and no-ops when
    tracing is disabled, so cost accounting routes through the same single
    tracer as spans, turns, and tool calls (card 25). The import is local to
    avoid the ``lucy.observe`` -> ``lucy.metrics`` cycle.
    """
    from lucy.observe import get_tracer

    chosen = tracer if tracer is not None else get_tracer()
    if not chosen.enabled:
        return
    chosen.cost(session_id=session_id, cost=cost, turn_id=turn_id)


_MOVED_TO_TESTING = ("LocalMetricEventChannel",)


def __getattr__(name: str) -> object:
    """Deprecation shim: the SSE channel fixture moved to lucy.testing (card 22)."""
    if name in _MOVED_TO_TESTING:
        import warnings

        from lucy import testing

        warnings.warn(
            "lucy.metrics.%s moved to lucy.testing; import it from lucy.testing" % name,
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(testing, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
