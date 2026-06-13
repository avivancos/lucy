# 5_2 - Create dashboard typed API boundary

**Epic:** Dashboard
**Estimated effort:** ~45 min
**State:** done

## Goal

Create a typed frontend boundary for API-shaped data before generating a full
OpenAPI client.

## Spec

Add local TypeScript types for health, session, and trace data plus demo data
used by the initial dashboard screen.

## Files to create/modify

- `dashboard/lib/api.ts` - typed API boundary and demo data

## Definition of Done

- [x] Session type includes `id`, `agent_id`, `status`, and `cost_per_minute`.
- [x] Trace type includes latency waterfall components.
- [x] Demo data matches the visible dashboard fields.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Replace local demo data with generated OpenAPI client data once auth and
  realtime transport are introduced.
