# 109 - Certify PSTN and human takeover

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / certification
**Estimated effort:** ~14 h
**Depends on:** 101, 104, 105, 106, 107, 108, platform cards 142 and 151
**State:** pending

## Goal

Certify one real provider-neutral call path from PSTN through AI, R2 recording,
browser takeover, return to AI, analysis and termination with reproducible
local evidence and an honest human-only gate.

## Context primer

- `backlog/pending/101_validate_real_cpaas_wss_media_sessions.md` - real WSS gate.
- `backlog/pending/104_generalize_rust_provider_media_drivers.md` - drivers.
- `backlog/pending/105_add_call_control_and_authority_directives.md` - controls.
- `backlog/pending/106_upload_envelope_encrypted_recordings_to_r2.md` - storage.
- `backlog/pending/108_add_browser_operator_webrtc_media.md` - takeover.
- `docs/cpaas-pstn.md` - live procedure.

## Spec

Build deterministic local/recorded certification for Telnyx and Twilio media
fixtures, generic STT/TTS/realtime driver manifests, consented dual-channel
recording, encrypted S3 upload, acoustic analysis, browser listen/takeover,
AI assist silence, return-to-AI checkpoint, conference/control failures, and
clean termination. Restart gateway/control/browser/storage and prove event/
authority/upload idempotency.

Run a separate real human checklist with an allowlisted verified destination:
originate accepted, authenticated public WSS reaches Rust, caller audio is
transcribed, AI response is generated, voice is audible, recording reaches R2
with verified ciphertext/checksum, browser listener hears the call, takeover
has no overlap, return resumes context, analysis links segments, and provider
hangup produces one terminal event. Capture only sanitized ids/timestamps/
metrics as evidence.

No CI/live secret coupling. If cloud, public WSS, browser, or human evidence is
missing, deterministic gates may pass but the card moves to
`need_human_testing`.

## Chips

- [ ] **C1 - Deterministic certification matrix.** Add local gateway/provider/storage/browser fixtures and one test covering AI call -> recording -> listen -> takeover -> return -> analysis -> end plus all restart/race failures. Verify: full targeted Rust/Python test command documented in the card -> green.
- [ ] **C2 - Security and plane-boundary audit.** Scan control messages, logs, OTel, JSONL, Kafka-facing fixtures, errors and object metadata for audio, PII, provider/storage/TURN secrets; assert public ABI and telemetry wire unchanged. Verify: architecture/security tests -> green.
- [ ] **C3 - Real human PSTN/R2/WebRTC run.** Follow `docs/cpaas-pstn.md`, record sanitized evidence for every checklist item, and verify audible caller/AI/human behavior. Verify: all items pass or document the exact missing prerequisite.
- [ ] **C4 - Full gates, reviews, and state move.** Run Docker pytest/ruff/format/mypy, complete gateway suite, docs, all mandatory reviewers, and move to `done` or `need_human_testing`. Verify: every deterministic gate green.

## Do NOT

- Do not use mocks or mocking frameworks; use local protocol servers, recorded provider fixtures, a real browser peer, and human PSTN evidence.
- Do not hardcode provider names, endpoints, credentials, phone numbers, limits, or latency budgets outside typed settings/registries.
- Do not count carrier dial acceptance as media success.
- Do not publish live phone numbers, credentials, signed URLs, audio, or raw transcripts as evidence.
- Do not close without audible takeover/return evidence.
- Do not change public provider ABI or telemetry wire v1.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test` -> deterministic full media-plane and authority certification passes.
- [ ] `docker compose run --rm lucy-api pytest && docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` -> plane/security/ABI and quality gates pass.
- [ ] Real PSTN, R2 and browser takeover checklist passes or is honestly parked.
- [ ] `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q` -> card/review contract passes with no unresolved P0/P1.

## Failure protocol

Any audio/secret/PII leak, authority overlap, unverified upload, cross-call
grant, or duplicate terminal state is blocking; keep the card in progress and
record exact evidence.

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

## Pending human testing

<!-- Record the real PSTN/WSS/R2/WebRTC evidence or exact missing prerequisite. -->
