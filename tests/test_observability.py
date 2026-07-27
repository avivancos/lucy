# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
from pathlib import Path
import re
import uuid

import pytest
from pydantic import ValidationError

from lucy import observe as observe_module
from lucy.mcp import McpClient, McpPermissionError
from lucy.metrics import CostBreakdown, CostComponent, LatencyWaterfall
from lucy.metrics import emit_cost
from lucy.observe import (
    ConsoleExporter,
    OtelExporterBridge,
    ObservabilityEvent,
    OtlpBridgeExporter,
    Tracer,
    configure,
)
from lucy.observe.events import (
    BusinessEvent,
    CpaasCostMetadata,
    CostEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SpanEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnEvent,
)
from lucy.providers import ProviderIdentity
from lucy.observe.redact import MAX_REDACTION_DEPTH
from lucy.runtime import GraphExecutionError, GraphExecutor, GraphNode
from lucy.testing import InMemoryOtelSpanExporter, InMemoryTraceExporter
from lucy.testing import LocalMcpCommandTransport

_SYNTHETIC_BEARER_MODEL = "Bearer " + "opaque-fixture-credential"
_SYNTHETIC_SK_MODEL = "sk-" + "fixture-opaque-credential-0000000000"
_SYNTHETIC_JWT_MODEL = "eyJ" + "header." + "payload." + "signature"


CPAAS_FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "cpaas"
PHONE_NUMBER_PATTERN = re.compile(r"\+?[1-9]\d{7,14}")


def _counter():
    state = {"n": 0}

    def factory() -> str:
        state["n"] += 1
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lucy-event-{state['n']}"))

    return factory


def _tracer(exporter, **kwargs):
    return Tracer(
        exporters=[exporter],
        clock=lambda: 1000,
        id_factory=_counter(),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("filename", "expected_events"),
    [
        (
            "telnyx_media_stream.jsonl",
            {"connected", "start", "media", "stop"},
        ),
        (
            "twilio_media_stream.jsonl",
            {"connected", "start", "media", "stop"},
        ),
    ],
)
def test_cpaas_recorded_media_stream_fixture_has_required_redacted_frames(
    filename,
    expected_events,
):
    path = CPAAS_FIXTURE_DIRECTORY / filename
    raw_lines = path.read_bytes().splitlines()

    assert raw_lines
    frames = [json.loads(line) for line in raw_lines]
    assert {frame["event"] for frame in frames} == expected_events
    assert all(
        json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode()
        == raw_line
        for frame, raw_line in zip(frames, raw_lines)
    )
    assert not PHONE_NUMBER_PATTERN.search(path.read_text(encoding="utf-8"))
    assert all("<redacted-" in json.dumps(frame) for frame in frames[1:])


def test_cpaas_cost_metadata_uses_typed_wire_tags_without_phone_numbers():
    metadata = CpaasCostMetadata(
        direction="outbound",
        provider="telnyx",
        country_code="ES",
        billable_seconds=12.5,
    )
    event = CostEvent(
        event_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        session_id="session-1",
        emitted_at_ms=1,
        cost=CostBreakdown(telephony_cost=0.01, billable_audio_minutes=0.25),
        tags=metadata.to_tags(),
        provider_attribution={
            CostComponent.TELEPHONY_COST: ProviderIdentity(provider="telnyx")
        },
    )

    wire = event.to_wire()
    assert wire["tags"] == {
        "telephony.direction": "outbound",
        "telephony.provider": "cpaas/telnyx",
        "telephony.country_code": "ES",
        "telephony.billable_seconds": "12.5",
        "telephony.cost_component": "telephony_cost",
    }
    assert not PHONE_NUMBER_PATTERN.search(json.dumps(wire))


@pytest.mark.parametrize("country_code", ("ZZ", "UK", "es"))
def test_cpaas_cost_metadata_rejects_unassigned_country_codes(country_code):
    with pytest.raises(ValidationError):
        CpaasCostMetadata(
            direction="outbound",
            provider="telnyx",
            country_code=country_code,
            billable_seconds=1,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"country_code": "es"},
        {"country_code": "+34"},
        {"provider": "unknown"},
        {"billable_seconds": -1},
    ],
)
def test_cpaas_cost_metadata_rejects_unregistered_or_unsafe_dimensions(payload):
    values = {
        "direction": "inbound",
        "provider": "twilio",
        "country_code": "ES",
        "billable_seconds": 1,
    }
    values.update(payload)
    with pytest.raises(ValidationError):
        CpaasCostMetadata(**values)


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


def test_cost_provider_attribution_has_typed_additive_wire_shape():
    event = CostEvent(
        event_id=str(uuid.uuid4()),
        session_id="s1",
        emitted_at_ms=1,
        cost=CostBreakdown(
            stt_cost=0.01,
            llm_cost=0.02,
            billable_audio_minutes=1.0,
        ),
        provider_attribution={
            CostComponent.STT_COST: ProviderIdentity(
                provider="deepgram", model="nova-3"
            ),
            CostComponent.LLM_COST: ProviderIdentity(provider="openai", model="gpt-5"),
        },
    )

    wire = event.to_wire()

    assert wire["provider_attribution"] == {
        "stt_cost": {"provider": "deepgram", "model": "nova-3"},
        "llm_cost": {"provider": "openai", "model": "gpt-5"},
    }


def test_cost_provider_attribution_accepts_provider_without_model():
    event = CostEvent(
        event_id=str(uuid.uuid4()),
        session_id="s1",
        emitted_at_ms=1,
        cost=CostBreakdown(infra_cost=0.01, billable_audio_minutes=1.0),
        provider_attribution={
            CostComponent.INFRA_COST: ProviderIdentity(provider="local")
        },
    )

    assert event.to_wire()["provider_attribution"] == {
        "infra_cost": {"provider": "local", "model": None}
    }


@pytest.mark.parametrize(
    "component",
    [
        CostComponent.STT_COST,
        CostComponent.LLM_COST,
        CostComponent.TTS_COST,
        CostComponent.TELEPHONY_COST,
        CostComponent.RAG_COST,
        CostComponent.MCP_TOOL_COST,
        CostComponent.INFRA_COST,
    ],
)
def test_cost_provider_attribution_accepts_each_cost_component(component):
    identity = ProviderIdentity(provider="fixture", model="model-v1")
    event = CostEvent(
        event_id=str(uuid.uuid4()),
        session_id="s1",
        emitted_at_ms=1,
        cost=CostBreakdown(billable_audio_minutes=1.0),
        provider_attribution={component: identity},
    )

    assert event.provider_attribution == {component: identity}


def test_legacy_cost_event_without_provider_attribution_stays_valid():
    event = CostEvent.model_validate(
        {
            "event_id": str(uuid.uuid4()),
            "session_id": "s1",
            "emitted_at_ms": 1,
            "cost": {"llm_cost": 0.02, "billable_audio_minutes": 1.0},
        }
    )

    assert event.provider_attribution == {}


def test_cost_provider_attribution_rejects_non_component_keys():
    with pytest.raises(ValidationError):
        CostEvent(
            event_id=str(uuid.uuid4()),
            session_id="s1",
            emitted_at_ms=1,
            cost=CostBreakdown(llm_cost=0.02, billable_audio_minutes=1.0),
            provider_attribution={
                "prompt_tokens": ProviderIdentity(provider="openai", model="gpt-5")
            },
        )


@pytest.mark.parametrize(
    "identity",
    [
        {"provider": "https://stt.example/v1", "model": "nova-3"},
        {"provider": "api.openai.com", "model": "nova-3"},
        {"provider": "api.prod.internal.", "model": "nova-3"},
        {"provider": "api.prod.internal..", "model": "nova-3"},
        {"provider": "api.xn--p1ai", "model": "nova-3"},
        {"provider": "example.123", "model": "nova-3"},
        {"provider": "api.example.123", "model": "nova-3"},
        {"provider": "0x7f000001", "model": "nova-3"},
        {"provider": "https:api.openai.com", "model": "nova-3"},
        {"provider": "http:localhost", "model": "nova-3"},
        {"provider": "http:127.0.0.1", "model": "nova-3"},
        {"provider": "redis:cache", "model": "nova-3"},
        {"provider": "ssh:server", "model": "nova-3"},
        {"provider": "postgresql:db", "model": "nova-3"},
        {"provider": "deepgram", "model": "mqtt:broker-01"},
        {"provider": "deepgram", "model": "s3:private-bucket"},
        {"provider": "deepgram", "model": "h2:internal-service"},
        {"provider": "deepgram", "model": "socks5:proxy"},
        {"provider": "deepgram", "model": "http2:metadata"},
        {"provider": "stt.internal.example:8443", "model": "nova-3"},
        {"provider": "deepgram", "model": "relay:443"},
        {"provider": "deepgram", "model": "example.123"},
        {"provider": "deepgram", "model": "example.123."},
        {"provider": "deepgram", "model": "example.123.."},
        {"provider": "127.0.0.1", "model": "nova-3"},
        {"provider": "localhost", "model": "nova-3"},
        {"provider": "api_key=do-not-export", "model": "nova-3"},
        {"provider": "deepgram", "model": _SYNTHETIC_BEARER_MODEL},
        {
            "provider": "deepgram",
            "model": _SYNTHETIC_SK_MODEL,
        },
        {
            "provider": "deepgram",
            "model": _SYNTHETIC_JWT_MODEL,
        },
    ],
)
def test_cost_provider_attribution_rejects_endpoints_and_secrets(identity):
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True)

    with pytest.raises(ValidationError):
        tracer.cost(
            session_id="s1",
            cost=CostBreakdown(llm_cost=0.02, billable_audio_minutes=1.0),
            provider_attribution={CostComponent.LLM_COST: identity},  # type: ignore[dict-item]
        )
    tracer.flush()

    assert exporter.events == []


def test_cost_provider_attribution_revalidates_copied_identity():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    identity = ProviderIdentity(provider="fixture", model="model-v1").model_copy(
        update={"provider": "api.openai.com"}
    )

    with pytest.raises(ValidationError):
        tracer.cost(
            session_id="s1",
            cost=CostBreakdown(llm_cost=0.02, billable_audio_minutes=1.0),
            provider_attribution={CostComponent.LLM_COST: identity},
        )

    assert exporter.events == []


def test_cost_provider_attribution_drops_configured_secret(monkeypatch):
    from tests.test_secret_fixtures import synthetic_configured_secret

    secret = synthetic_configured_secret()
    monkeypatch.setenv("LUCY_API_KEY", secret)
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.cost(
        session_id="s1",
        cost=CostBreakdown(llm_cost=0.02, billable_audio_minutes=1.0),
        provider_attribution={
            CostComponent.LLM_COST: ProviderIdentity(
                provider="fixture",
                model=secret,
            )
        },
    )
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 1


def test_component_attribution_is_not_overridden_by_coarse_provider_tag():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, tags={"provider": "coarse-provider"})
    identity = ProviderIdentity(provider="component-provider", model="model-v1")

    tracer.cost(
        session_id="s1",
        cost=CostBreakdown(llm_cost=0.02, billable_audio_minutes=1.0),
        provider_attribution={CostComponent.LLM_COST: identity},
    )
    tracer.flush()

    event = next(item for item in exporter.events if item.type == "cost")
    assert event.tags["provider"] == "coarse-provider"
    assert event.provider_attribution[CostComponent.LLM_COST] == identity


@pytest.mark.parametrize("model", ["gpt-4.1", "llama3.2:3b"])
def test_provider_identity_accepts_versioned_model_slugs(model):
    assert ProviderIdentity(provider="fixture-provider", model=model).model == model


def test_wire_event_id_must_be_a_uuid():
    with pytest.raises(ValueError, match="valid UUID"):
        SessionEndedEvent(
            event_id="not-a-uuid",
            session_id="s1",
            emitted_at_ms=1,
            reason="completed",
            duration_ms=1,
            billable_audio_minutes=1,
        )


def test_invalid_explicit_event_id_is_rejected_before_export():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    with pytest.raises(ValueError, match="valid UUID"):
        tracer.session_started(
            session_id="s1",
            agent_name="booking",
            spec_hash="h",
            environment="local",
            transport="sim",
            event_id="not-a-uuid",
        )
    tracer.flush()

    assert exporter.events == []


def _required_wire_string_cases():
    common = {
        "event_id": str(uuid.uuid4()),
        "session_id": "s1",
        "emitted_at_ms": 1,
    }
    cases = [
        (
            SessionStartedEvent,
            {
                **common,
                "agent_name": "agent",
                "spec_hash": "hash",
                "environment": "test",
                "transport": "sim",
            },
            ("session_id", "agent_name", "spec_hash", "environment", "transport"),
        ),
        (
            SessionEndedEvent,
            {
                **common,
                "reason": "completed",
                "duration_ms": 1,
                "billable_audio_minutes": 1,
            },
            ("reason",),
        ),
        (
            TurnEvent,
            {
                **common,
                "turn_id": "t1",
                "turn_index": 0,
                "latency_waterfall": LatencyWaterfall(),
            },
            ("turn_id",),
        ),
        (
            SpanEvent,
            {
                **common,
                "span_id": "span-1",
                "turn_id": "t1",
                "name": "node",
                "status": "ok",
                "started_at_ms": 1,
                "ended_at_ms": 2,
            },
            ("span_id", "name"),
        ),
        (
            BusinessEvent,
            {
                **common,
                "funnel_stage": "qualified",
                "funnel_confidence": 1,
                "sentiment_label": "positive",
                "sentiment_confidence": 1,
            },
            ("funnel_stage", "sentiment_label"),
        ),
        (
            ToolCallEvent,
            {
                **common,
                "turn_id": "t1",
                "server": "crm",
                "tool": "lookup",
                "allowed": True,
                "latency_ms": 1,
            },
            ("turn_id", "server", "tool"),
        ),
        (
            TranscriptEvent,
            {**common, "turn_id": "t1", "role": "caller", "text": "hello"},
            ("turn_id",),
        ),
    ]
    return [
        (model, payload, field) for model, payload, fields in cases for field in fields
    ]


@pytest.mark.parametrize(("model", "payload", "field"), _required_wire_string_cases())
def test_wire_models_reject_empty_required_strings(model, payload, field):
    invalid = {**payload, field: ""}
    with pytest.raises(ValueError):
        model(**invalid)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SessionEndedEvent(
            event_id=str(uuid.uuid4()),
            session_id="s1",
            emitted_at_ms=1,
            reason="completed",
            duration_ms=1,
            billable_audio_minutes=float("inf"),
        ),
        lambda: ToolCallEvent(
            event_id=str(uuid.uuid4()),
            session_id="s1",
            emitted_at_ms=1,
            turn_id="t1",
            server="crm",
            tool="lookup",
            allowed=True,
            latency_ms=float("inf"),
        ),
    ],
)
def test_wire_models_reject_nonfinite_numbers(factory):
    with pytest.raises(ValueError):
        factory()


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
        latency_waterfall=LatencyWaterfall(
            stt_ms=10, rag_ms=1, llm_ms=20, mcp_tools_ms=2, tts_ms=3, transport_ms=4
        ),
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
        arguments={
            "email": "john.doe@example.com",
            "note": "ok",
            "nested": {"contacts": ["john.doe@example.com", "+34 600 123 456"]},
        },
    )
    tracer.flush()

    transcript = exporter.events[0]
    tool = exporter.events[1]
    assert "@example.com" not in transcript.text
    assert "[REDACTED]" in transcript.text
    assert tool.arguments["email"] == "[REDACTED]"
    assert tool.arguments["note"] == "ok"
    assert tool.arguments["nested"] == {"contacts": ["[REDACTED]", "[REDACTED]"]}


@pytest.mark.parametrize("redact_pii", [True, False])
def test_cyclic_tool_arguments_are_dropped_and_counted_fail_open(redact_pii):
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=redact_pii)
    cyclic = {}
    cyclic["self"] = cyclic

    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
        arguments=cyclic,
    )
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 1


@pytest.mark.parametrize(
    ("container_levels", "expected_events", "expected_drops"),
    [
        (MAX_REDACTION_DEPTH, 1, 0),
        (MAX_REDACTION_DEPTH + 1, 0, 1),
    ],
)
def test_tool_argument_depth_boundary_is_fail_open(
    container_levels, expected_events, expected_drops
):
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    arguments = {}
    cursor = arguments
    for _ in range(container_levels - 1):
        nested = {}
        cursor["nested"] = nested
        cursor = nested
    cursor["value"] = "safe"

    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
        arguments=arguments,
    )
    tracer.flush()

    assert len(exporter.events) == expected_events
    assert tracer.dropped_events == expected_drops


def test_empty_container_beyond_tool_argument_depth_is_rejected():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    arguments = {}
    cursor = arguments
    for _ in range(MAX_REDACTION_DEPTH):
        nested = {}
        cursor["nested"] = nested
        cursor = nested

    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
        arguments=arguments,
    )
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 1


@pytest.mark.parametrize(
    "arguments",
    [{"value": object()}, {"value": float("inf")}],
)
def test_non_json_tool_arguments_are_dropped_and_counted(arguments):
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.tool_call(
        session_id="s1",
        turn_id="t1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
        arguments=arguments,
    )
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 1


async def test_cyclic_denied_mcp_arguments_preserve_permission_error():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=[],
        tracer=tracer,
    )
    cyclic = {}
    cyclic["self"] = cyclic

    with pytest.raises(McpPermissionError):
        await client.call_tool(
            "crm",
            "forbidden",
            cyclic,
            session_id="s1",
            turn_id="t1",
        )

    tracer.flush()
    assert len(client.audit_log) == 1
    assert exporter.events == []
    assert tracer.dropped_events == 1


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
    off.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
    )
    off.flush()
    assert dropped.events == []

    kept = InMemoryTraceExporter()
    on = _tracer(kept, sample_rate=1.0)
    on.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
    )
    on.flush()
    assert len(kept.events) == 1


def test_exporter_failure_is_fail_open_and_counts_drops():
    class Boom:
        def export_batch(self, events):
            raise RuntimeError("ingest down")

    tracer = _tracer(Boom())
    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
    )
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
    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
    )
    tracer.flush()
    assert exporter.events == []


def test_otlp_bridge_exporter_emits_one_span_per_event():
    spans = InMemoryOtelSpanExporter()
    tracer = _tracer(OtlpBridgeExporter(spans, service_name="lucy-api"))
    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=9),
    )
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


# -- card 72: telemetry run identity and tags --------------------------------


def test_configure_merges_env_configured_and_event_tags(monkeypatch):
    monkeypatch.setenv("LUCY_TAGS", "env=dev,region=eu")
    exporter = InMemoryTraceExporter()
    tracer = configure(exporters=[exporter], tags={"region": "us", "team": "voice"})
    tracer._clock = lambda: 1000
    tracer._id_factory = _counter()

    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
        tags={"team": "runtime", "call_type": "synthetic"},
    )
    tracer.flush()

    assert exporter.events[0].tags == {
        "env": "dev",
        "region": "us",
        "team": "runtime",
        "call_type": "synthetic",
    }


def test_tags_are_redacted_before_export(monkeypatch):
    monkeypatch.setenv("LUCY_TAGS", "owner=john.doe@example.com")
    exporter = InMemoryTraceExporter()
    tracer = configure(exporters=[exporter], tags={"phone": "+1 415 555 1234"})
    tracer._clock = lambda: 1000
    tracer._id_factory = _counter()

    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
        tags={"contact": "jane@example.com"},
    )
    tracer.flush()

    tags = exporter.events[0].tags
    assert tags["owner"] == "[REDACTED]"
    assert tags["phone"] == "[REDACTED]"
    assert tags["contact"] == "[REDACTED]"


def test_hex_structural_identifier_is_not_mistaken_for_phone_pii():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)
    span_id = "d687971559f9407386c63e9c1aed1a09"

    tracer.span(
        session_id="s1",
        turn_id=None,
        span_id=span_id,
        name="lucy.session",
        status="ok",
        started_at_ms=0,
        ended_at_ms=1,
    )
    tracer.flush()

    assert [event.span_id for event in exporter.events] == [span_id]
    assert tracer.dropped_events == 0


def test_session_started_carries_run_identity_tags():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, tags={"env": "test"})

    tracer.session_started(
        session_id="s1",
        agent_name="booking",
        spec_hash="spec1",
        environment="local",
        transport="sim",
        agent_version="v2",
        graph_hash="graph123",
        thread_id="thread-a",
        tags={"scenario": "happy"},
    )
    tracer.flush()

    event = exporter.events[0]
    assert event.agent_version == "v2"
    assert event.graph_hash == "graph123"
    assert event.thread_id == "thread-a"
    assert event.tags == {"env": "test", "scenario": "happy"}


# -- card 25: runtime instrumentation wired into the tracer ------------------


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

    client = McpClient(LocalMcpCommandTransport(), allowed_tools=["crm.upsert_lead"])
    asyncio.run(
        client.call_tool(
            "crm", "upsert_lead", {"lead_id": "x"}, session_id="s1", turn_id="t1"
        )
    )

    tracer = observe_module.get_tracer()
    assert tracer.enabled is False
    assert tracer._exporters == []


def test_tracer_emits_turn_event_with_waterfall():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=1.0),
    )
    tracer.flush()

    turn = next(event for event in exporter.events if event.type == "turn")
    assert turn.session_id == "s1"
    assert turn.turn_id == "t1"
    assert turn.latency_waterfall.stt_ms >= 0.0
    assert turn.interrupted is False
    assert turn.timeout_events == []


def test_turn_event_carries_barge_in_flag():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.turn(
        session_id="s1",
        turn_id="t2",
        turn_index=1,
        latency_waterfall=LatencyWaterfall(stt_ms=1.0),
        interrupted=True,
    )
    tracer.flush()

    turn = next(event for event in exporter.events if event.type == "turn")
    assert turn.interrupted is True


def test_turn_event_records_provider_timeout():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(),
        timeout_events=["stt:transcribe"],
    )
    tracer.flush()

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
        asyncio.run(client.call_tool("crm", "nope", {}, session_id="s1", turn_id="t1"))
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
        provider_attribution={
            CostComponent.LLM_COST: ProviderIdentity(provider="openai", model="gpt-5")
        },
    )
    tracer.flush()

    cost = next(event for event in exporter.events if event.type == "cost")
    assert cost.turn_id == "t1"
    assert cost.cost.total_cost == 0.5
    assert cost.cost.cost_per_minute == 0.25
    assert cost.provider_attribution[CostComponent.LLM_COST] == ProviderIdentity(
        provider="openai", model="gpt-5"
    )


def test_turn_produces_full_event_tree_with_correct_parents():
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

    tracer.turn(
        session_id=session_id,
        turn_id=turn_id,
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=1.0),
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
            "crm",
            "upsert_lead",
            {"lead_id": "x"},
            session_id=session_id,
            turn_id=turn_id,
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

    tracer.turn(
        session_id=session_id,
        turn_id=turn_id,
        turn_index=0,
        latency_waterfall=LatencyWaterfall(stt_ms=1.0),
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
            "crm",
            "upsert_lead",
            {"lead_id": "x"},
            session_id=session_id,
            turn_id=turn_id,
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
                "crm",
                "upsert_lead",
                {"lead_id": "x"},
                session_id="s1",
                turn_id="t1",
            )
        )
        custom.flush()
        assert any(event.type == "tool_call" for event in exporter.events)
    finally:
        observe_module.set_tracer(None)
