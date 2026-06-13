# 4_3 - Add control-plane contract routes

**Epic:** Backend
**Estimated effort:** ~1.5 h
**State:** done

## Goal

Expose Lucy's planned control-plane surface as typed contract endpoints.

## Spec

Add `/agents`, `/deployments`, `/sessions`, `/traces`, `/metrics`, `/models`,
`/evals`, `/mcp/servers`, and `/crm/events`. Responses are typed and use local
deterministic application data only.

## Files to create/modify

- `src/lucy/api/app.py` - control-plane endpoints
- `tests/test_api.py` - route presence tests

## Definition of Done

- [x] Every planned route appears in `/openapi.json`.
- [x] Contract responses are typed with Pydantic models or explicit dictionaries.
- [x] No endpoint performs a real provider, CRM, or MCP network call.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
