from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe import InMemoryOtelSpanExporter, ObservabilityEvent, OtelExporterBridge


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
    assert payload["primary_metric"] == "cost_per_minute"
    assert payload["cost_per_minute"] == cost.cost_per_minute
    assert payload["latency_total_ms"] == 562
    assert payload["cost_total"] == cost.total_cost


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
    assert span["resource"]["service.name"] == "lucy-api"
    assert span["attributes"]["session_id"] == "sess_demo"
    assert span["attributes"]["cost_per_minute"] == cost.cost_per_minute
    assert span["attributes"]["latency_llm_ms"] == 210
    assert exporter.spans == [span]
