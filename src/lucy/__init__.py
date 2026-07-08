"""Lucy - an open-core voice-agent SDK.

This module is the curated public surface: everything advertised in ``__all__``
is supported and documented. Internal modules stay importable but unadvertised.
"""

__version__ = "0.1.0"

from lucy.agent import VoiceAgent
from lucy.mcp import McpClient
from lucy.observe import configure
from lucy.providers import Capability, ModelRegistry
from lucy.runtime import GraphContext, GraphExecutor, GraphNode
from lucy.specs import (
    AgentSpec,
    CrmSpec,
    EvalSpec,
    FunnelStage,
    LucySpec,
    McpSpec,
    ObservabilitySpec,
    RagSpec,
    SentimentLabel,
    VoiceModulationSpec,
    VoiceSpec,
)
from lucy.voice import (
    AudioChunk,
    BargeInEvent,
    ProviderPayloadError,
    ProviderTimeoutEvent,
    SttProvider,
    TranscriptEvent,
    TtsProvider,
    TtsStreamEvent,
    TurnLatencyEvent,
    VoiceEvent,
)

__all__ = [
    # spec models + enums
    "LucySpec",
    "AgentSpec",
    "VoiceSpec",
    "VoiceModulationSpec",
    "RagSpec",
    "McpSpec",
    "CrmSpec",
    "ObservabilitySpec",
    "EvalSpec",
    "FunnelStage",
    "SentimentLabel",
    # facade
    "VoiceAgent",
    # runtime
    "GraphExecutor",
    "GraphNode",
    "GraphContext",
    # voice contracts + events
    "AudioChunk",
    "TranscriptEvent",
    "TurnLatencyEvent",
    "BargeInEvent",
    "TtsStreamEvent",
    "ProviderTimeoutEvent",
    "VoiceEvent",
    "SttProvider",
    "TtsProvider",
    "ProviderPayloadError",
    # mcp
    "McpClient",
    # model registry
    "ModelRegistry",
    "Capability",
    # observability entry point
    "configure",
]


def __dir__() -> list:
    """Expose exactly the curated surface (internal submodules stay importable
    but unadvertised, so ``dir(lucy)`` and IDE autocomplete show only ``__all__``)."""
    return sorted(__all__)
