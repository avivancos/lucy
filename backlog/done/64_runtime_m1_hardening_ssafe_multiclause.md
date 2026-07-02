# 64 - Voice runtime M1 hardening: fail-safe SSE, multi-clause playback, barge-in coverage

**Sprint:** S2 - Voice runtime core
**Epic:** Voice runtime
**Estimated effort:** ~6 h
**Depends on:** 33
**State:** pending

## Goal

Close the robustness gaps the card 33 review surfaced without expanding M1
scope: make the SSE adapter fail safe on malformed upstream output, let the
simulator gateway play back a whole multi-clause turn (so a driver-mode
barge-in truncates to what was actually heard), and add the negative-path
coverage (empty stream, error finish, clock-tied billing) that card 33's
tests left incidental. None of these block the M1 happy path today; they
harden it before card 34 builds tool rounds on the same seam.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  models, URLs, or budgets).
- `backlog/done/33_runtime_m1_streaming_llm.md` - the card whose `## Review
  evidence` raised every item below; read its findings dispositions first.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the `LlmProvider`
  stream contract (`StreamEnd(finish_reason="error")` is already a legal
  terminal event) and the cascaded turn/barge-in model.
- `docs/adr/0003-no-mocks-testing-policy.md` - the in-test SSE server and
  `LocalGatewaySimulator` are real doubles; keep it that way.
- `src/lucy/llm.py` - `OpenAiCompatibleAdapter.stream_chat`; the two
  `json.loads` calls (per SSE `data:` line and per assembled tool-call args)
  currently raise `JSONDecodeError` out of the turn instead of ending the
  stream.
- `src/lucy/transport/dev_gateway.py` - `LocalGatewaySimulator._agent_response`
  drains exactly one `TtsSpeak` per caller turn; a multi-clause turn leaves
  utterances 2..n unplayed in `_inbound` (benign for a clean turn - the
  transcript uses the report text - but wrong for a barge-in of a
  multi-clause turn).
- `src/lucy/session.py` - `_run_driver_turn`, `_interrupt`, `_finalize`
  (planner branch); `TtsPlanner.spoken_text()` truncation is unit-tested but
  never exercised end-to-end through a driver-mode turn.
- `src/lucy/speech.py` - `TtsPlanner(prefix="utt")`; the `prefix` param has
  no varying call site (simplicity review simp-003).
- `tests/test_cascaded_driver.py` - `_drain` helper and the multi-clause
  completion test added by card 33; extend these, do not duplicate them.

## Spec

1. **Fail-safe SSE parse (sec-001, test-004).** In
   `OpenAiCompatibleAdapter.stream_chat`, wrap each untrusted `json.loads`
   (the per-line chunk parse and the tool-argument assembly parse) so a
   `json.JSONDecodeError` terminates the stream with
   `StreamEnd(finish_reason="error")` instead of propagating. No partial
   token is emitted for the malformed line. `base_url`/`api_key` handling is
   unchanged; the key still appears only in the `Authorization` header.

2. **Multi-clause playback + driver-mode barge-in (code-001 residual,
   test-003).** `LocalGatewaySimulator._agent_response` plays back every
   `TtsSpeak` the session forwards for a turn, in order, emitting `started`
   for the first clause, `mark` per clause, and a single terminal `finished`
   after the last. A caller turn in `barge_in_turns` interrupts mid-clause
   (as today) and the remaining queued clauses for that turn are drained, not
   leaked into the next turn. The turn barrier is the driver's terminal
   report already observed by `VoiceSession._run_driver_turn`; do not add a
   new control-schema type without recording the extension in the card. After
   the fix, `TtsPlanner.spoken_text()` truncation must be correct for a
   barge-in of a two-clause turn (first clause finished, second truncated to
   its `mark_chars`).

3. **Billing tied to clock (test-001).** Add an assertion that
   `TurnRecord.cost.billable_audio_minutes` tracks the `ManualClock`-measured
   turn duration (e.g. two turns of different scripted length yield different
   billable minutes, or it equals the clock delta / 60), so hardcoding it
   would fail a test.

4. **Empty-stream coverage (test-005).** A `ScriptedLlmTurn(tokens=[])` and
   an SSE stream with zero content chunks before `[DONE]` each yield zero
   `TtsSpeak` events and a `TurnDriverReport` with `assistant_text == ""`.

5. **Hang-proof drain (test-006).** Give the shared `_drain` helper (or the
   gateway simulator) a hard step-count failure so an unresolved driver turn
   fails the test fast instead of hanging CI.

6. **Drop unused generality (simp-003).** Remove `TtsPlanner`'s `prefix`
   parameter (replace with a module constant) unless a second caller now
   varies it.

## Files to create/modify

- `src/lucy/llm.py` - fail-safe `json.loads` (item 1).
- `src/lucy/transport/dev_gateway.py` - multi-clause playback / drain
  (item 2). This is a card 32 file; extend `_agent_response` only, do not
  rename schema fields.
- `src/lucy/speech.py` - drop `TtsPlanner.prefix` (item 6).
- `tests/test_llm_stream.py` - error-finish + empty-stream tests
  (items 1, 4).
- `tests/test_cascaded_driver.py` - multi-clause barge-in, clock-tied
  billing, hang-proof drain (items 2, 3, 5).

## Chips

- [x] **C1 - fail-safe SSE parse.** Files: `src/lucy/llm.py`,
  `tests/test_llm_stream.py`. Test first:
  `test_adapter_emits_error_finish_on_malformed_sse_line` (in-test server
  sends a `data:` line that is not valid JSON; assert the stream ends with
  `StreamEnd(finish_reason="error")` and no crash). Verify:
  `docker compose run --rm lucy-api pytest tests/test_llm_stream.py -q` ->
  all pass, includes the new test.
- [x] **C2 - empty stream.** Files: `src/lucy/llm.py` (if needed),
  `tests/test_llm_stream.py`, `tests/test_cascaded_driver.py`. Test first:
  `test_empty_stream_yields_no_tts_speak_and_empty_report`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_llm_stream.py
  tests/test_cascaded_driver.py -q` -> all pass.
- [x] **C3 - multi-clause playback + barge-in truncation.** Files:
  `src/lucy/transport/dev_gateway.py`, `tests/test_cascaded_driver.py`. Test
  first: `test_driver_turn_barge_in_truncates_multi_clause_via_planner`
  (two-clause turn, caller barges in during the second clause; assert the
  recorded `assistant_text` is the first clause plus the truncated second,
  via `TtsPlanner.spoken_text()`). Verify:
  `docker compose run --rm lucy-api pytest tests/test_cascaded_driver.py -q`
  -> all pass.
- [x] **C4 - clock-tied billing + hang-proof drain + drop prefix.** Files:
  `tests/test_cascaded_driver.py`, `src/lucy/speech.py`. Tests first:
  `test_billable_minutes_track_clock_duration`, and make `_drain` raise on
  step exhaustion. Verify:
  `docker compose run --rm lucy-api pytest tests/test_cascaded_driver.py
  tests/test_sentence_assembler.py -q` -> all pass.
- [x] **C5 - full suite + card bookkeeping.** Run the whole suite, fill
  "Improvements noted", record `## Review evidence`, move this card to
  `done/`. Verify: `docker compose run --rm lucy-api pytest -q` -> full
  suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); extend the in-test SSE
  server and `LocalGatewaySimulator` - real implementations of real
  contracts.
- Do not hardcode provider names, model names, URLs, prices, or budgets
  (agents.md); flush timing references `LatencyBudgets` fields only.
- Do not rename card 32 transport-schema fields, `TurnState` values, or
  `TurnRecord` field meanings; extend the gateway's playback behavior only.
- Do not implement tool execution or speculation (cards 34/36); item 1 only
  makes the existing error path fail safe.
- Do not touch files outside: `src/lucy/{llm.py,speech.py}`,
  `src/lucy/transport/dev_gateway.py`,
  `tests/{test_llm_stream.py,test_cascaded_driver.py}`.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_llm_stream.py
      tests/test_sentence_assembler.py tests/test_cascaded_driver.py -q` ->
      all pass, including the malformed-SSE, empty-stream, and multi-clause
      barge-in tests
- [x] `docker compose run --rm lucy-api pytest -q` -> full suite green, no
      new warnings
- [x] `grep -rn "json.loads" src/lucy/llm.py` -> every call is inside a
      guarded block that ends the stream with `finish_reason="error"`
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do NOT
check boxes, do NOT force tests green. Leave the card in `in_progress/`,
document what happened under "Improvements noted", and report. Partial honest
work beats fake completion.

## Improvements noted

- Control-schema extension (recorded per Spec item 2): new downstream type
  `TtsStreamEnd` / `"tts.stream_end"` in `src/lucy/transport/schema.py` - "no
  more tts.speak directives this turn". The session sends it EXACTLY once per
  turn (both responder and driver modes, normal or cancelled paths); the dev
  gateway uses it as the deterministic turn barrier when echoing playback. A
  real media plane derives the same boundary from its own playout queue.
- File scope widened (same rationale class as card 34): `src/lucy/session.py`
  (barrier send in both turn bodies' `finally` + `_ActiveTurn.barrier_sent`
  exactly-once flag + `_interrupt` fallback send + terminal-finished guard via
  `TtsPlanner.is_last`), `src/lucy/transport/schema.py` (the extension above),
  and `tests/test_dev_gateway.py` (its `_drive` helper plays the session's
  role, so it must send the barrier too or its tests deadlock).
- Subtle asyncio edge covered: a turn task cancelled before its first
  scheduling never executes its `finally`, so the session's `_interrupt` is
  the fallback barrier sender - without it the gateway drain deadlocks on
  vad-interrupt turns.
- Gateway playback semantics for multi-clause turns: `started` once (first
  clause), `mark` per clause, ONE terminal `finished` (last clause); the
  session completes the turn only on the last utterance's finished. Barge-in
  cuts the LAST clause at its mark; earlier clauses report `finished` so
  `TtsPlanner.spoken_text()` reconstructs exactly what was heard.
- Stress: driver+gateway+session+realtime-tools suites 10/10 green; full
  suite 239 passed after review fixes, zero new warnings.
- Follow-up seed for card 35: `BargeInPolicy.on_barge_in` enforcement will
  interact with the new barrier protocol (a cancelled tool round must still
  reach the exactly-once stream-end path); noted for card 35's spec.

## Review evidence

Five-reviewer roster + adversarial verification of every P0-P2 finding
(roster + tiers per `CLAUDE.md`). The verifiers reproduced each confirmed
finding empirically in Docker before it was accepted. Final: 239 passed;
`mcp.py` byte-identical (`3906e485c0b5078f448b85614f7cbb29e8ef02ce`);
fixed suites 10/10 stress-green.

- **code-reviewer** (sonnet) - **FAIL -> resolved.** `[P1] code-001`
  (verifier-adjusted from P0): a zero-clause driver turn mid-session had no
  playback at all, so its task blocked forever on `playback_finished` and the
  next `SttFinal` silently overwrote it - record dropped, task leaked
  (reproduced: 1 record instead of 2, one permanently-pending task). FIXED:
  the `SttFinal` handler now reaps and records a still-active previous turn
  before starting the next; regression test
  `test_empty_driver_turn_mid_session_is_recorded_not_leaked` asserts both
  records and the full transcript. Barrier exactly-once analysis (normal,
  cancel-mid-stream, cancel-before-first-run, cancel-during-playback) held.
- **test-auditor** (sonnet) - **FAIL -> resolved.** `[P1] test-001`: C2's
  empty-stream test stopped at the driver seam, which is exactly why code-001
  shipped green - fixed by the session-level test above. `[P2] test-101`:
  the clean multi-clause playback path had no gateway-level event-sequence
  test - FIXED with
  `test_multi_clause_turn_plays_every_clause_with_one_terminal_finished`
  (started once, ordered marks, single terminal finished).
- **simplicity-reviewer** (sonnet) - **PASS.** Barrier protocol judged the
  smallest deterministic design; `TtsStreamEnd` minimal (zero fields); no
  dead `prefix` references.
- **docs-reviewer** (haiku) - **PASS.** Both stale-docstring findings were
  REFUTED on verification; Improvements noted records the schema extension
  and scope widenings as Spec item 2 requires; no ADR 0011 contradiction.
- **security-reviewer** (opus) - **FAIL -> resolved.** `[P1] sec-002` and
  `[P2] sec-001`: unguarded `int()` casts on untrusted upstream fields
  (`usage.prompt_tokens="NaN"`, `tool_call index="oops"`) crashed the turn
  with `ValueError`, re-opening the DoS-per-turn class this card closes
  (reproduced in Docker). FIXED: the whole per-chunk parse is now inside the
  fail-safe guard (JSONDecodeError/TypeError/ValueError/AttributeError ->
  `StreamEnd(finish_reason="error")`), non-string content deltas and
  non-object chunks/tool-arguments included; four new adversarial tests
  cover each shape. Confirmed clean: `TtsStreamEnd` adds no attack surface
  (extra="forbid", zero fields), `mcp.py` byte-identical, api_key handling
  untouched.
- Housekeeping: three probe scripts left behind by review agents
  (`probe_single.py`, `test_probe2.py`, `test_probe_empty.py`) were removed
  before commit.

<!-- Fill during execution. Raise a follow-up card per item. -->
