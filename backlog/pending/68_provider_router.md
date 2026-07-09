# 68 - Add provider router

**Sprint:** S6 - Provider ecosystem
**Epic:** Providers
**Estimated effort:** ~9 h
**Depends on:** 37
**State:** pending

## Goal

Add a LiteLLM-style in-process router for resolved provider instances so Lucy can
choose low-latency deployments, cool down failing providers, and fail over before
the first streamed token while preserving the frozen provider Protocols.

## Context primer

- `agents.md` - provider names and thresholds must live in settings or registries.
- `docs/adr/0015-in-process-cost-governance-and-routing.md` - routing rationale.
- `src/lucy/providers.py` - `ModelRegistry`, capabilities, and `ModelInfo.low_latency`.
- `src/lucy/llm.py` - `LlmProvider.stream_chat()` is the protocol to wrap.
- `src/lucy/tracing.py` - fallback and routing decisions must be observable.

## Spec

Create `src/lucy/router.py` with a `Router` that supports priority, weighted,
latency-aware, and cooldown-based selection over already resolved provider
instances. Add `RoutingLlmProvider` implementing the existing `LlmProvider`
Protocol. Failover is allowed only before the first token is yielded; after that,
errors are surfaced as stream errors and traced.

Routing emits spans that name the route policy, selected provider key, fallback
reason, cooldown state, and first-token EWMA. STT/TTS routing is limited to
session-start selection via `SessionConfigure`; mid-call STT failover is deferred
to gateway work.

## Files to create/modify

- `src/lucy/router.py` - router policy and `RoutingLlmProvider`.
- `src/lucy/llm.py` - imports or helper hooks only if needed.
- `tests/test_router.py` - deterministic routing and failover tests.
- `docs/model-registry-maintenance.md` - note router assumptions if needed.

## Chips

- [ ] **C1 - Selection policies.** Write failing tests for priority, weighted, and low-latency prior selection, then implement pure routing. Files: `src/lucy/router.py`, `tests/test_router.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_router.py -q -k selection` -> selected tests pass.
- [ ] **C2 - Streaming failover.** Add tests for failover before first token and no failover after first token, then implement `RoutingLlmProvider`. Files: `src/lucy/router.py`, `tests/test_router.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_router.py -q` -> all router tests pass.
- [ ] **C3 - Observability and gates.** Add span assertions for fallback/cooldown metadata, update docs if needed, and run full gates. Files: `src/lucy/router.py`, `tests/test_router.py`, `docs/model-registry-maintenance.md`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; implement deterministic local provider simulators for routing cases.
- Do not hardcode provider names, model names, cooldowns, thresholds, or weights outside typed policy objects.
- Do not change the frozen `LlmProvider` Protocol.
- Do not implement platform virtual keys or hosted budget enforcement here.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_router.py -q` -> router tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If failover needs to occur after audio has started or requires gateway-owned
state, defer that branch to a follow-up card and keep this card scoped.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
