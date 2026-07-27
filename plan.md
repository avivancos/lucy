# Plan

## Current goal: Development environment setup (Cursor Cloud)

- [x] Install Docker + Compose (docker-in-docker with fuse-overlayfs + iptables-legacy)
- [x] Install Python dependencies (`pip install -e ".[dev]"`)
- [x] Install dashboard dependencies (`npm install` in `dashboard/`)
- [x] Add missing test dependency `tomli` to `pyproject.toml` dev extras
- [x] Run backend tests (98 passed, both locally and in Docker with repo mounted)
- [x] Type-check dashboard (`npx tsc --noEmit`) and run dashboard contract check
- [x] Run the stack via Docker Compose (API `:8000`, dashboard `:3000`)
- [x] Hello-world: Pili voice event -> CRM `upsert_lead`; Pili booking -> `upsert_lead` + `calendar.hold_slot`
- [x] Document setup in `docs/` and record Cursor Cloud instructions in `agents.md`

## Notes / follow-ups

- `next lint` is not configured (no ESLint config); rely on `tsc --noEmit` until an ESLint config is added.
- `docker compose run --rm lucy-api pytest` needs the repo mounted (`-v "$PWD":/app`) because `Dockerfile.api` only copies `src/` and `tests/`.
- `postgres`, `redis`, `otel-collector`, `lucy-worker`, `lucy-media-gateway` are declared but not on the active code path yet.
