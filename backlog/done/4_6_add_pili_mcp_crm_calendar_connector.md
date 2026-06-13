# 4_6 - Add Pili MCP CRM and calendar connector

**Epic:** Backend
**Estimated effort:** ~6 h
**State:** done

## Goal

Connect the local Pili API contract to real CRM and calendar capabilities through
MCP while keeping Lucy's core platform agnostic.

## Spec

Implement a local MCP-backed connector path for Pili lead sync and booking holds.
The connector must use Lucy's MCP permission and audit model, expose no hardcoded
third-party credentials, and retain deterministic local behavior in default
automated tests.

## Files to create/modify

- `src/lucy/api/app.py` - Pili connector integration point
- `src/lucy/mcp.py` - connector support if needed
- `tests/test_pili_api.py` - connector contract coverage

## Definition of Done

- [x] Pili booking can enqueue a local MCP CRM/calendar sync command.
- [x] Connector calls are permission checked and audited.
- [x] Tests use local protocol behavior, not mocks or external network.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add persistent command storage once Postgres migrations exist; current command
  queue is deterministic and process-local for the foundation milestone.
