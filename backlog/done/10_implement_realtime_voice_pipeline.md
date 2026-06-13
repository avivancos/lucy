# 10 - Implement realtime voice pipeline

**Epic:** Voice
**Estimated effort:** ~10 h
**State:** done

## Goal

Create the first realtime voice pipeline contracts for sales and booking agents.

## Spec

Model the pipeline as audio ingress, VAD, streaming STT, intent/funnel
classification, RAG prefetch, LLM response, MCP tool calls, streaming TTS, and
barge-in handling.

## Files to create/modify

- `src/lucy/voice.py` - voice pipeline contracts
- `tests/test_voice_pipeline.py` - synthetic pipeline tests

## Definition of Done

- [x] Partial transcription events can flow through the pipeline.
- [x] Barge-in can cancel an active TTS response.
- [x] Turn-level latency events are emitted.
- [x] No provider calls are performed; tests exercise the local deterministic pipeline.
- [x] Targeted tests green in Docker Compose
- [x] Post-task audit done

## Improvements noted

- Add provider-backed STT/TTS adapter contract tests with local protocol
  simulators after the voice provider interfaces are expanded.
