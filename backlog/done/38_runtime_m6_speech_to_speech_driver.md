# 38 - Voice runtime M6: speech-to-speech driver

**Sprint:** S6 - Provider ecosystem
**Epic:** Voice runtime
**Estimated effort:** ~10 h
**Depends on:** 37
**State:** done

## Goal

Realtime models (`Capability.REALTIME` in `src/lucy/providers.py`) run
behind the same session abstraction as the cascaded path (ADR 0011): the
cognition graph collapses to pre/post hooks while tools, observability, and
transport stay byte-identical. Proof is the parity gate: the same eval
suite runs green on BOTH drivers with identical funnel outcomes.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  provider names, models, or budgets).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the decision this
  card implements: cascaded and speech-to-speech run behind ONE
  `TurnDriver` abstraction; tools, spans, waterfalls, and the control
  channel schema are identical for both.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - audio never
  crosses the control channel; the gateway bridges caller audio directly
  to the realtime provider.
- `docs/adr/0003-no-mocks-testing-policy.md` - `LocalRealtimeSimulator` is
  the sanctioned double: a real in-process provider implementation.
- `docs/adr/0010-open-core-split.md` - everything here is open SDK code;
  telemetry leaves only through the `lucy.observe` seam.
- `src/lucy/providers.py` - `Capability.REALTIME`, `ModelInfo`,
  `ModelRegistry`, `default_model_registry()` (`openai/gpt-realtime` and
  `google/gemini-live-flash-native-audio` carry REALTIME); after card 28
  also `parse_spec_string` and `LOCAL_PROVIDER_NAME`, which this card
  reuses. Card 28 lands earlier in S6; if the parser is absent, stop per
  the Failure protocol.
- `src/lucy/specs.py` - `LucySpec` / `VoiceSpec`; this card adds the
  `llm_provider` spec-string field that `select_driver` reads.
- `src/lucy/evals.py` - `default_sales_booking_scenarios()` (the six
  golden scenarios), `EvalEvidence`, `score_synthetic_call`; the parity
  suite scores both drivers through this exact rubric.
- `src/lucy/metrics.py` - `LatencyWaterfall`; realtime turns fill the SAME
  schema: `llm_ms` carries voice-to-voice, `stt_ms`/`tts_ms` stay zero.
- `src/lucy/mcp.py` - `McpClient`, `LocalMcpCommandTransport`,
  `McpAuditEvent`; the audit log both drivers must populate identically.
- `backlog/pending/32_runtime_m0_walking_skeleton.md` - creates
  `src/lucy/transport/schema.py` (`Envelope`, `parse_event`, `BargeIn`
  with `during: speaking|thinking`), `src/lucy/transport/dev_gateway.py`
  (`LocalGatewaySimulator`), `src/lucy/clock.py` (`Clock`, `ManualClock`),
  `src/lucy/settings.py` (`LatencyBudgets`), `src/lucy/session.py`
  (`VoiceSession`, turn-task cancellation), `src/lucy/harness.py`
  (`ConversationHarness`).
- `backlog/pending/33_runtime_m1_streaming_llm.md` - creates
  `src/lucy/llm.py` (`ToolCallReady`, `UsageReport`, `LocalLlmSimulator`,
  `LlmModelNotRegistered`) and `src/lucy/drivers.py` (`TurnDriver`,
  `DriverEvent`, `TurnDriverReport`, `CascadedTurnDriver`, `LlmPricing`
  in settings). This card extends `drivers.py`; it never redefines those.
- `backlog/pending/34_runtime_m2_realtime_tools.md` - creates
  `src/lucy/tools.py` (`ToolDef`, `ToolResult`, `McpToolExecutor`) and the
  cascaded tool-round semantics (round cap, `mcp_tools_ms` accrual) this
  card must mirror exactly.
- `backlog/pending/35_runtime_m3_interruption_correctness.md` - barge-in
  semantics and the harness evidence path (`interruption_handled`) the
  realtime side must reproduce.
- `backlog/pending/37_runtime_m5_agent_graph_checkpointing.md` - the
  dependency: `AgentGraph` and the default graph whose nodes collapse to
  hooks on the realtime path.
- `backlog/pending/28_add_plugin_mechanism_and_workspace.md` -
  `LucyPlugin.realtime_factory` is the slot real provider adapters fill
  later (card 29); this card ships contracts, driver, and simulator only.
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

## Spec

### Realtime contracts - `src/lucy/drivers.py` (extend)

- `RealtimeSessionConfig` (frozen dataclass): `provider: str`,
  `model: str`, `system_prompt: str`, `tools: Sequence[ToolDef] = ()`,
  `locale: str | None = None`.
- Realtime event union (frozen dataclasses):
  `RealtimeUserTranscript(text: str, final: bool)`,
  `RealtimeAssistantDelta(utterance_id: str, text: str)`,
  `RealtimeAssistantDone(utterance_id: str, full_text: str)` - where
  `full_text` is exactly the text the provider voiced (the truncation
  truth on interrupt) - plus `ToolCallReady` and `UsageReport` REUSED from
  `src/lucy/llm.py` (card 33), never redefined.
  `RealtimeEvent = Union[...]` of exactly those five.
- `RealtimeSession` Protocol (`@runtime_checkable`):
  - `def events(self) -> AsyncIterator[RealtimeEvent]: ...`
  - `async def send_tool_result(self, result: ToolResult) -> None: ...`
  - `async def interrupt(self) -> None: ...`
  - `async def close(self) -> None: ...`
- `RealtimeProvider` Protocol (`@runtime_checkable`):
  `async def open(self, config: RealtimeSessionConfig) ->
  RealtimeSession: ...` Real provider websocket adapters ship later as
  plugin packages via `LucyPlugin.realtime_factory` (cards 28/29).
- `RealtimeHooks` (frozen dataclass):
  `pre_turn: Sequence[Callable[[str], Awaitable[None]]] = ()`,
  `post_turn: Sequence[Callable[[TurnDriverReport], Awaitable[None]]] =
  ()`. This is where the graph collapses for realtime models: guardrail
  and funnel callables (cards 37/39) run on transcript events. Pre hooks
  receive the final user transcript before the assistant reply is
  consumed; post hooks receive the terminal report before it is yielded.
- `resolve_realtime(registry: ModelRegistry, provider: str, model: str)
  -> ModelInfo` - raises `LlmModelNotRegistered` (reused from
  `src/lucy/llm.py`) when the pair is absent or lacks
  `Capability.REALTIME`.
- `RealtimeTurnDriver(provider: RealtimeProvider,
  config: RealtimeSessionConfig, registry: ModelRegistry, clock: Clock,
  budgets: LatencyBudgets, tool_executor: McpToolExecutor | None = None,
  tools: Sequence[ToolDef] = (), hooks: RealtimeHooks = RealtimeHooks(),
  pricing: LlmPricing | None = None)` - implements the SAME `TurnDriver`
  Protocol from card 33. Constructor validates `(config.provider,
  config.model)` via `resolve_realtime`. `run_turn(user_text, history)`:
  1. First call opens `await provider.open(config)` and caches the
     session for the whole call; later turns reuse it.
     `async def aclose() -> None` closes the cached session.
  2. Records turn start via `clock.monotonic()`, runs `hooks.pre_turn` in
     order with `user_text`.
  3. Consumes `session.events()` for this turn: accumulates
     `RealtimeAssistantDelta` text into the public driver attribute
     `last_voiced_text: str`, retains the `UsageReport`, handles each
     `ToolCallReady` as a tool round, stops after
     `RealtimeAssistantDone`.
  4. Yields ZERO `TtsSpeak` directives - the provider voices its own
     audio and the gateway bridges it (ADR 0004). The only `DriverEvent`
     is the terminal `TurnDriverReport`.
  5. The report: `assistant_text = RealtimeAssistantDone.full_text`;
     `llm_ms` = clock delta from turn start to `RealtimeAssistantDone`
     (voice-to-voice; the waterfall's `stt_ms` and `tts_ms` stay 0.0
     because provider-internal STT/TTS is indivisible); `usage` from the
     retained report (None when absent); `llm_cost` from usage x
     `pricing` exactly like `CascadedTurnDriver` (0.0 when pricing is
     None).
  6. Runs `hooks.post_turn` in order with the report, then yields it.

  Tool round on `ToolCallReady(call_id, name, arguments)`:
  - Resolve `name` against `tools` by `ToolDef.name`. Unknown name, or
    `tool_executor is None`, produces
    `ToolResult(error_kind="unknown_tool")` with no execution; the round
    still counts toward the cap.
  - Execute via `await tool_executor.execute(tool, arguments)` - the SAME
    `McpToolExecutor` as card 34: `McpClient` permissions, schema
    validation, audit, and `tool_call` telemetry are byte-identical to
    the cascaded path. Never call `transport.call_tool` directly.
  - Deliver with `await session.send_tool_result(result)`; the provider
    continues the same utterance.
  - Rounds are bounded by `LatencyBudgets.max_tool_rounds_per_turn`
    (typed settings, card 32; never a literal). Past the cap the request
    is NOT executed: send back `ToolResult(error_kind="budget")`.
  - Accrue each `ToolResult.elapsed_ms` into the turn's
    `LatencyWaterfall.mcp_tools_ms` through the same mechanism card 34
    added to the cascaded driver - read the merged `drivers.py` first;
    do not invent a parallel channel.

  Barge-in: when `VoiceSession` cancels the turn task (BargeIn or
  VadSpeechStart, cards 32/35), the driver catches
  `asyncio.CancelledError`, awaits `session.interrupt()` so the provider
  stops voicing, and re-raises WITHOUT yielding a report.
  `last_voiced_text` then holds exactly the text voiced before the
  interrupt (provider-side truth; marks-based truncation remains the
  cascaded path's mechanism).

### Driver selection - `src/lucy/drivers.py` and `src/lucy/specs.py`

- `src/lucy/specs.py` (additive): `VoiceSpec.llm_provider: str =
  LOCAL_PROVIDER_NAME` - the LLM/realtime spec string in card 28 syntax
  (`"local"` or `"<provider>/<model>"`, e.g. `"openai/gpt-realtime"`).
  The default imports the constant from `lucy.providers`; the literal
  appears nowhere.
- `DriverKind(str, Enum)`: `CASCADED = "cascaded"`,
  `REALTIME = "realtime"`.
- `select_driver(spec: LucySpec, registry: ModelRegistry) -> DriverKind`:
  - parses `spec.voice.llm_provider` with `parse_spec_string` (card 28,
    reused unchanged; `InvalidProviderSpecError` propagates);
  - `("local", None)` -> `DriverKind.CASCADED` (simulator-backed path);
  - `(provider, model)`: `registry.get(provider, model)` returning None
    -> `LlmModelNotRegistered` naming the pair; `Capability.REALTIME` in
    capabilities -> `DriverKind.REALTIME`; else `Capability.LLM` ->
    `DriverKind.CASCADED`; else `LlmModelNotRegistered` whose message
    names the model's actual capabilities.
  - Pure routing: zero provider-name literals, no instantiation. The
    facade constructs the concrete driver with providers resolved through
    the plugin registry (`LucyPlugin.realtime_factory`, card 28).

### Transport directives - `src/lucy/transport/schema.py` (additive)

- `RealtimeConnect(provider: str, model: str)` - envelope type
  `"realtime.connect"`: tells the gateway to bridge caller audio directly
  to the named realtime provider. Identifiers only - no credentials and
  no audio bytes (ADR 0004; the gateway owns provider credentials).
- `RealtimeToolResult(call_id: str, output_json: str)` - envelope type
  `"realtime.tool_result"`: relays a tool result to the provider through
  the gateway-held provider connection.
- Both are downstream (python -> gateway) Pydantic models with
  `extra="forbid"`, registered in `parse_event` like every card 32 type.
- This card defines and round-trips the directives so the Rust gateway
  (card 51) and the telephony transports build against them; emitting
  them from live session wiring lands with those transports. The parity
  tests here connect `RealtimeTurnDriver` to the simulator directly. In
  production the gateway also emits `TtsPlayback` events for bridged
  provider audio, so SPEAKING-phase semantics converge; the schema
  already carries them.

### Gateway simulator - `src/lucy/transport/dev_gateway.py` (additive)

- `LocalGatewaySimulator` gains one scenario-driven behavior: when a
  caller line is marked as an interruption and NO TTS playback is in
  flight (the realtime case - the driver emits no `TtsSpeak`), it fires
  `BargeIn(during="thinking")` after one scripted clock interval instead
  of waiting for playback. Card 32's mid-playback barge-in behavior is
  unchanged; this is an additional trigger, not a replacement.

### Simulator - `src/lucy/testing/realtime.py` (new)

- `ScriptedRealtimeToolCall` (frozen dataclass): `call_id: str`,
  `name: str`, `arguments: dict`, `followup_text: str`.
- `ScriptedRealtimeTurn` (dataclass): `user_text: str`,
  `assistant_text: str`, `usage: UsageReport`,
  `tool_calls: Sequence[ScriptedRealtimeToolCall] = ()`.
- `LocalRealtimeSimulator(turns: list[ScriptedRealtimeTurn], clock:
  Clock, transcript_interval_ms: float)` - real in-process
  `RealtimeProvider`. `open(config)` appends to `opened_configs` and
  returns a `LocalRealtimeSession` over the scripted turns. Per turn,
  `events()` yields, paced via `clock.sleep` (never wall time):
  1. word-accumulating `RealtimeUserTranscript(final=False)` partials,
     then the full `final=True` transcript (mirrors the card 32 gateway
     simulator's rising partials);
  2. `RealtimeAssistantDelta` chunks of `assistant_text`;
  3. per scripted tool call, in order: `ToolCallReady`, then WAIT on an
     `asyncio.Event` until `send_tool_result` is called, record the
     result in `received_tool_results`, then stream `followup_text`
     deltas;
  4. `UsageReport`, then `RealtimeAssistantDone(full_text=<all voiced
     text>)`.
  - `interrupt()`: stops remaining deltas; the in-flight utterance ends
    immediately with `RealtimeAssistantDone(full_text=<text emitted so
    far>)`, its `UsageReport` is skipped, and `interrupted = True`.
  - `close()` sets `closed = True`; `events()` finishes after the last
    scripted turn. Consumer cancellation propagates `CancelledError`.
- Export `LocalRealtimeSimulator`, `ScriptedRealtimeTurn`, and
  `ScriptedRealtimeToolCall` from `src/lucy/testing/__init__.py`.

### Dual-driver parity - `tests/test_realtime_driver.py`

- One scripting helper per driver in the test file: cascaded turns come
  from `LocalLlmSimulator` (card 33) tokenizing the scenario agent lines;
  realtime turns come from `LocalRealtimeSimulator` carrying the same
  lines. Same scenario text in, same transcript out.
- For every scenario in `default_sales_booking_scenarios()` (all six),
  run `ConversationHarness.run(scenario, driver=...)` once per driver,
  assemble `EvalEvidence` through the same evidence path the cascaded
  eval tests use (card 35; `interruption_handled` flows from interrupted
  turns), and score with `score_synthetic_call`:
  - both `EvalRubricResult.passed` are True;
  - `actual_outcome` and `gates` are equal across drivers per scenario.
- `booking_interruption` on the realtime side: barge-in arrives during
  the assistant stream (THINKING phase - no playback exists without a
  gateway), cancels the turn task, and the driver maps it to
  `session.interrupt()`.
- Tool-audit parity: a booking turn calling `crm.book_meeting` through
  `McpClient(LocalMcpCommandTransport(),
  allowed_tools=["crm.book_meeting"])` on BOTH drivers yields audit logs
  whose `(server, tool, allowed)` sequences are equal, plus equal
  `tool_call` telemetry keys.

### File scope

- Create: `src/lucy/testing/realtime.py`, `tests/test_realtime_driver.py`.
- Modify: `src/lucy/drivers.py` (realtime contracts, driver, selection),
  `src/lucy/transport/schema.py` (two directives, additive),
  `src/lucy/transport/dev_gateway.py` (thinking-phase barge-in trigger,
  additive), `src/lucy/specs.py` (one field, additive),
  `src/lucy/testing/__init__.py` (exports).
- `src/lucy/session.py`, `src/lucy/tools.py`, `src/lucy/mcp.py`, and
  `src/lucy/llm.py` stay byte-identical. Nothing else is touched.

## Chips

- [x] **C1 - Realtime contracts.** Record `shasum src/lucy/session.py
  src/lucy/tools.py src/lucy/mcp.py src/lucy/llm.py` in "Improvements
  noted" before coding. Write tests first in new
  `tests/test_realtime_driver.py`:
  `test_realtime_event_union_covers_exactly_five_event_types` and
  `test_realtime_protocols_are_runtime_checkable` (a minimal in-test
  session class passes `isinstance`; a plain `object()` fails). Then
  implement `RealtimeSessionConfig`, the three new event dataclasses,
  `RealtimeEvent`, `RealtimeSession`, `RealtimeProvider`, and
  `RealtimeHooks` in `src/lucy/drivers.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` -> all
  pass (>=2 tests).
- [x] **C2 - Transport directives.** Tests first:
  `test_realtime_directives_round_trip_through_parse_event` and
  `test_realtime_directives_reject_extra_fields`. Implement
  `RealtimeConnect` and `RealtimeToolResult` plus `parse_event`
  registration in `src/lucy/transport/schema.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py
  tests/test_transport_schema.py -q` -> all pass (no schema regressions).
- [x] **C3 - Simulator transcripts and pacing.** Tests first:
  `test_simulator_emits_rising_user_partials_then_final_then_done` and
  `test_simulator_paces_events_via_manual_clock_without_wall_time`.
  Implement `ScriptedRealtimeTurn`, `ScriptedRealtimeToolCall`,
  `LocalRealtimeSimulator`, and `LocalRealtimeSession` (transcript path
  only) in `src/lucy/testing/realtime.py`; export from
  `src/lucy/testing/__init__.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` -> all
  pass, total runtime < 1 s (proves no real sleeping).
- [x] **C4 - Simulator tool round and interrupt.** Tests first:
  `test_simulator_tool_call_waits_for_result_then_streams_followup` and
  `test_simulator_interrupt_stops_deltas_and_done_carries_partial_text`.
  Implement `send_tool_result`, `interrupt`, and `close` in
  `src/lucy/testing/realtime.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` -> all
  pass.
- [x] **C5 - RealtimeTurnDriver happy turn.** Tests first:
  `test_run_turn_emits_no_tts_speak_and_one_terminal_report`,
  `test_report_llm_ms_is_voice_to_voice_on_manual_clock` (clock delta
  from `run_turn` start to `RealtimeAssistantDone`; report carries usage
  and cost like the cascaded driver), and
  `test_resolve_realtime_rejects_model_without_realtime_capability`
  (`default_model_registry()`: `anthropic/claude-sonnet` raises,
  `openai/gpt-realtime` resolves). Implement `resolve_realtime` and the
  driver core (no tools, no hooks yet) in `src/lucy/drivers.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` -> all
  pass.
- [x] **C6 - Driver tool rounds via the real McpToolExecutor.** Tests
  first: `test_realtime_tool_round_executes_via_mcp_executor_with_audit`
  (real `McpClient(LocalMcpCommandTransport(),
  allowed_tools=["crm.book_meeting"])`; result delivered to the session;
  one `allowed=True` audit entry; follow-up text lands in
  `report.assistant_text`),
  `test_realtime_tool_rounds_capped_by_typed_budget` (three scripted
  tool calls, `LatencyBudgets(max_tool_rounds_per_turn=2)`: exactly two
  executions, the third gets `error_kind == "budget"` unexecuted), and
  `test_tool_elapsed_ms_accrues_into_waterfall_same_as_cascaded`.
  Implement the tool round in `RealtimeTurnDriver`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` -> all
  pass.
- [x] **C7 - Barge-in mapping and hooks.** Tests first:
  `test_cancelling_run_turn_calls_session_interrupt_no_orphan_tasks`
  (cancel the consuming task mid-deltas; `session.interrupted` is True;
  no report yielded; `last_voiced_text` equals the deltas emitted before
  the interrupt; assert no orphans via `asyncio.all_tasks()`) and
  `test_pre_and_post_hooks_run_in_order_on_transcript_events` (recorder
  callables capture the user transcript before consumption and the
  report before yield, in declared order). Implement cancellation
  handling, `aclose`, and hook execution in `src/lucy/drivers.py`.
  Verify: `.venv/bin/python -m pytest tests/test_realtime_driver.py -q`
  -> all pass.
- [x] **C8 - select_driver and the spec field.** Tests first:
  `test_select_driver_routes_by_realtime_capability`
  (`openai/gpt-realtime` -> REALTIME, `anthropic/claude-sonnet` ->
  CASCADED, `"local"` -> CASCADED, all against
  `default_model_registry()`),
  `test_select_driver_rejects_unregistered_or_capabilityless_model`
  (unknown pair names the pair; STT-only `deepgram/nova-3` names its
  actual capabilities), and
  `test_voice_spec_defaults_llm_provider_to_local_constant`. Implement
  `DriverKind` and `select_driver` in `src/lucy/drivers.py`; add
  `llm_provider` to `VoiceSpec` in `src/lucy/specs.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py
  tests/test_specs.py -q` -> all pass (specs suite unbroken).
- [x] **C9 - Thinking-phase barge-in and interruption parity.** Tests
  first: `test_gateway_simulator_fires_thinking_barge_in_without_playback`
  and `test_booking_interruption_parity_across_drivers`
  (`booking_interruption` through `ConversationHarness` on both drivers;
  both pass with `interruption_handled` evidence; realtime maps the
  cancellation to `session.interrupt()`). Implement the additive trigger
  in `src/lucy/transport/dev_gateway.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_driver.py
  tests/test_dev_gateway.py -q` -> all pass (gateway suite unbroken).
- [x] **C10 - Six-scenario parity gate.** Tests first:
  `test_eval_suite_green_on_both_drivers_with_identical_funnel_outcomes`
  (parametrized over all six `default_sales_booking_scenarios()`; both
  drivers pass; `actual_outcome` and `gates` equal per scenario) and
  `test_booking_tool_audit_identical_across_drivers` (`(server, tool,
  allowed)` audit sequences equal, equal `tool_call` telemetry keys).
  Add the scripting helpers; adjust driver wiring only as needed.
  Verify: `.venv/bin/python -m pytest tests/test_realtime_driver.py -q`
  -> all pass (>=18 tests).
- [x] **C11 - Full suite + bookkeeping.** Run everything, confirm
  `shasum src/lucy/session.py src/lucy/tools.py src/lucy/mcp.py
  src/lucy/llm.py` matches the values recorded in C1, fill "Improvements
  noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003).
  `LocalRealtimeSimulator`, `LocalLlmSimulator`,
  `LocalMcpCommandTransport`, `LocalGatewaySimulator`, and `ManualClock`
  are the sanctioned doubles - real implementations of real contracts.
  Never monkeypatch provider sessions or fabricate events outside the
  simulator.
- Do not hardcode provider names, model names, URLs, budgets, or round
  caps (agents.md): routing is capability-based via `ModelRegistry`;
  provider literals live only in registry catalog data and test scenario
  scripts; timing and caps come from `LatencyBudgets`; the local default
  is the `LOCAL_PROVIDER_NAME` constant; pricing lives in `LlmPricing`.
- Do not carry audio bytes, codecs payloads, or credentials in
  `realtime.connect`, `realtime.tool_result`, or any control-channel
  message (ADR 0004); the gateway bridges audio and owns credentials.
- Do not modify the `TurnDriver` Protocol, `DriverEvent`,
  `TurnDriverReport` fields, `CascadedTurnDriver` behavior, or
  `McpToolExecutor` (cards 33/34 contracts). `RealtimeTurnDriver`
  implements the same Protocol; extend `drivers.py` only.
- Do not redefine `ToolCallReady`, `UsageReport`, `ToolDef`, or
  `ToolResult`; import them from `src/lucy/llm.py` / `src/lucy/tools.py`.
- Do not bypass `McpClient`: never call `transport.call_tool` directly
  and never send an unexecuted result to the provider - identical
  permissions and audit on both drivers is the point of this card.
- Do not emit `TtsSpeak` or `TtsCancel` from the realtime driver; the
  provider voices its own audio.
- Do not implement real provider websocket adapters (OpenAI Realtime,
  Gemini Live); they ship as plugin packages via
  `LucyPlugin.realtime_factory` (cards 28/29).
- Do not write a second eval rubric or fork the scenarios: parity means
  the same `default_sales_booking_scenarios()` scored by
  `score_synthetic_call` on both drivers.
- Do not touch files outside the File scope list above;
  `src/lucy/session.py`, `src/lucy/tools.py`, `src/lucy/mcp.py`, and
  `src/lucy/llm.py` stay byte-identical (shasum-checked).
- Do not check a chip or Definition of Done box without running its
  Verify command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): spans,
  waterfalls, and `tool_call` telemetry leave only through the
  `lucy.observe` seam; no ingest, storage, or dashboard code.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_realtime_driver.py -q` ->
      all pass (>=18 tests): all six golden scenarios produce identical
      funnel outcomes and identical tool audits on both drivers; realtime
      reports carry voice-to-voice `llm_ms` in the standard waterfall
      schema with `stt_ms`/`tts_ms` zero; barge-in maps to
      `RealtimeSession.interrupt`.
- [x] `.venv/bin/python -m pytest tests/test_realtime_driver.py
      tests/test_cascaded_driver.py tests/test_realtime_tools.py
      tests/test_transport_schema.py tests/test_dev_gateway.py
      tests/test_specs.py -q` -> all pass (cascaded driver, tool
      executor, schema, gateway, and spec behavior unchanged).
- [x] `shasum src/lucy/session.py src/lucy/tools.py src/lucy/mcp.py
      src/lucy/llm.py` -> identical to the checksums recorded in C1
      (same session abstraction, reused unchanged).
- [x] `grep -rn "unittest.mock\|MagicMock\|mocker"
      tests/test_realtime_driver.py src/lucy/testing/realtime.py` -> no
      matches.
- [x] `grep -rin "openai\|gemini\|deepgram\|elevenlabs"
      src/lucy/drivers.py` -> no matches (capability routing, zero
      provider literals in driver code).
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available).
- [x] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

- C1 protected-file SHA-1 baseline: `session.py`
  `43043c457c64f4f651e02597d8c151bc11a3ca9d`, `tools.py`
  `fbae3db4112b981da74d78a93ab4222e9712efc0`, `mcp.py`
  `1014cfa025f381a36fcdcd065b3614e7cb53f9c4`, and `llm.py`
  `6145db53bd181d48dea404bba554c4610ab1a7b3`.
- The card 29 OpenAI adapter was aligned to the landed card 38 ABI:
  `RealtimeSessionConfig`, the shared five-event union, and
  `send_tool_result(ToolResult)`. Its authenticated replay fixture remains green.
- Realtime tool-call IDs stay provider-session state and are paired FIFO with
  typed tool results, so the shared `ToolResult` contract does not leak provider
  identifiers into core.
- The six golden scenarios execute through both driver implementations and use
  the same `score_synthetic_call` rubric; MCP audit tuples and safe telemetry key
  sets are identical for the booking tool path.
- Card 51 remains the owner of the out-of-process audio bridge and measured
  `transport_ms`. This card intentionally proves the Python control plane with
  zero audio bytes and zero `TtsSpeak` directives.
- Final evidence: 28 card tests, 78 focused regression tests, 443 root tests,
  and 23 offline plugin tests passed in Docker; protected file hashes matched C1.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
