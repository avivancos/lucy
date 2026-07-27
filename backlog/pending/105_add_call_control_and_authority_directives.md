# 105 - Add call control and authority directives

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / call control
**Estimated effort:** ~16 h
**Depends on:** 42, 51, 101, 104
**State:** pending

## Goal

Give the platform provider-neutral originate, hangup, transfer/conference, AI
pause/flush, and speech-authority controls with gateway acknowledgements that
guarantee one outbound speaker.

## Context primer

- `media-gateway-rust/src/cpaas/call_control.rs` - current provider hangup client.
- `media-gateway-rust/src/cpaas/runtime.rs` - directive mapping and unsupported transfer.
- `media-gateway-rust/src/control/schema.rs` - versioned control channel.
- `media-gateway-rust/src/control/session.rs` - session command lifecycle.
- `docs/cpaas-pstn.md` - real provider behavior.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - cancellation/checkpoint guarantees.

## Spec

Add internal `TelephonyCallControlDriver` capabilities for originate, hangup,
transfer, conference-add/remove, and DTMF. Implement existing Telnyx/Twilio
operations only where recorded/local protocol evidence proves them; unsupported
capabilities report typed unavailable and never emulate audio forwarding.

Extend the versioned control schema additively with authority commands and
acks: request/listen, claim human, release to AI, pause/resume AI, flush
playback, and hangup. Every command carries session/call, command id,
expected authority version, deadline, and sanitized reason code. Authority
states are AI, transition-muted, human, and terminal.

Claim human must atomically cancel generation capability, reject later TTS,
clear provider playback, checkpoint, and ack silence before operator unmute.
Release must confirm operator mute/turn commit before restoring AI output.
Commands are idempotent and reject duplicates with conflicting payloads,
timeouts, stale versions, late provider acks, and terminal calls.

## Chips

- [ ] **C1 - Telephony control trait.** In `media-gateway-rust/src/cpaas/call_control.rs` and `media-gateway-rust/tests/cpaas_call_control.rs`, write failing real-local-server tests for capability discovery, originate/hangup/transfer/conference cleanup, unsupported provider, auth redaction, timeout, and duplicate idempotency; implement the trait/adapters. Verify: `docker compose run --rm lucy-media-gateway cargo test cpaas_call_control` -> all pass.
- [ ] **C2 - Authority schema and state machine.** Add golden fixture tests first in `media-gateway-rust/tests/authority_control.rs`, `tests/test_control_ws_bridge.py`, and `tests/fixtures/control_schema/` for valid states, stale/conflicting commands, cancel/flush/ack ordering, late TTS rejection, terminal behavior, and exactly one speaker; implement schema/session/runtime changes. Verify: Rust authority tests plus `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py -q` -> green.
- [ ] **C3 - Race and failure matrix.** Use ManualClock/local protocols to test takeover during TTS, STT, MCP, barge-in, provider timeout, disconnect, two operators, return-to-AI and partial conference failure. Files: Rust integration tests and `tests/test_voice_session.py`. Verify: targeted Rust/Python tests -> no overlapping output.
- [ ] **C4 - Docs, full suite, and bookkeeping.** Update control schema/CPaaS docs, run full gates, fill review evidence, and move the card. Verify: Docker pytest/ruff/format/mypy and full gateway tests -> green.

## Do NOT

- Do not use mocks or mocking frameworks; use real local CPaaS/control protocol servers and recorded fixtures.
- Do not hardcode provider names, URLs, credentials, timeouts, or authority budgets outside typed registries/settings.
- Do not unmute a human before silence acknowledgement.
- Do not emit AI TTS while human authority is active.
- Do not hardcode provider-specific operations in the platform-neutral schema.
- Do not close media WebSocket as a substitute for provider call termination.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test` -> provider-neutral call-control and authority race tests pass.
- [ ] `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py tests/test_voice_session.py -q` -> versioned control and session cancellation contracts pass.
- [ ] One outbound speaker is guaranteed across failures.
- [ ] `docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` -> Python quality gates pass.

## Failure protocol

On uncertain provider playback or authority state, enter transition-muted,
reject both new output paths, emit a sanitized error and require resync.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

- code-reviewer: PASS | FAIL - <report ref or summary>
- test-auditor: PASS | FAIL - <report ref or summary>
- docs-reviewer: PASS | FAIL - <report ref or summary>
- simplicity-reviewer: PASS | FAIL - <report ref or summary>
- security-reviewer: PASS | FAIL - <report ref or summary>

Findings disposition:

- [P0|P1|P2|P3][reviewer-NNN] finding - fixed | follow-up card NN | rejected: rationale
