# 97 - Propagate graph control directives through live sessions

**Sprint:** S5 - Native telephony
**Epic:** Runtime / control channel
**Estimated effort:** ~4 h
**Depends on:** 39, 70
**State:** pending

## Goal

Deliver every typed downstream directive emitted by an AgentGraph node to the
media gateway. Today `GraphTurnDriver` forwards `TtsSpeak` but drops transfer,
dial, hold, DTMF, configuration, and session-end directives before transport.

## Context primer

- `agents.md` - project invariants and no-mocks policy.
- `src/lucy/drivers.py` - `DriverEvent` and `GraphTurnDriver` emission queue.
- `src/lucy/session.py` - live driver-event dispatch and speculative buffering.
- `src/lucy/transport/schema.py` - authoritative downstream wire models.
- `src/lucy/nodes/telephony.py` - graph nodes that emit control directives.
- `tests/test_prebuilt_graphs.py` - receptionist and escalation behavior exposed
  by card 39.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - plane boundary.

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
  order only after promotion. Cancellation before promotion must discard all
  buffered side effects.
- A `SessionEnd` directive must not deadlock while waiting for TTS playback.
- No control message may carry audio bytes.

## Files to create/modify

- `src/lucy/transport/schema.py` - typed downstream union/helper.
- `src/lucy/drivers.py` - graph directive queue and event contract.
- `src/lucy/session.py` - generic downstream dispatch.
- `tests/test_graph_control_directives.py` - live and speculative regressions.

## Chips

- [ ] **C1 - Lock the driver contract.** Write failing tests proving a graph
  yields `Transfer`, `Hold`, and `SessionEnd` in order before its terminal
  report. Implement the typed union and graph queue. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> driver tests pass.
- [ ] **C2 - Deliver directives through VoiceSession.** Add a real
  `LocalGatewaySimulator` scenario proving the same directives reach
  `gateway.directives` in order. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> live session test passes without wall-time sleeps.
- [ ] **C3 - Enforce speculative side-effect safety.** Add promotion and
  cancellation scenarios using `ManualClock`; promoted directives flush once,
  cancelled directives never leave the process. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
  -> all tests pass.
- [ ] **C4 - Full suite and bookkeeping.** Run all Docker gates, record review
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

- [ ] `docker compose run --rm lucy-api pytest tests/test_graph_control_directives.py -q`
      -> live, ordered, speculative, and cancellation cases pass.
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green.
- [ ] Docker ruff check, format check, and mypy are green.
- [ ] Runtime smoke test proves `Transfer` reaches
      `LocalGatewaySimulator.directives` through `ConversationHarness`.
- [ ] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If typed ordering or speculative safety cannot be preserved without changing
the wire contract, leave the card in `in_progress/`, record the failure under
"Improvements noted", and report it. Do not weaken the tests.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

- code-reviewer: PENDING
- test-auditor: PENDING
- docs-reviewer: PENDING
- simplicity-reviewer: PENDING
- security-reviewer: PENDING

Findings disposition:

<!-- Record every finding and its disposition. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
