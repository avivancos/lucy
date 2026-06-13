# 20 - Extract Pili vertical to private repo

**Epic:** Open-core restructure
**Estimated effort:** ~4 h
**State:** pending

## Goal

Pili is a vertical product, not framework (ADR 0010). Move it out so the SDK
has no product routes, while keeping a sanitized public example.

## Spec

Create `/Users/agustin/Desarrollo/pili` as a standalone FastAPI app that
depends on lucy as a library. Move: `/pili/health`, `/pili/voice/events`,
`/pili/bookings` handlers, `Pili*` schemas from `src/lucy/api/schemas.py`,
`DEFAULT_PILI_MCP_TOOLS`, `_pili_booking_id`, and `tests/test_pili_api.py`.
The moved app keeps its MCP permission/audit behavior and its tests green via
the same `LocalMcpCommandTransport` (imported from lucy). Lucy's app factory
drops the `allowed_pili_tools` parameter. Add `examples/booking_agent/` in
lucy: a sanitized booking agent built only on public lucy APIs.

## Files to create/modify

- `/Users/agustin/Desarrollo/pili/` - new repo: app, schemas, tests, pyproject
- `src/lucy/api/app.py` - remove pili routes and helpers
- `src/lucy/api/schemas.py` - remove `Pili*` models
- `tests/test_pili_api.py` - moves to pili repo
- `tests/test_api.py` - drop pili route assertions
- `examples/booking_agent/` - sanitized reference app

## Definition of Done

- [ ] Lucy serves no `/pili/*` route and has no Pili symbol.
- [ ] Pili app starts and its tests pass in its own repo against installed lucy.
- [ ] Lucy test suite green after extraction.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
