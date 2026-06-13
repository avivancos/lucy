# 4_5 - Add Pili API contracts

**Epic:** Backend
**Estimated effort:** ~3 h
**State:** done

## Goal

Expose Pili as Lucy's first reference vertical API for sales and booking voice
agents while keeping the core platform provider- and vertical-agnostic.

## Spec

Add `/pili/*` FastAPI contract routes and schemas for health, voice funnel
events, and booking holds. The endpoints must use typed schemas, deterministic
local behavior, and no external CRM, calendar, telephony, or provider calls.

## Files to create/modify

- `src/lucy/api/schemas.py` - Pili API request/response schemas
- `src/lucy/api/app.py` - `/pili/*` routes
- `tests/test_pili_api.py` - Pili API contract tests

## Definition of Done

- [x] `/pili/health` returns Lucy compatibility and service status.
- [x] `/pili/voice/events` accepts funnel/sentiment/cost payloads and returns an accepted event id.
- [x] `/pili/bookings` accepts a booking hold request and returns deterministic hold data.
- [x] OpenAPI contains all `/pili/*` routes.
- [x] Targeted tests green in local runtime
- [x] Post-task audit done

## Improvements noted

- Add a future real Pili CRM/calendar connector behind MCP once the local
  contract is stable.
- Docker Compose verification should be rerun once the Docker daemon is active.
