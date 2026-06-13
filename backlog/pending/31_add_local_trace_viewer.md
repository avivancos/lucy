# 31 - Add lucy dev local trace viewer

**Sprint:** S3 - Conversational correctness
**Epic:** Observability
**Estimated effort:** ~5 h
**Depends on:** 24
**State:** pending

## Goal

Day-1 debugging gratification without the platform: a deliberately minimal
local viewer over the `LUCY_TRACE_FILE` JSONL traces - per-turn latency
waterfalls, cost per minute, transcript list, tool-call audit - scoped so it
never grows into a competing product (ADR 0010). This card delivers the S3
exit-demo surface: "traces visible in the `lucy dev` viewer".

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  URLs/ports/thresholds outside typed settings).
- `docs/adr/0010-open-core-split.md` - the boundary this card sits on. The
  "deliberately minimal local trace viewer" is explicitly OPEN; trace
  storage, search, and the hosted dashboard are explicitly CLOSED. The
  scope cap in this card is how we keep that line.
- `docs/adr/0003-no-mocks-testing-policy.md` - why the test fixture must be
  a recorded real trace, never hand-typed JSON.
- `docs/telemetry-wire-v1.md` - the event shapes the viewer parses
  (`session.started`, `session.ended`, `turn`, `span`, `cost`, `business`,
  `tool_call`, `transcript`, `audio_ref`) and the `LUCY_TRACE_FILE` env var
  that names the JSONL file.
- `src/lucy/observe.py` - today's module; card 24 replaces it with the
  `src/lucy/observe/` package whose `JsonlFileExporter` writes the exact
  file this viewer reads. Use its event models when you need a controlled
  trace in tests.
- `src/lucy/metrics.py` - `LatencyWaterfall` (stt_ms, rag_ms, llm_ms,
  mcp_tools_ms, tts_ms, transport_ms, total_ms) and `CostBreakdown`
  (total_cost, cost_per_minute); the waterfall and cost panels mirror these
  field names exactly.
- `src/lucy/serve/app.py` - the `create_app` factory (built in card 23, S1);
  `create_viewer_app` mirrors its factory style.
- `examples/quickstart_voice_agent.py` - the zero-key offline run (built in
  card 26, S1) that records the trace fixture for this card.
- `tests/test_api.py` - house FastAPI test style: plain pytest +
  `fastapi.testclient.TestClient`, no mocks.

## Spec

One new module, `src/lucy/serve/devviewer.py`. One FastAPI route serving one
static HTML page that reads the `LUCY_TRACE_FILE` JSONL and renders: per-turn
latency waterfalls, cost per minute, transcript list, tool-call audit.

Module docstring MUST contain the exact sentence "Scope cap (ADR 0010): no
storage, no auth, no cross-run comparisons, no audio." - this phrase is
asserted by test and grep.

Settings (typed, never hardcoded at call sites):

- `DevViewerSettings(BaseSettings)` with `env_prefix="LUCY_DEVVIEWER_"`:
  - `trace_file: Path | None = Field(default=None,
    validation_alias="LUCY_TRACE_FILE")` - same env var as the wire spec.
  - `host: str = "127.0.0.1"`
  - `port: int = 8642`

Trace loading (pure functions, no I/O besides reading the file):

- `load_trace(path: Path) -> TraceSummary` - reads the JSONL line by line:
  - Each well-formed line is a wire v1 event dict; route it by `type`.
  - Malformed JSON lines and non-dict lines are skipped and counted in
    `TraceSummary.skipped_lines`; loading never raises on bad lines.
  - Unknown `type` values are ignored (wire v1 is additive; the viewer must
    tolerate future event types).
  - `span`, `business`, and `audio_ref` events are ignored by this viewer
    (audio is out of scope; spans wait for the platform).
- `TraceSummary` dataclass: `sessions: list[SessionView]`,
  `skipped_lines: int`.
- `SessionView` dataclass: `session_id: str`, `agent_name: str | None`
  (from `session.started`), `turns: list[TurnView]` ordered by
  `turn_index`, `transcript: list[TranscriptLine]` ordered by
  `emitted_at_ms`, `tool_calls: list[ToolCallView]` ordered by
  `emitted_at_ms`, `total_cost: float` (sum of `total_cost` over `cost`
  events), `cost_per_minute: float | None` (`total_cost` divided by the
  sum of `billable_audio_minutes` over `cost` events; `None` when that sum
  is zero).
- `TurnView` dataclass: `turn_id: str`, `turn_index: int`,
  `waterfall: LatencyWaterfall` (parsed from the event's
  `latency_waterfall` dict), `interrupted: bool`.
- `TranscriptLine` dataclass: `turn_id: str`, `role: str` (caller/agent),
  `text: str`.
- `ToolCallView` dataclass: `turn_id: str`, `server: str`, `tool: str`,
  `allowed: bool`, `latency_ms: float`, `error: str | None`.

App factory and route:

- `create_viewer_app(settings: DevViewerSettings | None = None) -> FastAPI`
  (settings default to `DevViewerSettings()`).
- Exactly ONE route: `GET /` returning `HTMLResponse`. No JSON API, no
  websockets, no other routes. The route re-reads the trace file on every
  request so a refresh shows new turns from a running session.
- Page content, in order:
  - Title "Lucy dev trace viewer", the resolved trace file path, and a
    banner "N malformed lines skipped" when `skipped_lines > 0`.
  - Per session, four panels with stable element ids:
    - `id="waterfalls"`: one row per turn showing `turn_index`, the six
      segments (stt/rag/llm/mcp_tools/tts/transport) as inline-CSS bars
      with their ms values, the turn `total_ms`, and an "interrupted"
      marker when `interrupted` is true. Bar widths scale linearly to the
      largest `total_ms` in the session.
    - `id="costs"`: per-cost-event rows (`turn_id`, `total_cost`,
      `cost_per_minute`) plus the session `total_cost` and
      `cost_per_minute`.
    - `id="transcript"`: ordered lines rendered as `role: text`.
    - `id="tool-calls"`: a table with columns server, tool, allowed,
      latency_ms, error; when the session has no `tool_call` events the
      panel shows exactly "No tool calls recorded.".
- Missing trace file (env unset or path absent): HTTP 200 with a guidance
  page that names `LUCY_TRACE_FILE` and shows how to record a trace with
  the quickstart. Empty file: HTTP 200 with "No events yet.".
- All transcript text, tool arguments-derived strings, and error strings
  pass through `html.escape` before rendering.
- All CSS is inline in the page. No JavaScript, no external assets, no CDN
  links - the page must render offline, like the quickstart.

CLI entrypoint:

- `main() -> None`: builds `DevViewerSettings()`, calls
  `uvicorn.run(create_viewer_app(settings), host=settings.host,
  port=settings.port)`.
- `if __name__ == "__main__": main()` so
  `python -m lucy.serve.devviewer` serves the page. A `lucy dev` console
  script can come with the packaging milestone - NOT this card.

Test fixture:

- `tests/fixtures/quickstart_trace.jsonl` - recorded by actually running
  `examples/quickstart_voice_agent.py` with `LUCY_TRACE_FILE` pointed at
  the fixture path (real trace, not invented). Committed to the repo.
- Controlled traces for edge cases (tool calls, malformed lines, unknown
  types) are written in-test to `tmp_path` through the real
  `lucy.observe` event models and `JsonlFileExporter` - real code paths,
  never hand-typed JSON strings.

## Chips

- [ ] **C1 - Record the quickstart trace fixture.** Write
  `tests/test_devviewer.py` first with
  `test_quickstart_fixture_lines_are_wire_v1_events`: every non-empty line
  of `tests/fixtures/quickstart_trace.jsonl` parses as a JSON object with
  `type`, `event_id`, `session_id`, `emitted_at_ms`; the file contains at
  least one `session.started`, one `turn` (whose `latency_waterfall` has
  the six segment keys), two `transcript`, and one `cost` event. Then
  create `tests/fixtures/` and record the fixture:
  `LUCY_TRACE_FILE=tests/fixtures/quickstart_trace.jsonl
  .venv/bin/python examples/quickstart_voice_agent.py`. If the recorded
  trace lacks `turn` or `cost` events, that is an instrumentation gap from
  card 25 - stop per the failure protocol; do NOT hand-edit the fixture.
  Files: `tests/fixtures/quickstart_trace.jsonl`,
  `tests/test_devviewer.py`. Verify:
  `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> 1 test passes.
- [ ] **C2 - Settings and trace loader.** Tests first:
  `test_settings_read_trace_file_and_port_from_env` (set
  `LUCY_TRACE_FILE` and `LUCY_DEVVIEWER_PORT` via monkeypatch.setenv,
  assert both land in `DevViewerSettings`),
  `test_load_trace_builds_session_view_from_quickstart_fixture` (turns
  ordered by `turn_index`, transcript ordered by `emitted_at_ms`, session
  `total_cost` and `cost_per_minute` computed per spec),
  `test_load_trace_counts_and_skips_malformed_lines` and
  `test_load_trace_ignores_unknown_event_types` (controlled trace in
  `tmp_path` written through `JsonlFileExporter`, plus one appended garbage
  line for the malformed case). Implement `DevViewerSettings`,
  `TraceSummary`, `SessionView`, `TurnView`, `TranscriptLine`,
  `ToolCallView`, `load_trace` in `src/lucy/serve/devviewer.py`. Files:
  `src/lucy/serve/devviewer.py`, `tests/test_devviewer.py`. Verify:
  `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> all pass
  (>=5 tests).
- [ ] **C3 - Page route with waterfall and transcript panels.** Tests
  first: `test_index_page_renders_turn_waterfalls_from_fixture`
  (`TestClient(create_viewer_app(...)).get("/")` -> 200; body contains
  `id="waterfalls"`, each fixture turn's `total_ms`, and the six segment
  names) and `test_index_page_renders_transcript_in_order` (body contains
  `id="transcript"` and the fixture's caller/agent lines in
  `emitted_at_ms` order; assert escaping by index ordering of the
  rendered lines). Implement `create_viewer_app`, the single `GET /`
  route, and the waterfall + transcript panels with inline CSS. Files:
  `src/lucy/serve/devviewer.py`, `tests/test_devviewer.py`. Verify:
  `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> all pass
  (>=7 tests).
- [ ] **C4 - Cost and tool-audit panels, empty and missing states.** Tests
  first: `test_index_page_renders_session_cost_and_cost_per_minute`
  (body contains `id="costs"`, the session `total_cost` and
  `cost_per_minute` from the fixture),
  `test_tool_call_audit_renders_recorded_tool_events` (controlled trace
  with `tool_call` events written through `JsonlFileExporter`; body
  contains `id="tool-calls"` plus server, tool, allowed, latency columns),
  `test_tool_call_panel_shows_empty_state_without_tool_events` (fixture
  trace -> exact text "No tool calls recorded." when applicable, or the
  controlled no-tools trace), and
  `test_missing_trace_file_renders_guidance_page` (unset/absent path ->
  200, body names `LUCY_TRACE_FILE`). Implement both panels and the
  guidance/empty pages. Files: `src/lucy/serve/devviewer.py`,
  `tests/test_devviewer.py`. Verify:
  `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> all pass
  (>=11 tests).
- [ ] **C5 - CLI entrypoint and docstring scope cap.** Test first:
  `test_module_docstring_states_adr_0010_scope_cap` (the module docstring
  contains the exact sentence "Scope cap (ADR 0010): no storage, no auth,
  no cross-run comparisons, no audio."). Implement the docstring, `main()`
  and the `__main__` guard. Smoke-test the CLI (agents.md requires it):
  `LUCY_TRACE_FILE=tests/fixtures/quickstart_trace.jsonl
  .venv/bin/python -m lucy.serve.devviewer & sleep 2 &&
  curl -s http://127.0.0.1:8642/ | grep -c 'id="waterfalls"'; kill %1`.
  Files: `src/lucy/serve/devviewer.py`, `tests/test_devviewer.py`.
  Verify: the curl pipeline above -> prints `1`, and
  `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> all pass
  (>=12 tests).
- [ ] **C6 - Full suite + card bookkeeping.** Run the whole suite, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
  `docker compose run --rm lucy-api pytest` when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003). The fixture is a
  recorded real quickstart trace; controlled traces are written through the
  real `lucy.observe` event models and `JsonlFileExporter`. Never hand-type
  JSON event strings into tests or fixtures.
- Do not hardcode the trace file path, host, port, or URLs at call sites;
  they live only in `DevViewerSettings` typed settings (agents.md).
- Do not exceed the ADR 0010 scope cap, repeated here verbatim: no storage,
  no auth, no cross-run comparisons, no audio. Concretely: no database, no
  index, no persistence of parsed traces; no login, sessions, or API keys
  on the viewer; no aggregation or diffing across trace files or runs; no
  audio playback and no dereferencing of `audio_ref` events. Storage,
  search, and dashboards are the CLOSED platform plane.
- Do not add routes beyond `GET /`, JSON APIs, websockets, JavaScript, or
  external/CDN assets; the page renders offline.
- Do not add a `lucy dev` console script here; that belongs to the
  packaging milestone.
- Do not modify `src/lucy/observe/`, `src/lucy/serve/app.py`, or
  `src/lucy/metrics.py`; this card only adds `src/lucy/serve/devviewer.py`,
  `tests/test_devviewer.py`, and `tests/fixtures/quickstart_trace.jsonl`.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_devviewer.py -q` -> all pass
      (>=12 tests), fixture-driven render covers waterfall, cost,
      transcript, and tools panels
- [ ] `LUCY_TRACE_FILE=tests/fixtures/quickstart_trace.jsonl
      .venv/bin/python -m lucy.serve.devviewer & sleep 2 && curl -s
      http://127.0.0.1:8642/ | grep -o 'id="waterfalls"\|id="costs"\|
      id="transcript"\|id="tool-calls"' | sort -u | wc -l; kill %1` ->
      prints `4` (all four panels served by the real CLI)
- [ ] `grep -n "Scope cap (ADR 0010): no storage, no auth, no cross-run
      comparisons, no audio." src/lucy/serve/devviewer.py` -> exactly one
      match, inside the module docstring
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon is
      available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing (e.g. the quickstart trace lacks
`turn` or `cost` events, meaning card 25 instrumentation is incomplete), or
the spec turns out wrong: do NOT check boxes, do NOT force tests green, do
NOT hand-edit the fixture. Leave the card in `in_progress/`, document what
happened under "Improvements noted", and report. Partial honest work beats
fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
