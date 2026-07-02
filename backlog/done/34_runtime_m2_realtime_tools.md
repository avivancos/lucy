# 34 - Voice runtime M2: real-time MCP tools in conversation

**Sprint:** S2 - Voice runtime core
**Epic:** Voice runtime
**Estimated effort:** ~8 h
**Depends on:** 33
**State:** pending

## Goal

Tools execute mid-utterance without dead air (ADR 0011): filler speech masks
MCP latency, tool results resume generation in a new LLM round, and the
existing `McpClient` permissions, schema validation, and audit stay intact
and untouched.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  budgets or thresholds).
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the decision this card
  implements: tool calls execute through the existing `McpClient` wrapped by
  a tool executor with per-tool profiles (deadline, barge-in policy, filler
  utterances that mask tool latency).
- `docs/adr/0003-no-mocks-testing-policy.md` - MCP tests use local protocol
  transports; simulators are the sanctioned doubles.
- `docs/adr/0010-open-core-split.md` - everything in this card is open SDK
  code; telemetry leaves only through the `lucy.observe` seam.
- `src/lucy/mcp.py` - `McpClient`, `LocalMcpCommandTransport`,
  `McpToolSchema`, `McpPermissionError`, `McpSchemaError`, `McpTimeoutError`,
  `McpAuditEvent`. Reused UNCHANGED; this card only wraps it.
- `src/lucy/metrics.py` - `LatencyWaterfall.mcp_tools_ms` and
  `CostBreakdown.mcp_tool_cost`; your per-round accrual fills the former.
- `backlog/pending/33_runtime_m1_streaming_llm.md` - the dependency card that
  creates `src/lucy/llm.py` (`ToolCallDelta`, `ToolCallReady`,
  `LocalLlmSimulator`, `stream_chat`), `src/lucy/speech.py`
  (`SentenceAssembler`), and `src/lucy/drivers.py` (`CascadedTurnDriver`).
  Those files exist once card 33 is done; this card extends them.
- `backlog/pending/32_runtime_m0_walking_skeleton.md` - the card that creates
  `src/lucy/settings.py` (`LatencyBudgets.max_tool_rounds_per_turn`),
  `src/lucy/clock.py` (`Clock`, `ManualClock`), and `src/lucy/session.py`
  (`VoiceSession`, `TurnRecord`).
- `tests/test_registry_mcp_metrics.py` - existing `McpClient` tests; copy the
  house style and the `SlowMcpTransport` deadline pattern for the timeout
  test.

## Spec

### Tool contracts - `src/lucy/tools.py` (new)

- `BargeInPolicy(str, Enum)`: `CANCEL = "cancel"`,
  `RUN_TO_COMPLETION = "run_to_completion"`. This card only DEFINES the
  field; enforcement during barge-in is card 35.
- `ToolProfile` (frozen dataclass): `expected_latency_ms: int`,
  `deadline_ms: int`, `on_barge_in: BargeInPolicy = BargeInPolicy.CANCEL`,
  `speak_filler: bool = False`.
- `ToolDef` (frozen dataclass): `server: str`, `name: str`,
  `description: str`, `json_schema: dict`, `profile: ToolProfile`; property
  `key -> str` returning `"<server>.<name>"` - the exact key format
  `McpClient.allowed_tools` matches against.
- `ToolResult` (dataclass): `tool_key: str`, `ok: bool`, `value: Any = None`,
  `error_kind: str = ""` - one of `""`, `"permission"`, `"schema"`,
  `"timeout"`, `"unknown_tool"`, `"budget"` - `error: str = ""`,
  `elapsed_ms: float = 0.0`; method `to_llm_message() -> dict` rendering a
  `role="tool"` message whose content carries the JSON-encoded value or the
  typed error, so the LLM can recover verbally.
- `FillerPolicy(fillers: Mapping[str, Sequence[str]])` - locale ->
  utterances; the default catalog is the module-level named constant
  `DEFAULT_FILLERS` (no filler strings anywhere else).
  `filler_for(tool: ToolDef, locale: str) -> str | None` returns `None` when
  `tool.profile.speak_filler` is false or the locale has no entry; otherwise
  a deterministic pick (rotate per call) so tests are reproducible.
- `McpToolExecutor(client: McpClient, clock: Clock,
  emit: Callable[[dict], None] | None = None)` with
  `async execute(tool: ToolDef, arguments: dict) -> ToolResult`:
  - Delegates to `McpClient.call_tool(tool.server, tool.name, arguments,
    timeout_ms=tool.profile.deadline_ms)`. Permissions, schema validation,
    and audit happen INSIDE `McpClient`, reused unchanged. Never call
    `transport.call_tool` directly.
  - Catches `McpPermissionError` / `McpSchemaError` / `McpTimeoutError` and
    converts each into a `ToolResult` with the matching `error_kind`
    (`"permission"` / `"schema"` / `"timeout"`); never re-raises to the
    driver.
  - Measures `elapsed_ms` with the injected `Clock.monotonic()`.
  - Emits exactly one `tool_call` telemetry event per call through `emit`,
    a dict with keys `server, tool, ok, error_kind, elapsed_ms`.

### Driver tool rounds - `src/lucy/drivers.py` (extend)

`CascadedTurnDriver` gains constructor params
`tool_executor: McpToolExecutor | None = None`,
`tools: Sequence[ToolDef] = ()`,
`filler_policy: FillerPolicy | None = None`, `locale: str` (passed by the
caller, no hardcoded default locale). Tool-round behavior, on
`ToolCallReady(name, arguments, call_id)` in the stream:

1. Resolve `name` against `tools` by `ToolDef.name`. Unknown name produces
   `ToolResult(error_kind="unknown_tool")` with no execution; the round
   still counts toward the cap.
2. If `filler_policy.filler_for(tool, locale)` returns text AND no
   `SentenceAssembler` sentence is currently buffered or playing, emit
   exactly ONE `tts.speak` filler directive for this round. Never more than
   one filler per round; none when something is already speakable.
3. Execute `tool_executor.execute(tool, arguments)` concurrently with the
   filler playback (`asyncio` task), not serially after it.
4. Append `result.to_llm_message()` to the message list and call
   `stream_chat` again - the next tool round.
5. Rounds are bounded by `LatencyBudgets.max_tool_rounds_per_turn` (typed
   settings, card 32; never a literal in driver code). Once the cap is
   reached, a further tool request is NOT executed: append
   `ToolResult(error_kind="budget")` so the model must answer verbally, and
   let the final stream complete the turn.
6. Accrue each `ToolResult.elapsed_ms` into the current turn's
   `LatencyWaterfall.mcp_tools_ms`.

### Simulator support - `src/lucy/llm.py` (extend only if needed)

`LocalLlmSimulator` scripts must support a tool-call step: emit
`ToolCallDelta` fragments, then `ToolCallReady`, then `StreamEnd`; the next
`stream_chat` call whose messages contain the matching tool-result message
streams the scripted follow-up tokens. If card 33 already shipped this,
this card only adds the regression test.

### File scope

- Create: `src/lucy/tools.py`, `tests/test_realtime_tools.py`.
- Modify: `src/lucy/drivers.py` (tool-round continuation),
  `src/lucy/llm.py` (simulator tool-call scripting, only if missing).
- Modify (scope widened during execution, see Improvements noted):
  `src/lucy/session.py` (3-line `mcp_tools_ms` plumbing so the driver's
  accrued tool time reaches `LatencyWaterfall.mcp_tools_ms`, which only
  `_finalize` builds - Spec step 6 requires this and the driver alone cannot
  satisfy it) and `src/lucy/speech.py` (a one-line `SentenceAssembler.
  has_buffered()` accessor for the C5 filler-suppression check, cleaner than
  reaching `assembler._buffer` from the driver).
- `src/lucy/mcp.py` stays byte-identical. Nothing else is touched.

## Chips

- [x] **C1 - Tool contracts and filler policy.** Record
  `shasum src/lucy/mcp.py` in "Improvements noted" before coding. Write
  tests first in new `tests/test_realtime_tools.py`:
  `test_tool_def_key_matches_mcp_allowed_tools_format` and
  `test_filler_policy_silent_unless_profile_opts_in`. Then implement
  `src/lucy/tools.py` (`BargeInPolicy`, `ToolProfile`, `ToolDef`,
  `ToolResult`, `FillerPolicy`, `DEFAULT_FILLERS`). Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass
  (>=2 tests).
- [x] **C2 - Executor happy path over the real McpClient.** Test first:
  `test_executor_runs_tool_through_mcp_client_and_audits_allowed_call` -
  build `McpClient(LocalMcpCommandTransport(),
  allowed_tools=["crm.book_meeting"])`, execute one tool, assert
  `ToolResult.ok`, the queued command in the transport, one `allowed=True`
  `McpAuditEvent` in `client.audit_log`, and one captured `tool_call`
  telemetry dict. Implement `McpToolExecutor`. Files: `src/lucy/tools.py`,
  `tests/test_realtime_tools.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [x] **C3 - Typed error results.** Tests first:
  `test_denied_tool_yields_permission_result_and_audit_entry` (tool not in
  `allowed_tools`; `error_kind == "permission"`, audit `allowed=False`, no
  exception escapes `execute`),
  `test_deadline_yields_timeout_result` (transport awaiting a never-set
  `asyncio.Event` plus a few-ms `deadline_ms`, same pattern as
  `SlowMcpTransport` in `tests/test_registry_mcp_metrics.py`;
  `error_kind == "timeout"`), and
  `test_schema_violation_yields_schema_result` (client built with an
  `McpToolSchema`; `error_kind == "schema"`). Implement the except branches
  in `McpToolExecutor.execute`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [x] **C4 - Simulator scripted tool calls.** Test first:
  `test_simulator_streams_tool_call_then_scripted_followup` - script emits
  `ToolCallDelta` fragments then `ToolCallReady`; a second `stream_chat`
  whose messages include the tool-result message streams the scripted final
  answer. Extend `LocalLlmSimulator` in `src/lucy/llm.py` only if the test
  fails red; if card 33 already covers it, keep the test and note that in
  "Improvements noted". Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [x] **C5 - Tool round with concurrent filler.** Tests first:
  `test_tool_call_mid_stream_speaks_one_filler_and_completes_round` (with
  `ManualClock`: the filler `tts.speak` directive is emitted before the
  tool-result message is appended; the follow-up answer streams after;
  exactly one filler) and
  `test_no_filler_when_a_sentence_is_already_buffered`. Implement the
  tool-round continuation in `CascadedTurnDriver`
  (`src/lucy/drivers.py`). Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [x] **C6 - Round cap and waterfall accrual.** Tests first:
  `test_tool_rounds_capped_by_typed_budget` (simulator scripted to request a
  tool every round; with `LatencyBudgets(max_tool_rounds_per_turn=2)`
  exactly 2 executions happen, the third request gets
  `error_kind == "budget"`, and the turn still ends with spoken text) and
  `test_mcp_tools_ms_accrues_into_turn_waterfall` (sum of `elapsed_ms`
  lands in `LatencyWaterfall.mcp_tools_ms`). Files: `src/lucy/drivers.py`,
  `tests/test_realtime_tools.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [x] **C7 - Verbal recovery end to end.** Tests first:
  `test_denied_tool_recovers_verbally_audited_no_crash` (the scripted
  follow-up reacts to the permission result with an apology stream; assert
  the `allowed=False` audit entry, the spoken recovery text, and that no
  exception escapes the driver) and `test_timeout_recovers_verbally` (same
  shape for `error_kind == "timeout"`). Adjust driver wiring only as
  needed. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass
  (>=12 tests).
- [x] **C8 - Full suite + bookkeeping.** Run everything, confirm
  `shasum src/lucy/mcp.py` matches the value recorded in C1, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003);
  `LocalMcpCommandTransport`, `LocalLlmSimulator`, `ManualClock`, and a
  plain blocked-event transport in the test file are the sanctioned doubles
  - real implementations of real contracts.
- Do not hardcode deadlines, expected latencies, round caps, locales, or
  filler strings in driver or executor code; timing lives in `ToolProfile`
  and `LatencyBudgets` (typed settings), filler text only in
  `DEFAULT_FILLERS` or injected catalogs (agents.md).
- Do not modify `src/lucy/mcp.py` in any way: `McpClient` permissions,
  schema validation, audit, and `McpTimeoutError` are reused UNCHANGED.
  Never call `transport.call_tool` directly from executor or driver code -
  that bypasses permissions and audit.
- Do not add a second audit mechanism; `McpClient.audit_log` is the audit.
- Do not enforce `ToolProfile.on_barge_in` here - card 35 does; this card
  only defines the field.
- Do not implement speculation or prompt-cache plumbing (card 36).
- Do not speak more than one filler per tool round, and none when a
  sentence is already buffered or playing.
- Do not touch files outside the File scope list above (widened to
  `session.py` + `speech.py` per Improvements noted; `mcp.py` still frozen).
- Do not check a chip or Definition of Done box without running its Verify
  command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): the
  `tool_call` telemetry leaves only through the local `emit` callback /
  `lucy.observe` exporters; no ingest, storage, or dashboard code, and no
  Pili-specific tool catalogs.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all
      pass (>=12 tests): a mid-stream tool call produces filler speech and a
      completed tool round; a denied tool surfaces as verbal recovery,
      audited, no crash; rounds are capped by the typed budget; timeouts
      recover verbally; `mcp_tools_ms` accrues into the turn waterfall.
- [x] `.venv/bin/python -m pytest tests/test_realtime_tools.py
      tests/test_registry_mcp_metrics.py -q` -> all pass (existing
      `McpClient` behavior untouched).
- [x] `shasum src/lucy/mcp.py` -> identical to the checksum recorded in C1
      (`McpClient` reused unchanged).
- [x] `grep -rn "unittest.mock\|MagicMock\|mocker" tests/test_realtime_tools.py`
      -> no matches.
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon is
      available).
- [ ] Post-task audit done; follow-up cards raised for anything noticed.

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

- `shasum src/lucy/mcp.py` baseline recorded before coding (C1):
  `3906e485c0b5078f448b85614f7cbb29e8ef02ce`. `McpClient` must remain
  byte-identical; C8 re-checks this value.
- File scope widened to `session.py` (+`speech.py`): Spec step 6 requires tool
  time in `LatencyWaterfall.mcp_tools_ms`, but that waterfall is constructed
  only in `session._finalize`; a driver-only change would leave it permanently
  `0.0`. Minimal 3-line plumbing (`_ActiveTurn.mcp_tools_ms`, capture on the
  `TurnDriverReport` branch, use it in `_finalize`). `speech.py` gains a
  one-line `SentenceAssembler.has_buffered()` for filler suppression.
- Context-primer correction: `LocalMcpCommandTransport` moved to `lucy.testing`
  (card 22); importing it from `lucy.mcp` emits a `DeprecationWarning` that
  would trip the zero-warnings DoD. Tests import from `lucy.testing`.
- C4 confirmed a real extension (not test-only): `LocalLlmSimulator`/
  `ScriptedLlmTurn` had no tool-call support. Added a `tool_calls` field.
- `ToolCallsNotSupported` kept and now raised only when `tool_executor is
  None`, so card 33's `test_tool_call_event_raises_tool_calls_not_supported`
  stays green and the driver change is backward-compatible.

- C8 verification: `shasum src/lucy/mcp.py` == `3906e485c0b5078f448b85614f7cbb29e8ef02ce`
  (unchanged); full suite 227 passed, 3 pre-existing warnings, zero new;
  `tests/test_realtime_tools.py` 20/20 stress runs green.
- Lint/typecheck: `ruff`/`mypy` are not installed in the Docker image and the
  repo has no ruff config or ruff-formatted baseline yet - that is exactly
  card 63 (`lint_and_typecheck_in_docker`). `ruff check` on the card-34 files
  adds no new errors (the one F401 it reports, `ControlEvent` in `session.py`,
  is pre-existing from card 32). Deferred `ruff format`/`mypy` to card 63 to
  avoid a scope-violating repo-wide reformat of card 32/33 code.

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

Five-reviewer roster + adversarial verification of every P0-P2 finding (roster
+ tiers per `CLAUDE.md`). Final: 227 passed in Docker; `mcp.py` byte-identical;
`test_realtime_tools.py` 20/20 stress-green.

- **code-reviewer** (sonnet) - **PASS.** Round cap, filler concurrency
  (`create_task` before the filler yield, awaited same round), backward-compat
  `ToolCallsNotSupported`, `llm_ms`/`mcp_tools_ms` separation, and
  `LlmMessage(**to_llm_message())` validity all verified.
- **test-auditor** (sonnet) - **FAIL -> resolved.** `[P1] test-001`: the
  `unknown_tool` branch (`drivers.py`) had zero coverage - mutation-confirmed
  (deleting it left all tests green). FIXED with
  `test_unknown_tool_name_yields_unknown_tool_result_no_execution` (asserts no
  execution, no audit, and the typed result fed back; deleting the branch now
  raises `KeyError` and fails the test).
- **simplicity-reviewer** (sonnet) - **PASS.** `[P3] simp-001` (guard
  duplicated across the `ToolCallDelta`/`ToolCallReady` branches) FIXED (merged
  into one branch). `[P3] simp-002` (extract the tool-round block) REJECTED:
  the block interleaves the filler `yield` with `create_task`/`await`, so it
  must stay in the async generator; extraction "is a wash" (reviewer's words).
- **docs-reviewer** (haiku) - **FAIL -> both findings refuted.** `docs-001`
  (telemetry comment "misleading") and `docs-002` (`locale=""` "contradicts
  spec") were both REFUTED by the opus verifier: the comment's claims are
  literally true, and `locale=""` is the absent-sentinel (`filler_for` returns
  `None`), not a hardcoded locale - and it is required for card 33
  backward-compat. `[P3] docs-003/004` (missing `BargeInPolicy`/`ToolProfile`
  docstrings) FIXED.
- **security-reviewer** (opus) - **PASS.** `mcp.py` byte-identical verified;
  the executor calls `McpClient.call_tool` only (never the transport); no
  second audit mechanism; the `emit` dict is exactly the five non-PII keys.
  `[P3] sec-001` (the `emit` seam is a parallel export path not governed by the
  wire-spec redaction; safe today) disposition: safe by construction, and the
  comment now warns against widening the dict with unredacted payload; routing
  driver tool telemetry through `lucy.observe` is a possible future
  defence-in-depth follow-up.
- `[P3] code-001` (intermittent `test_realtime_tools` failure seen twice
  *during* the parallel review): NOT REPRODUCED in a clean run (20/20 stress +
  full suite green); attributed to other review agents running mutation probes
  against `drivers.py` while the full suite ran concurrently.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
