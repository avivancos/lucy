# Plan

## Current goal: Lucy-specific host ports

- [x] Map API host port `8000` → `8010`
- [x] Map dashboard host port `3000` → `3010`
- [x] Map Postgres host port `5432` → `5419`
- [x] Map Redis host port `6379` → `6310`
- [x] Point `NEXT_PUBLIC_LUCY_API_URL` at `http://localhost:8010`
- [x] Update contract tests + docs (`agents.md`, `docs/`)
- [ ] Recreate compose stack and smoke-test on the new ports

## Previous: Development environment setup (Cursor Cloud)

- [x] Install Docker + Compose (docker-in-docker with fuse-overlayfs + iptables-legacy)
- [x] Install Python dependencies (`pip install -e ".[dev]"`)
- [x] Install dashboard dependencies (`npm install` in `dashboard/`)
- [x] Add missing test dependency `tomli` to `pyproject.toml` dev extras
- [x] Run backend tests (98 passed, both locally and in Docker with repo mounted)
- [x] Type-check dashboard (`npx tsc --noEmit`) and run dashboard contract check
- [x] Run the stack via Docker Compose
- [x] Hello-world: Pili voice event -> CRM `upsert_lead`; Pili booking -> `upsert_lead` + `calendar.hold_slot`
- [x] Document setup in `docs/` and record Cursor Cloud instructions in `agents.md`

## Notes / follow-ups

- Host ports are Lucy-specific; container-internal ports stay at framework defaults so `.env.example` Docker-network URLs keep working.
- `next lint` is not configured (no ESLint config); rely on `tsc --noEmit` until an ESLint config is added.
- `docker compose run --rm lucy-api pytest` needs the repo mounted (`-v "$PWD":/app`) because `Dockerfile.api` only copies `src/` and `tests/`.
