# 71 - Add recording coordinator, BlobStore seam, and simulator WAV recording

**Sprint:** S11 - Platform feed (SDK)
**Epic:** Recordings
**Estimated effort:** ~10 h
**Depends on:** 70
**State:** pending

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
- `src/lucy/observe/events.py` - additive `audio_ref` metadata.
- `tests/test_recording.py` - local blob and WAV tests.

## Chips

- [ ] **C1 - Recording policy and BlobStore.** Write failing tests for disabled/default, consent required, and local presigned upload, then implement coordinator and store. Files: `src/lucy/recording.py`, `src/lucy/specs.py`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_recording.py -q -k policy` -> selected tests pass.
- [ ] **C2 - Simulator WAV upload.** Add a test that parses uploaded WAV files and verifies leg metadata, then extend `LocalGatewaySimulator`. Files: `src/lucy/transport/dev_gateway.py`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_recording.py -q` -> recording tests pass.
- [ ] **C3 - Telemetry metadata and gates.** Add `audio_ref` assertions, update docs if required, run full gates, and move the card. Files: `src/lucy/observe/events.py`, `docs/telemetry-wire-v1.md`, `tests/test_recording.py`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; uploads go through a real local HTTP/ASGI server.
- Do not hardcode blob URLs, recording channels, consent policy, sampling, or file paths outside typed settings/constants.
- Do not let audio bytes cross into Python outside deterministic test WAV generation owned by the simulator.
- Do not create orphan blobs when telemetry sampling or recording policy suppresses events.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_recording.py -q` -> recording tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If simulator-generated audio cannot represent the control-channel contract, stop
and record the missing gateway fixture rather than weakening the media boundary.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
