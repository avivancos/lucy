# 70 - Add recording control-channel messages and ADR 0014

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Recordings
**Estimated effort:** ~8 h
**Depends on:** 72
**State:** pending

## Goal

Freeze the control-channel contract for media-plane recording before the Rust
gateway conformance fixtures land. Recording must be capability-negotiated,
consent-aware, and additive to the existing schema.

## Context primer

- `agents.md` - no mocks, typed config, and no audio in Python.
- `docs/adr/0014-media-plane-recording.md` - recording architecture decision.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - media-plane boundary.
- `src/lucy/transport/schema.py` - versioned WebSocket control-channel models.
- `backlog/pending/51_rust_gateway_implements_control_schema.md` - waits for these fixtures.

## Spec

Extend `SessionStarted` with `features: list[str]` so gateways can advertise
`recording`. Add downstream `recording.start` and `recording.stop` directives,
and upstream `recording.started`, `recording.uploaded`, and `recording.failed`
events. Models must be Pydantic, `extra="forbid"`, registered in `parse_event`,
and covered by golden round-trip fixtures.

Recording metadata includes session id, recording id, leg (`caller`, `agent`,
or `mixed`), blob id, upload URL reference, duration, byte count, SHA-256,
container, and consent reference. No audio bytes appear in the control channel.

## Files to create/modify

- `src/lucy/transport/schema.py` - additive recording models.
- `tests/test_transport_schema.py` - round-trip and parse tests.
- `docs/telemetry-wire-v1.md` - note recording-to-`audio_ref` relationship.
- `backlog/pending/51_rust_gateway_implements_control_schema.md` - dependency note if needed.

## Chips

- [ ] **C1 - Capability negotiation.** Write failing tests for `SessionStarted.features`, then implement the additive field. Files: `src/lucy/transport/schema.py`, `tests/test_transport_schema.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q -k features` -> selected tests pass.
- [ ] **C2 - Recording messages.** Add parse and round-trip tests for all recording directives/events, then implement them. Files: `src/lucy/transport/schema.py`, `tests/test_transport_schema.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q` -> transport schema tests pass.
- [ ] **C3 - Docs and conformance note.** Update wire docs and card 51 dependency note, run full gates, and move the card. Files: `docs/telemetry-wire-v1.md`, `backlog/pending/51_rust_gateway_implements_control_schema.md`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use schema round trips and deterministic fixtures.
- Do not hardcode blob URLs, consent refs, feature strings, or recording states outside named constants/models.
- Do not put audio bytes in Python, telemetry, or the control-channel payload.
- Do not break existing card 32 control-channel messages.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q` -> transport schema tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a recording field conflicts with the existing schema or Rust gateway needs a
different shape, leave the card active and document the proposed contract change.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
