# agents.md - Lucy Operating Guide

This file is the source of truth for project operating rules.

## Project Placeholders

| Key | Value |
| --- | --- |
| Project name | Lucy |
| Project path | `/Users/agustin/Desarrollo/lucy` |
| Code root | `src/lucy/` |
| Tests root | `tests/` |
| Docs root | `docs/` |
| Runtime | Docker Compose |
| Dev command | `docker compose up --build` |
| Test command | `docker compose run --rm lucy-api pytest` |
| UI audit tool | Browser / Playwright |
| Project backlog | `backlog/` |
| Document language | English |
| Default branch | `main` |

## Rules

- Use English for documentation, code comments, backlog cards, commits, and PRs.
- Treat `backlog/agent_index.md` as the canonical backlog process.
- Formal tasks follow Spec -> red test -> green code -> refactor -> docs -> move.
- Use Docker Compose as the sanctioned runtime for tests, services, and audits.
- Lucy is a no-mocks project. Tests exercise real local implementations,
  deterministic in-process simulators, local protocol servers, or recorded
  fixtures from real interactions. Do not use mocking frameworks or invented
  provider behavior to make tests pass.
- Do not hardcode provider names, model names, URLs, secrets, thresholds, regions,
  currencies, quotas, or latency budgets outside typed settings, registries, or
  named constants.
- Stage only files related to the current task when committing.
- For UI work, run a visual audit before closing the task.
- For API/runtime work, smoke-test the affected endpoint or CLI before closing.

## Cursor Cloud specific instructions

Services (see `docker-compose.yml` for the canonical definitions):

- `lucy-api` — FastAPI control plane on host `:8010` (container `:8000`; `/docs`, `/openapi.json`, and the `pili` CRM/booking flow). Required.
- `lucy-dashboard` — Next.js ops dashboard on host `:3010` (container `:3000`), reads the API at `http://localhost:8010`. Required for UI E2E.
- `postgres` (host `:5419` → container `:5432`), `redis` (host `:6310` → container `:6379`), `lucy-worker`, `lucy-media-gateway` (`:8081`), `otel-collector` — declared but currently not on the active code path; optional for local E2E.

Lucy-specific host ports (avoid clashing with other local stacks): API `8010`, dashboard `3010`, Postgres `5419`, Redis `6310`. Intra-compose service URLs in `.env.example` keep the container ports (`postgres:5432`, `redis:6379`).

Docker is the sanctioned runtime but is NOT auto-started. Start it once per session before any `docker compose` command: `sudo dockerd > /tmp/dockerd.log 2>&1 &` (the daemon uses `fuse-overlayfs` + `iptables-legacy`, already configured). Prefix compose commands with `sudo`.

Testing gotcha: the sanctioned command `docker compose run --rm lucy-api pytest` FAILS on contract tests because `Dockerfile.api` only copies `src/` and `tests/`, so repo-root files (`docs/`, `backlog/`, `infra/`, compose files) that those tests read are absent inside the image. Run tests with the repo mounted instead: `sudo docker compose run --rm -v "$PWD":/app lucy-api pytest`. Running `pytest` locally from the repo root also passes all 98 tests.

The `tomli` package is a required test dependency (imported unconditionally by `tests/test_package_metadata.py`) and is declared in `pyproject.toml` `[project.optional-dependencies].dev`.

Dashboard lint: `npm run lint` (`next lint`) is NOT configured (no ESLint config) and will block on an interactive setup prompt — avoid it. Use `npx tsc --noEmit` for type-checking and `npm run test:contracts` for the dashboard↔API contract check. `npm run generate:openapi` regenerates `lib/generated/openapi.ts` from the API using `python3` with `PYTHONPATH=src`.

For a dev-mode (hot-reload) workflow outside Docker: API `uvicorn lucy.api.app:create_app --factory --reload` (needs `~/.local/bin` on PATH), dashboard `npm run dev` in `dashboard/`. The compose `lucy-dashboard` service serves a production build (`next start`), so code changes require an image rebuild there.
