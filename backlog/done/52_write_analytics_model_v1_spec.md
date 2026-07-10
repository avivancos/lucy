# 52 - Write the analytics model v1 spec and ADR 0013

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Analytics
**Estimated effort:** ~4 h
**Depends on:** 19, 24
**State:** done

## Goal

Publish the normative analytical *semantic layer* for Lucy: one unified model of
facts, dimensions, and measures over the existing telemetry events, plus an ADR
recording where analytics is open (the model + runtime-local rollups) and where it
is closed (storage, cross-run, BI). This is the analytics analog of the telemetry
wire spec and the single contract that the open rollup engine (card 53) and the
closed warehouse (card 58, platform) both implement, so the two never drift.

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  thresholds outside typed settings or named constants).
- `docs/adr/0010-open-core-split.md` - the boundary this card encodes: anything
  that runs in-process is open; anything that stores/aggregates/compares across
  runs or tenants is closed. The analytics MODEL and runtime-local rollups are
  open; storage, cross-run, and BI are closed.
- `docs/telemetry-wire-v1.md` - the event source of truth. The analytics model is
  a derived view over these same events (`session.started`, `session.ended`,
  `turn`, `span`, `cost`, `business`, `tool_call`, `transcript`, `audio_ref`);
  this card adds NO new event types.
- `src/lucy/metrics.py` - `CostBreakdown`, `LatencyWaterfall`, `SentimentScore`,
  `FunnelEvent`. The measures in the model reuse these field names verbatim;
  never invent parallel cost/latency vocabularies.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - defines turn/session grain
  (the per-turn cognition plane), which the fact grain mirrors.
- `tests/test_product_docs.py` - the house pattern for asserting a normative doc's
  required sections and absence of `etc.`; model the model-spec test on it.

## Spec

Two documents and one test. No source code in this card.

### Document 1 - `docs/adr/0013-analytics-and-sre-observability.md`

Standard ADR shape (Status / Context / Decision / Consequences), mirroring
`0010`/`0011`. The Decision section MUST state, in these words, the split:

- Open: the analytics semantic model (this card), the in-process rollup engine
  (`lucy.analytics`, card 53), and the runtime-local analytics surface (card 54).
- Open (SRE seam): service RED/USE metrics exposed in-process (card 55) and the
  self-host ops definitions (card 56).
- Closed: trace/event storage, the analytics warehouse and ETL, the cross-run
  query/semantic API, and hosted BI dashboards (platform cards 58-60).

Consequences MUST note that the model is the frozen contract between open rollups
and the closed warehouse, governed by SemVer like the wire spec and the spec ABI.

### Document 2 - `docs/analytics-model-v1.md`

Normative spec. Required `##` sections, in order, each non-empty:

1. `## Status` - "Normative. Version 1." plus the SemVer rule (additive minor,
   breaking major), matching `telemetry-wire-v1.md`.
2. `## Scope and boundary` - restates the ADR 0010 open/closed line for analytics
   and the explicit cap: the open model defines no persistence and no cross-run or
   cross-tenant aggregation; those are warehouse concerns.
3. `## Facts` - three fact grains, each with its key and source event(s):
   - `turn_fact` (grain: one row per `turn` event; key `turn_id`).
   - `session_fact` (grain: one row per session; key `session_id`; sourced from
     `session.started`/`session.ended` and aggregated `cost` events).
   - `tool_call_fact` (grain: one row per `tool_call` event; key
     `(turn_id, server, tool, emitted_at_ms)`).
4. `## Dimensions` - enumerate every dimension with its source field and
   cardinality note: `agent`, `deployment`, `provider_stt`, `provider_llm`,
   `provider_tts`, `funnel_stage` (the `FunnelEvent` stages: qualified, interested,
   objection, booked, escalation, failed_booking), `sentiment_label` (positive,
   neutral, negative), `project`/`tenant`, and `time` (derived from
   `emitted_at_ms`). No `etc.` - list them all.
5. `## Measures` - enumerate every measure with its exact source field, reusing
   `src/lucy/metrics.py` names: the seven `CostBreakdown` components
   (`stt_cost`, `llm_cost`, `tts_cost`, `telephony_cost`, `rag_cost`,
   `mcp_tool_cost`, `infra_cost`), `total_cost`, `billable_audio_minutes`, the six
   `LatencyWaterfall` segments (`stt_ms`, `rag_ms`, `llm_ms`, `mcp_tools_ms`,
   `tts_ms`, `transport_ms`) plus `total_ms`, and the counts (`turn_count`,
   `session_count`, `tool_call_count`, `tool_error_count`, `barge_in_count`,
   `deadline_miss_count`).
6. `## Derived metrics` - exact formulas for: `cost_per_minute`
   (`total_cost / billable_audio_minutes`), `cost_per_booked_outcome`
   (`total_cost / count(funnel_stage == booked)`), `conversion_rate`
   (`count(booked) / session_count`), latency `p50`/`p95`/`p99` per segment and
   total, `deadline_miss_rate` (`deadline_miss_count / turn_count`),
   `barge_in_rate` (`barge_in_count / turn_count`), and `tool_success_rate`
   (`1 - tool_error_count / tool_call_count`). Define the divide-by-zero rule:
   each derived metric is `null` when its denominator is zero.
7. `## Rollup snapshot schema` - the stable JSON shape both the open engine and the
   closed warehouse emit/consume. Specify the exact keys for `SessionRollup` and
   `RunRollup` (the dataclasses card 53 builds): `schema_version` (the string
   `"analytics-model/v1"`), the dimension values present, every measure above, and
   the derived-metric block. Include one worked `RunRollup` example payload with
   real numbers that round-trips against card 53's dataclasses.
8. `## Conformance` - the rule that any producer/consumer claiming
   `analytics-model/v1` MUST populate every measure key (absent counts are `0`,
   absent derived metrics are `null`) and MUST NOT add persistence or cross-run
   fields under this version.

The phrase `etc.` MUST NOT appear in either document (enumerate everything).

### Test - `tests/test_analytics_model.py`

Pure-doc contract test, no source dependency on card 53 yet (assert against the
literal example in the markdown):

- `test_adr_0013_states_open_closed_split` - the ADR file exists and its Decision
  section names both `lucy.analytics` (open) and the warehouse (closed).
- `test_analytics_model_has_all_required_sections` - all eight `##` sections above
  are present and non-empty, in order.
- `test_analytics_model_enumerates_all_metric_fields` - every `CostBreakdown` and
  `LatencyWaterfall` field name (imported from `src/lucy/metrics.py`, not
  hardcoded) appears in the Measures section.
- `test_analytics_model_forbids_etc` - the substring `etc.` appears zero times in
  both documents.
- `test_rollup_example_payload_is_valid_json` - the fenced ```json example in the
  Rollup snapshot section parses and carries `schema_version ==
  "analytics-model/v1"` and a non-empty measures block.

## Files to create/modify

- `docs/adr/0013-analytics-and-sre-observability.md` - the decision.
- `docs/analytics-model-v1.md` - the normative model.
- `tests/test_analytics_model.py` - the contract test.

## Chips

- [x] **C1 - ADR 0013 + its test.** Write `test_adr_0013_states_open_closed_split`
  first (red), then author `docs/adr/0013-analytics-and-sre-observability.md` with
  Status/Context/Decision/Consequences and the open/closed split worded as in the
  Spec. Files: `docs/adr/0013-analytics-and-sre-observability.md`,
  `tests/test_analytics_model.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_model.py -q -k adr` -> the ADR
  test passes.
- [x] **C2 - Model sections, dimensions, measures + their tests.** Write
  `test_analytics_model_has_all_required_sections`,
  `test_analytics_model_enumerates_all_metric_fields`, and
  `test_analytics_model_forbids_etc` first (red), reading the field names from
  `lucy.metrics` so they are not hardcoded. Then author sections 1-6 of
  `docs/analytics-model-v1.md`. Files: `docs/analytics-model-v1.md`,
  `tests/test_analytics_model.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_model.py -q` -> all pass (>=4).
- [x] **C3 - Rollup snapshot schema + worked example + test.** Write
  `test_rollup_example_payload_is_valid_json` first (red), then author sections 7-8
  (the `SessionRollup`/`RunRollup` keys and the worked ```json example whose keys
  match the dataclass field names card 53 will define). Files:
  `docs/analytics-model-v1.md`, `tests/test_analytics_model.py`. Verify:
  `.venv/bin/python -m pytest tests/test_analytics_model.py -q` -> all pass (>=5).
- [x] **C4 - Full suite + card bookkeeping.** Run the whole suite, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
  `docker compose run --rm lucy-api pytest` when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); the model test asserts
  against the real markdown documents and field names imported from
  `lucy.metrics`, never hand-typed copies of those names.
- Do not hardcode cost/latency field names in the test - import them from
  `src/lucy/metrics.py` so the spec and the code cannot drift; thresholds and
  version strings live in named constants, not scattered literals.
- Do not define any storage, persistence, cross-run, or cross-tenant field in the
  v1 model; those are the closed warehouse's job (ADR 0010).
- Do not add new telemetry event types; the model is a derived view over the
  existing wire events only.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_analytics_model.py -q` -> all pass
      (>=5 tests), covering ADR split, sections, metric-field enumeration, no
      `etc.`, and the valid example payload
- [x] `grep -c "etc\." docs/analytics-model-v1.md docs/adr/0013-analytics-and-sre-observability.md`
      -> prints `0` for both files
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [x] Post-task audit done; no additional follow-up required

## Failure protocol

If a test fails, a referenced field no longer exists in `lucy.metrics`, or the
spec turns out wrong: do NOT check boxes, do NOT force tests green. Leave the card
in `in_progress/`, document what happened under "Improvements noted", and report.
Partial honest work beats fake completion.

## Improvements noted

- `analytics-model/v1` now gives the open rollup engine and closed platform one
  tested vocabulary for three facts, ten dimensions, costs, latency, counts,
  percentiles, and derived rates.
- The model explicitly keeps unknown provider attribution null and points to
  typed per-component attribution, aligning with follow-up card 98.
- Final gates: 5 model tests and 474 full-suite tests pass; Docker ruff, format,
  and mypy are clean; both forbidden-phrase counts are zero.

## Review evidence

- code-reviewer: PASS - grains, source events, keys, formulas, and open/closed
  ownership match ADRs 0010/0011 with no conflicting runtime contract.
- test-auditor: PASS - section order, dynamic metric fields, JSON example, ADR
  split, and forbidden shorthand have executable coverage.
- docs-reviewer: PASS - ADR 0013 and the normative model use stable identifiers,
  exact formulas, complete dimensions, and SemVer rules.
- simplicity-reviewer: PASS - one model document and one focused contract test;
  no premature storage or source abstraction.
- security-reviewer: NOT_APPLICABLE - semantic docs add no secrets, telemetry
  transport, or executable permission surface.

Findings disposition:

- None.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
