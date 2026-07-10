# ADR 0011 - Hybrid Streaming Voice Runtime

## Status

Accepted

## Context

The current `GraphExecutor` (`src/lucy/runtime.py`) is a per-invocation DAG and
the current `RealtimeVoicePipeline` (`src/lucy/voice.py`) is batch
request/response. A live call is neither: audio events push continuously for
minutes, while reasoning happens in bounded bursts. Pipecat-style frame actors
solve a throughput problem Lucy does not have (ADR 0004 keeps audio frames out
of Python), and a pure per-turn DAG cannot express barge-in, speculation, or
sentence-streaming TTS.

## Decision

The runtime is a hybrid of two planes with different execution models:

- **Event plane (long-lived):** one `VoiceSession` actor task per call owns the
  duplex control channel, the turn state machine
  (idle -> listening -> thinking -> speaking, with barge-in edges), speculation
  on partial transcripts, sentence-level TTS flushing, and cancellation. This
  layer is framework-owned streaming code, not a user graph.
- **Cognition plane (per-turn):** a user-authored `AgentGraph` (typed state,
  conditional edges, checkpoints) runs once per turn. Each superstep compiles to
  the existing `GraphExecutor`, reusing deadlines, retries, fallbacks, and
  cancellation propagation unchanged. A default three-node graph
  (context synthesis -> llm -> finalize funnel) ships so simple agents need no
  graph authoring.

Supporting contracts:

- `LlmProvider.stream_chat()` yields a typed event union (token deltas,
  tool-call deltas, ready tool calls, usage, stream end). Tool calls execute
  through the existing `McpClient` (permissions, schemas, audit) wrapped by a
  tool executor with per-tool profiles (deadline, barge-in policy, filler
  utterances that mask tool latency).
- Cascaded (STT -> graph -> TTS) and speech-to-speech (realtime models) run
  behind one `TurnDriver` abstraction; tools, spans, waterfalls, and the control
  channel schema are identical for both.
- The control channel between the media plane and Python is a versioned
  WebSocket protocol carrying events and directives only - never audio frames
  (ADR 0004). The media plane forks audio directly to STT/TTS providers.
- Latency budgets live in typed settings, never hardcoded. Speculative
  mechanisms (RAG prefetch on partials, speculative LLM start on stable
  partials with abort-on-revision, sentence-streaming TTS, prompt caching) are
  configuration-gated.
- Conversation state (transcript, funnel stage, slots, tool results) is
  checkpointed per superstep behind a `CheckpointStore` protocol. The
  in-memory default and optional Postgres/Redis adapters key history by stable
  `thread_id` while retaining each telephony `session_id`, enabling cross-call
  resume, mid-call handoff, and post-call replay.

Testing follows ADR 0003: an in-process gateway simulator implements the same
control-channel schema, scripted by the synthetic scenarios in
`src/lucy/evals.py`, with an injectable clock for deterministic deadline tests.

## Consequences

- `RealtimeVoicePipeline` and the batch provider calls are deprecated in favor
  of the session runtime once it lands; event dataclasses are kept and extended.
- The control-channel schema becomes a public contract: every transport
  (gateway, CPaaS adapter, PBX adapter, simulator) is an adapter that implements
  it, and the Rust gateway is developed against it.
- Per-turn `LatencyWaterfall`, cost, and span trees are emitted through
  `lucy.observe`, which is what the closed platform consumes (ADR 0010).
