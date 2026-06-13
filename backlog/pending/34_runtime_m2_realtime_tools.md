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
- `src/lucy/mcp.py` stays byte-identical. Nothing else is touched.

## Chips

- [ ] **C1 - Tool contracts and filler policy.** Record
  `shasum src/lucy/mcp.py` in "Improvements noted" before coding. Write
  tests first in new `tests/test_realtime_tools.py`:
  `test_tool_def_key_matches_mcp_allowed_tools_format` and
  `test_filler_policy_silent_unless_profile_opts_in`. Then implement
  `src/lucy/tools.py` (`BargeInPolicy`, `ToolProfile`, `ToolDef`,
  `ToolResult`, `FillerPolicy`, `DEFAULT_FILLERS`). Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass
  (>=2 tests).
- [ ] **C2 - Executor happy path over the real McpClient.** Test first:
  `test_executor_runs_tool_through_mcp_client_and_audits_allowed_call` -
  build `McpClient(LocalMcpCommandTransport(),
  allowed_tools=["crm.book_meeting"])`, execute one tool, assert
  `ToolResult.ok`, the queued command in the transport, one `allowed=True`
  `McpAuditEvent` in `client.audit_log`, and one captured `tool_call`
  telemetry dict. Implement `McpToolExecutor`. Files: `src/lucy/tools.py`,
  `tests/test_realtime_tools.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [ ] **C3 - Typed error results.** Tests first:
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
- [ ] **C4 - Simulator scripted tool calls.** Test first:
  `test_simulator_streams_tool_call_then_scripted_followup` - script emits
  `ToolCallDelta` fragments then `ToolCallReady`; a second `stream_chat`
  whose messages include the tool-result message streams the scripted final
  answer. Extend `LocalLlmSimulator` in `src/lucy/llm.py` only if the test
  fails red; if card 33 already covers it, keep the test and note that in
  "Improvements noted". Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [ ] **C5 - Tool round with concurrent filler.** Tests first:
  `test_tool_call_mid_stream_speaks_one_filler_and_completes_round` (with
  `ManualClock`: the filler `tts.speak` directive is emitted before the
  tool-result message is appended; the follow-up answer streams after;
  exactly one filler) and
  `test_no_filler_when_a_sentence_is_already_buffered`. Implement the
  tool-round continuation in `CascadedTurnDriver`
  (`src/lucy/drivers.py`). Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [ ] **C6 - Round cap and waterfall accrual.** Tests first:
  `test_tool_rounds_capped_by_typed_budget` (simulator scripted to request a
  tool every round; with `LatencyBudgets(max_tool_rounds_per_turn=2)`
  exactly 2 executions happen, the third request gets
  `error_kind == "budget"`, and the turn still ends with spoken text) and
  `test_mcp_tools_ms_accrues_into_turn_waterfall` (sum of `elapsed_ms`
  lands in `LatencyWaterfall.mcp_tools_ms`). Files: `src/lucy/drivers.py`,
  `tests/test_realtime_tools.py`. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass.
- [ ] **C7 - Verbal recovery end to end.** Tests first:
  `test_denied_tool_recovers_verbally_audited_no_crash` (the scripted
  follow-up reacts to the permission result with an apology stream; assert
  the `allowed=False` audit entry, the spoken recovery text, and that no
  exception escapes the driver) and `test_timeout_recovers_verbally` (same
  shape for `error_kind == "timeout"`). Adjust driver wiring only as
  needed. Verify:
  `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all pass
  (>=12 tests).
- [ ] **C8 - Full suite + bookkeeping.** Run everything, confirm
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
- Do not touch files outside the File scope list above.
- Do not check a chip or Definition of Done box without running its Verify
  command.
- Do not put platform/Pili concerns inside the SDK (ADR 0010): the
  `tool_call` telemetry leaves only through the local `emit` callback /
  `lucy.observe` exporters; no ingest, storage, or dashboard code, and no
  Pili-specific tool catalogs.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_realtime_tools.py -q` -> all
      pass (>=12 tests): a mid-stream tool call produces filler speech and a
      completed tool round; a denied tool surfaces as verbal recovery,
      audited, no crash; rounds are capped by the typed budget; timeouts
      recover verbally; `mcp_tools_ms` accrues into the turn waterfall.
- [ ] `.venv/bin/python -m pytest tests/test_realtime_tools.py
      tests/test_registry_mcp_metrics.py -q` -> all pass (existing
      `McpClient` behavior untouched).
- [ ] `shasum src/lucy/mcp.py` -> identical to the checksum recorded in C1
      (`McpClient` reused unchanged).
- [ ] `grep -rn "unittest.mock\|MagicMock\|mocker" tests/test_realtime_tools.py`
      -> no matches.
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
