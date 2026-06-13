# 32 - Voice runtime M0: walking skeleton

**Sprint:** S2 - Voice runtime core
**Epic:** Voice runtime
**Estimated effort:** ~10 h
**Depends on:** 26
**State:** pending

## Goal

The smallest end-to-end live session (ADR 0011): a scripted call flows
through a `VoiceSession` actor and produces a real per-turn
`LatencyWaterfall` and span tree. The control-channel schema is locked in
this card because every transport adapter and the Rust gateway build
against it.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  budgets).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the architecture this
  card implements (event plane vs cognition plane; this card is the event
  plane).
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - why no audio
  frames ever cross the control channel.
- `src/lucy/runtime.py` - `GraphExecutor`, `GraphContext`, `TraceEvent`;
  cancellation propagation you will reuse, do not modify it in this card.
- `src/lucy/voice.py` - existing event dataclasses (`TranscriptEvent`,
  `BargeInEvent`, `TurnLatencyEvent`, `TtsStreamEvent`); reuse them, do not
  redefine.
- `src/lucy/metrics.py` - `LatencyWaterfall`; your turn finalization fills it.
- `src/lucy/evals.py` - `SyntheticCallScenario`, `booking_happy_path()`; the
  simulator scripts come from here.
- `src/lucy/observe/` - the tracer your spans feed (built in card 24).
- `tests/test_runtime.py` - house test style: plain pytest, asyncio_mode
  auto, no mocks.

## Spec

New modules, exact contracts:

`src/lucy/transport/schema.py` (Pydantic, all models `extra="forbid"`):

- `Envelope`: `v: int = 1`, `type: str`, `session_id: str`,
  `turn_id: str | None`, `seq: int`, `ts_ms: int`.
- Upstream (gateway -> python) payload models: `SessionStarted(transport,
  caller, codecs)`, `VadSpeechStart(at_ms)`, `VadSpeechEnd(at_ms,
  speech_ms)`, `SttPartial(text, stability: float 0..1, provider)`,
  `SttFinal(text, provider, stt_ms)`, `Dtmf(digit)`,
  `TtsPlayback(utterance_id, state: started|mark|finished|flushed,
  mark_chars: int)`, `BargeIn(at_ms, during: speaking|thinking,
  utterance_id: str | None)`, `TransportMetrics(jitter_ms, rtt_ms,
  packet_loss)`, `SessionEnded(reason)`.
- Downstream (python -> gateway): `SessionConfigure(stt, tts, vad)`,
  `TtsSpeak(utterance_id, text, flush: bool)`, `TtsCancel(utterance_id |
  "all")`, `DtmfSend(digits)`, `Transfer(target)`, `SessionEnd(reason)`.
- `parse_event(raw: dict) -> Envelope` and typed payload accessor; unknown
  `type` raises `UnknownControlMessage`.

`src/lucy/clock.py`: `Clock` Protocol (`monotonic() -> float`,
`async sleep(seconds: float) -> None`); `MonotonicClock` (real);
`ManualClock` (`advance(ms)`, pending sleeps resolve deterministically).

`src/lucy/settings.py`: `LatencyBudgets(BaseSettings)` with fields
`endpoint_silence_ms=150`, `stt_final_ms=60`, `control_transport_ms=10`,
`graph_dispatch_ms=10`, `llm_first_clause_ms=380`, `tts_first_byte_ms=150`,
`gateway_pacing_ms=30`, `turn_total_ms=800`, `max_tool_rounds_per_turn=3` -
env-overridable, never hardcoded elsewhere.

`src/lucy/session.py`:

- `TurnState(str, Enum)`: IDLE, LISTENING, THINKING, SPEAKING.
- `TurnRecord` dataclass: `turn_id, user_text, assistant_text, interrupted:
  bool, waterfall: LatencyWaterfall, cost: CostBreakdown | None`.
- `VoiceSession(session_id, transport, responder, state_store=None,
  tracer=None, clock=MonotonicClock(), budgets=LatencyBudgets())` with
  `async run() -> list[TurnRecord]`: consumes transport events, drives the
  state machine, runs each turn as ONE asyncio task, cancels it on
  `BargeIn`/`VadSpeechStart` during THINKING/SPEAKING, truncates
  `assistant_text` to the last `mark_chars` heard, finalizes waterfall +
  spans per turn. `responder` is `Callable[[str], Awaitable[str]]` for M0
  (canned text; the LLM driver replaces it in card 33).

`src/lucy/transport/dev_gateway.py`: `LocalGatewaySimulator(scenario:
SyntheticCallScenario, clock: Clock)` - real in-process implementation of
the schema: caller turns become `SttPartial` (word-accumulating, rising
stability) then `SttFinal`; `TtsSpeak` directives produce
`TtsPlayback(started -> mark -> finished)`; a caller line marked as
interruption in the scenario fires `BargeIn` mid-playback; ends with
`SessionEnded`.

`src/lucy/tracing.py`: `Span` dataclass (`span_id, parent_id, name,
started_at_ms, ended_at_ms, attributes, status`), `SpanRecorder` Protocol,
`TurnSpanTree` builder producing `lucy.session` -> `lucy.turn` ->
`lucy.node.*` hierarchy emitted through `lucy.observe`.

`src/lucy/harness.py`: `ConversationHarness.run(scenario, responder, ...)`
-> `HarnessResult(transcript, turn_records, spans, waterfalls)`.

## Chips

- [ ] **C1 - Control-channel schema.** Write
  `tests/test_transport_schema.py` first: round-trip every message type,
  reject unknown type, reject extra fields, version field present. Then
  implement `src/lucy/transport/{__init__.py,schema.py}`. Verify:
  `.venv/bin/python -m pytest tests/test_transport_schema.py -q` -> all
  pass (>=6 tests).
- [ ] **C2 - Clock.** Test first in `tests/test_clock.py`:
  `ManualClock.advance` resolves a pending sleep without wall time; two
  sleeps resolve in order. Implement `src/lucy/clock.py`. Verify:
  `.venv/bin/python -m pytest tests/test_clock.py -q` -> all pass, total
  runtime < 1 s (proves no real sleeping).
- [ ] **C3 - Typed budgets.** Test first in `tests/test_settings.py`: env
  var `LUCY_BUDGET_TURN_TOTAL_MS=500` overrides the default; defaults match
  the ADR 0011 table. Implement `src/lucy/settings.py` with
  `pydantic-settings`. Verify:
  `.venv/bin/python -m pytest tests/test_settings.py -q` -> all pass.
- [ ] **C4 - Gateway simulator.** Test first in `tests/test_dev_gateway.py`:
  `booking_happy_path()` yields partials with rising stability then a final
  per caller turn; a `TtsSpeak` gets started/mark/finished playback events.
  Implement `src/lucy/transport/dev_gateway.py` paced via the injected
  clock. Verify: `.venv/bin/python -m pytest tests/test_dev_gateway.py -q`
  -> all pass.
- [ ] **C5 - VoiceSession state machine.** Test first in
  `tests/test_voice_session.py`: happy turn walks IDLE -> LISTENING ->
  THINKING -> SPEAKING -> IDLE and yields one `TurnRecord` with a waterfall;
  barge-in during SPEAKING cancels the turn task (assert no orphan tasks via
  `asyncio.all_tasks()`), truncates by `mark_chars`, sets
  `interrupted=True`. Implement `src/lucy/session.py`. Verify:
  `.venv/bin/python -m pytest tests/test_voice_session.py -q` -> all pass.
- [ ] **C6 - Span tree.** Test first in `tests/test_tracing.py`: one turn
  emits session/turn spans with correct parent ids through an
  `InMemoryTraceExporter` (from `lucy.testing`). Implement
  `src/lucy/tracing.py` and wire emission in `VoiceSession`. Verify:
  `.venv/bin/python -m pytest tests/test_tracing.py -q` -> all pass.
- [ ] **C7 - Harness end-to-end.** Test first in `tests/test_harness.py`:
  `ConversationHarness.run(booking_happy_path(), canned_responder)` returns
  a transcript whose caller lines match the scenario and one waterfall per
  turn. Implement `src/lucy/harness.py`. Verify:
  `.venv/bin/python -m pytest tests/test_harness.py -q` -> all pass.
- [ ] **C8 - Full suite + bookkeeping.** Run everything, fill "Improvements
  noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); the gateway simulator
  and `ManualClock` are the sanctioned test doubles - they are real
  implementations of real contracts.
- Do not hardcode budgets, thresholds, or provider names; everything timing
  lives in `LatencyBudgets` (agents.md).
- Do not carry audio bytes in any control-channel message (ADR 0004).
- Do not modify `GraphExecutor` cancellation logic or `voice.py` event
  dataclasses; reuse them.
- Do not implement LLM calls, tools, or speculation here (cards 33, 34, 36).
- Do not check a box without running its Verify command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_transport_schema.py tests/test_clock.py tests/test_settings.py tests/test_dev_gateway.py tests/test_voice_session.py tests/test_tracing.py tests/test_harness.py -q` -> all pass
- [ ] `.venv/bin/python -m pytest -q` -> full suite green, zero warnings
      introduced (use Docker Compose `docker compose run --rm lucy-api
      pytest` when the daemon is available)
- [ ] `grep -rn "sleep(0\.\|time.sleep" tests/test_voice_session.py
      tests/test_dev_gateway.py` -> no real sleeps in timing tests
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
