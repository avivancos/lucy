# 23 - Create lucy.serve subpackage

**Epic:** Open-core restructure
**Estimated effort:** ~3 h
**State:** done

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

- [x] `uvicorn lucy.serve.app:create_app --factory` serves health/SSE/docs.
      (In-container smoke: /health, /docs, /models, /evals, /metrics/realtime OK;
      /agents and /pili/health correctly 404 on the framework app.)
- [x] Old `lucy.api` imports work with `DeprecationWarning`.
- [x] Full test suite green (`docker compose run --rm lucy-api pytest` -> 118 passed).
- [x] Targeted tests green in Docker Compose.
- [x] Post-task audit done (3-lens adversarial review: all pass, only nits).

## Improvements noted

- DEFERRED compose/Dockerfile flip (user decision): `docker-compose.yml` and
  `Dockerfile.api` still target `lucy.api.app:app` and `python -m lucy.worker`,
  NOT `lucy.serve`. This keeps the local container serving the fleet + Pili
  routes until cards 20/21 extract them. The compose flip is a one-line change
  to make in card 21 once fleet routes have a new home. Spec line 19 is
  intentionally not satisfied yet for this reason.
- `lucy.api.app` and `lucy.api.schemas` remain "thick" transitional shims (they
  still host fleet + Pili routes/models) rather than the thin shims the card
  envisions, because the extraction cards 20/21 were deferred. They become thin
  automatically as 20/21 remove the fleet/Pili pieces.
- `_demo_cost_breakdown` is duplicated in `lucy.serve.app` (for the
  /metrics/realtime demo) and `lucy.api.app` (for /sessions) on purpose: the
  fleet `/sessions` route leaves with card 21 and takes its own copy, so the
  two never need to share a private helper across the open/closed boundary.
