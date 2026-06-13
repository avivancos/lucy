# 29 - Ship first provider plugin trio

**Sprint:** S6 - Provider ecosystem
**Epic:** Providers
**Estimated effort:** ~12 h
**Depends on:** 28
**State:** pending

## Goal

Prove the plugin ABI (card 28) with real providers covering the cascaded
path: one STT (Deepgram), one TTS (ElevenLabs), one LLM/realtime (OpenAI),
each independently releasable. All tests run no-mocks on fixtures recorded
from real provider sessions, with an opt-in `-m live` profile against the
real sandboxes.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  provider names/URLs/keys outside typed settings or named constants).
- `docs/adr/0003-no-mocks-testing-policy.md` - recorded fixtures must come
  from real interactions; paid/network providers only behind explicit
  integration profiles.
- `docs/adr/0010-open-core-split.md` - provider plugins are open code; the
  voice provider Protocols and spec models are the frozen public ABI.
- `backlog/pending/28_add_plugin_mechanism_and_workspace.md` (in
  `backlog/done/` once executed) - defines `LucyPlugin`, `load_plugins()`,
  entry-point group `lucy.plugins`, spec-string resolution, and the uv
  workspace under `packages/`. Then read the landed `src/lucy/plugins.py`;
  its factory signatures are the contract this card implements against. If
  the landed code differs from this card's assumed shapes, the landed code
  wins.
- `src/lucy/providers.py` - `Capability`, `ModelInfo`, `ModelRegistry`,
  `default_model_registry()`; plugin catalog rows must mirror these exactly.
- `src/lucy/voice.py` - frozen `SttProvider`/`TtsProvider` Protocols,
  `AudioChunk`, `TranscriptEvent`, `TtsStreamEvent`, `ProviderPayloadError`,
  and `LocalSttSimulator`/`LocalTtsSimulator`. After card 22 these simulators
  live in `lucy.testing`; import them from `lucy.testing` in new code.
- `src/lucy/specs.py` - `VoiceSpec.stt_provider`/`tts_provider` carry the
  spec strings that resolve to these plugins.
- `backlog/pending/33_runtime_m1_streaming_llm.md` (done/ once executed) -
  defines `LlmProvider`, `LlmMessage`, `LlmRequest`, the stream event union
  (`TokenDelta`, `ToolCallDelta`, `ToolCallReady`, `UsageReport`,
  `StreamEnd`), and `LocalLlmSimulator`. Read the landed `src/lucy/llm.py`.
- `backlog/pending/38_runtime_m6_speech_to_speech_driver.md` - fixes the
  `RealtimeSession` method names (`events()`, `send_tool_result`,
  `interrupt`) the OpenAI realtime adapter must match so it binds to
  `RealtimeProvider` unchanged when card 38 lands.
- `tests/test_voice_pipeline.py` - house test style: plain pytest,
  asyncio_mode auto, simulators, no mocks.
- `pyproject.toml` - workspace config and pytest ini (`testpaths=["tests"]`:
  package tests under `packages/*/tests` must be targeted explicitly).

## Spec

### Contract suites - `src/lucy/testing/contracts.py`

Pytest mixin classes (no base-class registration tricks; plain classes whose
`test_*` methods are inherited by a binding class). Async tests rely on
asyncio_mode auto. Hooks raise `NotImplementedError` unless overridden.
"No orphan tasks" means `asyncio.all_tasks()` after the call equals the set
captured before it.

`SttContractSuite` - hooks: `make_provider() -> SttProvider`,
`chunks() -> list[AudioChunk]` (the input the provider was recorded/built
for), `make_stalled_provider() -> SttProvider` (never completes),
`make_malformed_case() -> tuple[SttProvider, list[AudioChunk]]` (a call that
must raise `ProviderPayloadError`). Test methods:

- `test_streaming_partials_accumulate_to_final` - >=1 event; exactly one
  event has `is_final=True` and it is the last; final `text` non-empty;
  `sequence` values non-decreasing.
- `test_stalled_transcription_times_out_cleanly` -
  `asyncio.wait_for(provider.transcribe(...), 0.05)` raises `TimeoutError`;
  no orphan tasks remain.
- `test_cancellation_leaves_no_orphan_tasks` - cancel a running
  `transcribe` task; `CancelledError` propagates; no orphan tasks.
- `test_malformed_payload_raises_provider_payload_error`.

`TtsContractSuite` - hooks: `make_provider() -> TtsProvider`,
`text() -> str`, `make_stalled_provider() -> TtsProvider`,
`make_malformed_case() -> tuple[TtsProvider, str]`. Test methods:

- `test_stream_orders_started_chunks_finished` - first event status
  `started`, last `finished`, >=1 `chunk` in between, all same `session_id`.
- `test_stalled_synthesis_times_out_cleanly` - as above for `synthesize`.
- `test_cancellation_leaves_no_orphan_tasks`.
- `test_malformed_payload_raises_provider_payload_error` (blank text is the
  canonical malformed TTS input; adapters must raise before any wire frame).

`LlmContractSuite` - hooks: `make_provider() -> LlmProvider`,
`request() -> LlmRequest`, `make_stalled_provider() -> LlmProvider`,
`make_tool_call_case() -> tuple[LlmProvider, LlmRequest]`,
`make_malformed_case() -> tuple[LlmProvider, LlmRequest]`. Test methods:

- `test_token_deltas_end_with_single_stream_end` - >=1 `TokenDelta`;
  exactly one `StreamEnd`, emitted last.
- `test_usage_report_emitted_before_stream_end` - exactly one
  `UsageReport`, ordered before `StreamEnd`.
- `test_tool_call_deltas_assemble_to_tool_call_ready` - the tool-call case
  yields >=1 `ToolCallDelta` then a `ToolCallReady` with complete arguments.
- `test_cancellation_mid_stream_closes_generator` - cancelling mid-stream
  closes the async generator; no orphan tasks.
- `test_malformed_wire_frame_raises_provider_payload_error` (reuse
  `ProviderPayloadError`; if the landed `src/lucy/llm.py` defines its own
  malformed-payload error, assert that one instead).

### Fixture replay - `src/lucy/testing/replay.py`

Fixture format: JSONL, one frame per line:
`{"direction": "sent" | "received", "at_ms": int, "payload": {...}}`.
Binary payloads are encoded as `{"b64": "<base64>", "codec": "<name>"}`
objects inside `payload`. API:

- `RecordedFrame` frozen dataclass: `direction: str`, `at_ms: int`,
  `payload: dict`.
- `load_fixture(path: Path) -> list[RecordedFrame]`.
- `class FixtureMismatch(AssertionError)` - message names the fixture path,
  the frame index, and both payloads.
- `ReplayTransport(frames: list[RecordedFrame])` with
  `async send(payload: dict) -> None` (must match the next `sent` frame
  after volatile masking, else raise `FixtureMismatch`) and
  `async receive() -> dict` (yields the next `received` payload; raises
  `FixtureMismatch` if the recording expected a `sent` frame first).
- `StalledTransport` - `send` accepts anything, `receive` never returns
  (for the stalled-provider contract tests).
- `mask_volatile(payload: dict, fields: tuple[str, ...]) -> dict` - replaces
  the named keys (recursively) with stable `"<masked>"` placeholders.

### Fixture recorder - `src/lucy/testing/record.py`

CLI: `python -m lucy.testing.record --plugin <name> --capability
stt|tts|llm|realtime --scenario <name> --fixture-dir <dir>`. Resolves the
scenario coroutine from `lucy_<plugin>.recording.SCENARIOS` (a
`dict[str, Callable]`), opens a REAL provider session using the plugin's
typed settings (env keys required), runs the scenario, and writes
`<capability>_<scenario>.jsonl`. Hard rules, each with a unit test:

- Never write `Authorization` headers, `api_key`/`xi-api-key` headers, or
  URL query credentials into any frame.
- Replace any payload string equal to a configured secret with
  `"<scrubbed>"`.
- Mask volatile fields before writing: `request_id`, `event_id`, `id`,
  `created`, `session.id` (extendable via a `VOLATILE_FIELDS` named
  constant).

### Plugin packages (three, same layout)

Final dist names are decided at the naming milestone; use these for now.
Each package is an independent uv workspace member, version `0.1.0`,
depending on `lucy` plus `pydantic-settings` and its wire client
(`websockets` and/or `httpx`) - never on official provider SDKs and never
the other way around (core gains no provider deps). Layout:

```
packages/lucy-<provider>/
  pyproject.toml                    # dist lucy-<provider>; entry point:
                                    # [project.entry-points."lucy.plugins"]
                                    # <provider> = "lucy_<provider>:PLUGIN"
  src/lucy_<provider>/__init__.py   # PLUGIN: LucyPlugin (card 28 dataclass)
  src/lucy_<provider>/catalog.py    # MODELS: list[ModelInfo]
  src/lucy_<provider>/settings.py   # <Provider>Settings(BaseSettings)
  src/lucy_<provider>/recording.py  # SCENARIOS for lucy.testing.record
  tests/fixtures/*.jsonl            # recorded from real sessions
  tests/test_*.py
```

`packages/lucy-deepgram/` - STT plugin:

- `DeepgramSettings(BaseSettings)`, env prefix `DEEPGRAM_`:
  `api_key: SecretStr | None = None`,
  `realtime_url: str = "wss://api.deepgram.com/v1/listen"`.
- `lucy_deepgram/stt.py`: `DeepgramSttAdapter(model: str, settings:
  DeepgramSettings | None = None, transport=None)` implementing
  `SttProvider.transcribe(chunks) -> list[TranscriptEvent]` over the raw
  Deepgram realtime WebSocket JSON protocol (interim results ->
  `is_final=False`; final/`speech_final` -> `is_final=True`). Non-JSON or
  schema-violating frames raise `ProviderPayloadError`. `transport=None`
  opens the real WebSocket; tests inject `ReplayTransport`.
- `catalog.py`: `flux` and `nova-3`, `capabilities=[Capability.STT]`,
  `low_latency=True`, `recommended_for` copied verbatim from
  `default_model_registry()`.
- `stt_factory(model)`: returns the adapter when `api_key` is set;
  otherwise emits a `UserWarning` naming `DEEPGRAM_API_KEY` and returns
  `LocalSttSimulator()` so keyless quickstarts still run.

`packages/lucy-elevenlabs/` - TTS plugin:

- `ElevenLabsSettings(BaseSettings)`, env prefix `ELEVENLABS_`:
  `api_key: SecretStr | None = None`, `voice_id: str | None = None`,
  `ws_url: str = "wss://api.elevenlabs.io"` (stream-input path built from a
  named module constant, not inline).
- `lucy_elevenlabs/tts.py`: `ElevenLabsTtsAdapter(model: str, settings:
  ElevenLabsSettings | None = None, transport=None)` implementing
  `TtsProvider.synthesize(session_id, text) -> list[TtsStreamEvent]` over
  the stream-input WebSocket. Alignment text maps to
  `TtsStreamEvent(status="chunk", chunk_text=...)`; audio bytes never enter
  the events (media stays out of the control plane, ADR 0004). Blank text
  raises `ProviderPayloadError` before any wire frame.
- `catalog.py`: `flash-v2.5` and `turbo-v2.5`,
  `capabilities=[Capability.TTS]`, `low_latency=True`, `recommended_for`
  verbatim from `default_model_registry()`. (`scribe-realtime` STT stays in
  the core catalog; this card ships TTS only.)
- `tts_factory(model)`: adapter when keyed; else `UserWarning` naming
  `ELEVENLABS_API_KEY` and `LocalTtsSimulator()`.

`packages/lucy-openai/` - LLM + realtime plugin:

- `OpenAiSettings(BaseSettings)`, env prefix `OPENAI_`:
  `api_key: SecretStr | None = None`,
  `base_url: str = "https://api.openai.com/v1"`,
  `realtime_url: str = "wss://api.openai.com/v1/realtime"`.
- `lucy_openai/llm.py`: `OpenAiLlmAdapter(model: str, settings:
  OpenAiSettings | None = None, transport=None)` implementing
  `LlmProvider.stream_chat(request) -> AsyncIterator[LlmStreamEvent]` over
  chat-completions SSE via httpx: content deltas -> `TokenDelta`; tool-call
  deltas -> `ToolCallDelta` then `ToolCallReady`; usage chunk ->
  `UsageReport`; `[DONE]` -> `StreamEnd`; malformed SSE ->
  `ProviderPayloadError`.
- `lucy_openai/realtime.py`: `OpenAiRealtimeAdapter(model: str, settings:
  OpenAiSettings | None = None, transport=None)` with
  `async open(config: dict) -> OpenAiRealtimeSession`. Session surface
  matches card 38 exactly: `events()` async iterator (transcript deltas,
  tool calls, usage), `async send_tool_result(call_id: str, result: dict)
  -> None`, `async interrupt() -> None` (sends `response.cancel`). Audio is
  bridged by the gateway (ADR 0004); this adapter is control-plane only.
- `catalog.py`: `gpt-realtime` (`[REALTIME, LLM, TTS]`, `low_latency=True`),
  `gpt-5` (`[LLM]`), `gpt-5-mini` (`[LLM]`, `low_latency=True`), fields
  verbatim from `default_model_registry()`. OpenAI STT/TTS rows stay core.
- `PLUGIN` exposes `llm_factory` (keyless -> `UserWarning` naming
  `OPENAI_API_KEY` + `LocalLlmSimulator()`) and `realtime_factory` (keyless
  -> `RuntimeError` naming `OPENAI_API_KEY`; no realtime simulator exists
  until card 38 - do not invent one here).

### Catalog merge, quickstart, live profile

- `tests/test_plugin_catalog_merge.py`: with the three plugins installed,
  the merged `default_model_registry()` has no duplicate
  `(provider, model)` keys, and `get("deepgram", "nova-3")`,
  `get("elevenlabs", "flash-v2.5")`, `get("openai", "gpt-realtime")` each
  return exactly one row matching the plugin catalog.
- `examples/quickstart_voice_agent.py` (card 26): extend to read
  `LUCY_QUICKSTART_STT_SPEC` / `LUCY_QUICKSTART_TTS_SPEC` env vars
  (default `"local"`). With keys present the plugin adapters run; without
  keys the factories fall back to simulators with warnings - the quickstart
  must complete either way.
- Root `pyproject.toml` `[tool.pytest.ini_options]` gains
  `markers = ["live: hits real provider endpoints; requires API keys"]` and
  `addopts = "-m 'not live'"`. Live tests are `@pytest.mark.live` binding
  classes reusing the SAME contract suites with real-wire transports;
  `-m live` on the command line re-enables them (CLI `-m` overrides
  addopts).

### Recorded fixtures (enumerated)

- `packages/lucy-elevenlabs/tests/fixtures/tts_short_sentence.jsonl` plus
  `utterance_16k.wav` (the REAL returned audio, 16 kHz mono PCM, ~2 s).
- `packages/lucy-deepgram/tests/fixtures/utterance_16k.wav` (copy of the
  ElevenLabs output - real speech, not invented) and
  `stt_short_utterance.jsonl` (recorded while streaming that wav).
- `packages/lucy-openai/tests/fixtures/llm_text_stream.jsonl`,
  `llm_tool_call_stream.jsonl`, `realtime_text_turn.jsonl` (one user text
  turn with transcript deltas + usage, then an interrupt exchange).

## Chips

- [ ] **C1 - STT/TTS contract suites on simulators.** Write
  `tests/test_provider_contracts.py` first with binding classes
  `TestLocalSttSimulatorContract(SttContractSuite)` and
  `TestLocalTtsSimulatorContract(TtsContractSuite)` (stalled hooks use
  `LocalSttSimulator(delay_ms=60_000)` style - real impls, no mocks). Then
  implement `src/lucy/testing/contracts.py` (STT + TTS suites). Verify:
  `.venv/bin/python -m pytest tests/test_provider_contracts.py -q` -> 8
  passed.
- [ ] **C2 - LLM contract suite on the LLM simulator.** Requires
  `src/lucy/llm.py` (card 33); if missing, stop per Failure protocol. Add
  `TestLocalLlmSimulatorContract(LlmContractSuite)` to
  `tests/test_provider_contracts.py` first, then add `LlmContractSuite` to
  `src/lucy/testing/contracts.py`. Verify:
  `.venv/bin/python -m pytest tests/test_provider_contracts.py -q` -> 13
  passed.
- [ ] **C3 - Fixture replay engine.** Tests first in
  `tests/test_fixture_replay.py`:
  `test_replay_yields_received_frames_in_order`,
  `test_sent_frame_mismatch_raises_fixture_mismatch`,
  `test_stalled_transport_never_yields` (wrap in `asyncio.wait_for`).
  Check in `tests/fixtures/replay_echo_session.jsonl` - a hand-written
  echo-protocol sample is allowed ONLY here because it tests the replay
  engine, not a provider. Implement `src/lucy/testing/replay.py`. Verify:
  `.venv/bin/python -m pytest tests/test_fixture_replay.py -q` -> >=3 pass.
- [ ] **C4 - Fixture recorder with scrubbing.** Tests first in
  `tests/test_fixture_recorder.py`:
  `test_recorder_scrubs_credentials_from_frames`,
  `test_recorder_masks_volatile_fields`,
  `test_recorder_writes_loadable_jsonl` (feed an in-memory frame sequence
  including an `Authorization` header; output must contain neither header
  nor key and must round-trip through `load_fixture`). Implement
  `src/lucy/testing/record.py` (writer functions + `__main__` CLI). Verify:
  `.venv/bin/python -m pytest tests/test_fixture_recorder.py -q` -> >=3
  pass.
- [ ] **C5 - ElevenLabs package scaffold.** Tests first in
  `packages/lucy-elevenlabs/tests/test_catalog.py`:
  `test_plugin_discovered_with_tts_catalog` (via `load_plugins()`),
  `test_factory_without_key_warns_and_returns_simulator`
  (`pytest.warns(UserWarning, match="ELEVENLABS_API_KEY")`). Create
  `packages/lucy-elevenlabs/pyproject.toml` and
  `src/lucy_elevenlabs/{__init__.py,catalog.py,settings.py}`; add the
  `live` marker + `addopts` to root `pyproject.toml`; run `uv sync`.
  Verify: `.venv/bin/python -m pytest packages/lucy-elevenlabs/tests -q`
  -> all pass.
- [ ] **C6 - ElevenLabs fixture + TTS adapter.** Requires real
  `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` exported; if absent, stop
  per Failure protocol. Write
  `packages/lucy-elevenlabs/tests/test_contract_tts.py` first:
  `TestElevenLabsTtsContract(TtsContractSuite)` on `ReplayTransport`
  (malformed case: blank text), plus `@pytest.mark.live`
  `TestElevenLabsTtsLiveContract` on the real WebSocket. Add
  `lucy_elevenlabs/recording.py`, record `tts_short_sentence.jsonl` with
  `python -m lucy.testing.record`, save the returned audio as
  `tests/fixtures/utterance_16k.wav`, implement `lucy_elevenlabs/tts.py`.
  Verify: `env -u ELEVENLABS_API_KEY .venv/bin/python -m pytest
  packages/lucy-elevenlabs/tests -q` -> all non-live pass, no network.
- [ ] **C7 - Deepgram package scaffold.** Tests first in
  `packages/lucy-deepgram/tests/test_catalog.py`:
  `test_plugin_discovered_with_stt_catalog`,
  `test_factory_without_key_warns_and_returns_simulator` (match
  `DEEPGRAM_API_KEY`). Create `packages/lucy-deepgram/pyproject.toml` and
  `src/lucy_deepgram/{__init__.py,catalog.py,settings.py}`; run `uv sync`.
  Verify: `.venv/bin/python -m pytest packages/lucy-deepgram/tests -q` ->
  all pass.
- [ ] **C8 - Deepgram fixture + STT adapter.** Requires real
  `DEEPGRAM_API_KEY`; if absent, stop per Failure protocol. Copy
  `utterance_16k.wav` from C6 into `packages/lucy-deepgram/tests/fixtures/`.
  Write `packages/lucy-deepgram/tests/test_contract_stt.py` first:
  `TestDeepgramSttContract(SttContractSuite)` on `ReplayTransport`
  (malformed case: in-memory corrupted copy of one recorded frame - never
  edit the fixture file), plus `@pytest.mark.live`
  `TestDeepgramSttLiveContract`. Add `lucy_deepgram/recording.py`, record
  `stt_short_utterance.jsonl` streaming the wav, implement
  `lucy_deepgram/stt.py`. Verify: `env -u DEEPGRAM_API_KEY
  .venv/bin/python -m pytest packages/lucy-deepgram/tests -q` -> all
  non-live pass, no network.
- [ ] **C9 - OpenAI package scaffold + catalog merge.** Tests first:
  `packages/lucy-openai/tests/test_catalog.py::
  test_plugin_discovered_with_llm_and_realtime_catalog` and
  `tests/test_plugin_catalog_merge.py::
  test_merged_registry_has_no_duplicate_provider_model_keys` plus
  `test_plugin_catalog_rows_survive_merge`. Create
  `packages/lucy-openai/pyproject.toml` and
  `src/lucy_openai/{__init__.py,catalog.py,settings.py}`; run `uv sync`.
  Verify: `.venv/bin/python -m pytest packages/lucy-openai/tests
  tests/test_plugin_catalog_merge.py -q` -> all pass.
- [ ] **C10 - OpenAI LLM streaming adapter.** Requires real
  `OPENAI_API_KEY`; if absent, stop per Failure protocol. Write
  `packages/lucy-openai/tests/test_contract_llm.py` first:
  `TestOpenAiLlmContract(LlmContractSuite)` on `ReplayTransport` plus
  `@pytest.mark.live` `TestOpenAiLlmLiveContract`. Add
  `lucy_openai/recording.py`, record `llm_text_stream.jsonl` and
  `llm_tool_call_stream.jsonl`, implement `lucy_openai/llm.py` and wire
  `llm_factory` (keyless fallback to `LocalLlmSimulator` with warning).
  Verify: `env -u OPENAI_API_KEY .venv/bin/python -m pytest
  packages/lucy-openai/tests -q` -> all non-live pass, no network.
- [ ] **C11 - OpenAI realtime adapter.** Requires real `OPENAI_API_KEY`;
  if absent, stop per Failure protocol. Tests first in
  `packages/lucy-openai/tests/test_realtime_adapter.py`:
  `test_realtime_session_streams_transcript_deltas`,
  `test_interrupt_sends_response_cancel`,
  `test_send_tool_result_frames_match_recorded_shape`,
  `test_realtime_factory_without_key_raises_named_error`. Record
  `realtime_text_turn.jsonl`, implement `lucy_openai/realtime.py` with the
  card-38 session surface, register `realtime_factory`. Verify:
  `env -u OPENAI_API_KEY .venv/bin/python -m pytest
  packages/lucy-openai/tests -q` -> all non-live pass, no network.
- [ ] **C12 - Quickstart with plugin spec strings.** Test first in
  `tests/test_quickstart_provider_specs.py`:
  `test_quickstart_falls_back_to_simulators_without_keys` - run
  `examples/quickstart_voice_agent.py` via `subprocess.run` with
  `LUCY_QUICKSTART_STT_SPEC=deepgram/nova-3`,
  `LUCY_QUICKSTART_TTS_SPEC=elevenlabs/flash-v2.5`, and the three provider
  keys removed from the env; assert exit 0, transcript in stdout, and both
  warnings naming the missing env vars. Then extend the quickstart to read
  the two env vars (default `"local"`). Verify:
  `.venv/bin/python -m pytest tests/test_quickstart_provider_specs.py -q`
  -> all pass.
- [ ] **C13 - Full suite + bookkeeping.** Run the root suite, the three
  package suites without keys, the fixture-secret grep, and the three
  package builds (`uv build --package lucy-deepgram`, then
  `lucy-elevenlabs`, then `lucy-openai`); fill "Improvements noted"; move
  this card to `done/`. Verify: `.venv/bin/python -m pytest -q` -> full
  suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003). The sanctioned doubles
  are the `lucy.testing` simulators and `ReplayTransport` over fixtures
  RECORDED from real provider sessions. Never hand-write or hand-edit a
  provider fixture - re-record it with `python -m lucy.testing.record`.
  The only hand-written fixture is `tests/fixtures/replay_echo_session.jsonl`
  (it tests the replay engine itself, not a provider). In-memory corruption
  of a copy of a recorded frame inside a malformed-payload test is allowed.
- Do not hardcode API keys, voice ids, URLs, model names, or deadlines
  outside the typed `*Settings` classes, `catalog.py` registries, or named
  module constants (agents.md).
- Do not add provider SDK dependencies (`deepgram-sdk`, `elevenlabs`,
  `openai`) anywhere - raw `websockets`/`httpx` keep wire frames
  recordable - and do not add any provider dependency to the core `lucy`
  dist (ADR 0010: core never inherits provider churn).
- Do not import `lucy_deepgram`/`lucy_elevenlabs`/`lucy_openai` from
  `src/lucy/`; discovery happens only via the `lucy.plugins` entry-point
  group. Plugins are open code; no platform (`lucy-platform`/`pili`)
  imports in them either (ADR 0010 boundary).
- Do not modify the `SttProvider`/`TtsProvider`/`LlmProvider` Protocols or
  the resolution logic in `src/lucy/plugins.py` - they are the frozen ABI
  this card proves (card 28).
- Do not let `live`-marked tests run in the default profile, and do not
  commit any fixture frame containing a credential (the recorder scrubs;
  the DoD grep double-checks).
- Do not touch files outside: `packages/lucy-deepgram/`,
  `packages/lucy-elevenlabs/`, `packages/lucy-openai/`,
  `src/lucy/testing/{contracts.py,replay.py,record.py}`,
  `tests/{test_provider_contracts.py,test_fixture_replay.py,
  test_fixture_recorder.py,test_plugin_catalog_merge.py,
  test_quickstart_provider_specs.py,fixtures/replay_echo_session.jsonl}`,
  `examples/quickstart_voice_agent.py`, root `pyproject.toml` (pytest
  markers/addopts and workspace members only).
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_provider_contracts.py
      tests/test_fixture_replay.py tests/test_fixture_recorder.py
      tests/test_plugin_catalog_merge.py
      tests/test_quickstart_provider_specs.py -q` -> all pass
- [ ] `env -u DEEPGRAM_API_KEY -u ELEVENLABS_API_KEY -u OPENAI_API_KEY
      .venv/bin/python -m pytest packages/lucy-deepgram/tests
      packages/lucy-elevenlabs/tests packages/lucy-openai/tests -q` -> all
      pass with zero network access (live tests deselected by default)
- [ ] With the three provider keys exported: `.venv/bin/python -m pytest
      packages/lucy-deepgram/tests packages/lucy-elevenlabs/tests
      packages/lucy-openai/tests -m live -q` -> all live tests pass against
      the real sandboxes (manual run)
- [ ] `env -u DEEPGRAM_API_KEY -u ELEVENLABS_API_KEY
      LUCY_QUICKSTART_STT_SPEC=deepgram/nova-3
      LUCY_QUICKSTART_TTS_SPEC=elevenlabs/flash-v2.5 .venv/bin/python
      examples/quickstart_voice_agent.py` -> exit 0, transcript printed,
      warnings name both missing keys (simulator fallback path)
- [ ] Same quickstart command with real keys exported -> real provider
      events end-to-end (manual run)
- [ ] `grep -rinE "(api[-_]?key|authorization|bearer)"
      packages/lucy-deepgram/tests/fixtures
      packages/lucy-elevenlabs/tests/fixtures
      packages/lucy-openai/tests/fixtures` -> no matches
- [ ] `uv build --package lucy-deepgram && uv build --package
      lucy-elevenlabs && uv build --package lucy-openai` -> three wheels
      built (independently releasable)
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon is
      available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing (including provider API keys for
the recording chips), or the spec turns out wrong: do NOT check boxes, do
NOT force tests green. Leave the card in `in_progress/`, document what
happened under "Improvements noted", and report. Partial honest work beats
fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
