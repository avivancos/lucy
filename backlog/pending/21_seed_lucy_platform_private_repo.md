# 21 - Seed lucy-platform private repo

**Epic:** Open-core restructure
**Estimated effort:** ~4 h
**State:** pending

## Goal

Give the closed platform (ADR 0010) its own home seeded with the assets that
are platform, not SDK: the dashboard and the fleet-shaped control-plane
routes.

## Spec

Create `/Users/agustin/Desarrollo/lucy-platform` containing: `dashboard/`
(moved wholesale from lucy, including its OpenAPI codegen script),
`docs/product/dashboard-ops-command-center.md`, and a `services/control-plane`
FastAPI seed holding the fleet demo routes lifted from lucy
(`/agents`, `/deployments`, `/sessions`, `/traces`, `/mcp/servers`,
`/crm/events`). Lucy keeps `/health`, `/metrics`, `/metrics/realtime`,
`/models`, `/evals` (these describe the local process, not a fleet). The
platform repo installs lucy as a dependency and reuses its schemas.

## Files to create/modify

- `/Users/agustin/Desarrollo/lucy-platform/` - new repo: dashboard, services seed
- `dashboard/` - moves out of lucy
- `docs/product/dashboard-ops-command-center.md` - moves out of lucy
- `src/lucy/api/app.py` - drop fleet routes
- `tests/test_api.py`, `tests/test_product_docs.py` - adjust to the split
- `docker-compose.yml` - dashboard service points at the new path or is removed

## Definition of Done

- [ ] Dashboard builds (`npm run build`) from lucy-platform.
- [ ] Fleet routes live only in lucy-platform; lucy keeps process-local routes.
- [ ] Lucy test suite green after the split.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
