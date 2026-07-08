"""Deterministic local STT/TTS provider simulators (ADR 0003)."""

from __future__ import annotations

import asyncio
from typing import List

from lucy.voice import (
    AudioChunk,
    ProviderPayloadError,
    TranscriptEvent,
    TtsStreamEvent,
)


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

        events = [
            TtsStreamEvent(session_id=session_id, status="started", chunk_text="")
        ]
        for word in text.split():
            events.append(
                TtsStreamEvent(session_id=session_id, status="chunk", chunk_text=word)
            )
        events.append(
            TtsStreamEvent(session_id=session_id, status="finished", chunk_text="")
        )
        return events
