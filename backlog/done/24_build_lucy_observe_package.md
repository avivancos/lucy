# 24 - Build lucy.observe instrumentation package

**Epic:** Observability
**Estimated effort:** ~6 h
**State:** done

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

- [x] `configure()` with no args yields console tracing; `LUCY_TRACE_FILE`
      yields JSONL matching the wire spec event shapes. (Smoke-verified.)
- [x] Redaction provably runs before export (test with PII fixture):
      `test_redaction_runs_before_export` + smoke (`jane@acme.com` -> `[REDACTED]`
      in the written JSONL).
- [x] Exporter failures never raise into caller code; drops are counted:
      `test_exporter_failure_is_fail_open_and_counts_drops`.
- [x] Full test suite green (`docker compose run --rm lucy-api pytest` -> 131 passed).
- [x] Targeted tests green in Docker Compose.
- [x] Post-task audit done (3-lens adversarial review: all pass, only nits).

## Improvements noted

- Redaction scope follows the wire spec: only `transcript.text` and
  `tool_call.arguments` (string values) are auto-redacted. NOT auto-redacted
  (caller responsibility per spec): `span.attributes`, `business` fields,
  `tool_call.error`, `session.started` fields, and nested/non-string argument
  values. Follow-up candidate: recursive redaction of nested `tool_call`
  arguments and optional span-attribute redaction.
- `configure()` reads `LUCY_TRACING` / `LUCY_TRACE_FILE` / `LUCY_TRACE_SAMPLE`.
  `record_audio` and `transcripts_enabled` are `Tracer` args with safe defaults
  (audio off, transcripts on), not yet env-overridable; a later card can wire
  `ObservabilitySpec` -> `configure` for them.
- The exporter contract is `TraceExporter.export_batch(events)` (batch-oriented,
  matches wire batching). Downstream cards 30 (cloud client) and 57 (S9
  `PrometheusExporter`) must implement `export_batch`; the card 57 text assumed a
  per-event `export(event)` and should be reconciled to `export_batch`.
