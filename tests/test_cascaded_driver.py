import asyncio

import pytest

from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver, ToolCallsNotSupported, TurnDriverReport
from lucy.evals import SyntheticCallScenario, SyntheticTurn, booking_happy_path
from lucy.harness import ConversationHarness
from lucy.llm import (
    LocalLlmSimulator,
    ScriptedLlmTurn,
    ToolCallDelta,
    UsageReport,
)
from lucy.providers import default_model_registry
from lucy.session import VoiceSession
from lucy.settings import LatencyBudgets, LlmPricing
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import TtsSpeak


async def _drain(clock: ManualClock, task, step_ms: float, max_steps: int = 80):
    for _ in range(max_steps):
        if task.done():
            break
        await asyncio.sleep(0)
        clock.advance(step_ms)
        await asyncio.sleep(0)
    if not task.done():
        # Hang-proof (card 64): a stuck task fails fast with a diagnosis
        # instead of hanging CI on an unbounded await.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise AssertionError(
            "task did not complete within %d drain steps (deadlock?)" % max_steps
        )
    await task


def _driver(clock, sim, budgets, **kw):
    return CascadedTurnDriver(
        sim, default_model_registry(), "openai", "gpt-5", clock, budgets, **kw
    )


# -- C6: CascadedTurnDriver --------------------------------------------------


async def test_first_tts_speak_before_stream_end_within_budget():
    budgets = LatencyBudgets()
    interval_ms = budgets.llm_first_clause_ms / 10  # derived, never a literal
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Sure, ", "I can ", "help. ", "Anything else?"], usage=UsageReport(3, 5))],
        clock,
        token_interval_ms=interval_ms,
    )
    driver = _driver(clock, sim, budgets, min_flush_chars=10)

    events: list = []
    stamps: list[float] = []
    start = {}

    async def consume():
        start["t"] = clock.monotonic()
        async for event in driver.run_turn("hi", []):
            events.append(event)
            stamps.append(clock.monotonic())

    task = asyncio.create_task(consume())
    await _drain(clock, task, interval_ms)

    first_tts_time = next(t for t, e in zip(stamps, events) if isinstance(e, TtsSpeak))
    report_time = next(t for t, e in zip(stamps, events) if isinstance(e, TurnDriverReport))
    assert first_tts_time < report_time  # first clause spoke before the stream ended
    assert (first_tts_time - start["t"]) <= budgets.llm_first_clause_ms / 1000.0 + 1e-9


async def test_report_carries_llm_ms_usage_and_cost():
    budgets = LatencyBudgets()
    clock = ManualClock()
    usage = UsageReport(prompt_tokens=100, completion_tokens=50)
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Done."], usage=usage)],
        clock,
        token_interval_ms=budgets.llm_first_clause_ms / 10,
    )
    pricing = LlmPricing(prompt_per_1k=0.01, completion_per_1k=0.03)
    driver = _driver(clock, sim, budgets, pricing=pricing, min_flush_chars=1)

    report = {}

    async def consume():
        async for event in driver.run_turn("hi", []):
            if isinstance(event, TurnDriverReport):
                report["r"] = event

    task = asyncio.create_task(consume())
    await _drain(clock, task, budgets.llm_first_clause_ms / 10)

    r = report["r"]
    assert r.assistant_text == "Done."
    assert r.usage == usage
    assert r.llm_ms > 0
    assert r.llm_cost == pytest.approx(100 / 1000 * 0.01 + 50 / 1000 * 0.03)


async def test_no_pricing_means_zero_cost():
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(9, 9))],
        clock,
        token_interval_ms=1,
    )
    driver = _driver(clock, sim, LatencyBudgets(), min_flush_chars=1)
    report = {}

    async def consume():
        async for event in driver.run_turn("hi", []):
            if isinstance(event, TurnDriverReport):
                report["r"] = event

    task = asyncio.create_task(consume())
    await _drain(clock, task, 1)
    assert report["r"].llm_cost == 0.0


async def test_driver_flushes_multiple_clauses_in_order():
    # A reply spanning two sentences flushes as two ordered TtsSpeak clauses
    # (the mid-stream flush, not a single coalesced utterance).
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(
            tokens=["Happy ", "to ", "help. ", "Anything ", "else?"],
            usage=UsageReport(3, 5),
        )],
        clock,
        token_interval_ms=1,
    )
    driver = _driver(clock, sim, LatencyBudgets(), min_flush_chars=8)

    tts_texts: list[str] = []

    async def consume():
        async for event in driver.run_turn("hi", []):
            if isinstance(event, TtsSpeak):
                tts_texts.append(event.text)

    task = asyncio.create_task(consume())
    await _drain(clock, task, 1, max_steps=400)
    assert tts_texts == ["Happy to help.", "Anything else?"]


async def test_harness_multi_clause_turn_completes_without_deadlock():
    # Regression guard: a driver turn that flushes >1 clause must run end-to-end
    # through the gateway/session without hanging (an unfed assembler emitting no
    # clause, or a gateway that stalls on the extra clauses, would hang here).
    budgets = LatencyBudgets()
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(
            tokens=["Happy ", "to ", "help. ", "Anything ", "else?"],
            usage=UsageReport(10, 4),
        )],
        clock,
        token_interval_ms=budgets.llm_first_clause_ms / 10,
    )
    driver = _driver(clock, sim, budgets, min_flush_chars=8)
    scenario = SyntheticCallScenario(
        name="one_turn",
        objective="single caller turn with a multi-clause reply",
        turns=[SyntheticTurn(speaker="caller", text="hello there")],
        expected_outcome="booked",
    )

    task = asyncio.create_task(
        ConversationHarness().run(scenario, None, clock=clock, driver=driver)
    )
    await _drain(clock, task, budgets.llm_first_clause_ms / 10, max_steps=400)
    result = task.result()

    # The full assistant reply (both clauses) is recorded; nothing hung or dropped.
    assert result.transcript[1] == ("agent", "Happy to help. Anything else?")
    assert result.turn_records[0].waterfall.llm_ms > 0


async def test_tool_call_event_raises_tool_calls_not_supported():
    class ToolProvider:  # a real in-process LlmProvider (ADR 0003), not a mock
        async def stream_chat(self, request):
            yield ToolCallDelta(call_id="c1", name="book", arguments_delta="{}")

    driver = _driver(ManualClock(), ToolProvider(), LatencyBudgets())
    with pytest.raises(ToolCallsNotSupported):
        async for _ in driver.run_turn("hi", []):
            pass


# -- C7: VoiceSession + harness wiring ---------------------------------------


async def test_voice_session_rejects_responder_and_driver_together():
    async def responder(text):
        return "x"

    clock = ManualClock()
    sim = LocalLlmSimulator([ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1))], clock, 1)
    driver = _driver(clock, sim, LatencyBudgets(), min_flush_chars=1)
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock())

    with pytest.raises(ValueError):
        VoiceSession("s1", gw, responder, driver=driver)
    with pytest.raises(ValueError):
        VoiceSession("s1", gw, None, driver=None)


async def test_harness_booking_happy_path_with_llm_simulator():
    budgets = LatencyBudgets()
    clock = ManualClock()
    # one scripted LLM turn per caller turn; tokens spell the agent reply
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["Happy ", "to ", "help ", "you."], usage=UsageReport(10, 4)),
            ScriptedLlmTurn(tokens=["Booked ", "for ", "Tuesday."], usage=UsageReport(12, 3)),
        ],
        clock,
        token_interval_ms=budgets.llm_first_clause_ms / 10,
    )
    driver = _driver(clock, sim, budgets, pricing=LlmPricing(completion_per_1k=0.02), min_flush_chars=8)

    # the driver's simulator paces tokens on the shared ManualClock, so advance
    # virtual time while the harness runs (no wall time consumed)
    task = asyncio.create_task(
        ConversationHarness().run(booking_happy_path(), None, clock=clock, driver=driver)
    )
    await _drain(clock, task, budgets.llm_first_clause_ms / 10)
    result = task.result()

    caller_lines = [text for who, text in result.transcript if who == "caller"]
    assert caller_lines == [t.text for t in booking_happy_path().turns if t.speaker == "caller"]
    assert result.transcript[1] == ("agent", "Happy to help you.")
    # driver-filled telemetry
    assert all(r.waterfall.llm_ms > 0 for r in result.turn_records)
    assert all(r.cost is not None and r.cost.billable_audio_minutes > 0 for r in result.turn_records)
    assert result.turn_records[0].cost.llm_cost == pytest.approx(4 / 1000 * 0.02)


# -- card 64: empty stream through the driver ---------------------------------


async def test_empty_stream_yields_no_tts_speak_and_empty_report():
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(1, 0))], clock, token_interval_ms=0
    )
    driver = _driver(clock, sim, LatencyBudgets(), min_flush_chars=1)

    events = [e async for e in driver.run_turn("hi", [])]

    assert [e for e in events if isinstance(e, TtsSpeak)] == []
    report = next(e for e in events if isinstance(e, TurnDriverReport))
    assert report.assistant_text == ""
    assert report.usage == UsageReport(1, 0)


# -- card 64: multi-clause barge-in truncation + clock-tied billing -----------


def _one_turn_scenario():
    return SyntheticCallScenario(
        name="one_turn",
        objective="single caller turn",
        turns=[SyntheticTurn(speaker="caller", text="hello there")],
        expected_outcome="booked",
    )


async def test_driver_turn_barge_in_truncates_multi_clause_via_planner():
    budgets = LatencyBudgets()
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(
            tokens=["Happy ", "to ", "help. ", "Anything ", "else?"],
            usage=UsageReport(10, 4),
        )],
        clock,
        token_interval_ms=budgets.llm_first_clause_ms / 10,
    )
    driver = _driver(clock, sim, budgets, min_flush_chars=8)

    task = asyncio.create_task(
        ConversationHarness().run(
            _one_turn_scenario(), None, clock=clock, driver=driver, barge_in_turns={0}
        )
    )
    await _drain(clock, task, budgets.llm_first_clause_ms / 10, max_steps=400)
    record = task.result().turn_records[0]

    assert record.interrupted is True
    # First clause fully heard; the second cut at its playback mark
    # ("Anything else?" is 14 chars -> heard = 7 -> "Anythin").
    assert record.assistant_text == "Happy to help. Anythin"


async def test_billable_minutes_track_clock_duration():
    # Billing must derive from the injected clock: a longer scripted turn
    # yields strictly more billable minutes (a hardcoded value would be equal).
    budgets = LatencyBudgets()
    interval_ms = budgets.llm_first_clause_ms / 10
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(
                tokens=["One ", "two ", "three ", "four ", "five ", "six ", "done."],
                usage=UsageReport(1, 7),
            ),
        ],
        clock,
        token_interval_ms=interval_ms,
    )
    driver = _driver(clock, sim, budgets, min_flush_chars=1)
    scenario = SyntheticCallScenario(
        name="two_turns",
        objective="two caller turns of different lengths",
        turns=[
            SyntheticTurn(speaker="caller", text="hi"),
            SyntheticTurn(speaker="agent", text="ignored"),
            SyntheticTurn(speaker="caller", text="count for me"),
        ],
        expected_outcome="booked",
    )

    task = asyncio.create_task(
        ConversationHarness().run(scenario, None, clock=clock, driver=driver)
    )
    await _drain(clock, task, interval_ms, max_steps=400)
    records = task.result().turn_records

    short_min = records[0].cost.billable_audio_minutes
    long_min = records[1].cost.billable_audio_minutes
    assert short_min >= (interval_ms / 60000.0) - 1e-12  # at least one token pace
    assert long_min > short_min  # clock-derived, not a constant


async def test_empty_driver_turn_mid_session_is_recorded_not_leaked():
    # code-001: a zero-clause driver reply mid-session has no playback at all;
    # the session must still record it and start the next turn cleanly instead
    # of dropping the record and leaking the blocked turn task.
    budgets = LatencyBudgets()
    interval = budgets.llm_first_clause_ms / 10
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=[], usage=UsageReport(1, 0)),  # empty reply
            ScriptedLlmTurn(tokens=["Hello ", "there."], usage=UsageReport(1, 2)),
        ],
        clock,
        token_interval_ms=interval,
    )
    driver = _driver(clock, sim, budgets, min_flush_chars=1)
    scenario = SyntheticCallScenario(
        name="empty_then_normal",
        objective="an empty agent reply must not swallow the turn",
        turns=[
            SyntheticTurn(speaker="caller", text="one"),
            SyntheticTurn(speaker="agent", text="ignored"),
            SyntheticTurn(speaker="caller", text="two"),
        ],
        expected_outcome="booked",
    )

    task = asyncio.create_task(
        ConversationHarness().run(scenario, None, clock=clock, driver=driver)
    )
    await _drain(clock, task, interval, max_steps=400)
    result = task.result()

    assert result.transcript == [
        ("caller", "one"),
        ("agent", ""),
        ("caller", "two"),
        ("agent", "Hello there."),
    ]
    assert len(result.turn_records) == 2  # nothing dropped, nothing leaked
