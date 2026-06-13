# ADR 0009 - Turn-Taking And Conversational Fluidity

## Status

Accepted

## Context

Lucy is a turn-based, voice-first conversational framework with a funnel. It already
handles turns and barge-in (`RealtimeVoicePipeline` in `src/lucy/voice.py`) and a
funnel (`FunnelStage`, `FunnelEvent`). This ADR formalizes the turn lifecycle and the
fluidity toolset as the spine of the framework so a voice LLM agent feels fluid.

## Decision

The turn lifecycle is the spine of the framework: VAD and endpointing, STT partials,
speculative context and response, streaming TTS, and barge-in or interruption
handling.

The conversational fluidity toolset includes configurable endpointing and turn
detection, barge-in (already in `src/lucy/voice.py`), interruption styles (already in
`VoiceModulationSpec.interruption_style`), backchannels and fillers, and speculative
response on partial transcripts. Every stage is deadline-bounded through the
`GraphExecutor` (`src/lucy/runtime.py`).

Each completed turn feeds the funnel (`FunnelStage` and `FunnelEvent`) and emits a
per-turn `LatencyWaterfall`, keeping the framework turn-based and funnel-driven.

## Consequences

- Turn-lifecycle stages map to `GraphNode`s with deadlines and fallbacks in
  `src/lucy/runtime.py`.
- Funnel transitions are turn outcomes; the framework stays turn-based and funnel-
  driven.
- Fluidity features are configuration in the voice spec, not hardcoded thresholds.
