# 51 - Rust gateway implements the control-channel schema

**Sprint:** S6 - Provider ecosystem
**Epic:** Media gateway
**Estimated effort:** ~14 h
**Depends on:** 32, 70
**State:** done

## Goal

The Rust media gateway (`media-gateway-rust/`, today an axum health-only
skeleton) becomes the first out-of-process transport adapter for card 32's
control-channel schema (ADR 0004, ADR 0011): it connects to Lucy's session
WebSocket endpoint as a client, handshakes with `session.started`, streams
scripted STT events from a recorded WAV fixture timeline, plays `tts.speak`
with real `mark_chars` pacing, and reports measured transport metrics.
Schema conformance is locked by golden JSON fixtures generated from the
Pydantic source of truth, so Python and Rust can never drift silently.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  URLs, provider names, or budgets; Docker Compose is the test runtime).
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - the
  Python/Rust boundary this card implements: Rust owns the media plane and
  the `transport_ms` slice; audio frames NEVER cross the control channel.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the control-channel
  schema is a public contract; the Rust gateway is developed against it.
- `docs/adr/0001-python-first-rust-hot-paths.md` - sidecar policy: the
  boundary must remain explicit and observable.
- `docs/adr/0003-no-mocks-testing-policy.md` - recorded fixtures and local
  protocol servers are the only sanctioned test doubles.
- `docs/adr/0010-open-core-split.md` - the Rust media gateway is open SDK
  code; nothing platform-shaped belongs in it.
- `src/lucy/transport/schema.py` (built in card 32, extended by card 70) - THE
  source of truth: `Envelope`, all landed payload models including recording,
  `parse_event`,
  `UnknownControlMessage`. Read the landed code for the exact wire shape
  and `type` strings; this card mirrors, never redefines.
- `src/lucy/transport/dev_gateway.py` (built in card 32) -
  `LocalGatewaySimulator`; the in-process gateway whose JSON the Rust
  gateway must be byte-compatible with.
- `src/lucy/session.py` (built in card 32) - `VoiceSession`, `TurnRecord`,
  and the transport surface the WS bridge must satisfy.
- `src/lucy/serve/app.py` (built in card 23) - `create_app()` factory
  where the session WS route registers.
- `src/lucy/evals.py` - `booking_happy_path()`; its caller lines are the
  fixture script.
- `src/lucy/metrics.py` - `LatencyWaterfall.transport_ms`; the slice the
  gateway's metrics fill (ADR 0004).
- `media-gateway-rust/src/main.rs`, `media-gateway-rust/Cargo.toml`,
  `media-gateway-rust/Dockerfile` - the current health-only skeleton you
  extend.
- `docker-compose.yml` - the `lucy-media-gateway` service you extend and
  the profiles you add.
- `tests/test_infrastructure.py` - asserts compose service names/ports and
  greps `main.rs` for `lucy-media-gateway`, `"/health"`, `8081`; it must
  stay green.
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

If `src/lucy/transport/schema.py` or `src/lucy/serve/app.py` do not exist,
a dependency card has not landed: stop per Failure protocol.

## Spec

### Canonical JSON (the conformance contract)

One canonical serialization, identical in both languages:

- UTF-8, keys sorted lexicographically, compact separators (no spaces),
  non-ASCII characters NOT escaped.
- Python: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)`.
- Rust: deserialize into the typed struct, then
  `serde_json::to_string(&serde_json::to_value(&msg)?)`. serde_json's
  default `Map` is a BTreeMap, which sorts keys; therefore the
  `preserve_order` feature must NOT be enabled on `serde_json`.
- Golden files: `tests/fixtures/control_schema/<type>.json`, one per wire
  message type, exactly one canonical JSON line plus a trailing `\n`.
  Comparisons strip exactly that one trailing newline.

### Python golden generator - `src/lucy/transport/golden.py` (new)

- `canonical_dumps(obj) -> str` - the single canonicalization helper
  (accepts a dict or a Pydantic model; models go through
  `model_dump(mode="json")` first). Reused by the WS bridge, the dev
  gateway runner, and every conformance test - canonical rules exist in
  exactly one place.
- Named constants for deterministic example values:
  `GOLDEN_SESSION_ID = "sess-golden"`, `GOLDEN_TURN_ID = "turn-1"`,
  `GOLDEN_TS_MS = 1_700_000_000_000`. Regeneration must be byte-stable.
- `golden_messages() -> dict[str, dict]` - maps each wire `type` string to
  ONE complete wire message exactly as `parse_event` accepts it
  (envelope + payload, in whatever composition the schema landed). Covers all
  27 payload models currently registered in `MESSAGE_TYPES`, including the
  recording messages from card 70. The base upstream family includes `SessionStarted`,
  `VadSpeechStart`, `VadSpeechEnd`, `SttPartial`, `SttFinal`, `Dtmf`,
  `TtsPlayback`, `BargeIn`, `TransportMetrics`, `SessionEnded`; downstream
  `SessionConfigure`, `TtsSpeak`, `TtsCancel`, `DtmfSend`, `Transfer`,
  `SessionEnd`. The `type` strings come from the LANDED `schema.py`; do
  not invent them. Every optional field is populated non-null (e.g.
  `turn_id`) so the Rust structs prove full field coverage. Every message
  must round-trip through `parse_event`.
- `write_golden(directory: Path) -> list[Path]` - writes one file per
  type, named `<type>.json`, canonical line + newline.
- `python -m lucy.transport.golden <directory>` CLI (`__main__` guard,
  directory from `sys.argv` - no output path hardcoded in src).

### Audio fixture (recorded, committed once)

- `tests/fixtures/audio/booking_caller_8k.wav` - real recorded audio of
  the two caller lines of `booking_happy_path()` ("I want to book a
  demo." then "Tuesday morning."), 8 kHz, mono, 16-bit PCM (the G.711
  rate, ADR 0004). Record with a microphone, or bootstrap once from the
  OS speech synthesizer and convert, e.g. on macOS:
  `say -o /tmp/booking_caller.aiff "I want to book a demo. ...
  Tuesday morning."` then `afconvert -f WAVE -d LEI16@8000 -c 1
  /tmp/booking_caller.aiff tests/fixtures/audio/booking_caller_8k.wav`.
  The committed bytes are the fixture; tests consume them and never
  synthesize audio at test time.
- `tests/fixtures/audio/booking_caller.timeline.json` - hand-authored
  against the WAV: `{"wav": "booking_caller_8k.wav", "utterances":
  [{"text": str, "start_ms": int, "end_ms": int, "words": [{"text": str,
  "at_ms": int}]}]}`. Utterance texts equal the scenario caller lines
  exactly; all offsets strictly increasing and within the WAV duration
  (stdlib `wave` computes it: frames / framerate).

### Rust gateway - `media-gateway-rust/`

Dependencies (add with `cargo add`; current minors, no pinned guesses):
`tokio-tungstenite`, `serde_json` (default features only - NO
`preserve_order`), `futures-util`, `hound`. Restructure as lib + bin:
`src/lib.rs` with `pub mod control;`, `src/main.rs` keeps the existing
axum `/health` on `8081` and the `lucy-media-gateway` service string
untouched (`tests/test_infrastructure.py` greps for all three).

`src/control/schema.rs`:

- serde structs mirroring the exact wire shape `parse_event` accepts, all
  with `#[serde(deny_unknown_fields)]`, plus a `ControlMessage` enum
  dispatched on the wire `type` string covering all 27 types. The golden
  fixtures are normative: derive struct layout from them and from the
  landed `schema.py`, never from this card's prose. Unknown `type` or
  extra fields must fail deserialization.
- `canonical_json(msg: &ControlMessage) -> String` via
  `serde_json::to_value` + `to_string` (sorted keys for free).

`src/control/fixture.rs`:

- `AudioFixture::load(wav: &Path, timeline: &Path)` - reads the WAV with
  `hound`; rejects anything that is not 8 kHz / mono / 16-bit PCM;
  rejects timeline offsets outside the WAV duration.
- Streaming: audio frames are consumed internally at a named constant
  `FRAME_MS: u64 = 20` cadence and are NEVER sent on the control channel
  (ADR 0004). Per utterance: `vad.speech_start` at `start_ms`; one
  `stt.partial` per timeline word with accumulated text and strictly
  rising `stability` capped below 1.0; `vad.speech_end` at `end_ms` with
  `speech_ms = end_ms - start_ms`; then `stt.final` with the full
  utterance text, `stt_ms` = measured ms from last consumed frame to
  emission (near zero here; real once S5 providers land), and provider
  label from the named constant `FIXTURE_PROVIDER_LABEL: &str =
  "fixture"`.
- Turn gating: utterance N+1 starts only after every `tts.playback`
  `finished` for directives received in response to utterance N's
  `stt.final`, or after `LUCY_GATEWAY_REPLY_TIMEOUT_MS` (env, default
  named constant `DEFAULT_REPLY_TIMEOUT_MS: u64 = 5000`). No barge-in is
  emitted by the gateway in this card (real VAD barge-in is S5 scope).

`src/control/session.rs`:

- `GatewayConfig::from_env()` - `LUCY_SESSION_WS_URL` (required in
  session mode; NO default URL anywhere in src), `LUCY_AUDIO_FIXTURE_WAV`,
  `LUCY_AUDIO_FIXTURE_TIMELINE`, `LUCY_GATEWAY_PACING_MS` (default
  `DEFAULT_PACING_MS: u64 = 30`), `LUCY_GATEWAY_MODE`
  (`serve` | `session-oneshot`, default `serve`),
  `LUCY_GATEWAY_CALLER_ID` (default `DEFAULT_CALLER_ID: &str =
  "fixture-caller"`).
- `SessionClient::run()` - connects with `tokio-tungstenite`, retrying
  every `DEFAULT_CONNECT_RETRY_MS: u64 = 500` up to
  `DEFAULT_CONNECT_TIMEOUT_MS: u64 = 30_000` (compose startup race). The
  FIRST outbound message is `session.started` (codecs derived from the
  WAV header, e.g. `pcm16/8000`). `seq` starts at 0 and increments per
  outbound envelope; `ts_ms` from the system clock. Inbound handling:
  `session.configure` is stored and logged, never blocks streaming;
  `tts.speak` starts a playback task; `tts.cancel` stops the matching
  playback (or all) and emits `tts.playback` state `flushed` with the
  `mark_chars` heard so far; `session.end` closes cleanly.
- Playback pacing for `tts.speak(utterance_id, text)`: emit `tts.playback`
  `started` with `mark_chars = 0`; split `text` on whitespace; every
  `pacing_ms` advance one word and emit a `mark` whose `mark_chars` is the
  count of Unicode scalar values of the consumed prefix (matches Python
  `len()`); end with `finished` and `mark_chars = len(text)`.
- Transport metrics: after each `stt.final`, send a WS ping; on pong,
  emit `transport.metrics` with `rtt_ms` = measured round-trip,
  `jitter_ms` = mean absolute deviation of this utterance's mark
  intervals from `pacing_ms`, `packet_loss = 0.0` (no RTP path in this
  card). This is the gateway's measurement of the control-transport
  slice; the Python bridge maps it into `LatencyWaterfall.transport_ms`.
- Oneshot mode (`LUCY_GATEWAY_MODE=session-oneshot`): run the scripted
  session, emit `session.ended` (reason `fixture_complete`) after the
  final playback, print ONE `SessionReport` canonical JSON line as the
  FINAL stdout line (all logs to stderr), exit 0 on clean close else 1.
  `SessionReport` fields: `session_id`, `turns` (count of `stt.final`
  sent), `tts_speak_received`, `playback_finished`, `marks_emitted`,
  `rtt_ms_last`, `jitter_ms_last`, `clean_close: bool`.
- `serve` mode: today's health-only behavior, unchanged.

Rust tests: `tests/schema_conformance.rs` reads the golden directory from
`LUCY_CONTROL_SCHEMA_DIR`, defaulting to
`concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/fixtures/control_schema")`
(a named constant in the test). Timing tests use
`#[tokio::test(start_paused = true)]` - no wall-clock sleeping. The
session-client test runs against an in-test local `tokio-tungstenite`
server scripting the Python side (a local protocol server, ADR 0003).

### Python session WS endpoint - `src/lucy/serve/control_ws.py` (new)

- `CONTROL_WS_PATH = "/v1/session/ws"` named constant, exported.
- `register_control_ws(app: FastAPI, responder = echo_responder) -> None`
  adds the WebSocket route; `create_app()` calls it by default so the
  compose `lucy-api` service serves it with no extra wiring.
- `async def echo_responder(text: str) -> str` returns
  `f"You said: {text}"` - a real deterministic responder matching card
  32's `Callable[[str], Awaitable[str]]` seam; wire-level proof needs no
  LLM (card 33 owns that).
- `WebSocketGatewayTransport` adapts an accepted WebSocket to the exact
  transport surface `VoiceSession` consumes (read the landed `session.py`
  and `dev_gateway.py` for that surface; implement the same one). Inbound
  text frames go through `parse_event`; outbound directives are
  serialized with `canonical_dumps`.
- Handshake: the first inbound message MUST be `session.started`,
  otherwise close with WS code 1002 and never construct a session.
- transport_ms: the bridge tracks `transport.metrics` samples; each
  finalized `TurnRecord.waterfall.transport_ms` equals the most recent
  `rtt_ms` sample at that turn's finalization, `0.0` when none yet
  (ADR 0004: the media plane owns this slice).
- Tested with starlette's `TestClient.websocket_connect` - a real
  in-process ASGI protocol client, sanctioned like card 33's
  `httpx.ASGITransport`.

### Python dev gateway over WS - `src/lucy/transport/dev_gateway_ws.py` (new)

- `async def run_dev_gateway(url: str, scenario: SyntheticCallScenario)
  -> dict` - drives `LocalGatewaySimulator` over a REAL WebSocket client
  connection (`websockets` library, added to the dev extra): simulator
  messages out as `canonical_dumps` frames, inbound frames through
  `parse_event` and fed back to the simulator. Returns a report dict with
  the SAME field names as the Rust `SessionReport`.
- `python -m lucy.transport.dev_gateway_ws` reads `LUCY_SESSION_WS_URL`
  from env, runs `booking_happy_path()`, prints the report as the final
  stdout line, exits 0 on clean close.
- `pyproject.toml`: add `websockets>=12.0` to
  `[project.optional-dependencies].dev` (`Dockerfile.api` already
  installs `.[dev]`).

### docker-compose.yml (extend; existing services keep names and ports)

- `lucy-media-gateway`: add `environment` (`LUCY_SESSION_WS_URL:
  ws://lucy-api:8000/v1/session/ws`, `LUCY_GATEWAY_MODE: serve`) and
  `depends_on: [lucy-api]`. No `profiles` key: the Rust gateway is the
  default-profile gateway.
- `lucy-gateway-session` (new, `profiles: ["gateway-it"]`): same build as
  the gateway, `LUCY_GATEWAY_MODE: session-oneshot`, fixture env vars,
  volume `./tests/fixtures/audio:/fixtures/audio:ro`, `depends_on:
  [lucy-api]`.
- `lucy-gateway-tests` (new, `profiles: ["gateway-it"]`): image
  `rust:1.82-slim` (matches the Dockerfile builder), volumes
  `./media-gateway-rust:/app` and `./tests/fixtures:/fixtures:ro`,
  `working_dir: /app`, env `LUCY_CONTROL_SCHEMA_DIR:
  /fixtures/control_schema`, `LUCY_AUDIO_FIXTURE_WAV:
  /fixtures/audio/booking_caller_8k.wav`, `LUCY_AUDIO_FIXTURE_TIMELINE:
  /fixtures/audio/booking_caller.timeline.json`, command `cargo test`.
- `lucy-dev-gateway` (new, `profiles: ["test"]`): build `Dockerfile.api`,
  command `python -m lucy.transport.dev_gateway_ws`,
  `LUCY_SESSION_WS_URL: ws://lucy-api:8000/v1/session/ws`, `depends_on:
  [lucy-api]`. This is the demotion: the Python dev gateway never runs in
  the default profile.

### Integration test - `tests/test_gateway_integration.py` (opt-in)

- `[tool.pytest.ini_options]` gains marker `gateway_it: needs the Docker
  daemon; runs gateways against lucy-api via compose` and an `addopts`
  that deselects it (`-m "not gateway_it"`; merge into
  `-m "not live and not gateway_it"` if card 29 already landed). CLI
  `-m gateway_it` re-enables.
- Tests drive `docker compose --profile gateway-it run --rm
  lucy-gateway-session` (and the `test` profile dev gateway) via
  `subprocess`, parse the final stdout line as JSON, and tear down with
  `docker compose --profile gateway-it down` in a fixture finalizer.
- Assertions: `clean_close` is true; `turns` equals the number of caller
  lines in `booking_happy_path()`; `playback_finished ==
  tts_speak_received >= turns`; `rtt_ms_last > 0`; and the Rust and dev
  gateway reports agree on `turns` and `playback_finished`.

## Chips

- [x] **C1 - Golden generator on the Python side.** Write
  `tests/test_control_schema_golden.py` first:
  `test_every_schema_payload_model_has_a_golden_message`,
  `test_golden_messages_round_trip_through_parse_event`,
  `test_committed_golden_files_match_regenerated_canonical_bytes`
  (regenerate into `tmp_path`, compare byte-for-byte with
  `tests/fixtures/control_schema/`). Implement
  `src/lucy/transport/golden.py` and generate the fixtures with
  `python -m lucy.transport.golden tests/fixtures/control_schema`.
  Verify: `.venv/bin/python -m pytest
  tests/test_control_schema_golden.py -q` -> >=3 pass, 27 golden files
  committed.
- [x] **C2 - Simulator byte-compatibility.** Test first, same file:
  `test_dev_gateway_emissions_canonicalize_and_reparse_byte_identical` -
  run `LocalGatewaySimulator` over `booking_happy_path()`, canonicalize
  every emitted message with `canonical_dumps`, reparse via `parse_event`,
  re-dump, assert byte equality, and assert each type's field-name set
  matches its golden file. Fix `golden.py` (never `schema.py`) if a
  mismatch appears. Verify: `.venv/bin/python -m pytest
  tests/test_control_schema_golden.py -q` -> all pass.
- [x] **C3 - Recorded WAV fixture + timeline.** Write
  `tests/test_audio_fixture.py` first:
  `test_wav_fixture_is_8k_mono_pcm16` (stdlib `wave`),
  `test_timeline_matches_scenario_caller_lines_and_wav_duration`. Record
  and convert `tests/fixtures/audio/booking_caller_8k.wav`, author
  `tests/fixtures/audio/booking_caller.timeline.json`, commit both.
  Verify: `.venv/bin/python -m pytest tests/test_audio_fixture.py -q`
  -> all pass.
- [x] **C4 - Rust schema structs + conformance harness.** Write
  `media-gateway-rust/tests/schema_conformance.rs` first:
  `golden_files_reserialize_byte_identical`,
  `every_golden_type_maps_to_a_variant_and_counts_match`,
  `unknown_type_is_rejected`, `extra_field_is_rejected`. Then add deps to
  `media-gateway-rust/Cargo.toml`, create `media-gateway-rust/src/lib.rs`
  and `media-gateway-rust/src/control/{mod.rs,schema.rs}`, and add the
  `lucy-gateway-tests` compose service. Verify: `docker compose --profile
  gateway-it run --rm lucy-gateway-tests` -> conformance tests pass.
- [x] **C5 - Rust fixture streamer.** Tests first in
  `media-gateway-rust/src/control/fixture.rs` (`#[cfg(test)]`, tokio
  `start_paused`): `timeline_emits_rising_stability_partials_then_final`,
  `loader_rejects_non_8k_mono_wav`,
  `frames_pace_internally_and_never_become_control_messages`. Implement
  `AudioFixture` load + stream. Verify: `docker compose --profile
  gateway-it run --rm lucy-gateway-tests` -> all pass, no test sleeps
  wall-clock (paused clock).
- [x] **C6 - Rust session client handshake + streaming.** Test first in
  `media-gateway-rust/src/control/session.rs` against an in-test
  tokio-tungstenite server:
  `session_client_sends_session_started_first_then_streams_fixture`
  (server asserts first message type, replies `session.configure`, then a
  `tts.speak` per `stt.final`). Implement `GatewayConfig::from_env`,
  connect-with-retry, handshake, turn-gated streaming. Verify: `docker
  compose --profile gateway-it run --rm lucy-gateway-tests` -> all pass.
- [x] **C7 - Playback pacing, metrics, oneshot report.** Tests first in
  `session.rs`:
  `tts_speak_yields_started_marks_finished_with_advancing_mark_chars`,
  `tts_cancel_mid_playback_emits_flushed_with_partial_mark_chars`,
  `transport_metrics_reports_measured_rtt_after_stt_final`. Implement
  pacing, ping/pong metrics, `SessionReport`, and the
  `LUCY_GATEWAY_MODE` dispatch in `media-gateway-rust/src/main.rs`
  (keep `lucy-media-gateway`, `"/health"`, `8081` strings intact).
  Verify: `docker compose --profile gateway-it run --rm
  lucy-gateway-tests` -> all pass; `.venv/bin/python -m pytest
  tests/test_infrastructure.py -q` -> still green.
- [x] **C8 - Python session WS endpoint.** Write
  `tests/test_control_ws_bridge.py` first:
  `test_first_message_must_be_session_started` (close code 1002),
  `test_ws_session_runs_turns_and_returns_tts_speak` (drive golden-shaped
  messages through `TestClient.websocket_connect`, assert a `tts.speak`
  per `stt.final` and canonical outbound frames),
  `test_transport_metrics_rtt_fills_waterfall_transport_ms`. Implement
  `src/lucy/serve/control_ws.py` and register it in
  `src/lucy/serve/app.py`. Verify: `.venv/bin/python -m pytest
  tests/test_control_ws_bridge.py -q` -> all pass.
- [x] **C9 - Dev gateway WS runner + compose demotion.** Write
  `tests/test_dev_gateway_ws.py` first:
  `test_dev_gateway_ws_runner_completes_scenario_against_local_server`
  (uvicorn serving `create_app()` on an ephemeral port inside the test),
  plus `test_docker_compose_gateway_profiles` in
  `tests/test_infrastructure.py` (`lucy-dev-gateway` -> `["test"]`,
  `lucy-gateway-session`/`lucy-gateway-tests` -> `["gateway-it"]`,
  `lucy-media-gateway` -> no profiles key). Implement
  `src/lucy/transport/dev_gateway_ws.py`, add `websockets>=12.0` to the
  dev extra, add/extend the compose services. Verify:
  `.venv/bin/python -m pytest tests/test_dev_gateway_ws.py
  tests/test_infrastructure.py -q` -> all pass.
- [x] **C10 - Compose integration profile.** Write
  `tests/test_gateway_integration.py` (marker `gateway_it`, registered in
  `pyproject.toml` with deselecting `addopts`):
  `test_rust_gateway_oneshot_session_reports_clean_close`,
  `test_dev_and_rust_gateways_report_identical_turn_counts`. If the
  Docker daemon is unavailable, stop per Failure protocol and route the
  card to `need_human_testing/` with the exact commands below. Verify:
  `.venv/bin/python -m pytest -m gateway_it
  tests/test_gateway_integration.py -q` -> 2 pass; `.venv/bin/python -m
  pytest -q` -> integration tests deselected by default.
- [x] **C11 - Full suite + bookkeeping.** Run the whole Python suite and
  the full Rust suite, fill "Improvements noted", move this card to
  `done/`. Verify: `.venv/bin/python -m pytest -q` -> full suite green;
  `docker compose --profile gateway-it run --rm lucy-gateway-tests` ->
  all Rust tests pass.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003): no `unittest.mock`,
  no fake WebSocket layers. The sanctioned doubles are the golden
  fixtures, the committed WAV recording, the in-test tokio-tungstenite
  server, starlette's in-process WS client, and `LocalGatewaySimulator` -
  real implementations of real contracts.
- Do not hardcode URLs, ports, pacing, timeouts, provider or caller
  labels in source: `LUCY_SESSION_WS_URL` is always injected (compose env
  is config, src is not); everything else is an env-overridable named
  constant listed in the Spec. `grep ws:// media-gateway-rust/src` must
  stay empty.
- Do not modify `src/lucy/transport/schema.py` or any card 32 contract
  (`Envelope` fields, model names, `TurnState`, `TurnRecord` meanings).
  Mirror only. If Rust needs a field the schema lacks, stop per Failure
  protocol - schema changes are a separate card.
- Do not put audio bytes, base64 audio, or per-frame messages on the
  control channel (ADR 0004). The WAV is consumed inside the gateway.
- Do not regenerate golden files from Rust or hand-edit them; the Python
  generator is the only writer, and C1's drift test is the guard.
- Do not enable serde_json's `preserve_order` feature; it breaks
  canonical key ordering.
- Do not implement SIP/RTP, real VAD, barge-in emission, or provider
  STT/TTS streaming (S5 cards and card 29). No platform/ingest/API-key
  code in the gateway or bridge (ADR 0010): telemetry leaves only via
  `lucy.observe`.
- Do not break `tests/test_infrastructure.py`: keep service names, ports
  `8081:8081`, and the `main.rs` health markers.
- Do not touch files outside: `media-gateway-rust/{Cargo.toml,Cargo.lock,
  src/main.rs,src/lib.rs,src/control/*,tests/schema_conformance.rs}`,
  `src/lucy/transport/{golden.py,dev_gateway_ws.py}`,
  `src/lucy/serve/{control_ws.py,app.py}`, `docker-compose.yml`,
  `pyproject.toml`, `tests/fixtures/{control_schema,audio}/*`,
  `tests/{test_control_schema_golden.py,test_audio_fixture.py,
  test_control_ws_bridge.py,test_dev_gateway_ws.py,
  test_gateway_integration.py,test_infrastructure.py}`.
- Do not check a box without running its Verify command.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_control_schema_golden.py
      tests/test_audio_fixture.py tests/test_control_ws_bridge.py
      tests/test_dev_gateway_ws.py tests/test_infrastructure.py -q` ->
      all pass
- [x] `docker compose --profile gateway-it run --rm lucy-gateway-tests`
      -> `cargo test` green, including
      `golden_files_reserialize_byte_identical`
- [x] `docker compose --profile gateway-it run --rm lucy-gateway-session`
      -> exit code 0, final stdout line is a JSON report with
      `"clean_close":true` and `turns` == 2
- [x] `.venv/bin/python -m pytest -m gateway_it
      tests/test_gateway_integration.py -q` -> 2 pass (Docker daemon up)
- [x] `grep -rn "ws://" media-gateway-rust/src src/lucy/serve
      src/lucy/transport` -> no matches (URLs injected, never in src)
- [x] `grep -rn "preserve_order" media-gateway-rust/Cargo.toml` -> no
      matches
- [x] `.venv/bin/python -m pytest -q` -> full suite green with
      `gateway_it` deselected by default (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is
      available)
- [x] Post-task audit done; no new follow-up card is required

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion. If only the Docker
daemon blocks C10/C11, move to `need_human_testing/` with the exact
pending commands listed.

## Improvements noted

- Python now generates 27 canonical fixtures directly from the registered
  Pydantic payload models; committed bytes and gateway simulator emissions
  round-trip without drift (4 tests).
- The committed caller fixture is real OS-synthesized speech converted to 8 kHz,
  mono, 16-bit PCM and is paired with a validated two-utterance timeline.
- Rust conformance currently passes 4 schema tests and 3 fixture-streamer tests
  in `rust:1.82-bookworm`, including unknown type/field rejection and proof that
  paced audio frames never become control messages.
- The Compose conformance profile and release builder now use the tested
  `rust:1.82-bookworm` image; `docker compose --profile gateway-it run --rm
  lucy-gateway-tests` passes all 7 schema and fixture tests.
- The Rust client now proves a real local WebSocket handshake and two-turn
  session, emits paced playback marks, handles cancellation while playback is
  active, reports measured RTT/jitter, and prints a canonical oneshot report.
- The framework app now exposes a strict session WebSocket bridge; its protocol
  tests prove canonical directives, handshake rejection, and measured gateway
  RTT mapped into the finalized turn waterfall.
- The Python simulator now traverses the same real WebSocket endpoint under the
  opt-in `test` profile; the default Compose gateway remains the Rust service.
- The opt-in Compose integration suite passes both gateway sessions from clean
  images with an ephemeral host API port, avoiding collisions while preserving
  the in-network `lucy-api:8000` contract.
- Final review fixed real-time 20 ms fixture pacing, Unicode scalar playback
  marks, and Rust rejection of missing or wrongly typed payload fields.
- Final evidence: 469 Python tests passed with 2 gateway integration tests
  deselected by default; the 2 gateway integrations and all 14 Rust tests pass;
  Docker ruff, format, and mypy gates are clean.

## Review evidence

- code-reviewer: PASS - control flow, session gating, cancellation, metrics, and
  strict wire validation reviewed; pacing and Unicode findings were fixed.
- test-auditor: PASS - 27 golden fixtures, malformed-message negatives, recorded
  WAV, local WS servers, both Compose gateways, and full-suite regression covered.
- docs-reviewer: PASS - card evidence and Compose profile responsibilities match
  the open-core media-plane and platform boundaries.
- simplicity-reviewer: PASS - one shared wire generator, one WS transport, and one
  Rust session client reuse the existing VoiceSession and simulator contracts.
- security-reviewer: PASS - no secrets, source URLs, audio frames, or
  credential-bearing payloads cross the control channel.

Findings disposition:

- None open. Review findings for pacing, Unicode marks, and malformed Rust
  payload acceptance were fixed and covered by tests.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
