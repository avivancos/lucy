# 41 - Add the Asterisk audio-fork adapter

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~16 h
**Depends on:** 40, 51
**State:** done

## Goal

Make Lucy speak Asterisk-native audio-fork paths through the Rust media
gateway: AudioSocket as the universal baseline, Media over WebSocket as the
modern path, and ARI externalMedia RTP as the legacy fallback.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - no mocks, Docker Compose runtime, typed settings, and staged
  task commits.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - Rust owns
  sockets, jitter, codecs, and audio fan-out; Python gets events.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - control-channel event
  and directive boundary.
- `docs/adr/0012-telephony-native-first.md` - Asterisk is the P2 PBX baseline.
- `docs/telephony-connectivity.md` - protocol facts for AudioSocket, Media
  over WebSocket, and ARI externalMedia.
- `backlog/done/40_build_local_asterisk_telephony_lab.md` - local Asterisk
  service names, extensions, and scripted caller commands.
- `backlog/done/51_rust_gateway_implements_control_schema.md` - golden schema
  fixtures and Rust control-channel conformance.
- `media-gateway-rust/` - implement adapter code in the media plane only.
- `src/lucy/specs.py` and `src/lucy/settings.py` - add SDK transport settings
  without hardcoded endpoints.

## Spec

- Add an Asterisk transport registry entry that resolves from a spec string or
  typed config into one of three modes: `audiosocket`, `media_websocket`, or
  `ari_external_media`.
- Implement Rust adapter modules behind named config structs. Each adapter
  converts Asterisk media/control signals into the card 51 control-channel
  schema and converts Lucy downstream directives into playback, cancel, DTMF,
  transfer, or hangup operations.
- AudioSocket mode: parse TLV frames, require UUID first, accept DTMF and 8 kHz
  signed linear audio, emit `dtmf` and STT/provider audio fan-out inside Rust,
  and never serialize raw audio to Python.
- Media over WebSocket mode: handle binary media frames and text control frames,
  including MEDIA_START, DTMF_END, MEDIA_XOFF, MEDIA_XON, and media marks.
- ARI externalMedia mode: create and bridge the external channel through ARI,
  receive RTP locally, and map hangup/DTMF events through the control schema.
- Record transport metrics (`rtt_ms`, jitter, packet loss where available) so
  Python can fill `LatencyWaterfall.transport_ms`.
- Add the first production Rust media backend for the provider pair already
  shipped by card 29: caller PCM streams directly to Deepgram STT and
  ElevenLabs TTS audio streams directly back to the active Asterisk transport.
  Provider URLs, models, credentials, framing, endpointing, and output format
  come from typed environment config; secrets are never logged or serialized.
  The recorded fixture backend remains local-lab/test-only.
- Add local protocol tests and compose smoke tests against the card 40 lab.

## Files to create/modify

- `media-gateway-rust/Cargo.toml` - adapter dependencies and features.
- `media-gateway-rust/Dockerfile.test` - reproducible Rustfmt and Clippy image.
- `media-gateway-rust/src/` - Asterisk adapter modules and config.
- `media-gateway-rust/tests/` - protocol and conformance tests.
- `src/lucy/specs.py` - transport spec values for Asterisk modes.
- `src/lucy/settings.py` - typed Asterisk settings.
- `tests/test_specs.py` and `tests/test_settings.py` - Python registry tests.
- `docs/local-telephony-lab.md` - adapter execution notes.
- This card file.

## Chips

- [x] **C1 - Transport config and registry.** Write failing Python tests for
  Asterisk transport spec parsing and typed settings. Then add the registry
  entries and settings. Files: `src/lucy/specs.py`, `src/lucy/settings.py`,
  `tests/test_specs.py`, `tests/test_settings.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> all pass.
- [x] **C2 - AudioSocket adapter.** Write Rust protocol tests first for UUID,
  audio, DTMF, hangup, malformed frame, and wrong first frame. Then implement
  AudioSocket parsing and control-schema emission. Files:
  `media-gateway-rust/src/`, `media-gateway-rust/tests/`. Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> all pass.
- [x] **C3 - Media WebSocket adapter.** Write Rust tests first for binary
  media, JSON/text control, flow-control events, media marks, and cancel.
  Then implement the adapter. Files: `media-gateway-rust/src/`,
  `media-gateway-rust/tests/`. Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> all pass.
- [x] **C4 - ARI externalMedia fallback.** Write tests using a local ARI
  protocol server and RTP socket. Then implement channel create, bridge, RTP
  receive, and hangup cleanup. Files: `media-gateway-rust/src/`,
  `media-gateway-rust/tests/`. Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> all pass.
- [x] **C5 - Asterisk lab smoke and docs.** Run the card 40 lab through the
  implemented adapter and document the command. Files:
  `docs/local-telephony-lab.md`, this card file. Verify:
  `docker compose --profile telephony-lab run --rm telephony-caller` ->
  scripted call reaches Lucy through the Asterisk adapter.
- [x] **C6 - Production provider media backend.** Write failing Rust tests
  against local WebSocket protocol servers replaying card 29's recorded
  Deepgram and ElevenLabs JSONL fixtures. Then implement typed provider config,
  direct PCM fan-out, transcript mapping, streaming playback, cancellation,
  bounded connection/idle behavior, codec conversion, and secret redaction.
  Verify both AudioSocket and Media WebSocket consume the backend without audio
  crossing the Python control channel. Files: `media-gateway-rust/src/`,
  `media-gateway-rust/tests/`, `media-gateway-rust/Cargo.toml`, `.env.example`,
  `docker-compose.yml`, and `docs/local-telephony-lab.md`. Verify:
  `docker compose --profile gateway-it run --rm lucy-gateway-tests cargo test`
  -> all pass.
- [x] **C7 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
  mypy, Rust tests, record evidence, and move this card. Verify:
  `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use local protocol servers, real
  sockets, the Asterisk lab, and recorded fixtures.
- Do not hardcode hosts, ports, provider names, codecs, budgets, or mode names
  outside typed settings, registries, or named constants.
- Do not send audio frames to Python or add ad hoc control-channel fields.
- Do not make CPaaS, FreeSWITCH, or managed-edge code part of this adapter.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> Asterisk transport config tests pass
- [x] `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> Rust adapter tests pass
- [x] `docker compose --profile telephony-lab run --rm telephony-caller` ->
  scripted Asterisk call reaches Lucy
- [x] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api mypy src` -> exit 0
- [x] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If any Asterisk path cannot be implemented against verified protocol behavior,
leave the card in `in_progress/`, record the exact failure, and raise a
follow-up only after documenting why the fallback path is still viable.

## Improvements noted

- The Rust gate now mounts the repository root so card 51's real audio and
  control fixtures remain available at their checked-in relative paths. A
  clean rebuild is required after changing the container mount because Rust
  embeds `CARGO_MANIFEST_DIR` in the test binary.
- AudioSocket rejects missing or duplicate UUIDs, malformed TLV lengths,
  invalid DTMF, odd PCM samples, remote errors, unknown types, and data after
  hangup. Raw PCM is represented only as an internal media-plane frame.
- Media over WebSocket implements Asterisk's documented JSON control format,
  keeps binary frames in Rust, blocks writes between `MEDIA_XOFF` and
  `MEDIA_XON`, maps `FLUSH_MEDIA` to cancel, and correlates processed media
  marks with `tts.playback` progress.
- ARI externalMedia uses a real local HTTP server and UDP sockets in tests,
  rolls back partial bridge creation, source-locks RTP, and reports measured
  packet loss and jitter. Dependency versions are pinned so the fixed Rust
  1.82 gate cannot resolve Edition 2024-only transitive crates.
- The live AudioSocket server accepts the TCP close emitted by Asterisk 22 as
  a remote hangup, serves health counters, and handles downstream DTMF and
  hangup concurrently with inbound media. The generic directive mapper also
  covers playback, cancel, transfer, and ARI execution paths.
- Deepgram transcript events retain provider attribution, ElevenLabs PCM is
  packetized and paced in typed 20 ms frames, and its `flush` value is preserved
  from the control directive rather than forced by the gateway.
- AudioSocket, Media WebSocket, and ARI now share a bounded 32-clause ordered
  playback queue. Back-to-back clauses no longer terminate a session, and
  `tts.cancel` can remove a queued clause or clear the active and pending queue.
- ARI reorders ordinary RTP delivery, inserts deterministic PCM16 silence only
  after its bounded window proves loss, and transmits with a stable gateway
  SSRC distinct from Asterisk's inbound SSRC.
- The initial and follow-up code reviews found seven P1 correctness defects:
  production-provider attribution, media RTT ownership, ARI turn attribution,
  RTP reordering, playback packetization/pacing, outbound SSRC ownership, and
  multi-clause playback. All seven were fixed with negative regression tests.
- All Rust control clients now require one nonblank Bearer credential and every
  route has a direct header assertion. The Python endpoint fails closed when no
  token is configured; no reusable token value is committed.
- Both ARI entry points require RTP packets to pass the configured CIDR and
  match an IP resolved from the authenticated ARI origin before source lock.
- ARI response bodies are streamed with a cumulative 64 KiB ceiling, provider
  base URLs reject query parameters, and Media WebSocket frame/message limits
  are enforced before adapter parsing.
- Provider protocol tests now separate real-time handshake readiness from
  paused-clock timeout assertions and bound every readiness/error wait. The
  provider target passed three consecutive runs and the full Rust suite passed.
- Control directive envelope validation and schema-version ownership are shared
  across AudioSocket, Media WebSocket, and ARI.
- The `lucy-gateway-tests` image now installs the pinned Rust 1.82 Rustfmt and
  Clippy components at build time, so direct Compose static-gate commands work
  without per-run bootstrapping.
- The guide now states the exact per-transport directive support and that the
  Compose caller is an AudioSocket smoke. Card 100 tracks further consolidation
  of Asterisk configuration and test support.

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

- code-reviewer: PASS (`019f6899-ec99-7811-ad4c-4473a218ece7`); both ARI
  paths enforce resolved-origin IP plus CIDR, and all control paths use exact
  Bearer authentication.
- test-auditor: PASS (`019f689a-3e96-7493-bea5-82f2200ddc90`); 112 Rust
  tests pass, the provider target passed three consecutive runs, and all
  readiness/error waits are bounded without mocks.
- docs-reviewer: PASS via compatible fallback
  (`019f68aa-b9a5-7e60-bd4f-4cfbbcfaa0f8`); the configured
  `gpt-5.4-nano` model is unavailable for this account. Lab scope, transport
  capabilities, startup readiness, ports, provider URLs, and RTP trust are
  consistent with the implementation.
- security-reviewer: PASS (`019f68aa-963b-7543-921b-20ee695983a9`);
  authentication, secret scans, ARI response bounds, provider URL handling,
  WebSocket message caps, allowlists, and RTP source locking have no unresolved
  findings.
- simplicity-reviewer: PASS (`019f68b9-2380-7142-a248-f1625416cd94`);
  directive envelope validation and schema version now have one owner reused by
  all Asterisk producers.
- final-integrator: PASS (`019f68c6-7f1c-7d01-970f-fa3ea901afd9`); all
  specialist findings, reproducible Docker static gates, fresh-volume boot,
  control conformance, and the real Asterisk smoke are complete.

Verification evidence:

- Python: `1329 passed, 2 deselected`; Ruff check and format check pass; mypy
  reports no issues in 71 source files.
- Rust: format check, Clippy with warnings denied, and 112 tests pass in the
  sanctioned Rust 1.82 Compose service. The test image was rebuilt with
  `--no-cache`; direct `cargo fmt --check` and `cargo clippy --all-targets --
  -D warnings` commands then passed without installing components at runtime.
- Clean boot: a fresh Compose project with new Postgres/Redis volumes completed
  `docker compose up -d --build --wait`; API, worker, gateway, Postgres, Redis,
  and OTEL collector all reached the ready state before teardown.
- Control conformance: the authenticated Rust `session-oneshot` completed two
  turns, 12 marks, two playback completions, and a clean close.
- Real PBX smoke: `Lucy AudioSocket adapter completed: 43208 inbound audio
  bytes, 39680 outbound audio bytes, 15 control messages` on a clean final
  gateway build.

Findings disposition:

- Fixed: mandatory Bearer auth covers AudioSocket, Media WebSocket, ARI, the
  Rust oneshot client, and the Python dev gateway; blank configuration rejects
  all control WebSocket upgrades.
- Fixed: ARI event credentials are sent only to the exact configured origin,
  and RTP rejects an in-CIDR first packet from a non-origin address.
- Fixed: provider protocol deadlines no longer rely on one-second wall-clock
  startup races.
- Fixed: ARI chunked responses, provider URL queries, and oversized Media
  WebSocket messages are rejected before unbounded buffering or debug leakage.
- Fixed: documentation no longer implies that changing the gateway mode rewires
  the AudioSocket-only Compose caller or that non-ARI modes execute transfer.
- Fixed: duplicate control-envelope validators and hardcoded ARI schema version
  values were replaced by the shared directive contract.
- Excluded from scope: pre-existing `CLAUDE.md`, `agents.md`, and `.codex/`
  changes are unrelated and will not be staged in the Card 41 commit.

## Pending human testing

None. The optional softphone path remains documented; the deterministic real
Asterisk caller satisfies the automated AudioSocket acceptance gate.
