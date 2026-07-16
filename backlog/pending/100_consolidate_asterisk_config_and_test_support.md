# 100 - Consolidate Asterisk config and test support

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~5 h
**Depends on:** 41
**State:** pending

## Goal

Remove the remaining non-behavioral duplication around Asterisk backend
configuration, text validation, and integration-test setup after the shared
media-session coordinator shipped in card 41.

## Context primer

- `agents.md` - Docker-only, TDD, no-mocks, and plane-boundary rules.
- `backlog/done/41_add_asterisk_audio_fork_adapter.md` - transport behavior and
  the simplicity-review findings that created this follow-up.
- `media-gateway-rust/src/asterisk/media_session.rs` - shared media lifecycle;
  this card must not duplicate or widen it.
- `media-gateway-rust/src/asterisk/server.rs` - AudioSocket configuration and
  current backend environment resolution.
- `media-gateway-rust/src/asterisk/runtime.rs` - Media WebSocket and ARI
  configuration paths.
- `media-gateway-rust/tests/` - repeated fixture and local-server setup.

## Spec

- Resolve the selected fixture or production provider backend exactly once in
  top-level gateway startup and pass typed, cloneable backend configuration to
  AudioSocket, Media WebSocket, or ARI runtime construction.
- Preserve environment variable names, defaults, validation, secret redaction,
  local-protocol-test restrictions, and all three transport behaviors.
- Provide one crate-local non-empty/control-character text validator used by
  media-plane and playback-queue inputs without changing the public ABI.
- Add a small `tests/common` module for fixture paths, media config, and bounded
  local protocol-server readiness. Helpers must use real sockets, recorded
  fixtures, and Tokio's paused clock or explicit deadlines; no mocks or sleeps.
- Keep transport-specific request parsing, output framing, metrics, and commands
  in their transport modules.

## Files to create/modify

- `media-gateway-rust/src/main.rs` - resolve the backend once at startup.
- `media-gateway-rust/src/asterisk/server.rs` - consume typed backend config.
- `media-gateway-rust/src/asterisk/runtime.rs` - consume typed backend config.
- `media-gateway-rust/src/asterisk/media_plane.rs` - shared text validation.
- `media-gateway-rust/src/asterisk/playback_queue.rs` - reuse validation.
- `media-gateway-rust/tests/common/mod.rs` - focused integration-test support.
- `media-gateway-rust/tests/asterisk_*.rs` - adopt helpers without changing
  coverage.
- This card file.

## Chips

- [ ] **C1 - Resolve backend configuration once.** First add a failing startup
  test proving environment resolution occurs once and the same typed config is
  passed to each selected transport. Refactor startup/config construction while
  preserving all validation and redaction. Files: `media-gateway-rust/src/main.rs`,
  `media-gateway-rust/src/asterisk/server.rs`,
  `media-gateway-rust/src/asterisk/runtime.rs`, and focused tests. Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test` -> all pass.
- [ ] **C2 - Reuse validation and deterministic test support.** First add
  negative tests for blank/control-character values and a stalled local server.
  Introduce one crate-local validator and minimal `tests/common` helpers, then
  migrate repeated setup. Files: `media-gateway-rust/src/asterisk/media_plane.rs`,
  `media-gateway-rust/src/asterisk/playback_queue.rs`,
  `media-gateway-rust/tests/common/mod.rs`, and affected integration tests.
  Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> all pass with no wall-time sleeps.
- [ ] **C3 - Full gates and bookkeeping.** Run isolated Rust tests, Rustfmt,
  Clippy, the Python suite, and the real Asterisk Compose smoke; fill review
  evidence and move this card. Verify:
  `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not change the public provider Protocols, spec models, or control wire.
- Do not move audio, provider credentials, or provider payloads into Python.
- Do not add a second media-session coordinator or transport abstraction.
- Do not use mocks, invented provider payloads, wall-time sleeps, or shared host
  Cargo artifacts.
- Do not hardcode provider names, URLs, paths, thresholds, ports, or timeouts;
  keep them in typed settings, fixtures, or named constants.
- Do not rename existing `LUCY_*` environment variables.

## Definition of Done

- [ ] Isolated Docker Rust suite with
  `CARGO_TARGET_DIR=/tmp/lucy-cargo-target` -> all tests pass.
- [ ] Docker Rustfmt and Clippy `-D warnings` -> exit 0.
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green.
- [ ] Real `telephony-lab` AudioSocket smoke reports non-zero inbound and
  outbound audio bytes and control messages.
- [ ] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If central resolution changes any transport behavior or makes per-session state
shared, leave the card pending/in progress, record the failing regression, and
restore per-session ownership before continuing. Do not weaken card 41 tests.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

- code-reviewer: PENDING
- test-auditor: PENDING
- docs-reviewer: PENDING
- simplicity-reviewer: PENDING
- security-reviewer: PENDING

Findings disposition:

- Pending.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
