# 4_4 - Add base API models and schemas

**Epic:** Backend
**Estimated effort:** ~2 h
**State:** done

## Goal

Move the FastAPI contract models into a reusable schema layer so endpoint
responses, OpenAPI generation, and future SDK generation share one source of
truth.

## Spec

Create base API models and route response schemas under `lucy.api.schemas`.
Schemas must forbid unexpected fields, serialize enum values cleanly, include
typed list response aliases, and be used by `lucy.api.app` instead of inline
Pydantic classes.

## Files to create/modify

- `src/lucy/api/schemas.py` - base API models and response schemas
- `src/lucy/api/app.py` - imports schemas instead of declaring inline models
- `tests/test_api_schemas.py` - schema contract tests
- `tests/test_api.py` - API contract remains green

## Definition of Done

- [x] `LucyApiModel` forbids extra fields.
- [x] Health, agent, deployment, session, trace, and MCP server schemas live in `lucy.api.schemas`.
- [x] App endpoints use schemas from `lucy.api.schemas`.
- [x] OpenAPI still contains every planned route.
- [x] Targeted tests green in local runtime
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
