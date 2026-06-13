"""Metrics, sentiment, funnel, and cost accounting."""

from __future__ import annotations

import json
from typing import AsyncIterator, Dict, List

from pydantic import BaseModel, Field

from lucy.specs import FunnelStage, SentimentLabel


class CostBreakdown(BaseModel):
    stt_cost: float = 0.0
    llm_cost: float = 0.0
    tts_cost: float = 0.0
    telephony_cost: float = 0.0
    rag_cost: float = 0.0
    mcp_tool_cost: float = 0.0
    infra_cost: float = 0.0
    billable_audio_minutes: float = Field(gt=0)

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
    stt_ms: float = 0.0
    rag_ms: float = 0.0
    llm_ms: float = 0.0
    mcp_tools_ms: float = 0.0
    tts_ms: float = 0.0
    transport_ms: float = 0.0

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


class LocalMetricEventChannel:
    def __init__(self, events: List[RealtimeMetricEvent]):
        self.events = events

    async def sse(self) -> AsyncIterator[str]:
        for event in self.events:
            yield "event: metric\n"
            yield "data: %s\n\n" % json.dumps(
                event.dashboard_payload(),
                sort_keys=True,
            )
