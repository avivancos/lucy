# Decision 53 - Superseded analytics rollup scope

**Historical sprint:** S9 - Analytics & SRE observability (not executable)
**Epic:** Analytics
**Estimated effort:** ~6 h
**Depends on:** 24, 52
**State:** superseded by Card 103

## Decision

This original implementation card is no longer executable. Card 103 replaces
its incomplete package layout and owns the complete in-process
`analytics-model/v1` engine after Cards 98 and 99 established provider and talk
attribution. Cards 54 and 57 depend on Card 103.

The remaining text is retained as historical context only.

## Original goal

Turn the raw telemetry event stream into the analytical snapshots defined by
analytics-model-v1, entirely in-process and without storage. A `RollupAccumulator`
folds events into ephemeral `SessionRollup` and `RunRollup` snapshots - percentiles,
distributions, conversion, cost breakdown, tool success - so an operator (and the
local surface in card 54) gets real analytics with zero infrastructure, while
cross-run storage stays the closed platform's job (ADR 0010).

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - operating rules (English, no mocks, no hardcoded thresholds).
- `docs/analytics-model-v1.md` - the contract this engine implements: the fact
  grains, the measure names, the derived-metric formulas with their divide-by-zero
  (`null`) rule, and the `SessionRollup`/`RunRollup` snapshot keys. The code field
  names MUST match the spec exactly.
- `docs/adr/0010-open-core-split.md` - why this engine is open but storage and
  cross-run comparison are not. The scope cap below is how we hold that line.
- `src/lucy/observe/__init__.py` and `src/lucy/observe/events.py` - the telemetry
  event models produced by card 24 (`TelemetryEvent` union: `turn`, `cost`,
  `tool_call`, `transcript`, `session.started`, `session.ended`, `business`,
  `span`, `audio_ref`). The accumulator consumes these objects; tests build traces
  through these models and the `JsonlFileExporter`, never hand-typed JSON.
- `src/lucy/metrics.py` - `LatencyWaterfall`, `CostBreakdown`, `FunnelEvent`,
  `SentimentScore`; the rollup reuses these field names and may reuse
  `CostBreakdown` arithmetic when summing cost events.
- `src/lucy/serve/devviewer.py` (card 31) - the `load_trace` reducer that already
  routes wire events by `type`; mirror its tolerant routing (skip malformed, ignore
  unknown types) and its grep-asserted scope-cap docstring style.

## Spec

One package `src/lucy/analytics/` with `__init__.py` (re-exports the public names)
and `rollup.py` (the engine). Pure Python, stdlib `statistics` for percentiles; no
numpy, no I/O, no persistence.

Module docstring of `rollup.py` MUST contain the exact sentence
"Scope cap (ADR 0010): no storage, no cross-run comparisons, no cross-tenant
aggregation." - asserted by test and grep.

### Data structures (field names match analytics-model-v1)

- `SessionRollup` dataclass: `session_id: str`, `agent_name: str | None`,
  `turn_count: int`, `tool_call_count: int`, `tool_error_count: int`,
  `barge_in_count: int`, `deadline_miss_count: int`,
  `cost: CostBreakdown` (summed over the session's `cost` events),
  `billable_audio_minutes: float`,
  `latency_p50/p95/p99: LatencyPercentiles` (per-segment + total),
  `funnel_distribution: dict[str, int]` (stage -> count),
  `sentiment_distribution: dict[str, int]` (label -> count),
  and the derived metrics `cost_per_minute: float | None`,
  `conversion_rate: float | None`, `deadline_miss_rate: float | None`,
  `barge_in_rate: float | None`, `tool_success_rate: float | None`.
- `RunRollup` dataclass: `schema_version: str = "analytics-model/v1"`,
  `session_count: int`, the same aggregate counts/cost/latency/distributions
  summed across all sessions, plus `cost_per_booked_outcome: float | None`.
  `RunRollup.to_dict()` returns exactly the JSON shape in the spec's example.
- `LatencyPercentiles` dataclass: one field per segment (`stt_ms`, `rag_ms`,
  `llm_ms`, `mcp_tools_ms`, `tts_ms`, `transport_ms`, `total_ms`).

### Engine

- `RollupAccumulator` class:
  - `add(event: TelemetryEvent) -> None` - routes by event type, accumulating raw
    per-session state. Unknown/`span`/`audio_ref` events are ignored; malformed
    inputs (missing required fields) are counted in `dropped_events`, never raise.
  - `add_all(events: Iterable[TelemetryEvent]) -> None` - convenience loop.
  - `session_rollup(session_id: str) -> SessionRollup` - finalize one session.
  - `run_rollup() -> RunRollup` - finalize across all seen sessions.
  - `dropped_events: int` - counter, surfaced for observability.
- Percentiles use `statistics.quantiles`/linear interpolation deterministically;
  with a single sample, p50=p95=p99=that sample; with zero samples, segment value
  is `0.0`.
- Every derived metric returns `None` when its denominator is zero (spec rule).
- A pure helper `percentiles(values: list[float]) -> LatencyPercentiles`-style
  function is unit-tested independently for determinism.

### No-storage guarantee

The accumulator holds only in-memory aggregates for the current process; it exposes
no load/save, no file paths, no database handles, and no method that reads a second
run. A test asserts the public surface contains no `load`, `save`, `persist`,
`store`, or `connect` names.

## Files to create/modify

- `src/lucy/analytics/__init__.py` - public re-exports.
- `src/lucy/analytics/rollup.py` - dataclasses, `percentiles`, `RollupAccumulator`.
- `tests/test_analytics_rollup.py` - the tests below.

## Chips

- [ ] **C1 - Percentiles + dataclasses (pure, deterministic).** Tests first:
  `test_percentiles_are_deterministic_for_known_sample` (a fixed list -> exact
  p50/p95/p99), `test_percentiles_handle_single_and_empty_samples`. Implement
  `LatencyPercentiles`, `percentiles`, and the rollup dataclasses (no engine yet).
  Files: `src/lucy/analytics/rollup.py`, `tests/test_analytics_rollup.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_rollup.py -q -k percentile` ->
  pass.
- [ ] **C2 - Accumulator over a recorded trace.** Test first:
  `test_session_rollup_from_recorded_trace` - build a trace through the card 24
  event models + `JsonlFileExporter`, read it back, feed the accumulator, and
  assert `turn_count`, summed `cost`, `cost_per_minute`, and the funnel/sentiment
  distributions match the known inputs. Implement `RollupAccumulator.add`,
  `add_all`, `session_rollup`. Files: `src/lucy/analytics/rollup.py`,
  `tests/test_analytics_rollup.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_rollup.py -q` -> all pass (>=4).
- [ ] **C3 - Run rollup, derived-metric nulls, dropped counter, scope cap.** Tests
  first: `test_run_rollup_aggregates_sessions_and_matches_spec_example` (compare
  `RunRollup.to_dict()` against the worked example payload in
  `docs/analytics-model-v1.md`), `test_derived_metrics_are_none_on_zero_denominator`,
  `test_malformed_events_are_counted_not_raised`,
  `test_public_surface_has_no_storage_names`, and
  `test_module_docstring_states_adr_0010_scope_cap`. Implement `run_rollup`,
  `to_dict`, the divide-by-zero rule, the `dropped_events` counter, and the
  docstring. Files: `src/lucy/analytics/rollup.py`,
  `src/lucy/analytics/__init__.py`, `tests/test_analytics_rollup.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_rollup.py -q` -> all pass (>=9).
- [ ] **C4 - Full suite + card bookkeeping.** Run the whole suite, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
  `docker compose run --rm lucy-api pytest` when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003). Test traces are written
  through the real card 24 event models and `JsonlFileExporter`; never hand-type
  JSON event strings.
- Do not hardcode percentile thresholds, segment names, or cost field names at call
  sites - reuse the `LatencyWaterfall`/`CostBreakdown` fields from
  `src/lucy/metrics.py` and keep the schema-version string in one named constant.
- Do not add storage, persistence, file/database I/O, or any cross-run or
  cross-tenant aggregation; this engine is in-process only (ADR 0010 scope cap).
- Do not import platform or Pili code; `lucy.analytics` is pure open SDK.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_analytics_rollup.py -q` -> all pass
      (>=9 tests), including the run-rollup match against the spec example payload
- [ ] `grep -n "Scope cap (ADR 0010): no storage, no cross-run comparisons, no cross-tenant aggregation." src/lucy/analytics/rollup.py`
      -> exactly one match, inside the module docstring
- [ ] `.venv/bin/python -c "import lucy.analytics as a; print(sorted(n for n in dir(a) if any(k in n.lower() for k in ('load','save','persist','store','connect'))))"`
      -> prints `[]` (no storage surface)
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, the card 24 event models are missing a field the rollup needs, or
the spec example does not round-trip: do NOT check boxes, do NOT force tests green,
do NOT hand-edit the spec example. Leave the card in `in_progress/`, document the
gap under "Improvements noted" (it may be a card 52 or card 24 follow-up), and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
