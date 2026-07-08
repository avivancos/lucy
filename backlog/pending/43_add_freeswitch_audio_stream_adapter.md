# 43 - Add the FreeSWITCH audio-stream adapter

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~12 h
**Depends on:** 51
**State:** pending

## Goal

Support FreeSWITCH installations through the open `mod_audio_stream` protocol,
giving Lucy a second native PBX family without adopting FreeSWITCH as the
managed edge.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - no mocks, typed configuration, Docker Compose runtime, and no
  hardcoded ports or URLs in code.
- `docs/adr/0003-no-mocks-testing-policy.md` - local protocol servers and
  recorded fixtures are the sanctioned test doubles.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - media stays in
  Rust; Python sees control events and directives only.
- `docs/adr/0012-telephony-native-first.md` - FreeSWITCH is the P2 native PBX
  adapter after Asterisk and CPaaS.
- `docs/telephony-connectivity.md` - verified `mod_audio_stream` protocol
  notes, license constraints, and open/commercial boundary.
- `backlog/done/51_rust_gateway_implements_control_schema.md` - Rust gateway
  schema conformance fixtures.
- `media-gateway-rust/` - adapter implementation target.
- `src/lucy/specs.py` and `src/lucy/settings.py` - SDK transport resolution and
  typed adapter settings.

## Spec

- Add a FreeSWITCH transport registry entry and typed settings for
  `mod_audio_stream` endpoint, sample rate, stream direction, metadata, and
  playback mode.
- Implement the open `mod_audio_stream` protocol only: JSON metadata text
  frames, binary L16 audio frames, JSON control messages for playback, and
  connection lifecycle events. Do not depend on commercial bidirectional
  features.
- Add recorded fixtures from a real or locally built FreeSWITCH
  `mod_audio_stream` session. Redact UUIDs and network addresses in committed
  fixtures.
- Add a local WebSocket protocol-server test that replays the fixture and
  asserts control-channel output, playback command serialization, cancellation,
  and hangup cleanup.
- Add a Docker profile or documented local command for running FreeSWITCH with
  `mod_audio_stream` when the module is available. If licensing or build steps
  block automation, record the exact manual command and park the card in
  `need_human_testing/`.

## Files to create/modify

- `media-gateway-rust/` - FreeSWITCH adapter and tests.
- `src/lucy/specs.py` - FreeSWITCH transport spec.
- `src/lucy/settings.py` - typed FreeSWITCH settings.
- `tests/fixtures/freeswitch/` - redacted recorded fixtures.
- `tests/test_specs.py` and `tests/test_settings.py` - registry and settings
  tests.
- `docs/freeswitch-audio-stream.md` - setup, limits, and smoke guide.
- This card file.

## Chips

- [ ] **C1 - Registry and settings.** Write failing tests first for
  FreeSWITCH transport resolution, sample-rate validation, and missing endpoint
  handling. Then implement the registry and typed settings. Files:
  `src/lucy/specs.py`, `src/lucy/settings.py`, `tests/test_specs.py`,
  `tests/test_settings.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> all pass.
- [ ] **C2 - Fixture and protocol contract.** Add redacted `mod_audio_stream`
  fixture frames and Python/Rust contract tests that prove frame ordering and
  redaction. Files: `tests/fixtures/freeswitch/`,
  `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C3 - Adapter implementation.** Implement metadata parse, binary L16
  ingestion, playback JSON command serialization, cancel, and hangup cleanup.
  Files: `media-gateway-rust/src/`, `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C4 - Local FreeSWITCH smoke path.** Add docs and a Docker profile or
  explicit manual command for a real FreeSWITCH session. Files:
  `docs/freeswitch-audio-stream.md`, `docker-compose.yml` if automated.
  Verify:
  `docker compose --profile freeswitch-lab run --rm freeswitch-caller` ->
  real session reaches Lucy, or card parks in `need_human_testing/`.
- [ ] **C5 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
  mypy, Rust tests, record evidence, and move this card. Verify:
  `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use recorded frames, local protocol
  servers, or a real FreeSWITCH process.
- Do not hardcode hosts, ports, provider names, sample rates, or retry budgets
  outside typed settings, registries, or named constants.
- Do not rely on commercial `mod_audio_stream` features for the open SDK path.
- Do not make FreeSWITCH the managed edge default; card 44 owns that decision.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> FreeSWITCH settings and registry tests pass
- [ ] `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> FreeSWITCH adapter tests pass
- [ ] `docker compose --profile freeswitch-lab run --rm freeswitch-caller` ->
  live local smoke succeeds or card is parked in `need_human_testing/`
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If the open module cannot be built, fixture licensing is unclear, or the smoke
requires a commercial feature, leave the card outside `done/`, document the
blocker, and raise a follow-up with the exact alternative path.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
