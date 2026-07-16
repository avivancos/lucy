"""Orthogonal specification models for Lucy agents."""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lucy.providers import LOCAL_PROVIDER_NAME


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


class AsteriskTransportMode(str, Enum):
    AUDIO_SOCKET = "audiosocket"
    MEDIA_WEBSOCKET = "media_websocket"
    ARI_EXTERNAL_MEDIA = "ari_external_media"


class CpaasTransportMode(str, Enum):
    TELNYX = "telnyx"
    TWILIO = "twilio"


ASTERISK_TRANSPORT_NAMESPACE = "asterisk"
ASTERISK_TRANSPORT_REGISTRY: Mapping[str, AsteriskTransportMode] = MappingProxyType(
    {mode.value: mode for mode in AsteriskTransportMode}
)
CPAAS_TRANSPORT_NAMESPACE = "cpaas"
CPAAS_TRANSPORT_REGISTRY: Mapping[str, CpaasTransportMode] = MappingProxyType(
    {mode.value: mode for mode in CpaasTransportMode}
)


class InvalidAsteriskTransportSpecError(ValueError):
    """Raised when a spec does not identify a registered Asterisk transport."""


class InvalidCpaasTransportSpecError(ValueError):
    """Raised when a spec does not identify a registered CPaaS transport."""


class AsteriskTransportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: AsteriskTransportMode

    @property
    def spec_string(self) -> str:
        return f"{ASTERISK_TRANSPORT_NAMESPACE}/{self.mode.value}"


class CpaasTransportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CpaasTransportMode

    @property
    def spec_string(self) -> str:
        return f"{CPAAS_TRANSPORT_NAMESPACE}/{self.provider.value}"


def resolve_asterisk_transport(
    value: Union[str, AsteriskTransportSpec],
) -> AsteriskTransportSpec:
    if isinstance(value, AsteriskTransportSpec):
        return value
    parts = value.split("/")
    if (
        len(parts) == 2
        and parts[0] == ASTERISK_TRANSPORT_NAMESPACE
        and all(part and part == part.strip() for part in parts)
    ):
        mode = ASTERISK_TRANSPORT_REGISTRY.get(parts[1])
        if mode is not None:
            return AsteriskTransportSpec(mode=mode)
    raise InvalidAsteriskTransportSpecError(
        "invalid Asterisk transport spec %r; expected 'asterisk/<mode>'" % value
    )


def resolve_cpaas_transport(
    value: Union[str, CpaasTransportSpec],
) -> CpaasTransportSpec:
    if isinstance(value, CpaasTransportSpec):
        return value
    parts = value.split("/")
    if (
        len(parts) == 2
        and parts[0] == CPAAS_TRANSPORT_NAMESPACE
        and all(part and part == part.strip() for part in parts)
    ):
        provider = CPAAS_TRANSPORT_REGISTRY.get(parts[1])
        if provider is not None:
            return CpaasTransportSpec(provider=provider)
    raise InvalidCpaasTransportSpecError(
        "invalid CPaaS transport spec %r; expected 'cpaas/<provider>'" % value
    )


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
    llm_provider: str = LOCAL_PROVIDER_NAME
    vad_enabled: bool = True
    barge_in_enabled: bool = True
    modulation: VoiceModulationSpec = Field(default_factory=VoiceModulationSpec)

    @field_validator("transport", mode="before")
    @classmethod
    def validate_registered_transport(cls, value: object) -> object:
        if isinstance(value, str) and (
            value == ASTERISK_TRANSPORT_NAMESPACE
            or value.startswith(f"{ASTERISK_TRANSPORT_NAMESPACE}/")
        ):
            return resolve_asterisk_transport(value).spec_string
        if isinstance(value, str) and (
            value == CPAAS_TRANSPORT_NAMESPACE
            or value.startswith(f"{CPAAS_TRANSPORT_NAMESPACE}/")
        ):
            return resolve_cpaas_transport(value).spec_string
        return value


class AgentSpec(BaseModel):
    name: str
    goal: str
    prompt: str
    agent_version: str = "dev"
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


class RecordingSpec(BaseModel):
    enabled: bool = False
    channels: Literal["dual", "mixed"] = "dual"
    require_consent: bool = True


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
    recording: RecordingSpec = Field(default_factory=RecordingSpec)
    evals: EvalSpec = Field(default_factory=EvalSpec)
