# 26 - Add VoiceAgent facade and curated public API

**Epic:** SDK surface
**Estimated effort:** ~5 h
**State:** pending

## Goal

Give the SDK a face: build a working voice agent in under 30 lines, with zero
API keys, using only documented top-level imports.

## Spec

- `src/lucy/agent.py`: `VoiceAgent(spec: LucySpec)` resolves
  `VoiceSpec.stt_provider`/`tts_provider` strings (bare name `local` resolves
  to `lucy.testing` simulators; plugin resolution arrives with card 28),
  builds `RealtimeVoicePipeline` + `GraphExecutor`, and routes turns through
  the tracer. `start_session(session_id)` returns a session handle with
  `user_audio(chunk) -> list[VoiceEvent]` and `synthesize(text)`.
- `src/lucy/__init__.py` exports exactly: the spec models, `VoiceAgent`,
  `GraphExecutor`/`GraphNode`/`GraphContext`, voice contracts and events,
  `McpClient`, `ModelRegistry`/`Capability`, and `configure` (re-export from
  `lucy.observe`). Everything else stays reachable but unadvertised.
- `examples/quickstart_voice_agent.py`: <30 lines, runs offline on simulators,
  prints transcript/TTS events and a console trace.
- Session lifecycle (deferred here from card 25): emit `session.started` on
  `start_session(session_id)` and `session.ended` on session close through the
  tracer.
- Unified turn (deferred here from card 25): assemble one `turn` event per
  caller turn spanning STT+LLM+MCP+TTS - populate the full `LatencyWaterfall`
  (not STT-only) and aggregate provider timeout events from both
  `handle_audio_turn` and `synthesize_response`. Card 25 instrumented the STT
  half only; `synthesize_response` currently emits no turn event and its TTS
  provider timeouts never reach telemetry.

## Files to create/modify

- `src/lucy/agent.py` - new facade
- `src/lucy/__init__.py` - curated exports
- `examples/quickstart_voice_agent.py` - runnable quickstart
- `tests/test_agent_facade.py` - facade builds from spec, runs a turn on
  simulators, emits telemetry, rejects unknown provider strings

## Definition of Done

- [ ] Quickstart runs with no network and no keys, printing events + trace.
- [ ] `import lucy; dir(lucy)` matches the curated surface.
- [ ] Unknown provider string raises a clear error naming known providers.
- [ ] Full test suite green.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

An 11-agent adversarial review of the facade diff confirmed the curated surface,
provider resolution, and DoD fidelity, and surfaced fixes (all applied):

- Session state machine hardened: `user_audio`/`synthesize` now raise once the
  session is closed (no orphan turn enqueued after `session.ended`), and
  `synthesize` requires an open turn instead of fabricating a phantom turn that
  inflated `turn_index`. Covered by `test_turn_methods_raise_after_close` and
  `test_synthesize_without_open_turn_raises`.
- `dir(lucy)` now literally equals the curated surface via a module `__dir__`
  (internal submodules no longer leak into `dir()`/IDE autocomplete).
- `user_audio` no longer returns the internal `TurnLatencyEvent` (already folded
  into the unified turn event); the caller stream is transcripts + timeouts.
- Quickstart trimmed under 30 lines; the test now asserts the bound.
- Added a README Quickstart section pointing at the runnable example.

Deferred (documented, not blocking):

- The facade's unified `turn` always reports `interrupted=False`. The pipeline
  tracks barge-in internally, but `user_audio` calls `handle_audio_turn` without
  a turn id (to suppress the pipeline's own turn emission), so the flag is not
  read back. Wiring it needs a session-level barge-in entry point, which is
  beyond this card's `user_audio`/`synthesize` API - revisit when barge-in
  handling lands in the runtime milestones (S3).
