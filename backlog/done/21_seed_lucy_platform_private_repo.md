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

A 20-agent two-repo adversarial review confirmed byte-for-byte fleet-route
parity, a genuinely standalone-on-lucy control plane, and a green dashboard
build. Fixes applied:

- Dashboard OpenAPI regen was broken by the move: `generate-openapi-client.mjs`
  still imported `lucy.api.app` (now process-local only) with a `src/` PYTHONPATH
  that no longer exists. Retargeted it to `control_plane.app` with
  `services/control-plane/src`, then regenerated `dashboard/lib/generated/
  openapi.ts` against the control plane - which also dropped the stale `/pili/*`
  paths and `Pili*` types that had lingered in the committed client since before
  card 20.
- Tightened `verify-dashboard-contract.mjs` to assert every fleet schema and the
  `/agents` path are present and that no `/pili` route reappears, so a future
  regen that strips the fleet surface fails the contract.
- Fixed stale docs in lucy: the README claimed `lucy.api.app` still mounts the
  fleet/Pili routes and listed the dashboard as a lucy component; the `serve/`
  docstrings claimed the fleet still lived in the deprecated shim.

Decisions:

- The fleet response schemas moved to `lucy-platform` (`control_plane.schemas`,
  subclassing lucy's `LucyApiModel`), mirroring the pili pattern. The card's
  files-to-modify list did not name `api/schemas.py`, but the existing module
  docstrings stated card 21 moves the fleet models out, so the clean split wins.
- `git init` of lucy-platform is deferred to card 27 (the `.gitignore` is seeded;
  build artifacts `.next`/`node_modules` were cleaned from the tree).
