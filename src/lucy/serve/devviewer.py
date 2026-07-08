"""Local HTML trace viewer for ``LUCY_TRACE_FILE`` JSONL traces.

Scope cap (ADR 0010): no storage, no auth, no cross-run comparisons, no audio.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucy.metrics import LatencyWaterfall

SEGMENT_FIELDS = (
    "stt_ms",
    "rag_ms",
    "llm_ms",
    "mcp_tools_ms",
    "tts_ms",
    "transport_ms",
)
SEGMENT_LABELS = ("stt", "rag", "llm", "mcp_tools", "tts", "transport")


class DevViewerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUCY_DEVVIEWER_",
        populate_by_name=True,
    )

    trace_file: Optional[Path] = Field(
        default=None,
        validation_alias="LUCY_TRACE_FILE",
    )
    host: str = "127.0.0.1"
    port: int = 8642


@dataclass
class TurnView:
    turn_id: str
    turn_index: int
    waterfall: LatencyWaterfall
    interrupted: bool


@dataclass
class TranscriptLine:
    turn_id: str
    role: str
    text: str
    emitted_at_ms: int = 0


@dataclass
class ToolCallView:
    turn_id: str
    server: str
    tool: str
    allowed: bool
    latency_ms: float
    error: Optional[str]
    emitted_at_ms: int = 0


@dataclass
class CostView:
    turn_id: Optional[str]
    total_cost: float
    cost_per_minute: Optional[float]
    billable_audio_minutes: float
    emitted_at_ms: int = 0


@dataclass
class SessionView:
    session_id: str
    agent_name: Optional[str] = None
    turns: List[TurnView] = field(default_factory=list)
    transcript: List[TranscriptLine] = field(default_factory=list)
    tool_calls: List[ToolCallView] = field(default_factory=list)
    costs: List[CostView] = field(default_factory=list)
    total_cost: float = 0.0
    cost_per_minute: Optional[float] = None


@dataclass
class TraceSummary:
    sessions: List[SessionView]
    skipped_lines: int


def _as_text(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _session_for(
    sessions: Dict[str, SessionView],
    order: List[str],
    session_id: str,
) -> SessionView:
    if session_id not in sessions:
        sessions[session_id] = SessionView(session_id=session_id)
        order.append(session_id)
    return sessions[session_id]


def _load_event(line: str) -> tuple[Optional[Dict[str, object]], bool]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(event, dict):
        return None, True
    return event, False


def load_trace(path: Path) -> TraceSummary:
    sessions: Dict[str, SessionView] = {}
    order: List[str] = []
    skipped_lines = 0

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event, skipped = _load_event(line)
            if skipped:
                skipped_lines += 1
                continue
            if event is None:
                continue
            event_type = event.get("type")
            if event_type in {"span", "business", "audio_ref"}:
                continue
            session_id = event.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            session = _session_for(sessions, order, session_id)
            if event_type == "session.started":
                session.agent_name = _as_text(event.get("agent_name")) or None
            elif event_type == "turn":
                waterfall_data = event.get("latency_waterfall")
                if not isinstance(waterfall_data, dict):
                    continue
                session.turns.append(
                    TurnView(
                        turn_id=_as_text(event.get("turn_id")),
                        turn_index=_as_int(event.get("turn_index")),
                        waterfall=LatencyWaterfall(**waterfall_data),
                        interrupted=bool(event.get("interrupted", False)),
                    )
                )
            elif event_type == "transcript":
                session.transcript.append(
                    TranscriptLine(
                        turn_id=_as_text(event.get("turn_id")),
                        role=_as_text(event.get("role")),
                        text=_as_text(event.get("text")),
                        emitted_at_ms=_as_int(event.get("emitted_at_ms")),
                    )
                )
            elif event_type == "tool_call":
                session.tool_calls.append(
                    ToolCallView(
                        turn_id=_as_text(event.get("turn_id")),
                        server=_as_text(event.get("server")),
                        tool=_as_text(event.get("tool")),
                        allowed=bool(event.get("allowed", False)),
                        latency_ms=_as_float(event.get("latency_ms")),
                        error=(
                            None
                            if event.get("error") is None
                            else _as_text(event.get("error"))
                        ),
                        emitted_at_ms=_as_int(event.get("emitted_at_ms")),
                    )
                )
            elif event_type == "cost":
                cost = event.get("cost")
                if not isinstance(cost, dict):
                    continue
                cost_per_minute: Optional[float]
                if cost.get("cost_per_minute") is None:
                    cost_per_minute = None
                else:
                    cost_per_minute = _as_float(cost.get("cost_per_minute"))
                session.costs.append(
                    CostView(
                        turn_id=(
                            None
                            if event.get("turn_id") is None
                            else _as_text(event.get("turn_id"))
                        ),
                        total_cost=_as_float(cost.get("total_cost")),
                        cost_per_minute=cost_per_minute,
                        billable_audio_minutes=_as_float(
                            cost.get("billable_audio_minutes")
                        ),
                        emitted_at_ms=_as_int(event.get("emitted_at_ms")),
                    )
                )

    result_sessions = [sessions[session_id] for session_id in order]
    for session in result_sessions:
        session.turns.sort(key=lambda turn: turn.turn_index)
        session.transcript.sort(key=lambda line: line.emitted_at_ms)
        session.tool_calls.sort(key=lambda call: call.emitted_at_ms)
        session.costs.sort(key=lambda cost: cost.emitted_at_ms)
        session.total_cost = sum(cost.total_cost for cost in session.costs)
        billable_minutes = sum(cost.billable_audio_minutes for cost in session.costs)
        session.cost_per_minute = (
            session.total_cost / billable_minutes if billable_minutes > 0 else None
        )
    return TraceSummary(sessions=result_sessions, skipped_lines=skipped_lines)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return "%.6f" % value


def _page(title: str, content: str) -> HTMLResponse:
    css = """
<style>
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0;
  color: #18202a;
  background: #f6f7f8;
}
main { max-width: 1180px; margin: 0 auto; padding: 28px; }
h1 { font-size: 28px; margin: 0 0 8px; }
h2 { font-size: 20px; margin: 28px 0 12px; }
h3 { font-size: 16px; margin: 0 0 10px; }
.trace-path { color: #54606d; margin: 0 0 18px; }
.banner {
  background: #fff3cd;
  border: 1px solid #f2cf66;
  padding: 10px 12px;
  margin: 16px 0;
}
.panel {
  background: #ffffff;
  border: 1px solid #dce1e7;
  border-radius: 8px;
  padding: 16px;
  margin: 14px 0;
}
.waterfall-row {
  display: grid;
  grid-template-columns: 88px 1fr 92px 96px;
  gap: 12px;
  align-items: center;
  margin: 10px 0;
}
.bars {
  display: flex;
  height: 28px;
  border: 1px solid #d9e0e7;
  background: #eef2f5;
  overflow: hidden;
}
.bar {
  min-width: 2px;
}
.segment-values {
  display: grid;
  grid-template-columns: repeat(6, minmax(76px, 1fr));
  gap: 6px;
  margin-top: 6px;
  color: #384554;
  font-size: 12px;
}
.bar-stt { background: #8fd3ff; }
.bar-rag { background: #a7e4bd; }
.bar-llm { background: #f7cf75; }
.bar-mcp_tools { background: #c4b6ff; }
.bar-tts { background: #ffb0a8; }
.bar-transport { background: #b5c5d8; }
.muted { color: #657282; }
.interrupted { color: #9a3412; font-weight: 700; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #e4e8ee; padding: 8px; text-align: left; }
th { color: #46515e; font-size: 13px; }
.line { padding: 6px 0; border-bottom: 1px solid #edf0f3; }
.role { font-weight: 700; }
code { background: #edf1f5; padding: 2px 4px; border-radius: 4px; }
</style>
"""
    body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>%s</title>%s</head><body><main>%s</main></body></html>"
        % (_escape(title), css, content)
    )
    return HTMLResponse(body)


def _guidance_page(trace_file: Optional[Path]) -> HTMLResponse:
    path_text = "not set" if trace_file is None else str(trace_file)
    content = (
        "<h1>Lucy dev trace viewer</h1>"
        '<p class="trace-path">Trace file: %s</p>'
        '<section class="panel"><h2>Record a local trace</h2>'
        "<p>Set <code>LUCY_TRACE_FILE</code> and run the quickstart, then refresh "
        "this page.</p>"
        "<p><code>LUCY_TRACE_FILE=tests/fixtures/quickstart_trace.jsonl "
        "python examples/quickstart_voice_agent.py</code></p>"
        "</section>"
    ) % _escape(path_text)
    return _page("Lucy dev trace viewer", content)


def _render_waterfalls(session: SessionView) -> str:
    max_total = max((turn.waterfall.total_ms for turn in session.turns), default=0.0)
    rows: List[str] = []
    for turn in session.turns:
        cells: List[str] = []
        values: List[str] = []
        for label, field_name in zip(SEGMENT_LABELS, SEGMENT_FIELDS):
            value = getattr(turn.waterfall, field_name)
            width = (value / max_total * 100.0) if max_total > 0 else 0.0
            cells.append(
                '<span class="bar bar-%s" style="width: %.3f%%" '
                'title="%s %.1fms" aria-label="%s %.1fms"></span>'
                % (_escape(label), width, _escape(label), value, _escape(label), value)
            )
            values.append(
                "<span>%s %.1fms</span>"
                % (
                    _escape(label),
                    value,
                )
            )
        rows.append(
            '<div class="waterfall-row"><span>turn %d</span>'
            '<div><div class="bars">%s</div><div class="segment-values">%s</div>'
            "</div><span>%.1fms</span><span>%s</span></div>"
            % (
                turn.turn_index,
                "".join(cells),
                "".join(values),
                turn.waterfall.total_ms,
                '<span class="interrupted">interrupted</span>'
                if turn.interrupted
                else "",
            )
        )
    if not rows:
        rows.append('<p class="muted">No turns recorded.</p>')
    return (
        '<section id="waterfalls" class="panel"><h3>Waterfalls</h3>%s</section>'
        % "".join(rows)
    )


def _render_costs(session: SessionView) -> str:
    rows = ["<tr><th>turn_id</th><th>total_cost</th><th>cost_per_minute</th></tr>"]
    if session.costs:
        for cost in session.costs:
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    _escape(cost.turn_id or ""),
                    _money(cost.total_cost),
                    _money(cost.cost_per_minute),
                )
            )
    else:
        rows.append(
            '<tr><td colspan="3" class="muted">No cost events recorded.</td></tr>'
        )
    totals = (
        "<p>Session total_cost: <strong>%s</strong> cost_per_minute: "
        "<strong>%s</strong></p>"
        % (_money(session.total_cost), _money(session.cost_per_minute))
    )
    return (
        '<section id="costs" class="panel"><h3>Costs</h3>%s<table>%s</table>'
        "</section>" % (totals, "".join(rows))
    )


def _render_transcript(session: SessionView) -> str:
    if not session.transcript:
        content = '<p class="muted">No transcript recorded.</p>'
    else:
        content = "".join(
            '<div class="line"><span class="role">%s:</span> %s</div>'
            % (_escape(line.role), _escape(line.text))
            for line in session.transcript
        )
    return (
        '<section id="transcript" class="panel"><h3>Transcript</h3>%s</section>'
        % content
    )


def _render_tool_calls(session: SessionView) -> str:
    if not session.tool_calls:
        content = "No tool calls recorded."
    else:
        rows = [
            "<tr><th>server</th><th>tool</th><th>allowed</th>"
            "<th>latency_ms</th><th>error</th></tr>"
        ]
        for call in session.tool_calls:
            rows.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f</td><td>%s</td></tr>"
                % (
                    _escape(call.server),
                    _escape(call.tool),
                    "true" if call.allowed else "false",
                    call.latency_ms,
                    _escape(call.error or ""),
                )
            )
        content = "<table>%s</table>" % "".join(rows)
    return (
        '<section id="tool-calls" class="panel"><h3>Tool calls</h3>%s</section>'
        % content
    )


def _render_sessions(summary: TraceSummary, trace_file: Path) -> HTMLResponse:
    if not summary.sessions:
        content = (
            "<h1>Lucy dev trace viewer</h1>"
            '<p class="trace-path">Trace file: %s</p>'
            '<section class="panel">No events yet.</section>'
        ) % _escape(trace_file.resolve())
        return _page("Lucy dev trace viewer", content)

    parts = [
        "<h1>Lucy dev trace viewer</h1>",
        '<p class="trace-path">Trace file: %s</p>' % _escape(trace_file.resolve()),
    ]
    if summary.skipped_lines > 0:
        parts.append(
            '<div class="banner">%d malformed lines skipped</div>'
            % summary.skipped_lines
        )
    for session in summary.sessions:
        heading = session.agent_name or session.session_id
        parts.append(
            '<h2>%s <span class="muted">%s</span></h2>'
            % (_escape(heading), _escape(session.session_id))
        )
        parts.extend(
            [
                _render_waterfalls(session),
                _render_costs(session),
                _render_transcript(session),
                _render_tool_calls(session),
            ]
        )
    return _page("Lucy dev trace viewer", "".join(parts))


def create_viewer_app(settings: Optional[DevViewerSettings] = None) -> FastAPI:
    chosen = settings or DevViewerSettings()
    app = FastAPI(
        title="Lucy dev trace viewer",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        trace_file = chosen.trace_file
        if trace_file is None or not trace_file.exists():
            return _guidance_page(trace_file)
        summary = load_trace(trace_file)
        return _render_sessions(summary, trace_file)

    return app


def main() -> None:
    settings = DevViewerSettings()
    uvicorn.run(
        create_viewer_app(settings),
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
