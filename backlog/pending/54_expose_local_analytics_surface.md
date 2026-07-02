# 54 - Expose the runtime-local analytics surface

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Analytics
**Estimated effort:** ~4 h
**Depends on:** 53, 31
**State:** pending

## Goal

Make the rollup snapshots from card 53 visible without any platform: a JSON API on
the serve app (`GET /analytics`, `GET /analytics/sessions/{id}`) and an analytics
panel in the local dev trace viewer. Everything recomputes per request from the
in-process accumulator or the trace file and persists nothing, holding the ADR 0010
scope cap that card 53 established.

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - operating rules (English, no mocks, no hardcoded routes/ports
  outside typed settings).
- `docs/adr/0010-open-core-split.md` - the boundary: a runtime-local read of the
  current process's rollup is open; stored history, search, and cross-run views are
  closed. This surface computes on the fly and stores nothing.
- `src/lucy/analytics/rollup.py` (card 53) - `RollupAccumulator`, `RunRollup`,
  `SessionRollup`, and `RunRollup.to_dict()` (the analytics-model-v1 JSON shape the
  API returns verbatim).
- `src/lucy/serve/app.py` (card 23) - the `create_app` FastAPI factory and how
  existing routes (`/health`, `/metrics`, `/sessions`) are registered; the new
  routes mirror that style and reuse the app's existing accumulator/event source.
- `src/lucy/serve/devviewer.py` (card 31) - `load_trace`, `SessionView`, the panel
  rendering pattern (`id="waterfalls"`, `id="costs"`, etc.) and the grep-asserted
  scope-cap docstring. The analytics panel adds an `id="analytics"` block in the
  same offline, inline-CSS, no-JavaScript style.
- `tests/test_api.py` and `tests/test_devviewer.py` - the house test style: plain
  pytest + `fastapi.testclient.TestClient`, recorded fixtures, no mocks.

## Spec

### API routes on the serve app (`src/lucy/serve/app.py`)

The app already owns the live telemetry event source feeding a `RollupAccumulator`
(wire it in `create_app` if not already present, sourced from the same in-process
event stream the `/metrics` route reads). Add exactly two read-only routes:

- `GET /analytics` -> `200` JSON, the body of `RollupAccumulator.run_rollup().to_dict()`
  for the current process. Empty process (no events seen) returns the valid
  zero-state `RunRollup` (`session_count == 0`, counts `0`, derived metrics `null`),
  never an error.
- `GET /analytics/sessions/{session_id}` -> `200` JSON of the matching
  `SessionRollup`; unknown id -> `404` with `{"detail": "unknown session"}`.

No write routes, no query parameters that filter across runs, no pagination over
stored history (there is no stored history). The response `schema_version` is
`"analytics-model/v1"`.

### Dev viewer panel (`src/lucy/serve/devviewer.py`)

Extend the existing `GET /` page (card 31 reads `LUCY_TRACE_FILE`) with one panel:

- Build a `RollupAccumulator` from the same parsed trace events and render an
  `id="analytics"` block showing: per-segment latency `p50/p95/p99`, the funnel and
  sentiment distributions, the cost breakdown by component, and the run derived
  metrics (`cost_per_minute`, `conversion_rate`, `tool_success_rate`). Inline CSS,
  no JavaScript, no external assets - identical constraints to card 31.
- The panel re-reads/recomputes on every request (the route already re-reads the
  file), so a refresh reflects new turns. It stores nothing.
- All rendered text passes through `html.escape`.

The viewer's existing scope-cap docstring stays intact; this card does not loosen
it (no storage, no auth, no cross-run comparisons, no audio).

## Files to create/modify

- `src/lucy/serve/app.py` - the two `/analytics` routes (and wiring the
  accumulator if absent).
- `src/lucy/serve/devviewer.py` - the `id="analytics"` panel.
- `tests/test_analytics_surface.py` - the tests below.

## Chips

- [ ] **C1 - `/analytics` run-rollup route.** Test first:
  `test_analytics_route_returns_run_rollup_for_recorded_session` - drive the app
  with a recorded set of telemetry events (through card 24 models), then
  `TestClient(create_app()).get("/analytics")` -> 200 and body equals the
  accumulator's `run_rollup().to_dict()`; also
  `test_analytics_route_returns_zero_state_when_empty` -> 200, `session_count == 0`.
  Implement the route. Files: `src/lucy/serve/app.py`,
  `tests/test_analytics_surface.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_surface.py -q -k analytics_route`
  -> pass.
- [ ] **C2 - per-session route + 404.** Tests first:
  `test_session_analytics_route_returns_session_rollup` and
  `test_unknown_session_returns_404`. Implement
  `GET /analytics/sessions/{session_id}`. Files: `src/lucy/serve/app.py`,
  `tests/test_analytics_surface.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_surface.py -q` -> all pass (>=4).
- [ ] **C3 - dev viewer analytics panel.** Test first:
  `test_devviewer_renders_analytics_panel_from_trace` - point `LUCY_TRACE_FILE` at a
  recorded fixture, `TestClient(create_viewer_app()).get("/")` -> 200, body contains
  `id="analytics"`, the percentile values, and the funnel/sentiment distribution
  labels. Implement the panel reusing `RollupAccumulator`. Files:
  `src/lucy/serve/devviewer.py`, `tests/test_analytics_surface.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_surface.py -q` -> all pass (>=5).
- [ ] **C4 - Smoke test + full suite + bookkeeping.** Smoke the API (agents.md):
  start the app, `curl -s localhost:8000/analytics | python -c "import sys,json;
  print(json.load(sys.stdin)['schema_version'])"` -> prints `analytics-model/v1`.
  Run the whole suite, fill "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
  `docker compose run --rm lucy-api pytest` when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); drive the app and the viewer
  with recorded telemetry built through the card 24 event models, never hand-typed
  JSON or a stubbed accumulator.
- Do not hardcode routes, host, or port at call sites; reuse the serve app's typed
  settings and the existing `DevViewerSettings`. No magic strings for the schema
  version - reuse card 53's named constant.
- Do not add storage, history, search, pagination over past runs, cross-run
  comparison, or auth to either surface (ADR 0010 scope cap); both recompute and
  persist nothing.
- Do not add JavaScript, external/CDN assets, or extra viewer routes; the page
  stays offline and single-route like card 31.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_analytics_surface.py -q` -> all pass
      (>=5 tests), covering `/analytics`, the zero state, the per-session route, the
      404, and the viewer panel
- [ ] `curl -s localhost:8000/analytics | python -c "import sys,json; print(json.load(sys.stdin)['schema_version'])"`
      -> prints `analytics-model/v1` against the running app
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, card 53's `to_dict()` shape does not match what the route returns,
or the viewer panel cannot reuse the accumulator cleanly: do NOT check boxes, do
NOT force tests green. Leave the card in `in_progress/`, document the mismatch under
"Improvements noted", and report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
