# 10_2 - Add voice provider deadline simulation

**Epic:** Voice
**Estimated effort:** ~4 h
**State:** done

## Goal

Make voice provider deadline behavior explicit at the STT/TTS adapter boundary.

## Spec

Add local provider simulators that can delay transcript or audio chunks, then
verify the realtime voice pipeline applies per-provider deadlines, cancels late
streams, and emits traceable timeout events.

## Files to create/modify

- `src/lucy/voice.py` - deadline-aware voice provider behavior
- `tests/test_voice_pipeline.py` - timeout simulation tests

## Definition of Done

- [x] STT timeout behavior is tested with local provider behavior.
- [x] TTS timeout behavior is tested with local provider behavior.
- [x] Timeout events are traceable and typed.
- [x] Tests use no mocks or external network.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add provider-specific timeout budgets to `VoiceSpec` once deployment config
  persistence exists.
