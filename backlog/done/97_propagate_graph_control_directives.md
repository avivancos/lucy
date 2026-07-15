# 97 - Propagate graph control directives through live sessions

**Sprint:** S5 - Native telephony
**Epic:** Runtime / control channel
**Estimated effort:** ~4 h
**Depends on:** 39, 70
**State:** done

## Goal

Deliver every typed downstream directive emitted by an AgentGraph node to the
media gateway. Before this card, `GraphTurnDriver` forwarded `TtsSpeak` but dropped transfer,
dial, hold, DTMF, configuration, and session-end directives before transport.

## Context primer

- `agents.md` - project invariants and no-mocks policy.
- `src/lucy/drivers.py` - `DriverEvent` and `GraphTurnDriver` emission queue.
- `src/lucy/session.py` - live driver-event dispatch and speculative buffering.
- `src/lucy/runtime.py` - typed `TurnContext` speculative directive buffer.
- `src/lucy/transport/schema.py` - authoritative downstream wire models.
- `src/lucy/nodes/telephony.py` - graph nodes that emit control directives.
- `tests/test_prebuilt_graphs.py` - receptionist and escalation behavior exposed
  by card 39.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - plane boundary.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - `TurnContext`, live
  session, and control-channel ownership.

## Spec

- Define one typed downstream-directive union from the registered v1 schema;
  do not use `object` as the public driver-event contract.
- Extend `DriverEvent` so `GraphTurnDriver` yields all downstream directives in
  graph emission order, followed by exactly one `TurnDriverReport`.
- Keep `CascadedTurnDriver` behavior unchanged.
- Dispatch non-TTS directives in `VoiceSession` with the correct wire type,
  session id, turn id, and ordering. `TtsSpeak` continues through its playback
  accounting path.
- During speculative turns, buffer every downstream directive and release it in
  order only after promotion. Graph turns containing controls require an exact
  normalized final transcript; prefix-only matches are cancelled and rerun from
  the final transcript. Cancellation before promotion must discard all buffered
  side effects.
- `RecordingStart` and `RecordingStop` require a one-use, session-bound claim
  from the configured `RecordingCoordinator`; raw, disabled, unsampled,
  consentless, cross-session, and replayed controls fail closed.
- `SessionEnd` and `TtsStreamEnd` are terminal for graph-emitted directives.
  Reject any downstream directive emitted after either terminal control.
- A `SessionEnd` directive must not deadlock while waiting for TTS playback.
- No control message may carry audio bytes.

## Files to create/modify

- `src/lucy/transport/schema.py` - typed downstream union/helper.
- `src/lucy/drivers.py` - graph directive queue and event contract.
- `src/lucy/session.py` - generic downstream dispatch.
- `src/lucy/runtime.py` - typed speculative directive buffer.
- `src/lucy/recording.py` - session-bound recording-control authorization.
- `src/lucy/transport/dev_gateway.py` - local terminal-directive handling.
- `tests/test_graph_control_directives.py` - live and speculative regressions.
- `tests/test_recording.py` - live upload/failure lifecycle regressions.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - directive guarantees.
- `docs/adr/0014-media-plane-recording.md` - recording control capabilities.

## Chips

- [x] **C1 - Lock the driver contract.** Write failing tests proving a graph
  yields `Transfer`, `Hold`, and `SessionEnd` in order before its terminal
  report. Implement the typed union and graph queue. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> driver tests pass. Result: 88 passed, including all 13 downstream types,
  registry/union drift, terminal ordering, and exactly-once stream-end coverage.
- [x] **C2 - Deliver directives through VoiceSession.** Add a real
  `LocalGatewaySimulator` scenario proving the same directives reach
  `gateway.directives` in order. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> live session test passes without wall-time sleeps. Result: `Transfer`,
  `Hold`, and `SessionEnd` arrived with registered wire names and identities.
- [x] **C3 - Enforce speculative side-effect safety.** Add promotion and
  cancellation scenarios using `ManualClock`; promoted directives flush once,
  cancelled directives never leave the process. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> all tests pass. Result: promotion flushes once in order; revision emits
  only the final turn's controls.
- [x] **C4 - Full suite and bookkeeping.** Run all Docker gates, record review
  evidence, and move the card to `done/`. Verify:
  `docker compose run --rm lucy-api pytest` -> full suite passes.

## Do NOT

- Do not use mocks or mocking frameworks; use AgentGraph,
  `LocalGatewaySimulator`, and `ManualClock`.
- Do not hardcode wire names, timeouts, provider identifiers, or retry budgets;
  use the schema registry and typed settings.
- Do not bypass the versioned control-channel schema or infer wire type names
  from class-name string manipulation.
- Do not send audio through Python or widen telemetry payloads.
- Do not execute speculative side effects before promotion.
- Do not touch provider adapters or telephony media implementations.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
      -> 88 passed; live, ordered, speculative, cancellation, recording-policy,
      timeout, and terminal cases pass.
- [x] Related regression set -> 231 passed (including recording and dev-gateway behavior).
- [x] `docker compose run --rm lucy-api pytest` -> 1,259 passed, 2 deselected.
- [x] Docker ruff check, format check, and mypy are green.
- [x] Runtime smoke test proves `Transfer` reaches
      `LocalGatewaySimulator.directives` through `ConversationHarness`.
- [x] Clean Compose image build and `/health` smoke pass on isolated port 18097.

## Failure protocol

If typed ordering or speculative safety cannot be preserved without changing
the wire contract, leave the card in `in_progress/`, record the failure under
"Improvements noted", and report it. Do not weaken the tests.

## Improvements noted

- Graph emission also carries internal typed events such as `FunnelEvent`.
  `GraphTurnDriver` now selects downstream controls through the schema registry
  and keeps graph-local events off the wire instead of treating every emission
  as a control message.
- The downstream union is guarded by a drift test against `DOWNSTREAM_TYPES`,
  and reverse wire-name lookup comes from the same registry rather than class
  name inference.
- Promotion keeps the context speculative while its queue drains, so controls
  released by work unblocked at promotion cannot overtake earlier buffered
  directives.
- Mixed TTS and control directives share the same ordered path through the
  driver, live dispatch, and speculative promotion; TTS still uses its special
  playback and character-accounting path.
- A graph-emitted `TtsStreamEnd` marks the turn barrier, preserving the existing
  exactly-once invariant, and a committed `SessionEnd` bypasses playback wait.
- Graph speculative promotion now requires exact normalized text, preventing
  irreversible controls computed from a partial transcript from surviving a
  longer or revised final transcript.
- Recording controls are accepted only when the same coordinator prepared the
  plan for the live session, explicitly issued any stop, and atomically claims
  its first dispatch. Failed
  or cancelled transport outcomes retain the claim because commit status can be
  ambiguous; cross-session and replayed controls fail closed.
- Both graph and session boundaries reject downstream controls after
  `SessionEnd` or `TtsStreamEnd`; internal events and audio-like objects remain
  outside `DriverEvent`.
- Terminal state is latched before transport commit, so a timed-out
  `SessionEnd` or `TtsStreamEnd` still blocks all later graph side effects.
- Runtime cleanup emits no control after graph `SessionEnd`; the local gateway
  treats it as the call terminator, emits `session.ended` with the graph reason,
  and never starts a later scenario turn.
- Closing a driver stream for any reason cancels and awaits both the graph
  invocation and its queue waiter, preventing rejected controls from leaving
  graph work or node side effects running in the background.
- Recording controls are identity capabilities rather than structurally
  equivalent payloads. Clones and stale controls cannot consume an issued
  claim, including after the same opaque recording id is reused by a new plan.
- Live sessions route `recording.uploaded` and `recording.failed` back to their
  coordinator. Real local HTTP regressions prove successful uploads emit
  `audio_ref` and failures clean prepared storage without retaining the plan.
- Recording completion and failure callbacks require the receiving session id
  to match the plan owner, preventing shared coordinators from confirming or
  deleting another live session's recording.
- Claimed-control replay protection uses weak identity references: ambiguous
  dispatches remain denied while the original control exists without retaining
  completed control objects for the coordinator lifetime.
- No follow-up card is required; the three existing FastAPI/httpx deprecation
  warnings remain assigned to launch-hygiene Card 93.

## Review evidence

- code-reviewer: PASS (`019f66e3-b03f-7132-9a28-58e2fbac9ba8`); 140
  scoped and 1,259 full tests passed independently.
- test-auditor: PASS (`019f66e3-b643-71c0-ad84-8f5ffe2e0501`); 140
  scoped tests and unchanged cascaded-driver coverage passed.
- docs-reviewer: PASS via fallback reviewer
  (`019f66e9-94be-7b50-b5f3-b0dc984efc7c`); the configured
  `gpt-5.4-nano` role was unavailable for this account. ADR 0011, ADR 0014,
  and card evidence match the implementation.
- simplicity-reviewer: PASS (`019f66e3-ba42-7440-9989-82a2b168e050`);
  140 scoped tests passed and no unnecessary abstraction remained.
- security-reviewer: PASS (`019f66e3-bf57-70a3-a0b7-12dfe810253e`);
  session ownership, client-side telemetry controls, secrets, injection, and
  public-surface boundaries were reviewed with no remaining finding.
- final-integrator: PASS (`019f66ec-8567-78b2-a7d3-3a4f894b9f5d`); all gates,
  specialist verdicts, prior findings, image hashes, and focused commit scope
  were reconciled with no blocking evidence gap.

Findings disposition:

- Fixed: graph `SessionEnd` no longer receives a trailing runtime
  `TtsStreamEnd`, and `LocalGatewaySimulator` terminates the call instead of
  starting a later scenario turn.
- Fixed: recording start/stop controls use exact-instance, one-use,
  session-bound capabilities. Structural clones, unissued stops, replay,
  cross-session controls, and stale controls after id reuse are rejected.
- Fixed: recording claims latch before transport and remain consumed after
  cancellation or timeout because commit status is ambiguous.
- Fixed: speculative tests cover every registered directive, normalized exact
  promotion, prefix revision, mixed TTS/control ordering, cancellation discard,
  and TTS character accounting.
- Fixed: closing `GraphTurnDriver` on a dispatch error now cancels and awaits
  both the graph invocation and queue waiter; the regression observes node
  cancellation and no late side effect.
- Fixed: live `recording.uploaded` and `recording.failed` events reach the
  coordinator through real local HTTP success/failure tests.
- Fixed: recording completion and failure require the receiving session to own
  the plan, preventing cross-session confirmation, telemetry, or deletion.
- Fixed: ADR 0011 documents the typed directive, exact promotion, terminal, and
  cleanup guarantees; ADR 0014 documents recording claims and lifecycle routing.
- Fixed: stale focused/related/full counts were replaced with the final
  88/231/1,259 evidence, and C4 remained open until all reviews completed.
- Accepted without follow-up: `requires_exact_speculative_promotion` remains a
  driver capability flag so `VoiceSession` does not depend on a concrete graph
  driver class.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
