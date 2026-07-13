import uuid

import httpx
import pytest

from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe import Tracer
from lucy.observe.events import ToolCallEvent, TranscriptEvent
from lucy.observe.redact import redact_event
from lucy.testing import InMemoryTraceExporter
from lucy_cloud.client import IngestClient
from lucy_cloud.exporter import CloudTraceExporter
from .ingest_app import create_ingest_app, run_local_ingest


API_KEY = "test-project-key"
FREE_FORM_FIELDS = (
    "session_id",
    "turn_id",
    "tag_key",
    "tag_value",
    "agent_name",
    "spec_hash",
    "environment",
    "transport",
    "agent_version",
    "graph_hash",
    "thread_id",
    "reason",
    "timeout_event",
    "span_id",
    "parent_id",
    "span_name",
    "attribute_key",
    "attribute_value",
    "funnel_stage",
    "sentiment_label",
    "server",
    "tool",
    "error",
    "argument_key",
    "argument_value",
    "transcript_text",
)
STRUCTURAL_FIELDS = {
    "session_id",
    "turn_id",
    "spec_hash",
    "graph_hash",
    "thread_id",
    "span_id",
    "parent_id",
}


def _emit_turn(exporter):
    tracer = Tracer(exporters=[exporter])
    tracer.turn(
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        latency_waterfall=LatencyWaterfall(),
    )
    tracer.flush()


def _emit_free_form_field(tracer, field, value):
    session_id = value if field == "session_id" else "session-1"
    turn_id = value if field == "turn_id" else "turn-1"
    tags = (
        {value: "safe"}
        if field == "tag_key"
        else {"owner": value}
        if field == "tag_value"
        else {}
    )
    session_started_fields = {
        "agent_name",
        "spec_hash",
        "environment",
        "transport",
        "agent_version",
        "graph_hash",
        "thread_id",
    }
    if field in session_started_fields:
        values = {
            "agent_name": "booking",
            "spec_hash": "spec",
            "environment": "test",
            "transport": "sim",
            "agent_version": "v1",
            "graph_hash": "graph",
            "thread_id": "thread-1",
        }
        values[field] = value
        tracer.session_started(session_id=session_id, tags=tags, **values)
        return
    if field == "reason":
        tracer.session_ended(
            session_id=session_id,
            reason=value,
            duration_ms=1,
            billable_audio_minutes=1,
            tags=tags,
        )
        return
    if field == "timeout_event":
        tracer.turn(
            session_id=session_id,
            turn_id=turn_id,
            turn_index=1,
            latency_waterfall=LatencyWaterfall(),
            timeout_events=[value],
            tags=tags,
        )
        return
    span_fields = {
        "span_id",
        "parent_id",
        "span_name",
        "attribute_key",
        "attribute_value",
    }
    if field in span_fields:
        attributes = (
            {value: "safe"}
            if field == "attribute_key"
            else {"owner": value}
            if field == "attribute_value"
            else {}
        )
        tracer.span(
            session_id=session_id,
            turn_id=turn_id,
            span_id=value if field == "span_id" else "span-1",
            parent_id=value if field == "parent_id" else "parent-1",
            name=value if field == "span_name" else "node",
            status="ok",
            started_at_ms=1,
            ended_at_ms=2,
            attributes=attributes,
            tags=tags,
        )
        return
    if field in {"funnel_stage", "sentiment_label"}:
        tracer.business(
            session_id=session_id,
            turn_id=turn_id,
            funnel_stage=value if field == "funnel_stage" else "qualified",
            funnel_confidence=1,
            sentiment_label=value if field == "sentiment_label" else "positive",
            sentiment_confidence=1,
            tags=tags,
        )
        return
    tool_fields = {"server", "tool", "error", "argument_key", "argument_value"}
    if field in tool_fields:
        arguments = (
            {value: "safe"}
            if field == "argument_key"
            else {"owner": value}
            if field == "argument_value"
            else {}
        )
        tracer.tool_call(
            session_id=session_id,
            turn_id=turn_id,
            server=value if field == "server" else "crm",
            tool=value if field == "tool" else "lookup",
            allowed=True,
            latency_ms=1,
            error=value if field == "error" else None,
            arguments=arguments,
            tags=tags,
        )
        return
    tracer.transcript(
        session_id=session_id,
        turn_id=turn_id,
        role="caller",
        text=value if field == "transcript_text" else "safe",
        tags=tags,
    )


async def test_export_batch_delivers_wire_events_to_ingest():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    _emit_turn(exporter)
    await client.flush()
    assert app.state.ingest.stored_events[0]["type"] == "turn"
    await exporter.aclose()


def test_from_env_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("LUCY_API_KEY", raising=False)
    assert CloudTraceExporter.from_env() is None


def test_env_configuration_builds_working_exporter(monkeypatch):
    app = create_ingest_app(API_KEY)
    with run_local_ingest(app) as endpoint:
        monkeypatch.setenv("LUCY_API_KEY", API_KEY)
        monkeypatch.setenv("LUCY_ENDPOINT", endpoint)
        monkeypatch.delenv("LUCY_PROJECT", raising=False)
        exporter = CloudTraceExporter.from_env()
        assert exporter is not None
        _emit_turn(exporter)
        assert app.state.ingest.accepted.wait(1)
        exporter.close()
    assert app.state.ingest.stored_batches[0]["project"] == "default"


def test_explicit_empty_endpoint_does_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("LUCY_ENDPOINT", "http://environment")
    monkeypatch.setenv("LUCY_API_KEY", API_KEY)
    with pytest.raises(ValueError, match="endpoint"):
        CloudTraceExporter(endpoint="", api_key=API_KEY, project="project-a")


@pytest.mark.parametrize(
    "project",
    [
        "token=project-secret",
        "owner@example.com",
        "Basic:dXNlcjpwYXNz",
        "Basic=dXNlcjpwYXNz",
        "basic = dXNlcjpwYXNz",
        "redis://:project-password@cache.local/0",
    ],
)
def test_explicit_project_rejects_secret_or_pii_even_without_event_redaction(project):
    with pytest.raises(ValueError, match="project"):
        CloudTraceExporter(
            endpoint="http://127.0.0.1",
            api_key=API_KEY,
            project=project,
        )


def test_environment_project_rejects_secret_even_without_event_redaction(monkeypatch):
    monkeypatch.setenv("LUCY_ENDPOINT", "http://127.0.0.1")
    monkeypatch.setenv("LUCY_API_KEY", API_KEY)
    monkeypatch.setenv("LUCY_PROJECT", "api_key=project-secret")
    with pytest.raises(ValueError, match="project"):
        CloudTraceExporter.from_env()


@pytest.mark.parametrize(
    "project",
    ["Cookie=   ", "cookie: \t", "-----BEGIN PRIVATE KEY-----"],
)
def test_incomplete_credential_marker_project_is_allowed(project):
    exporter = CloudTraceExporter(
        endpoint="http://127.0.0.1",
        api_key=API_KEY,
        project=project,
    )
    try:
        assert exporter.client.project == project
    finally:
        exporter.close()


def test_all_explicit_settings_win_over_environment(monkeypatch):
    app = create_ingest_app(API_KEY)
    with run_local_ingest(app) as endpoint:
        monkeypatch.setenv("LUCY_ENDPOINT", "http://environment.invalid")
        monkeypatch.setenv("LUCY_API_KEY", "environment-key")
        monkeypatch.setenv("LUCY_PROJECT", "environment-project")
        exporter = CloudTraceExporter(
            endpoint=endpoint,
            api_key=API_KEY,
            project="explicit-project",
        )
        _emit_turn(exporter)
        assert app.state.ingest.accepted.wait(1)
        exporter.close()
    assert app.state.ingest.stored_batches[0]["project"] == "explicit-project"


async def test_export_batch_never_raises_on_server_error():
    app = create_ingest_app(API_KEY, fail_first_n=10)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        max_retries=0,
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    _emit_turn(exporter)
    await client.flush()
    assert client.dropped_events == 1
    await exporter.aclose()


async def test_direct_export_batch_cannot_bypass_tracer_privacy_controls():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    raw = TranscriptEvent(
        event_id=str(uuid.uuid4()),
        session_id="session-1",
        emitted_at_ms=1,
        turn_id="turn-1",
        role="caller",
        text="call +34 600 123 456",
    )

    exporter.export_batch([raw])
    await client.flush()

    assert app.state.ingest.stored_events == []
    assert client.dropped_events == 1
    await exporter.aclose()


async def test_public_redaction_does_not_issue_cloud_export_approval():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    sanitized = redact_event(
        TranscriptEvent(
            event_id=str(uuid.uuid4()),
            session_id="unsampled-session",
            emitted_at_ms=1,
            turn_id="turn-1",
            role="caller",
            text="owner@example.com",
        ),
        redact_pii=True,
        record_audio=False,
        transcripts_enabled=True,
    )
    assert isinstance(sanitized, TranscriptEvent)

    exporter.export_batch([sanitized])
    await client.flush()

    assert app.state.ingest.stored_events == []
    assert client.dropped_events == 1
    await exporter.aclose()


@pytest.mark.parametrize(
    "mutation",
    ["in-place", "model-copy", "model-copy-update"],
)
async def test_privacy_approval_is_invalidated_by_post_redaction_mutation(
    mutation,
):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    capture = InMemoryTraceExporter()
    tracer = Tracer(exporters=[capture])
    tracer.transcript(
        session_id="session-1",
        turn_id="turn-1",
        role="caller",
        text="safe text",
        emitted_at_ms=1,
        event_id=str(uuid.uuid4()),
    )
    tracer.flush()
    approved = capture.events[0]
    assert isinstance(approved, TranscriptEvent)
    unsafe_text = "call +34 600 123 456"
    if mutation == "model-copy":
        approved = approved.model_copy()
    elif mutation == "model-copy-update":
        approved = approved.model_copy(update={"text": unsafe_text})
    else:
        approved.text = unsafe_text

    exporter.export_batch([approved])
    await client.flush()

    assert app.state.ingest.stored_events == []
    assert client.dropped_events == 1
    await exporter.aclose()


@pytest.mark.parametrize("allowed", [True, False])
async def test_nested_tool_arguments_are_redacted_before_cloud_export(allowed):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter])
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="crm",
        tool="lookup",
        allowed=allowed,
        latency_ms=1,
        arguments={
            "person@example.com": {
                "contacts": ["person@example.com", "+34 600 123 456"]
            }
        },
    )
    tracer.flush()
    await client.flush()

    stored = app.state.ingest.stored_events[0]
    assert stored["allowed"] is allowed
    assert stored["arguments"] == {
        "[REDACTED]": {"contacts": ["[REDACTED]", "[REDACTED]"]}
    }
    await exporter.aclose()


async def test_free_form_diagnostics_are_redacted_before_cloud_export():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter])
    tracer.session_started(
        session_id="session-1",
        agent_name="owner@example.com",
        spec_hash="spec",
        environment="call +34 600 123 456",
        transport="sim",
    )
    tracer.session_ended(
        session_id="session-1",
        reason="owner@example.com",
        duration_ms=1,
        billable_audio_minutes=1,
    )
    tracer.turn(
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        latency_waterfall=LatencyWaterfall(),
        timeout_events=["call +34 600 123 456"],
    )
    tracer.span(
        session_id="session-1",
        turn_id="turn-1",
        span_id="span-1",
        name="owner@example.com",
        status="error",
        started_at_ms=1,
        ended_at_ms=2,
        attributes={
            "owner@example.com": "call +34 600 123 456",
        },
    )
    tracer.business(
        session_id="session-1",
        funnel_stage="owner@example.com",
        funnel_confidence=1,
        sentiment_label="call +34 600 123 456",
        sentiment_confidence=1,
    )
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="owner@example.com",
        tool="call +34 600 123 456",
        allowed=False,
        latency_ms=1,
        error="owner@example.com",
    )
    tracer.flush()
    await client.flush()

    stored = app.state.ingest.stored_events
    assert len(stored) == 6
    assert "owner@example.com" not in repr(stored)
    assert "+34 600 123 456" not in repr(stored)
    assert "[REDACTED]" in repr(stored)
    await exporter.aclose()


@pytest.mark.parametrize("field", FREE_FORM_FIELDS)
@pytest.mark.parametrize(
    "pii",
    ["owner@example.com", "+34 600 123 456"],
    ids=["email", "phone"],
)
async def test_each_free_form_field_is_redacted_before_cloud_export(field, pii):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter])

    _emit_free_form_field(tracer, field, pii)
    tracer.flush()
    await client.flush()

    stored_events = app.state.ingest.stored_events
    if field in STRUCTURAL_FIELDS:
        assert stored_events == []
        assert tracer.dropped_events == 1
    else:
        assert len(stored_events) == 1
        stored = repr(stored_events[0])
        assert pii not in stored
        assert "[REDACTED]" in stored
    await exporter.aclose()


async def test_distinct_unsafe_session_ids_are_rejected_not_merged():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter])

    for session_id in ("alice@example.com", "bob@example.com"):
        tracer.transcript(
            session_id=session_id,
            turn_id="turn-1",
            role="caller",
            text="safe",
        )
    tracer.flush()
    await client.flush()

    assert app.state.ingest.stored_events == []
    assert tracer.dropped_events == 2
    await exporter.aclose()


@pytest.mark.parametrize("redact_pii", [True, False])
async def test_runtime_secrets_are_scrubbed_independent_of_pii_setting(redact_pii):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter], redact_pii=redact_pii)
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="crm",
        tool="lookup",
        allowed=False,
        latency_ms=1,
        arguments={
            "password": "password-value",
            "api_key": "api-key-value",
            "jwt": "jwt-value",
        },
        error="Bearer bearer-value token=diagnostic-value",
    )
    tracer.span(
        session_id="session-1",
        turn_id="turn-1",
        span_id="span-1",
        name="node",
        status="error",
        started_at_ms=1,
        ended_at_ms=2,
        attributes={
            "authorization": "Bearer attribute-value",
            "note": "token=span-value",
        },
    )
    tracer.flush()
    await client.flush()

    stored = repr(app.state.ingest.stored_events)
    for secret in (
        "password-value",
        "api-key-value",
        "jwt-value",
        "bearer-value",
        "diagnostic-value",
        "attribute-value",
        "span-value",
    ):
        assert secret not in stored
    assert "[REDACTED]" in stored
    await exporter.aclose()


@pytest.mark.parametrize("redact_pii", [True, False])
async def test_authentication_and_cloud_credentials_never_reach_cloud(redact_pii):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter], redact_pii=redact_pii)
    secrets = {
        "basic": "Basic dXNlcjpwYXNz",
        "basic_colon": "Basic:dXNlcjpwYXNz",
        "basic_equals": "Basic=dXNlcjpwYXNz",
        "basic_spaced_equals": "basic = dXNlcjpwYXNz",
        "cookie": "sessionid=cookie-secret",
        "cookie_diagnostic": "Cookie=sessionid=cookie-equals-secret",
        "database_url": "postgresql://alice:url-secret@db.local/app",
        "redis_url": "redis://:redis-password@cache.local/0",
        "private_key": (
            "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n"
            "-----END PRIVATE KEY-----"
        ),
        "truncated_private_key": (
            "-----BEGIN PRIVATE KEY-----\ntruncated-private-key-material"
        ),
        "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "credentials": "credential-value",
    }
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="crm",
        tool="lookup",
        allowed=False,
        latency_ms=1,
        arguments=secrets,
        error="Cookie: sessionid=diagnostic-cookie",
    )
    tracer.flush()
    await client.flush()

    stored = repr(app.state.ingest.stored_events)
    for secret in (
        "dXNlcjpwYXNz",
        "cookie-secret",
        "cookie-equals-secret",
        "url-secret",
        "redis-password",
        "private-key-material",
        "truncated-private-key-material",
        "AKIAIOSFODNN7EXAMPLE",
        "credential-value",
        "diagnostic-cookie",
    ):
        assert secret not in stored
    assert "[REDACTED]" in stored
    await exporter.aclose()


@pytest.mark.parametrize("blob_id", ["api_key:super-secret", "Basic:dXNlcjpwYXNz"])
async def test_secret_bearing_recording_reference_is_rejected_before_cloud_export(
    blob_id,
):
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter], record_audio=True)

    with pytest.raises(ValueError, match="recording reference"):
        tracer.audio_ref(
            session_id="session-1",
            blob_id=blob_id,
        )
    tracer.flush()
    await client.flush()

    assert app.state.ingest.stored_events == []
    await exporter.aclose()


async def test_corrupt_approved_event_does_not_abort_following_valid_event():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    capture = InMemoryTraceExporter()
    tracer = Tracer(exporters=[capture])
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
        emitted_at_ms=1,
        event_id=str(uuid.uuid4()),
    )
    tracer.transcript(
        session_id="session-1",
        turn_id="turn-1",
        role="caller",
        text="safe",
        emitted_at_ms=2,
        event_id=str(uuid.uuid4()),
    )
    tracer.flush()
    corrupt, valid = capture.events
    assert isinstance(corrupt, ToolCallEvent)
    assert isinstance(valid, TranscriptEvent)
    corrupt.arguments = {"invalid": object()}

    exporter.export_batch([corrupt, valid])
    await client.flush()

    assert [event["type"] for event in app.state.ingest.stored_events] == ["transcript"]
    assert client.dropped_events == 1
    await exporter.aclose()


async def test_all_wire_event_variants_are_forwarded_without_rewriting():
    app = create_ingest_app(API_KEY)
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
    )
    exporter = CloudTraceExporter(client=client)
    tracer = Tracer(exporters=[exporter], record_audio=True)
    tracer.session_started(
        session_id="session-1",
        agent_name="booking",
        spec_hash="spec",
        environment="test",
        transport="sim",
    )
    tracer.session_ended(
        session_id="session-1",
        reason="done",
        duration_ms=100,
        billable_audio_minutes=0.1,
    )
    tracer.turn(
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        latency_waterfall=LatencyWaterfall(),
    )
    tracer.span(
        session_id="session-1",
        turn_id="turn-1",
        span_id="span-1",
        name="node",
        status="ok",
        started_at_ms=1,
        ended_at_ms=2,
    )
    tracer.cost(
        session_id="session-1",
        cost=CostBreakdown(billable_audio_minutes=0.1),
    )
    tracer.business(
        session_id="session-1",
        funnel_stage="qualified",
        funnel_confidence=1.0,
        sentiment_label="positive",
        sentiment_confidence=1.0,
    )
    tracer.tool_call(
        session_id="session-1",
        turn_id="turn-1",
        server="crm",
        tool="lookup",
        allowed=True,
        latency_ms=1,
    )
    tracer.transcript(
        session_id="session-1", turn_id="turn-1", role="caller", text="hello"
    )
    tracer.audio_ref(session_id="session-1", blob_id="blob-safe")
    tracer.flush()
    await client.flush()
    assert {event["type"] for event in app.state.ingest.stored_events} == {
        "session.started",
        "session.ended",
        "turn",
        "span",
        "cost",
        "business",
        "tool_call",
        "transcript",
        "audio_ref",
    }
    by_type = {event["type"]: event for event in app.state.ingest.stored_events}
    assert by_type["session.started"]["agent_name"] == "booking"
    assert by_type["session.ended"]["duration_ms"] == 100
    assert by_type["turn"]["latency_waterfall"]["llm_ms"] == 0.0
    assert by_type["span"]["status"] == "ok"
    assert by_type["cost"]["total_cost"] == 0.0
    assert "cost" not in by_type["cost"]
    assert by_type["business"]["funnel_stage"] == "qualified"
    assert by_type["tool_call"]["tool"] == "lookup"
    assert by_type["transcript"]["text"] == "hello"
    assert by_type["audio_ref"]["blob_id"] == "blob-safe"
    await exporter.aclose()
