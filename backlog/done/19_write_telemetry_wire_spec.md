# 19 - Write telemetry wire spec v1

**Epic:** Observability
**Estimated effort:** ~2 h
**State:** done

## Goal

Publish the normative client/server telemetry contract so the open SDK and the
closed platform can evolve independently against one document.

## Spec

`docs/telemetry-wire-v1.md` defines: `POST /v1/events` with api-key auth,
batching, gzip, idempotency keys; fail-open delivery with a drop counter; the
envelope and event union (session, turn, span, cost, business, tool_call,
transcript, audio_ref); client-side privacy controls (`redact_pii`,
`record_audio`, sampling, transcript kill-switch); env-var configuration
(`LUCY_TRACING`, `LUCY_ENDPOINT`, `LUCY_API_KEY`, `LUCY_PROJECT`,
`LUCY_TRACE_SAMPLE`, `LUCY_TRACE_FILE`); additive-only versioning on `/v1/`.

## Files to create/modify

- `docs/telemetry-wire-v1.md` - the spec
- `tests/test_architecture_adrs.py` - contract tests

## Definition of Done

- [x] Spec covers transport, semantics, events, privacy, config, versioning.
- [x] Contract tests assert endpoint, idempotency, fail-open, privacy flags.
- [x] Targeted tests green (local venv; Docker daemon not running this session).
- [x] Post-task audit done

## Improvements noted

- Card 24 implements `lucy.observe` against this spec; card 30 implements the
  cloud exporter; the platform ingest service must add a conformance fixture.
