# 94 - Emit RAG inspection span attributes

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Observability
**Estimated effort:** ~5 h
**Depends on:** 37, 72
**State:** pending

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

- [ ] **C1 - RAG attribute builder.** Add tests for converting `RagResult` and
  `RagChunk` into redacted `rag.*` attributes, then implement a small helper.
  Files: `src/lucy/rag.py`, `src/lucy/observe/events.py`,
  `tests/test_rag_telemetry.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_rag_telemetry.py -q`
  -> all pass.
- [ ] **C2 - Runtime emission.** Emit RAG spans from the graph/voice turn path
  without changing retrieval behavior. Files: `src/lucy/graph.py`,
  `src/lucy/session.py`, `tests/test_agent_graph.py`,
  `tests/test_speculation.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_agent_graph.py tests/test_speculation.py -q`
  -> all pass.
- [ ] **C3 - Wire docs and gates.** Update `docs/telemetry-wire-v1.md` with the
  supported `rag.*` span attributes and run full gates. Files:
  `docs/telemetry-wire-v1.md`, `backlog/pending/94_emit_rag_inspection_span_attributes.md`.
  Verify: `docker compose run --rm lucy-api pytest` ->
  full suite green.

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

- [ ] `docker compose run --rm lucy-api pytest tests/test_rag_telemetry.py -q`
  -> new RAG telemetry tests pass.
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green.
- [ ] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If the current telemetry/redaction seams cannot safely carry chunk text, emit
only non-text evidence and record the blocked field under "Improvements noted"
instead of bypassing privacy controls.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
