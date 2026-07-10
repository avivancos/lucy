# 70 - Add recording control-channel messages and ADR 0014

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Recordings
**Estimated effort:** ~8 h
**Depends on:** 72
**State:** done

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

- [x] **C1 - Capability negotiation.** Write failing tests for `SessionStarted.features`, then implement the additive field. Files: `src/lucy/transport/schema.py`, `tests/test_transport_schema.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q -k features` -> selected tests pass.
- [x] **C2 - Recording messages.** Add parse and round-trip tests for all recording directives/events, then implement them. Files: `src/lucy/transport/schema.py`, `tests/test_transport_schema.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q` -> transport schema tests pass.
- [x] **C3 - Docs and conformance note.** Update wire docs and card 51 dependency note, run full gates, and move the card. Files: `docs/telemetry-wire-v1.md`, `backlog/pending/51_rust_gateway_implements_control_schema.md`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use schema round trips and deterministic fixtures.
- Do not hardcode blob URLs, consent refs, feature strings, or recording states outside named constants/models.
- Do not put audio bytes in Python, telemetry, or the control-channel payload.
- Do not break existing card 32 control-channel messages.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_transport_schema.py -q` -> transport schema tests pass
- [x] `docker compose run --rm lucy-api pytest` -> full suite green
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a recording field conflicts with the existing schema or Rust gateway needs a
different shape, leave the card active and document the proposed contract change.

## Improvements noted

- `Envelope.session_id` remains the single session identity; recording payloads
  do not duplicate it.
- `upload_url_ref` is an opaque identifier constrained to a credential-safe
  character set. Signed URLs and query strings are rejected by schema validation.
- The control schema now has 27 registered message types. Card 51 was updated to
  generate and consume all 27 golden fixtures, including the five recording types.
- `SessionStarted.features` defaults to an empty list, preserving every legacy
  gateway payload while allowing explicit recording capability negotiation.
- Final evidence: 10 transport-schema tests and 458 root tests passed; Docker
  ruff, format, and mypy gates were clean.

## Review evidence

- code-reviewer: PASS - additive models, registry wiring, and envelope ownership reviewed; no P0-P3 findings.
- test-auditor: PASS - legacy default, five round trips, extra audio, invalid hash, and signed-URL rejection covered.
- docs-reviewer: PASS - telemetry `audio_ref` join semantics and card 51's 27-type conformance scope are explicit.
- simplicity-reviewer: PASS - seven small Pydantic models reuse the existing schema registry and envelope.
- security-reviewer: PASS - no audio bytes or credential-bearing URL can enter recording payloads.

Findings disposition:

- None.
