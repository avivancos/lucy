# 12 - Add sentiment, funnel, and CRM metrics

**Epic:** Metrics
**Estimated effort:** ~7 h
**State:** done

## Goal

Make sales/booking outcomes observable and CRM-ready in realtime.

## Spec

Implement sentiment scoring contracts, funnel-stage events, CRM event payloads,
confidence scores, and realtime metric emission for qualified, interested,
objection, booked, escalation, and failed booking states.

## Files to create/modify

- `src/lucy/metrics.py` - sentiment, funnel, CRM event models
- `tests/test_registry_mcp_metrics.py` - metric model tests

## Definition of Done

- [x] Sentiment includes label, confidence, and model attribution.
- [x] Funnel events include session id, stage, confidence, and CRM payload.
- [x] Booking and escalation states are covered by tests.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add realtime metric emission transport once WebSocket/SSE backend channels are
  introduced.
- Docker Compose verification should be rerun once the Docker daemon is active.
