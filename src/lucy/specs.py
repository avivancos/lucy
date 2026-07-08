"""Orthogonal specification models for Lucy agents."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class FunnelStage(str, Enum):
    QUALIFIED = "qualified"
    INTERESTED = "interested"
    OBJECTION = "objection"
    BOOKED = "booked"
    ESCALATION = "escalation"
    FAILED_BOOKING = "failed_booking"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class VoiceModulationSpec(BaseModel):
    pace: float = Field(default=1.0, ge=0.5, le=2.0)
    warmth: float = Field(default=0.5, ge=0.0, le=1.0)
    pause_ms: int = Field(default=180, ge=0, le=2000)
    emphasis: float = Field(default=0.35, ge=0.0, le=1.0)
    pronunciation_hints: Dict[str, str] = Field(default_factory=dict)
    language: str = "en"
    accent: Optional[str] = None
    interruption_style: str = "graceful"


class VoiceSpec(BaseModel):
    transport: str
    stt_provider: str
    tts_provider: str
    vad_enabled: bool = True
    barge_in_enabled: bool = True
    modulation: VoiceModulationSpec = Field(default_factory=VoiceModulationSpec)


class AgentSpec(BaseModel):
    name: str
    goal: str
    prompt: str
    tools: List[str] = Field(default_factory=list)
    escalation_policy: str = "handoff_on_low_confidence"

    @field_validator("name", "goal", "prompt")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be blank")
        return value


class RagSpec(BaseModel):
    enabled: bool = True
    sources: List[str] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(default=300, ge=0)
    max_chunks: int = Field(default=8, ge=1)
    speculative_prefetch: bool = True


class McpSpec(BaseModel):
    servers: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    audit_enabled: bool = True


class CrmSpec(BaseModel):
    provider: str
    lead_id_field: str
    funnel_stages: List[FunnelStage] = Field(default_factory=lambda: list(FunnelStage))


class ObservabilitySpec(BaseModel):
    trace_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    record_audio: bool = False
    redact_pii: bool = True
    primary_metric: str = "cost_per_minute"


class EvalSpec(BaseModel):
    scenarios: List[str] = Field(default_factory=list)
    golden_transcripts: List[str] = Field(default_factory=list)
    regression_gate: str = "no_critical_regressions"


class LucySpec(BaseModel):
    agent: AgentSpec
    voice: VoiceSpec
    rag: RagSpec = Field(default_factory=RagSpec)
    mcp: McpSpec = Field(default_factory=McpSpec)
    crm: Optional[CrmSpec] = None
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)
    evals: EvalSpec = Field(default_factory=EvalSpec)
