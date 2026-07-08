import asyncio

import pytest

import lucy.voice as voice
from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.voice import (
    AudioChunk,
    ProviderPayloadError,
    TtsStreamEvent,
)


def test_legacy_pipeline_symbol_is_retired():
    retired_name = "Realtime" + "VoicePipeline"
    assert not hasattr(voice, retired_name)


def test_local_stt_simulator_emits_partial_and_final_transcripts():
    stt = LocalSttSimulator()

    events = asyncio.run(
        stt.transcribe(
            [
                AudioChunk(session_id="sess_demo", data=b"hello", sequence=1),
                AudioChunk(session_id="sess_demo", data=b" world", sequence=2),
            ]
        )
    )

    assert [event.text for event in events] == ["hello", "hello world"]
    assert [event.is_final for event in events] == [False, True]


def test_local_tts_simulator_emits_stream_lifecycle_events():
    tts = LocalTtsSimulator()

    events = asyncio.run(tts.synthesize(session_id="sess_demo", text="hello world"))

    assert events[0] == TtsStreamEvent(
        session_id="sess_demo",
        status="started",
        chunk_text="",
    )
    assert events[-1] == TtsStreamEvent(
        session_id="sess_demo",
        status="finished",
        chunk_text="",
    )
    assert [event.chunk_text for event in events if event.status == "chunk"] == [
        "hello",
        "world",
    ]


def test_voice_provider_simulators_reject_malformed_payloads():
    stt = LocalSttSimulator()
    tts = LocalTtsSimulator()

    with pytest.raises(ProviderPayloadError):
        asyncio.run(
            stt.transcribe(
                [AudioChunk(session_id="sess_demo", data=b"\xff", sequence=1)]
            )
        )

    with pytest.raises(ProviderPayloadError):
        asyncio.run(tts.synthesize(session_id="sess_demo", text=""))


async def test_simulators_set_cancelled_flag_when_cancelled():
    stt = LocalSttSimulator(delay_ms=30)
    tts = LocalTtsSimulator(delay_ms=30)

    stt_task = asyncio.create_task(
        stt.transcribe([AudioChunk(session_id="sess_demo", data=b"hello", sequence=1)])
    )
    tts_task = asyncio.create_task(
        tts.synthesize(session_id="sess_demo", text="hello world")
    )
    await asyncio.sleep(0)
    stt_task.cancel()
    tts_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stt_task
    with pytest.raises(asyncio.CancelledError):
        await tts_task
    assert stt.cancelled is True
    assert tts.cancelled is True
