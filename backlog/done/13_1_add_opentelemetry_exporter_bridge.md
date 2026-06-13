# 13_1 - Add OpenTelemetry exporter bridge

**Epic:** Observability
**Estimated effort:** ~6 h
**State:** done

## Goal

Connect Lucy observability events to an OpenTelemetry collector in the local
Docker Compose stack.

## Spec

Add an exporter bridge that turns `ObservabilityEvent` trace attributes into
OpenTelemetry spans and verifies local collector delivery when Docker is
available.

## Files to create/modify

- `src/lucy/observe.py` - OTEL exporter bridge
- `tests/test_observability.py` - exporter contract tests
- `docker-compose.yml` - collector integration if needed

## Definition of Done

- [x] Observability events can be exported as spans.
- [x] Export payload preserves `cost_per_minute` and latency fields.
- [x] Local tests remain deterministic without external vendors.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Verify end-to-end collector delivery with `docker compose up otel-collector`
  once Docker daemon access is available.
