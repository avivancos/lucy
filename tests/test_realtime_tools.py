import asyncio
from typing import cast

import pytest

from lucy.clock import ManualClock
from lucy.llm import (
    LlmMessage,
    LlmRequest,
    LocalLlmSimulator,
    ScriptedLlmTurn,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
)
from lucy.drivers import CascadedTurnDriver, TurnDriverReport
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.harness import ConversationHarness
from lucy.mcp import McpClient, McpToolSchema
from lucy.providers import default_model_registry
from lucy.settings import LatencyBudgets
from lucy.testing import LocalMcpCommandTransport
from lucy.transport.schema import TtsSpeak
from lucy.tools import (
    DEFAULT_FILLERS,
    BargeInPolicy,
    FillerPolicy,
    McpToolExecutor,
    ToolDef,
    ToolProfile,
    ToolResult,
)


def _profile(**kw) -> ToolProfile:
    base: dict[str, object] = {
        "expected_latency_ms": 100,
        "deadline_ms": 1000,
        "on_barge_in": BargeInPolicy.CANCEL,
        "speak_filler": False,
    }
    base.update(kw)
    return ToolProfile(
        expected_latency_ms=cast(int, base["expected_latency_ms"]),
        deadline_ms=cast(int, base["deadline_ms"]),
        on_barge_in=cast(BargeInPolicy, base["on_barge_in"]),
        speak_filler=cast(bool, base["speak_filler"]),
    )


def _tool(server="crm", name="book_meeting", **profile_kw) -> ToolDef:
    return ToolDef(
        server=server,
        name=name,
        description="",
        json_schema={},
        profile=_profile(**profile_kw),
    )


# -- C1: tool contracts + filler policy --------------------------------------


def test_tool_def_key_matches_mcp_allowed_tools_format():
    tool = _tool(server="crm", name="book_meeting")
    assert tool.key == "crm.book_meeting"
    # The key is exactly what McpClient.allowed_tools is matched against.
    client = McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key])
    assert tool.key in client.allowed_tools


def test_filler_policy_silent_unless_profile_opts_in():
    policy = FillerPolicy(DEFAULT_FILLERS)
    quiet = _tool(speak_filler=False)
    speaks = _tool(speak_filler=True)

    assert policy.filler_for(quiet, "en-US") is None  # profile opted out
    assert policy.filler_for(speaks, "zz-ZZ") is None  # locale absent
    assert policy.filler_for(speaks, "en-US") in DEFAULT_FILLERS["en-US"]


def test_filler_policy_rotates_deterministically():
    policy = FillerPolicy(DEFAULT_FILLERS)
    speaks = _tool(speak_filler=True)
    picks = [policy.filler_for(speaks, "en-US") for _ in range(4)]
    expected = list(DEFAULT_FILLERS["en-US"])
    assert picks == [expected[0], expected[1], expected[2], expected[0]]


def test_barge_in_policy_default_is_cancel():
    assert _tool().profile.on_barge_in is BargeInPolicy.CANCEL


def test_tool_result_to_llm_message_shape_is_llm_message_legal():
    from lucy.llm import LlmMessage

    ok = ToolResult(tool_key="crm.book_meeting", ok=True, value={"id": 7})
    msg = ok.to_llm_message()
    assert set(msg) == {"role", "content", "tool_call_id"}
    assert msg["role"] == "tool"
    # constructs without ValidationError under extra="forbid"
    assert LlmMessage(**msg).tool_call_id == "crm.book_meeting"

    bad = ToolResult(tool_key="crm.x", ok=False, error_kind="permission", error="no")
    payload = bad.to_llm_message()["content"]
    assert '"ok": false' in payload and '"error_kind": "permission"' in payload


# -- C2: executor happy path over the real McpClient -------------------------


async def test_executor_runs_tool_through_mcp_client_and_audits_allowed_call():
    transport = LocalMcpCommandTransport()
    client = McpClient(transport, allowed_tools=["crm.book_meeting"])
    emitted: list = []
    executor = McpToolExecutor(client, ManualClock(), emit=emitted.append)
    tool = _tool()

    result = await executor.execute(tool, {"lead_id": "lead_demo"})

    assert result.ok and result.error_kind == ""
    assert result.tool_key == "crm.book_meeting"
    assert transport.commands[0]["tool"] == "book_meeting"  # queued command
    assert len(client.audit_log) == 1 and client.audit_log[0].allowed is True
    # exactly one telemetry dict, exactly the five allowed keys, no arguments
    assert len(emitted) == 1
    assert set(emitted[0]) == {"server", "tool", "ok", "error_kind", "elapsed_ms"}
    assert emitted[0]["ok"] is True and emitted[0]["error_kind"] == ""


# -- C3: typed error results (no exception escapes execute) ------------------


async def test_denied_tool_yields_permission_result_and_audit_entry():
    client = McpClient(LocalMcpCommandTransport(), allowed_tools=[])
    executor = McpToolExecutor(client, ManualClock())

    result = await executor.execute(_tool(), {})  # not allowed -> no raise

    assert not result.ok and result.error_kind == "permission"
    assert client.audit_log[0].allowed is False


# -- C4: simulator scripted tool calls ---------------------------------------


def _req(*messages) -> LlmRequest:
    msgs = messages or (LlmMessage(role="user", content="hi"),)
    return LlmRequest(provider="openai", model="gpt-5", messages=list(msgs))


async def test_simulator_streams_tool_call_then_scripted_followup():
    clock = ManualClock()
    ready = ToolCallReady(call_id="c1", name="book_meeting", arguments={"day": "tue"})
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=[],
                usage=UsageReport(5, 0),
                finish_reason="tool_calls",
                tool_calls=[ready],
            ),
            ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(6, 2)),
        ],
        clock,
        token_interval_ms=0,  # sleep(0) returns immediately, no drain needed
    )

    events1 = [e async for e in sim.stream_chat(_req())]
    assert any(isinstance(e, ToolCallDelta) for e in events1)
    ready_event = next(e for e in events1 if isinstance(e, ToolCallReady))
    assert ready_event.name == "book_meeting" and ready_event.arguments == {
        "day": "tue"
    }
    assert (
        isinstance(events1[-1], StreamEnd) and events1[-1].finish_reason == "tool_calls"
    )

    # the next stream_chat (messages now carry the tool result) streams the answer
    followup = _req(
        LlmMessage(role="user", content="book tue"),
        LlmMessage(
            role="tool", content='{"ok": true}', tool_call_id="crm.book_meeting"
        ),
    )
    events2 = [e async for e in sim.stream_chat(followup)]
    assert [e.text for e in events2 if isinstance(e, TokenDelta)] == ["Booked."]
    assert isinstance(events2[-1], StreamEnd) and events2[-1].finish_reason == "stop"


class ImmediateTimeoutTransport:
    async def call_tool(self, server, tool, arguments):
        raise asyncio.TimeoutError


async def test_deadline_yields_timeout_result():

    client = McpClient(ImmediateTimeoutTransport(), allowed_tools=["crm.slow"])
    executor = McpToolExecutor(client, ManualClock())
    tool = _tool(server="crm", name="slow", deadline_ms=1)

    result = await executor.execute(tool, {})

    assert not result.ok and result.error_kind == "timeout"
    assert client.audit_log[0].error == "deadline exceeded"


async def test_schema_violation_yields_schema_result():
    client = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=["crm.upsert"],
        tool_schemas={"crm.upsert": McpToolSchema(required_fields={"lead_id": str})},
    )
    executor = McpToolExecutor(client, ManualClock())
    tool = _tool(server="crm", name="upsert")

    result = await executor.execute(tool, {})  # missing lead_id

    assert not result.ok and result.error_kind == "schema"
    assert client.audit_log[0].allowed is True  # allowed, then schema-rejected


# -- C5/C6/C7 driver tool rounds ---------------------------------------------

_FILLER_STRINGS = {text for options in DEFAULT_FILLERS.values() for text in options}


class RecordingProvider:
    """Real LlmProvider decorator (ADR 0003, not a mock): records the messages
    each stream_chat call receives, then delegates to the wrapped provider."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.requests: list = []

    async def stream_chat(self, request):
        self.requests.append(list(request.messages))
        async for event in self._inner.stream_chat(request):
            yield event


class ClockAdvancingTransport:
    """Real transport that advances virtual time by a fixed amount per call,
    so tool elapsed_ms is deterministic under a ManualClock."""

    def __init__(self, clock: ManualClock, advance_ms: float) -> None:
        self._clock = clock
        self._advance_ms = advance_ms
        self.commands: list = []

    async def call_tool(self, server, tool, arguments):
        self._clock.advance(self._advance_ms)
        command = {"server": server, "tool": tool, "arguments": arguments}
        self.commands.append(command)
        return command


def _mk_driver(
    clock,
    llm,
    *,
    tools=(),
    executor=None,
    filler_policy=None,
    locale="",
    budgets=None,
    min_flush=1,
):
    return CascadedTurnDriver(
        llm,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        budgets or LatencyBudgets(),
        min_flush_chars=min_flush,
        tool_executor=executor,
        tools=tools,
        filler_policy=filler_policy,
        locale=locale,
    )


def _tool_turn(call):
    return ScriptedLlmTurn(
        tokens=[],
        usage=UsageReport(1, 0),
        finish_reason="tool_calls",
        tool_calls=[call],
    )


async def test_tool_call_mid_stream_speaks_one_filler_and_completes_round():
    clock = ManualClock()
    call = ToolCallReady(call_id="c1", name="book_meeting", arguments={"day": "tue"})
    sim = LocalLlmSimulator(
        [
            _tool_turn(call),
            ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(2, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    transport = LocalMcpCommandTransport()
    client = McpClient(transport, allowed_tools=["crm.book_meeting"])
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(
        clock,
        sim,
        tools=[_tool(speak_filler=True)],
        executor=executor,
        filler_policy=FillerPolicy(DEFAULT_FILLERS),
        locale="en-US",
    )

    events = [e async for e in driver.run_turn("book tue", [])]
    tts = [e for e in events if isinstance(e, TtsSpeak)]
    fillers = [e for e in tts if e.text in _FILLER_STRINGS]

    assert len(fillers) == 1  # exactly one filler this round
    assert tts[0].text in _FILLER_STRINGS  # filler spoken before the answer clause
    report = next(e for e in events if isinstance(e, TurnDriverReport))
    assert report.assistant_text == "Booked."  # follow-up answer completed the round
    assert report.usage == UsageReport(3, 1)
    assert report.mcp_tool_calls == 1
    assert transport.commands[0]["tool"] == "book_meeting"  # tool actually executed


async def test_no_filler_when_a_sentence_is_already_buffered():
    clock = ManualClock()
    call = ToolCallReady(call_id="c1", name="book_meeting", arguments={})
    # "Let me " has no boundary -> still buffered when the tool call arrives
    tool_turn = ScriptedLlmTurn(
        tokens=["Let me "],
        usage=UsageReport(1, 0),
        finish_reason="tool_calls",
        tool_calls=[call],
    )
    sim = LocalLlmSimulator(
        [tool_turn, ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(2, 1))],
        clock,
        token_interval_ms=0,
    )
    client = McpClient(LocalMcpCommandTransport(), allowed_tools=["crm.book_meeting"])
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(
        clock,
        sim,
        tools=[_tool(speak_filler=True)],
        executor=executor,
        filler_policy=FillerPolicy(DEFAULT_FILLERS),
        locale="en-US",
        min_flush=8,
    )

    events = [e async for e in driver.run_turn("x", [])]
    tts = [e for e in events if isinstance(e, TtsSpeak)]

    assert [e for e in tts if e.text in _FILLER_STRINGS] == []  # suppressed
    report = next(e for e in events if isinstance(e, TurnDriverReport))
    assert report.assistant_text == "Let me Booked."


async def test_tool_rounds_capped_by_typed_budget():
    clock = ManualClock()
    call = ToolCallReady(call_id="c", name="book_meeting", arguments={})
    sim = LocalLlmSimulator(
        [
            _tool_turn(call),
            _tool_turn(call),
            _tool_turn(call),
            ScriptedLlmTurn(tokens=["All ", "done."], usage=UsageReport(2, 2)),
        ],
        clock,
        token_interval_ms=0,
    )
    recorder = RecordingProvider(sim)
    transport = LocalMcpCommandTransport()
    client = McpClient(transport, allowed_tools=["crm.book_meeting"])
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(
        clock,
        recorder,
        tools=[_tool()],
        executor=executor,
        budgets=LatencyBudgets(max_tool_rounds_per_turn=2),
    )

    events = [e async for e in driver.run_turn("go", [])]
    report = next(e for e in events if isinstance(e, TurnDriverReport))

    assert report.assistant_text == "All done."  # turn still ends with spoken text
    assert report.usage == UsageReport(5, 2)
    assert report.mcp_tool_calls == 2
    assert len(transport.commands) == 2  # exactly max_tool_rounds_per_turn executions
    tool_msgs = [m for msgs in recorder.requests for m in msgs if m.role == "tool"]
    assert any('"error_kind": "budget"' in m.content for m in tool_msgs)  # 3rd refused


async def test_unknown_tool_name_yields_unknown_tool_result_no_execution():
    # The model requests a tool never registered with the driver: no execution,
    # no audit, a typed unknown_tool result fed back for verbal recovery.
    clock = ManualClock()
    call = ToolCallReady(call_id="c", name="ghost_tool", arguments={})
    sim = LocalLlmSimulator(
        [
            _tool_turn(call),
            ScriptedLlmTurn(
                tokens=["Sorry, ", "unavailable."], usage=UsageReport(1, 2)
            ),
        ],
        clock,
        token_interval_ms=0,
    )
    recorder = RecordingProvider(sim)
    transport = LocalMcpCommandTransport()
    client = McpClient(transport, allowed_tools=["crm.book_meeting"])
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(
        clock, recorder, tools=[_tool()], executor=executor
    )  # only book_meeting

    events = [e async for e in driver.run_turn("use the ghost tool", [])]
    report = next(e for e in events if isinstance(e, TurnDriverReport))

    assert report.assistant_text == "Sorry, unavailable."  # recovered verbally
    assert report.mcp_tool_calls == 0
    assert transport.commands == []  # unknown tool never executed
    assert client.audit_log == []  # no client call -> no audit entry
    tool_msgs = [m for msgs in recorder.requests for m in msgs if m.role == "tool"]
    assert any('"error_kind": "unknown_tool"' in m.content for m in tool_msgs)


async def test_mcp_tools_ms_accrues_into_turn_waterfall():
    clock = ManualClock()
    call = ToolCallReady(call_id="c", name="book_meeting", arguments={})
    sim = LocalLlmSimulator(
        [_tool_turn(call), ScriptedLlmTurn(tokens=["Done."], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=0,
    )
    advance_ms = 40.0
    transport = ClockAdvancingTransport(clock, advance_ms)
    client = McpClient(transport, allowed_tools=["crm.book_meeting"])
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(clock, sim, tools=[_tool()], executor=executor)
    scenario = SyntheticCallScenario(
        name="one_turn",
        objective="x",
        turns=[SyntheticTurn(speaker="caller", text="hello")],
        expected_outcome="booked",
    )

    result = await asyncio.wait_for(
        ConversationHarness().run(scenario, None, clock=clock, driver=driver),
        timeout=5,
    )
    waterfall = result.turn_records[0].waterfall
    assert waterfall.mcp_tools_ms == pytest.approx(advance_ms)  # session-level accrual
    assert waterfall.llm_ms == pytest.approx(0.0)  # tool time not counted as llm time


async def test_denied_tool_recovers_verbally_audited_no_crash():
    clock = ManualClock()
    call = ToolCallReady(call_id="c", name="book_meeting", arguments={})
    sim = LocalLlmSimulator(
        [
            _tool_turn(call),
            ScriptedLlmTurn(
                tokens=["Sorry, ", "I can't ", "do that."], usage=UsageReport(2, 3)
            ),
        ],
        clock,
        token_interval_ms=0,
    )
    client = McpClient(LocalMcpCommandTransport(), allowed_tools=[])  # tool denied
    executor = McpToolExecutor(client, clock)
    driver = _mk_driver(clock, sim, tools=[_tool()], executor=executor)

    events = [e async for e in driver.run_turn("book it", [])]  # no exception escapes
    report = next(e for e in events if isinstance(e, TurnDriverReport))

    assert report.assistant_text == "Sorry, I can't do that."  # verbal recovery
    assert report.mcp_tool_calls == 0
    assert client.audit_log[-1].allowed is False  # denial was audited


async def test_timeout_recovers_verbally():
    clock = ManualClock()
    call = ToolCallReady(call_id="c", name="slow", arguments={})
    sim = LocalLlmSimulator(
        [
            _tool_turn(call),
            ScriptedLlmTurn(
                tokens=["That ", "took ", "too long."], usage=UsageReport(2, 3)
            ),
        ],
        clock,
        token_interval_ms=0,
    )

    client = McpClient(ImmediateTimeoutTransport(), allowed_tools=["crm.slow"])
    executor = McpToolExecutor(client, clock)
    tool = _tool(server="crm", name="slow", deadline_ms=1)
    driver = _mk_driver(clock, sim, tools=[tool], executor=executor)

    events = [e async for e in driver.run_turn("do it", [])]
    report = next(e for e in events if isinstance(e, TurnDriverReport))

    assert report.assistant_text == "That took too long."  # verbal recovery
    assert report.mcp_tool_calls == 1
    assert client.audit_log[-1].error == "deadline exceeded"
