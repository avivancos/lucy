# 3_2 - Define dashboard realtime policy

**Epic:** Architecture
**Estimated effort:** ~30 min
**State:** done

## Goal

Define how the dashboard receives live operational state.

## Spec

The ADR records that live calls, traces, latency, sentiment, funnel state, and
cost updates use realtime channels such as WebSocket or SSE from FastAPI.

## Files to create/modify

- `docs/adr/0002-nextjs-dashboard.md` - realtime policy

## Definition of Done

- [x] Realtime data classes are named.
- [x] WebSocket or SSE is named as the transport family.
- [x] Dashboard visual audit remains required for UI work.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
