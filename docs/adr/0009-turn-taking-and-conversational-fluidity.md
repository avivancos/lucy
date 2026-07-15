# ADR 0009 - Turn-Taking And Conversational Fluidity

## Status

Accepted

## Context

Lucy is a turn-based, voice-first conversational framework with a funnel. When this
decision was proposed, turn handling lived in the `RealtimeVoicePipeline` prototype.
The current event-plane runtime is `VoiceSession` in `src/lucy/session.py`; it owns
live turns, barge-in, speculative work, and streaming directives. This ADR formalizes
that lifecycle and fluidity toolset as the spine of the framework.

## Decision

The turn lifecycle is the spine of the framework: VAD and endpointing, STT partials,
speculative context and response, streaming TTS, and barge-in or interruption
handling.

The conversational fluidity toolset includes configurable endpointing and turn
detection, barge-in (implemented by `VoiceSession`), interruption styles (already in
`VoiceModulationSpec.interruption_style`), backchannels and fillers, and speculative
response on partial transcripts. Every stage is deadline-bounded through the
typed event-plane settings and cancellation owned by `VoiceSession`. Cognition
nodes run through `GraphExecutor` (`src/lucy/runtime.py`), which applies their
deadlines and fallbacks.

Each completed turn feeds the funnel (`FunnelStage` and `FunnelEvent`) and emits a
per-turn `LatencyWaterfall`, keeping the framework turn-based and funnel-driven.

## Consequences

- Event-plane lifecycle controls remain framework-owned streaming behavior;
  cognition stages map to `GraphNode`s with deadlines and fallbacks in
  `src/lucy/runtime.py`.
- Funnel transitions are turn outcomes; the framework stays turn-based and funnel-
  driven.
- Fluidity features are configuration in the voice spec, not hardcoded thresholds.
