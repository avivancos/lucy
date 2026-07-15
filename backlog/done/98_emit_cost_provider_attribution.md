# 98 - Emit per-component cost provider attribution

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Cost governance
**Estimated effort:** ~4 h
**Depends on:** 66, 72
**State:** done

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

- [x] **C1 - Typed attribution model.** Add failing serialization tests, then
  implement the additive provider/model attribution on cost events. Verify:
  `docker compose run --rm lucy-api pytest tests/test_observability.py -q -k cost_provider` -> 42 passed.
- [x] **C2 - Runtime wiring.** Add deterministic cascaded and realtime tests
  proving resolved provider specs populate the correct components without
  leaking secrets. Verify: `docker compose run --rm lucy-api pytest tests/test_pricing.py tests/test_observability.py -q` -> all pass.
  Result: 152 passed.
- [x] **C3 - Wire and gates.** Document allocation semantics, run full Docker
  gates, fill review evidence, and move the card. Verify: full pytest, ruff,
  format, and mypy gates pass. Result: 1,168 passed, 2 deselected; static gates
  clean; final no-cache build and `/health` smoke passed on isolated port 18089.

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

- [x] Cascaded fixture attributes STT, LLM, and TTS independently
- [x] Realtime fixture may attribute multiple components to one provider
- [x] Legacy cost event parsing remains green
- [x] `docker compose run --rm lucy-api pytest tests/test_pricing.py tests/test_observability.py -q` -> 152 passed
- [x] `docker compose run --rm lucy-api pytest` -> 1,168 passed, 2 deselected
- [x] Docker ruff, format, and mypy gates pass
- [x] Post-task audit done; no new follow-up card required

## Failure protocol

If a resolved driver does not expose provider/model identity, leave that
component unattributed, document the missing seam, and raise a provider-specific
follow-up instead of guessing from class names or URLs.

## Improvements noted

- `CostComponent` constrains attribution keys to the seven existing cost fields;
  raw usage attribution remains a separate map and legacy events remain valid.
- Cascaded and realtime drivers expose only identities already validated by the
  model registry. Session-level STT/TTS owners remain explicit inputs because
  the Python control plane does not own media-provider instantiation, but every
  nonlocal identity must exist in the supplied registry with the right speech
  capability.
- Conflicting configured and driver identities fail before session execution,
  preventing silent attribution overrides.
- Provider and model strings are bounded slug identifiers. Endpoint-shaped,
  unregistered, capability-mismatched, and secret-bearing values are rejected
  before enqueue; the privacy traversal remains defense in depth.
- Endpoint rejection covers URLs, DNS hostnames, host-and-port values,
  root-dot and IDNA hostnames, localhost, canonical and legacy IP literals, and
  slashless network URIs while retaining versioned model slugs.
- Every nested provider identity is revalidated at the event boundary, and
  configured environment secrets cause the complete telemetry event to be
  dropped before export rather than being emitted as attribution.
- Driver-owned identities are checked again at the session boundary against a
  retained registry snapshot and the component capability they claim; a
  caller-supplied media registry cannot replace that authority.
- Barge-in and rejected MCP paths retain the same resolved component identity
  on the final cost event; unknown responder execution remains unattributed.
- Default compiled graphs snapshot the inner driver attribution and registry so
  `GraphTurnDriver` preserves the same validated LLM owner on cost events.
- The three existing FastAPI/httpx deprecation warnings remain assigned to
  launch-hygiene Card 93.

## Review evidence

- `code-reviewer` (`019f6682-d383-7340-b6de-09ecc88f7833`): PASS. No
  correctness or contract findings after the final 42-test focused, 152-test
  targeted, and 1,168-test full runs.
- `test-auditor` (`019f6682-e697-7921-abba-06f2cefafa86`): PASS. The tests
  exercise legacy parsing, all seven component keys, cascaded/realtime owners,
  graph propagation, interruption/MCP paths, registry authority, capability
  mismatches, invalid nested identities, and secret-bearing event suppression
  without mocks.
- `simplicity-reviewer` (`019f6682-d8ed-7e81-813a-276fb736c049`): PASS. No
  removable task-scope complexity remained.
- `security-reviewer` (`019f6682-dd7b-7580-942c-0c9bf96f830b`): PASS. Endpoint,
  hostname, URI-scheme, IP-literal, configured-secret, and opaque-secret bypass
  variants are rejected; no MCP, route, or curated public-surface change was
  introduced.
- `docs-reviewer` fallback (`019f6682-e323-79c1-840c-f29e6e3aba1e`): PASS. The
  configured Nano reviewer was unavailable, so the documented fallback used a
  default reviewer; wire allocation semantics, local/unknown identity behavior,
  graph propagation, and ADR 0015 now match the implementation.
- Review findings resolved before the final round: require authoritative
  registry/capability validation, revalidate nested copied models, preserve
  optional models and unknown attribution, propagate immutable mappings through
  graphs, reject generic endpoint schemes without breaking versioned model
  slugs, and correct stale future-tense documentation.
- `final-integrator` (`019f6688-a5b2-7d13-b7d4-c5cdc722fa5f`): PASS. No
  blocking finding remained after review evidence and backlog-contract
  verification were consolidated.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
