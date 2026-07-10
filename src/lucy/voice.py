"""Voice provider contracts and event dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, Union, runtime_checkable


@dataclass(frozen=True)
class AudioChunk:
    session_id: str
    data: bytes
    sequence: int


@dataclass(frozen=True)
class TranscriptEvent:
    session_id: str
    text: str
    is_final: bool
    sequence: int


@dataclass(frozen=True)
class TurnLatencyEvent:
    session_id: str
    stt_ms: float
    tts_ms: float
    total_ms: float


@dataclass(frozen=True)
class BargeInEvent:
    session_id: str
    cancelled_tts: bool


@dataclass(frozen=True)
class TtsStreamEvent:
    session_id: str
    status: str
    chunk_text: str


@dataclass(frozen=True)
class ProviderTimeoutEvent:
    session_id: str
    provider: str
    stage: str
    deadline_ms: int


VoiceEvent = Union[
    TranscriptEvent,
    TurnLatencyEvent,
    BargeInEvent,
    TtsStreamEvent,
    ProviderTimeoutEvent,
]


class ProviderPayloadError(ValueError):
    """Raised when a local provider simulator receives malformed payload data."""


@runtime_checkable
class SttProvider(Protocol):
    """Frozen plugin ABI per ADR 0010; signature changes break SemVer."""

    async def transcribe(self, chunks: List[AudioChunk]) -> List[TranscriptEvent]: ...


@runtime_checkable
class TtsProvider(Protocol):
    """Frozen plugin ABI per ADR 0010; signature changes break SemVer."""

    async def synthesize(self, session_id: str, text: str) -> List[TtsStreamEvent]: ...


_MOVED_TO_TESTING = ("LocalSttSimulator", "LocalTtsSimulator")


def __getattr__(name: str) -> object:
    """Deprecation shim: the simulators moved to lucy.testing (card 22, ADR 0010)."""
    if name in _MOVED_TO_TESTING:
        import warnings

        from lucy import testing

        warnings.warn(
            "lucy.voice.%s moved to lucy.testing; import it from lucy.testing" % name,
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(testing, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
