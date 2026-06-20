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

<!-- Fill during execution. -->
