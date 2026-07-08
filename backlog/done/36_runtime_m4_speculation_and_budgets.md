# 36 - Voice runtime M4: speculation and latency budgets

**Sprint:** S3 - Conversational correctness
**Epic:** Voice runtime
**Estimated effort:** ~8 h
**Depends on:** 35
**State:** done

## Goal

Hide provider latency behind the caller's own speech (ADR 0011): RAG
prefetch and speculative LLM generation triggered by stable partial
transcripts, both config-gated, plus deterministic budget assertions that
keep the <800 ms p50 voice-to-voice target honest. Nothing speculative is
ever externally visible until the final transcript confirms it.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  thresholds or latency budgets outside typed settings).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the decision this card
  implements: speculative mechanisms (RAG prefetch on partials, speculative
  LLM start on stable partials with abort-on-revision, prompt caching) are
  configuration-gated; latency budgets live in typed settings.
- `docs/adr/0007-on-the-fly-context-synthesis.md` - context synthesis is
  speculative and triggered on partial transcripts, reusing
  `SpeculativeRagNode` prefetch; this card wires that trigger.
- `docs/adr/0003-no-mocks-testing-policy.md` - simulators and the injectable
  clock are the sanctioned doubles; no mocking frameworks.
- `docs/adr/0010-open-core-split.md` - everything here is open SDK code;
  telemetry leaves only through the `lucy.observe` seam.
- `src/lucy/rag.py` - `SpeculativeRagNode` (`prefetch`, `_cache`,
  `deadline_ms`), `RagResult` (`cache_hit`, `deadline_exceeded`). Reused
  UNCHANGED; this card only calls `prefetch` from the session.
- `src/lucy/metrics.py` - `LatencyWaterfall` (`rag_ms`, `llm_ms`,
  `tts_ms`); your budget assertions read these fields.
- `tests/test_rag.py` - existing `SpeculativeRagNode` tests: prefetch cache
  hits and the `deadline_ms` timeout pattern. Copy the house style.
- `backlog/pending/32_runtime_m0_walking_skeleton.md` - the card that
  creates `src/lucy/settings.py` (`LatencyBudgets` with the component
  fields `endpoint_silence_ms=150`, `stt_final_ms=60`,
  `control_transport_ms=10`, `graph_dispatch_ms=10`,
  `llm_first_clause_ms=380`, `tts_first_byte_ms=150`,
  `gateway_pacing_ms=30`, `turn_total_ms=800`), `src/lucy/clock.py`
  (`Clock`, `ManualClock`), `src/lucy/session.py` (`VoiceSession`,
  `TurnRecord`), `src/lucy/transport/schema.py` (`SttPartial(text,
  stability, provider)`, `SttFinal`, `TtsSpeak`), and
  `src/lucy/harness.py` (`ConversationHarness`).
- `backlog/pending/33_runtime_m1_streaming_llm.md` - creates
  `src/lucy/llm.py` (`LlmMessage`, `LlmRequest` with the `cache_key`
  prompt-cache hint, `LlmProvider.stream_chat`, `OpenAiCompatibleAdapter`,
  `LocalLlmSimulator` paced via `Clock`) and `src/lucy/drivers.py`
  (`TurnDriver`, `CascadedTurnDriver`).
- `backlog/pending/34_runtime_m2_realtime_tools.md` - tool rounds in
  `CascadedTurnDriver` (`ToolCallReady`, `McpToolExecutor`); this card
  defers those rounds while a run is speculative.
- `backlog/pending/35_runtime_m3_interruption_correctness.md` - the
  dependency card: turn-task cancellation is correct and orphan-free; your
  speculative abort reuses exactly that cancellation path.
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

## Spec

### Speculation settings - `src/lucy/settings.py` (extend)

`SpeculationSettings(BaseSettings)`, env prefix `LUCY_SPECULATION_`,
env-overridable like `LatencyBudgets`:

- `enabled_rag_prefetch: bool = True` - prefetch hits only the local index;
  it costs no provider tokens, so it may default on.
- `enabled_llm_start: bool = False` - speculative LLM generation is OFF by
  default. Reason: an aborted speculative run has already consumed paid
  provider tokens, and callers revise partials routinely, so a default-on
  flag silently spends money on every revision. Only explicit opt-in.
- `rag_prefetch_stability: float = 0.6` - minimum `SttPartial.stability`
  to trigger a RAG prefetch.
- `llm_start_stability: float = 0.9` - minimum stability to start a
  speculative LLM run.

Validation at construction: `0.0 <= rag_prefetch_stability <=
llm_start_stability <= 1.0`, otherwise raise `ValueError`. No new
`LatencyBudgets` fields are expected; if the budget test in C7 reveals a
missing serial component, add it with its ADR 0011 default and record that
under "Improvements noted".

### SpeculationController - `src/lucy/session.py` (extend)

```python
class SpeculativeAction(str, Enum):
    NONE = "none"
    PREFETCH_RAG = "prefetch_rag"
    START_LLM = "start_llm"

class SpeculationController:
    def __init__(self, settings: SpeculationSettings) -> None: ...
    def on_partial(self, text: str, stability: float) -> SpeculativeAction
    def start(self, text: str, task: asyncio.Task) -> None
    async def reconcile(self, final: str) -> bool
    @property
    def speculating(self) -> bool
```

`on_partial` decision table, evaluated in this order (first match wins):

1. `settings.enabled_llm_start` AND `stability >=
   settings.llm_start_stability` AND not `speculating` -> `START_LLM`.
   At most one speculative LLM run per turn.
2. `settings.enabled_rag_prefetch` AND `stability >=
   settings.rag_prefetch_stability` AND this normalized text not already
   prefetched this turn -> `PREFETCH_RAG`. Each distinct text prefetches
   once per turn.
3. Otherwise -> `NONE`.

`start(text, task)` registers the speculative run the session spawned for
`START_LLM`; `text` is the trigger partial, `task` the asyncio task.

`reconcile(final)` resolves speculation when `SttFinal` arrives:

- Normalization for comparison: `" ".join(s.split()).casefold()`.
- If a run is in flight and the normalized trigger text is a non-empty
  prefix of the normalized final: PROMOTE - keep the in-flight task as the
  turn's real run, return `True`.
- Otherwise: ABORT - cancel the task and `await` it (the card 35
  cancellation path; no orphan tasks), return `False`. The caller then
  starts a fresh serial run from the final text.
- Either way, clear the per-turn state: in-flight handle and the
  prefetched-text set.

### Speculative turn context and suppression

`TurnContext` dataclass in `src/lucy/session.py` (create it; if card 35
already introduced one, extend it with exactly these fields):

```python
@dataclass
class TurnContext:
    turn_id: str
    speculative: bool = False
    promoted: asyncio.Event = field(default_factory=asyncio.Event)
    buffered_directives: list[TtsSpeak] = field(default_factory=list)
```

Speculative runs execute with `TurnContext.speculative=True`, which
suppresses every externally visible effect:

- No `tts.speak`: all `TtsSpeak` directives pass through the single
  session send path; while the active context is speculative they are
  appended to `buffered_directives` instead of sent to the transport.
- Tools deferred: in `CascadedTurnDriver`, when the turn context is
  speculative and a `ToolCallReady` arrives, the driver awaits
  `turn_context.promoted.wait()` BEFORE calling the tool executor. Abort
  cancels that await; the tool never runs. The driver entry point gains a
  keyword argument `turn_context: TurnContext | None = None` (also added
  to the `TurnDriver` Protocol); `None` means non-speculative behavior,
  unchanged from card 34.

On promotion the session sets `speculative=False`, sets the `promoted`
event, and flushes `buffered_directives` to the transport in order. On
abort the buffer is discarded with the cancelled task.

### Session wiring - `src/lucy/session.py` (extend)

`VoiceSession` gains keyword-only constructor params
`speculation: SpeculationSettings | None = None` (`None` ->
`SpeculationSettings()`, i.e. speculative LLM off) and
`rag: SpeculativeRagNode | None = None` (`None` disables prefetch even
when the flag is on). Event handling:

- On `SttPartial(text, stability)`: call `controller.on_partial`.
  `PREFETCH_RAG` -> spawn `asyncio.create_task(rag.prefetch(text))`,
  tracked in a session-owned set and awaited/cancelled at session end so
  no task outlives the session. `START_LLM` -> spawn the turn run early
  with `TurnContext.speculative=True` and register it via
  `controller.start(text, task)`.
- On `SttFinal(text, ...)`: `promoted = await controller.reconcile(text)`.
  Promoted -> flip the context as described above. Not promoted -> run the
  normal serial path from the final text.
- The turn's real RAG retrieval calls `rag.prefetch(final_text)`; when a
  partial already prefetched that exact query, `RagResult.cache_hit` is
  `True` and `LatencyWaterfall.rag_ms` records only the cache lookup.

### Prompt-cache key plumbing - `src/lucy/llm.py` (extend)

- Module-level named constant `PROMPT_CACHE_FIELD = "prompt_cache_key"` -
  the provider payload field name lives here and nowhere else.
- `compute_cache_key(session_id: str, system_prompt: str) -> str`: first
  16 hex chars of `sha256(session_id + "\x00" + system_prompt)`. Stable
  for the whole session (the stable system+history prefix), distinct
  across sessions.
- `VoiceSession` computes the key once per session and sets
  `LlmRequest.cache_key` on EVERY request it issues - speculative,
  promoted, serial, and tool-round continuations - so even aborted
  speculative tokens warm the provider cache for the retry.
- `OpenAiCompatibleAdapter`: payload construction includes
  `PROMPT_CACHE_FIELD: request.cache_key` if and only if `cache_key` is
  set. If card 33 built the payload inline, extract a
  `build_payload(request: LlmRequest) -> dict` method so the mapping is
  testable without network.
- `LocalLlmSimulator` gains `seen_cache_keys: list[str | None]`, appended
  on every `stream_chat` call, so tests assert plumbing without network.

### Budget assertions - `tests/test_speculation.py`

All timing via `ManualClock`; zero real sleeps; the whole module runs in
under 1 s of wall time.

- Serial path: the seven `LatencyBudgets` component fields
  (`endpoint_silence_ms`, `stt_final_ms`, `control_transport_ms`,
  `graph_dispatch_ms`, `llm_first_clause_ms`, `tts_first_byte_ms`,
  `gateway_pacing_ms`) sum to 790 ms - the ADR 0011 p50 component table -
  and that sum is `<= turn_total_ms`. Then drive one scripted turn with
  each stage paced at its budget field and assert the measured
  speech-end-to-first-`TtsSpeak` gap fits the modeled serial sum.
- Speculative path: same scripted turn run twice - serial (defaults) and
  with `enabled_llm_start=True` plus a stable partial arriving
  `head_start_ms` before `SttFinal` (script constant, smaller than
  `llm_first_clause_ms`). Assert the speculative final-to-first-speak gap
  equals the serial gap minus `head_start_ms`: the in-flight run was
  reused, not restarted.

### File scope

- Create: `tests/test_speculation.py`.
- Modify: `src/lucy/session.py` (controller, context, wiring),
  `src/lucy/settings.py` (`SpeculationSettings`), `src/lucy/llm.py`
  (cache-key helper, adapter mapping, simulator recording),
  `src/lucy/drivers.py` (speculative tool-deferral edge only).
- `src/lucy/rag.py` stays byte-identical. Nothing else is touched.

## Chips

- [x] **C1 - Speculation settings, gated off by default.** Record
  `shasum src/lucy/rag.py` in "Improvements noted" before coding. Tests
  first in new `tests/test_speculation.py`:
  `test_speculative_llm_is_off_by_default` (fresh `SpeculationSettings()`
  has `enabled_llm_start is False` and `enabled_rag_prefetch is True`),
  `test_env_overrides_speculation_settings`
  (`LUCY_SPECULATION_ENABLED_LLM_START=1` and
  `LUCY_SPECULATION_LLM_START_STABILITY=0.8` override), and
  `test_threshold_ordering_validated` (prefetch threshold above LLM
  threshold raises `ValueError`). Implement `SpeculationSettings` in
  `src/lucy/settings.py`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass
  (>=3 tests).
- [x] **C2 - Decision logic.** Tests first:
  `test_on_partial_below_thresholds_returns_none`,
  `test_on_partial_at_rag_threshold_prefetches_once_per_text` (same
  normalized text twice -> `PREFETCH_RAG` then `NONE`),
  `test_on_partial_never_starts_llm_when_gated_off` (stability 1.0 with
  defaults -> never `START_LLM`), and
  `test_on_partial_starts_llm_once_per_turn_when_enabled`. Implement
  `SpeculativeAction`, `SpeculationController.on_partial`, `start`, and
  `speculating` in `src/lucy/session.py` (pure decision state; no tasks
  yet). Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass.
- [x] **C3 - RAG prefetch wiring.** Tests first:
  `test_partial_prefetch_makes_final_retrieve_a_cache_hit` (scripted
  partial text equals the final; the turn's retrieval returns
  `RagResult(cache_hit=True)` and `LatencyWaterfall.rag_ms` reflects only
  the cache lookup) and
  `test_prefetch_tasks_never_outlive_the_session` (after session end, no
  pending prefetch tasks in `asyncio.all_tasks()`). Wire
  `SttPartial -> controller -> rag.prefetch` in `VoiceSession` with the
  tracked task set. Files: `src/lucy/session.py`,
  `tests/test_speculation.py`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass.
- [x] **C4 - Speculative suppression.** Tests first:
  `test_speculative_run_emits_no_tts_speak` (with `enabled_llm_start=True`
  and a stable partial, the gateway simulator records zero `TtsSpeak`
  before `SttFinal`; sentences are in `buffered_directives`) and
  `test_speculative_tool_call_waits_for_promotion` (simulator scripted to
  request a tool; the executor is not invoked until `promoted` is set).
  Implement `TurnContext`, the buffering send path in `VoiceSession`, and
  the `turn_context` deferral edge in `CascadedTurnDriver`. Files:
  `src/lucy/session.py`, `src/lucy/drivers.py`,
  `tests/test_speculation.py`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass.
- [x] **C5 - Reconcile: promote and abort.** Tests first:
  `test_prefix_match_promotes_in_flight_run` (`reconcile` returns `True`,
  the task object is the same one started on the partial),
  `test_promotion_flushes_buffered_sentences_in_order`,
  `test_revision_aborts_run_and_nothing_speculative_is_spoken` (final
  diverges from the trigger prefix; speculative text never reaches the
  transport; the serial answer is spoken), and
  `test_no_orphan_tasks_after_abort` (assert via `asyncio.all_tasks()`,
  card 35 pattern). Implement `async reconcile` plus the `SttFinal`
  branch in `VoiceSession`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass.
- [x] **C6 - Prompt-cache key plumbing.** Tests first:
  `test_cache_key_stable_within_session_distinct_across_sessions`,
  `test_adapter_payload_carries_cache_key_field_only_when_set`
  (`build_payload` includes `PROMPT_CACHE_FIELD` iff `cache_key` is set),
  and `test_simulator_records_cache_keys_for_speculative_and_serial_runs`
  (every request in a session - speculative, aborted retry, tool round -
  carries the same key in `seen_cache_keys`). Implement
  `compute_cache_key`, `PROMPT_CACHE_FIELD`, the adapter mapping, the
  simulator recording, and the session plumbing. Files: `src/lucy/llm.py`,
  `src/lucy/session.py`, `tests/test_speculation.py`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass.
- [x] **C7 - Budget assertions.** Tests first:
  `test_serial_path_fits_latency_budget_defaults` (component fields sum
  to 790, `<= turn_total_ms`; the ManualClock-paced scripted turn fits
  the modeled serial gap) and
  `test_speculation_shaves_final_to_first_speak_gap` (speculative gap ==
  serial gap minus the scripted head start; proves in-flight reuse by
  timing). Read every budget from `LatencyBudgets()` fields, never
  literals. Files: `tests/test_speculation.py`. Verify:
  `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all pass
  (>=16 tests) with total runtime < 1 s (proves no real sleeping).
- [x] **C8 - Full suite + bookkeeping.** Run everything, confirm
  `shasum src/lucy/rag.py` matches the value recorded in C1, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); `ManualClock`,
  `LocalLlmSimulator`, the gateway simulator, and `InMemoryRagIndex` /
  `SpeculativeRagNode` are the sanctioned doubles - real implementations
  of real contracts.
- Do not hardcode stability thresholds, feature flags, latency budgets, or
  the provider cache field name in session, driver, or adapter code;
  thresholds and flags live in `SpeculationSettings`, budgets in
  `LatencyBudgets`, the field name in `PROMPT_CACHE_FIELD` (agents.md).
- Do not enable the speculative LLM by default and do not flip the default
  in tests via module globals: an aborted speculative run has already
  consumed paid provider tokens, and revisions are routine, so default-on
  silently spends money. Tests opt in per instance
  (`SpeculationSettings(enabled_llm_start=True)`) or via env vars.
- Do not modify `src/lucy/rag.py`: `SpeculativeRagNode` is reused
  UNCHANGED (its cache and `deadline_ms` are already tested in
  `tests/test_rag.py`).
- Do not let any speculative output reach the transport before promotion:
  no `tts.speak`, no tool execution, no `tts.cancel` originating from a
  speculative run.
- Do not redefine `LlmRequest` or the stream event union (card 33 owns
  them); this card only plumbs the existing `cache_key` field.
- Do not start more than one speculative LLM run per turn, and do not
  bypass the card 35 cancellation path when aborting.
- Do not use real sleeps (`time.sleep`, `asyncio.sleep` with nonzero wall
  delay) in `tests/test_speculation.py`; all pacing goes through
  `ManualClock`.
- Do not touch files outside the File scope list above.
- Do not check a chip or Definition of Done box without running its
  Verify command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): speculation
  and cache telemetry leave only through `lucy.observe` exporters; no
  ingest, storage, or dashboard code.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_speculation.py -q` -> all
      pass (>=16 tests): promotion on prefix match reuses the in-flight
      run (asserted by timing); revision aborts cleanly and nothing
      speculative was ever spoken; the budget table is asserted
      deterministically.
- [x] `.venv/bin/python -m pytest tests/test_speculation.py
      tests/test_rag.py -q` -> all pass (existing `SpeculativeRagNode`
      behavior untouched).
- [x] `shasum src/lucy/rag.py` -> identical to the checksum recorded in C1
      (`SpeculativeRagNode` reused unchanged).
- [x] `.venv/bin/python -c "from lucy.settings import SpeculationSettings;
      assert SpeculationSettings().enabled_llm_start is False"` -> exits 0
      (speculative LLM off by default).
- [x] `grep -rn "unittest.mock\|MagicMock\|mocker"
      tests/test_speculation.py` -> no matches.
- [x] `grep -rn "time.sleep\|sleep(0\." tests/test_speculation.py` -> no
      matches (no real sleeps in timing tests).
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

- `shasum src/lucy/rag.py` before and after: `c9a10dec81747983b2b70d03da2abc1e58acd488`.
  `SpeculativeRagNode` was reused unchanged.
- Added `TurnRecord.rag_cache_hit` so the session can expose whether the
  final retrieval used a speculative prefetch without changing `src/lucy/rag.py`.
- `OpenAiCompatibleAdapter._payload` became testable as `build_payload` so the
  prompt-cache field mapping is covered without network.
- Verification run with Docker Compose:
  `docker compose run --rm lucy-api pytest tests/test_speculation.py -q` ->
  `22 passed`;
  `docker compose run --rm lucy-api pytest tests/test_speculation.py tests/test_rag.py -q`
  -> `27 passed`;
  `docker compose run --rm lucy-api python -c "from lucy.settings import SpeculationSettings; assert SpeculationSettings().enabled_llm_start is False"`
  -> exit 0;
  `docker compose run --rm lucy-api ruff check src tests` -> exit 0;
  `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0;
  `docker compose run --rm lucy-api mypy src` -> exit 0;
  `docker compose run --rm lucy-api pytest -q` -> `270 passed, 3 warnings`.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
