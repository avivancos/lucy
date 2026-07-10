"""Provider and model registry for Lucy."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

if TYPE_CHECKING:
    from lucy.plugins import LucyPlugin


class Capability(str, Enum):
    STT = "stt"
    LLM = "llm"
    REALTIME = "realtime"
    TTS = "tts"
    EMBEDDING = "embedding"
    RERANKER = "reranker"


LOCAL_PROVIDER_NAME = "local"


class InvalidProviderSpecError(ValueError):
    """Raised when a provider spec is neither local nor plugin/model."""


class ModelCatalogConflictError(ValueError):
    """Raised when merged catalogs repeat a provider/model key."""


def parse_spec_string(value: str) -> Tuple[str, Optional[str]]:
    if value == LOCAL_PROVIDER_NAME:
        return (LOCAL_PROVIDER_NAME, None)
    parts = value.split("/")
    if len(parts) == 2 and all(part and part == part.strip() for part in parts):
        return (parts[0], parts[1])
    raise InvalidProviderSpecError(
        "invalid provider spec %r; expected 'local' or '<plugin>/<model>'" % value
    )


class ModelInfo(BaseModel):
    provider: str
    model: str
    capabilities: List[Capability]
    recommended_for: List[str]
    low_latency: bool = False
    notes: str = ""


class ModelRegistry(BaseModel):
    version: str
    models: List[ModelInfo]

    def by_capability(self, capability: Capability) -> List[ModelInfo]:
        return [model for model in self.models if capability in model.capabilities]

    def get(self, provider: str, model: str) -> Optional[ModelInfo]:
        for item in self.models:
            if item.provider == provider and item.model == model:
                return item
        return None


class ModelRegistryRevalidationReport(BaseModel):
    checked_in_version: str
    observed_version: str
    added: List[str]
    removed: List[str]
    renamed: List[str]
    capability_changed: List[str]

    @property
    def has_changes(self) -> bool:
        return any(
            [
                self.added,
                self.removed,
                self.renamed,
                self.capability_changed,
            ]
        )


ModelKey = Tuple[str, str]


def _model_key(model: ModelInfo) -> ModelKey:
    return (model.provider, model.model)


def _format_key(key: ModelKey) -> str:
    return "%s/%s" % key


def _capability_names(model: ModelInfo) -> List[str]:
    return sorted(capability.value for capability in model.capabilities)


def revalidate_model_registry(
    checked_in: ModelRegistry,
    observed: ModelRegistry,
    rename_hints: Optional[Dict[ModelKey, ModelKey]] = None,
) -> ModelRegistryRevalidationReport:
    """Compare a checked-in registry against an observed provider catalog."""

    checked_models = {_model_key(model): model for model in checked_in.models}
    observed_models = {_model_key(model): model for model in observed.models}
    hints = rename_hints or {}

    renamed_from = set(hints.keys())
    renamed_to = set(hints.values())
    renamed = [
        "%s -> %s" % (_format_key(old_key), _format_key(new_key))
        for old_key, new_key in sorted(hints.items())
        if old_key in checked_models and new_key in observed_models
    ]

    added = sorted(
        _format_key(key)
        for key in observed_models
        if key not in checked_models and key not in renamed_to
    )
    removed = sorted(
        _format_key(key)
        for key in checked_models
        if key not in observed_models and key not in renamed_from
    )

    capability_changed = []
    for key in sorted(set(checked_models) & set(observed_models)):
        old_capabilities = _capability_names(checked_models[key])
        new_capabilities = _capability_names(observed_models[key])
        if old_capabilities != new_capabilities:
            capability_changed.append(
                "%s: %s -> %s"
                % (
                    _format_key(key),
                    ",".join(old_capabilities),
                    ",".join(new_capabilities),
                )
            )

    return ModelRegistryRevalidationReport(
        checked_in_version=checked_in.version,
        observed_version=observed.version,
        added=added,
        removed=removed,
        renamed=renamed,
        capability_changed=capability_changed,
    )


def registry_revalidation_due(
    registry: ModelRegistry,
    as_of: str,
    max_age_days: int,
) -> bool:
    registry_date = date.fromisoformat(registry.version)
    current_date = date.fromisoformat(as_of)
    return (current_date - registry_date).days > max_age_days


def default_model_registry(
    plugins: Sequence["LucyPlugin"] = (),
) -> ModelRegistry:
    core = ModelRegistry(
        version="2026-06-06",
        models=[
            ModelInfo(
                provider="openai",
                model="gpt-realtime",
                capabilities=[Capability.REALTIME, Capability.LLM, Capability.TTS],
                recommended_for=["native speech-to-speech", "low-latency agents"],
                low_latency=True,
            ),
            ModelInfo(
                provider="openai",
                model="gpt-4o-transcribe",
                capabilities=[Capability.STT],
                recommended_for=["high-quality transcription"],
                low_latency=True,
            ),
            ModelInfo(
                provider="openai",
                model="gpt-4o-mini-transcribe",
                capabilities=[Capability.STT],
                recommended_for=["cost-efficient realtime transcription"],
                low_latency=True,
            ),
            ModelInfo(
                provider="openai",
                model="whisper-1",
                capabilities=[Capability.STT],
                recommended_for=["general-purpose transcription"],
            ),
            ModelInfo(
                provider="openai",
                model="gpt-4o-mini-tts",
                capabilities=[Capability.TTS],
                recommended_for=["fast synthetic voice"],
                low_latency=True,
            ),
            ModelInfo(
                provider="openai",
                model="gpt-5",
                capabilities=[Capability.LLM],
                recommended_for=["high-quality chained reasoning"],
            ),
            ModelInfo(
                provider="openai",
                model="gpt-5-mini",
                capabilities=[Capability.LLM],
                recommended_for=["fast cost-efficient chained reasoning"],
                low_latency=True,
            ),
            ModelInfo(
                provider="deepgram",
                model="flux",
                capabilities=[Capability.STT],
                recommended_for=["voice-agent turn-taking"],
                low_latency=True,
            ),
            ModelInfo(
                provider="deepgram",
                model="nova-3",
                capabilities=[Capability.STT],
                recommended_for=["accurate streaming transcription"],
                low_latency=True,
            ),
            ModelInfo(
                provider="assemblyai",
                model="universal-streaming",
                capabilities=[Capability.STT],
                recommended_for=["immutable streaming transcripts"],
                low_latency=True,
            ),
            ModelInfo(
                provider="elevenlabs",
                model="scribe-realtime",
                capabilities=[Capability.STT],
                recommended_for=["multilingual realtime transcription"],
                low_latency=True,
            ),
            ModelInfo(
                provider="elevenlabs",
                model="flash-v2.5",
                capabilities=[Capability.TTS],
                recommended_for=["lowest-latency conversational TTS"],
                low_latency=True,
            ),
            ModelInfo(
                provider="elevenlabs",
                model="turbo-v2.5",
                capabilities=[Capability.TTS],
                recommended_for=["balanced conversational TTS"],
                low_latency=True,
            ),
            ModelInfo(
                provider="cartesia",
                model="sonic",
                capabilities=[Capability.TTS],
                recommended_for=["low-latency expressive speech"],
                low_latency=True,
            ),
            ModelInfo(
                provider="google",
                model="gemini-live-flash-native-audio",
                capabilities=[Capability.REALTIME, Capability.LLM, Capability.TTS],
                recommended_for=["native audio live agents"],
                low_latency=True,
            ),
            ModelInfo(
                provider="anthropic",
                model="claude-sonnet",
                capabilities=[Capability.LLM],
                recommended_for=["chained text reasoning"],
            ),
            ModelInfo(
                provider="anthropic",
                model="claude-opus",
                capabilities=[Capability.LLM],
                recommended_for=["deep chained reasoning"],
            ),
            ModelInfo(
                provider="mistral",
                model="mistral-large",
                capabilities=[Capability.LLM],
                recommended_for=["provider-agnostic chained reasoning"],
            ),
            ModelInfo(
                provider="mistral",
                model="voxtral-transcribe-realtime",
                capabilities=[Capability.STT],
                recommended_for=["open realtime transcription"],
                low_latency=True,
            ),
            ModelInfo(
                provider="mistral",
                model="voxtral-tts",
                capabilities=[Capability.TTS],
                recommended_for=["open-weight TTS path"],
            ),
            ModelInfo(
                provider="groq",
                model="whisper-large-v3",
                capabilities=[Capability.STT],
                recommended_for=["fast Whisper transcription"],
                low_latency=True,
            ),
        ],
    )
    merged = list(core.models)
    positions = {_model_key(model): index for index, model in enumerate(merged)}
    conflicts = set()
    for plugin in plugins:
        for model in plugin.catalog:
            key = _model_key(model)
            existing_index = positions.get(key)
            if existing_index is not None:
                if plugin.name == model.provider and merged[existing_index] == model:
                    merged[existing_index] = model
                    continue
                conflicts.add(key)
                continue
            positions[key] = len(merged)
            merged.append(model)
    if conflicts:
        raise ModelCatalogConflictError(
            "duplicate model catalog entries: %s"
            % ", ".join(_format_key(key) for key in sorted(conflicts))
        )
    return ModelRegistry(version=core.version, models=merged)


def registry_summary(registry: Optional[ModelRegistry] = None):
    active = registry or default_model_registry()
    return {
        capability.value: len(active.by_capability(capability))
        for capability in Capability
    }
