# 69 - Add persistent checkpoint stores and thread identity

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Graph state
**Estimated effort:** ~10 h
**Depends on:** 37, 72, 96
**State:** pending

## Goal

Make AgentGraph checkpoints durable across process restarts and calls. A stable
`thread_id` lets the platform and local SDK treat a conversation as longer-lived
than one telephony session.

## Context primer

- `agents.md` - Docker runtime and no-mocks testing policy.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - checkpointing and replay decision.
- `src/lucy/state.py` - `Checkpoint` and `CheckpointStore` Protocol.
- `src/lucy/graph.py` - `CompiledAgentGraph.invoke_turn()` saves checkpoints.
- `backlog/done/96_reconcile_interrupted_turns_into_graph_memory.md` - the
  live heard transcript can correct graph state after the final superstep.
- `docker-compose.yml` - local Postgres and Redis services for real adapter tests.

## Spec

Add additive `Checkpoint.thread_id`, defaulting to `session_id` for backwards
compatibility. Add `src/lucy/checkpoint/postgres.py` using asyncpg and a
`lucy_checkpoints` table, and `src/lucy/checkpoint/redis.py` using append-only
RPUSH with TTL for a hot tier. Add `lucy[checkpoint-postgres]` and
`lucy[checkpoint-redis]` extras if dependencies are not already present.

Publish a `lucy.testing.check_checkpoint_store` conformance helper and run it
against memory, Postgres, and Redis stores. Add `GraphTurnDriver.resume(thread_id=...)`
or the equivalent landed resume seam, plus a kill/restart/resume replay test.
Persist the post-finalization state produced by card 96's history
reconciliation so a process restart immediately after barge-in resumes from
heard assistant text, never the generated-but-unplayed answer.

## Files to create/modify

- `src/lucy/state.py` - additive `thread_id`.
- `src/lucy/checkpoint/postgres.py` and `src/lucy/checkpoint/redis.py` - adapters.
- `src/lucy/testing/checkpoint.py` - store conformance helper.
- `tests/test_checkpoint_stores.py` - real Postgres/Redis tests.
- `pyproject.toml` - optional extras if needed.

## Chips

- [ ] **C1 - Thread identity.** Add failing tests proving checkpoint ids preserve session compatibility and include `thread_id`, then update state models. Files: `src/lucy/state.py`, `tests/test_checkpointing.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_checkpointing.py -q` -> checkpoint tests pass.
- [ ] **C2 - Store adapters.** Add conformance tests against real Compose Postgres and Redis, then implement both adapters. Files: `src/lucy/checkpoint/postgres.py`, `src/lucy/checkpoint/redis.py`, `tests/test_checkpoint_stores.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_checkpoint_stores.py -q` -> adapter tests pass.
- [ ] **C3 - Resume, heard-state correction, and gates.** Add
  kill/restart/resume replay coverage plus a barge-in test that restarts before
  the next caller turn and recovers only heard assistant text. Wire the resume
  seam, run full gates, and move the card. Files: `src/lucy/graph.py`,
  `src/lucy/testing/checkpoint.py`, `tests/test_checkpoint_stores.py`. Verify:
  `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use real local Postgres and Redis from Docker Compose.
- Do not hardcode URLs, TTLs, table names, or retry settings outside typed settings or named constants.
- Do not add platform tenancy or trace storage to the SDK.
- Do not break the existing in-memory checkpoint store.
- Do not persist generated assistant text that playback marks say the caller
  did not hear.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_checkpoint_stores.py -q` -> store conformance tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If Compose services are unavailable or the existing protocol cannot support an
adapter without breaking ABI, stop and record the exact incompatibility.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
