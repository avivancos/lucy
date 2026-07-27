# 107 - Analyze recordings in the media plane

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / recording analysis
**Estimated effort:** ~16 h
**Depends on:** 99, 106, platform card 153
**State:** pending

## Goal

Produce versioned acoustic evidence from authorized recordings in Rust so the
platform can measure calls without receiving or decoding audio.

## Context primer

- `media-gateway-rust/src/asterisk/media_session.rs` - audio/channel behavior.
- `media-gateway-rust/tests/asterisk_provider_media.rs` - deterministic media fixtures.
- `src/lucy/recording.py` - recording metadata contract.
- `backlog/done/99_emit_speaker_activity_analytics.md` - live talk-time semantics.
- `../lucy-platform/backlog/pending/153_run_versioned_recording_analysis.md` - job/result contract.
- `agents.md` - media-plane and no-mocks rules.

## Spec

Add a bounded Rust recording-analysis worker/library that accepts an
authenticated job with encrypted object lease, wrapped-key reference,
recording/channel metadata, analysis config/version, purpose/consent, deadline,
and callback audience. It fetches/decrypts in the media plane and emits
progress plus one sanitized typed result; it never exposes audio or DEKs.

Compute talk time by participant, AI/human/customer ratio, silence, overlap,
interruptions/barge-in, turn gaps, speaking rate, clipping/level quality,
transcript coverage/confidence inputs, disclosure segment evidence, and
timestamped bounded segments. Definitions, units, algorithms and numeric
bounds are versioned named constants/settings. Missing channels/evidence yield
partial/unavailable, never invented zero.

Jobs are idempotent, cancellable, deadline/retention/revoke aware, memory/
duration bounded, and reject corrupt/tampered/unsupported files and callbacks.
Use deterministic generated/recorded WAV fixtures and ManualClock; no external
ML model is required for the acoustic v1.

## Chips

- [ ] **C1 - Analysis contract and fixtures.** Write failing tests in `media-gateway-rust/tests/recording_analysis.rs` with generated dual-channel WAVs for each measure, partial/missing channel, exact segment bounds, config/version, and result schema; implement pure analysis library. Verify: `docker compose run --rm lucy-media-gateway cargo test recording_analysis` -> expected numeric tolerances pass.
- [ ] **C2 - Encrypted storage worker.** Test real MinIO fetch, envelope decrypt/tamper, lease scope/expiry/revoke, cancellation/deadline, memory/size limit, corrupt codec, and zeroization; implement worker protocol/binary. Verify: targeted Rust integration tests -> all negative paths fail safely.
- [ ] **C3 - Platform callback/golden contract.** Add recorded job/progress/result fixtures and a local protocol server test proving only typed metrics/segments cross the boundary and duplicate callbacks are stable. Files: Rust tests, `tests/fixtures/recording_analysis/`, architecture contract tests. Verify: Docker Rust/Python targeted tests -> green.
- [ ] **C4 - Full gates and bookkeeping.** Run full gateway/Python/ruff/format/mypy gates, docs and all reviews; move the card. Verify: sanctioned full commands -> green.

## Do NOT

- Do not use mocks or mocking frameworks; use deterministic WAVs, real MinIO, and local authenticated protocol servers.
- Do not hardcode algorithms, thresholds, units, segment limits, URLs, or resource budgets outside versioned settings/constants.
- Do not process recording bytes in Python.
- Do not emit audio, decrypted chunks, DEKs, phone numbers, or transcript text in OTel.
- Do not infer missing participant/channel measures.
- Do not add an opaque external ML dependency for deterministic acoustic v1.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test recording_analysis` -> all acoustic measures, encrypted fetch/decrypt/cancel/tamper/resource-limit cases pass.
- [ ] `docker compose run --rm lucy-api pytest tests/test_architecture_adrs.py tests/test_recording.py -q` -> only sanitized typed evidence crosses to platform.
- [ ] Only sanitized typed evidence crosses to platform.
- [ ] `docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` -> Python quality gates pass.

## Failure protocol

Reject the entire result on schema, decrypt, integrity or bound failure; return
sanitized unavailable/error evidence and never partial unvalidated metrics.

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
