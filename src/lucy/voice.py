"""Realtime voice pipeline contracts."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Set, Union

from lucy.metrics import LatencyWaterfall
from lucy.observe import Tracer, get_tracer


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


class SttProvider(Protocol):
    async def transcribe(self, chunks: List[AudioChunk]) -> List[TranscriptEvent]:
        ...


class TtsProvider(Protocol):
    async def synthesize(self, session_id: str, text: str) -> List[TtsStreamEvent]:
        ...


class RealtimeVoicePipeline:
    """Minimal realtime pipeline for synthetic TDD fixtures.

    This class intentionally uses deterministic byte-to-text behavior for v0
    tests. Provider-backed STT/TTS adapters will plug into this boundary later.
    """

    def __init__(
        self,
        stt_provider: Optional[SttProvider] = None,
        tts_provider: Optional[TtsProvider] = None,
        stt_deadline_ms: int = 500,
        tts_deadline_ms: int = 500,
        *,
        tracer: Optional[Tracer] = None,
    ) -> None:
        if stt_provider is None or tts_provider is None:
            # Default providers are the deterministic simulators, which now live
            # in lucy.testing. Import lazily so constructing a pipeline with real
            # providers never pulls in the testing subpackage (and to avoid an
            # import cycle: lucy.testing imports from lucy.voice).
            from lucy.testing import LocalSttSimulator, LocalTtsSimulator
        self.stt_provider = stt_provider or LocalSttSimulator()
        self.tts_provider = tts_provider or LocalTtsSimulator()
        self.stt_deadline_ms = stt_deadline_ms
        self.tts_deadline_ms = tts_deadline_ms
        self._active_tts: Dict[str, str] = {}
        self._tracer = tracer
        # Sessions whose in-flight TTS a barge-in cancelled; the next audio turn
        # for that session reports interrupted=True and clears the flag.
        self._pending_barge_in: Set[str] = set()

    async def handle_audio_turn(
        self,
        chunks: List[AudioChunk],
        *,
        turn_id: str = "",
        turn_index: int = 0,
    ) -> List[VoiceEvent]:
        started = time.perf_counter()
        session_id = chunks[-1].session_id if chunks else ""
        interrupted = session_id in self._pending_barge_in
        self._pending_barge_in.discard(session_id)
        try:
            events: List[VoiceEvent] = list(
                await asyncio.wait_for(
                    self.stt_provider.transcribe(chunks),
                    timeout=self.stt_deadline_ms / 1000,
                )
            )
        except asyncio.TimeoutError:
            self._emit_turn(
                session_id,
                turn_id,
                turn_index,
                LatencyWaterfall(),
                interrupted=interrupted,
                timeout_events=["stt:transcribe"],
            )
            return [
                ProviderTimeoutEvent(
                    session_id=session_id,
                    provider="stt",
                    stage="transcribe",
                    deadline_ms=self.stt_deadline_ms,
                )
            ]

        finished = time.perf_counter()
        stt_ms = (finished - started) * 1000
        events.append(
            TurnLatencyEvent(
                session_id=session_id,
                stt_ms=stt_ms,
                tts_ms=0.0,
                total_ms=stt_ms,
            )
        )
        self._emit_turn(
            session_id,
            turn_id,
            turn_index,
            LatencyWaterfall(stt_ms=stt_ms),
            interrupted=interrupted,
            timeout_events=[],
        )
        return events

    def _emit_turn(
        self,
        session_id: str,
        turn_id: str,
        turn_index: int,
        latency_waterfall: LatencyWaterfall,
        *,
        interrupted: bool,
        timeout_events: List[str],
    ) -> None:
        """Emit one ``turn`` telemetry event. No-ops without a turn id or when
        tracing is disabled (zero overhead: no event built, no enqueue)."""
        if not turn_id:
            return
        tracer = self._tracer if self._tracer is not None else get_tracer()
        if not tracer.enabled:
            return
        tracer.turn(
            session_id=session_id,
            turn_id=turn_id,
            turn_index=turn_index,
            latency_waterfall=latency_waterfall,
            interrupted=interrupted,
            timeout_events=timeout_events,
        )

    async def synthesize_response(self, session_id: str, text: str) -> List[VoiceEvent]:
        try:
            return list(
                await asyncio.wait_for(
                    self.tts_provider.synthesize(session_id, text),
                    timeout=self.tts_deadline_ms / 1000,
                )
            )
        except asyncio.TimeoutError:
            return [
                ProviderTimeoutEvent(
                    session_id=session_id,
                    provider="tts",
                    stage="synthesize",
                    deadline_ms=self.tts_deadline_ms,
                )
            ]

    def start_tts_stream(self, session_id: str, text: str) -> None:
        self._active_tts[session_id] = text

    def handle_barge_in(self, session_id: str) -> BargeInEvent:
        cancelled = session_id in self._active_tts
        self._active_tts.pop(session_id, None)
        if cancelled:
            self._pending_barge_in.add(session_id)
        return BargeInEvent(session_id=session_id, cancelled_tts=cancelled)

    def is_tts_active(self, session_id: str) -> bool:
        return session_id in self._active_tts


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
