# 23 - Create lucy.serve subpackage

**Epic:** Open-core restructure
**Estimated effort:** ~3 h
**State:** pending

## Goal

Separate the framework serving runtime from platform control-plane concerns so
"run your agent" never requires the platform (ADR 0010).

## Spec

Create `src/lucy/serve/` with `app.py` (factory `create_app` keeping
`/health`, `/metrics`, `/metrics/realtime` SSE, `/models`, `/evals`) and
`schemas.py` (the non-fleet, non-Pili response models). `src/lucy/worker.py`
moves to `src/lucy/serve/worker.py`. `src/lucy/api/` keeps thin deprecation
shims for one release. Docker compose commands and `Dockerfile.api` point at
`lucy.serve.app:create_app` and `lucy.serve.worker`.

## Files to create/modify

- `src/lucy/serve/{__init__.py,app.py,schemas.py,worker.py}` - new subpackage
- `src/lucy/api/{app.py,schemas.py}` - deprecation shims
- `docker-compose.yml`, `Dockerfile.api`, `README.md` - new module paths
- `tests/test_api.py`, `tests/test_api_schemas.py`,
  `tests/test_package_metadata.py`, `tests/test_infrastructure.py` - adjust

## Definition of Done

- [ ] `uvicorn lucy.serve.app:create_app --factory` serves health/SSE/docs.
- [ ] Old `lucy.api` imports work with `DeprecationWarning`.
- [ ] Full test suite green.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
