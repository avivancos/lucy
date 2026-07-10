"""Real ElevenLabs fixture recording scenarios."""

from __future__ import annotations

import io
import wave

from lucy.testing.record import RecordingResult

from lucy_elevenlabs.settings import ElevenLabsSettings
from lucy_elevenlabs.tts import ElevenLabsTtsAdapter, ElevenLabsWebSocketTransport

SHORT_SENTENCE = "Lucy providers can speak naturally."
SAMPLE_RATE_HZ = 16_000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1


def _wav_bytes(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(SAMPLE_RATE_HZ)
        wav.writeframes(pcm)
    return output.getvalue()


async def short_sentence() -> RecordingResult:
    settings = ElevenLabsSettings()
    transport = ElevenLabsWebSocketTransport(settings, "flash-v2.5")
    adapter = ElevenLabsTtsAdapter(
        model="flash-v2.5",
        settings=settings,
        transport=transport,
    )
    try:
        await adapter.synthesize("record-elevenlabs", SHORT_SENTENCE)
    finally:
        await transport.close()
    return RecordingResult(
        frames=list(transport.frames),
        artifacts={"utterance_16k.wav": _wav_bytes(bytes(transport.audio))},
    )


SCENARIOS = {"short_sentence": short_sentence}
