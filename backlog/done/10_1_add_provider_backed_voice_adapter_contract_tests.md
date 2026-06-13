# 10_1 - Add provider-backed voice adapter contract tests

**Epic:** Voice
**Estimated effort:** ~5 h
**State:** done

## Goal

Extend the realtime voice pipeline beyond deterministic synthetic chunks by
defining provider-backed STT and TTS adapter contracts without mocks.

## Spec

Add local in-process STT and TTS protocol simulators that exercise partial
transcripts, streaming audio output, provider timeout, malformed payload, and
cancellation. The simulators must implement the same adapter contracts as real
providers and must not rely on a mocking framework.

## Files to create/modify

- `src/lucy/voice.py` - provider adapter protocols and pipeline integration
- `tests/test_voice_pipeline.py` - provider contract tests

## Definition of Done

- [x] Local STT simulator emits partial and final transcript events.
- [x] Local TTS simulator emits stream lifecycle events.
- [x] Timeout and malformed provider payload behavior are tested.
- [x] Barge-in cancels active local TTS simulator streams.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add explicit timeout simulation for provider streams once the voice pipeline
  owns per-provider deadlines.
