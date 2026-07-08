# 35 - Voice runtime M3: interruption correctness

**Sprint:** S3 - Conversational correctness
**Epic:** Voice runtime
**Estimated effort:** ~8 h
**Depends on:** 34
**State:** done

## Goal

Barge-in behaves truthfully end to end: speech stops, in-flight work cancels
per policy, and state records only what the caller actually heard. The
`booking_interruption` eval scenario goes green through the harness, and the
deprecated `RealtimeVoicePipeline` shim retires.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  thresholds or policies).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the architecture: the
  `VoiceSession` event plane owns barge-in edges and cancellation; this card
  makes those edges correct. Its Consequences section deprecates
  `RealtimeVoicePipeline`, which this card removes.
- `docs/adr/0009-turn-taking-and-conversational-fluidity.md` - barge-in and
  interruption handling as the spine of conversational fluidity.
- `docs/adr/0003-no-mocks-testing-policy.md` - simulators and `ManualClock`
  are the sanctioned doubles; no mocking frameworks.
- `docs/adr/0010-open-core-split.md` - everything here is open SDK code;
  telemetry leaves only through the `lucy.observe` seam.
- `backlog/pending/32_runtime_m0_walking_skeleton.md` (in `done/` by the time
  you execute) - defines the contracts this card builds on:
  `src/lucy/transport/schema.py` (`TtsPlayback` with
  `state: started|mark|finished|flushed` and `mark_chars: int`, `BargeIn`,
  `VadSpeechStart`, `TtsCancel(utterance_id | "all")`), `src/lucy/session.py`
  (`VoiceSession`, `TurnState`, `TurnRecord`), `src/lucy/clock.py`
  (`ManualClock`), `src/lucy/transport/dev_gateway.py`
  (`LocalGatewaySimulator`), `src/lucy/harness.py` (`ConversationHarness`,
  `HarnessResult`). Card 32 already ships a first-cut SPEAKING barge-in with
  `mark_chars` truncation against a canned responder; this card hardens that
  contract across the full driver stack.
- `backlog/pending/33_runtime_m1_streaming_llm.md` (in `done/` once executed)
  - creates `src/lucy/drivers.py` (`CascadedTurnDriver`), `src/lucy/llm.py`
  (`LocalLlmSimulator`), `src/lucy/speech.py` (`SentenceAssembler`); sentence
  flushing means one turn can emit several `TtsSpeak` utterances.
- `backlog/pending/34_runtime_m2_realtime_tools.md` (in `done/` once
  executed) - creates `src/lucy/tools.py` (`ToolProfile.on_barge_in`,
  `BargeInPolicy.CANCEL` / `RUN_TO_COMPLETION`, `McpToolExecutor`,
  `ToolResult`); card 34 only DEFINES `on_barge_in`, this card ENFORCES it.
- `src/lucy/evals.py` - `SyntheticCallScenario`,
  `default_sales_booking_scenarios()` (contains `booking_interruption` with
  `expected_outcome="interruption"`), `EvalEvidence.interruption_handled`,
  `score_synthetic_call` (gates `interruption_handling` when the expected
  outcome is `"interruption"`). Reused UNCHANGED.
- `src/lucy/voice.py` - `RealtimeVoicePipeline` is the shim to delete; the
  event dataclasses (`TranscriptEvent`, `BargeInEvent`, `TurnLatencyEvent`,
  `TtsStreamEvent`, `ProviderTimeoutEvent`), `SttProvider`/`TtsProvider`
  protocols, and `LocalSttSimulator`/`LocalTtsSimulator` STAY.
- `src/lucy/metrics.py` - `LatencyWaterfall.mcp_tools_ms`; a
  run-to-completion tool that survives barge-in still accrues here.
- `tests/test_voice_pipeline.py` - the tests to migrate when the shim goes;
  see the migration map in the Spec.
- `tests/test_registry_mcp_metrics.py` - `SlowMcpTransport` blocked-on-
  `asyncio.Event` pattern; reuse it for the in-flight tool cancellation test.
- `tests/test_evals.py` - house style for rubric assertions; the eval-gate
  test follows it.

## Spec

### Interruption contract - `src/lucy/session.py`

Barge-in behavior by `TurnState`, enumerated for all four states. The
session's own state machine is authoritative; the `BargeIn.during` hint from
the gateway is advisory and ignored on disagreement (races happen):

- IDLE: incoming `BargeIn` or `VadSpeechStart` is NOT an interruption; it is
  the start of the next turn (normal LISTENING entry, no `interrupted` flag,
  no `TtsCancel`).
- LISTENING: `BargeIn` is ignored (the caller is already the speaker); no
  state change, no directive.
- THINKING: `VadSpeechStart` or `BargeIn` cancels the turn's asyncio task
  (the graph/LLM run). No `TtsSpeak` directive may be emitted for the
  cancelled turn after cancellation. The `TurnRecord` is kept with
  `assistant_text == ""` and `interrupted=True` (the caller heard nothing).
  The session returns to LISTENING and the new utterance accumulates as the
  next turn's input.
- SPEAKING: `BargeIn` triggers, in this order:
  1. Send exactly one `TtsCancel` directive with `utterance_id="all"`
     downstream, before any new turn work starts.
  2. Cancel the turn's asyncio task (driver stream and tool rounds, per the
     tool policy below).
  3. Set `TurnRecord.assistant_text` to the heard prefix (rule below) and
     `TurnRecord.interrupted = True`.
  4. Return to LISTENING; the interrupting utterance becomes the next turn.

Truncation rule (the `mark_chars` contract from the card 32 schema; pure
helper so it is testable without a session):

```
def heard_assistant_text(
    utterance_texts: Sequence[tuple[str, str]],  # (utterance_id, full text)
    playbacks: Sequence[TtsPlayback],            # received, in arrival order
) -> str
```

- Utterances whose last playback state is `finished` contribute their full
  text, in `TtsSpeak` emission order.
- The utterance in flight contributes `text[:pos]` where `pos` is, in
  precedence order: `mark_chars` of its `flushed` playback (the gateway's
  authoritative final position after a `TtsCancel`), else `mark_chars` of its
  last `mark` playback, else `0` (barge-in before the first mark contributes
  the empty string).
- Utterances spoken (`TtsSpeak` sent) but never started contribute nothing.
- `VoiceSession` records each turn's `(utterance_id, text)` pairs when
  emitting `TtsSpeak` and feeds them with the received playbacks to this
  helper on interruption. `TurnRecord.assistant_text` for an interrupted turn
  is exactly its return value - never the planned full reply.
- `LocalGatewaySimulator` (`src/lucy/transport/dev_gateway.py`) must answer a
  `TtsCancel` with a `TtsPlayback(state="flushed", mark_chars=<heard>)` for
  the in-flight utterance; extend it in this card only if card 32 did not
  ship that, and note it under "Improvements noted".

Task hygiene: after any interruption path, `asyncio.all_tasks()` returns to
the pre-turn baseline, except run-to-completion tool tasks, which
`VoiceSession.run()` must await before returning.

### Tool barge-in policy - `src/lucy/tools.py`, `src/lucy/drivers.py`

`ToolProfile.on_barge_in` (field defined in card 34) is enforced here:

- `CascadedTurnDriver` wraps each `McpToolExecutor.execute(tool, arguments)`
  in its own `asyncio.Task` named `f"tool:{tool.key}"`.
- `BargeInPolicy.CANCEL`: the tool task is awaited directly, so cancelling
  the turn task cancels it. `McpToolExecutor.execute` must let
  `asyncio.CancelledError` propagate - it converts only
  `McpPermissionError`/`McpSchemaError`/`McpTimeoutError` into `ToolResult`s
  (card 34); it never swallows cancellation into a result. No `ToolResult`
  is recorded for a cancelled tool and no further tool round starts.
- `BargeInPolicy.RUN_TO_COMPLETION`: the driver awaits the tool through
  `asyncio.shield(task)`. On turn cancellation the inner task keeps running;
  the driver hands it off via a new constructor param
  `adopt_background_task: Callable[[asyncio.Task], None] | None = None`.
  `VoiceSession` passes its adopter, keeps the tasks in a private set, and
  `run()` awaits them all before returning. When the shielded tool completes:
  its `ToolResult.elapsed_ms` still accrues into the interrupted turn's
  `LatencyWaterfall.mcp_tools_ms` and its `tool_call` telemetry event (card
  34 `emit`) is still emitted - but NO new LLM round starts and nothing from
  the result is ever spoken. A booking write completes; the caller just does
  not hear about it in the cancelled turn.
- `src/lucy/mcp.py` stays byte-identical; permissions and audit are not
  touched by either policy.

### Shim retirement - `src/lucy/voice.py` and dependents

- Delete the `RealtimeVoicePipeline` class. Keep everything else in
  `src/lucy/voice.py`: `AudioChunk`, `TranscriptEvent`, `TurnLatencyEvent`,
  `BargeInEvent`, `TtsStreamEvent`, `ProviderTimeoutEvent`, `VoiceEvent`,
  `ProviderPayloadError`, `SttProvider`, `TtsProvider`, `LocalSttSimulator`,
  `LocalTtsSimulator` (or their post-restructure homes if the S1 cards moved
  them; follow the grep, not this list).
- `grep -rn "RealtimeVoicePipeline" src/ tests/ examples/` and migrate every
  hit to `VoiceSession`/`ConversationHarness`. Known candidates at card-
  writing time: `tests/test_voice_pipeline.py` (7 usages); cards 25/26 wire
  the pipeline into instrumentation and the `VoiceAgent` facade
  (`src/lucy/agent.py`), so expect hits there too. Record the actual hit
  list under "Improvements noted" before editing.
- Migration map for `tests/test_voice_pipeline.py`:
  - `test_voice_pipeline_streams_partial_transcripts_and_latency` - delete;
    superseded by the dev-gateway partial/final tests (card 32 C4).
  - `test_voice_pipeline_barge_in_cancels_active_tts` and
    `test_voice_pipeline_barge_in_cancels_local_tts_simulator_stream` -
    delete; superseded by `tests/test_interruption.py` (this card, C1).
  - `test_voice_pipeline_emits_stt_timeout_event_and_cancels_provider` and
    `test_voice_pipeline_emits_tts_timeout_event_and_cancels_provider` -
    replace with direct simulator-cancellation coverage:
    `test_simulators_set_cancelled_flag_when_cancelled` (wrap
    `transcribe`/`synthesize` in `asyncio.wait_for` with a short timeout,
    assert `.cancelled is True`).
  - The pure simulator tests (`test_local_stt_simulator_*`,
    `test_local_tts_simulator_*`,
    `test_voice_provider_simulators_reject_malformed_payloads`) stay
    unchanged.

### Eval gate - `src/lucy/harness.py`

- If `HarnessResult` does not already capture downstream messages, extend it
  with `directives: list[Envelope]` filled from the gateway simulator's sent
  log (`TtsSpeak`, `TtsCancel`, and the rest in emission order).
- New function, exact signature:

```
def evidence_from_result(
    scenario: SyntheticCallScenario,
    result: HarnessResult,
) -> EvalEvidence
```

  - `actual_outcome`: `"interruption"` when any `TurnRecord.interrupted` is
    True, else `"completed"`. Only the interruption mapping is wired in this
    card; card 37 (S4) extends outcome detection for the other five golden
    scenarios.
  - `interruption_handled`: True iff ALL of: (a) at least one interrupted
    `TurnRecord` exists, (b) a `TtsCancel` directive appears in
    `result.directives`, (c) for the interrupted turn, with `planned` =
    concatenation of its `TtsSpeak.text` directives in emission order,
    `planned.startswith(record.assistant_text)` and
    `len(record.assistant_text) < len(planned)` (a proper heard prefix).
    Computed from the run - never passed in by the caller.
  - `rag_grounded=True` and `policy_adhered=True`, with an inline comment:
    the M3 harness exercises neither RAG nor policy, so these gates cannot
    fail here; real signals arrive with card 37. Note this under
    "Improvements noted".
  - `escalated_to_human=False`.
- The `booking_interruption` scenario (from
  `default_sales_booking_scenarios()`, `expected_outcome="interruption"`)
  must then pass `score_synthetic_call` end to end:
  `result.passed is True` and `gates["interruption_handling"] is True`.
  `LocalGatewaySimulator` fires `BargeIn` mid-playback for the caller line
  that follows an agent line in an interruption scenario (card 32 behavior;
  extend the simulator here only if missing, and note it).

### File scope

- Create: `tests/test_interruption.py`.
- Modify: `src/lucy/session.py`, `src/lucy/drivers.py`, `src/lucy/tools.py`,
  `src/lucy/harness.py`, `src/lucy/voice.py` (delete the shim),
  `tests/test_voice_pipeline.py` (migration map above),
  `src/lucy/transport/dev_gateway.py` (only if `flushed`/`BargeIn` emission
  is missing), plus the facade/instrumentation files the C5 grep finds
  (list them in "Improvements noted" first). Nothing else -
  `src/lucy/mcp.py` and `src/lucy/evals.py` stay byte-identical.

## Chips

- [x] **C1 - SPEAKING barge-in cancels TTS and truncates by marks.** Test
  first in new `tests/test_interruption.py`:
  `test_barge_in_during_speaking_emits_tts_cancel_all_and_truncates_to_marks`
  - drive `VoiceSession` with `LocalGatewaySimulator` + `ManualClock` so the
  agent reply plays past two `mark` playbacks, then fire `BargeIn`; assert
  exactly one `TtsCancel` with `utterance_id == "all"`,
  `assistant_text == planned_text[:heard_mark_chars]`, `interrupted is True`,
  and `asyncio.all_tasks()` back to baseline. Implement
  `heard_assistant_text` plus the SPEAKING edge (flushed-position
  precedence) in `src/lucy/session.py`; extend
  `src/lucy/transport/dev_gateway.py` with `flushed` emission only if card
  32 did not ship it. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass
  (>=1 test). If card 32's first cut already passes parts, keep the test as
  regression and note it in "Improvements noted".
- [x] **C2 - Truncation edge cases.** Tests first:
  `test_barge_in_before_first_mark_records_empty_assistant_text`,
  `test_multi_utterance_turn_truncates_finished_plus_partial` (two
  sentence-flushed `TtsSpeak` utterances; first `finished`, second
  interrupted mid-mark; assistant_text = full first + heard prefix of
  second), and `test_barge_in_after_finished_starts_next_turn_uninterrupted`
  (post-`finished` BargeIn is the next turn's speech start: no `TtsCancel`,
  no `interrupted` flag). Files: `src/lucy/session.py`,
  `tests/test_interruption.py`. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass.
- [x] **C3 - THINKING interruption discards unspoken output.** Tests first:
  `test_speech_during_thinking_cancels_run_and_discards_unspoken_output`
  (`VadSpeechStart` while the driver streams, before any `TtsSpeak`: no
  `TtsSpeak` directive is ever sent for that turn, `assistant_text == ""`,
  `interrupted is True`, no orphan tasks) and
  `test_interrupting_utterance_becomes_next_turn_input` (the new utterance's
  `SttFinal` text is the next `TurnRecord.user_text`). Implement the
  THINKING edge in `src/lucy/session.py`. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass.
- [x] **C4 - Tool barge-in policies.** Tests first:
  `test_barge_in_cancels_inflight_tool_with_cancel_policy` (transport
  blocked on a never-set `asyncio.Event`, same pattern as `SlowMcpTransport`
  in `tests/test_registry_mcp_metrics.py`; barge-in mid-execution; assert
  the tool task is cancelled, no `ToolResult` recorded, no orphan tasks) and
  `test_run_to_completion_tool_survives_barge_in_and_records_result`
  (profile `on_barge_in=RUN_TO_COMPLETION`; barge-in mid-execution; assert
  the tool completes, `elapsed_ms` accrues into the interrupted turn's
  `LatencyWaterfall.mcp_tools_ms`, one `tool_call` telemetry event, and
  nothing from the result appears in any `TtsSpeak`). Implement
  shield/adoption in `src/lucy/drivers.py` (`adopt_background_task` param),
  `src/lucy/session.py` (adopter + await-before-return), and the
  CancelledError pass-through in `src/lucy/tools.py`. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass.
- [x] **C5 - Interruption storm leaves no orphans.** Test first:
  `test_repeated_barge_ins_leave_no_orphan_tasks` - a scenario with three
  consecutive interruptions; after `run()` returns, `asyncio.all_tasks()`
  matches the baseline and every interrupted record satisfies the
  proper-heard-prefix property. Fix any leak it finds in
  `src/lucy/session.py`. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass.
- [x] **C6 - Retire the RealtimeVoicePipeline shim.** Run
  `grep -rn "RealtimeVoicePipeline" src/ tests/ examples/` and record the
  hit list in "Improvements noted". Test first (red while the class exists):
  `test_realtime_voice_pipeline_is_retired` in
  `tests/test_voice_pipeline.py` asserting
  `not hasattr(lucy.voice, "RealtimeVoicePipeline")`. Then delete the class
  from `src/lucy/voice.py`, apply the migration map (delete the two barge-in
  and one transcript test, replace the two timeout tests with
  `test_simulators_set_cancelled_flag_when_cancelled`, keep the simulator
  tests), and migrate every remaining grep hit (facade/instrumentation) to
  `VoiceSession`/`ConversationHarness`. Verify:
  `.venv/bin/python -m pytest tests/test_voice_pipeline.py -q` -> all pass,
  then `grep -rn "RealtimeVoicePipeline" src/ tests/ examples/` -> no
  matches.
- [x] **C7 - booking_interruption eval green via the harness.** Test first:
  `test_booking_interruption_eval_green_via_harness` - select the scenario
  by name from `default_sales_booking_scenarios()`, run
  `ConversationHarness.run`, build evidence with
  `evidence_from_result(scenario, result)` (never hand-set booleans), score
  with `score_synthetic_call`, assert `result.passed is True` and
  `gates["interruption_handling"] is True`. Implement
  `evidence_from_result` and (if missing) the `HarnessResult.directives`
  capture in `src/lucy/harness.py`. Verify:
  `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass
  (>=9 tests).
- [x] **C8 - Full suite + bookkeeping.** Run everything, confirm
  `src/lucy/mcp.py` and `src/lucy/evals.py` are untouched (`git diff --stat`
  shows no changes there), fill "Improvements noted", move this card to
  `done/`. Verify: `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003);
  `LocalGatewaySimulator`, `LocalLlmSimulator`, `ManualClock`, and a plain
  blocked-event transport in the test file are the sanctioned doubles - real
  implementations of real contracts.
- Do not hardcode mark positions, truncation offsets, deadlines, or policy
  choices in session/driver code; policies live in `ToolProfile`, timing in
  `LatencyBudgets` typed settings (agents.md).
- Do not approximate truncation by wall-clock time or word heuristics; only
  the `mark_chars`/`flushed` positions from `TtsPlayback` count.
- Do not record the planned full reply as `assistant_text` for an
  interrupted turn - state records only what the caller actually heard.
- Do not let a RUN_TO_COMPLETION tool speak after interruption: its result
  is recorded and accrued, never voiced, and never starts a new LLM round.
- Do not swallow `asyncio.CancelledError` into a `ToolResult` inside
  `McpToolExecutor.execute`; cancellation must propagate for CANCEL tools.
- Do not modify `src/lucy/mcp.py` or `src/lucy/evals.py`; `McpClient` audit
  and the eval rubric are reused unchanged.
- Do not delete the voice event dataclasses, provider protocols, or local
  simulators when retiring the shim; only `RealtimeVoicePipeline` goes.
- Do not hand-set `interruption_handled=True` (or any evidence boolean) in
  the eval test; evidence comes from `evidence_from_result` over the run.
- Do not use real sleeps in interruption tests; pace everything through
  `ManualClock`.
- Do not implement speculation or prompt caching (card 36), checkpointing or
  handoff (card 37), or outcome detection beyond interruption (card 37).
- Do not touch files outside the File scope list above.
- Do not check a chip or Definition of Done box without running its Verify
  command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): interruption
  telemetry leaves only through `lucy.observe` exporters; no ingest,
  storage, or dashboard code.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_interruption.py -q` -> all pass
      (>=9 tests): the truncated transcript matches exactly the heard prefix
      by marks; THINKING-phase speech discards unspoken output; both
      `on_barge_in` policies enforced; no orphan asyncio tasks after any
      interruption (asserted via `asyncio.all_tasks()`);
      `test_booking_interruption_eval_green_via_harness` passes with
      evidence computed from the run.
- [x] `.venv/bin/python -m pytest tests/test_voice_pipeline.py
      tests/test_voice_session.py tests/test_harness.py -q` -> all pass
      (shim retired without breaking the M0 suite).
- [x] `grep -rn "RealtimeVoicePipeline" src/ tests/ examples/` -> no matches.
- [x] `grep -rn "unittest.mock\|MagicMock\|mocker" tests/test_interruption.py`
      -> no matches.
- [x] `git diff --stat src/lucy/mcp.py src/lucy/evals.py` -> no changes.
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is
      available).
- [x] Post-task audit done; follow-up cards raised for anything noticed
      (at minimum: real `rag_grounded`/`policy_adhered` evidence signals if
      not already covered by card 37).

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

- Initial grep hits for `RealtimeVoicePipeline` before migration:
  `src/lucy/voice.py`, `src/lucy/agent.py`, `src/lucy/__init__.py`,
  `src/lucy/observe/__init__.py`, `tests/test_agent_facade.py`,
  `tests/test_observability.py`, `tests/test_testing_subpackage.py`, and
  `tests/test_voice_pipeline.py`. All were migrated; final grep over
  `src/ tests/ examples/` returns no matches.
- `LocalGatewaySimulator` already handled basic mark truncation, but it did not
  emit/drain `flushed` after `TtsCancel` or safely skip stale cancel barriers.
  This card added that behavior and kept the older gateway helper compatible
  with directives that omit `turn_id`.
- `RealtimeVoicePipeline` was still part of the curated facade and
  observability tests. The facade now resolves local STT/TTS providers directly
  and emits the unified turn telemetry without the shim.
- M3 evidence still sets `rag_grounded=True` and `policy_adhered=True` because
  interruption scenarios do not exercise those signals. Existing card 37 owns
  broader golden-scenario outcome/evidence detection, so no new follow-up card
  was needed.
- Verification run with Docker Compose:
  `docker compose run --rm lucy-api pytest tests/test_interruption.py -q` ->
  `10 passed`;
  `docker compose run --rm lucy-api pytest tests/test_voice_pipeline.py tests/test_voice_session.py tests/test_harness.py -q`
  -> `11 passed`;
  `docker compose run --rm lucy-api ruff check src tests` -> exit 0;
  `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0;
  `docker compose run --rm lucy-api mypy src` -> exit 0;
  `docker compose run --rm lucy-api pytest -q` -> `248 passed, 3 warnings`.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
