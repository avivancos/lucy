# 25 - Wire runtime instrumentation into the tracer

**Epic:** Observability
**Estimated effort:** ~4 h
**State:** pending

## Goal

Every existing runtime structure already produces structured data; route it
through `lucy.observe` so one tracer sees spans, turns, tools, and costs.

## Spec

- `GraphExecutor`: each `TraceEvent` also emits a `span` telemetry event
  (node name, status, latency, parent = turn) through an injected tracer
  (optional constructor arg; default global tracer; no behavior change when
  tracing is off).
- `RealtimeVoicePipeline`: each turn emits a `turn` event carrying
  `LatencyWaterfall`, barge-in flag, and provider timeout events.
- `McpClient`: every audit-log append also emits `tool_call` (post-redaction
  arguments).
- Cost accounting: `CostBreakdown` emission helper produces `cost` events.

## Files to create/modify

- `src/lucy/runtime.py` - tracer injection, span emission
- `src/lucy/voice.py` - turn emission
- `src/lucy/mcp.py` - tool_call emission
- `src/lucy/metrics.py` - cost event helper
- `tests/test_observability.py` - end-to-end: run a graph + pipeline turn with
  `InMemoryTraceExporter`, assert the span tree and event kinds

## Definition of Done

- [ ] One pipeline turn produces session/turn/span/tool_call/cost events with
      correct parent links.
- [ ] Zero overhead path verified when tracing disabled (no queue, no export).
- [ ] Full test suite green.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

A 16-agent adversarial review of the diff confirmed all six spec requirements
(correct parent links, no barge-in/session leakage, no event built before the
enabled check, no import cycle, redaction post-enqueue) and surfaced:

Fixed in this card:

- Zero-overhead-when-off via the global-default path: `configure()` now skips
  exporter construction and entry-point discovery when `LUCY_TRACING=0`, so
  resolving a disabled global tracer is cheap (regression-tested by
  `test_disabled_global_tracer_skips_exporter_discovery`).
- A hard node failure (no fallback, retries exhausted) now emits an `error`
  span before raising, so the most important failure case is observable
  (`GraphExecutor._emit_span`; `test_graph_executor_emits_error_span_*`).
- Added an autouse test fixture resetting the process-global tracer between
  tests to prevent cross-test console/state leakage.

Deferred to card 26 (the VoiceAgent facade owns the unified turn and session
lifecycle) - explicit spec bullets added there:

- TTS path is uninstrumented: `synthesize_response` emits no turn event and TTS
  provider timeouts never reach telemetry; the turn `LatencyWaterfall` is
  STT-only. The facade assembles STT+LLM+MCP+TTS into one turn and should
  aggregate all provider timeouts and populate the full waterfall.
- `session.started`/`session.ended` are not emitted by any runtime component;
  the facade's `start_session()`/session close should emit them.

Not changed (intentional): the DAG abort path does not emit `cancelled` spans
for in-flight sibling nodes - low value vs. complexity; revisit if the local
trace viewer (card 31) needs it.
