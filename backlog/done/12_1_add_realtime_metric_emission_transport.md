# 12_1 - Add realtime metric emission transport

**Epic:** Metrics
**Estimated effort:** ~6 h
**State:** done

## Goal

Stream sentiment, funnel, CRM, and cost metric events to the dashboard in
realtime.

## Spec

Add a local WebSocket or SSE event channel for metric events, with typed payloads
and deterministic local integration tests.

## Files to create/modify

- `src/lucy/api/app.py` - realtime metric route
- `src/lucy/metrics.py` - event transport schema if needed
- `tests/test_api.py` - realtime route contract

## Definition of Done

- [x] Metric events can be consumed through a realtime local channel.
- [x] Payloads include sentiment, funnel, CRM, and cost fields.
- [x] Tests use local transport behavior, not mocks.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Replace demo event seeding with session-backed event fan-out once storage
  exists.
