# 99 - Add measured talk-ratio telemetry

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Analytics / voice quality
**Estimated effort:** ~5 h
**Depends on:** 52, 41
**State:** done

## Goal

Expose caller and agent speech duration as portable analytics measures so local
and hosted dashboards can report talk ratio without inferring speech activity
from billable audio minutes or total call duration.

## Context primer

- `docs/analytics-model-v1.md` - normative fact grains and measure formulas.
- `docs/telemetry-wire-v1.md` - client-safe event contract.
- `src/lucy/transport/schema.py` and `src/lucy/transport/dev_gateway.py` -
  media/control-plane boundary and deterministic speaker-activity fixtures.
- `backlog/done/41_add_asterisk_audio_fork_adapter.md` - real VAD and
  endpointing source.
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

- [x] **C1 - Contract and red tests.** Add failing analytics conformance tests
  for both duration measures, zero-denominator behavior, and multi-turn sums.
  Files: `tests/test_analytics_model.py`, `docs/analytics-model-v1.md`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_analytics_model.py -q` -> new
  assertions fail before implementation.
- [x] **C2 - Measured runtime attribution.** Wire deterministic simulator
  speaker activity and real media-plane control messages into typed turn facts;
  add negative tests proving billable minutes and transcript length are ignored.
  Files: `src/lucy/transport/`, `src/lucy/analytics.py`, focused tests. Verify:
  focused transport and analytics tests pass.
- [x] **C3 - Conformance and bookkeeping.** Update wire/schema documentation if
  required, run all Docker gates, record review evidence, and move this card.
  Verify: full pytest, ruff, format, and mypy gates pass.

## Do NOT

- Do not send audio frames into Python or telemetry.
- Do not infer speech duration from billing, text, tokens, or wall-clock call time.
- Do not add platform persistence or dashboard code to the SDK.
- Do not hardcode VAD thresholds outside typed media-plane settings.
- Do not use mocks or mocking frameworks.

## Definition of Done

- [x] Cascaded simulator fixture -> exact caller and agent talk milliseconds
- [x] Zero measured speech -> `talk_ratio` is null
- [x] Existing analytics-model/v1 consumers remain backward compatible
- [x] Full Docker pytest, ruff, format, and mypy gates pass
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If the current control channel cannot attribute real speaker activity without
moving audio into Python, specify an additive media-plane summary message first;
do not derive a plausible-looking ratio from unrelated telemetry.

## Improvements noted

- Card 103 now owns the complete in-process analytics-model/v1 engine. Card 99
  deliberately ships only measured talk aggregation and keeps persistence and
  cross-tenant analytics out of the open SDK.
- Control-event session, turn, utterance, lifecycle, replay, and timestamp
  validation were strengthened because uncorrelated playback or VAD/STT
  summaries could otherwise forge talk-duration telemetry.
- The deterministic simulator now accepts strict, bounded per-turn speaker
  activity profiles so tests can prove media intervals win over text and
  billing proxies.

## Review evidence

- code-reviewer: PASS after continuous multi-clause and active-only late-flush
  correlation fixes (`019f6ad3-2111-7c23-86c0-10f2447a1b7e`).
- test-auditor: PASS after ratio endpoints, strict/legacy wire cases, divergent
  multi-turn intervals, and malicious and delayed-control negatives
  (`019f6ad3-2600-7771-8da7-43a64ff507dc`).
- docs-reviewer: PASS after narrowing the implemented analytics scope,
  retiring Decision 53 unambiguously, fixing dependent card paths/state, and
  documenting the duration bound (`019f6b01-3158-7d92-85b4-5e15820d2a03`).
- simplicity-reviewer: PASS after centralizing playback duration accounting
  and correcting ADR 0013's canonical owner
  (`019f6aff-9663-78a1-b1c2-0003dc528129`).
- security-reviewer: PASS after global control-session validation and bounded,
  correlated playback lifecycle enforcement
  (`019f6ad6-d255-73f0-8e63-a9b2da4ed492`).
- final-integrator: PASS after the foreign-turn barge-in regression, canonical
  Card 103 ownership, all specialist reviews, full gates, and isolated clean
  Compose boot were reconciled (`019f6b04-31aa-76b3-a97c-d8fc1019ca79`).
- Gates: `1404 passed, 2 deselected`; repository-wide ruff check and format
  check pass; mypy passes for 74 source files; backlog contract passes 9 tests.
  A fresh isolated Compose project built from scratch, booted with empty
  Postgres/Redis state, returned a healthy API response, and reached healthy
  media-gateway status.

Findings disposition:

- `[P1 test-099]` proxy-resistance did not exercise runtime attribution: fixed
  with explicit two-turn media intervals divergent from transcript lengths and
  a real session cost event.
- `[P1 test-100]` ratio endpoints were absent: fixed with caller-only `0.0` and
  agent-only `1.0` cases.
- `[P1 test-101]` telemetry assertions could copy one turn: fixed with distinct
  values asserted by turn ID and index.
- `[P1 test-102]` duration fields accepted booleans: fixed with strict bounded
  integer types plus legacy, boolean, negative, and overflow tests.
- `[P1 test-103]` wall-clock allowance did not prove deterministic timing:
  fixed by sharing `ManualClock` and asserting control timestamps while virtual
  time remains unchanged.
- `[P1 code-001]` continuous multi-clause playback undercounted barge-in: fixed
  by resuming measured intervals across nonterminal clause boundaries and
  asserting exact first-start-to-barge duration.
- `[P1 sec-001]` unmatched utterances could consume another playback start:
  fixed with expected-utterance lifecycle checks and constrained continuous
  terminal handling.
- `[P1 sec-003]` cross-session VAD/STT could contaminate caller duration: fixed
  by validating every control envelope before state or metric processing.
- `[P1 late-flush-001]` known but inactive clauses could consume the late flush
  allowance: fixed by binding the one-use allowance to utterances active at
  interruption.
- `[P1 test-104]` the late-flush test did not delay a valid event into the next
  active turn: fixed with a deterministic gateway that retains the interrupted
  turn's flush until the following playback lifecycle is active.
- `[P1 final-099-001]` a foreign-turn barge-in could end the active playback
  interval: fixed by enforcing active turn identity before interruption and by
  adding the final integrator's mutation as a deterministic regression test.
- `[P2 final-099-002]` Cards 53 and 103 both appeared executable: fixed by
  preserving 53 as a non-work decision, removing it from S9, and making Cards
  54 and 57 depend on Card 103 and its canonical module.
- `[P2 simp-001-final]` ADR 0013 still named Card 53 as the rollup owner: fixed
  by naming Card 103 as the implementation and Decision 53 as history.
- `[P2 final-099-002-doc]` the `52-57` shorthand and historical Sprint field
  still implied active Card 53 scope: fixed with an explicit open-card list and
  a non-executable historical-sprint label.
- `[P2 simp-001]` duplicated playback accounting: fixed with
  `_add_agent_playback_duration`.
- `[P2 DOC-99-001]` docs overstated the open engine: fixed and full scope raised
  as Card 103. Remaining documentation, diagnostics, format, and card-state P2
  findings were fixed in this card.
