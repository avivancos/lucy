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

<!-- Fill during execution. -->
