import asyncio

from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.metrics import LatencyWaterfall
from lucy.session import TurnState, VoiceSession
from lucy.transport.dev_gateway import LocalGatewaySimulator


async def _canned(user_text: str) -> str:
    return "Response to: %s" % user_text


async def test_happy_turn_walks_the_state_machine_and_yields_records():
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock())
    session = VoiceSession("s1", gw, _canned, clock=ManualClock())

    records = await session.run()

    assert len(records) == 2  # two caller turns
    first = records[0]
    assert first.user_text == "I want to book a demo."
    assert first.assistant_text == "Response to: I want to book a demo."
    assert first.interrupted is False
    assert isinstance(first.waterfall, LatencyWaterfall)
    assert first.waterfall.stt_ms == 60  # from the SttFinal
    assert first.waterfall.tts_ms > 0  # real playback duration

    # the session visited the full happy-turn walk
    for state in (
        TurnState.IDLE,
        TurnState.LISTENING,
        TurnState.THINKING,
        TurnState.SPEAKING,
    ):
        assert state in session.transitions
    assert session.transitions[-1] == TurnState.IDLE


async def test_barge_in_cancels_the_turn_truncates_and_leaves_no_orphans():
    before = set(asyncio.all_tasks())
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock(), barge_in_turns={0})
    session = VoiceSession("s1", gw, _canned, clock=ManualClock())

    records = await session.run()

    turn0 = records[0]
    assert turn0.interrupted is True
    # assistant_text truncated to what the caller actually heard (mark_chars)
    full = "Response to: I want to book a demo."
    assert turn0.assistant_text != full
    assert full.startswith(turn0.assistant_text)
    assert len(turn0.assistant_text) >= 1
    # the interrupted turn still produced a waterfall
    assert turn0.waterfall.tts_ms > 0

    # the second turn was not interrupted
    assert records[1].interrupted is False

    # no leaked turn tasks
    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def test_thinking_phase_interruption_cancels_without_corrupting_waterfall():
    # Caller re-speaks (VadSpeechStart) while the agent is still THINKING, before
    # any TTS playback. The turn is cancelled and the waterfall stays sane.
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock(), vad_interrupt_turns={0})
    session = VoiceSession("s1", gw, _canned, clock=ManualClock())

    records = await session.run()

    turn0 = records[0]
    assert turn0.interrupted is True
    # never spoke, so tts_ms must be 0 - not a raw absolute timestamp (code-002)
    assert turn0.waterfall.tts_ms == 0.0
    assert turn0.assistant_text == ""  # nothing heard -> truncated to empty
    assert records[1].interrupted is False  # only turn 0 was interrupted


async def test_no_wall_clock_sleeps_in_the_session_run():
    # a full run completes effectively instantly (proves virtual time only)
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock())
    session = VoiceSession("s1", gw, _canned, clock=ManualClock())
    await asyncio.wait_for(session.run(), timeout=2.0)
