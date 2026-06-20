"""Card 26: VoiceAgent facade + curated public surface."""

import asyncio
import runpy
import types
from pathlib import Path

import pytest

import lucy
from lucy import (
    AgentSpec,
    AudioChunk,
    LucySpec,
    VoiceAgent,
    VoiceSpec,
)
from lucy.observe import Tracer
from lucy.testing import InMemoryTraceExporter

ROOT = Path(__file__).resolve().parents[1]

# The exact advertised surface (DoD: `dir(lucy)` matches the curated surface).
CURATED_SURFACE = {
    # spec models + enums
    "LucySpec",
    "AgentSpec",
    "VoiceSpec",
    "VoiceModulationSpec",
    "RagSpec",
    "McpSpec",
    "CrmSpec",
    "ObservabilitySpec",
    "EvalSpec",
    "FunnelStage",
    "SentimentLabel",
    # facade
    "VoiceAgent",
    # runtime
    "GraphExecutor",
    "GraphNode",
    "GraphContext",
    # voice contracts + events
    "RealtimeVoicePipeline",
    "AudioChunk",
    "TranscriptEvent",
    "TurnLatencyEvent",
    "BargeInEvent",
    "TtsStreamEvent",
    "ProviderTimeoutEvent",
    "VoiceEvent",
    "SttProvider",
    "TtsProvider",
    "ProviderPayloadError",
    # mcp
    "McpClient",
    # model registry
    "ModelRegistry",
    "Capability",
    # observability entry point
    "configure",
}


def _counter():
    state = {"n": 0}

    def factory() -> str:
        state["n"] += 1
        return "evt_%d" % state["n"]

    return factory


def _tracer(exporter) -> Tracer:
    return Tracer(exporters=[exporter], clock=lambda: 1000, id_factory=_counter())


def _spec(stt: str = "local", tts: str = "local") -> LucySpec:
    return LucySpec(
        agent=AgentSpec(
            name="Quickstart Agent",
            goal="Help the caller.",
            prompt="Be helpful.",
        ),
        voice=VoiceSpec(transport="sim", stt_provider=stt, tts_provider=tts),
    )


# -- curated public surface --------------------------------------------------


def test_all_matches_curated_surface():
    assert set(lucy.__all__) == CURATED_SURFACE


def test_dir_matches_curated_surface_exactly():
    # The module defines __dir__, so dir(lucy) is exactly the advertised surface
    # (no internal submodules leak into dir()/IDE autocomplete).
    assert dir(lucy) == sorted(CURATED_SURFACE)
    assert not any(isinstance(getattr(lucy, name), types.ModuleType) for name in dir(lucy))


def test_every_advertised_name_is_importable():
    for name in lucy.__all__:
        assert getattr(lucy, name) is not None


# -- provider resolution -----------------------------------------------------


def test_local_provider_strings_build_on_simulators():
    agent = VoiceAgent(_spec("local", "local"))
    from lucy.testing import LocalSttSimulator, LocalTtsSimulator

    assert isinstance(agent.pipeline.stt_provider, LocalSttSimulator)
    assert isinstance(agent.pipeline.tts_provider, LocalTtsSimulator)


def test_unknown_provider_string_raises_clear_error_naming_known_providers():
    with pytest.raises(ValueError) as excinfo:
        VoiceAgent(_spec(stt="deepgram"))
    message = str(excinfo.value)
    assert "deepgram" in message
    assert "local" in message


# -- a turn on simulators emits telemetry ------------------------------------


def test_session_turn_emits_lifecycle_turn_and_span_events():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)

    async def run():
        session = agent.start_session("s1")
        events = await session.user_audio(
            AudioChunk(session_id="s1", data=b"hello world", sequence=0)
        )
        assert session.last_response is not None
        await session.synthesize(session.last_response)
        session.close()
        return events

    events = asyncio.run(run())
    tracer.flush()

    # the inbound turn returned transcript events to the caller
    assert any(getattr(e, "text", None) == "hello world" for e in events)

    by_type: dict = {}
    for event in exporter.events:
        by_type.setdefault(event.type, []).append(event)

    assert {"session.started", "turn", "span", "session.ended"} <= set(by_type)
    assert all(e.session_id == "s1" for e in exporter.events)

    turn = by_type["turn"][0]
    span = by_type["span"][0]
    # one unified turn spanning STT + LLM + TTS (full waterfall, not STT-only)
    assert turn.latency_waterfall.stt_ms >= 0.0
    assert turn.latency_waterfall.llm_ms > 0.0
    assert turn.latency_waterfall.tts_ms >= 0.0
    # the graph span is parented on the turn
    assert span.name == "llm"
    assert span.parent_id == turn.turn_id


def test_user_audio_does_not_leak_internal_latency_event():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)

    async def run():
        session = agent.start_session("s1")
        events = await session.user_audio(
            AudioChunk(session_id="s1", data=b"hi", sequence=0)
        )
        session.close()
        return events

    events = asyncio.run(run())
    from lucy import TranscriptEvent, TurnLatencyEvent

    assert any(isinstance(e, TranscriptEvent) for e in events)
    assert not any(isinstance(e, TurnLatencyEvent) for e in events)


def test_turn_methods_raise_after_close():
    agent = VoiceAgent(_spec())

    async def run():
        session = agent.start_session("s1")
        await session.user_audio(AudioChunk(session_id="s1", data=b"hi", sequence=0))
        await session.synthesize("ack")
        session.close()
        with pytest.raises(RuntimeError):
            await session.user_audio(
                AudioChunk(session_id="s1", data=b"hi", sequence=1)
            )
        with pytest.raises(RuntimeError):
            await session.synthesize("again")

    asyncio.run(run())


def test_synthesize_without_open_turn_raises():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)

    async def run():
        session = agent.start_session("s1")
        with pytest.raises(RuntimeError):
            await session.synthesize("hello")  # no preceding user_audio
        session.close()

    asyncio.run(run())
    tracer.flush()
    # no phantom turn was emitted
    assert not any(e.type == "turn" for e in exporter.events)


def test_session_started_and_ended_carry_agent_identity():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)

    session = agent.start_session("s1")
    session.close(reason="done")
    tracer.flush()

    started = next(e for e in exporter.events if e.type == "session.started")
    ended = next(e for e in exporter.events if e.type == "session.ended")
    assert started.agent_name == "Quickstart Agent"
    assert started.transport == "sim"
    assert ended.reason == "done"


def test_turn_index_advances_across_turns():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)

    async def run():
        session = agent.start_session("s1")
        for i in range(2):
            await session.user_audio(
                AudioChunk(session_id="s1", data=b"hi", sequence=i)
            )
            await session.synthesize("ack")
        session.close()

    asyncio.run(run())
    tracer.flush()

    indexes = [e.turn_index for e in exporter.events if e.type == "turn"]
    assert indexes == [0, 1]


def test_tts_provider_timeout_is_aggregated_into_the_turn():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    agent = VoiceAgent(_spec(), tracer=tracer)
    # force the TTS stage to blow its deadline
    agent.pipeline.tts_deadline_ms = 1
    from lucy.testing import LocalTtsSimulator

    agent.pipeline.tts_provider = LocalTtsSimulator(delay_ms=50)

    async def run():
        session = agent.start_session("s1")
        await session.user_audio(AudioChunk(session_id="s1", data=b"hi", sequence=0))
        await session.synthesize("ack")
        session.close()

    asyncio.run(run())
    tracer.flush()

    turn = next(e for e in exporter.events if e.type == "turn")
    assert "tts:synthesize" in turn.timeout_events


# -- quickstart runs offline -------------------------------------------------


def test_quickstart_example_runs_offline(capsys):
    path = ROOT / "examples" / "quickstart_voice_agent.py"
    assert path.exists()
    # DoD: under 30 lines.
    assert len(path.read_text().splitlines()) < 30
    runpy.run_path(str(path), run_name="__main__")
    out = capsys.readouterr().out
    assert "hello" in out.lower()
    assert "lucy." in out  # a console trace line was printed
