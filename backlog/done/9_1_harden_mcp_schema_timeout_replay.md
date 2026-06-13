# 9_1 - Harden MCP schema timeout and replay

**Epic:** MCP
**Estimated effort:** ~6 h
**State:** done

## Goal

Strengthen Lucy's MCP boundary with schema validation, timeout handling, and
replayable call artifacts.

## Spec

Add typed tool schemas, deadline-aware MCP calls, malformed payload handling,
and replay file creation for allowed and denied tool calls.

## Files to create/modify

- `src/lucy/mcp.py` - schema, timeout, and replay support
- `tests/test_registry_mcp_metrics.py` - MCP hardening tests

## Definition of Done

- [x] Malformed MCP payloads fail with typed errors.
- [x] Tool calls respect explicit deadlines.
- [x] Allowed and denied calls can be serialized for replay.
- [x] Tests use local protocol behavior, not mocks or external network.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Persist replay artifacts under trace storage once storage migrations exist.
