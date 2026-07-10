# 99 - Add measured talk-ratio telemetry

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Analytics / voice quality
**Estimated effort:** ~5 h
**Depends on:** 52, 41
**State:** pending

## Goal

Expose caller and agent speech duration as portable analytics measures so local
and hosted dashboards can report talk ratio without inferring speech activity
from billable audio minutes or total call duration.

## Context primer

- `docs/analytics-model-v1.md` - normative fact grains and measure formulas.
- `docs/telemetry-wire-v1.md` - client-safe event contract.
- `src/lucy/transport/schema.py` - media/control-plane boundary.
- `backlog/pending/41_asterisk_adapter.md` - real VAD and endpointing source.
- `agents.md` - project operating rules.

## Spec

Add additive `caller_talk_ms` and `agent_talk_ms` measures sourced from real
media-plane speaker activity. Define
`talk_ratio = agent_talk_ms / (agent_talk_ms + caller_talk_ms)` and return null
when total measured speech is zero. Extend telemetry wire v1 additively only if
the existing turn event cannot carry the redacted duration counters without
crossing audio into Python.

The simulator must produce deterministic speaker-activity intervals from its
real control-channel flow. The runtime must not estimate either duration from
tokens, transcript length, TTS text length, call duration, or billable minutes.

## Chips

- [ ] **C1 - Contract and red tests.** Add failing analytics conformance tests
  for both duration measures, zero-denominator behavior, and multi-turn sums.
  Files: `tests/test_analytics.py`, `docs/analytics-model-v1.md`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_analytics.py -q` -> new
  assertions fail before implementation.
- [ ] **C2 - Measured runtime attribution.** Wire deterministic simulator
  speaker activity and real media-plane control messages into typed turn facts;
  add negative tests proving billable minutes and transcript length are ignored.
  Files: `src/lucy/transport/`, `src/lucy/analytics.py`, focused tests. Verify:
  focused transport and analytics tests pass.
- [ ] **C3 - Conformance and bookkeeping.** Update wire/schema documentation if
  required, run all Docker gates, record review evidence, and move this card.
  Verify: full pytest, ruff, format, and mypy gates pass.

## Do NOT

- Do not send audio frames into Python or telemetry.
- Do not infer speech duration from billing, text, tokens, or wall-clock call time.
- Do not add platform persistence or dashboard code to the SDK.
- Do not hardcode VAD thresholds outside typed media-plane settings.
- Do not use mocks or mocking frameworks.

## Definition of Done

- [ ] Cascaded simulator fixture -> exact caller and agent talk milliseconds
- [ ] Zero measured speech -> `talk_ratio` is null
- [ ] Existing analytics-model/v1 consumers remain backward compatible
- [ ] Full Docker pytest, ruff, format, and mypy gates pass
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If the current control channel cannot attribute real speaker activity without
moving audio into Python, specify an additive media-plane summary message first;
do not derive a plausible-looking ratio from unrelated telemetry.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

- code-reviewer: PENDING
- test-auditor: PENDING
- docs-reviewer: PENDING
- simplicity-reviewer: PENDING
- security-reviewer: PENDING

Findings disposition:

<!-- Fill during execution. -->
