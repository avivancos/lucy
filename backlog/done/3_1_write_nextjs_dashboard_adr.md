# 3_1 - Write Next.js dashboard ADR

**Epic:** Architecture
**Estimated effort:** ~40 min
**State:** done

## Goal

Record the dashboard technology decision before deeper frontend work begins.

## Spec

The ADR states that Lucy's dashboard uses React, Next.js App Router, TypeScript,
and the FastAPI OpenAPI contract as its source of truth.

## Files to create/modify

- `docs/adr/0002-nextjs-dashboard.md` - dashboard ADR

## Definition of Done

- [x] ADR status is accepted.
- [x] ADR names React, Next.js App Router, and TypeScript.
- [x] ADR names FastAPI OpenAPI as the contract source.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
