# 106 - Upload envelope-encrypted recordings to R2

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / recording storage
**Estimated effort:** ~16 h
**Depends on:** 70, 73, 104, platform cards 130, 131, 132
**State:** pending

## Goal

Encrypt recordings inside Rust and upload ciphertext directly to an
S3-compatible storage lease so R2 becomes the first production backend without
moving audio or storage credentials through Python.

## Context primer

- `docs/adr/0014-media-plane-recording.md` - recording ownership.
- `src/lucy/recording.py` - recording directives/metadata boundary.
- `media-gateway-rust/src/asterisk/media_session.rs` - live media capture.
- `media-gateway-rust/src/control/schema.rs` - recording control messages.
- `tests/fixtures/control_schema/recording.*.json` - golden contract.
- `../lucy-platform/backlog/pending/132_encrypt_retain_and_delete_r2_recordings.md` - platform policy.

## Spec

Add internal `RecordingObjectSink` for an exact object upload lease with S3
endpoint/bucket/object, temporary access/session credentials, permitted
operations, expiry, maximum bytes, expected content type, and checksum policy.
Implement multipart put/abort/head verification against real MinIO and
S3-compatible R2 behavior. Parent credentials never reach Rust.

Implement `application_envelope_v1`: generate a per-recording DEK, AES-256-GCM
stream/chunk encrypt mixed or dual-channel artifacts, obtain/store only the
Vault-wrapped DEK reference supplied by the control plane, and zeroize plaintext
key/buffers after finalization. Object metadata contains opaque ids, algorithm/
version, nonce/chunk scheme, ciphertext checksum, byte count, and no PII.

Recording consent/policy arrives before capture. Oversize, expiry, revoked
lease, network failure, multipart failure, checksum mismatch, duplicate
completion, and session cancellation abort/clean up idempotently and emit
sanitized recording state events only.

## Chips

- [ ] **C1 - S3 object sink contract.** Write real-MinIO/local-S3-protocol tests in `media-gateway-rust/tests/recording_object_sink.rs` for exact path/action/TTL, multipart, abort, head/checksum, oversize, expired/revoked credentials, retry and redaction; implement sink/settings. Verify: `docker compose run --rm lucy-media-gateway cargo test recording_object_sink` -> all pass.
- [ ] **C2 - Envelope encryption.** Write deterministic known-vector/chunk/dual-channel tests for AES-GCM, wrapped-key-reference metadata, tamper detection, cancellation, and zeroization-visible seams; implement encryption pipeline. Verify: `docker compose run --rm lucy-media-gateway cargo test recording_envelope` -> vectors and tamper negatives pass.
- [ ] **C3 - Control/session integration.** Extend golden fixtures additively for policy/upload lease/encryption metadata and test full capture -> encrypt -> upload -> head -> uploaded event using the simulator/local MinIO. Verify: gateway tests plus `docker compose run --rm lucy-api pytest tests/test_recording.py tests/test_control_ws_bridge.py -q` -> green.
- [ ] **C4 - R2 smoke, full gates, and bookkeeping.** Add an opt-in credential-redacted R2 smoke, run full Rust/Python/ruff/mypy gates, docs and reviews. Move to `need_human_testing` if live R2 evidence is unavailable. Verify: deterministic gates green.

## Do NOT

- Do not use mocks or mocking frameworks; use real MinIO/local S3 protocol servers, recorded fixtures, and opt-in R2 smoke tests.
- Do not hardcode endpoints, buckets, credentials, object limits, encryption versions, or TTLs outside typed settings/manifests.
- Do not send audio bytes or temporary storage credentials through telemetry.
- Do not store plaintext DEKs, provider keys, PII object names, or raw key material.
- Do not rely on R2-specific APIs in the common sink.
- Do not report uploaded before HEAD/checksum verification.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test recording_object_sink recording_envelope` -> exact-object upload, envelope, tamper, zeroize/cancel, and multipart cleanup tests pass.
- [ ] `docker compose run --rm lucy-api pytest tests/test_recording.py tests/test_control_ws_bridge.py -q` -> golden recording/control contracts pass without Python audio.
- [ ] Golden control schema remains compatible and audio stays out of Python.
- [ ] `docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` -> quality gates pass and optional live R2 evidence is recorded separately.

## Failure protocol

If encryption, upload, or HEAD verification is uncertain, abort/delete the
partial object where permitted, emit failed metadata and never declare it
available.

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
