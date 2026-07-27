# 104 - Generalize Rust provider media drivers

**Sprint:** S16 - Managed call media plane
**Epic:** media gateway / provider drivers
**Estimated effort:** ~14 h
**Depends on:** 42, 51, 68, 102
**State:** pending

## Goal

Replace the gateway's hardcoded Deepgram/ElevenLabs media pair with internal,
typed provider-driver seams so platform manifests can select compatible STT,
TTS, and native-realtime implementations without changing Lucy's public ABI.

## Context primer

- `media-gateway-rust/src/asterisk/provider_media.rs` - current fixed STT/TTS configuration and sockets.
- `media-gateway-rust/src/asterisk/media_session.rs` - media lifecycle.
- `media-gateway-rust/src/cpaas/runtime.rs` - CPaaS media/runtime integration.
- `media-gateway-rust/tests/asterisk_provider_media.rs` - real local provider protocol tests.
- `packages/lucy-deepgram/` and `packages/lucy-elevenlabs/` - open provider packages whose public contracts stay frozen.
- `agents.md` - plane, no-hardcode, and no-mocks rules.

## Spec

Introduce internal Rust `SttMediaDriver`, `TtsMediaDriver`, and
`RealtimeMediaDriver` traits plus a registry-owned `MediaDriverManifest` with
driver key/version, capabilities, config schema revision, codecs, sample rates,
languages, cancellation/mark support, and health. Driver construction consumes
typed resolved config and ephemeral credentials; registry keys and provider
defaults live in settings/registry constants, not UI or session logic.

Move the existing Deepgram STT and ElevenLabs TTS implementations behind the
traits without changing frame pacing, barge-in, marks, transcript events,
redaction, or public Python provider protocols. Add a native-realtime driver
seam that remains unavailable until an implementation is registered; do not
route realtime audio through Python.

Compatibility validation rejects codec/sample-rate/capability mismatch before
opening a provider socket. Credentials are redacted, held only for connection
lifetime, and never appear in manifests, errors, control events, or debug
formatting.

## Chips

- [ ] **C1 - Driver contracts and conformance harness.** In `media-gateway-rust/src/provider/` and `media-gateway-rust/tests/provider_driver_contract.rs`, write failing tests for manifest validation, codec negotiation, lifecycle, partial/final STT, TTS marks/cancel, native-realtime unavailable, redaction, and bounded frames; then add traits/registry. Verify: `docker compose run --rm lucy-media-gateway cargo test provider_driver_contract` -> all new tests pass.
- [ ] **C2 - Move existing STT/TTS behind drivers.** Test first in `media-gateway-rust/tests/asterisk_provider_media.rs` and `media-gateway-rust/tests/cpaas_runtime.rs` for exact existing Deepgram/ElevenLabs behavior through registry resolution; refactor `provider_media.rs` and runtime wiring. Verify: `docker compose run --rm lucy-media-gateway cargo test asterisk_provider_media cpaas_runtime` -> existing and new tests pass.
- [ ] **C3 - Config/control compatibility.** Add recorded golden fixtures for driver manifests/config references and prove Python/control-schema/public provider ABI output is unchanged. Files: `media-gateway-rust/src/control/schema.rs`, `tests/fixtures/control_schema/`, `tests/test_control_ws_bridge.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py tests/test_architecture_adrs.py -q` -> green.
- [ ] **C4 - Full suite and bookkeeping.** Run Rust/Python/ruff/mypy gates, fill Improvements noted and reviews, and move the card. Verify: `docker compose run --rm lucy-api pytest && docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api mypy src` plus gateway tests -> green.

## Do NOT

- Do not change the public `SttProvider`, `TtsProvider`, `RealtimeProvider`, or spec ABI.
- Do not route audio frames into Python.
- Do not hardcode new provider/model/URL defaults outside registry/settings.
- Do not use mocks; use real local WebSocket protocol servers and recorded frames.

## Definition of Done

- [ ] `docker compose run --rm lucy-media-gateway cargo test` -> existing Deepgram/ElevenLabs behavior passes through generic drivers and codec/capability/secret negatives are green.
- [ ] `docker compose run --rm lucy-api pytest tests/test_control_ws_bridge.py tests/test_architecture_adrs.py -q` -> public Python and control golden contracts remain compatible.
- [ ] Public Python and control golden contracts remain compatible.
- [ ] `docker compose run --rm lucy-api ruff check src tests && docker compose run --rm lucy-api ruff format --check src tests && docker compose run --rm lucy-api mypy src` -> quality gates pass.

## Failure protocol

If an existing behavior cannot be represented without a public ABI change,
leave the card in progress, document the exact ABI conflict, and do not add a
provider-specific escape hatch.

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
