# 22 - Create lucy.testing subpackage

**Epic:** Open-core restructure
**Estimated effort:** ~3 h
**State:** pending

## Goal

Make the deterministic simulators a shipped, documented public subpackage so
the no-mocks policy (ADR 0003) scales to SDK users and plugin authors.

## Spec

Create `src/lucy/testing/__init__.py` exporting: `LocalSttSimulator`,
`LocalTtsSimulator` (from voice.py), `LocalMcpCommandTransport` (from mcp.py),
`LocalEmbeddingFixture` (from rag.py), `InMemoryOtelSpanExporter` (from
observe.py), `LocalMetricEventChannel` (from metrics.py). Originals move;
the old module paths keep deprecation re-exports for one release. Lucy's own
tests import from `lucy.testing` - one source of truth.

## Files to create/modify

- `src/lucy/testing/__init__.py` - new subpackage
- `src/lucy/{voice,mcp,rag,observe,metrics}.py` - relocate + re-export shims
- `tests/*` - import simulators from `lucy.testing`

## Definition of Done

- [ ] All simulators importable from `lucy.testing`.
- [ ] Old import paths still work and emit `DeprecationWarning`.
- [ ] Full test suite green with tests importing from `lucy.testing`.
- [ ] Targeted tests green in Docker Compose
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
