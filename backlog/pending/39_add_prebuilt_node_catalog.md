# 39 - Add prebuilt node and graph catalog

**Sprint:** S4 - Graph and state
**Epic:** SDK surface
**Estimated effort:** ~12 h
**Depends on:** 37
**State:** pending

## Goal

The standard library that makes the framework adoptable: voice-native nodes
and complete prebuilt graphs, so the 5-minute path and the custom-graph path
share the same primitives. Every node is deadline-bounded with typed config
and runs under the existing executor; `booking_agent()` passing all six
golden booking scenarios is the S4 exit demo (`backlog/sprints.md`).

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  provider names, models, thresholds, or budgets).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - cognition plane: nodes
  run inside user-authored `AgentGraph`s compiled onto `GraphExecutor`.
- `docs/adr/0010-open-core-split.md` - everything in this card is open SDK
  code; CRM sync goes through MCP, never through platform ingest.
- `docs/adr/0007-on-the-fly-context-synthesis.md` - the synthesis node:
  deadline-first, retrieval-only fallback, grounding ids preserved.
- `docs/adr/0009-turn-taking-and-conversational-fluidity.md` - turn-lifecycle
  stages map to `GraphNode`s with deadlines; fluidity is config, not literals.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - telephony
  nodes emit control-channel directives only, never audio bytes.
- `docs/adr/0003-no-mocks-testing-policy.md` - simulators and local protocol
  servers are the only sanctioned test doubles.
- `src/lucy/runtime.py` - `GraphNode(name, handler, deadline_ms, retries,
  fallback)`, `GraphExecutor`, `GraphContext`; reused unchanged.
- `backlog/pending/37_runtime_m5_agent_graph_checkpointing.md` - the
  dependency card that creates `src/lucy/graph.py` (`AgentGraph`,
  `CompiledAgentGraph.invoke_turn`, `add_conditional_edge`),
  `src/lucy/state.py` (`ConversationState` with `slots`, `funnel_stage`,
  `tool_results`, `agent_state`), and `TurnContext` (with `emit`, clock,
  cancellation) in `src/lucy/runtime.py`. Those exist once 37 is done.
- `backlog/pending/32_runtime_m0_walking_skeleton.md` - creates
  `src/lucy/transport/schema.py` (`Envelope`, `parse_event`, downstream
  `TtsSpeak`/`TtsCancel`/`DtmfSend`/`Transfer`/`SessionEnd`/
  `SessionConfigure`, upstream `Dtmf`), `src/lucy/transport/dev_gateway.py`
  (`LocalGatewaySimulator`), `src/lucy/clock.py` (`Clock`, `ManualClock`),
  `src/lucy/settings.py` (`LatencyBudgets`), and `src/lucy/harness.py`
  (`ConversationHarness`).
- `backlog/pending/33_runtime_m1_streaming_llm.md` - creates
  `src/lucy/llm.py` (`LlmProvider`, `LocalLlmSimulator`, `resolve_llm`) and
  `src/lucy/speech.py` (`SentenceAssembler`, `TtsPlanner`).
- `backlog/pending/34_runtime_m2_realtime_tools.md` - creates
  `src/lucy/tools.py` (`ToolDef`, `ToolProfile`, `ToolResult`,
  `McpToolExecutor`); the only sanctioned tool-execution path.
- `src/lucy/rag.py` - `SpeculativeRagNode.prefetch`,
  `RagResult.prompt_context`, `RagChunk.grounding_id`; reused unchanged.
- `src/lucy/metrics.py` - `SentimentScore`, `FunnelEvent`,
  `CrmMetricEvent.crm_ready_payload`, `LatencyWaterfall`; emit these
  existing models, never new shapes.
- `src/lucy/specs.py` - `FunnelStage`, `SentimentLabel`, `VoiceSpec`.
- `src/lucy/providers.py` - `ModelRegistry.get`/`by_capability`,
  `ModelInfo.low_latency`, `Capability`; all model resolution goes here.
- `src/lucy/mcp.py` - `McpClient` permissions/schema/audit, wrapped by
  `McpToolExecutor`; never bypassed.
- `src/lucy/evals.py` - `default_sales_booking_scenarios()` (exactly six),
  `SyntheticCallScenario`, `EvalEvidence`, `score_synthetic_call`.
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

## Spec

`src/lucy/nodes/` organized by family, every node deadline-bounded with
typed config (no hardcoded thresholds anywhere in node code).

### Node base - `src/lucy/nodes/base.py` (new)

- `NodeConfig(BaseModel, extra="forbid")`: `deadline_ms: int`,
  `retries: int = 0`. Every config below subclasses it; a config's defaults
  are the ONLY place its numbers exist.
- `PrebuiltNode` Protocol: `name: str`, `config: NodeConfig`,
  `async def __call__(self, ctx: TurnContext) -> Any`; optional
  `async def fallback(self, ctx: TurnContext) -> Any`.
- `as_graph_node(node: PrebuiltNode) -> GraphNode` - maps name, handler,
  `config.deadline_ms`, `config.retries`, and `fallback` (when present)
  onto `lucy.runtime.GraphNode`; the single bridge to the executor.
- Node results land in `ctx.results[node.name]`; state mutations target the
  `ConversationState` carried by `TurnContext` (card 37). Downstream
  directives and metric events leave through `ctx.emit`.
- `src/lucy/nodes/__init__.py` re-exports every node and config below - the
  catalog's one public import surface.

### Perception/context family - `src/lucy/nodes/perception.py` (new)

- `ContextSynthesisNode(rag: SpeculativeRagNode, llm: LlmProvider | None =
  None, config: ContextSynthesisConfig)` - wraps `SpeculativeRagNode`
  (reused unchanged, ADR 0007). With `llm` set, synthesizes grounded
  context from `RagResult.prompt_context` plus the live transcript; on
  deadline or `deadline_exceeded` falls back to retrieval-only
  `prompt_context`. Result: `str` preserving `grounding_id` citations.
  Config fields: `deadline_ms`, `max_chunks`.
- `SlotFillerNode(slots: Sequence[SlotSpec], config: SlotFillerConfig)` -
  `SlotSpec` frozen dataclass: `name: str`, `pattern: str` (regex over the
  user text), `required: bool = False`,
  `confirm_template: str | None = None`. Matches merge into
  `ConversationState.slots`; when `confirm_template` is set, emits exactly
  one `TtsSpeak` rendered from the captured value (optional verbal
  confirmation). Result: `dict[str, str]` of newly filled slots.
- `SentimentNode(llm: LlmProvider, registry: ModelRegistry, provider: str,
  model: str, config: SentimentConfig)` - classifies the last user
  utterance into a `SentimentScore` (its `model` field is the resolved
  registry id); stores it in `ConversationState.agent_state["sentiment"]`.
  Resolution via `resolve_llm` (card 33); unknown pair fails loud.
- `FunnelClassifierNode(llm, registry, provider, model, config:
  FunnelClassifierConfig)` - classifies the turn into a `FunnelStage`,
  updates `ConversationState.funnel_stage`, and emits the EXISTING
  `FunnelEvent` model through `ctx.emit` (no new event shapes).
- `LanguageDetectNode(config: LanguageDetectConfig)` - config:
  `supported_locales: Sequence[str]`, `min_confidence: float`. Detects the
  caller locale from user text; on a confident change emits one
  `SessionConfigure` directive hot-swapping STT/TTS config and records the
  locale in `ConversationState.agent_state["locale"]` (prompt config reads
  it). Below confidence or unsupported locale: keep current, no directive.

### Decision/control family - `src/lucy/nodes/decision.py` (new)

- `IntentRouterNode(intents: Mapping[str, str], llm, registry, provider,
  model, config: IntentRouterConfig)` - `intents` maps label ->
  description; classifies user text into exactly one configured label. The
  resolved registry model MUST have `low_latency=True`, else `ValueError`
  at construction naming the offending model. The label result feeds
  `AgentGraph.add_conditional_edge(route_fn)` (card 37).
- `GuardrailNode(policy: GuardrailPolicy, config: GuardrailConfig)` -
  `GuardrailPolicy` frozen dataclass: `blocked_terms: Sequence[str]`,
  `fallback_text: str`. `config.position: Literal["pre", "post"]` - pre
  checks user text, post checks the assistant draft. Result:
  `GuardrailVerdict(blocked: bool, matched: str | None,
  replacement: str | None)`; a post violation replaces the draft with
  `fallback_text` (the policy fallback).
- `DisclosureNode(config: DisclosureConfig)` - config: `template: str`,
  `once_per_call: bool = True`. Legal AI/recording announcement: emits the
  rendered template as one `TtsSpeak`; with `once_per_call` it sets
  `ConversationState.agent_state["disclosed"]` and is a no-op afterwards.

### Action/speech family - `src/lucy/nodes/action.py` (new)

- `LlmNode(llm: LlmProvider, registry, provider, model, clock: Clock,
  budgets: LatencyBudgets, config: LlmNodeConfig)` - the per-turn
  generation node: streams via `stream_chat`, flushes clauses through
  `SentenceAssembler`/`TtsPlanner` (card 33, reused), emits each clause as
  a `TtsSpeak` through `ctx.emit`. Result: the full assistant text.
- `McpToolNode(tool: ToolDef, executor: McpToolExecutor, arguments:
  Callable[[ConversationState], dict], config: McpToolNodeConfig)` -
  `ToolDef` as a first-class node: executes through `McpToolExecutor`
  (permissions, schema, audit stay inside `McpClient`, card 34); the
  `ToolResult` is appended to `ConversationState.tool_results` and is the
  node result. `ok=False` is data the graph routes on, not an exception.
- `SayNode(template: str, config: SayNodeConfig)` - template speech
  rendered from `ConversationState.slots` via `str.format`; emits exactly
  one `TtsSpeak`, zero LLM latency. A missing slot key raises `KeyError`
  (fail loud, no silent blanks).
- `HandoffNode(target: CompiledAgentGraph, config: HandoffConfig)` - hands
  control to another `AgentGraph` preserving `ConversationState`
  (multi-agent squads): calls `target.invoke_turn` with the live state;
  the target's result is the node result.

### Telephony family - `src/lucy/nodes/telephony.py` (new)

Telephony nodes speak the card 32 control-channel schema. Three messages
are missing there and are added ADDITIVELY to
`src/lucy/transport/schema.py` (same `Envelope`, `extra="forbid"`,
registered in `parse_event`; zero changes to existing models):

- Downstream `Dial(target: str, caller_id: str, timeout_ms: int)`.
- Downstream `Hold(state: Literal["hold", "resume"], music: bool = False)`.
- Upstream `AmdResult(outcome: Literal["human", "machine", "unknown"],
  confidence: float)`.

Nodes:

- `TransferNode(config: TransferConfig)` - config: `target: str`,
  `mode: Literal["blind", "attended"]`,
  `announce_template: str | None = None`. Emits `Transfer(target)`
  (blind/attended via the transfer directive); attended first speaks the
  rendered announce template as one `TtsSpeak`.
- `DtmfMenuNode(config: DtmfMenuConfig)` - hybrid IVR: config:
  `prompt_template: str`, `options: Mapping[str, str]` (digit -> label),
  `timeout_ms: int`, `max_retries: int`. Speaks the prompt (`TtsSpeak`),
  consumes upstream `Dtmf` events, returns the matched option label.
  Unknown digit or clock-driven timeout re-prompts up to `max_retries`,
  then returns the module-level named constant `DTMF_TIMEOUT`.
- `VoicemailDetectNode(config: VoicemailDetectConfig)` - AMD outcome
  routing: consumes upstream `AmdResult`, result is the outcome literal
  feeding conditional edges (human -> conversation, machine -> voicemail
  script or `EndCallNode`).
- `DialNode(config: DialConfig)` - outbound originate: config: `target:
  str`, `caller_id: str`, `timeout_ms: int`. Emits `Dial`, awaits the
  scripted answer outcome from the transport, result
  `Literal["answered", "no_answer"]` (timeout via the injected clock).
- `HoldNode(config: HoldConfig)` - config: `hold_ms: int`,
  `music: bool = False`. Emits `Hold(state="hold")`, waits `hold_ms` on
  the injected clock, emits `Hold(state="resume")`.
- `EndCallNode(config: EndCallConfig)` - `EndReason(str, Enum)`:
  `COMPLETED`, `BOOKED`, `ESCALATED`, `VOICEMAIL`, `FAILED`, `POLICY`.
  Emits `SessionEnd(reason=<EndReason value>)` (typed reason).

`src/lucy/transport/dev_gateway.py` (additive): `LocalGatewaySimulator`
gains scripted `Dtmf` and `AmdResult` steps and records every received
downstream directive, in order, in `simulator.directives` so tests assert
exact control-channel output. Existing behavior unchanged.

### Post-call family - `src/lucy/nodes/postcall.py` (new)

Async, OFF the latency path: these run in a dedicated post-call graph
invoked once after `SessionEnded`, never inside a live turn.

- `SummaryNode(llm, registry, provider, model, config: SummaryConfig)` -
  summarizes the final transcript into
  `ConversationState.agent_state["summary"]`.
- `CrmSyncNode(tool: ToolDef, executor: McpToolExecutor, config:
  CrmSyncConfig)` - builds the `CrmMetricEvent.crm_ready_payload` dict
  from state and pushes it via MCP (audited through `McpClient`); the
  `ToolResult` lands in `ConversationState.tool_results`.
- `DispositionNode(config: DispositionConfig)` - config:
  `mapping: Mapping[FunnelStage, str]`, `default: str`. Maps the final
  `funnel_stage` to a disposition code; unmapped stages get `default`.
- `EvalHookNode(config: EvalHookConfig)` - when a `SyntheticCallScenario`
  is attached in `ConversationState.agent_state["scenario"]`, builds
  `EvalEvidence` from state and emits the `score_synthetic_call` result
  through `ctx.emit`; no scenario -> no-op result `None`.

### Prebuilt graphs - `src/lucy/prebuilt/__init__.py` (new)

Factories return `CompiledAgentGraph` (card 37). Every dependency is
injected (llm, registry, provider, model, rag index, tools, executor,
clock, budgets, plus a typed `*Config`); no provider, model, URL, or
threshold literal lives inside a factory. Each prebuilt graph ships a
harness scenario proving it end to end:

- `booking_agent(...) -> CompiledAgentGraph` - disclosure ->
  context_synthesis -> slot_filler (day/time) -> intent_router ->
  llm | say -> funnel_classifier; MUST pass all six
  `default_sales_booking_scenarios()` through `ConversationHarness`.
- `lead_qualifier(...) -> CompiledAgentGraph` - slot filler over
  config-listed qualification fields -> sentiment -> funnel classifier ->
  end_call with disposition.
- `receptionist(...) -> CompiledAgentGraph` - disclosure -> intent router
  -> `TransferNode` per a configured department map, with a
  `DtmfMenuNode` fallback route when intent confidence is below the
  configured floor.
- `survey_agent(...) -> CompiledAgentGraph` - `SayNode` question sequence
  from config -> slot filler per answer -> post-call summary and
  disposition.

### File scope

- Create: `src/lucy/nodes/{__init__.py,base.py,perception.py,decision.py,
  action.py,telephony.py,postcall.py}`, `src/lucy/prebuilt/__init__.py`,
  `tests/test_nodes_base.py`, `tests/test_nodes_perception.py`,
  `tests/test_nodes_decision.py`, `tests/test_nodes_action.py`,
  `tests/test_nodes_telephony.py`, `tests/test_nodes_postcall.py`,
  `tests/test_prebuilt_graphs.py`.
- Modify (additive only): `src/lucy/transport/schema.py`,
  `src/lucy/transport/dev_gateway.py`. Nothing else is touched.

## Chips

- [ ] **C1 - Node base contract.** Write `tests/test_nodes_base.py` first:
  `test_node_config_rejects_unknown_fields`,
  `test_as_graph_node_maps_deadline_retries_and_fallback`,
  `test_node_deadline_timeout_triggers_fallback_under_executor` (handler
  awaiting a never-set `asyncio.Event` plus a few-ms `deadline_ms`, run
  through a real `GraphExecutor`; fallback result lands in
  `context.results`). Implement `src/lucy/nodes/{__init__.py,base.py}`.
  Verify: `.venv/bin/python -m pytest tests/test_nodes_base.py -q` -> all
  pass (>=3 tests).
- [ ] **C2 - Perception family.** Tests first in
  `tests/test_nodes_perception.py`:
  `test_context_synthesis_falls_back_to_retrieval_only_on_deadline`,
  `test_slot_filler_merges_slots_and_speaks_confirmation`,
  `test_sentiment_node_stores_score_from_resolved_model`,
  `test_funnel_classifier_emits_existing_funnel_event_model`,
  `test_language_detect_emits_session_configure_on_locale_change`.
  Implement `src/lucy/nodes/perception.py` (drive LLM-backed nodes with
  `LocalLlmSimulator`; RAG with `InMemoryRagIndex`). Verify:
  `.venv/bin/python -m pytest tests/test_nodes_perception.py -q` -> all
  pass (>=5 tests).
- [ ] **C3 - Decision family.** Tests first in
  `tests/test_nodes_decision.py`:
  `test_intent_router_rejects_model_without_low_latency_flag`,
  `test_intent_router_label_drives_conditional_edge`,
  `test_guardrail_post_replaces_draft_with_fallback_text`,
  `test_disclosure_speaks_once_per_call`. Implement
  `src/lucy/nodes/decision.py`. Verify:
  `.venv/bin/python -m pytest tests/test_nodes_decision.py -q` -> all pass
  (>=4 tests).
- [ ] **C4 - Action family.** Tests first in `tests/test_nodes_action.py`:
  `test_llm_node_streams_clauses_into_tts_speak_directives`,
  `test_mcp_tool_node_appends_audited_result_to_state` (assert one
  `allowed=True` `McpAuditEvent` in `client.audit_log`),
  `test_say_node_renders_slots_with_zero_llm_calls`
  (`KeyError` on a missing slot asserted too),
  `test_handoff_preserves_conversation_state_across_graphs`. Implement
  `src/lucy/nodes/action.py`. Verify:
  `.venv/bin/python -m pytest tests/test_nodes_action.py -q` -> all pass
  (>=4 tests).
- [ ] **C5 - Additive telephony schema messages.** Tests first in
  `tests/test_nodes_telephony.py`:
  `test_dial_hold_amd_round_trip_through_parse_event`,
  `test_unknown_type_still_rejected_after_additions`. Add `Dial`, `Hold`,
  `AmdResult` to `src/lucy/transport/schema.py` (additive only). Verify:
  `.venv/bin/python -m pytest tests/test_nodes_telephony.py
  tests/test_transport_schema.py -q` -> all pass; card 32 schema tests
  untouched and green.
- [ ] **C6 - Telephony node family.** Tests first in
  `tests/test_nodes_telephony.py`:
  `test_transfer_node_emits_transfer_directive`,
  `test_dtmf_menu_routes_digit_and_returns_timeout_after_retries`
  (`ManualClock`, no real sleeps),
  `test_voicemail_detect_routes_amd_outcome`,
  `test_dial_node_emits_dial_and_reports_answer_outcome`,
  `test_hold_node_emits_hold_then_resume`,
  `test_end_call_emits_session_end_with_typed_reason`. Implement
  `src/lucy/nodes/telephony.py`; extend
  `src/lucy/transport/dev_gateway.py` additively (scripted `Dtmf`/
  `AmdResult` steps, `simulator.directives` capture). Verify:
  `.venv/bin/python -m pytest tests/test_nodes_telephony.py -q` -> all
  pass (>=8 tests).
- [ ] **C7 - Post-call family.** Tests first in
  `tests/test_nodes_postcall.py`:
  `test_summary_node_writes_summary_after_session_end`,
  `test_crm_sync_pushes_crm_ready_payload_via_mcp_audited`,
  `test_disposition_maps_funnel_stage_to_configured_code`,
  `test_eval_hook_scores_attached_scenario`. Implement
  `src/lucy/nodes/postcall.py`; assert the post-call graph runs only
  after `SessionEnded`, never inside a turn. Verify:
  `.venv/bin/python -m pytest tests/test_nodes_postcall.py -q` -> all
  pass (>=4 tests).
- [ ] **C8 - Prebuilt booking_agent.** Test first in
  `tests/test_prebuilt_graphs.py`:
  `test_booking_agent_passes_all_six_golden_scenarios` - iterate
  `default_sales_booking_scenarios()` through `ConversationHarness` with
  a scripted `LocalLlmSimulator` per scenario; every
  `score_synthetic_call` result has `passed=True`. Implement
  `booking_agent()` in `src/lucy/prebuilt/__init__.py`. Verify:
  `.venv/bin/python -m pytest
  tests/test_prebuilt_graphs.py::test_booking_agent_passes_all_six_golden_scenarios
  -q` -> passes.
- [ ] **C9 - Prebuilt lead_qualifier.** Test first in
  `tests/test_prebuilt_graphs.py`:
  `test_lead_qualifier_fills_slots_and_ends_with_disposition` (harness
  scenario: qualification slots filled, `FunnelEvent` emitted, one
  `SessionEnd` directive with a typed reason). Implement
  `lead_qualifier()`. Verify:
  `.venv/bin/python -m pytest tests/test_prebuilt_graphs.py -q` -> all
  pass.
- [ ] **C10 - Prebuilt receptionist.** Tests first in
  `tests/test_prebuilt_graphs.py`:
  `test_receptionist_routes_intent_to_transfer_directive`,
  `test_receptionist_falls_back_to_dtmf_menu_on_low_confidence`.
  Implement `receptionist()`. Verify:
  `.venv/bin/python -m pytest tests/test_prebuilt_graphs.py -q` -> all
  pass.
- [ ] **C11 - Prebuilt survey_agent.** Test first in
  `tests/test_prebuilt_graphs.py`:
  `test_survey_agent_collects_answers_and_writes_summary` (configured
  question list spoken in order via `SayNode`, answers land in slots,
  post-call summary present). Implement `survey_agent()`. Verify:
  `.venv/bin/python -m pytest tests/test_prebuilt_graphs.py -q` -> all
  pass (>=5 tests).
- [ ] **C12 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); `LocalLlmSimulator`,
  `LocalGatewaySimulator`, `LocalMcpCommandTransport`, `InMemoryRagIndex`,
  and `ManualClock` are the sanctioned doubles - real implementations of
  real contracts. No `unittest.mock`, no monkeypatched providers.
- Do not hardcode provider names, model names, locales, thresholds,
  timeouts, retry counts, disposition codes, or announcement/menu/say text
  in node code (agents.md): numbers live in typed `NodeConfig` subclasses,
  models resolve through `ModelRegistry`, text comes from config
  templates, and the only literals are named module constants
  (`DTMF_TIMEOUT`).
- Do not modify any existing card 32 schema model, `LatencyBudgets` field,
  or card 33/34/37 contract; `schema.py` and `dev_gateway.py` changes are
  ADDITIVE only, everything else is reused unchanged.
- Do not call `transport.call_tool` or `McpClient` directly from nodes;
  `McpToolNode` and `CrmSyncNode` go through `McpToolExecutor` so
  permissions, schema validation, and audit stay intact (card 34).
- Do not carry audio bytes in any control-channel message (ADR 0004), and
  do not implement real telephony transports here - SIP/CPaaS adapters are
  S5 work (cards 40-45); this card only emits directives against the
  simulator.
- Do not run post-call nodes on the per-turn latency path; they execute
  once after `SessionEnded`.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): no trace
  ingest, storage, dashboard code, or Pili-specific tool catalogs; CRM
  sync is an MCP call, telemetry leaves only through `ctx.emit` /
  `lucy.observe`.
- Do not touch files outside the File scope list above.
- Do not check a chip or Definition of Done box without running its Verify
  command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_nodes_base.py
      tests/test_nodes_perception.py tests/test_nodes_decision.py
      tests/test_nodes_action.py tests/test_nodes_telephony.py
      tests/test_nodes_postcall.py -q` -> all pass; every node family
      verified under the executor with deadline + fallback
      (`test_node_deadline_timeout_triggers_fallback_under_executor`).
- [ ] `.venv/bin/python -m pytest tests/test_prebuilt_graphs.py -q` -> all
      pass, including
      `test_booking_agent_passes_all_six_golden_scenarios`.
- [ ] `.venv/bin/python -m pytest tests/test_nodes_telephony.py
      tests/test_transport_schema.py -q` -> all pass (telephony nodes emit
      the correct control-channel directives; card 32 schema models
      unchanged).
- [ ] `grep -rn "unittest.mock\|MagicMock\|mocker" tests/test_nodes_base.py
      tests/test_nodes_perception.py tests/test_nodes_decision.py
      tests/test_nodes_action.py tests/test_nodes_telephony.py
      tests/test_nodes_postcall.py tests/test_prebuilt_graphs.py` -> no
      matches.
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon is
      available).
- [ ] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
