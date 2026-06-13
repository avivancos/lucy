# 3 - Record Next.js dashboard ADR

**Epic:** Architecture
**Estimated effort:** ~1 h
**State:** done

## Goal

Lock the decision that Lucy's dashboard is React and Next.js, driven by the
FastAPI OpenAPI contract.

## Spec

An ADR explains that the dashboard uses Next.js App Router, TypeScript, generated
typed API clients, and realtime channels for live operational views.

## Files to create/modify

- `docs/adr/0002-nextjs-dashboard.md` - ADR

## Definition of Done

- [x] ADR status is accepted.
- [x] ADR names FastAPI OpenAPI as the source of truth.
- [x] ADR requires visual audit for dashboard work.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
