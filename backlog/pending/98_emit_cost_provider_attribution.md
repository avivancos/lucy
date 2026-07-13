# 98 - Emit per-component cost provider attribution

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Cost governance
**Estimated effort:** ~4 h
**Depends on:** 66, 72
**State:** pending

## Goal

Make provider spend grouping exact for cascaded and realtime voice sessions by
emitting the provider/model responsible for each reported cost component.

## Context primer

- `agents.md` - operating, no-mocks, typed-config, and commit rules.
- `docs/telemetry-wire-v1.md` - additive wire-v1 cost contract.
- `backlog/done/66_voice_pricing_registry_and_cost_roundout.md` - source of
  complete seven-component cost events and price-book attribution.
- `src/lucy/observe/events.py` - typed cost telemetry event.
- `src/lucy/providers.py` - resolved provider/model identities.
- `../lucy-platform/backlog/done/83_spend_module.md` - consumer that groups
  reported costs and exposes missing attribution explicitly.

## Spec

Extend the cost event additively with `provider_attribution`, keyed by the seven
`CostBreakdown` component names. Each value contains typed `provider` and
optional `model` strings from the resolved runtime specs. Realtime providers may
own multiple components; cascaded sessions may attribute STT, LLM, and TTS to
different providers. Components with local or unknown execution use explicit
registry identities or remain absent; no provider name is invented.

The wire documentation must define that consumers allocate only each attributed
component to its provider. A legacy top-level `tags.provider` remains an optional
coarse dimension and must not override richer component attribution.

## Chips

- [ ] **C1 - Typed attribution model.** Add failing serialization tests, then
  implement the additive provider/model attribution on cost events. Verify:
  `docker compose run --rm lucy-api pytest tests/test_observability.py -q -k cost_provider` -> selected tests pass.
- [ ] **C2 - Runtime wiring.** Add deterministic cascaded and realtime tests
  proving resolved provider specs populate the correct components without
  leaking secrets. Verify: `docker compose run --rm lucy-api pytest tests/test_pricing.py tests/test_observability.py -q` -> all pass.
- [ ] **C3 - Wire and gates.** Document allocation semantics, run full Docker
  gates, fill review evidence, and move the card. Verify: full pytest, ruff,
  format, and mypy gates pass.

## Do NOT

- Do not use mocks or mocking frameworks; use resolved fixture providers and
  recorded telemetry events.
- Do not hardcode provider/model names or infer them from cost magnitude.
- Do not collapse cascaded STT/LLM/TTS attribution into one provider.
- Do not put spend aggregation or tenant logic in the SDK.
- Do not remove legacy cost fields or require consumers to understand the new
  additive field.
- Do not emit API keys, endpoints, credentials, or unredacted provider errors.

## Definition of Done

- [ ] Cascaded fixture attributes STT, LLM, and TTS independently
- [ ] Realtime fixture may attribute multiple components to one provider
- [ ] Legacy cost event parsing remains green
- [ ] `docker compose run --rm lucy-api pytest tests/test_pricing.py tests/test_observability.py -q` -> attribution tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite passes
- [ ] Docker ruff, format, and mypy gates pass
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a resolved driver does not expose provider/model identity, leave that
component unattributed, document the missing seam, and raise a provider-specific
follow-up instead of guessing from class names or URLs.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
