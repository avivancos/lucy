import json
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import lucy.serve.devviewer as devviewer
from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe import JsonlFileExporter, Tracer
from lucy.serve.devviewer import (
    DevViewerSettings,
    create_viewer_app,
    load_trace,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "quickstart_trace.jsonl"
SEGMENT_KEYS = {"stt_ms", "rag_ms", "llm_ms", "mcp_tools_ms", "tts_ms", "transport_ms"}


def _id_factory():
    state = {"n": 0}

    def factory() -> str:
        state["n"] += 1
        return "evt_%d" % state["n"]

    return factory


def _write_controlled_trace(path: Path, *, tool_calls: bool = False) -> None:
    tracer = Tracer(
        exporters=[JsonlFileExporter(path)],
        clock=lambda: 1000,
        id_factory=_id_factory(),
    )
    tracer.session_started(
        session_id="s1",
        agent_name="booking",
        spec_hash="hash",
        environment="local",
        transport="sim",
        emitted_at_ms=10,
    )
    tracer.transcript(
        session_id="s1",
        turn_id="t2",
        role="agent",
        text="<confirmed>",
        emitted_at_ms=50,
    )
    tracer.transcript(
        session_id="s1",
        turn_id="t1",
        role="caller",
        text="hello <agent>",
        emitted_at_ms=30,
    )
    tracer.turn(
        session_id="s1",
        turn_id="t2",
        turn_index=1,
        latency_waterfall=LatencyWaterfall(stt_ms=2, rag_ms=3, llm_ms=4),
        emitted_at_ms=80,
    )
    tracer.turn(
        session_id="s1",
        turn_id="t1",
        turn_index=0,
        latency_waterfall=LatencyWaterfall(
            stt_ms=10,
            rag_ms=1,
            llm_ms=20,
            mcp_tools_ms=2,
            tts_ms=3,
            transport_ms=4,
        ),
        interrupted=True,
        emitted_at_ms=70,
    )
    if tool_calls:
        tracer.tool_call(
            session_id="s1",
            turn_id="t1",
            server="crm",
            tool="upsert_lead",
            allowed=True,
            latency_ms=12.5,
            arguments={"lead": "<redacted>"},
            error="<none>",
            emitted_at_ms=60,
        )
    tracer.cost(
        session_id="s1",
        turn_id="t1",
        cost=CostBreakdown(llm_cost=0.5, tts_cost=0.25, billable_audio_minutes=3.0),
        emitted_at_ms=90,
    )
    tracer.session_ended(
        session_id="s1",
        reason="done",
        duration_ms=120,
        billable_audio_minutes=3.0,
        emitted_at_ms=100,
    )
    tracer.flush()


def _fixture_events() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_quickstart_fixture_lines_are_wire_v1_events():
    events = _fixture_events()
    assert events
    for event in events:
        assert isinstance(event, dict)
        assert {"type", "event_id", "session_id", "emitted_at_ms"} <= set(event)

    types = [event["type"] for event in events]
    assert "session.started" in types
    assert "turn" in types
    assert types.count("transcript") >= 2
    assert "cost" in types

    turn = next(event for event in events if event["type"] == "turn")
    assert SEGMENT_KEYS <= set(turn["latency_waterfall"])


def test_settings_read_trace_file_and_port_from_env(monkeypatch, tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    monkeypatch.setenv("LUCY_TRACE_FILE", str(trace_file))
    monkeypatch.setenv("LUCY_DEVVIEWER_PORT", "9797")

    settings = DevViewerSettings()

    assert settings.trace_file == trace_file
    assert settings.port == 9797
    assert settings.host == "127.0.0.1"


def test_load_trace_builds_session_view_from_quickstart_fixture():
    summary = load_trace(FIXTURE)

    assert summary.skipped_lines == 0
    assert len(summary.sessions) == 1
    session = summary.sessions[0]
    assert session.session_id == "quickstart-session"
    assert session.agent_name == "Quickstart Agent"
    assert [turn.turn_index for turn in session.turns] == sorted(
        turn.turn_index for turn in session.turns
    )
    assert [line.role for line in session.transcript] == ["caller", "agent"]
    assert session.total_cost == 0.0
    assert session.cost_per_minute == 0.0


def test_load_trace_counts_and_skips_malformed_lines(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    _write_controlled_trace(trace_file)
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
        handle.write(json.dumps(["not", "a", "dict"]))
        handle.write("\n")

    summary = load_trace(trace_file)

    assert summary.skipped_lines == 2
    assert len(summary.sessions) == 1
    assert [turn.turn_index for turn in summary.sessions[0].turns] == [0, 1]


def test_load_trace_ignores_unknown_event_types(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    _write_controlled_trace(trace_file)
    with trace_file.open("a", encoding="utf-8") as handle:
        json.dump(
            {
                "type": "future.event",
                "event_id": "future",
                "session_id": "s1",
                "emitted_at_ms": 999,
                "payload": "ignored",
            },
            handle,
        )
        handle.write("\n")

    summary = load_trace(trace_file)

    assert summary.skipped_lines == 0
    assert len(summary.sessions[0].turns) == 2
    assert len(summary.sessions[0].transcript) == 2


def test_create_viewer_app_has_exactly_one_route(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text("", encoding="utf-8")
    app = create_viewer_app(DevViewerSettings(trace_file=trace_file))

    routes = [route for route in app.routes if isinstance(route, APIRoute)]

    assert len(routes) == 1
    assert routes[0].path == "/"


def test_index_page_renders_turn_waterfalls_from_fixture():
    response = TestClient(create_viewer_app(DevViewerSettings(trace_file=FIXTURE))).get(
        "/"
    )

    body = response.text
    summary = load_trace(FIXTURE)

    assert response.status_code == 200
    assert 'id="waterfalls"' in body
    for segment in ("stt", "rag", "llm", "mcp_tools", "tts", "transport"):
        assert segment in body
    for turn in summary.sessions[0].turns:
        assert "%.1fms" % turn.waterfall.total_ms in body


def test_index_page_renders_transcript_in_order_and_escapes_text(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    _write_controlled_trace(trace_file)
    body = (
        TestClient(create_viewer_app(DevViewerSettings(trace_file=trace_file)))
        .get("/")
        .text
    )

    assert 'id="transcript"' in body
    assert "hello &lt;agent&gt;" in body
    assert "&lt;confirmed&gt;" in body
    assert body.index("caller:") < body.index("agent:")


def test_index_page_renders_session_cost_and_cost_per_minute():
    response = TestClient(create_viewer_app(DevViewerSettings(trace_file=FIXTURE))).get(
        "/"
    )

    body = response.text
    session = load_trace(FIXTURE).sessions[0]

    assert response.status_code == 200
    assert 'id="costs"' in body
    assert "%.6f" % session.total_cost in body
    assert "%.6f" % session.cost_per_minute in body


def test_tool_call_audit_renders_recorded_tool_events(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    _write_controlled_trace(trace_file, tool_calls=True)
    body = (
        TestClient(create_viewer_app(DevViewerSettings(trace_file=trace_file)))
        .get("/")
        .text
    )

    assert 'id="tool-calls"' in body
    assert "crm" in body
    assert "upsert_lead" in body
    assert "true" in body
    assert "12.5" in body
    assert "&lt;none&gt;" in body


def test_tool_call_panel_shows_empty_state_without_tool_events():
    body = (
        TestClient(create_viewer_app(DevViewerSettings(trace_file=FIXTURE)))
        .get("/")
        .text
    )

    assert "No tool calls recorded." in body


def test_missing_trace_file_renders_guidance_page(tmp_path):
    missing = tmp_path / "missing.jsonl"
    response = TestClient(create_viewer_app(DevViewerSettings(trace_file=missing))).get(
        "/"
    )

    assert response.status_code == 200
    assert "LUCY_TRACE_FILE" in response.text
    assert "quickstart" in response.text


def test_empty_trace_file_renders_empty_state(tmp_path):
    trace_file = tmp_path / "empty.jsonl"
    trace_file.write_text("", encoding="utf-8")
    response = TestClient(
        create_viewer_app(DevViewerSettings(trace_file=trace_file))
    ).get("/")

    assert response.status_code == 200
    assert "No events yet." in response.text


def test_index_page_reports_skipped_lines_banner(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    _write_controlled_trace(trace_file)
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")

    response = TestClient(
        create_viewer_app(DevViewerSettings(trace_file=trace_file))
    ).get("/")

    assert "1 malformed lines skipped" in response.text


def test_index_route_re_reads_trace_file_each_request(tmp_path):
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text("", encoding="utf-8")
    client = TestClient(create_viewer_app(DevViewerSettings(trace_file=trace_file)))

    assert "No events yet." in client.get("/").text
    _write_controlled_trace(trace_file)

    assert 'id="waterfalls"' in client.get("/").text


def test_module_docstring_states_adr_0010_scope_cap():
    assert (
        "Scope cap (ADR 0010): no storage, no auth, no cross-run comparisons, no audio."
        in (devviewer.__doc__ or "")
    )
