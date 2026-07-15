# 94 - Emit RAG inspection span attributes

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Observability
**Estimated effort:** ~5 h
**Depends on:** 37, 72
**State:** done

## Goal

Make RAG retrieval evidence visible to platform trace inspectors by emitting
stable `rag.*` span attributes from the open SDK whenever retrieval runs.
The platform can already parse these fields; this card fills the SDK-side wire
gap without adding storage or hosted concerns to Lucy.

## Context primer

- `src/lucy/rag.py` - `RagResult`, `RagChunk`, cache-hit, deadline, score, and
  grounding-id source data.
- `src/lucy/observe/events.py` - wire span event schema and attribute typing.
- `docs/telemetry-wire-v1.md` - normative span payload contract.
- `backlog/done/37_runtime_m5_agent_graph_checkpointing.md` - graph RAG node
  stores prompt context, grounding ids, and deadline fallback state.
- `agents.md` - no-mocks, open-core, and telemetry safety rules.

## Spec

When SDK RAG retrieval runs inside a voice turn or prebuilt graph node, emit a
span whose attributes include:

- `rag.query`: redacted retrieval query text, only when transcript export is
  enabled for the local process.
- `rag.cache_hit`: boolean.
- `rag.deadline_exceeded`: boolean.
- `rag.prompt_included_grounding_ids`: ordered list of grounding ids included
  in prompt context.
- `rag.chunks`: ordered list with `id`, `source`, `grounding_id`, `score`,
  optional `score_components`, optional redacted `text`, and
  `included_in_prompt`.

The attributes must pass through the same PII redaction and telemetry sampling
path as transcript/tool data. If transcript export is disabled, emit chunk ids,
grounding ids, scores, cache state, and deadline state, but suppress query text
and chunk text.

## Chips

- [x] **C1 - RAG attribute builder.** Add tests for converting `RagResult` and
  `RagChunk` into redacted `rag.*` attributes, then implement a small helper.
  Files: `src/lucy/rag.py`, `src/lucy/observe/__init__.py`,
  `src/lucy/observe/events.py`, `src/lucy/observe/redact.py`,
  `tests/test_rag_telemetry.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_rag_telemetry.py -q`
  -> 6 passed.
- [x] **C2 - Runtime emission.** Emit RAG spans from the graph/voice turn path
  and prebuilt context-synthesis catalog without changing retrieval behavior.
  Files: `src/lucy/graph.py`, `src/lucy/session.py`,
  `src/lucy/nodes/perception.py`, `src/lucy/prebuilt/__init__.py`,
  `tests/test_agent_graph.py`, `tests/test_speculation.py`,
  `tests/test_nodes_perception.py`, `tests/test_prebuilt_graphs.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_rag_telemetry.py tests/test_agent_graph.py tests/test_speculation.py tests/test_nodes_perception.py tests/test_prebuilt_graphs.py -q`
  -> 58 passed.
- [x] **C3 - Wire docs and gates.** Update `docs/telemetry-wire-v1.md` with the
  supported `rag.*` span attributes and run full gates. Files:
  `docs/telemetry-wire-v1.md`, `backlog/done/94_emit_rag_inspection_span_attributes.md`.
  Verify: `docker compose run --rm lucy-api pytest` ->
  1,112 passed, 2 deselected.

## Do NOT

- Do not add platform storage, dashboard code, or cross-run aggregation to the
  SDK.
- Do not use mocks or mocking frameworks; use `lucy.testing` simulators and
  deterministic RAG fixtures.
- Do not hardcode chunk ids, provider names, thresholds, deadlines, or PII
  policy outside typed settings and existing telemetry config.
- Do not emit transcript/query/chunk text when telemetry privacy settings
  suppress text-bearing events.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_rag_telemetry.py -q`
  -> 6 passed.
- [x] `docker compose run --rm lucy-api pytest` -> 1,112 passed, 2 deselected.
- [x] Docker ruff, format check, and mypy gates are clean.
- [x] Clean Compose build and `/health` smoke passed on isolated port 18078.
- [x] Post-task audit done; no new follow-up card required.

## Failure protocol

If the current telemetry/redaction seams cannot safely carry chunk text, emit
only non-text evidence and record the blocked field under "Improvements noted"
instead of bypassing privacy controls.

## Improvements noted

- Canonical JSON keeps ordered chunk evidence inside the existing wire-v1
  string attribute schema, so no telemetry event type or platform dependency
  was added.
- Transcript suppression is enforced both while building RAG attributes and
  again in the client-side privacy traversal, protecting direct span callers.
- Retrieval telemetry is fail-open for missing identities and malformed or
  nonfinite evidence; graph and voice behavior remains authoritative.
- Speculative prefetch spans correctly report no prompt inclusion, while the
  promoted/final cache retrieval identifies the chunks actually inserted into
  context.
- The catalog `ContextSynthesisNode` now shares one instrumented retrieval path
  for normal and fallback execution, and reports only the chunks retained by
  its prompt limit.
- Deadline telemetry uses a deterministic simulator and injected manual clock;
  no wall-time sleep is required by the Card 94 regression tests.
- The three existing FastAPI/httpx deprecation warnings remain assigned to
  launch-hygiene Card 93.

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

- code-reviewer: PASS - `019f6623-0ec4-7193-8ad0-4cfe53110b85`; scoped diff, 58 focused tests, 1,112 full-suite tests, static gates, and prior finding resolution verified.
- test-auditor: PASS - `019f6623-29d4-7e81-88a0-550f535d60e8`; exact canonical wire assertions, deterministic deadline simulation, no mocks, and focused gates verified.
- docs-reviewer: PASS - `019f6623-1526-7da3-a4c9-f0522dce8dca`; wire-v1 semantics, privacy behavior, paths, and card evidence are consistent.
- simplicity-reviewer: PASS - `019f6623-2476-7563-b307-6f3cb086891a`; shared normal/fallback retrieval instrumentation is direct and introduces no unnecessary abstraction.
- security-reviewer: PASS - `019f6623-1d38-7a23-ba23-239d0e7f67ad`; client-side sampling, redaction, transcript suppression, secret scanning, and open-core boundaries verified.

- [P1][code-001] catalog `ContextSynthesisNode` retrieval lacked RAG telemetry - fixed with one instrumented normal/fallback path, explicit tracer propagation, and a runtime regression covering prompt-limited evidence.
- [P1][test-001] canonical JSON was decoded instead of asserted exactly - fixed with byte-for-byte canonical attribute assertions.
- [P1][test-002] deadline telemetry regression used wall-time timing - fixed with a deterministic deadline simulator and injected `ManualClock` tracer.
- [P2][docs-001] C3 referenced the stale pending card path - fixed to track the card's current lifecycle path.
