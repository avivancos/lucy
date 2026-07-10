# 69 - Add persistent checkpoint stores and thread identity

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Graph state
**Estimated effort:** ~10 h
**Depends on:** 37, 72, 96
**State:** done

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
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - replace the stale
  future-adapter note with the landed thread/store contract.

## Chips

- [x] **C1 - Thread identity.** Add failing tests proving checkpoint ids preserve session compatibility and include `thread_id`, then update state models. Files: `src/lucy/state.py`, `tests/test_checkpointing.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_checkpointing.py -q` -> checkpoint tests pass.
- [x] **C2 - Store adapters.** Add conformance tests against real Compose Postgres and Redis, then implement both adapters. Files: `src/lucy/checkpoint/postgres.py`, `src/lucy/checkpoint/redis.py`, `tests/test_checkpoint_stores.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_checkpoint_stores.py -q` -> adapter tests pass.
- [x] **C3 - Resume, heard-state correction, and gates.** Add
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

- [x] `docker compose run --rm lucy-api pytest tests/test_checkpoint_stores.py -q` -> store conformance tests pass
- [x] `docker compose run --rm lucy-api pytest` -> full suite green
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Post-task audit done; no additional follow-up card was required

## Failure protocol

If Compose services are unavailable or the existing protocol cannot support an
adapter without breaking ABI, stop and record the exact incompatibility.

## Improvements noted

- Replay still treated `session_id` as the isolation boundary. It now accepts
  multiple sessions in one thread and rejects only cross-thread histories.
- Redis append-only corrections initially moved an older checkpoint to the end
  of logical history. Reads now retain first-insertion order while selecting
  the latest payload for each checkpoint id.
- Playback reconciliation writes only when state differs from the graph's
  final checkpoint; normal uninterrupted turns avoid a redundant store write.
- ADR 0011's stale "persistent adapters later" wording now documents the
  optional Postgres/Redis stores and stable thread identity.

## Review evidence

- code-reviewer: PASS - thread/session ownership, idempotent correction,
  cross-call resume, cancellation, and replay ordering match the card without
  changing provider or TurnDriver Protocols.
- test-auditor: PASS - the shared conformance helper runs against real Compose
  Postgres and Redis; the negative barge-in restart test proves unplayed text
  cannot survive and a second call continues the same thread.
- docs-reviewer: PASS - ADR 0011 and the card now match the landed optional
  adapters, thread semantics, and durable playback correction.
- simplicity-reviewer: PASS - one store Protocol, two direct adapters, and one
  reusable conformance helper; no repository or service abstraction was added.
- security-reviewer: PASS - URLs are injected, SQL values are parameterized,
  table/index names are module constants, and no credentials or transcript
  payloads are logged or exported.

Findings disposition:

- [P1][code-001] playback-safe state was not durable after barge-in - fixed
  with idempotent final-checkpoint correction.
- [P1][code-002] replay rejected valid cross-session thread history - fixed.
- [P2][code-003] Redis late correction changed logical ordering - fixed by
  first-position/latest-value deduplication.
- [P2][simp-001] every normal turn would issue an extra correction write -
  fixed by state equality short-circuit.
