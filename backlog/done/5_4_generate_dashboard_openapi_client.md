# 5_4 - Generate dashboard OpenAPI client

**Epic:** Dashboard
**Estimated effort:** ~4 h
**State:** done

## Goal

Replace dashboard-local demo API types with generated client types from the
FastAPI OpenAPI contract.

## Spec

Add an OpenAPI client generation command for the dashboard, commit generated
types in a stable location or generate them during build, and update dashboard
data access to consume the generated contract.

## Files to create/modify

- `dashboard/package.json` - OpenAPI generation script
- `dashboard/lib/` - generated or generated-source API client
- `src/lucy/api/app.py` - schema compatibility if needed

## Definition of Done

- [x] Dashboard types are generated from `/openapi.json`.
- [x] Local handwritten demo response types are removed or narrowed to fixtures.
- [x] Generation is deterministic and documented.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Consider adding a CI diff check that fails when generated OpenAPI types are
  stale relative to FastAPI.
