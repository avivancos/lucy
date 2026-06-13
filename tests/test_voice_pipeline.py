import asyncio

import pytest

from lucy.voice import (
    AudioChunk,
    BargeInEvent,
    LocalSttSimulator,
    LocalTtsSimulator,
    ProviderPayloadError,
    ProviderTimeoutEvent,
    RealtimeVoicePipeline,
    TranscriptEvent,
    TtsStreamEvent,
    TurnLatencyEvent,
)


def test_voice_pipeline_streams_partial_transcripts_and_latency():
    pipeline = RealtimeVoicePipeline()

    events = asyncio.run(
        pipeline.handle_audio_turn(
            [
                AudioChunk(session_id="sess_demo", data=b"hello", sequence=1),
                AudioChunk(session_id="sess_demo", data=b" world", sequence=2),
            ]
        )
    )

    transcripts = [event for event in events if isinstance(event, TranscriptEvent)]
    latency_events = [event for event in events if isinstance(event, TurnLatencyEvent)]

    assert [event.text for event in transcripts] == ["hello", "hello world"]
    assert transcripts[0].is_final is False
    assert transcripts[1].is_final is True
    assert latency_events
    assert latency_events[0].total_ms >= 0


def test_voice_pipeline_barge_in_cancels_active_tts():
    pipeline = RealtimeVoicePipeline()
    pipeline.start_tts_stream(session_id="sess_demo", text="Let me explain that.")

    event = pipeline.handle_barge_in(session_id="sess_demo")

    assert isinstance(event, BargeInEvent)
    assert event.session_id == "sess_demo"
    assert event.cancelled_tts is True
    assert pipeline.is_tts_active("sess_demo") is False


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
        asyncio.run(stt.transcribe([AudioChunk(session_id="sess_demo", data=b"\xff", sequence=1)]))

    with pytest.raises(ProviderPayloadError):
        asyncio.run(tts.synthesize(session_id="sess_demo", text=""))


def test_voice_pipeline_barge_in_cancels_local_tts_simulator_stream():
    pipeline = RealtimeVoicePipeline(tts_provider=LocalTtsSimulator())
    pipeline.start_tts_stream(session_id="sess_demo", text="hello world")

    event = pipeline.handle_barge_in(session_id="sess_demo")

    assert event == BargeInEvent(session_id="sess_demo", cancelled_tts=True)
    assert pipeline.is_tts_active("sess_demo") is False


def test_voice_pipeline_emits_stt_timeout_event_and_cancels_provider():
    stt = LocalSttSimulator(delay_ms=30)
    pipeline = RealtimeVoicePipeline(stt_provider=stt, stt_deadline_ms=1)

    events = asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id="sess_demo", data=b"hello", sequence=1)]
        )
    )

    assert events == [
        ProviderTimeoutEvent(
            session_id="sess_demo",
            provider="stt",
            stage="transcribe",
            deadline_ms=1,
        )
    ]
    assert stt.cancelled is True


def test_voice_pipeline_emits_tts_timeout_event_and_cancels_provider():
    tts = LocalTtsSimulator(delay_ms=30)
    pipeline = RealtimeVoicePipeline(tts_provider=tts, tts_deadline_ms=1)

    events = asyncio.run(
        pipeline.synthesize_response(session_id="sess_demo", text="hello world")
    )

    assert events == [
        ProviderTimeoutEvent(
            session_id="sess_demo",
            provider="tts",
            stage="synthesize",
            deadline_ms=1,
        )
    ]
    assert tts.cancelled is True
