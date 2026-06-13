# 18 - Record hybrid streaming voice runtime ADR

**Epic:** Architecture
**Estimated effort:** ~2 h
**State:** done

## Goal

Lock the runtime architecture for live conversations: a long-lived event plane
per call and a per-turn cognition graph, so streaming needs and
LangGraph-parity graphs stop competing for one execution model.

## Spec

ADR 0011 records: `VoiceSession` actor with the turn state machine and
barge-in edges; `AgentGraph` supersteps compiling to the existing
`GraphExecutor`; `LlmProvider.stream_chat` event union with real-time MCP tool
execution and filler masking; one `TurnDriver` abstraction for cascaded and
speech-to-speech; a versioned control-channel WebSocket schema that never
carries audio frames (ADR 0004); typed latency budgets; `CheckpointStore`;
no-mocks testing via an in-process gateway simulator and injectable clock.

## Files to create/modify

- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the decision
- `tests/test_architecture_adrs.py` - contract tests

## Definition of Done

- [x] ADR 0011 accepted naming both planes and all supporting contracts.
- [x] Contract tests assert planes, control channel, and checkpointing appear.
- [x] Targeted tests green (local venv; Docker daemon not running this session).
- [x] Post-task audit done

## Improvements noted

- Implementation milestones are cards 32-38; the control-channel schema must
  land first because the Rust gateway and all transports build against it.
