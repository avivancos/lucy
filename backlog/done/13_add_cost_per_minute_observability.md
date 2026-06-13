# 13 - Add cost-per-minute observability

**Epic:** Observability
**Estimated effort:** ~8 h
**State:** done

## Goal

Make `cost_per_minute` Lucy's primary business and engineering metric.

## Spec

Compute cost per minute from STT, LLM, TTS, telephony, RAG, MCP tool, and infra
costs divided by billable audio minutes. Emit OpenTelemetry-compatible trace
spans and Lucy metric events.

## Files to create/modify

- `src/lucy/metrics.py` - cost and latency models
- `src/lucy/observe.py` - trace event bridge
- `tests/test_observability.py` - trace and cost tests

## Definition of Done

- [x] Cost uses all required components.
- [x] Billable minutes must be greater than zero.
- [x] Latency waterfall exposes component and total latency.
- [x] API metrics endpoint exposes the primary metric name.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add a real OpenTelemetry exporter bridge once collector integration is tested
  in Docker Compose.
- Docker Compose verification should be rerun once the Docker daemon is active.
