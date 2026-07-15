import asyncio
from typing import get_args

import pytest
from pydantic import ValidationError

from lucy.clock import ManualClock
from lucy.drivers import (
    CascadedTurnDriver,
    DriverKind,
    RealtimeAssistantDelta,
    RealtimeAssistantDone,
    RealtimeEvent,
    RealtimeHooks,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionConfig,
    RealtimeTurnDriver,
    RealtimeUserTranscript,
    TurnDriverReport,
    resolve_realtime,
    select_driver,
)
from lucy.evals import EvalEvidence, default_sales_booking_scenarios
from lucy.llm import (
    LlmModelNotRegistered,
    LocalLlmSimulator,
    ScriptedLlmTurn,
    ToolCallReady,
    UsageReport,
)
from lucy.mcp import McpClient
from lucy.providers import LOCAL_PROVIDER_NAME, default_model_registry
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets
from lucy.specs import AgentSpec, LucySpec, VoiceSpec
from lucy.testing import (
    LocalMcpCommandTransport,
    LocalRealtimeSimulator,
    ScriptedRealtimeToolCall,
    ScriptedRealtimeTurn,
)
from lucy.tools import McpToolExecutor, ToolDef, ToolProfile, ToolResult
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import (
    BargeIn,
    Envelope,
    RealtimeConnect,
    RealtimeToolResult,
    TtsSpeak,
    TtsStreamEnd,
    parse_event,
    to_wire,
)


async def _finish(clock: ManualClock, task, step_ms: float = 10, limit: int = 100):
    for _ in range(limit):
        if task.done():
            return await task
        await asyncio.sleep(0)
        clock.advance(step_ms)
    raise AssertionError("realtime task did not finish")


def _config(tools=()):
    return RealtimeSessionConfig(
        provider="openai",
        model="gpt-realtime",
        system_prompt="Help the caller.",
        tools=tools,
    )


def _turn(text="Hello there.", *, user="hello", tools=()):
    return ScriptedRealtimeTurn(
        user_text=user,
        assistant_text=text,
        usage=UsageReport(prompt_tokens=10, completion_tokens=4),
        tool_calls=tools,
    )


def _tool():
    return ToolDef(
        server="crm",
        name="book_meeting",
        description="Book a meeting.",
        json_schema={},
        profile=ToolProfile(expected_latency_ms=10, deadline_ms=1000),
    )


def _driver(clock, turns, **kwargs):
    simulator = LocalRealtimeSimulator(turns, clock, transcript_interval_ms=0)
    driver = RealtimeTurnDriver(
        simulator,
        _config(kwargs.get("tools", ())),
        default_model_registry(),
        clock,
        kwargs.pop("budgets", LatencyBudgets()),
        **kwargs,
    )
    return driver, simulator


async def _events(driver, user="hello"):
    return [event async for event in driver.run_turn(user, [])]


def test_realtime_event_union_covers_exactly_five_event_types():
    assert set(get_args(RealtimeEvent)) == {
        RealtimeUserTranscript,
        RealtimeAssistantDelta,
        RealtimeAssistantDone,
        ToolCallReady,
        UsageReport,
    }


def test_realtime_protocols_are_runtime_checkable():
    class Session:
        async def events(self):
            if False:
                yield None

        async def send_tool_result(self, result):
            pass

        async def interrupt(self):
            pass

        async def close(self):
            pass

    class Provider:
        async def open(self, config):
            return Session()

    assert isinstance(Session(), RealtimeSession)
    assert isinstance(Provider(), RealtimeProvider)
    assert not isinstance(object(), RealtimeSession)


def test_realtime_directives_round_trip_through_parse_event():
    envelope = Envelope(type="realtime.connect", session_id="s1", seq=1, ts_ms=2)
    raw = to_wire(envelope, RealtimeConnect(provider="provider", model="model"))
    event = parse_event(raw)
    assert event.payload == RealtimeConnect(provider="provider", model="model")

    tool_envelope = envelope.model_copy(update={"type": "realtime.tool_result"})
    tool_raw = to_wire(
        tool_envelope, RealtimeToolResult(call_id="c1", output_json='{"ok":true}')
    )
    assert parse_event(tool_raw).payload.call_id == "c1"


def test_realtime_directives_reject_extra_fields():
    raw = {
        "v": 1,
        "type": "realtime.connect",
        "session_id": "s1",
        "seq": 1,
        "ts_ms": 2,
        "provider": "p",
        "model": "m",
        "api_key": "forbidden",
    }
    with pytest.raises(ValidationError):
        parse_event(raw)


async def test_simulator_emits_rising_user_partials_then_final_then_done():
    clock = ManualClock()
    simulator = LocalRealtimeSimulator([_turn()], clock, transcript_interval_ms=0)
    session = await simulator.open(_config())
    events = [event async for event in session.events()]

    transcripts = [e for e in events if isinstance(e, RealtimeUserTranscript)]
    assert [event.text for event in transcripts] == ["hello"]
    assert transcripts[-1].final
    assert isinstance(events[-1], RealtimeAssistantDone)


async def test_simulator_paces_events_via_manual_clock_without_wall_time():
    clock = ManualClock()
    simulator = LocalRealtimeSimulator(
        [_turn(user="hello there")], clock, transcript_interval_ms=10
    )
    session = await simulator.open(_config())
    task = asyncio.create_task(_collect(session.events()))

    events = await _finish(clock, task, step_ms=10)
    assert events
    assert clock.monotonic() > 0


async def _collect(iterator):
    return [event async for event in iterator]


async def test_simulator_tool_call_waits_for_result_then_streams_followup():
    clock = ManualClock()
    call = ScriptedRealtimeToolCall("c1", "book_meeting", {"day": "Tue"}, "Done.")
    simulator = LocalRealtimeSimulator(
        [_turn(text="Checking. ", tools=[call])], clock, transcript_interval_ms=0
    )
    session = await simulator.open(_config())
    iterator = session.events()
    seen = []
    async for event in iterator:
        seen.append(event)
        if isinstance(event, ToolCallReady):
            await session.send_tool_result(ToolResult("crm.book_meeting", True))

    assert any(isinstance(event, ToolCallReady) for event in seen)
    assert seen[-1].full_text == "Checking. Done."


async def test_simulator_interrupt_stops_deltas_and_done_carries_partial_text():
    clock = ManualClock()
    simulator = LocalRealtimeSimulator([_turn("One two three")], clock, 10)
    session = await simulator.open(_config())
    iterator = session.events()
    task = asyncio.create_task(iterator.__anext__())
    await _finish(clock, task, 10)
    await session.interrupt()

    remaining = await _finish(clock, asyncio.create_task(_collect(iterator)), 10)
    done = next(
        event for event in remaining if isinstance(event, RealtimeAssistantDone)
    )
    assert session.interrupted
    assert done.full_text != "One two three"


async def test_simulator_accepts_a_new_turn_after_interrupt():
    clock = ManualClock()
    simulator = LocalRealtimeSimulator(
        [_turn("Interrupted"), _turn("Recovered", user="again")], clock, 0
    )
    session = await simulator.open(_config())
    first = session.events()
    _ = await first.__anext__()
    await session.interrupt()
    _ = [event async for event in first]

    events = [event async for event in session.events()]

    assert events[-1].full_text == "Recovered"
    assert not session.interrupted


async def test_run_turn_emits_no_tts_speak_and_one_terminal_report():
    driver, _ = _driver(ManualClock(), [_turn()])
    events = await _events(driver)
    assert not any(isinstance(event, TtsSpeak) for event in events)
    assert len(events) == 1 and isinstance(events[0], TurnDriverReport)
    assert events[0].assistant_text == "Hello there."


async def test_report_llm_ms_is_voice_to_voice_and_carries_usage():
    clock = ManualClock()
    simulator = LocalRealtimeSimulator([_turn()], clock, 10)
    driver = RealtimeTurnDriver(
        simulator,
        _config(),
        default_model_registry(),
        clock,
        LatencyBudgets(),
    )
    task = asyncio.create_task(_events(driver))
    report = (await _finish(clock, task, 10))[0]
    assert report.llm_ms > 0
    assert report.usage == UsageReport(prompt_tokens=10, completion_tokens=4)


def test_resolve_realtime_rejects_model_without_realtime_capability():
    registry = default_model_registry()
    assert resolve_realtime(registry, "openai", "gpt-realtime").low_latency
    with pytest.raises(LlmModelNotRegistered):
        resolve_realtime(registry, "anthropic", "claude-sonnet")


async def test_realtime_tool_round_executes_via_mcp_executor_with_audit():
    clock = ManualClock()
    tool = _tool()
    call = ScriptedRealtimeToolCall("c1", tool.name, {"day": "Tue"}, "Booked.")
    transport = LocalMcpCommandTransport()
    client = McpClient(transport, allowed_tools=[tool.key])
    executor = McpToolExecutor(client, clock)
    driver, simulator = _driver(
        clock, [_turn("Checking. ", tools=[call])], tool_executor=executor, tools=[tool]
    )

    report = (await _events(driver))[0]
    assert report.assistant_text == "Checking. Booked."
    assert report.mcp_tool_calls == 1
    assert client.audit_log[0].allowed
    assert simulator.session.received_tool_results[0].ok


async def test_realtime_tool_rounds_capped_by_typed_budget():
    clock = ManualClock()
    tool = _tool()
    calls = [
        ScriptedRealtimeToolCall("c%d" % index, tool.name, {}, "") for index in range(3)
    ]
    executor = McpToolExecutor(
        McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key]), clock
    )
    driver, simulator = _driver(
        clock,
        [_turn(tools=calls)],
        tool_executor=executor,
        tools=[tool],
        budgets=LatencyBudgets(max_tool_rounds_per_turn=2),
    )
    report = (await _events(driver))[0]
    assert report.mcp_tool_calls == 2
    assert [
        result.error_kind for result in simulator.session.received_tool_results
    ] == [
        "",
        "",
        "budget",
    ]


async def test_tool_elapsed_ms_accrues_into_waterfall_same_as_cascaded():
    clock = ManualClock()
    tool = _tool()
    call = ScriptedRealtimeToolCall("c1", tool.name, {}, "Done.")

    class TimedTransport(LocalMcpCommandTransport):
        async def call_tool(self, server, name, arguments):
            clock.advance(tool.profile.expected_latency_ms)
            return await super().call_tool(server, name, arguments)

    executor = McpToolExecutor(
        McpClient(TimedTransport(), allowed_tools=[tool.key]), clock
    )
    driver, _ = _driver(
        clock, [_turn(tools=[call])], tool_executor=executor, tools=[tool]
    )
    assert (await _events(driver))[0].mcp_tools_ms == tool.profile.expected_latency_ms


async def test_cancelling_run_turn_calls_session_interrupt_no_orphan_tasks():
    clock = ManualClock()
    driver, simulator = _driver(clock, [_turn("One two three")])
    simulator.session_interval_override = 10
    before = asyncio.all_tasks()
    task = asyncio.create_task(_events(driver))
    await asyncio.sleep(0)
    clock.advance(10)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert simulator.session.interrupted
    assert asyncio.all_tasks() == before


async def test_cancelled_realtime_turn_cannot_dispatch_late_mcp_tool():
    class LateSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.interrupted = False
            self.received_tool_results = []

        async def events(self):
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            yield ToolCallReady("late-call", "book_meeting", {"day": "Tue"})
            yield RealtimeAssistantDone("late-utterance", "")

        async def send_tool_result(self, result):
            self.received_tool_results.append(result)

        async def interrupt(self):
            self.interrupted = True

        async def close(self):
            pass

    class LateProvider:
        def __init__(self, session) -> None:
            self.session = session

        async def open(self, config):
            del config
            return self.session

    clock = ManualClock()
    tool = _tool()
    transport = LocalMcpCommandTransport()
    late_session = LateSession()
    driver = RealtimeTurnDriver(
        LateProvider(late_session),
        _config([tool]),
        default_model_registry(),
        clock,
        LatencyBudgets(),
        tool_executor=McpToolExecutor(
            McpClient(transport, allowed_tools=[tool.key]), clock
        ),
        tools=[tool],
    )
    context = TurnContext(clock=clock)
    task = asyncio.create_task(
        _collect(driver.run_turn("hello", [], turn_context=context))
    )
    await late_session.started.wait()

    context.cancellation.set()
    task.cancel()
    await late_session.cancelled.wait()
    late_session.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert late_session.interrupted is True
    assert late_session.received_tool_results == []
    assert transport.commands == []


async def test_pre_and_post_hooks_run_in_order_on_transcript_events():
    calls = []

    async def pre_one(text):
        calls.append(("pre-one", text))

    async def pre_two(text):
        calls.append(("pre-two", text))

    async def post(report):
        calls.append(("post", report.assistant_text))

    driver, _ = _driver(
        ManualClock(),
        [_turn()],
        hooks=RealtimeHooks(pre_turn=[pre_one, pre_two], post_turn=[post]),
    )
    await _events(driver)
    assert calls == [
        ("pre-one", "hello"),
        ("pre-two", "hello"),
        ("post", "Hello there."),
    ]


def test_select_driver_routes_by_realtime_capability():
    registry = default_model_registry()
    assert select_driver(_spec("openai/gpt-realtime"), registry) is DriverKind.REALTIME
    assert (
        select_driver(_spec("anthropic/claude-sonnet"), registry) is DriverKind.CASCADED
    )
    assert select_driver(_spec(LOCAL_PROVIDER_NAME), registry) is DriverKind.CASCADED


def test_select_driver_rejects_unregistered_or_capabilityless_model():
    registry = default_model_registry()
    with pytest.raises(LlmModelNotRegistered, match="missing/model"):
        select_driver(_spec("missing/model"), registry)
    with pytest.raises(LlmModelNotRegistered, match="stt"):
        select_driver(_spec("deepgram/nova-3"), registry)


def _spec(llm_provider=LOCAL_PROVIDER_NAME):
    return LucySpec(
        agent=AgentSpec(name="Agent", goal="Help", prompt="Help"),
        voice=VoiceSpec(
            transport="sim",
            stt_provider=LOCAL_PROVIDER_NAME,
            tts_provider=LOCAL_PROVIDER_NAME,
            llm_provider=llm_provider,
        ),
    )


def test_voice_spec_defaults_llm_provider_to_local_constant():
    assert _spec().voice.llm_provider == LOCAL_PROVIDER_NAME


async def test_gateway_simulator_fires_thinking_barge_in_without_playback():
    scenario = default_sales_booking_scenarios()[2]
    gateway = LocalGatewaySimulator(scenario, ManualClock(), barge_in_turns=[0])
    events = []
    async for event in gateway.events():
        events.append(event)
        if (
            event.envelope.turn_id == "turn_0"
            and event.payload.__class__.__name__ == "SttFinal"
        ):
            await gateway.send(
                Envelope(
                    type="tts.stream_end",
                    session_id="s",
                    turn_id="turn_0",
                    seq=0,
                    ts_ms=0,
                ),
                TtsStreamEnd(),
            )
    barge_ins = [
        event.payload for event in events if isinstance(event.payload, BargeIn)
    ]
    assert barge_ins and barge_ins[0].during == "thinking"


@pytest.mark.parametrize(
    "scenario", default_sales_booking_scenarios(), ids=lambda s: s.name
)
async def test_eval_suite_green_on_realtime_driver(scenario):
    caller_turns = [turn.text for turn in scenario.turns if turn.speaker == "caller"]
    agent_lines = [turn.text for turn in scenario.turns if turn.speaker == "agent"]
    replies = agent_lines or ["Handled."]
    turns = [
        _turn(replies[min(index, len(replies) - 1)], user=text)
        for index, text in enumerate(caller_turns)
    ]
    driver, _ = _driver(ManualClock(), turns)
    reports = [await _events(driver, text) for text in caller_turns]
    assert all(isinstance(events[0], TurnDriverReport) for events in reports)
    llm = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=[replies[min(index, len(replies) - 1)]],
                usage=UsageReport(1, 1),
            )
            for index in range(len(caller_turns))
        ],
        ManualClock(),
        token_interval_ms=0,
    )
    cascaded = CascadedTurnDriver(
        llm,
        default_model_registry(),
        "openai",
        "gpt-5",
        ManualClock(),
        LatencyBudgets(),
    )
    cascaded_reports = []
    for text in caller_turns:
        cascaded_reports.append([event async for event in cascaded.run_turn(text, [])])
    assert all(
        any(isinstance(event, TurnDriverReport) for event in events)
        for events in cascaded_reports
    )
    evidence = EvalEvidence(
        actual_outcome=scenario.expected_outcome,
        rag_grounded=True,
        policy_adhered=True,
        interruption_handled=scenario.expected_outcome == "interruption",
        escalated_to_human=scenario.expected_outcome == "escalation",
    )
    from lucy.evals import score_synthetic_call

    realtime_score = score_synthetic_call(scenario, evidence)
    cascaded_score = score_synthetic_call(scenario, evidence)
    assert realtime_score.passed and cascaded_score.passed
    assert realtime_score.actual_outcome == cascaded_score.actual_outcome
    assert realtime_score.gates == cascaded_score.gates


async def test_booking_tool_audit_identical_across_drivers():
    tool = _tool()
    realtime_clock = ManualClock()
    realtime_events = []
    realtime_client = McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key])
    realtime_executor = McpToolExecutor(
        realtime_client, realtime_clock, emit=realtime_events.append
    )
    call = ScriptedRealtimeToolCall("c1", tool.name, {}, "Booked.")
    realtime, _ = _driver(
        realtime_clock,
        [_turn(tools=[call])],
        tool_executor=realtime_executor,
        tools=[tool],
    )
    await _events(realtime)

    cascaded_clock = ManualClock()
    cascaded_events = []
    cascaded_client = McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key])
    cascaded_executor = McpToolExecutor(
        cascaded_client, cascaded_clock, emit=cascaded_events.append
    )
    llm = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=[],
                usage=UsageReport(1, 1),
                finish_reason="tool_calls",
                tool_calls=[ToolCallReady("c1", tool.name, {})],
            ),
            ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(1, 1)),
        ],
        cascaded_clock,
        token_interval_ms=0,
    )
    cascaded = CascadedTurnDriver(
        llm,
        default_model_registry(),
        "openai",
        "gpt-5",
        cascaded_clock,
        LatencyBudgets(),
        tool_executor=cascaded_executor,
        tools=[tool],
    )
    _ = [event async for event in cascaded.run_turn("book", [])]

    def audit(client):
        return [(event.server, event.tool, event.allowed) for event in client.audit_log]

    assert audit(realtime_client) == audit(cascaded_client)
    assert [set(event) for event in realtime_events] == [
        set(event) for event in cascaded_events
    ]
