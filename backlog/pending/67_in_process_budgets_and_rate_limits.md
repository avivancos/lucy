# 67 - Add in-process budgets and rate limits

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Cost governance
**Estimated effort:** ~7 h
**Depends on:** 66
**State:** pending

## Goal

Give self-host and offline Lucy users LiteLLM-style local cost protection without
depending on the hosted platform. Budgets and RPM/TPM limits run in process,
emit observable events, and can end a session deterministically.

## Context primer

- `agents.md` - no mocks, typed settings, and deterministic tests.
- `docs/adr/0015-in-process-cost-governance-and-routing.md` - in-process budget rationale.
- `src/lucy/clock.py` - budget windows use the injectable clock.
- `src/lucy/session.py` - hard budget action emits a session end directive.
- `src/lucy/llm.py` - RPM/TPM checks wrap the LLM stream seam.

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
- `src/lucy/session.py` - session hard-action hook.
- `src/lucy/llm.py` or driver seam - RPM/TPM enforcement point.
- `tests/test_budget.py` - deterministic budget and rate-limit tests.

## Chips

- [ ] **C1 - Budget policy model.** Write failing tests for soft and hard USD caps, then implement typed policies and counters. Files: `src/lucy/budget.py`, `tests/test_budget.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_budget.py -q -k budget` -> selected tests pass.
- [ ] **C2 - Session hard action.** Add a call test proving hard budget emits `SessionEnd(reason="budget_exceeded")`, then wire the session hook. Files: `src/lucy/session.py`, `tests/test_budget.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_budget.py -q` -> all budget tests pass.
- [ ] **C3 - LLM rate limits and gates.** Add RPM/TPM ManualClock tests, wire the LLM seam, run full gates, and move the card. Files: `src/lucy/budget.py`, `src/lucy/llm.py`, `tests/test_budget.py`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; drive real local providers or deterministic simulators.
- Do not hardcode limits, windows, provider names, model names, or currencies outside typed policy settings.
- Do not implement hosted virtual-key budgets in the SDK; those belong to `lucy-platform`.
- Do not use wall-clock sleeps.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_budget.py -q` -> budget tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a budget action cannot be expressed through the current session or LLM seam,
stop, document the missing hook, and leave the card in `in_progress/`.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
