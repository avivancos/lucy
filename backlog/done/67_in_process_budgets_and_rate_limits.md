# 67 - Add in-process budgets and rate limits

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Cost governance
**Estimated effort:** ~7 h
**Depends on:** 66
**State:** done

## Goal

Give self-host and offline Lucy users LiteLLM-style local cost protection without
depending on the hosted platform. Budgets and RPM/TPM limits run in process,
emit observable events, and can end a session deterministically.

## Context primer

- `agents.md` - no mocks, typed settings, and deterministic tests.
- `docs/adr/0015-in-process-cost-governance-and-routing.md` - in-process budget rationale.
- `src/lucy/clock.py` - budget windows use the injectable clock.
- `src/lucy/session.py` - hard budget action emits a session end directive.
- `src/lucy/budget.py` - `BudgetedLlmProvider` wraps the LLM stream seam.

## Spec

Add `src/lucy/budget.py` with typed budget policies for per-session and per-agent
USD caps, plus RPM and TPM limits at the LLM seam. Policies support a soft-limit
warning and a hard action. The hard action for calls emits `SessionEnd` with
reason `budget_exceeded`; non-call LLM use raises a typed budget error.

Budget checks use `ManualClock` in tests and never sleep on wall time. Budget
events are emitted through `lucy.observe` as existing `business` or `span` data;
no new wire type is added.

## Files to create/modify

- `src/lucy/budget.py` - policies, counters, and errors.
- `src/lucy/__init__.py` - curated public governance exports.
- `src/lucy/limits.py` - named governance bounds.
- `src/lucy/llm.py` - sanctioned simulator stream-finalization evidence.
- `src/lucy/drivers.py` - propagate the typed budget binding to the session.
- `src/lucy/mcp.py` - preserve sanitized terminal audits for background tools.
- `src/lucy/session.py` - session hard-action hook.
- `src/lucy/settings.py` - typed directive-commit deadline.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - bounded runtime cleanup.
- `docs/adr/0009-turn-taking-and-conversational-fluidity.md` - current runtime wording.
- `docs/adr/0015-in-process-cost-governance-and-routing.md` - governance contract.
- `tests/test_budget.py` - deterministic budget and rate-limit tests.
- `tests/test_agent_facade.py` - curated export contract.
- `tests/test_architecture_adrs.py` - current runtime documentation contract.
- `tests/test_interruption.py` - run-to-completion MCP shutdown regression.
- `tests/test_realtime_driver.py` - realtime late-dispatch cancellation regression.
- `tests/test_settings.py` - typed commit-budget default.

## Chips

- [x] **C1 - Budget policy model.** Write failing tests for soft and hard USD caps, then implement typed policies and counters. Files: `src/lucy/budget.py`, `tests/test_budget.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_budget.py -q -k budget` -> 86 tests pass.
- [x] **C2 - Session hard action.** Add call tests proving session and agent USD, RPM, TPM, missing metering, and elapsed idle cost emit `SessionEnd(reason="budget_exceeded")`, then wire the session hook. Files: `src/lucy/session.py`, `src/lucy/drivers.py`, `tests/test_budget.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_budget.py -q` -> all budget tests pass.
- [x] **C3 - LLM rate limits, lifecycle hardening, docs, and gates.** Add RPM/TPM ManualClock tests, lease-authenticate the LLM seam, bound/supervise cancellation and MCP background work, publish the curated API, align ADRs, run full gates, and move the card. Files: `src/lucy/__init__.py`, `src/lucy/budget.py`, `src/lucy/drivers.py`, `src/lucy/limits.py`, `src/lucy/llm.py`, `src/lucy/mcp.py`, `src/lucy/session.py`, `src/lucy/settings.py`, `docs/adr/0009-turn-taking-and-conversational-fluidity.md`, `docs/adr/0011-hybrid-streaming-voice-runtime.md`, `docs/adr/0015-in-process-cost-governance-and-routing.md`, `tests/test_agent_facade.py`, `tests/test_architecture_adrs.py`, `tests/test_budget.py`, `tests/test_interruption.py`, `tests/test_realtime_driver.py`, `tests/test_settings.py`. Verify: `docker compose run --rm lucy-api pytest` -> 1,103 passed, 2 deselected.

## Do NOT

- Do not use mocks or mocking frameworks; drive real local providers or deterministic simulators.
- Do not hardcode limits, windows, provider names, model names, or currencies outside typed policy settings.
- Do not implement hosted virtual-key budgets in the SDK; those belong to `lucy-platform`.
- Do not use wall-clock sleeps.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_budget.py -q` -> 86 passed
- [x] `docker compose run --rm lucy-api pytest` -> 1,103 passed, 2 deselected
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Clean Compose build and `/health` smoke passed on isolated port 18077
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a budget action cannot be expressed through the current session or LLM seam,
stop, document the missing hook, and leave the card in `in_progress/`.

## Improvements noted

- Assigned call pricing exclusively to `VoiceSession`; priced provider wrappers
  are standalone-only, while unpriced wrappers supply RPM/TPM governance.
- Bounded rate-window and identity state with O(1) token accounting prevents
  governance state from growing without limit. At the 100,000-entry ceiling, a
  Docker `timeit` measurement found deque summation costs about 1.36 ms per
  check, justifying the synchronized token total on the voice latency path.
- Session cleanup is bounded for non-cooperative tasks while MCP operations
  explicitly marked `run_to_completion` retain their sanitized terminal audit
  event; detached turn output is fenced by turn cancellation and single-use
  session closure. Transport commits resolve within a typed deadline or invoke
  the adapter abort/close seam before shutdown returns.
- Exclusive opaque leases prevent duplicate opens and stale closes from
  resetting a live session ledger; every mutation authenticates the lease and
  unmetered cancellation poisons it.
- Governed LLM streams require provider usage metering, and elapsed call costs
  are checked on live control events and reconciled once more during cancellation
  so idle or disconnected calls cannot bypass USD limits.
- Standalone USD-governed LLM decorators fail before provider dispatch without
  an explicit price book; voice sessions mark their shared lease as externally
  priced so the complete multimodal call remains single-charged.
- Turn cancellation is latched before task cancellation and checked again at
  cascaded and realtime MCP dispatch boundaries, preventing a non-cooperative
  late model event from causing side effects after barge-in or session closure.
- Disabled policies allocate no historical agent state, and expired rate-only
  identities are retired before capacity admission.
- Existing FastAPI/httpx deprecation warnings remain owned by launch-hygiene
  card 93; no new follow-up card is required.
- Stale landed-card references found during review were corrected separately in
  commit `79887fa` so Card 67 remains focused.

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

- code-reviewer: PASS - `019f6509-7f3b-7540-a8dc-94fa8b77ba15`; final diff and all runtime gates clean.
- test-auditor: PASS - `019f6512-a375-7372-ad02-bc8288fbaaa3`; 128 focused and 1,103 full-suite tests passed with no mocks or wall-time sleeps.
- docs-reviewer: PASS - `019f6516-1353-7f81-9b65-df3b05defa8d`; counts, ADR alignment, referenced paths, and backlog contract verified.
- simplicity-reviewer: PASS - `019f6509-87df-7f32-955c-53dcfcfb441e`; no redundant governance or cancellation implementation remains.
- security-reviewer: PASS - `019f6509-8bed-72d0-9be2-7e08cba2f801`; cost ownership, cancellation, secrets, telemetry, and open-core boundaries verified.

- [P1][code-001] standalone unpriced USD governance bypass - fixed with fail-closed pre-dispatch metering and a red/green regression.
- [P1][sec-001] detached cancelled turn could dispatch a late MCP side effect - fixed with a latched cancellation capability and cascaded red/green regression.
- [P1][test-001] realtime late MCP cancellation guard lacked mutation-resistant coverage - fixed with a controlled red mutation and green realtime regression.
- [P2][docs-001] C3 and Definition-of-Done evidence remained unchecked - fixed in this bookkeeping pass.
- [P2][docs-002] final reviewer evidence was missing - fixed in this bookkeeping pass.
- [P2][docs-003] C1 retained the pre-regression budget-test count - fixed at 86.
