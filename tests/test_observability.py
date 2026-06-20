import json

from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe import (
    ConsoleExporter,
    OtelExporterBridge,
    ObservabilityEvent,
    OtlpBridgeExporter,
    Tracer,
    configure,
)
from lucy.testing import InMemoryOtelSpanExporter, InMemoryTraceExporter


def _counter():
    state = {"n": 0}

    def factory() -> str:
        state["n"] += 1
        return "evt_%d" % state["n"]

    return factory


def _tracer(exporter, **kwargs):
    return Tracer(
        exporters=[exporter],
        clock=lambda: 1000,
        id_factory=_counter(),
        **kwargs,
    )


# -- backward compatibility (pre-card-24 OTel bridge) ------------------------


def test_observability_event_exposes_latency_and_cost_per_minute():
    cost = CostBreakdown(
        stt_cost=0.01,
        llm_cost=0.04,
        tts_cost=0.02,
        telephony_cost=0.015,
        rag_cost=0.003,
        mcp_tool_cost=0.002,
        infra_cost=0.005,
        billable_audio_minutes=3.0,
    )
    waterfall = LatencyWaterfall(
        stt_ms=145,
        rag_ms=38,
        llm_ms=210,
        mcp_tools_ms=42,
        tts_ms=95,
        transport_ms=32,
    )
    event = ObservabilityEvent(
        session_id="sess_demo",
        primary_metric="cost_per_minute",
        cost=cost,
        latency=waterfall,
    )

    payload = event.as_trace_attributes()
    assert payload["session_id"] == "sess_demo"
    assert payload["cost_per_minute"] == cost.cost_per_minute
    assert payload["latency_total_ms"] == 562


def test_otel_exporter_bridge_exports_observability_event_as_span():
    cost = CostBreakdown(
        stt_cost=0.01,
        llm_cost=0.04,
        tts_cost=0.02,
        telephony_cost=0.015,
        rag_cost=0.003,
        mcp_tool_cost=0.002,
        infra_cost=0.005,
        billable_audio_minutes=3.0,
    )
    event = ObservabilityEvent(
        session_id="sess_demo",
        primary_metric="cost_per_minute",
        cost=cost,
        latency=LatencyWaterfall(stt_ms=145, rag_ms=38, llm_ms=210),
    )
    exporter = InMemoryOtelSpanExporter()
    bridge = OtelExporterBridge(exporter=exporter, service_name="lucy-api")

    span = bridge.export_event(event)
    assert span["name"] == "lucy.session"
    assert span["attributes"]["latency_llm_ms"] == 210
    assert exporter.spans == [span]


def test_deprecated_inmemory_span_exporter_import_path_still_warns():
    import lucy.observe
    import pytest

    with pytest.warns(DeprecationWarning):
        obj = lucy.observe.InMemoryOtelSpanExporter
    from lucy.testing import InMemoryOtelSpanExporter as canonical

    assert obj is canonical


# -- card 24: tracer, exporters, redaction, sampling, fail-open --------------


def test_typed_methods_emit_each_event_type():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.session_started(
        session_id="s1",
        agent_name="booking",
        spec_hash="h",
        environment="local",
        transport="sim",
    )
    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=10, llm_ms=20),
    )
    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="upsert_lead",
        allowed=True,
        latency_ms=5.0,
    )
    tracer.flush()

    types = [event.type for event in exporter.events]
    assert types == ["session.started", "turn", "tool_call"]


def test_configure_default_uses_console_exporter():
    tracer = configure()
    assert any(isinstance(exp, ConsoleExporter) for exp in tracer._exporters)


def test_jsonl_file_exporter_writes_wire_shaped_events(tmp_path, monkeypatch):
    trace_file = tmp_path / "trace.jsonl"
    monkeypatch.setenv("LUCY_TRACE_FILE", str(trace_file))
    tracer = configure()
    tracer._clock = lambda: 4242
    tracer._id_factory = _counter()

    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=10, rag_ms=1, llm_ms=20, mcp_tools_ms=2, tts_ms=3, transport_ms=4),
    )
    tracer.cost(
        session_id="s1",
        turn_id="t1",
        cost=CostBreakdown(llm_cost=0.5, billable_audio_minutes=2.0),
    )
    tracer.flush()

    lines = [json.loads(line) for line in trace_file.read_text().splitlines()]
    turn = next(line for line in lines if line["type"] == "turn")
    cost = next(line for line in lines if line["type"] == "cost")
    for key in ("stt_ms", "rag_ms", "llm_ms", "mcp_tools_ms", "tts_ms", "transport_ms"):
        assert key in turn["latency_waterfall"]
    assert cost["cost"]["total_cost"] == 0.5
    assert cost["cost"]["cost_per_minute"] == 0.25


def test_redaction_runs_before_export():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True)

    tracer.transcript(
        session_id="s1",
        turn_id="t1",
        role="caller",
        text="email me at john.doe@example.com or call +1 415 555 1234",
    )
    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="upsert_lead",
        allowed=True,
        latency_ms=1.0,
        arguments={"email": "john.doe@example.com", "note": "ok"},
    )
    tracer.flush()

    transcript = exporter.events[0]
    tool = exporter.events[1]
    assert "@example.com" not in transcript.text
    assert "[REDACTED]" in transcript.text
    assert tool.arguments["email"] == "[REDACTED]"
    assert tool.arguments["note"] == "ok"


def test_transcripts_killswitch_drops_transcript_events():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, transcripts_enabled=False)
    tracer.transcript(session_id="s1", turn_id="t1", role="caller", text="hello")
    tracer.flush()
    assert exporter.events == []


def test_audio_ref_dropped_when_record_audio_false():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, record_audio=False)
    tracer.audio_ref(session_id="s1", turn_id="t1", blob_id="b1")
    tracer.flush()
    assert exporter.events == []

    keep = InMemoryTraceExporter()
    tracer2 = _tracer(keep, record_audio=True)
    tracer2.audio_ref(session_id="s1", turn_id="t1", blob_id="b1")
    tracer2.flush()
    assert [event.type for event in keep.events] == ["audio_ref"]


def test_session_sampling_is_deterministic_and_drops_unsampled():
    dropped = InMemoryTraceExporter()
    off = _tracer(dropped, sample_rate=0.0)
    off.turn(session_id="s1", turn_id="t1", turn_index=0, latency_waterfall=LatencyWaterfall())
    off.flush()
    assert dropped.events == []

    kept = InMemoryTraceExporter()
    on = _tracer(kept, sample_rate=1.0)
    on.turn(session_id="s1", turn_id="t1", turn_index=0, latency_waterfall=LatencyWaterfall())
    on.flush()
    assert len(kept.events) == 1


def test_exporter_failure_is_fail_open_and_counts_drops():
    class Boom:
        def export_batch(self, events):
            raise RuntimeError("ingest down")

    tracer = _tracer(Boom())
    tracer.turn(session_id="s1", turn_id="t1", turn_index=0, latency_waterfall=LatencyWaterfall())
    tracer.flush()  # must not raise
    assert tracer.dropped_events == 1


def test_bounded_queue_drops_when_full():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, queue_maxlen=2)
    for index in range(5):
        tracer.turn(
            session_id="s1",
            turn_id="t%d" % index,
            turn_index=index,
            latency_waterfall=LatencyWaterfall(),
        )
    assert tracer.dropped_events == 3
    tracer.flush()
    assert len(exporter.events) == 2


def test_tracing_disabled_yields_noop_tracer(monkeypatch):
    monkeypatch.setenv("LUCY_TRACING", "0")
    exporter = InMemoryTraceExporter()
    tracer = configure(exporters=[exporter])
    tracer.turn(session_id="s1", turn_id="t1", turn_index=0, latency_waterfall=LatencyWaterfall())
    tracer.flush()
    assert exporter.events == []


def test_otlp_bridge_exporter_emits_one_span_per_event():
    spans = InMemoryOtelSpanExporter()
    tracer = _tracer(OtlpBridgeExporter(spans, service_name="lucy-api"))
    tracer.turn(session_id="s1", turn_id="t1", turn_index=0, latency_waterfall=LatencyWaterfall(stt_ms=9))
    tracer.flush()
    assert len(spans.spans) == 1
    assert spans.spans[0]["name"] == "lucy.turn"
    assert spans.spans[0]["resource"]["service.name"] == "lucy-api"


def test_entry_point_discovery_attaches_factory_results(monkeypatch):
    import lucy.observe as observe

    sentinel = InMemoryTraceExporter()

    class FakeEntryPoint:
        def load(self):
            return lambda: sentinel

    class NoneEntryPoint:
        def load(self):
            return lambda: None

    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group: [FakeEntryPoint(), NoneEntryPoint()],
    )
    discovered = observe._discover_exporters()
    assert sentinel in discovered
    assert len(discovered) == 1


# -- card 25: runtime instrumentation wired into the tracer ------------------

import asyncio

import pytest

from lucy import observe as observe_module
from lucy.mcp import McpClient, McpPermissionError
from lucy.metrics import emit_cost
from lucy.runtime import GraphExecutionError, GraphExecutor, GraphNode
from lucy.testing import LocalMcpCommandTransport
from lucy.voice import AudioChunk, ProviderTimeoutEvent, RealtimeVoicePipeline


@pytest.fixture(autouse=True)
def _reset_global_tracer():
    # The process-global tracer is cached; reset it after each test so a test
    # that exercises the global-default path cannot leak its tracer (and its
    # console exporter / queue state) into later tests.
    yield
    observe_module.set_tracer(None)


async def _echo(context):
    return context.payload.get("input", "")


def test_graph_executor_emits_one_span_per_node_under_turn():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    executor = GraphExecutor(
        [GraphNode(name="rag", handler=_echo), GraphNode(name="llm", handler=_echo)],
        tracer=tracer,
    )

    asyncio.run(executor.run({"input": "hi"}, session_id="s1", turn_id="t1"))
    tracer.flush()

    spans = [event for event in exporter.events if event.type == "span"]
    assert {span.name for span in spans} == {"rag", "llm"}
    assert all(span.parent_id == "t1" and span.turn_id == "t1" for span in spans)
    assert all(span.session_id == "s1" and span.status == "ok" for span in spans)
    assert all(span.ended_at_ms >= span.started_at_ms for span in spans)


def test_graph_executor_emits_no_spans_without_turn_context():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    executor = GraphExecutor([GraphNode(name="llm", handler=_echo)], tracer=tracer)

    asyncio.run(executor.run({"input": "hi"}))  # no session/turn -> no spans
    tracer.flush()

    assert exporter.events == []


def test_graph_executor_span_status_reflects_fallback():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    async def boom(context):
        raise RuntimeError("primary down")

    async def fallback(context):
        return "recovered"

    executor = GraphExecutor(
        [GraphNode(name="llm", handler=boom, fallback=fallback)],
        tracer=tracer,
    )

    asyncio.run(executor.run({}, session_id="s1", turn_id="t1"))
    tracer.flush()

    span = next(event for event in exporter.events if event.type == "span")
    assert span.status == "fallback"
    assert "primary down" in span.attributes.get("error", "")


def test_graph_executor_emits_error_span_on_unrecovered_failure():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    async def boom(context):
        raise RuntimeError("kaboom")

    executor = GraphExecutor([GraphNode(name="llm", handler=boom)], tracer=tracer)
    with pytest.raises(GraphExecutionError):
        asyncio.run(executor.run({}, session_id="s1", turn_id="t1"))
    tracer.flush()

    span = next(event for event in exporter.events if event.type == "span")
    assert span.status == "error"
    assert span.name == "llm"
    assert span.parent_id == "t1"
    assert "kaboom" in span.attributes.get("error", "")


def test_disabled_global_tracer_skips_exporter_discovery(monkeypatch):
    # Zero-overhead-when-off must hold even via the global-default path (no
    # injected tracer): building the disabled global tracer must not run
    # exporter construction or entry-point discovery.
    monkeypatch.setenv("LUCY_TRACING", "0")
    observe_module.set_tracer(None)

    def _forbidden(group):  # pragma: no cover - asserts it is never called
        raise AssertionError("entry-point discovery ran while tracing was off")

    monkeypatch.setattr("importlib.metadata.entry_points", _forbidden)

    client = McpClient(
        LocalMcpCommandTransport(), allowed_tools=["crm.upsert_lead"]
    )
    asyncio.run(
        client.call_tool(
            "crm", "upsert_lead", {"lead_id": "x"}, session_id="s1", turn_id="t1"
        )
    )

    tracer = observe_module.get_tracer()
    assert tracer.enabled is False
    assert tracer._exporters == []


def test_voice_pipeline_emits_turn_event_with_waterfall():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    pipeline = RealtimeVoicePipeline(tracer=tracer)

    asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id="s1", data=b"hello", sequence=0)],
            turn_id="t1",
            turn_index=0,
        )
    )
    tracer.flush()

    turn = next(event for event in exporter.events if event.type == "turn")
    assert turn.session_id == "s1"
    assert turn.turn_id == "t1"
    assert turn.latency_waterfall.stt_ms >= 0.0
    assert turn.interrupted is False
    assert turn.timeout_events == []


def test_voice_pipeline_turn_carries_barge_in_flag():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    pipeline = RealtimeVoicePipeline(tracer=tracer)

    pipeline.start_tts_stream("s1", "a long spoken answer")
    pipeline.handle_barge_in("s1")  # cancels the active TTS for this session

    asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id="s1", data=b"hi", sequence=0)],
            turn_id="t2",
            turn_index=1,
        )
    )
    tracer.flush()

    turn = next(event for event in exporter.events if event.type == "turn")
    assert turn.interrupted is True


class _SlowStt:
    async def transcribe(self, chunks):
        await asyncio.sleep(0.05)
        return []


def test_voice_pipeline_turn_records_provider_timeout():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    pipeline = RealtimeVoicePipeline(
        stt_provider=_SlowStt(), stt_deadline_ms=1, tracer=tracer
    )

    returned = asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id="s1", data=b"hi", sequence=0)],
            turn_id="t1",
            turn_index=0,
        )
    )
    tracer.flush()

    assert any(isinstance(event, ProviderTimeoutEvent) for event in returned)
    turn = next(event for event in exporter.events if event.type == "turn")
    assert turn.timeout_events == ["stt:transcribe"]


def test_mcp_client_emits_tool_call_for_allowed_and_denied():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=["crm.upsert_lead"],
        tracer=tracer,
    )

    asyncio.run(
        client.call_tool(
            "crm", "upsert_lead", {"lead_id": "x"}, session_id="s1", turn_id="t1"
        )
    )
    with pytest.raises(McpPermissionError):
        asyncio.run(
            client.call_tool("crm", "nope", {}, session_id="s1", turn_id="t1")
        )
    tracer.flush()

    calls = [event for event in exporter.events if event.type == "tool_call"]
    assert [call.allowed for call in calls] == [True, False]
    assert all(call.turn_id == "t1" and call.session_id == "s1" for call in calls)
    # one tool_call event per audit-log append
    assert len(calls) == len(client.audit_log)


def test_mcp_tool_call_arguments_are_redacted_before_export():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True)
    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=["crm.upsert_lead"],
        tracer=tracer,
    )

    asyncio.run(
        client.call_tool(
            "crm",
            "upsert_lead",
            {"email": "john.doe@example.com", "lead_id": "x"},
            session_id="s1",
            turn_id="t1",
        )
    )
    tracer.flush()

    call = next(event for event in exporter.events if event.type == "tool_call")
    assert call.arguments["email"] == "[REDACTED]"
    assert call.arguments["lead_id"] == "x"


def test_emit_cost_produces_cost_event_for_turn():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    emit_cost(
        CostBreakdown(llm_cost=0.5, billable_audio_minutes=2.0),
        session_id="s1",
        turn_id="t1",
        tracer=tracer,
    )
    tracer.flush()

    cost = next(event for event in exporter.events if event.type == "cost")
    assert cost.turn_id == "t1"
    assert cost.cost.total_cost == 0.5
    assert cost.cost.cost_per_minute == 0.25


def test_pipeline_turn_produces_full_event_tree_with_correct_parents():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    session_id, turn_id = "s1", "t1"

    tracer.session_started(
        session_id=session_id,
        agent_name="booking",
        spec_hash="h",
        environment="local",
        transport="sim",
    )

    pipeline = RealtimeVoicePipeline(tracer=tracer)
    asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id=session_id, data=b"hello", sequence=0)],
            turn_id=turn_id,
            turn_index=0,
        )
    )

    async def llm(context):
        return "answer"

    executor = GraphExecutor([GraphNode(name="llm", handler=llm)], tracer=tracer)
    asyncio.run(executor.run({"input": "hi"}, session_id=session_id, turn_id=turn_id))

    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=["crm.upsert_lead"],
        tracer=tracer,
    )
    asyncio.run(
        client.call_tool(
            "crm", "upsert_lead", {"lead_id": "x"},
            session_id=session_id, turn_id=turn_id,
        )
    )

    emit_cost(
        CostBreakdown(llm_cost=0.3, billable_audio_minutes=1.0),
        session_id=session_id,
        turn_id=turn_id,
        tracer=tracer,
    )
    tracer.flush()

    by_type: dict = {}
    for event in exporter.events:
        by_type.setdefault(event.type, []).append(event)

    assert set(by_type) == {"session.started", "turn", "span", "tool_call", "cost"}
    assert all(event.session_id == session_id for event in exporter.events)
    assert by_type["turn"][0].turn_id == turn_id
    assert by_type["span"][0].parent_id == turn_id
    assert by_type["span"][0].turn_id == turn_id
    assert by_type["tool_call"][0].turn_id == turn_id
    assert by_type["cost"][0].turn_id == turn_id


def test_instrumentation_is_zero_overhead_when_tracing_disabled():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, enabled=False)
    session_id, turn_id = "s1", "t1"

    pipeline = RealtimeVoicePipeline(tracer=tracer)
    asyncio.run(
        pipeline.handle_audio_turn(
            [AudioChunk(session_id=session_id, data=b"hi", sequence=0)],
            turn_id=turn_id,
            turn_index=0,
        )
    )

    async def llm(context):
        return "x"

    executor = GraphExecutor([GraphNode(name="llm", handler=llm)], tracer=tracer)
    asyncio.run(executor.run({}, session_id=session_id, turn_id=turn_id))

    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=["crm.upsert_lead"],
        tracer=tracer,
    )
    asyncio.run(
        client.call_tool(
            "crm", "upsert_lead", {"lead_id": "x"},
            session_id=session_id, turn_id=turn_id,
        )
    )

    emit_cost(
        CostBreakdown(llm_cost=0.1, billable_audio_minutes=1.0),
        session_id=session_id,
        turn_id=turn_id,
        tracer=tracer,
    )
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 0
    assert len(tracer._queue) == 0


def test_components_use_global_tracer_when_none_injected():
    exporter = InMemoryTraceExporter()
    custom = _tracer(exporter)
    observe_module.set_tracer(custom)
    try:
        client = McpClient(
            LocalMcpCommandTransport(), allowed_tools=["crm.upsert_lead"]
        )
        asyncio.run(
            client.call_tool(
                "crm", "upsert_lead", {"lead_id": "x"},
                session_id="s1", turn_id="t1",
            )
        )
        custom.flush()
        assert any(event.type == "tool_call" for event in exporter.events)
    finally:
        observe_module.set_tracer(None)
