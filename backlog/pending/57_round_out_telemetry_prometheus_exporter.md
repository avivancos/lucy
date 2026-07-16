# 57 - Round out call-telemetry and add a Prometheus exporter for lucy.observe

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Observability
**Estimated effort:** ~5 h
**Depends on:** 24, 25, 103
**State:** pending

## Goal

Bridge the two observability planes and close the last call-telemetry gaps: a
`PrometheusExporter` that implements card 24's `TraceExporter` protocol so business
telemetry events also drive the SRE metric registry, plus the instrumentation
completeness card 103's rollups need (a full `LatencyWaterfall` on every turn,
deadline-miss and barge-in signals, and the export-drop counter surfaced as an
event). After this card, one event stream feeds the local viewer, the analytics
rollups, and the Prometheus scrape with no divergence.

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - operating rules (English, no mocks, no hardcoded thresholds).
- `docs/telemetry-wire-v1.md` - the event shapes; this card adds no new event types,
  it ensures the existing ones are fully populated.
- `src/lucy/observe/exporters.py` and `src/lucy/observe/__init__.py` (card 24) - the
  `TraceExporter` protocol, the existing `ConsoleExporter`/`JsonlFileExporter`/
  `OtlpBridgeExporter`, the exporter entry-point group `lucy.exporters`, and the
  bounded queue + drop counter. The new exporter slots in beside the others.
- `src/lucy/serve/prometheus.py` (card 55) - the registry and `record_*` helpers the
  exporter calls; reuse them, do not define a second registry.
- `src/lucy/runtime.py` (instrumented in card 25) - where turn spans, deadline
  misses, and fallbacks originate; this card asserts each turn emits a complete
  waterfall and a deadline/barge-in signal when applicable.
- `src/lucy/analytics.py` (card 103) - the exact event fields the rollup
  consumes; the conformance test below asserts a recorded quickstart trace carries
  them all.
- `tests/test_observability.py` - house style for telemetry tests; recorded events
  through the real models, no mocks.

## Spec

### `PrometheusExporter` (`src/lucy/observe/exporters.py`)

- A class implementing the card 24 `TraceExporter` protocol
  (`export(event) -> None`, `close() -> None`) constructed with the card 55 registry
  (default: the shared module registry; injectable for tests, never a global
  singleton hardcoded at call sites).
- On each event it increments the matching card 55 instrument via the existing
  `record_*` helpers: `turn` -> `lucy_turn_duration_seconds` + `lucy_turns_total`;
  `tool_call` -> `lucy_tool_calls_total{status}`; `cost` -> no-op (cost is business,
  not a service metric); provider-timeout/fallback fields on `turn`/`span` ->
  `lucy_provider_timeouts_total`/`lucy_provider_fallbacks_total`. Unknown event types
  are ignored, never raised.
- Registered under the `lucy.exporters` entry-point group as
  `prometheus = lucy.observe.exporters:PrometheusExporter`, so it is discoverable
  like the other exporters.

### Instrumentation completeness (card 25 round-out, in `src/lucy/runtime.py`)

- Every emitted `turn` event carries a `latency_waterfall` with all six segments
  populated (zero is allowed, missing is not).
- A turn that exceeds its deadline emits a `deadline_miss` flag (and the
  corresponding `lucy_provider_timeouts_total`/`lucy_turns_total{outcome="fallback"}`
  increment through the exporter).
- A barged-in turn carries `interrupted: true`.
- When the card 24 export queue drops an event, a single `business` event of kind
  `exporter_drop` is emitted (rate-limited so a storm of drops does not amplify),
  letting the drop be visible in the trace as well as the gauge.

### Conformance test for the analytics rollup

`tests/test_observe_prometheus.py`:

- `test_prometheus_exporter_increments_service_metrics` - feed recorded events
  (built through card 24 models) to a `PrometheusExporter` over a fresh registry,
  scrape, and assert `lucy_turns_total` and `lucy_tool_calls_total` moved.
- `test_prometheus_exporter_is_discoverable_via_entry_point` - the `lucy.exporters`
  entry points include `prometheus` resolving to the class.
- `test_every_turn_emits_complete_latency_waterfall` - run the quickstart
  (`examples/quickstart_voice_agent.py`) to a recorded trace and assert each `turn`
  event has all six waterfall segments.
- `test_quickstart_trace_satisfies_rollup_required_fields` - feed that trace to
  card 103's `RollupAccumulator` and assert `dropped_events == 0` and a non-empty
  `RunRollup` (the rollup's required fields are all present in real traces).
- `test_exporter_drop_emits_business_event` - drive the card 24 queue to drop and
  assert exactly one `exporter_drop` business event is recorded.

## Files to create/modify

- `src/lucy/observe/exporters.py` - `PrometheusExporter`.
- `src/lucy/observe/__init__.py` - export the class; register the entry point.
- `pyproject.toml` - add the `lucy.exporters` `prometheus` entry point.
- `src/lucy/runtime.py` - complete the waterfall, deadline-miss, barge-in, and
  drop-event emission (card 25 round-out).
- `tests/test_observe_prometheus.py` - the tests above.

## Chips

- [ ] **C1 - PrometheusExporter + discovery.** Tests first:
  `test_prometheus_exporter_increments_service_metrics` and
  `test_prometheus_exporter_is_discoverable_via_entry_point`. Implement the exporter
  against the card 55 registry/helpers and register the entry point. Files:
  `src/lucy/observe/exporters.py`, `src/lucy/observe/__init__.py`, `pyproject.toml`,
  `tests/test_observe_prometheus.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observe_prometheus.py -q -k exporter` ->
  pass.
- [ ] **C2 - Waterfall + deadline + barge-in completeness.** Tests first:
  `test_every_turn_emits_complete_latency_waterfall` and a deadline-miss/barge-in
  assertion over a recorded trace. Complete the emission in `src/lucy/runtime.py`.
  Files: `src/lucy/runtime.py`, `tests/test_observe_prometheus.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observe_prometheus.py -q` -> all pass
  (>=3).
- [ ] **C3 - Drop event + rollup conformance.** Tests first:
  `test_exporter_drop_emits_business_event` and
  `test_quickstart_trace_satisfies_rollup_required_fields` (feed the real quickstart
  trace to card 103's accumulator; `dropped_events == 0`, non-empty `RunRollup`).
  Implement the rate-limited `exporter_drop` business event. Files:
  `src/lucy/observe/__init__.py`, `src/lucy/runtime.py`,
  `tests/test_observe_prometheus.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observe_prometheus.py -q` -> all pass
  (>=5).
- [ ] **C4 - Full suite + bookkeeping.** Run the whole suite, fill "Improvements
  noted", move this card to `done/`. Verify: `.venv/bin/python -m pytest -q` ->
  full suite green (use Docker Compose `docker compose run --rm lucy-api pytest`
  when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); exercise the real card 24
  exporter protocol, the real card 55 registry, and recorded quickstart traces.
  Never hand-type telemetry JSON.
- Do not hardcode metric names, the registry, or the drop rate-limit window at call
  sites; reuse card 55's helpers and keep tunables in named constants or typed
  settings.
- Do not define a second metric registry or duplicate card 55's instruments; this
  exporter feeds the existing one.
- Do not add new wire event types; only populate existing ones and the one
  `business`/`exporter_drop` signal already permitted by the wire spec.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_observe_prometheus.py -q` -> all pass
      (>=5 tests), covering the exporter, discovery, waterfall completeness, the
      drop event, and rollup conformance
- [ ] `.venv/bin/python -c "from importlib.metadata import entry_points; print('prometheus' in {e.name for e in entry_points(group='lucy.exporters')})"`
      -> prints `True`
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, the quickstart trace is missing a waterfall segment (a real card 25
gap), or card 55's helpers are not importable: do NOT check boxes, do NOT force
tests green, do NOT hand-edit the recorded trace. Leave the card in `in_progress/`,
document the gap under "Improvements noted" (likely a card 25 follow-up), and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
