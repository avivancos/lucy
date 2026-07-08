from lucy.evals import booking_happy_path
from lucy.harness import ConversationHarness, HarnessResult
from lucy.metrics import LatencyWaterfall


async def _canned(user_text: str) -> str:
    return "Sure, about: %s" % user_text


async def test_harness_runs_a_scenario_end_to_end():
    result = await ConversationHarness().run(booking_happy_path(), _canned)

    assert isinstance(result, HarnessResult)

    caller_lines = [text for who, text in result.transcript if who == "caller"]
    scenario_callers = [
        t.text for t in booking_happy_path().turns if t.speaker == "caller"
    ]
    assert caller_lines == scenario_callers

    agent_lines = [text for who, text in result.transcript if who == "agent"]
    assert agent_lines == ["Sure, about: %s" % c for c in scenario_callers]

    assert len(result.turn_records) == 2
    assert len(result.waterfalls) == 2
    assert all(isinstance(w, LatencyWaterfall) for w in result.waterfalls)
    assert all(w.stt_ms == 60 for w in result.waterfalls)

    assert any(s.name == "lucy.session" for s in result.spans)
    assert [s.name for s in result.spans].count("lucy.turn") == 2


async def test_harness_surfaces_an_interruption():
    result = await ConversationHarness().run(
        booking_happy_path(), _canned, barge_in_turns={0}
    )
    assert result.turn_records[0].interrupted is True
    assert result.turn_records[1].interrupted is False
