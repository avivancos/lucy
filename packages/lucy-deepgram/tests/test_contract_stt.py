import wave
from pathlib import Path

import pytest
from pydantic import SecretStr

from lucy.testing.contracts import SttContractSuite
from lucy.testing.replay import (
    RecordedFrame,
    ReplayTransport,
    StalledTransport,
    load_fixture,
)
from lucy.voice import AudioChunk
from lucy_deepgram.settings import DeepgramSettings
from lucy_deepgram.stt import DeepgramSttAdapter

FIXTURES = Path(__file__).parent / "fixtures"
RECORDING = FIXTURES / "stt_short_utterance.jsonl"
WAV = FIXTURES / "utterance_16k.wav"


def _chunks():
    with wave.open(str(WAV), "rb") as audio:
        pcm = audio.readframes(audio.getnframes())
    size = 3_200
    return [
        AudioChunk(session_id="deepgram", data=pcm[offset : offset + size], sequence=i)
        for i, offset in enumerate(range(0, len(pcm), size))
    ]


def _settings():
    return DeepgramSettings(api_key=SecretStr("fixture-key"))


class TestDeepgramSttContract(SttContractSuite):
    def make_provider(self):
        return DeepgramSttAdapter(
            model="nova-3",
            settings=_settings(),
            transport=ReplayTransport(load_fixture(RECORDING)),
        )

    def chunks(self):
        return _chunks()

    def make_stalled_provider(self):
        return DeepgramSttAdapter(
            model="nova-3",
            settings=_settings(),
            transport=StalledTransport(),
        )

    def make_malformed_case(self):
        frames = load_fixture(RECORDING)
        corrupted = []
        changed = False
        for frame in frames:
            if (
                frame.direction == "received"
                and frame.payload.get("type") == "Results"
                and not changed
            ):
                corrupted.append(
                    RecordedFrame(
                        direction=frame.direction,
                        at_ms=frame.at_ms,
                        payload={"type": "Results", "channel": "invalid"},
                    )
                )
                changed = True
            else:
                corrupted.append(frame)
        return (
            DeepgramSttAdapter(
                model="nova-3",
                settings=_settings(),
                transport=ReplayTransport(corrupted),
            ),
            _chunks(),
        )


async def test_large_audio_chunk_is_framed_for_wire():
    chunks = _chunks()
    provider = DeepgramSttAdapter(
        model="nova-3",
        settings=_settings(),
        transport=ReplayTransport(load_fixture(RECORDING)),
    )

    events = await provider.transcribe(
        [
            AudioChunk(
                session_id="deepgram",
                data=b"".join(chunk.data for chunk in chunks),
                sequence=0,
            )
        ]
    )

    assert events[-1].is_final


@pytest.mark.live
class TestDeepgramSttLiveContract(TestDeepgramSttContract):
    def make_provider(self):
        return DeepgramSttAdapter(
            model="nova-3",
            settings=DeepgramSettings(),
        )
