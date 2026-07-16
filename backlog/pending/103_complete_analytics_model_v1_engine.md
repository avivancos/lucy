# 103 - Complete the in-process analytics-model/v1 engine

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Analytics
**Estimated effort:** ~10 h
**Depends on:** 52, 98, 99; replaces superseded Decision 53
**State:** pending

## Goal

Complete Lucy's open, runtime-local `analytics-model/v1` engine so a caller can
turn real telemetry events into typed turn/session/tool facts and exact
`SessionRollup` and `RunRollup` snapshots with every v1 dimension, measure,
percentile, and derived metric. The engine remains ephemeral and client-side:
it must not persist events or aggregate across tenants or stored runs.

## Context primer

Read these files in order before changing code or tests:

- `agents.md` - English documentation, TDD, Docker verification, typed config,
  and the no-mocks/open-core rules.
- `docs/analytics-model-v1.md` - normative fact grains, dimensions, all measure
  keys, snapshot JSON shape, percentile definition, formulas, and null rules.
- `docs/adr/0010-open-core-split.md` - the boundary that keeps this engine
  in-process and leaves persistence, warehouse, and cross-tenant work closed.
- `src/lucy/analytics.py` - the existing Card 99 `TalkMeasures` model and
  `aggregate_talk_measures` behavior that must remain compatible.
- `src/lucy/observe/events.py` - typed telemetry event source models and the
  exact fields available for turn, session, cost, business, and tool facts.
- `src/lucy/metrics.py` - `CostBreakdown` and `LatencyWaterfall` field names and
  totals; reuse them instead of inventing parallel vocabularies.
- `tests/test_analytics_model.py` - existing Card 99 red/green coverage and
  fixtures for measured talk duration.

Card 99 is the prerequisite for measured `caller_talk_ms`, `agent_talk_ms`, and
`talk_ratio`. Decision 53 preserves the superseded earlier scope; this card is
the only executable completion scope and must preserve
the normative names in `docs/analytics-model-v1.md`.

## Spec

Implement the open surface in `src/lucy/analytics.py` and its focused tests in
`tests/test_analytics_engine.py`. Keep the existing public `TalkMeasures` and
`aggregate_talk_measures` API behavior intact.

1. Define typed immutable-or-validation-backed models for the three fact grains:
   `TurnFact` keyed by `turn_id`, `SessionFact` keyed by `session_id`, and
   `ToolCallFact` keyed by `(turn_id, server, tool, emitted_at_ms)`. Preserve
   source timestamps, project/deployment tags, agent identity, interruption and
   timeout state, latency waterfall values, measured talk durations, cost
   attribution, business values, tool permission/latency/error values, and
   provider attribution. Reject invalid typed values; do not silently invent
   provider, tenant, or business values.
2. Provide a deterministic in-process event-to-fact boundary accepting an
   iterable of typed telemetry events. It must group only by the supplied
   session/run input, sum every `CostBreakdown` component across cost events,
   retain the latest business dimensions by `emitted_at_ms`, count one turn per
   turn event, one tool call per tool event, tool errors where `error` is
   non-null, barge-ins where `interrupted` is true, and deadline misses once per
   `timeout_events` entry. Malformed or unrelated events must be counted in a
   surfaced `dropped_events` counter rather than raised or fabricated.
3. Populate every v1 dimension exactly: `agent`, `deployment`,
   `provider_stt`, `provider_llm`, `provider_tts`, `funnel_stage`,
   `sentiment_label`, `project`, `tenant`, and UTC `time`. Provider dimensions
   come only from typed per-component provider attribution and otherwise remain
   null; local rollups always leave `tenant` null. Do not infer dimensions from
   prices, URLs, class names, or arbitrary unregistered strings.
4. Populate every v1 measure exactly: `stt_cost`, `llm_cost`, `tts_cost`,
   `telephony_cost`, `rag_cost`, `mcp_tool_cost`, `infra_cost`, `total_cost`,
   `billable_audio_minutes`, `stt_ms`, `rag_ms`, `llm_ms`, `mcp_tools_ms`,
   `tts_ms`, `transport_ms`, `total_ms`, `caller_talk_ms`, `agent_talk_ms`,
   `turn_count`, `session_count`, `tool_call_count`, `tool_error_count`,
   `barge_in_count`, and `deadline_miss_count`. Absent additive and count
   measures are numeric zero; `total_cost` and `total_ms` are calculated sums.
5. Implement deterministic nearest-rank `p50`, `p95`, and `p99` values for
   each of `stt_ms`, `rag_ms`, `llm_ms`, `mcp_tools_ms`, `tts_ms`,
   `transport_ms`, and `total_ms`. Empty samples produce `0.0`; a single
   sample produces that value for all three percentiles. Document and test the
   interpolation/rounding choice used for multi-sample inputs against a fixed
   fixture.
6. Implement every derived metric with aggregate numerators and denominators:
   `cost_per_minute`, `cost_per_booked_outcome`, `conversion_rate`,
   `deadline_miss_rate`, `barge_in_rate`, `tool_success_rate`, and `talk_ratio`.
   Return `None` for each zero denominator; calculate tool success as
   `1 - tool_error_count / tool_call_count` and talk ratio as agent speech
   divided by total measured speech.
7. Expose `SessionRollup` and `RunRollup` models with the exact snapshot keys in
   the normative spec, including `schema_version == "analytics-model/v1"`.
   `SessionRollup` must represent one session. `RunRollup` must aggregate only
   the explicitly supplied in-process session set, include `run_id`, window
   bounds, `group_by`, selected dimension values, all measures, all percentile
   blocks, and all derived metrics. Its `to_dict()` must round-trip the worked
   JSON example in `docs/analytics-model-v1.md` without extra persistence or
   cross-tenant fields.
8. Keep all state owned by the accumulator/call object and discardable by the
   caller. There must be no database, filesystem, network, cache, global
   history, cross-run comparison, tenant aggregation, warehouse, or platform
   import in the implementation.

## Files to create/modify

- `src/lucy/analytics.py` - typed facts, deterministic fact builder,
  accumulator, percentile helper, rollup models, and derived metrics.
- `tests/test_analytics_engine.py` - no-mocks behavioral and negative tests for
  the complete analytics-model/v1 contract.

## Chips

- [ ] **C1 - Add typed fact models and deterministic event parsing.** Write
  failing tests first for one `TurnFact`, `SessionFact`, and `ToolCallFact`,
  their keys/source fields, latest-business selection, cost-event summing, and
  `dropped_events` for malformed/unrelated input. Files:
  `src/lucy/analytics.py`, `tests/test_analytics_engine.py`. Test first:
  `test_build_facts_emits_typed_turn_session_tool_facts_and_counts_drops`.
  Verify: `docker compose run --rm lucy-api pytest tests/test_analytics_engine.py -q -k facts` -> the new tests pass and no mocks are imported.
- [ ] **C2 - Populate complete dimensions and additive measures.** Write
  failing tests first for all ten dimensions, all cost/latency/talk/count
  measures, provider attribution nulls, UTC time, interruption/timeout counts,
  and aggregate talk duration across turns. Files:
  `src/lucy/analytics.py`, `tests/test_analytics_engine.py`. Test first:
  `test_session_rollup_populates_every_v1_dimension_and_measure`.
  Verify: `docker compose run --rm lucy-api pytest tests/test_analytics_engine.py -q -k dimensions` -> exact v1 keys and totals pass, including Card 99 talk measures.
- [ ] **C3 - Implement percentiles and every derived metric.** Write failing
  tests first for fixed multi-sample p50/p95/p99 values, empty/single samples,
  cost/conversion/deadline/barge/tool/talk formulas, and `None` for every zero
  denominator. Files: `src/lucy/analytics.py`,
  `tests/test_analytics_engine.py`. Test first:
  `test_percentiles_and_derived_metrics_match_analytics_model_v1`.
  Verify: `docker compose run --rm lucy-api pytest tests/test_analytics_engine.py -q -k "percentile or derived"` -> deterministic values and null rules pass.
- [ ] **C4 - Add SessionRollup and RunRollup serialization with scope guards.**
  Write failing tests first for exact snapshot keys, worked-example-compatible
  `to_dict()`, explicit run aggregation, window/group dimensions, local null
  tenant, and absence of storage/cross-tenant state. Files:
  `src/lucy/analytics.py`, `tests/test_analytics_engine.py`. Test first:
  `test_run_rollup_matches_worked_example_and_stays_in_process`.
  Verify: `docker compose run --rm lucy-api pytest tests/test_analytics_engine.py -q -k rollup` -> session and run snapshots round-trip with schema version `analytics-model/v1` and no forbidden scope.
- [ ] **C5 - Run conformance gates and finish bookkeeping.** Re-read
  `docs/analytics-model-v1.md`, run the focused and complete Docker tests plus
  lint/type gates, fill `Improvements noted`, record applicable review evidence,
  and move this card to `done/` only after all commands pass. Files:
  `src/lucy/analytics.py`, `tests/test_analytics_engine.py`,
  `backlog/pending/103_complete_analytics_model_v1_engine.md`. Test first:
  `test_existing_card_99_talk_measure_contract_remains_green`.
  Verify: `docker compose run --rm lucy-api pytest tests/test_analytics_model.py tests/test_analytics_engine.py -q` -> focused suites pass; `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q` -> backlog contract passes before the move.

## Do NOT

- Do not use mocks, monkeypatching, mocking frameworks, or invented provider
  behavior (ADR 0003); use real typed event models and deterministic local
  fixtures/simulators.
- Do not hardcode provider names, model names, URLs, secrets, thresholds,
  regions, currencies, quotas, or latency budgets outside typed settings,
  registries, or named constants.
- Do not edit `docs/analytics-model-v1.md`, telemetry wire definitions, Card 99,
  or any file not listed by a chip.
- Do not add persistence, filesystem/database/network storage, global history,
  cross-run comparisons, cross-tenant aggregation, warehouse logic, or platform
  imports to the open SDK (ADR 0010).
- Do not derive talk duration from transcript text, token counts, TTS text,
  call duration, or billable audio minutes.
- Do not add fields or formulas outside the normative v1 contract, and do not
  silently coerce malformed facts into plausible analytics.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_analytics_model.py tests/test_analytics_engine.py -q` -> Card 99 and all new fact, dimension, measure, percentile, formula, serialization, and negative tests pass.
- [ ] `docker compose run --rm lucy-api ruff check src/lucy/analytics.py tests/test_analytics_engine.py` -> no lint violations.
- [ ] `docker compose run --rm lucy-api ruff format --check src/lucy/analytics.py tests/test_analytics_engine.py` -> both files are formatted.
- [ ] `docker compose run --rm lucy-api mypy src/lucy/analytics.py` -> no type errors.
- [ ] `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q` -> the upgraded pending-card contract passes before state move.
- [ ] Post-task audit done; `Improvements noted` is filled, follow-up cards are raised for every remaining issue, and this card is moved to `done/`.

## Failure protocol

If the telemetry event models cannot represent a required v1 field, provider
attribution is unavailable, a percentile or worked-example value conflicts with
the normative document, or any scope guard fails: stop at the last green chip,
leave the card in `in_progress/`, record the exact failing test and evidence in
`Improvements noted`, and report the mismatch. Do not weaken validation, invent
provider behavior, persist data, or claim completion with skipped tests.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

- code-reviewer: PASS | FAIL - <report ref or summary>
- test-auditor: PASS | FAIL - <report ref or summary>
- docs-reviewer: PASS | FAIL - <report ref or summary>
- simplicity-reviewer: PASS | FAIL - <report ref or summary>
- security-reviewer: PASS | FAIL - <report ref or summary, or NOT_APPLICABLE with reason>

Findings disposition:

- [P0|P1|P2|P3][reviewer-NNN] finding - fixed | follow-up card NN | rejected: rationale
