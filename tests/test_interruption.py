import asyncio

import pytest

from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver
from lucy.evals import (
    SyntheticCallScenario,
    SyntheticTurn,
    default_sales_booking_scenarios,
    score_synthetic_call,
)
from lucy.harness import ConversationHarness, evidence_from_result
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, ToolCallReady, UsageReport
from lucy.mcp import McpClient
from lucy.observe import Tracer
from lucy.pricing import PriceBook
from lucy.providers import default_model_registry
from lucy.session import VoiceSession, heard_assistant_text
from lucy.settings import LatencyBudgets
from lucy.testing import InMemoryTraceExporter
from lucy.tools import (
    DEFAULT_FILLERS,
    BargeInPolicy,
    FillerPolicy,
    McpToolExecutor,
    ToolDef,
    ToolProfile,
)
from lucy.transport.schema import (
    BargeIn,
    ControlEvent,
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    TtsCancel,
    TtsPlayback,
    TtsSpeak,
)


async def _canned(user_text: str) -> str:
    return "Response to: %s" % user_text


def _one_turn_scenario() -> SyntheticCallScenario:
    return SyntheticCallScenario(
        name="one_turn",
        objective="single turn",
        turns=[SyntheticTurn(speaker="caller", text="hello there")],
        expected_outcome="booked",
    )


async def _drain(clock: ManualClock, task, step_ms: float, max_steps: int = 200):
    for _ in range(max_steps):
        if task.done():
            break
        await asyncio.sleep(0)
        clock.advance(step_ms)
        await asyncio.sleep(0)
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise AssertionError("task did not complete")
    return await task


class ImmediateBargeGateway:
    """A real in-process control-channel transport for precise interruption edges."""

    def __init__(
        self,
        *,
        mark_chars: int = 1,
        before_mark: bool = False,
        after_finished: bool = False,
    ) -> None:
        self.sent: list[ControlEvent] = []
        self._inbound: asyncio.Queue[tuple[Envelope, object]] = asyncio.Queue()
        self._mark_chars = mark_chars
        self._before_mark = before_mark
        self._after_finished = after_finished

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.sent.append(ControlEvent(envelope, payload))
        await self._inbound.put((envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="s", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="+10000000000", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(type="stt.final", session_id="s", turn_id="t0", seq=2, ts_ms=10),
            SttFinal(text="hello", provider="local", stt_ms=10),
        )
        _, speak = await self._inbound.get()
        assert isinstance(speak, TtsSpeak)
        yield ControlEvent(
            Envelope(
                type="tts.playback", session_id="s", turn_id="t0", seq=3, ts_ms=20
            ),
            TtsPlayback(utterance_id=speak.utterance_id, state="started"),
        )
        if self._after_finished:
            yield ControlEvent(
                Envelope(
                    type="tts.playback",
                    session_id="s",
                    turn_id="t0",
                    seq=4,
                    ts_ms=30,
                ),
                TtsPlayback(
                    utterance_id=speak.utterance_id,
                    state="finished",
                    mark_chars=len(speak.text),
                ),
            )
            yield ControlEvent(
                Envelope(
                    type="barge_in", session_id="s", turn_id="t0", seq=5, ts_ms=40
                ),
                BargeIn(at_ms=40, during="speaking", utterance_id=speak.utterance_id),
            )
        else:
            if not self._before_mark:
                yield ControlEvent(
                    Envelope(
                        type="tts.playback",
                        session_id="s",
                        turn_id="t0",
                        seq=4,
                        ts_ms=30,
                    ),
                    TtsPlayback(
                        utterance_id=speak.utterance_id,
                        state="mark",
                        mark_chars=self._mark_chars,
                    ),
                )
            yield ControlEvent(
                Envelope(
                    type="barge_in", session_id="s", turn_id="t0", seq=5, ts_ms=40
                ),
                BargeIn(at_ms=40, during="speaking", utterance_id=speak.utterance_id),
            )
        yield ControlEvent(
            Envelope(type="session.ended", session_id="s", seq=6, ts_ms=50),
            SessionEnded(reason="done"),
        )


class BlockingToolTransport:
    def __init__(self, clock: ManualClock, *, advance_ms: float = 40.0) -> None:
        self.clock = clock
        self.advance_ms = advance_ms
        self.started = asyncio.Event()
        self.complete = asyncio.Event()
        self.cancelled = False
        self.commands: list[dict] = []

    async def call_tool(self, server, tool, arguments):
        self.started.set()
        try:
            await self.complete.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.clock.advance(self.advance_ms)
        command = {"server": server, "tool": tool, "arguments": arguments}
        self.commands.append(command)
        return command


def _tool(policy: BargeInPolicy) -> ToolDef:
    return ToolDef(
        server="crm",
        name="book_meeting",
        description="",
        json_schema={},
        profile=ToolProfile(
            expected_latency_ms=100,
            deadline_ms=1000,
            on_barge_in=policy,
            speak_filler=True,
        ),
    )


def _tool_driver(clock: ManualClock, transport: BlockingToolTransport, policy):
    call = ToolCallReady(call_id="c1", name="book_meeting", arguments={"day": "tue"})
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=[],
                usage=UsageReport(1, 0),
                finish_reason="tool_calls",
                tool_calls=[call],
            ),
            ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(2, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    tool = _tool(policy)
    client = McpClient(transport, allowed_tools=[tool.key])
    executor = McpToolExecutor(client, clock, emit=lambda event: None)
    return CascadedTurnDriver(
        sim,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
        tool_executor=executor,
        tools=[tool],
        filler_policy=FillerPolicy(DEFAULT_FILLERS),
        locale="en-US",
    )


def test_heard_text_prefers_flushed_position_over_last_mark():
    text = heard_assistant_text(
        [("u1", "First sentence."), ("u2", "Second sentence.")],
        [
            TtsPlayback(utterance_id="u1", state="finished", mark_chars=99),
            TtsPlayback(utterance_id="u2", state="mark", mark_chars=10),
            TtsPlayback(utterance_id="u2", state="flushed", mark_chars=6),
        ],
    )

    assert text == "First sentence. Second"


async def test_barge_in_sends_cancel_and_truncates_heard_prefix():
    gateway = ImmediateBargeGateway(mark_chars=5)
    session = VoiceSession("s", gateway, _canned, clock=ManualClock())

    records = await session.run()

    cancels = [
        event.payload for event in gateway.sent if isinstance(event.payload, TtsCancel)
    ]
    assert cancels == [TtsCancel(utterance_id="all")]
    assert records[0].interrupted is True
    assert records[0].assistant_text == "Respo"


async def test_barge_in_before_first_mark_records_empty_assistant_text():
    gateway = ImmediateBargeGateway(before_mark=True)
    session = VoiceSession("s", gateway, _canned, clock=ManualClock())

    records = await session.run()

    assert records[0].interrupted is True
    assert records[0].assistant_text == ""


async def test_barge_in_after_finished_does_not_interrupt_completed_turn():
    gateway = ImmediateBargeGateway(after_finished=True)
    session = VoiceSession("s", gateway, _canned, clock=ManualClock())

    records = await session.run()

    assert records[0].interrupted is False
    assert not any(isinstance(event.payload, TtsCancel) for event in gateway.sent)


async def test_multi_utterance_turn_truncates_finished_plus_partial():
    budgets = LatencyBudgets()
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["Happy ", "to ", "help. ", "Anything ", "else?"],
                usage=UsageReport(10, 4),
            )
        ],
        clock,
        token_interval_ms=budgets.llm_first_clause_ms / 10,
    )
    driver = CascadedTurnDriver(
        sim,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        budgets,
        min_flush_chars=8,
    )

    task = asyncio.create_task(
        ConversationHarness().run(
            _one_turn_scenario(), None, clock=clock, driver=driver, barge_in_turns={0}
        )
    )
    result = await _drain(clock, task, budgets.llm_first_clause_ms / 10)

    assert result.turn_records[0].assistant_text == "Happy to help. Anythin"


async def test_speech_during_thinking_discards_unspoken_output_and_next_turn_survives():
    scenario = SyntheticCallScenario(
        name="two_turns",
        objective="thinking interruption",
        turns=[
            SyntheticTurn(speaker="caller", text="first"),
            SyntheticTurn(speaker="caller", text="second"),
        ],
        expected_outcome="booked",
    )
    gateway = ConversationHarness()

    result = await gateway.run(scenario, _canned, vad_interrupt_turns={0})

    assert result.turn_records[0].interrupted is True
    assert result.turn_records[0].assistant_text == ""
    assert result.turn_records[1].user_text == "second"
    assert result.turn_records[1].interrupted is False


async def test_barge_in_cancels_inflight_tool_with_cancel_policy():
    before = set(asyncio.all_tasks())
    clock = ManualClock()
    transport = BlockingToolTransport(clock)
    driver = _tool_driver(clock, transport, BargeInPolicy.CANCEL)
    gateway = ImmediateBargeGateway(mark_chars=4)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=PriceBook(version="mcp-cancel", mcp_per_call=1.0),
    )

    task = asyncio.create_task(session.run())
    for _ in range(20):
        if transport.started.is_set():
            break
        await asyncio.sleep(0)
    assert transport.started.is_set()
    records = await _drain(clock, task, 1, max_steps=80)

    assert records[0].interrupted is True
    assert transport.cancelled is True
    assert transport.commands == []
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")
    assert cost.attribution["mcp_tool_calls"] == 1
    assert cost.cost.mcp_tool_cost == 1.0
    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def test_run_to_completion_tool_survives_barge_in_and_records_result():
    clock = ManualClock()
    transport = BlockingToolTransport(clock, advance_ms=37.0)
    driver = _tool_driver(clock, transport, BargeInPolicy.RUN_TO_COMPLETION)
    gateway = ImmediateBargeGateway(mark_chars=4)
    session = VoiceSession("s", gateway, None, driver=driver, clock=clock)

    task = asyncio.create_task(session.run())
    for _ in range(20):
        if transport.started.is_set():
            break
        await asyncio.sleep(0)
    assert transport.started.is_set()
    transport.complete.set()
    records = await _drain(clock, task, 1, max_steps=120)

    assert records[0].interrupted is True
    assert transport.cancelled is False
    assert transport.commands == [
        {"server": "crm", "tool": "book_meeting", "arguments": {"day": "tue"}}
    ]
    assert records[0].waterfall.mcp_tools_ms == pytest.approx(37.0)
    spoken = [
        event.payload.text
        for event in gateway.sent
        if isinstance(event.payload, TtsSpeak)
    ]
    assert spoken == [DEFAULT_FILLERS["en-US"][0]]


async def test_repeated_barge_ins_leave_no_orphan_tasks():
    before = set(asyncio.all_tasks())
    scenario = SyntheticCallScenario(
        name="interruptions",
        objective="three interrupted turns",
        turns=[
            SyntheticTurn(speaker="caller", text="one"),
            SyntheticTurn(speaker="caller", text="two"),
            SyntheticTurn(speaker="caller", text="three"),
        ],
        expected_outcome="interruption",
    )

    result = await ConversationHarness().run(
        scenario, _canned, barge_in_turns={0, 1, 2}
    )

    assert [record.interrupted for record in result.turn_records] == [True, True, True]
    for record in result.turn_records:
        full = "Response to: %s" % record.user_text
        assert full.startswith(record.assistant_text)
        assert len(record.assistant_text) < len(full)
    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def test_booking_interruption_eval_green_via_harness():
    scenario = next(
        item
        for item in default_sales_booking_scenarios()
        if item.name == "booking_interruption"
    )

    result = await ConversationHarness().run(scenario, _canned)
    evidence = evidence_from_result(scenario, result)
    scored = score_synthetic_call(scenario, evidence)

    assert scored.passed is True
    assert scored.gates["interruption_handling"] is True
