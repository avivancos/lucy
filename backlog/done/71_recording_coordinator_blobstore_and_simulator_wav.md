# 71 - Add recording coordinator, BlobStore seam, and simulator WAV recording

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Recordings
**Estimated effort:** ~10 h
**Depends on:** 70
**State:** done

## Goal

Prove the recording seam end to end without real telephony: the SDK coordinates
recording policy, the simulator writes real WAV assets, uploads through a local
blob server, and telemetry emits `audio_ref` records the platform can consume.

## Context primer

- `agents.md` - no mocks, deterministic simulators, and typed config.
- `docs/adr/0014-media-plane-recording.md` - recording stays in the media plane.
- `src/lucy/transport/dev_gateway.py` - `LocalGatewaySimulator` records directives and events.
- `src/lucy/observe/events.py` - `AudioRefEvent` wire model.
- `src/lucy/specs.py` - add `RecordingSpec` next to observability/voice settings.

## Spec

Add `src/lucy/recording.py` with a `BlobStore` Protocol, `LocalBlobStore`, and
`RecordingCoordinator`. Add `RecordingSpec` with `enabled=False`,
`channels: dual|mixed`, and `require_consent=True`. The coordinator obeys
`record_audio`, consent reference, and sampling to prevent orphan blobs.

Extend `LocalGatewaySimulator` to generate deterministic real WAV files for
caller and agent legs using stdlib `wave`, upload them through real HTTP to an
in-process ASGI blob server, and emit `recording.uploaded`. Emit `audio_ref`
events per leg with additive leg, duration, container, SHA-256, and consent
metadata.

## Files to create/modify

- `src/lucy/recording.py` - BlobStore and coordinator.
- `src/lucy/specs.py` - `RecordingSpec`.
- `src/lucy/transport/dev_gateway.py` - deterministic WAV recording.
- `src/lucy/transport/schema.py` - opaque consent reference validation.
- `src/lucy/observe/__init__.py` - tracer-owned audio eligibility and metadata.
- `src/lucy/observe/events.py` - additive `audio_ref` metadata.
- `src/lucy/testing/__init__.py` - recording simulator export.
- `src/lucy/testing/recording.py` - deterministic BlobStore failure simulator.
- `docs/telemetry-wire-v1.md` - recording-derived `audio_ref` contract.
- `docs/adr/0014-media-plane-recording.md` - landed conformance and pre-release ABI disposition.
- `tests/test_recording.py` - local blob and WAV tests.

## Chips

- [x] **C1 - Recording policy and BlobStore.** Write failing tests for disabled/default, consent required, and local presigned upload, then implement coordinator and store. Files: `src/lucy/recording.py`, `src/lucy/specs.py`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_recording.py -q -k policy` -> selected tests pass.
- [x] **C2 - Simulator WAV upload.** Add a test that parses uploaded WAV files and verifies leg metadata, then extend `LocalGatewaySimulator`. Files: `src/lucy/transport/dev_gateway.py`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_recording.py -q` -> recording tests pass.
- [x] **C3 - Telemetry metadata and gates.** Add `audio_ref` assertions, update docs if required, run full gates, and prepare final bookkeeping. Files: `src/lucy/observe/events.py`, `docs/telemetry-wire-v1.md`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; uploads go through a real local HTTP/ASGI server.
- Do not hardcode blob URLs, recording channels, consent policy, sampling, or file paths outside typed settings/constants.
- Do not let audio bytes cross into Python outside deterministic test WAV generation owned by the simulator.
- Do not create orphan blobs when telemetry sampling or recording policy suppresses events.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_recording.py -q` -> recording tests pass
- [x] `docker compose run --rm lucy-api pytest` -> full suite green
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If simulator-generated audio cannot represent the control-channel contract, stop
and record the missing gateway fixture rather than weakening the media boundary.

## Improvements noted

- The tracer is the single source of truth for both head sampling and the
  `record_audio` kill switch, preventing policy drift and unreferenced uploads.
- Local uploads use one-shot randomized references and receipts tied to the
  exact blob. Confirmation verifies WAV duration, byte count, and SHA-256;
  failed, rejected, or cancelled plans attempt both cleanup operations and
  retain retryable coordinator state until both target and object are deleted.
- The simulator accepts only loopback HTTP upload destinations, streams through
  a named size limit, requires `audio/wav`, rejects unsupported containers, and
  classifies permanent versus retryable HTTP failures.
- Lucy-generated recording/blob IDs and caller-supplied consent references are
  bounded opaque values and reject phone-like or PII-bearing values before
  BlobStore side effects. BlobStore-returned upload references are validated
  immediately and trigger rollback if invalid.
- `RecordingSpec` and the stricter recording reference schema land before card
  46's first PyPI publication, so no published serialized ABI needs migration.
- No implementation finding was deferred. Existing API deprecation warnings are
  already owned by launch-base card 93 and platform dependency card 108.

## Review evidence

- `code-reviewer`: PASS. Resolved cleanup retryability/cancellation, opaque
  reference collisions, upload-first completion correlation, and atomic
  recording-ID reservation findings (`code-010` through `code-015`).
- `test-auditor`: PASS. Added adversarial coverage for stale receipts, fully
  awaited revocation, non-destructive uncorrelated events, cleanup cancellation,
  concurrent ID reservation, and reservation release after rollback.
- `simplicity-reviewer`: PASS. Centralized recording eligibility in `Tracer`,
  simplified BlobStore state, and removed the single-use rejection wrapper.
- `docs-reviewer`: PASS. ADR 0014, telemetry wire v1, lifecycle claims, and the
  complete file inventory agree with the implementation.
- `security-reviewer`: PASS. Verified client-side audio/PII policy, one-shot
  receipts, upload limits, loopback-only simulator URLs, race-safe revocation,
  and cross-plan tamper cleanup.
- Final gates: recording tests `42 passed`; full suite `516 passed, 2 deselected`;
  Docker ruff check, format check, mypy, and clean image build passed.
- No review finding was deferred. Three pre-existing deprecation warnings remain
  owned by cards 93 and 108 as recorded above.
