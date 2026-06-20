# 22 - Create lucy.testing subpackage

**Epic:** Open-core restructure
**Estimated effort:** ~3 h
**State:** done

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

- [x] All simulators importable from `lucy.testing`.
- [x] Old import paths still work and emit `DeprecationWarning`.
- [x] Full test suite green with tests importing from `lucy.testing`.
- [x] Targeted tests green in Docker Compose (`docker compose run --rm lucy-api pytest` -> 111 passed).
- [x] Post-task audit done (3-lens adversarial review: all pass, no blockers).

## Improvements noted

- `RealtimeVoicePipeline` defaults its STT/TTS providers to the relocated
  `lucy.testing` simulators via a cycle-safe lazy import, coupling the default
  framework path to the testing subpackage. This is intentional for the
  zero-key quickstart and is formalized in card 26 (provider-string resolution,
  where the bare name `local` resolves to `lucy.testing`); no separate
  follow-up card raised. The adversarial review flagged it only as a design
  nit, not a defect.
- Card 27's premise "Lucy has never been a git repo" is now stale: the repo
  already has an initial commit. Noted for card 27.
- Behavior was preserved exactly: the six class bodies moved byte-for-byte;
  only `__getattr__` deprecation shims and import cleanups were added to the
  original modules. Suite: 98 -> 111 passing in Docker.
