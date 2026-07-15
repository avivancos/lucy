# ADR 0011 - Hybrid Streaming Voice Runtime

## Status

Accepted

## Context

When this decision was proposed, `GraphExecutor` (`src/lucy/runtime.py`) was a
per-invocation DAG and `RealtimeVoicePipeline` (`src/lucy/voice.py`) was a batch
request/response prototype. A live call is neither: audio events push
continuously for minutes, while reasoning happens in bounded bursts.
Pipecat-style frame actors solve a throughput problem Lucy does not have (ADR
0004 keeps audio frames out of Python), and a pure per-turn DAG cannot express
barge-in, speculation, or sentence-streaming TTS. `VoiceSession` now implements
the event-plane runtime selected by this ADR.

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
  (ADR 0004). The media plane forks audio directly to STT/TTS providers. Every
  registered Python-to-gateway payload belongs to the typed
  `DownstreamDirective` union and leaves a graph in emission order; graph-local
  events and audio-like objects never enter that contract. TTS remains on its
  playback and usage-accounting path while sharing ordering with other controls.
- Latency budgets live in typed settings, never hardcoded. Speculative
  mechanisms (RAG prefetch on partials, speculative LLM start on stable
  partials with abort-on-revision, sentence-streaming TTS, prompt caching) are
  configuration-gated. A speculative graph buffers every downstream directive
  until promotion. Graph controls require an exact normalized final transcript;
  a prefix extension or revision cancels the partial graph, discards its queued
  side effects, and reruns from the final transcript.
- `SessionEnd` and `TtsStreamEnd` are terminal within a graph turn: any later
  downstream directive is rejected. `SessionEnd` bypasses playback waiting,
  ends the local gateway call without a runtime-owned trailing stream-end, and
  prevents later scenario turns. Closing a driver stream for any reason cancels
  and awaits its graph invocation and queue waiter.
- Session shutdown cancels active turn and speculative prefetch tasks, but
  bounds cooperative cleanup to a named number of event-loop turns. A task that
  ignores cancellation is detached, retained in supervised accounting until it
  finishes, and reported through a `session.task_cleanup` error span. A
  `VoiceSession` is single-use; cancelled turns and closed sessions cannot emit
  later TTS directives or dispatch new MCP calls. The session latches the turn
  cancellation capability before task cancellation; cascaded and realtime
  drivers check it again at the MCP transport dispatch boundary. An
  already-started transport commit resolves within the
  typed `control_commit_ms` budget or the runtime invokes the transport's
  abort/close seam before shutdown returns. Transport adapters must make abort
  idempotent and prevent a timed-out commit from becoming visible later. Adopted MCP tasks whose policy is
  `run_to_completion` are not cancelled at session end and retain their normal
  result or sanitized terminal error audit path, with bounded admission per
  session. Capacity overflow cancels the rejected task under detached
  supervision and ends the live session. Results that arrive after session completion never mutate returned
  turn records. Cleanup failures are reported
  and retained as the context of a primary session cancellation instead of
  replacing it.
- Conversation state (transcript, funnel stage, slots, tool results) is
  checkpointed per superstep behind a `CheckpointStore` protocol. The
  in-memory default and optional Postgres/Redis adapters key history by stable
  `thread_id` while retaining each telephony `session_id`, enabling cross-call
  resume, mid-call handoff, and post-call replay.

Testing follows ADR 0003: an in-process gateway simulator implements the same
control-channel schema, scripted by the synthetic scenarios in
`src/lucy/evals.py`, with an injectable clock for deterministic deadline tests.

## Consequences

- `VoiceSession` is the live runtime. The former `RealtimeVoicePipeline` has been
  removed; retained event dataclasses evolve behind the current session API.
- The control-channel schema becomes a public contract: every transport
  (gateway, CPaaS adapter, PBX adapter, simulator) is an adapter that implements
  it, and the Rust gateway is developed against it.
- Per-turn `LatencyWaterfall`, cost, and span trees are emitted through
  `lucy.observe`, which is what the closed platform consumes (ADR 0010).
