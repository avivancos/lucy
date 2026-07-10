# 95 - Inject grounded RAG context into LLM requests

**Sprint:** S4 - Graph and state
**Epic:** RAG correctness
**Estimated effort:** ~5 h
**Depends on:** 37
**State:** done

## Goal

Make retrieved evidence affect the answer instead of only being measured or
stored in graph state. Both the live cascaded session and the default agent
graph must pass grounded context to the LLM while preserving cache hits,
deadlines, citations, and empty-context fallback behavior.

## Context primer

- `agents.md` - project operating rules, Docker gates, and no-mocks policy.
- `src/lucy/rag.py` - `RagResult.prompt_context` and grounding identifiers.
- `src/lucy/session.py` - live RAG prefetch and cascaded driver invocation.
- `src/lucy/graph.py` - default context-synthesis and LLM graph nodes.
- `src/lucy/drivers.py` - `TurnDriver` history contract and LLM request assembly.

## Spec

Add one canonical helper that converts a non-empty `RagResult.prompt_context`
into an LLM system message. The message must identify the content as grounded
context, preserve every `grounding_id`, and instruct the model not to invent
facts outside the supplied evidence. An empty result produces no message.

`VoiceSession` must retain the final retrieval result for a turn and prepend
the canonical grounded message to the history passed to a cascaded driver.
The default graph must prepend the same canonical message from its synthesized
state before invoking its inner driver. Existing transcript order, tool rounds,
speculation, cache-hit accounting, and timeout fallback remain unchanged.

## Files to create/modify

- `src/lucy/rag.py` - canonical grounded-message helper.
- `src/lucy/session.py` - retain and pass live retrieved context.
- `src/lucy/graph.py` - pass synthesized graph context to the inner driver.
- `tests/test_rag_prompt.py` - behavioral request-inspection coverage.
- `backlog/sprints.md` - map this correctness card to S4.

## Chips

- [x] **C1 - Canonical grounded message.** Write failing helper tests for
  non-empty and empty results, then implement one canonical conversion. Files:
  `src/lucy/rag.py`, `tests/test_rag_prompt.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_rag_prompt.py -q -k message`
  -> selected tests pass.
- [x] **C2 - Live and graph injection.** Write failing tests that inspect the
  actual LLM request for both paths, then wire the helper without changing the
  driver ABI. Files: `src/lucy/session.py`, `src/lucy/graph.py`,
  `tests/test_rag_prompt.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_rag_prompt.py -q`
  -> all RAG prompt tests pass.
- [x] **C3 - Regression gates and bookkeeping.** Run the full Docker gates,
  complete review evidence, and move the card to `done/`. Files:
  `backlog/in_progress/95_inject_grounded_rag_context_into_llm_requests.md`.
  Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; inspect a deterministic local LLM
  provider wrapper.
- Do not hardcode providers, models, retrieval limits, or deadlines.
- Do not change the frozen `TurnDriver` or `LlmProvider` Protocols.
- Do not send retrieved text through telemetry or platform storage in this card.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_rag_prompt.py -q` ->
      both live and graph paths pass grounded context to the LLM.
- [x] `docker compose run --rm lucy-api pytest` plus Docker ruff, format, and
      mypy -> all gates green.
- [x] Post-task audit done; no follow-up was required.

## Failure protocol

If injecting context requires a provider ABI change or duplicates the user
message, leave the card in `in_progress/`, record the exact conflict below, and
do not weaken the tests.

## Improvements noted

- Retrieved chunks are an untrusted prompt-injection surface. The canonical
  message now labels them as evidence rather than instructions and wraps them
  in explicit `grounded_context` delimiters.
- The responder-only M0 path remains intentionally context-free; grounded RAG
  is supported by the production cascaded and graph driver paths without
  widening the frozen driver/provider Protocols.

## Review evidence

- code-reviewer: PASS - scope matches the card; both production paths inject
  context without ABI changes, and deadline/cancellation behavior is preserved.
- test-auditor: PASS - five deterministic no-mocks tests inspect real simulator
  requests, including empty evidence and deadline fallback negative branches.
- docs-reviewer: PASS - ADR 0007's grounded-context claim now matches runtime
  behavior; sprint index and card references resolve.
- simplicity-reviewer: PASS - one canonical helper is reused by two callers;
  no new protocol, class hierarchy, cache, or provider seam was introduced.
- security-reviewer: PASS - retrieved text stays in-process, no telemetry or
  secret surface changed, and untrusted evidence is explicitly delimited.

Findings disposition:

- [P1][code-001] `rag_result` was initially placed on public `TurnRecord` -
  fixed by keeping it only on ephemeral `_ActiveTurn`.
- [P2][sec-001] retrieved text lacked an explicit prompt-injection trust
  boundary - fixed with untrusted-evidence instructions and delimiters.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
