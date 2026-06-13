# 9 - Implement MCP-first integration layer

**Epic:** MCP
**Estimated effort:** ~6 h
**State:** done

## Goal

Make MCP the default boundary for external tools and resources.

## Spec

Implement an MCP client boundary with allowed-tool permissions, denied-call
errors, audit events, transport abstraction, and replayable call results.

## Files to create/modify

- `src/lucy/mcp.py` - MCP client boundary
- `tests/test_registry_mcp_metrics.py` - MCP permission tests

## Definition of Done

- [x] Allowed tools execute through the transport.
- [x] Unknown tools are denied before transport execution.
- [x] Every allowed or denied call creates an audit event.
- [x] Tests use a local MCP protocol transport, not mocks or real external network.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add schema validation, timeout, and replay file coverage when MCP protocol
  transport grows beyond the current local boundary.
- Docker Compose verification should be rerun once the Docker daemon is active.
