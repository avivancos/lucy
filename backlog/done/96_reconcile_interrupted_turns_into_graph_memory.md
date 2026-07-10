# 96 - Reconcile interrupted turns into graph memory

**Sprint:** S4 - Graph and state
**Epic:** Conversation memory correctness
**Estimated effort:** ~6 h
**Depends on:** 35, 37
**State:** done

## Goal

Keep graph memory aligned with what the caller actually heard. A barge-in or
thinking-phase cancellation must not lose the caller turn, retain unspoken
assistant text, duplicate prior messages, or reuse a graph turn identifier.

## Context primer

- `agents.md` - cancellation, no-mocks, and checkpoint testing rules.
- `src/lucy/session.py` - authoritative playback-mark reconstruction and
  per-session LLM history.
- `src/lucy/drivers.py` - `GraphTurnDriver` state adoption and turn numbering.
- `src/lucy/state.py` - serializable transcript and checkpoint state.
- `tests/test_interruption.py` - existing heard-prefix and cancellation cases.

## Spec

`GraphTurnDriver` captures an immutable transcript/turn-count base when it is
created or resumed. Add an optional concrete-driver method
`reconcile_history(history: Sequence[LlmMessage]) -> None`; this does not alter
the frozen `TurnDriver` Protocol. It ignores system/tool messages, converts
user/assistant messages into `TranscriptLine`s, and replaces the current
session suffix over the captured base. The resulting turn count is base turns
plus caller messages in the session history.

`VoiceSession` calls the optional reconciliation hook after it reconstructs
and appends the authoritative heard assistant text. The hook must run for
normal completion, speaking barge-in, thinking interruption, and channel-close
finalization. The next graph turn therefore receives the exact heard prefix
and a monotonically increasing turn id.

When `GraphTurnDriver` receives a speculative `TurnContext`, it must share the
promotion/cancellation controls with its internal graph context and defer state
adoption until promotion. A revised partial is cancelled without adding its
user text, answer, tools, or turn count to the final request history.
`TurnContext.current_user_in_state` explicitly marks the graph-driver path so
the default graph removes that state copy before the inner cascaded driver
appends the current user exactly once; repeated utterance text is never used as
an implicit deduplication signal.

Durable persistence of a correction that occurs after the last graph
superstep remains card 69 scope; this card fixes the live in-process state and
records that dependency explicitly.

## Files to create/modify

- `src/lucy/drivers.py` - session-base snapshot and history reconciliation.
- `src/lucy/session.py` - optional post-finalization hook.
- `tests/test_graph_interruption_memory.py` - speaking/thinking integration
  tests through the real local gateway simulator.
- `src/lucy/llm.py`, `tests/test_speculation.py` - deterministic request
  inspection and speculative graph-state regression.
- `src/lucy/runtime.py`, `src/lucy/graph.py` - typed current-user ownership
  marker and exact-once request assembly.
- `backlog/sprints.md` - S4 mapping and card 69 dependency note.

## Chips

- [x] **C1 - Heard-prefix reconciliation.** Write a failing graph-session
  test where the generated answer exceeds the played prefix, then reconcile
  state from live history. Files: `src/lucy/drivers.py`, `src/lucy/session.py`,
  `tests/test_graph_interruption_memory.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_interruption_memory.py -q -k speaking`
  -> selected tests pass.
- [x] **C2 - Thinking cancellation, speculation, and turn identity.** Add
  negative tests proving a cancelled unspoken turn is retained, a revised
  speculative partial is discarded before the final prompt, and the next
  graph turn id is not reused. Files: `src/lucy/drivers.py`, `src/lucy/llm.py`,
  `tests/test_graph_interruption_memory.py`, `tests/test_speculation.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_graph_interruption_memory.py -q`
  -> all interruption-memory tests pass.
- [x] **C3 - Regression gates and bookkeeping.** Run full Docker gates,
  complete review evidence, and move this card to `done/`. Files:
  `backlog/in_progress/96_reconcile_interrupted_turns_into_graph_memory.md`.
  Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or monkeypatch provider behavior; use Lucy simulators.
- Do not hardcode provider names, latency thresholds, or turn limits.
- Do not change the frozen `TurnDriver` Protocol.
- Do not append a second final checkpoint kind or weaken replay ordering;
  durable corrected checkpoints belong to card 69.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_graph_interruption_memory.py -q`
      -> speaking and thinking interruption memory is exact.
- [x] `docker compose run --rm lucy-api pytest` plus Docker ruff, format, and
      mypy -> all gates green.
- [x] Post-task audit done; durable correction is explicitly handed to card 69.

## Failure protocol

If reconciliation cannot distinguish the current session suffix without a
public ABI change, leave the card in `in_progress/`, record the ambiguous
sequence, and do not guess from repeated utterance text.

## Improvements noted

- The default graph was sending the current caller message twice because it
  existed in state and the cascaded driver appended it again. A typed context
  ownership marker now removes exactly the state copy without comparing text.
- A speculative graph could adopt a partial transcript before promotion. The
  internal graph now shares promotion/cancellation controls and defers state
  adoption, so a revision cannot contaminate the final prompt or execute tools.
- The current checkpoint protocol cannot append a corrected final state after
  playback without violating replay's one-final-per-turn ordering. Card 69 now
  requires durable heard-state correction as part of its persistent store seam.

## Review evidence

- code-reviewer: PASS - authoritative heard history replaces only the current
  session suffix; turn ownership, cancellation, and provider Protocols remain
  correct and unchanged.
- test-auditor: PASS - real local graph/session simulations cover speaking
  barge-in, thinking interruption, duplicate-user prevention, and speculative
  revision before the final prompt; full suite is green.
- docs-reviewer: PASS - sprint exit criteria and card 69 now describe live and
  durable reconciliation responsibilities without contradicting ADR 0011.
- simplicity-reviewer: PASS - one concrete optional hook plus one typed context
  flag replaces text heuristics and avoids a new public protocol.
- security-reviewer: NOT_APPLICABLE - transcript state remains in-process; no
  telemetry, secret, MCP permission, route, or public export changed.

Findings disposition:

- [P1][code-001] full generated answer survived speaking barge-in - fixed.
- [P1][code-002] thinking cancellation lost a turn and reused its id - fixed.
- [P1][code-003] speculative graph state reached the revised final prompt - fixed.
- [P1][code-004] current caller message appeared twice in graph LLM requests - fixed.
- [P2][code-005] corrected playback state is not immediately durable - assigned
  to card 69 with an explicit restart-after-barge-in acceptance test.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
