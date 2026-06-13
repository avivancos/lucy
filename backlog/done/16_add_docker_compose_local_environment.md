# 16 - Add Docker Compose local environment

**Epic:** Infrastructure
**Estimated effort:** ~6 h
**State:** done

## Goal

Make Lucy runnable as a production-like local stack.

## Spec

Docker Compose defines `lucy-api`, `lucy-worker`, `lucy-dashboard`,
`lucy-media-gateway`, `postgres`, `redis`, and `otel-collector`. The Rust media
gateway is Docker-buildable even if local Cargo is not installed.

## Files to create/modify

- `docker-compose.yml` - local stack
- `Dockerfile.api` - backend image
- `dashboard/Dockerfile` - dashboard image
- `media-gateway-rust/` - Rust sidecar source and Dockerfile
- `.env.example` - local settings

## Definition of Done

- [x] Compose services match the planned architecture.
- [x] Backend image can run FastAPI.
- [x] Dashboard image can run Next.js.
- [x] Media gateway exposes `/health`.
- [x] Targeted infrastructure tests green locally.
- [x] Post-task audit done.

## Improvements noted

- Rerun `docker compose build` and `docker compose up` once the local Docker
  daemon is available; current environment has no Docker socket.
