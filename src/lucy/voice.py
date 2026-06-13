"""Realtime voice pipeline contracts."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Union


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


class LocalSttSimulator:
    """Deterministic local STT provider implementation for contract tests."""

    def __init__(self, delay_ms: int = 0) -> None:
        self.delay_ms = delay_ms
        self.cancelled = False

    async def transcribe(self, chunks: List[AudioChunk]) -> List[TranscriptEvent]:
        try:
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

        events: List[TranscriptEvent] = []
        transcript = ""
        for index, chunk in enumerate(chunks):
            try:
                transcript += chunk.data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProviderPayloadError("audio chunk is not utf-8 text") from exc
            events.append(
                TranscriptEvent(
                    session_id=chunk.session_id,
                    text=transcript,
                    is_final=index == len(chunks) - 1,
                    sequence=chunk.sequence,
                )
            )
        return events


class LocalTtsSimulator:
    """Deterministic local TTS provider implementation for contract tests."""

    def __init__(self, delay_ms: int = 0) -> None:
        self.delay_ms = delay_ms
        self.cancelled = False

    async def synthesize(self, session_id: str, text: str) -> List[TtsStreamEvent]:
        try:
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

        if not text.strip():
            raise ProviderPayloadError("tts text cannot be blank")

        events = [TtsStreamEvent(session_id=session_id, status="started", chunk_text="")]
        for word in text.split():
            events.append(
                TtsStreamEvent(session_id=session_id, status="chunk", chunk_text=word)
            )
        events.append(TtsStreamEvent(session_id=session_id, status="finished", chunk_text=""))
        return events


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
    ) -> None:
        self.stt_provider = stt_provider or LocalSttSimulator()
        self.tts_provider = tts_provider or LocalTtsSimulator()
        self.stt_deadline_ms = stt_deadline_ms
        self.tts_deadline_ms = tts_deadline_ms
        self._active_tts: Dict[str, str] = {}

    async def handle_audio_turn(self, chunks: List[AudioChunk]) -> List[VoiceEvent]:
        started = time.perf_counter()
        session_id = chunks[-1].session_id if chunks else ""
        try:
            events: List[VoiceEvent] = list(
                await asyncio.wait_for(
                    self.stt_provider.transcribe(chunks),
                    timeout=self.stt_deadline_ms / 1000,
                )
            )
        except asyncio.TimeoutError:
            return [
                ProviderTimeoutEvent(
                    session_id=session_id,
                    provider="stt",
                    stage="transcribe",
                    deadline_ms=self.stt_deadline_ms,
                )
            ]

        finished = time.perf_counter()
        events.append(
            TurnLatencyEvent(
                session_id=session_id,
                stt_ms=(finished - started) * 1000,
                tts_ms=0.0,
                total_ms=(finished - started) * 1000,
            )
        )
        return events

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
        return BargeInEvent(session_id=session_id, cancelled_tts=cancelled)

    def is_tts_active(self, session_id: str) -> bool:
        return session_id in self._active_tts
