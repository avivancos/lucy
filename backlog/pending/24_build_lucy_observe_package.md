# 24 - Build lucy.observe instrumentation package

**Epic:** Observability
**Estimated effort:** ~6 h
**State:** pending

## Goal

Turn the single-purpose OTel bridge into the open/closed telemetry seam: one
instrumentation API, pluggable exporters, client-side privacy, env-var
configuration (wire spec card 19).

## Spec

Replace `src/lucy/observe.py` with package `src/lucy/observe/`:

- `events.py`: `TelemetryEvent` union mirroring the wire spec (session.started,
  session.ended, turn, span, cost, business, tool_call, transcript, audio_ref).
- `exporters.py`: `TraceExporter` protocol (`export_batch`), `ConsoleExporter`
  (pretty per-turn waterfall), `JsonlFileExporter`, `OtlpBridgeExporter`
  (wraps today's `OtelExporterBridge`); `InMemoryTraceExporter` in
  `lucy.testing`.
- `redact.py`: PII pass over transcripts and tool arguments, applied before
  any exporter; honors `ObservabilitySpec.redact_pii/record_audio/
  trace_sample_rate` plus a transcripts kill-switch.
- `__init__.py`: `configure(*, exporters=None, sample_rate=None,
  redact_pii=None) -> Tracer`; env handling per the wire spec; bounded queue,
  fail-open with drop counter; deterministic session head-sampling.
- Exporter discovery via entry-point group `lucy.exporters` so `lucy-cloud`
  can auto-attach later (card 30).

Backwards compatibility: `ObservabilityEvent`, `OtelSpanExporter`,
`OtelExporterBridge` keep working (re-exported), existing tests keep passing.

## Files to create/modify

- `src/lucy/observe/{__init__.py,events.py,exporters.py,redact.py}` - new
- `src/lucy/observe.py` - becomes the package (shim removed)
- `tests/test_observability.py` - extend: exporters, redaction, sampling,
  fail-open drop counter, env configuration

## Definition of Done

- [ ] `configure()` with no args yields console tracing; `LUCY_TRACE_FILE`
      yields JSONL matching the wire spec event shapes.
- [ ] Redaction provably runs before export (test with PII fixture).
- [ ] Exporter failures never raise into caller code; drops are counted.
- [ ] Full test suite green.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
