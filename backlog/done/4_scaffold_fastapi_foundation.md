# 4 - Scaffold FastAPI foundation

**Epic:** Backend
**Estimated effort:** ~4 h
**State:** done

## Goal

Create the backend control-plane foundation and developer portal.

## Spec

FastAPI exposes `/health`, `/agents`, `/deployments`, `/sessions`, `/traces`,
`/metrics`, `/models`, `/evals`, `/mcp/servers`, and `/crm/events`. Swagger UI,
ReDoc, and `/openapi.json` are available through FastAPI.

## Files to create/modify

- `pyproject.toml` - Python package and dependencies
- `src/lucy/api/app.py` - FastAPI app
- `tests/test_api.py` - API smoke tests

## Definition of Done

- [x] `/health` returns service, status, and version.
- [x] OpenAPI contains every planned public route.
- [x] API tests use no real network calls.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
