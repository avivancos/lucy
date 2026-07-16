import asyncio

import pytest

from lucy.clock import ManualClock
from lucy.evals import SyntheticCallScenario, SyntheticTurn, booking_happy_path
from lucy.metrics import LatencyWaterfall
from lucy.observe import Tracer
from lucy.session import SessionControlError, TurnState, VoiceSession
from lucy.testing import InMemoryTraceExporter
from lucy.transport.dev_gateway import (
    LocalGatewaySimulator,
    SimulatedSpeakerActivity,
)
from lucy.transport.schema import (
    BargeIn,
    ControlEvent,
    SttFinal,
    TtsPlayback,
    VadSpeechEnd,
)


async def _canned(user_text: str) -> str:
    return "Response to: %s" % user_text


async def test_happy_turn_walks_the_state_machine_and_yields_records():
    clock = ManualClock()
    gw = LocalGatewaySimulator(booking_happy_path(), clock, session_id="s1")
    session = VoiceSession("s1", gw, _canned, clock=clock)

    records = await session.run()

    assert len(records) == 2  # two caller turns
    first = records[0]
    assert first.user_text == "I want to book a demo."
    assert first.assistant_text == "Response to: I want to book a demo."
    assert first.interrupted is False
    assert isinstance(first.waterfall, LatencyWaterfall)
    assert first.waterfall.stt_ms == 60  # from the SttFinal
    assert first.waterfall.tts_ms > 0  # real playback duration
    assert first.caller_talk_ms == (
        len(first.user_text.split()) * gw.budgets.gateway_pacing_ms
    )
    assert first.agent_talk_ms == 2 * gw.budgets.gateway_pacing_ms

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
    gw = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        session_id="s1",
        barge_in_turns={0},
    )
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
    assert turn0.agent_talk_ms == turn0.waterfall.tts_ms
    assert turn0.caller_talk_ms > 0

    # the second turn was not interrupted
    assert records[1].interrupted is False

    # no leaked turn tasks
    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def test_thinking_phase_interruption_cancels_without_corrupting_waterfall():
    # Caller re-speaks (VadSpeechStart) while the agent is still THINKING, before
    # any TTS playback. The turn is cancelled and the waterfall stays sane.
    gw = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        session_id="s1",
        vad_interrupt_turns={0},
    )
    session = VoiceSession("s1", gw, _canned, clock=ManualClock())

    records = await session.run()

    turn0 = records[0]
    assert turn0.interrupted is True
    # never spoke, so tts_ms must be 0 - not a raw absolute timestamp (code-002)
    assert turn0.waterfall.tts_ms == 0.0
    assert turn0.agent_talk_ms == 0
    assert turn0.caller_talk_ms > 0
    assert turn0.assistant_text == ""  # nothing heard -> truncated to empty
    assert records[1].interrupted is False  # only turn 0 was interrupted


async def test_talk_measurement_uses_control_timestamps_not_wall_clock():
    clock = ManualClock()
    gateway = LocalGatewaySimulator(booking_happy_path(), clock, session_id="s1")
    session = VoiceSession("s1", gateway, _canned, clock=clock)

    records = await session.run()

    assert clock.monotonic() == 0.0
    assert records[0].caller_talk_ms == (
        len(records[0].user_text.split()) * gateway.budgets.gateway_pacing_ms
    )


async def test_distinct_media_intervals_win_over_text_and_billing_proxies():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    clock = ManualClock()
    scenario = SyntheticCallScenario(
        name="distinct-speaker-activity",
        objective="prove media intervals are authoritative",
        turns=[
            SyntheticTurn(speaker="caller", text="x"),
            SyntheticTurn(
                speaker="caller",
                text="this transcript is intentionally much much longer",
            ),
        ],
        expected_outcome="measured",
    )
    activity = (
        SimulatedSpeakerActivity(caller_talk_ms=701, agent_talk_ms=127),
        SimulatedSpeakerActivity(caller_talk_ms=103, agent_talk_ms=911),
    )
    gateway = LocalGatewaySimulator(
        scenario,
        clock,
        session_id="s1",
        speaker_activity=activity,
    )
    session = VoiceSession(
        "s1",
        gateway,
        _canned,
        tracer=tracer,
        clock=clock,
    )

    records = await session.run()
    tracer.flush()
    turns = [event for event in exporter.events if event.type == "turn"]

    assert [record.caller_talk_ms for record in records] == [701, 103]
    assert [record.agent_talk_ms for record in records] == [127, 911]
    assert [turn.turn_id for turn in turns] == ["turn_0", "turn_1"]
    assert [turn.turn_index for turn in turns] == [0, 1]
    assert [turn.caller_talk_ms for turn in turns] == [701, 103]
    assert [turn.agent_talk_ms for turn in turns] == [127, 911]
    costs = [event for event in exporter.events if event.type == "cost"]
    assert costs and costs[-1].cost.billable_audio_minutes > 0


class _PlaybackMutationGateway(LocalGatewaySimulator):
    def __init__(self, mode: str) -> None:
        super().__init__(
            booking_happy_path(),
            ManualClock(),
            barge_in_turns={0} if mode == "wrong_barge_turn" else (),
        )
        self.mode = mode
        self.started_at_ms = None
        self.changed = False

    async def events(self):
        async for event in super().events():
            payload = event.payload
            if (
                self.mode == "cross_session_vad"
                and isinstance(payload, VadSpeechEnd)
                and not self.changed
            ):
                self.changed = True
                event = ControlEvent(
                    event.envelope.model_copy(update={"session_id": "other"}),
                    payload,
                )
            elif (
                self.mode == "cross_session_stt"
                and isinstance(payload, SttFinal)
                and not self.changed
            ):
                self.changed = True
                event = ControlEvent(
                    event.envelope.model_copy(update={"session_id": "other"}),
                    payload,
                )
            elif (
                self.mode == "wrong_barge_turn"
                and isinstance(payload, BargeIn)
                and not self.changed
            ):
                self.changed = True
                event = ControlEvent(
                    event.envelope.model_copy(update={"turn_id": "foreign-turn"}),
                    payload,
                )
            if isinstance(payload, TtsPlayback):
                if payload.state == "started":
                    self.started_at_ms = event.envelope.ts_ms
                    if self.mode == "wrong_session" and not self.changed:
                        self.changed = True
                        event = ControlEvent(
                            event.envelope.model_copy(update={"session_id": "other"}),
                            payload,
                        )
                    elif self.mode == "wrong_turn" and not self.changed:
                        self.changed = True
                        event = ControlEvent(
                            event.envelope.model_copy(update={"turn_id": "other"}),
                            payload,
                        )
                    elif self.mode == "replay_start" and not self.changed:
                        self.changed = True
                        yield event
                elif payload.state == "finished" and not self.changed:
                    if self.mode == "unknown_utterance":
                        self.changed = True
                        event = ControlEvent(
                            event.envelope,
                            payload.model_copy(update={"utterance_id": "unknown"}),
                        )
                    elif self.mode == "reversed_terminal":
                        assert self.started_at_ms is not None
                        self.changed = True
                        event = ControlEvent(
                            event.envelope.model_copy(
                                update={"ts_ms": self.started_at_ms - 1}
                            ),
                            payload,
                        )
                    elif self.mode == "replay_terminal":
                        self.changed = True
                        yield event
            yield event


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_session",
        "wrong_turn",
        "unknown_utterance",
        "replay_start",
        "reversed_terminal",
        "replay_terminal",
        "cross_session_vad",
        "cross_session_stt",
        "wrong_barge_turn",
    ],
)
async def test_playback_attribution_rejects_uncorrelated_control_events(mode):
    gateway = _PlaybackMutationGateway(mode)
    session = VoiceSession("sess_sim", gateway, _canned, clock=ManualClock())

    with pytest.raises(SessionControlError):
        await session.run()


class _LateFlushMutationGateway(LocalGatewaySimulator):
    def __init__(self, field: str) -> None:
        super().__init__(
            booking_happy_path(),
            ManualClock(),
            barge_in_turns={0},
        )
        self.field = field

    async def events(self):
        async for event in super().events():
            if (
                isinstance(event.payload, TtsPlayback)
                and event.payload.state == "flushed"
            ):
                if self.field == "session_id":
                    event = ControlEvent(
                        event.envelope.model_copy(update={"session_id": "other"}),
                        event.payload,
                    )
                else:
                    event = ControlEvent(
                        event.envelope,
                        event.payload.model_copy(update={"utterance_id": "other"}),
                    )
            yield event


@pytest.mark.parametrize("field", ["session_id", "utterance_id"])
async def test_late_flush_rejects_foreign_interruption_identity(field):
    gateway = _LateFlushMutationGateway(field)
    session = VoiceSession("sess_sim", gateway, _canned, clock=ManualClock())

    with pytest.raises(SessionControlError):
        await session.run()


class _DelayedFlushGateway(LocalGatewaySimulator):
    def __init__(self) -> None:
        super().__init__(
            booking_happy_path(),
            ManualClock(),
            barge_in_turns={0},
        )

    async def events(self):
        delayed_flush = None
        async for event in super().events():
            if (
                isinstance(event.payload, TtsPlayback)
                and event.payload.state == "flushed"
                and event.envelope.turn_id == "turn_0"
            ):
                delayed_flush = event
                continue
            if (
                delayed_flush is not None
                and isinstance(event.payload, TtsPlayback)
                and event.payload.state == "started"
                and event.envelope.turn_id == "turn_1"
            ):
                yield delayed_flush
                delayed_flush = None
            yield event


async def test_late_flush_rejects_prior_turn_once_next_turn_is_active():
    gateway = _DelayedFlushGateway()
    session = VoiceSession("sess_sim", gateway, _canned, clock=ManualClock())

    with pytest.raises(SessionControlError):
        await session.run()
