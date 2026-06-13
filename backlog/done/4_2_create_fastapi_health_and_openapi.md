# 4_2 - Create FastAPI health and OpenAPI

**Epic:** Backend
**Estimated effort:** ~1 h
**State:** done

## Goal

Create the FastAPI app entrypoint and health endpoint.

## Spec

`create_app()` returns a FastAPI app titled `Lucy API`. `/health` returns
`service`, `status`, and `version`. `/docs`, `/redoc`, and `/openapi.json` are
available through FastAPI defaults.

## Files to create/modify

- `src/lucy/api/app.py` - app factory and health endpoint
- `tests/test_api.py` - health and OpenAPI tests

## Definition of Done

- [x] `/health` returns HTTP 200.
- [x] Health response includes `lucy-api`, `ok`, and the package version.
- [x] OpenAPI schema can be fetched in tests.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
