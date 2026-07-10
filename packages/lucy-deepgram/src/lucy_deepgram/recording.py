"""Real Deepgram fixture recording scenarios."""

from __future__ import annotations

import wave
from pathlib import Path

from lucy.testing.record import RecordingResult
from lucy.voice import AudioChunk

from lucy_deepgram.settings import DeepgramSettings
from lucy_deepgram.stt import DeepgramSttAdapter, DeepgramWebSocketTransport

PCM_CHUNK_BYTES = 3_200


def _chunks() -> list[AudioChunk]:
    fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "utterance_16k.wav"
    with wave.open(str(fixture), "rb") as audio:
        pcm = audio.readframes(audio.getnframes())
    return [
        AudioChunk(
            session_id="record-deepgram",
            data=pcm[offset : offset + PCM_CHUNK_BYTES],
            sequence=index,
        )
        for index, offset in enumerate(range(0, len(pcm), PCM_CHUNK_BYTES))
    ]


async def short_utterance() -> RecordingResult:
    settings = DeepgramSettings()
    transport = DeepgramWebSocketTransport(settings, "nova-3")
    adapter = DeepgramSttAdapter(
        model="nova-3",
        settings=settings,
        transport=transport,
    )
    try:
        await adapter.transcribe(_chunks())
    finally:
        await transport.close()
    return RecordingResult(frames=list(transport.frames), artifacts={})


SCENARIOS = {"short_utterance": short_utterance}
