# 66 - Add voice pricing registry and cost round-out

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Cost governance
**Estimated effort:** ~8 h
**Depends on:** 72
**State:** pending

## Goal

Make Lucy report complete voice-agent costs, not only LLM cost. The SDK computes
STT, LLM, TTS, telephony, RAG, MCP, and infra components from typed price data so
local analytics and the platform Cost Board share one source of truth.

## Context primer

- `agents.md` - project operating rules, no mocks, typed config, and commit policy.
- `docs/adr/0015-in-process-cost-governance-and-routing.md` - cost governance boundary.
- `docs/telemetry-wire-v1.md` - additive cost fields must stay wire-v1 compatible.
- `src/lucy/metrics.py` - `CostBreakdown` field names are the public metric vocabulary.
- `src/lucy/session.py` - session finalization is where call-level cost is assembled.

## Spec

Add `src/lucy/pricing.py` with a `PriceBook` model covering LLM tokens, cached
tokens, STT minutes, TTS characters or seconds, telephony inbound/outbound
minutes, RAG, MCP, and infra components. Prices load from typed settings and an
optional JSON file pointed to by `LUCY_PRICING_PRICEBOOK_PATH`.

`VoiceSession` must emit a session-level `cost` event whose `CostBreakdown`
contains all seven components. Additive wire metadata is allowed:
`CostEvent.pricebook_version` and `CostEvent.attribution: dict[str, float]`.
Missing usage facts produce explicit zero components, not omitted fields.

## Files to create/modify

- `src/lucy/pricing.py` - typed price book and registration helpers.
- `src/lucy/session.py` - final cost assembly from control-channel facts.
- `src/lucy/observe/events.py` - additive cost event metadata.
- `docs/telemetry-wire-v1.md` - document the additive fields.
- `tests/test_pricing.py` - price book and session cost coverage.

## Chips

- [ ] **C1 - Price book model.** Add failing tests for loading and validating a voice price book, then implement `PriceBook` and registration helpers. Files: `src/lucy/pricing.py`, `tests/test_pricing.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_pricing.py -q -k pricebook` -> all selected tests pass.
- [ ] **C2 - Full session cost emission.** Add a deterministic session test that exercises STT minutes, TTS chars, telephony minutes, and LLM usage, then emit a complete `CostBreakdown`. Files: `src/lucy/session.py`, `tests/test_pricing.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_pricing.py -q` -> all tests pass.
- [ ] **C3 - Wire docs and full gates.** Document `pricebook_version` and `attribution`, run full Docker gates, fill Improvements noted, and move the card. Files: `docs/telemetry-wire-v1.md`, `src/lucy/observe/events.py`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use `lucy.testing` simulators and deterministic control-channel facts.
- Do not hardcode provider names, model names, prices, currencies, thresholds, or budgets outside typed settings, registries, or named constants.
- Do not put platform spend-ledger or invoice reconciliation code in the SDK.
- Do not remove or rename existing `CostBreakdown` fields.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_pricing.py -q` -> pricing tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If price facts are unavailable, the event shape conflicts with wire v1, or tests
need provider behavior not in fixtures: leave the card in `in_progress/`, record
the gap under Improvements noted, and report.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
