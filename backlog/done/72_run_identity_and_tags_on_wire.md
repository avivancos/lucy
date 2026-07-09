# 72 - Add run identity and tags on the telemetry wire

**Sprint:** S7 - Cloud observability seam
**Epic:** Observability
**Estimated effort:** ~6 h
**Depends on:** 24, 37
**State:** done

## Goal

Give the platform stable dimensions for traces, costs, evals, and diffs before
the database schema lands: session tags, agent version, graph hash, and thread
identity must travel with telemetry.

## Context primer

- `agents.md` - PII redaction and typed settings rules.
- `docs/telemetry-wire-v1.md` - additive wire-v1 contract.
- `src/lucy/observe/events.py` - telemetry event models.
- `src/lucy/graph.py` - graph topology can produce a stable hash.
- `src/lucy/specs.py` - `AgentSpec` and session specs carry public identity fields.

## Spec

Add additive `tags: dict[str, str]` to every telemetry event base. Tags come from
explicit `lucy.observe.configure(...)`, `LUCY_TAGS`, and session-level runtime
metadata, with later explicit values winning. Tag keys and values pass through
the existing PII redaction path.

Add `agent_version`, `graph_hash`, and `thread_id` to `session.started`.
`CompiledAgentGraph.topology_hash()` must hash node names, edges, conditional
edge names when available, and graph limits; it must not hash function memory
addresses.

## Files to create/modify

- `src/lucy/observe/events.py` - event base tags and session identity fields.
- `src/lucy/observe/__init__.py` - tag configuration and env parsing.
- `src/lucy/graph.py` - `topology_hash()`.
- `src/lucy/specs.py` - additive `agent_version`.
- `docs/telemetry-wire-v1.md` - document identity and tags.
- `tests/test_observability.py` and `tests/test_checkpointing.py` - coverage.

## Chips

- [x] **C1 - Event tags.** Write failing tests for configured tags, `LUCY_TAGS`, precedence, and redaction, then implement base event tags. Files: `src/lucy/observe/events.py`, `src/lucy/observe/__init__.py`, `tests/test_observability.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_observability.py -q -k tags` -> selected tests pass.
- [x] **C2 - Session identity.** Add tests for `agent_version`, `thread_id`, and stable `graph_hash`, then implement the additive fields. Files: `src/lucy/specs.py`, `src/lucy/graph.py`, `tests/test_checkpointing.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_checkpointing.py tests/test_observability.py -q` -> selected files pass.
- [x] **C3 - Wire docs and gates.** Update telemetry docs, run full gates, and move the card. Files: `docs/telemetry-wire-v1.md`, `src/lucy/observe/events.py`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; emit real telemetry events through `lucy.observe`.
- Do not hardcode tag keys, agent versions, graph hash salts, or environment parsing outside named constants/settings.
- Do not put tenant ids or platform-only concepts into SDK runtime state unless they arrive as user-provided tags.
- Do not make `topology_hash()` depend on object ids or function addresses.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_observability.py tests/test_checkpointing.py -q` -> identity tests pass
- [x] `docker compose run --rm lucy-api pytest` -> full suite green
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If stable graph hashing needs more graph metadata than exists, record the missing
metadata and keep the wire field empty until the blocker is fixed.

## Improvements noted

No follow-up cards raised. The existing telemetry seam supported additive
identity fields cleanly; no platform-only tenant concepts were added to SDK
state.

## Review evidence

- code-reviewer: PASS - local review of additive wire fields, graph hash, and backwards-compatible tracer API; no P0/P1 findings.
- test-auditor: PASS - red tests failed first for tags and topology hash; targeted tests and full Docker suite passed.
- docs-reviewer: PASS - `docs/telemetry-wire-v1.md` updated for tags and session identity fields.
- simplicity-reviewer: PASS - implementation keeps one tag merge helper and one topology hash method; no new abstraction beyond the card scope.
- security-reviewer: PASS - tag keys and values pass through client-side PII redaction before export.

Findings disposition:

- No findings.
