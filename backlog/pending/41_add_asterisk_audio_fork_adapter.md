# 41 - Add the Asterisk audio-fork adapter

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~16 h
**Depends on:** 40, 51
**State:** pending

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
- Add local protocol tests and compose smoke tests against the card 40 lab.

## Files to create/modify

- `media-gateway-rust/Cargo.toml` - adapter dependencies and features.
- `media-gateway-rust/src/` - Asterisk adapter modules and config.
- `media-gateway-rust/tests/` - protocol and conformance tests.
- `src/lucy/specs.py` - transport spec values for Asterisk modes.
- `src/lucy/settings.py` - typed Asterisk settings.
- `tests/test_specs.py` and `tests/test_settings.py` - Python registry tests.
- `docs/local-telephony-lab.md` - adapter execution notes.
- This card file.

## Chips

- [ ] **C1 - Transport config and registry.** Write failing Python tests for
  Asterisk transport spec parsing and typed settings. Then add the registry
  entries and settings. Files: `src/lucy/specs.py`, `src/lucy/settings.py`,
  `tests/test_specs.py`, `tests/test_settings.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> all pass.
- [ ] **C2 - AudioSocket adapter.** Write Rust protocol tests first for UUID,
  audio, DTMF, hangup, malformed frame, and wrong first frame. Then implement
  AudioSocket parsing and control-schema emission. Files:
  `media-gateway-rust/src/`, `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C3 - Media WebSocket adapter.** Write Rust tests first for binary
  media, JSON/text control, flow-control events, media marks, and cancel.
  Then implement the adapter. Files: `media-gateway-rust/src/`,
  `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C4 - ARI externalMedia fallback.** Write tests using a local ARI
  protocol server and RTP socket. Then implement channel create, bridge, RTP
  receive, and hangup cleanup. Files: `media-gateway-rust/src/`,
  `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C5 - Asterisk lab smoke and docs.** Run the card 40 lab through the
  implemented adapter and document the command. Files:
  `docs/local-telephony-lab.md`, this card file. Verify:
  `docker compose --profile telephony-lab run --rm telephony-caller` ->
  scripted call reaches Lucy through the Asterisk adapter.
- [ ] **C6 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
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

- [ ] `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> Asterisk transport config tests pass
- [ ] `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> Rust adapter tests pass
- [ ] `docker compose --profile telephony-lab run --rm telephony-caller` ->
  scripted Asterisk call reaches Lucy
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If any Asterisk path cannot be implemented against verified protocol behavior,
leave the card in `in_progress/`, record the exact failure, and raise a
follow-up only after documenting why the fallback path is still viable.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
