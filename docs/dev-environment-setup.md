# Development Environment Setup

This document captures how the Lucy development environment is provisioned and
run, with the specific gotchas discovered while setting it up in a fresh Linux
(Ubuntu 24.04) environment. It complements the high-level instructions in
`README.md` and the operating rules in `agents.md`.

## Toolchain

| Tool | Version used | Notes |
| --- | --- | --- |
| Python | 3.12 | Docker image is `python:3.12-slim`; project requires `>=3.9`. |
| Node.js | 22.x | Dashboard image is `node:22-alpine`. |
| Rust | 1.82 | Only needed for the optional media gateway. |
| Docker Engine + Compose v2 | 28.x / v2 | Sanctioned runtime; not auto-started. |

## Dependency installation

Backend (from repo root):

```bash
pip install -e ".[dev]"
```

Dashboard:

```bash
npm --prefix dashboard install
```

`tomli` is declared as a dev dependency because `tests/test_package_metadata.py`
imports it unconditionally (the project targets Python `>=3.9`, so it cannot
rely on the stdlib `tomllib` added in 3.11).

## Docker (sanctioned runtime)

Docker is not started automatically. Start the daemon once per session:

```bash
sudo dockerd > /tmp/dockerd.log 2>&1 &
```

The daemon is configured to use `fuse-overlayfs` (`/etc/docker/daemon.json`) and
`iptables-legacy`, which are required for docker-in-docker in this environment.
Prefix `docker compose` commands with `sudo`.

Bring up the core services:

```bash
sudo docker compose up -d lucy-api lucy-dashboard
```

- API: <http://localhost:8000> (`/docs`, `/redoc`, `/openapi.json`)
- Dashboard: <http://localhost:3000>

`postgres`, `redis`, `otel-collector`, `lucy-worker`, and `lucy-media-gateway`
are declared in `docker-compose.yml` but are not on the active request path yet,
so they are optional for local end-to-end work.

## Running tests

All 98 tests pass. Run them locally from the repo root:

```bash
pytest
```

Or inside Docker with the repo mounted (required — see gotcha below):

```bash
sudo docker compose run --rm -v "$PWD":/app lucy-api pytest
```

### Gotcha: bare `docker compose run ... pytest` fails

The sanctioned command in `agents.md` (`docker compose run --rm lucy-api pytest`)
fails on contract tests. `Dockerfile.api` only copies `src/` and `tests/` into
the image, so repo-root files that contract tests read (`docs/`, `backlog/`,
`infra/`, the compose files) are missing inside the container. Mount the repo
(`-v "$PWD":/app`) or run `pytest` locally instead.

## Dashboard checks

- Type-check: `npx tsc --noEmit` (in `dashboard/`).
- Contract check: `npm run test:contracts`.
- OpenAPI client regeneration: `npm run generate:openapi` (uses `python3` with
  `PYTHONPATH=src`).
- `npm run lint` (`next lint`) is **not configured** — it prompts for
  interactive ESLint setup. Prefer `tsc --noEmit` until an ESLint config exists.

## Dev-mode (hot reload) outside Docker

```bash
# API
uvicorn lucy.api.app:create_app --factory --reload   # needs ~/.local/bin on PATH

# Dashboard
npm --prefix dashboard run dev
```

The compose `lucy-dashboard` service runs a production build (`next start`), so
front-end changes there require rebuilding the image; use `npm run dev` for
hot reload during development.

## Smoke test (hello-world)

The Pili CRM/booking flow exercises the product's core value (voice event ->
MCP CRM tools -> booking hold):

```bash
curl -s -X POST http://localhost:8000/pili/voice/events \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_001","lead_id":"lead_001","funnel_stage":"interested","sentiment":"positive","cost_per_minute":0.12,"transcript_excerpt":"I would like to book a demo."}'

curl -s -X POST http://localhost:8000/pili/bookings \
  -H "Content-Type: application/json" \
  -d '{"session_id":"sess_001","lead_id":"lead_001","requested_slot":"2026-08-03T15:00:00Z","timezone":"Europe/Madrid","source":"voice"}'
```

The voice event returns a queued `crm.upsert_lead` MCP command; the booking
returns queued `crm.upsert_lead` + `calendar.hold_slot` commands.
