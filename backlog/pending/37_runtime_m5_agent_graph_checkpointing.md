# 37 - Voice runtime M5: AgentGraph and checkpointing

**Sprint:** S4 - Graph and state
**Epic:** Voice runtime
**Estimated effort:** ~10 h
**Depends on:** 36
**State:** pending

## Goal

The LangGraph-parity release: user-authored cognition graphs with conditional
edges and checkpoints, compiled onto the existing executor (ADR 0011 cognition
plane). Conversation state survives a kill mid-call and replays
deterministically post-call. Card 39's prebuilt `booking_agent()` and the S4
exit demo build directly on the engine this card ships.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  thresholds or limits).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the decision this card
  implements: the per-turn cognition plane is a user-authored `AgentGraph`
  whose supersteps compile to the existing `GraphExecutor`, with conversation
  state checkpointed per superstep behind a `CheckpointStore` protocol.
- `docs/adr/0007-on-the-fly-context-synthesis.md` - the context-synthesis
  node: deadline-first, falls back to retrieval-only context, reuses
  `SpeculativeRagNode`; it is the first node of the default graph.
- `docs/adr/0003-no-mocks-testing-policy.md` - simulators and in-memory
  implementations are the only sanctioned test doubles.
- `docs/adr/0010-open-core-split.md` - everything here is open SDK code;
  telemetry leaves only through the `lucy.observe` seam.
- `src/lucy/runtime.py` - `GraphExecutor`, `GraphContext`, `GraphNode`,
  `GraphExecutionError`, `NodeHandler`. The superstep engine. This card makes
  ADDITIVE changes only: `TurnContext` and an optional `context=` keyword on
  `run`. Deadlines, retries, fallbacks, and cancellation propagation are
  reused unchanged.
- `src/lucy/rag.py` - `SpeculativeRagNode.prefetch` (deadline-bounded,
  returns `RagResult` with `deadline_exceeded=True` instead of raising) and
  `RagResult.prompt_context` with `grounding_id` citations; the synthesis
  node wraps these, reused unchanged.
- `src/lucy/specs.py` - `FunnelStage` enum; `ConversationState.funnel_stage`
  uses it, never string literals.
- `src/lucy/metrics.py` - `LatencyWaterfall` (`rag_ms` gates synthesis cost
  per ADR 0007) and `FunnelEvent`; nothing here is redefined.
- `src/lucy/evals.py` - `booking_happy_path()`, `SyntheticCallScenario`; the
  harness scenarios the graph-driven calls run against.
- `src/lucy/clock.py` (built in card 32) - `Clock` Protocol, `MonotonicClock`,
  `ManualClock`; `TurnContext.clock` and all checkpoint timestamps use it.
- `src/lucy/settings.py` (built in card 32) - `LatencyBudgets` house style
  for typed settings; this card adds `GraphLimits` beside it.
- `src/lucy/transport/schema.py` (built in card 32) - `Envelope`, `SttFinal`,
  `TtsSpeak`; replay consumes recorded envelopes, the default graph emits
  `TtsSpeak` directives.
- `src/lucy/drivers.py` (built in cards 33/34) - `TurnDriver` Protocol,
  `DriverEvent`, `TurnDriverReport`, `CascadedTurnDriver`; the new
  `GraphTurnDriver` implements the same Protocol and wraps an inner driver.
- `src/lucy/session.py` / `src/lucy/harness.py` (built in cards 32/33) -
  `VoiceSession`, `ConversationHarness`, `HarnessResult`; the harness gains
  `replay(...)` here.
- `backlog/pending/36_runtime_m4_speculation_and_budgets.md` - the dependency
  card; its `SpeculationController` sets `TurnContext.speculative=True` on
  speculative runs. If card 36 landed a provisional `speculative` flag on a
  context object, fold it into the `TurnContext` defined here - one flag,
  never two.
- `tests/test_runtime.py` - house test style (plain pytest, asyncio_mode
  auto, no mocks) AND the regression guard: it must pass unmodified.

## Spec

### Additive runtime changes - `src/lucy/runtime.py`

- `TurnContext(GraphContext)` dataclass, every new field with a default so
  dataclass inheritance stays valid:
  `session_id: str = ""`, `turn_id: str = ""`,
  `emit: Callable[[Any], None] | None = None`,
  `cancellation: asyncio.Event = field(default_factory=asyncio.Event)`,
  `clock: Clock = field(default_factory=MonotonicClock)`,
  `speculative: bool = False`.
- `GraphExecutor.run` gains a keyword-only parameter
  `context: GraphContext | None = None`. When `None`, behavior is
  byte-for-byte today's (build a fresh `GraphContext(payload=payload)`).
  When provided, `payload` is merged into `context.payload` via dict
  `update` and execution runs on the given context (results and trace
  accumulate on it), which is also the return value. No other line of
  `GraphExecutor` changes; `tests/test_runtime.py` passes unmodified.

### Conversation state and checkpoints - `src/lucy/state.py` (new)

- `TranscriptLine(BaseModel)`: `speaker: Literal["caller", "agent"]`,
  `text: str`.
- `ConversationState(BaseModel)`: `transcript: list[TranscriptLine] = []`,
  `funnel_stage: FunnelStage | None = None`, `slots: dict[str, str] = {}`,
  `tool_results: list[dict] = []` (serialized `ToolResult` payloads from
  card 34), `turns: int = 0`, `agent_state: dict[str, Any] = {}` (the user
  extension bag; the default graph stores `prompt_context`,
  `grounding_ids`, `rag_deadline_exceeded`, and `last_turn_report` here).
  Method `merged(update: dict[str, Any]) -> ConversationState`: returns a
  NEW instance with replace-per-key semantics (a node that appends to
  `transcript` returns the full new list); an unknown key raises
  `StateKeyError(KeyError)`. JSON round-trip
  (`model_dump_json`/`model_validate_json`) preserves equality.
- `Checkpoint(BaseModel)`: `checkpoint_id: str` - deterministic, exactly
  `"<session_id>:<turn_id>:<superstep>"` - `session_id: str`,
  `turn_id: str`, `superstep: int`,
  `kind: Literal["superstep", "turn_final"]`, `state: ConversationState`,
  `created_at_ms: int` (from the injected `Clock`, NEVER wall time - replay
  determinism).
- `CheckpointStore` Protocol: `async save(checkpoint: Checkpoint) -> None`,
  `async load_latest(session_id: str) -> Checkpoint | None`,
  `async history(session_id: str) -> list[Checkpoint]` (save order,
  oldest first).
- `InMemoryCheckpointStore` implementing the Protocol; `save` and reads
  deep-copy state so later mutation cannot corrupt history.
- Cadence rule: checkpoint per superstep and per turn finalize - NEVER per
  token and never per `TtsSpeak` directive.

### Graph limits - `src/lucy/settings.py` (extend; rename nothing)

- `GraphLimits(BaseSettings)`: `max_supersteps_per_turn: int = 16`, env
  prefix `LUCY_GRAPH_` (so `LUCY_GRAPH_MAX_SUPERSTEPS_PER_TURN` overrides).
  The cycle cap exists nowhere else as a literal.

### AgentGraph - `src/lucy/graph.py` (new)

- `END = "__end__"` module-level sentinel;
  `StateT = TypeVar("StateT", bound=ConversationState)`;
  `AgentNodeHandler = Callable[[StateT, TurnContext],
  Awaitable[dict[str, Any]]]` (returns a partial state update);
  `RouteFn = Callable[[StateT], str]` (returns a node name or `END`).
- Errors: `GraphValidationError(ValueError)` (build/compile time),
  `GraphRouteError(GraphExecutionError)` (route to unknown node),
  `GraphCycleLimitExceeded(GraphExecutionError)` (superstep cap hit).
- `AgentGraph(Generic[StateT])`, every builder method returns `self`:
  - `add_node(name: str, handler: AgentNodeHandler, *,
    deadline_ms: int = 500, retries: int = 0,
    fallback: AgentNodeHandler | None = None)` - duplicate name raises
    `GraphValidationError`; the three keyword params map 1:1 onto
    `GraphNode` fields.
  - `add_edge(source: str, target: str)` - `target` may be `END`; multiple
    unconditional edges from one source fan out (next frontier gets all
    targets, declaration order, deduped).
  - `add_conditional_edge(source: str, route_fn: RouteFn)` - at most one
    per source; a source with both unconditional edges and a conditional
    edge raises `GraphValidationError` at compile.
  - `set_entry(name: str)`.
  - `compile(checkpointer: CheckpointStore | None = None,
    limits: GraphLimits | None = None) -> CompiledAgentGraph[StateT]` -
    validates: entry is set and known, every edge endpoint is a known node
    or `END`; `limits` defaults to `GraphLimits()`. The checkpointer is
    exposed read-only as `CompiledAgentGraph.checkpointer`.
- `CompiledAgentGraph.invoke_turn(state: StateT, ctx: TurnContext) ->
  StateT` superstep loop, exact semantics:
  1. Frontier starts as `[entry]`; superstep counter starts at 1.
  2. Each superstep wraps the frontier nodes as `GraphNode`s whose handlers
     close over the current state snapshot and `ctx`, then executes them in
     ONE `GraphExecutor([...]).run({}, context=ctx)` call - deadlines,
     retries, fallbacks, and cancellation are the executor's, unchanged.
  3. Partial updates are read from `ctx.results[<frontier name>]` and
     merged with `state.merged(...)` in `add_node` declaration order;
     last write wins on key collisions (deterministic).
  4. If a checkpointer is configured, save one `kind="superstep"`
     checkpoint (id `"<session>:<turn>:<superstep>"`, time from
     `ctx.clock`). A turn with N supersteps writes exactly N of these.
  5. Next frontier: unconditional edge targets plus, per source with a
     conditional edge, `route_fn(merged_state)`; `END` contributes nothing;
     a node with no outgoing edges contributes nothing; an unknown route
     result raises `GraphRouteError`. Dedupe preserving order.
  6. Empty frontier ends the turn; the merged state is returned.
  7. Exceeding `limits.max_supersteps_per_turn` raises
     `GraphCycleLimitExceeded`.
  8. `asyncio.CancelledError` (barge-in) propagates; no partial merge is
     adopted - observable state is whatever the last saved checkpoint
     holds.
- `default_agent_graph(inner: TurnDriver,
  rag: SpeculativeRagNode | None = None,
  classify: Callable[[ConversationState], FunnelStage | None] | None =
  None) -> AgentGraph[ConversationState]` - the three-node default
  (`context_synthesis -> llm -> finalize_funnel`, linear edges) so simple
  agents author nothing:
  - `context_synthesis` (ADR 0007, first node): reads the caller line from
    `ctx.payload["user_text"]`, awaits `rag.prefetch(user_text)` when
    `rag` is set; update writes `agent_state["prompt_context"]`
    (`RagResult.prompt_context`), `agent_state["grounding_ids"]` (chunk
    `grounding_id`s, preserved end to end), and
    `agent_state["rag_deadline_exceeded"]`. Deadline handling is
    DELEGATED to `SpeculativeRagNode.prefetch` (it returns
    `deadline_exceeded=True` rather than raising); the node's `GraphNode`
    fallback returns the retrieval-only/empty-context update, so synthesis
    never blocks the turn. With `rag=None` it returns an empty-context
    update.
  - `llm`: consumes `inner.run_turn(user_text, history)` where history is
    rebuilt from `state.transcript`; forwards each `TtsSpeak` through
    `ctx.emit` AS IT ARRIVES (first clause speaks while the graph still
    runs); update appends the agent `TranscriptLine` and stores the
    terminal report as a dict under `agent_state["last_turn_report"]`
    (keys `assistant_text`, `llm_ms`, `llm_cost`).
  - `finalize_funnel`: update sets `turns` to `state.turns + 1` and
    `funnel_stage` to `classify(state)` when `classify` is given,
    otherwise leaves the stage unchanged (identity default; the richer
    classifier node is card 39's, not this card's).

### Graph turn driver - `src/lucy/drivers.py` (extend)

- `GraphTurnDriver(graph: CompiledAgentGraph[ConversationState], *,
  session_id: str, clock: Clock,
  state: ConversationState | None = None)` - implements the card 33
  `TurnDriver` Protocol unchanged; current state exposed as `.state`.
  `run_turn(user_text, history)` semantics, in order:
  1. `turn_id = f"turn-{state.turns + 1}"` - deterministic, no uuid.
  2. Append the caller `TranscriptLine` via `state.merged(...)`.
  3. Build `TurnContext(payload={"user_text": user_text},
     session_id=..., turn_id=..., clock=...,
     emit=<internal queue put>)` and start `graph.invoke_turn(state, ctx)`
     as one asyncio task.
  4. Yield each `TtsSpeak` from the queue as it arrives, then exactly one
     terminal `TurnDriverReport` built from
     `agent_state["last_turn_report"]` of the returned state, which the
     driver adopts as `.state`.
  5. After adoption, save one `kind="turn_final"` checkpoint through
     `graph.checkpointer` when it is configured.
  6. On cancellation (barge-in) the graph task is cancelled and `.state`
     stays at the last checkpointed value; no partial adoption.
- `GraphTurnDriver.resume(graph, *, session_id: str,
  store: CheckpointStore, clock: Clock) -> GraphTurnDriver` (async
  classmethod): seeds `.state` from `(await
  store.load_latest(session_id)).state`, or a fresh `ConversationState()`
  when no checkpoint exists.

### Replay - `src/lucy/harness.py` (extend)

- `HarnessResult` gains additive defaulted fields
  `events: list[Envelope] = []` (upstream envelopes recorded during the
  run) and `checkpoints: list[Checkpoint] = []` (copied from the store
  when the driver is a `GraphTurnDriver` with a checkpointer).
- `ReplayResult` (frozen dataclass): `final_state: ConversationState`,
  `matches_final_checkpoint: bool`,
  `diverged_at_checkpoint_id: str | None`.
- `ConversationHarness.replay(checkpoints: Sequence[Checkpoint],
  recorded_events: Sequence[Envelope]) -> ReplayResult` - deterministic
  post-call verification, no LLM call:
  1. Validates checkpoint ordering (same session; `turn_id`/`superstep`
     monotonic in save order) - violation raises `GraphValidationError`.
  2. Extracts caller lines from the `SttFinal` payloads in
     `recorded_events`, in order.
  3. Folds the `kind="turn_final"` checkpoints and cross-checks each one's
     transcript caller lines against the recorded lines so far.
  4. Returns `matches_final_checkpoint=True` and
     `diverged_at_checkpoint_id=None` when the fold reproduces the last
     checkpoint's state exactly; otherwise `False` plus the first
     diverging checkpoint id.

### File scope

- Create: `src/lucy/graph.py`, `src/lucy/state.py`,
  `tests/test_agent_graph.py`, `tests/test_checkpointing.py`.
- Modify: `src/lucy/runtime.py` (additive only), `src/lucy/drivers.py`,
  `src/lucy/harness.py`, `src/lucy/settings.py` (add `GraphLimits`).
- Nothing else is touched.

## Chips

- [ ] **C1 - TurnContext, additive executor seam.** Write
  `tests/test_agent_graph.py` first:
  `test_turn_context_is_additive_over_graph_context` (constructs
  `TurnContext(payload={})`; all new fields default; isinstance of
  `GraphContext`) and `test_graph_executor_runs_with_injected_turn_context`
  (a two-node `GraphExecutor` run with `context=TurnContext(...)`
  accumulates results and trace on the injected context). Implement
  `TurnContext` and the `context=` keyword in `src/lucy/runtime.py`.
  Verify: `.venv/bin/python -m pytest tests/test_agent_graph.py
  tests/test_runtime.py -q` -> all pass; `tests/test_runtime.py`
  unmodified.
- [ ] **C2 - ConversationState and merge semantics.** Write
  `tests/test_checkpointing.py` first:
  `test_state_merged_replaces_only_named_keys`,
  `test_state_merged_rejects_unknown_key` (raises `StateKeyError`),
  `test_state_json_round_trip_preserves_equality`. Implement
  `TranscriptLine`, `ConversationState`, `StateKeyError` in
  `src/lucy/state.py`. Verify:
  `.venv/bin/python -m pytest tests/test_checkpointing.py -q` -> all pass
  (>=3 tests).
- [ ] **C3 - Checkpoint store.** Tests first in
  `tests/test_checkpointing.py`: `test_checkpoint_id_is_deterministic`
  (exactly `"<session>:<turn>:<superstep>"`),
  `test_load_latest_returns_newest_checkpoint`,
  `test_history_returns_copies_in_save_order` (mutating a returned state
  does not corrupt the store). Implement `Checkpoint`, `CheckpointStore`,
  `InMemoryCheckpointStore` in `src/lucy/state.py`. Verify:
  `.venv/bin/python -m pytest tests/test_checkpointing.py -q` -> all pass.
- [ ] **C4 - AgentGraph builder and compile validation.** Tests first in
  `tests/test_agent_graph.py`: `test_duplicate_node_name_raises`,
  `test_compile_requires_known_entry`,
  `test_compile_rejects_edge_to_unknown_node`,
  `test_mixed_conditional_and_static_edges_on_one_source_raise`.
  Implement `AgentGraph`, `END`, the error types in `src/lucy/graph.py`,
  and `GraphLimits` in `src/lucy/settings.py` (env override test:
  `LUCY_GRAPH_MAX_SUPERSTEPS_PER_TURN=3`). Verify:
  `.venv/bin/python -m pytest tests/test_agent_graph.py -q` -> all pass.
- [ ] **C5 - invoke_turn supersteps on GraphExecutor.** Tests first in
  `tests/test_agent_graph.py`:
  `test_linear_graph_executes_supersteps_via_graph_executor` (TraceEvents
  appear on `ctx.trace` for every node - proof the executor ran them),
  `test_node_fallback_and_deadline_reused_from_executor` (a node whose
  handler exceeds its `deadline_ms` lands on its fallback update, status
  `"fallback"` in trace), `test_conditional_edge_routes_on_state`,
  `test_route_to_unknown_node_raises_graph_route_error`,
  `test_cycle_capped_by_typed_graph_limits` (looping graph +
  `GraphLimits(max_supersteps_per_turn=3)` ->
  `GraphCycleLimitExceeded`),
  `test_checkpoint_written_per_superstep_never_per_token` (N supersteps ->
  exactly N `kind="superstep"` checkpoints in the store). Implement
  `CompiledAgentGraph.invoke_turn`. Verify:
  `.venv/bin/python -m pytest tests/test_agent_graph.py -q` -> all pass.
- [ ] **C6 - Default three-node graph (ADR 0007 first node).** Tests first
  in `tests/test_agent_graph.py`:
  `test_default_graph_runs_synthesis_then_llm_then_finalize` (trace order;
  `agent_state["prompt_context"]` filled from an `InMemoryRagIndex`-backed
  `SpeculativeRagNode`; grounding ids preserved),
  `test_synthesis_deadline_falls_back_to_retrieval_only` (a slow index
  wrapper in the test makes `prefetch` return `deadline_exceeded=True`;
  the turn still completes),
  `test_finalize_increments_turns_and_applies_classify`. Implement
  `default_agent_graph` in `src/lucy/graph.py` with `LocalLlmSimulator`
  behind a `CascadedTurnDriver` as `inner`. Verify:
  `.venv/bin/python -m pytest tests/test_agent_graph.py -q` -> all pass.
- [ ] **C7 - GraphTurnDriver, kill/resume mid-call.** Tests first in
  `tests/test_checkpointing.py`:
  `test_custom_graph_conditional_edge_routes_real_harness_call` (a custom
  graph - default nodes plus a conditional edge off `context_synthesis` -
  routes `booking_happy_path()` through the real
  `ConversationHarness`/`VoiceSession`; assert the route taken via trace
  and a transcript matching the scenario) and
  `test_kill_mid_call_resume_from_load_latest_continues` (cancel the run
  after turn 1, build a new driver via `GraphTurnDriver.resume(...,
  store=...)`, run the remaining caller lines; final transcript contains
  both halves, `turns` is continuous). Implement `GraphTurnDriver` in
  `src/lucy/drivers.py`; record `events`/`checkpoints` additively in
  `src/lucy/harness.py`. Verify:
  `.venv/bin/python -m pytest tests/test_checkpointing.py -q` -> all pass.
- [ ] **C8 - Post-call replay.** Tests first in
  `tests/test_checkpointing.py`:
  `test_replay_reproduces_identical_final_state` (run a scenario, feed
  `HarnessResult.checkpoints` + `HarnessResult.events` to `replay`,
  `matches_final_checkpoint=True`, `final_state` equals the live final
  state) and `test_replay_flags_divergence_on_tampered_events` (alter one
  recorded `SttFinal` text -> `matches_final_checkpoint=False` and the
  diverging checkpoint id is reported). Implement `ReplayResult` and
  `ConversationHarness.replay` in `src/lucy/harness.py`. Verify:
  `.venv/bin/python -m pytest tests/test_checkpointing.py -q` -> all pass.
- [ ] **C9 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003);
  `InMemoryCheckpointStore`, `LocalLlmSimulator`, `ManualClock`,
  `InMemoryRagIndex`, and the in-test slow-index wrapper are the sanctioned
  doubles - real implementations of real contracts. No `unittest.mock`.
- Do not hardcode limits, thresholds, or stage names (agents.md): the
  superstep cap lives only in `GraphLimits`, funnel stages only via the
  `FunnelStage` enum, and no literal ms values in graph or driver code.
- Do not use `uuid4`, `time.time()`, or any wall-clock source in checkpoint
  ids, `created_at_ms`, or turn ids - determinism is what makes replay and
  resume testable. All time flows through the injected `Clock`.
- Do not modify `GraphExecutor` semantics: `_run_dag`, `_run_node`, retries,
  fallbacks, and cancellation propagation in `src/lucy/runtime.py` stay
  as they are; only `TurnContext` and the optional `context=` keyword are
  added, and `tests/test_runtime.py` must pass without edits.
- Do not checkpoint per token or per `TtsSpeak`; per superstep and turn
  finalize only (ADR 0011).
- Do not build persistent checkpoint adapters (Redis, Postgres, files);
  Protocol + in-memory only, persistence is a later card.
- Do not implement the prebuilt node catalog or `booking_agent()` (card
  39); only the default three-node graph ships here.
- Do not redefine card 32/33/34 contracts: `TurnDriver`, `DriverEvent`,
  `TurnDriverReport`, transport schema models, `TurnRecord`,
  `LatencyBudgets` field names. Extend only.
- Do not touch files outside the File scope list in the Spec.
- Do not check a chip or Definition of Done box without running its Verify
  command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): checkpoints
  and state stay in-process; telemetry leaves only through the
  `lucy.observe` seam; no ingest, storage, or dashboard code here.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_agent_graph.py
      tests/test_checkpointing.py -q` -> all pass (>=18 tests), including
      `test_custom_graph_conditional_edge_routes_real_harness_call`
      (a custom graph with a conditional edge routes a real harness call),
      `test_kill_mid_call_resume_from_load_latest_continues` (kill/resume
      mid-call from `load_latest` continues the conversation), and
      `test_replay_reproduces_identical_final_state` (replay reproduces
      identical state from checkpoints + recorded events).
- [ ] `.venv/bin/python -m pytest tests/test_runtime.py -q` -> all pass
      with the test file unmodified (executor reused, not rewritten).
- [ ] `grep -rn "unittest.mock\|MagicMock\|mocker" tests/test_agent_graph.py
      tests/test_checkpointing.py` -> no matches.
- [ ] `grep -rn "uuid4\|time.time()" src/lucy/graph.py src/lucy/state.py`
      -> no matches (replay determinism).
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
