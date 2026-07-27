# 108 - Add browser operator WebRTC media

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / operator media
**Estimated effort:** ~16 h
**Depends on:** 45, 104, 105, platform cards 145 and 146
**State:** pending

## Goal

Connect an authorized browser operator directly to the Rust media plane for
listen-only and takeover audio while preserving one-speaker authority and
keeping WebRTC media out of Python.

## Context primer

- `backlog/pending/45_rust_native_sip_spike.md` - dependency/security verdict for WebRTC media crates.
- `media-gateway-rust/src/control/session.rs` - authenticated sessions.
- `media-gateway-rust/src/asterisk/media_session.rs` - mixing/playback ownership.
- `backlog/pending/105_add_call_control_and_authority_directives.md` - authority gate.
- `docs/telephony-connectivity.md` - browser/WebRTC boundary.
- `../lucy-platform/backlog/pending/146_issue_operator_media_sessions.md` - bootstrap contract.

## Spec

Implement the internal `OperatorMediaDriver` using the WebRTC dependency and
version selected by card 45's ADR. Accept a short-lived one-use signed bootstrap
containing call/session, operator, tenant/environment, listen/takeover mode,
audience, nonce, expiry, and expected authority version. Negotiate WebRTC
directly with the gateway; support ICE, configured STUN/TURN, Opus/PCM
conversion, jitter/flow control, mute, listen, microphone, disconnect/rejoin,
and revocation.

Every join starts listen-only/muted. Operator microphone frames enter the call
mix only after card 105's committed human authority. AI playback is rejected
while human authority is active. Release mutes operator media before AI output
resumes. Enforce one operator authority, bounded observers, tenant/call scope,
origin/audience, replay prevention, and content-free diagnostics.

Use a real headless browser/fake media device and local gateway in tests; do not
mock WebRTC. If card 45's verdict is Wait/Reject with no approved dependency,
record the blocker and do not invent a transport.

## Chips

- [ ] **C1 - Bootstrap and WebRTC transport.** Test one-use/expiry/audience/origin/call scope, ICE/STUN/TURN config, codec negotiation, listen-only receive, mute, and revoke using a real local browser/peer; implement driver. Verify: gateway WebRTC integration test -> listen-only audio flows and replay fails.
- [ ] **C2 - Authority/mixing integration.** Test microphone gating, cancel/flush/ack, human audio, AI TTS rejection, release/mute/resume, two operators, barge-in, packet loss, reconnect and terminal call. Files: Rust operator/authority integration tests. Verify: no scenario emits overlapping outbound sources.
- [ ] **C3 - Control golden fixtures and security audit.** Add additive operator-media control fixtures, TURN credential redaction, origin/cert guidance, resource bounds and failure events; run Python bridge/architecture tests. Verify: `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py tests/test_architecture_adrs.py -q` -> green.
- [ ] **C4 - Full suite and bookkeeping.** Run gateway/Python/ruff/format/mypy gates, docs and reviews; park honestly if dependency or human media verification remains. Verify: sanctioned gates green.

## Do NOT

- Do not use mocks or mocking frameworks; use a real local WebRTC peer, headless browser media devices, and recorded impairment fixtures.
- Do not hardcode STUN/TURN URLs, credentials, codecs, origins, TTLs, or media budgets outside typed settings/manifests.
- Do not route WebRTC audio/SDP media through FastAPI.
- Do not admit microphone frames before authority acknowledgement.
- Do not log bootstrap/TURN credentials or media.
- Do not choose a WebRTC crate contrary to card 45's recorded verdict.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test operator_media` -> real-peer listen/takeover and bootstrap replay/scope/origin/expiry negatives pass.
- [ ] `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py tests/test_architecture_adrs.py -q` -> control and plane-boundary contracts pass.
- [ ] One-speaker authority holds under loss/reconnect/races.
- [ ] `docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` -> quality gates pass.

## Failure protocol

On authority, identity, origin, or media-state uncertainty immediately mute/
drop operator transmission, keep AI output blocked if needed, and require
control resync.

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
