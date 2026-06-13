# 33 - Voice runtime M1: streaming LLM and sentence TTS

**Sprint:** S2 - Voice runtime core
**Epic:** Voice runtime
**Estimated effort:** ~8 h
**Depends on:** 32
**State:** pending

## Goal

Replace card 32's canned `responder` with a streaming LLM driver whose first
flushed clause is already speaking while generation continues (ADR 0011).
This card locks the `LlmProvider` stream-event contract that tool rounds
(card 34), speculation (card 36), and the speech-to-speech driver (card 38)
all build on.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  models, URLs, or budgets).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the typed
  `LlmProvider.stream_chat()` event union and the `TurnDriver` abstraction
  this card implements (cognition plane, cascaded path).
- `docs/adr/0005-self-hosted-pluggable-inference-layer.md` - why one
  OpenAI-compatible adapter also covers vLLM/SGLang self-hosted engines.
- `docs/adr/0003-no-mocks-testing-policy.md` - simulators and local protocol
  servers are the only sanctioned test doubles.
- `src/lucy/providers.py` - `Capability.LLM`, `ModelInfo`, `ModelRegistry`,
  `default_model_registry()`; model resolution validates against this.
- `src/lucy/metrics.py` - `LatencyWaterfall.llm_ms` and
  `CostBreakdown.llm_cost` (note `billable_audio_minutes` must be > 0);
  this card fills both per turn.
- `src/lucy/evals.py` - `booking_happy_path()`; the harness scenario the
  driver must pass end-to-end.
- `src/lucy/session.py` (built in card 32) - `VoiceSession`, `TurnState`,
  `TurnRecord`, and the `responder` seam this card supersedes.
- `src/lucy/clock.py` (built in card 32) - `Clock` Protocol and
  `ManualClock`; every timing test is paced through it.
- `src/lucy/settings.py` (built in card 32) - `LatencyBudgets`; all
  flush-timing assertions reference its fields, never literal ms.
- `src/lucy/transport/schema.py` (built in card 32) - `TtsSpeak`,
  `TtsCancel`, `TtsPlayback` directives the planner emits and consumes.
- `src/lucy/harness.py` (built in card 32) - `ConversationHarness`; the
  end-to-end runner this card extends with a driver.
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

## Spec

New and extended modules, exact contracts:

`src/lucy/llm.py` (new):

- `LlmMessage` (Pydantic): `role: Literal["system", "user", "assistant",
  "tool"]`, `content: str`, `tool_call_id: str | None = None`.
- `LlmRequest` (Pydantic): `provider: str`, `model: str`,
  `messages: list[LlmMessage]` (min length 1), `tools: list[dict] | None =
  None`, `temperature: float | None = None`, `max_output_tokens: int | None
  = None`, `cache_key: str | None = None` - prompt-cache hint, carried now,
  consumed by card 36; this card only round-trips it.
- Stream event union (frozen dataclasses): `TokenDelta(text: str)`,
  `ToolCallDelta(call_id: str, name: str | None, arguments_delta: str)`,
  `ToolCallReady(call_id: str, name: str, arguments: dict)`,
  `UsageReport(prompt_tokens: int, completion_tokens: int,
  cached_prompt_tokens: int = 0)`, `StreamEnd(finish_reason:
  Literal["stop", "tool_calls", "cancelled", "error"])`.
  `LlmStreamEvent = Union[...]` of exactly those five.
- `LlmProvider` Protocol:
  `def stream_chat(self, request: LlmRequest) ->
  AsyncIterator[LlmStreamEvent]: ...`
- `resolve_llm(registry: ModelRegistry, provider: str, model: str) ->
  ModelInfo` - raises `LlmModelNotRegistered(ValueError)` when the pair is
  absent or lacks `Capability.LLM`.
- `OpenAiCompatibleAdapter(base_url: str, api_key: str | None = None,
  http_client: httpx.AsyncClient | None = None)` - implements `LlmProvider`
  over the OpenAI chat-completions SSE wire format; the same adapter covers
  vLLM/SGLang self-hosted endpoints (ADR 0005). `base_url` is always
  injected; no URL literal lives in `src/`.
- `LocalLlmSimulator(turns: list[ScriptedLlmTurn], clock: Clock,
  token_interval_ms: float)` - real in-process `LlmProvider`.
  `ScriptedLlmTurn` dataclass: `tokens: list[str]`, `usage: UsageReport`,
  `finish_reason: str = "stop"`. Each `stream_chat` call consumes the next
  scripted turn: `TokenDelta` per token paced via `clock.sleep`, then
  `UsageReport`, then `StreamEnd`. Consumer cancellation propagates
  `CancelledError` and sets `simulator.cancelled = True`.

`src/lucy/speech.py` (new):

- Named constants `CLAUSE_BOUNDARY_CHARS = (".", "!", "?", ",", ";", ":")`
  and `MIN_FLUSH_CHARS` (default minimum clause length; the only place that
  number exists).
- `SentenceAssembler(min_flush_chars: int = MIN_FLUSH_CHARS)`:
  `feed(text: str) -> list[str]` buffers deltas and flushes a clause when a
  boundary char arrives and the buffered length >= `min_flush_chars`;
  `finalize() -> list[str]` flushes any non-empty remainder and resets.
- `TtsPlanner`: `plan(clause: str) -> TtsSpeak` assigning unique,
  monotonically ordered utterance ids with `flush=True`;
  `record_playback(event: TtsPlayback) -> None` tracks `mark_chars` per
  utterance; `spoken_text() -> str` returns fully played utterances plus
  the interrupted one truncated to its last heard `mark_chars`;
  `cancel_directive() -> TtsCancel` returns `TtsCancel("all")`.

`src/lucy/drivers.py` (new):

- `TurnDriverReport` frozen dataclass: `assistant_text: str`,
  `llm_ms: float`, `usage: UsageReport | None`, `llm_cost: float = 0.0`.
- `DriverEvent = Union[TtsSpeak, TurnDriverReport]`.
- `TurnDriver` Protocol: `def run_turn(self, user_text: str, history:
  Sequence[LlmMessage]) -> AsyncIterator[DriverEvent]: ...` - yields zero
  or more `TtsSpeak`, then exactly one terminal `TurnDriverReport`.
- `CascadedTurnDriver(llm: LlmProvider, registry: ModelRegistry,
  provider: str, model: str, clock: Clock, budgets: LatencyBudgets,
  pricing: LlmPricing | None = None,
  min_flush_chars: int = MIN_FLUSH_CHARS)` - resolves `(provider, model)`
  via `resolve_llm` at construction. `run_turn` is stt.final text -> LLM
  stream -> sentence-flushed tts.speak directives: feeds each
  `TokenDelta.text` into a `SentenceAssembler`, emits one `TtsSpeak` per
  flushed clause through a `TtsPlanner`, calls `finalize()` on `StreamEnd`,
  measures `llm_ms` with the injected clock from stream start to
  `StreamEnd`, computes `llm_cost` from `UsageReport` x `pricing` (0.0 when
  pricing is None). M1 raises `ToolCallsNotSupported(RuntimeError)` on
  `ToolCallDelta`/`ToolCallReady`; card 34 replaces that with tool rounds
  bounded by `LatencyBudgets.max_tool_rounds_per_turn`.

`src/lucy/settings.py` (extend; do not rename card 32 fields):

- `LlmPricing(BaseSettings)`: `prompt_per_1k: float = 0.0`,
  `completion_per_1k: float = 0.0`, env prefix `LUCY_LLM_PRICE_`. Zero
  defaults: no invented prices, env-overridable.

`src/lucy/session.py` and `src/lucy/harness.py` (modify):

- `VoiceSession` gains keyword-only `driver: TurnDriver | None = None`;
  exactly one of `responder`/`driver` must be set, else `ValueError`. With
  a driver: THINKING -> SPEAKING on the first forwarded `TtsSpeak`; the
  terminal report fills `waterfall.llm_ms` and sets `TurnRecord.cost =
  CostBreakdown(llm_cost=report.llm_cost, billable_audio_minutes=<clock-
  measured turn duration in minutes>)` (> 0 because the simulator paces
  via the clock). Barge-in cancellation from card 32 is unchanged;
  truncation now goes through `TtsPlanner.spoken_text()`.
- `ConversationHarness.run(...)` accepts `driver=` and passes it through;
  agent lines then come from the LLM stream, not the scenario script.

`pyproject.toml` (modify): promote `httpx` from
`[project.optional-dependencies].dev` to `[project.dependencies]` (the
adapter needs it at runtime).

Timing rule for the whole card: every flush-timing assertion is expressed
against `LatencyBudgets` fields (`llm_first_clause_ms`, `turn_total_ms`) on
a `ManualClock`; literal millisecond values appear nowhere in this card's
src or tests.

## Chips

- [ ] **C1 - LLM contract: events, request, resolution.** Write
  `tests/test_llm_stream.py` first:
  `test_stream_event_union_covers_five_event_types`,
  `test_llm_request_carries_cache_key_hint`,
  `test_resolve_llm_rejects_model_without_llm_capability` (use
  `default_model_registry()` and an STT-only entry). Then implement the
  models, union, Protocol, and `resolve_llm` in `src/lucy/llm.py`. Verify:
  `.venv/bin/python -m pytest tests/test_llm_stream.py -q` -> all pass
  (>=3 tests).
- [ ] **C2 - LocalLlmSimulator.** Tests first in
  `tests/test_llm_stream.py`:
  `test_simulator_streams_tokens_then_usage_then_end`,
  `test_simulator_paces_tokens_via_manual_clock_without_wall_time`,
  `test_simulator_cancellation_mid_stream_sets_cancelled`. Implement the
  simulator in `src/lucy/llm.py`. Verify:
  `.venv/bin/python -m pytest tests/test_llm_stream.py -q` -> all pass,
  total runtime < 1 s (proves no real sleeping).
- [ ] **C3 - OpenAI-compatible adapter vs local SSE server.** Tests first
  in `tests/test_llm_stream.py`:
  `test_adapter_parses_sse_tokens_and_usage`,
  `test_adapter_assembles_tool_call_deltas_into_ready`, driven by an
  in-test FastAPI app speaking the chat-completions SSE format, served
  in-process via `httpx.ASGITransport` (a local protocol server, ADR
  0003). Implement `OpenAiCompatibleAdapter`; move `httpx` to runtime
  dependencies in `pyproject.toml`. Verify:
  `.venv/bin/python -m pytest tests/test_llm_stream.py -q` -> all pass.
- [ ] **C4 - SentenceAssembler.** Write
  `tests/test_sentence_assembler.py` first:
  `test_feed_flushes_clause_at_boundary_past_min_length`,
  `test_short_clause_stays_buffered_until_next_boundary`,
  `test_finalize_flushes_trailing_text`. Implement assembler and
  constants in `src/lucy/speech.py`. Verify:
  `.venv/bin/python -m pytest tests/test_sentence_assembler.py -q` ->
  all pass.
- [ ] **C5 - TtsPlanner.** Tests first in
  `tests/test_sentence_assembler.py`:
  `test_planner_assigns_ordered_unique_utterance_ids`,
  `test_spoken_text_truncates_to_last_mark_chars_on_barge_in` (feed
  `TtsPlayback` events from `src/lucy/transport/schema.py`, not invented
  shapes). Implement `TtsPlanner` in `src/lucy/speech.py`. Verify:
  `.venv/bin/python -m pytest tests/test_sentence_assembler.py -q` ->
  all pass.
- [ ] **C6 - CascadedTurnDriver.** Write `tests/test_cascaded_driver.py`
  first: `test_first_tts_speak_before_stream_end_within_budget` (clock
  timestamp of the first `TtsSpeak` is earlier than `StreamEnd` and within
  `budgets.llm_first_clause_ms` of stream start; derive
  `token_interval_ms` from `budgets.llm_first_clause_ms`, never a literal),
  `test_report_carries_llm_ms_usage_and_cost`,
  `test_tool_call_event_raises_tool_calls_not_supported`. Implement
  `src/lucy/drivers.py`. Verify:
  `.venv/bin/python -m pytest tests/test_cascaded_driver.py -q` -> all
  pass.
- [ ] **C7 - VoiceSession and harness wiring.** Tests first in
  `tests/test_cascaded_driver.py`:
  `test_voice_session_with_driver_fills_llm_ms_and_cost` (rejects
  responder+driver together with `ValueError`),
  `test_harness_booking_happy_path_with_llm_simulator` (script the
  simulator turns to tokenize the scenario's agent lines so the transcript
  still matches `booking_happy_path()`). Modify `src/lucy/session.py`,
  `src/lucy/harness.py`, and add `LlmPricing` to `src/lucy/settings.py`.
  Verify: `.venv/bin/python -m pytest tests/test_cascaded_driver.py -q`
  -> all pass.
- [ ] **C8 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); `LocalLlmSimulator`
  and the in-test SSE server are the sanctioned doubles - real
  implementations of real contracts. No `unittest.mock`, no monkeypatched
  provider calls.
- Do not hardcode provider names, model names, URLs, prices, or budgets
  (agents.md): models resolve through `ModelRegistry`, `base_url` is
  injected, pricing lives in `LlmPricing`, the minimum flush length is the
  `MIN_FLUSH_CHARS` named constant, and flush timing references
  `LatencyBudgets` fields only - never literal ms values.
- Do not call paid or networked provider endpoints in default tests; those
  belong to explicit integration profiles (ADR 0003).
- Do not modify card 32 contracts: transport schema models, `TurnState`
  values, `TurnRecord` field meanings, `LatencyBudgets` field names.
  Extend only.
- Do not implement tool execution or filler speech (card 34), speculation
  or prompt-cache consumption (card 36) - only carry `cache_key` - or the
  speech-to-speech driver (card 38).
- Do not touch files outside: `src/lucy/{llm.py,speech.py,drivers.py}`,
  `src/lucy/{session.py,harness.py,settings.py}`, `pyproject.toml`,
  `tests/{test_llm_stream.py,test_sentence_assembler.py,
  test_cascaded_driver.py}`.
- Do not put platform/Pili concerns inside the SDK (ADR 0010); per-turn
  telemetry leaves only through the `lucy.observe` seam.
- Do not check a box without running its Verify command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_llm_stream.py
      tests/test_sentence_assembler.py tests/test_cascaded_driver.py -q`
      -> all pass, including
      `test_first_tts_speak_before_stream_end_within_budget` and
      `test_harness_booking_happy_path_with_llm_simulator`
- [ ] `.venv/bin/python -m pytest -q` -> full suite green, zero warnings
      introduced (use Docker Compose `docker compose run --rm lucy-api
      pytest` when the daemon is available)
- [ ] `grep -rn "time.sleep\|sleep(0\." tests/test_llm_stream.py
      tests/test_cascaded_driver.py` -> no matches (no real sleeps in
      timing tests)
- [ ] `grep -rn "https://\|api\.openai\.com" src/lucy/llm.py
      src/lucy/drivers.py src/lucy/speech.py` -> no matches (no hardcoded
      endpoints)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
