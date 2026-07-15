import asyncio
from typing import get_args

import pytest

from lucy.clock import ManualClock
from lucy.drivers import DirectiveOrderError, GraphTurnDriver, TurnDriverReport
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.graph import AgentGraph, GraphExecutionError
from lucy.harness import ConversationHarness
from lucy.metrics import FunnelEvent
from lucy.observe import Tracer
from lucy.pricing import PriceBook
from lucy.recording import RecordingCoordinator
from lucy.runtime import TurnContext
from lucy.session import VoiceSession, _ActiveTurn
from lucy.settings import LatencyBudgets, SpeculationSettings
from lucy.specs import FunnelStage, RecordingSpec
from lucy.state import ConversationState
from lucy.testing import InMemoryTraceExporter, RecordingBlobStoreSimulator
from lucy.transport.schema import (
    ControlEvent,
    DOWNSTREAM_TYPES,
    Dial,
    DownstreamDirective,
    DtmfSend,
    Envelope,
    Hold,
    RealtimeConnect,
    RealtimeToolResult,
    RecordingStart,
    RecordingStop,
    SessionConfigure,
    SessionEnd,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    Transfer,
    TtsCancel,
    TtsPlayback,
    TtsSpeak,
    TtsStreamEnd,
    downstream_type,
)

TEST_SCHEDULER_TURNS = 200


def _base_directives():
    return [
        SessionConfigure(stt="local-stt", tts="local-tts", vad="local-vad"),
        TtsSpeak(utterance_id="utt-1", text="Hello."),
        TtsCancel(utterance_id="all"),
        TtsStreamEnd(),
        RealtimeConnect(provider="local", model="realtime"),
        RealtimeToolResult(call_id="call-1", output_json="{}"),
        DtmfSend(digits="1"),
        Transfer(target="sales"),
        Dial(target="support", caller_id="outbound", timeout_ms=1_000),
        Hold(state="hold", music=True),
        SessionEnd(reason="done"),
        RecordingStart(
            recording_id="rec-graph",
            leg="mixed",
            blob_id="blob-graph",
            upload_url_ref="upload-graph",
            container="wav",
            consent_ref="consent-graph",
        ),
        RecordingStop(recording_id="rec-graph"),
    ]


def _graph_emitting(*events: object):
    async def control_node(state: ConversationState, ctx: TurnContext):
        assert ctx.emit is not None
        for event in events:
            ctx.emit(event)
        return {}

    return (
        AgentGraph[ConversationState]()
        .add_node("control", control_node)
        .set_entry("control")
        .compile()
    )


def _dynamic_control_graph():
    async def control_node(state: ConversationState, ctx: TurnContext):
        assert ctx.emit is not None
        target = str(ctx.payload["user_text"])
        ctx.emit(Transfer(target=target))
        ctx.emit(Hold(state="hold", music=True))
        ctx.emit(SessionEnd(reason=target))
        return {}

    return (
        AgentGraph[ConversationState]()
        .add_node("control", control_node)
        .set_entry("control")
        .compile()
    )


async def _driver_events(*directives: object):
    clock = ManualClock()
    driver = GraphTurnDriver(
        _graph_emitting(*directives),
        session_id="directive-session",
        clock=clock,
    )
    return [
        event
        async for event in driver.run_turn(
            "sales",
            [],
            turn_context=TurnContext(turn_id="turn-1", clock=clock),
        )
    ]


def test_downstream_union_and_wire_names_cannot_drift_from_registry():
    assert set(get_args(DownstreamDirective)) == set(DOWNSTREAM_TYPES.values())
    for directive in _base_directives():
        assert DOWNSTREAM_TYPES[downstream_type(directive)] is type(directive)

    with pytest.raises(TypeError, match="registered downstream directive"):
        downstream_type(SttFinal(text="not downstream", provider="local", stt_ms=1))


@pytest.mark.parametrize("directive", _base_directives(), ids=downstream_type)
async def test_graph_driver_yields_every_registered_directive_before_one_report(
    directive,
):
    events = await _driver_events(directive)

    assert events[:-1] == [directive]
    assert isinstance(events[-1], TurnDriverReport)
    assert sum(isinstance(event, TurnDriverReport) for event in events) == 1


async def test_graph_driver_preserves_control_order_before_terminal_report():
    directives = [
        Transfer(target="sales"),
        Hold(state="hold", music=True),
        SessionEnd(reason="done"),
    ]

    assert (await _driver_events(*directives))[:-1] == directives


async def test_graph_driver_preserves_mixed_tts_control_order():
    directives = [
        TtsSpeak(utterance_id="utt-before", text="Before."),
        Transfer(target="sales"),
        TtsSpeak(utterance_id="utt-after", text="After."),
        SessionEnd(reason="transferred"),
    ]

    assert (await _driver_events(*directives))[:-1] == directives


async def test_internal_and_audio_like_graph_events_never_reach_driver_contract():
    transfer = Transfer(target="sales")
    events = await _driver_events(
        FunnelEvent(
            session_id="s",
            stage=FunnelStage.INTERESTED,
            confidence=0.9,
        ),
        b"audio-frame",
        transfer,
    )

    assert events[:-1] == [transfer]


@pytest.mark.parametrize(
    ("terminal", "late"),
    [
        (SessionEnd(reason="done"), Transfer(target="late")),
        (TtsStreamEnd(), TtsSpeak(utterance_id="late", text="Too late.")),
    ],
)
async def test_graph_rejects_directives_after_a_terminal_control(terminal, late):
    with pytest.raises(GraphExecutionError, match="after terminal directive"):
        await _driver_events(terminal, late)


class LiveDirectiveGateway:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.sent: list[ControlEvent] = []
        self.stream_ended = asyncio.Event()

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.sent.append(ControlEvent(envelope, payload))
        if isinstance(payload, (SessionEnd, TtsStreamEnd)):
            self.stream_ended.set()

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="s", seq=1, ts_ms=0),
            SessionStarted(transport="sim", caller="anonymous", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(type="stt.final", session_id="s", turn_id="t0", seq=2, ts_ms=10),
            SttFinal(text="sales", provider="local", stt_ms=10),
        )
        await self.stream_ended.wait()
        spoken = [
            event.payload for event in self.sent if isinstance(event.payload, TtsSpeak)
        ]
        if spoken:
            utterance = spoken[-1]
            yield ControlEvent(
                Envelope(
                    type="tts.playback", session_id="s", turn_id="t0", seq=3, ts_ms=20
                ),
                TtsPlayback(utterance_id=utterance.utterance_id, state="started"),
            )
            yield ControlEvent(
                Envelope(
                    type="tts.playback", session_id="s", turn_id="t0", seq=4, ts_ms=30
                ),
                TtsPlayback(
                    utterance_id=utterance.utterance_id,
                    state="finished",
                    mark_chars=len(utterance.text),
                ),
            )
        yield ControlEvent(
            Envelope(type="session.ended", session_id="s", seq=5, ts_ms=40),
            SessionEnded(reason="done"),
        )


async def _recording_plan():
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        RecordingBlobStoreSimulator(),
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-live", "blob-live"]).__next__,
    )
    start = (await coordinator.start("s", consent_ref="consent-live"))[0]
    return coordinator, start


async def _live_directive(name: str):
    if name == "recording.start":
        coordinator, start = await _recording_plan()
        return start, coordinator
    if name == "recording.stop":
        coordinator, start = await _recording_plan()
        stop = coordinator.stop("s", start.recording_id)
        assert stop is not None
        return stop, coordinator
    directive = next(
        item for item in _base_directives() if downstream_type(item) == name
    )
    return directive, None


@pytest.mark.parametrize("wire_name", tuple(DOWNSTREAM_TYPES))
async def test_live_session_dispatches_every_registered_directive(wire_name):
    directive, coordinator = await _live_directive(wire_name)
    clock = ManualClock()
    gateway = LiveDirectiveGateway(clock)
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(directive),
            session_id="s",
            clock=clock,
        ),
        clock=clock,
        recording_coordinator=coordinator,
    )

    await session.run()

    matching = [event for event in gateway.sent if event.payload == directive]
    assert len(matching) == 1
    assert matching[0].envelope.type == wire_name
    assert matching[0].envelope.session_id == "s"
    assert matching[0].envelope.turn_id == "t0"


async def test_transfer_smoke_reaches_local_gateway_through_harness():
    clock = ManualClock()
    result = await ConversationHarness("directive-session").run(
        SyntheticCallScenario(
            name="graph-control",
            objective="transfer",
            turns=[SyntheticTurn(speaker="caller", text="sales")],
            expected_outcome="completed",
        ),
        driver=GraphTurnDriver(
            _graph_emitting(Transfer(target="sales")),
            session_id="directive-session",
            clock=clock,
        ),
        clock=clock,
    )

    transfer = next(
        event for event in result.directives if isinstance(event.payload, Transfer)
    )
    assert transfer.payload == Transfer(target="sales")
    assert transfer.envelope.type == "transfer"


async def test_live_session_preserves_multi_directive_order_and_identity():
    directives = [
        Transfer(target="sales"),
        Hold(state="hold", music=True),
        SessionEnd(reason="done"),
    ]
    clock = ManualClock()
    gateway = LiveDirectiveGateway(clock)
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(*directives), session_id="s", clock=clock
        ),
        clock=clock,
    )

    await session.run()

    controls = [
        event
        for event in gateway.sent
        if isinstance(event.payload, (Transfer, Hold, SessionEnd))
    ]
    assert [event.payload for event in controls] == directives
    assert [event.envelope.type for event in controls] == [
        "transfer",
        "hold",
        "session.end",
    ]
    assert all(event.envelope.session_id == "s" for event in controls)
    assert all(event.envelope.turn_id == "t0" for event in controls)
    assert not any(isinstance(event.payload, TtsStreamEnd) for event in gateway.sent)


async def test_live_session_preserves_mixed_order_and_tts_accounting():
    directives = [
        TtsSpeak(utterance_id="utt-before", text="Before."),
        Transfer(target="sales"),
        TtsSpeak(utterance_id="utt-after", text="After."),
        SessionEnd(reason="transferred"),
    ]
    clock = ManualClock()
    gateway = LiveDirectiveGateway(clock)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(*directives), session_id="s", clock=clock
        ),
        clock=clock,
        tracer=tracer,
        pricebook=PriceBook(version="card-97"),
    )

    await session.run()
    tracer.flush()

    assert [event.payload for event in gateway.sent] == directives
    cost = next(event for event in exporter.events if event.type == "cost")
    assert cost.attribution["tts_characters"] == len("Before.After.")


async def test_session_end_is_last_control_in_local_gateway_harness():
    clock = ManualClock()
    result = await ConversationHarness("session-end-terminal").run(
        SyntheticCallScenario(
            name="session-end-terminal",
            objective="end without a trailing control",
            turns=[
                SyntheticTurn(speaker="caller", text="done"),
                SyntheticTurn(speaker="caller", text="must never run"),
            ],
            expected_outcome="completed",
        ),
        driver=GraphTurnDriver(
            _graph_emitting(SessionEnd(reason="done")),
            session_id="session-end-terminal",
            clock=clock,
        ),
        clock=clock,
    )

    assert [event.envelope.type for event in result.directives] == ["session.end"]
    assert [
        event.payload.text
        for event in result.events
        if isinstance(event.payload, SttFinal)
    ] == ["done"]
    assert isinstance(result.events[-1].payload, SessionEnded)
    assert result.events[-1].payload.reason == "done"


async def test_graph_stream_end_remains_exactly_once_per_turn():
    directive, _ = await _live_directive("tts.stream_end")
    clock = ManualClock()
    gateway = LiveDirectiveGateway(clock)
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(_graph_emitting(directive), session_id="s", clock=clock),
        clock=clock,
    )

    await session.run()

    assert sum(isinstance(event.payload, TtsStreamEnd) for event in gateway.sent) == 1


class TurnCompletionProbeSession(VoiceSession):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.turn_finished = asyncio.Event()

    async def _run_driver_turn(self, turn, context=None) -> None:
        await super()._run_driver_turn(turn, context)
        self.turn_finished.set()


class GatedSessionEndGateway(LiveDirectiveGateway):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.release_session_end = asyncio.Event()

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="s", seq=1, ts_ms=0),
            SessionStarted(transport="sim", caller="anonymous", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(type="stt.final", session_id="s", turn_id="t0", seq=2, ts_ms=10),
            SttFinal(text="done", provider="local", stt_ms=10),
        )
        await self.stream_ended.wait()
        await self.release_session_end.wait()
        yield ControlEvent(
            Envelope(type="session.ended", session_id="s", seq=3, ts_ms=20),
            SessionEnded(reason="done"),
        )


async def _wait_scheduler_turns(event: asyncio.Event) -> bool:
    for _ in range(TEST_SCHEDULER_TURNS):
        if event.is_set():
            return True
        await asyncio.sleep(0)
    return event.is_set()


async def test_session_end_completes_turn_before_gateway_ends_session():
    clock = ManualClock()
    gateway = GatedSessionEndGateway(clock)
    session = TurnCompletionProbeSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(SessionEnd(reason="done")),
            session_id="s",
            clock=clock,
        ),
        clock=clock,
    )
    run_task = asyncio.create_task(session.run())

    completed_before_session_end = await _wait_scheduler_turns(session.turn_finished)
    if not completed_before_session_end:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        pytest.fail("SessionEnd left the turn waiting for playback")
    gateway.release_session_end.set()
    await run_task


class SpeculativeDirectiveGateway:
    def __init__(
        self,
        *,
        partial: str,
        final: str,
        directives_buffered: asyncio.Event,
    ) -> None:
        self.partial = partial
        self.final = final
        self.directives_buffered = directives_buffered
        self.sent: list[ControlEvent] = []
        self.sent_before_final: list[ControlEvent] = []
        self._inbound: asyncio.Queue[tuple[Envelope, object]] = asyncio.Queue()

    async def send(self, envelope: Envelope, payload: object) -> None:
        event = ControlEvent(envelope, payload)
        self.sent.append(event)
        await self._inbound.put((envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="s", seq=1, ts_ms=0),
            SessionStarted(transport="sim", caller="anonymous", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(type="stt.partial", session_id="s", turn_id="t0", seq=2, ts_ms=10),
            SttPartial(text=self.partial, stability=0.95, provider="local"),
        )
        await self.directives_buffered.wait()
        self.sent_before_final = list(self.sent)
        yield ControlEvent(
            Envelope(type="stt.final", session_id="s", turn_id="t0", seq=3, ts_ms=20),
            SttFinal(text=self.final, provider="local", stt_ms=10),
        )
        while True:
            _, payload = await self._inbound.get()
            if isinstance(payload, (SessionEnd, TtsStreamEnd)):
                break
        yield ControlEvent(
            Envelope(type="session.ended", session_id="s", seq=4, ts_ms=30),
            SessionEnded(reason="done"),
        )


class SpeculativeBufferProbeSession(VoiceSession):
    def __init__(self, *args, directives_buffered: asyncio.Event, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.directives_buffered = directives_buffered

    async def _send_driver_directive(self, turn, directive, context=None) -> None:
        before = len(context.buffered_directives) if context is not None else 0
        await super()._send_driver_directive(turn, directive, context)
        if (
            context is not None
            and context.speculative
            and len(context.buffered_directives) > before
        ):
            self.directives_buffered.set()


async def _run_speculative(partial: str, final: str):
    clock = ManualClock()
    buffered = asyncio.Event()
    gateway = SpeculativeDirectiveGateway(
        partial=partial,
        final=final,
        directives_buffered=buffered,
    )
    session = SpeculativeBufferProbeSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(_dynamic_control_graph(), session_id="s", clock=clock),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
        directives_buffered=buffered,
    )
    await session.run()
    return gateway


async def _run_speculative_directive(wire_name: str, partial: str, final: str):
    directive, coordinator = await _live_directive(wire_name)
    clock = ManualClock()
    buffered = asyncio.Event()
    gateway = SpeculativeDirectiveGateway(
        partial=partial,
        final=final,
        directives_buffered=buffered,
    )
    session = SpeculativeBufferProbeSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(_graph_emitting(directive), session_id="s", clock=clock),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
        recording_coordinator=coordinator,
        directives_buffered=buffered,
    )

    await session.run()
    return gateway, directive


async def test_exact_speculative_promotion_flushes_controls_once_in_order():
    gateway = await _run_speculative("book", "book")

    assert not any(
        isinstance(event.payload, (Transfer, Hold, SessionEnd))
        for event in gateway.sent_before_final
    )
    assert [
        event.payload
        for event in gateway.sent
        if isinstance(event.payload, (Transfer, Hold, SessionEnd))
    ] == [
        Transfer(target="book"),
        Hold(state="hold", music=True),
        SessionEnd(reason="book"),
    ]


async def test_speculative_promotion_preserves_mixed_tts_control_order():
    directives = [
        TtsSpeak(utterance_id="utt-before", text="Before transfer."),
        Transfer(target="sales"),
        TtsSpeak(utterance_id="utt-after", text="After transfer."),
        SessionEnd(reason="transferred"),
    ]
    clock = ManualClock()
    buffered = asyncio.Event()
    gateway = SpeculativeDirectiveGateway(
        partial="  Book   Demo  ",
        final="book demo",
        directives_buffered=buffered,
    )
    session = SpeculativeBufferProbeSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(*directives), session_id="s", clock=clock
        ),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
        directives_buffered=buffered,
    )

    await session.run()

    assert gateway.sent_before_final == []
    assert [event.payload for event in gateway.sent] == directives
    assert [event.envelope.type for event in gateway.sent] == [
        "tts.speak",
        "transfer",
        "tts.speak",
        "session.end",
    ]


@pytest.mark.parametrize("final", ["book demo", "cancel"])
async def test_nonidentical_final_discards_partial_controls_and_reruns(final):
    gateway = await _run_speculative("book", final)

    assert [
        event.payload
        for event in gateway.sent
        if isinstance(event.payload, (Transfer, Hold, SessionEnd))
    ] == [
        Transfer(target=final),
        Hold(state="hold", music=True),
        SessionEnd(reason=final),
    ]


@pytest.mark.parametrize("wire_name", tuple(DOWNSTREAM_TYPES))
@pytest.mark.parametrize(
    ("partial", "final"),
    [
        ("  Book   Demo  ", "book demo"),
        ("book", "book demo"),
    ],
    ids=["normalized-exact", "prefix-revision"],
)
async def test_every_directive_waits_for_safe_graph_promotion(
    wire_name, partial, final
):
    gateway, directive = await _run_speculative_directive(wire_name, partial, final)

    assert directive not in [event.payload for event in gateway.sent_before_final]
    assert [event.payload for event in gateway.sent].count(directive) == 1


async def test_recording_controls_require_session_bound_coordinator_plan():
    coordinator, start = await _recording_plan()
    stop = coordinator.stop("s", start.recording_id)
    assert stop is not None

    assert coordinator.authorizes_control("s", start)
    assert coordinator.authorizes_control("s", stop)
    assert not coordinator.authorizes_control("other-session", start)
    assert not coordinator.authorizes_control("other-session", stop)
    assert not coordinator.authorizes_control(
        "s", RecordingStop(recording_id="rec-unissued")
    )
    assert coordinator.claim_control("s", start)
    assert not coordinator.claim_control("s", start)


@pytest.mark.parametrize(
    ("spec", "tracer", "consent_ref"),
    [
        (RecordingSpec(), Tracer(exporters=[], record_audio=True), "consent"),
        (
            RecordingSpec(enabled=True),
            Tracer(exporters=[], record_audio=False),
            "consent",
        ),
        (
            RecordingSpec(enabled=True),
            Tracer(exporters=[], record_audio=True, sample_rate=0.0),
            "consent",
        ),
        (RecordingSpec(enabled=True), Tracer(exporters=[], record_audio=True), None),
    ],
)
async def test_unplanned_recording_controls_fail_closed(spec, tracer, consent_ref):
    coordinator = RecordingCoordinator(
        spec,
        RecordingBlobStoreSimulator(),
        tracer,
        id_factory=iter(["rec-denied", "blob-denied"]).__next__,
    )

    assert await coordinator.start("s", consent_ref=consent_ref) == []
    assert not coordinator.authorizes_control("s", _base_directives()[-2])


class EagerEndGateway:
    def __init__(self, session_id: str = "s") -> None:
        self.session_id = session_id
        self.sent: list[ControlEvent] = []

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.sent.append(ControlEvent(envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(
                type="session.started", session_id=self.session_id, seq=1, ts_ms=0
            ),
            SessionStarted(transport="sim", caller="anonymous", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id=self.session_id,
                turn_id="t0",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="record", provider="local", stt_ms=10),
        )
        for _ in range(TEST_SCHEDULER_TURNS):
            await asyncio.sleep(0)
        yield ControlEvent(
            Envelope(type="session.ended", session_id=self.session_id, seq=3, ts_ms=20),
            SessionEnded(reason="done"),
        )


async def test_raw_recording_directive_without_coordinator_is_rejected():
    gateway = EagerEndGateway()
    clock = ManualClock()
    raw_start = _base_directives()[-2]
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(_graph_emitting(raw_start), session_id="s", clock=clock),
        clock=clock,
    )

    with pytest.raises(PermissionError, match="recording directive"):
        await session.run()
    assert not any(isinstance(event.payload, RecordingStart) for event in gateway.sent)


async def test_directive_dispatch_failure_cancels_pending_graph_invocation():
    node_cancelled = asyncio.Event()

    async def blocked_node(state: ConversationState, ctx: TurnContext):
        assert ctx.emit is not None
        ctx.emit(_base_directives()[-2])
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            node_cancelled.set()
            raise

    graph = (
        AgentGraph[ConversationState]()
        .add_node("blocked", blocked_node)
        .set_entry("blocked")
        .compile()
    )
    clock = ManualClock()
    session = VoiceSession(
        "s",
        EagerEndGateway(),
        None,
        driver=GraphTurnDriver(graph, session_id="s", clock=clock),
        clock=clock,
    )

    with pytest.raises(PermissionError, match="recording directive"):
        await session.run()

    assert await _wait_scheduler_turns(node_cancelled)


async def _assert_live_recording_rejected(
    directive, coordinator: RecordingCoordinator, *, session_id: str = "s"
) -> None:
    gateway = EagerEndGateway(session_id)
    clock = ManualClock()
    session = VoiceSession(
        session_id,
        gateway,
        None,
        driver=GraphTurnDriver(
            _graph_emitting(directive), session_id=session_id, clock=clock
        ),
        clock=clock,
        recording_coordinator=coordinator,
    )

    with pytest.raises(PermissionError, match="recording directive"):
        await session.run()
    assert directive not in [event.payload for event in gateway.sent]


async def _dispatch_live_recording_once(directive, coordinator) -> None:
    clock = ManualClock()
    gateway = LiveDirectiveGateway(clock)
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=GraphTurnDriver(_graph_emitting(directive), session_id="s", clock=clock),
        clock=clock,
        recording_coordinator=coordinator,
    )
    await session.run()
    assert directive in [event.payload for event in gateway.sent]


async def test_unissued_recording_stop_is_rejected_by_live_session():
    coordinator, start = await _recording_plan()

    await _assert_live_recording_rejected(
        RecordingStop(recording_id=start.recording_id), coordinator
    )


@pytest.mark.parametrize("control", ["start", "stop"])
async def test_structural_clone_of_recording_control_is_rejected_live(control):
    coordinator, start = await _recording_plan()
    directive = start
    if control == "stop":
        directive = coordinator.stop("s", start.recording_id)
        assert directive is not None
    clone = type(directive).model_validate(directive.model_dump())

    await _assert_live_recording_rejected(clone, coordinator)


async def test_stale_stop_cannot_claim_reused_recording_id():
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        RecordingBlobStoreSimulator(),
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(
            ["rec-reused", "blob-first", "rec-reused", "blob-second"]
        ).__next__,
    )
    first = (await coordinator.start("s", consent_ref="consent-live"))[0]
    stale_stop = coordinator.stop("s", first.recording_id)
    assert stale_stop is not None
    assert await coordinator.cancel(first.recording_id)
    current = (await coordinator.start("s", consent_ref="consent-live"))[0]
    current_stop = coordinator.stop("s", current.recording_id)
    assert current_stop is not None

    await _assert_live_recording_rejected(stale_stop, coordinator)
    assert coordinator.claim_control("s", current_stop)


@pytest.mark.parametrize("control", ["start", "stop"])
async def test_cross_session_recording_control_is_rejected_live(control):
    coordinator, start = await _recording_plan()
    directive = start
    if control == "stop":
        directive = coordinator.stop("s", start.recording_id)
        assert directive is not None

    await _assert_live_recording_rejected(
        directive, coordinator, session_id="other-session"
    )


@pytest.mark.parametrize("control", ["start", "stop"])
async def test_replayed_recording_control_is_rejected_live(control):
    coordinator, start = await _recording_plan()
    directive = start
    if control == "stop":
        directive = coordinator.stop("s", start.recording_id)
        assert directive is not None
    await _dispatch_live_recording_once(directive, coordinator)

    await _assert_live_recording_rejected(directive, coordinator)


class CancellableRecordingGateway:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.committed: list[object] = []

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.started.set()
        await self.release.wait()
        self.committed.append(payload)


async def test_cancelled_recording_dispatch_preserves_ambiguous_claim():
    coordinator, start = await _recording_plan()
    gateway = CancellableRecordingGateway()

    async def responder(text: str) -> str:
        return text

    session = VoiceSession(
        "s",
        gateway,
        responder,
        clock=ManualClock(),
        recording_coordinator=coordinator,
    )
    session._closed = False
    dispatch = asyncio.create_task(
        session._send_driver_directive(
            _ActiveTurn(turn_id="t0", user_text="record", stt_ms=0),
            start,
        )
    )
    await gateway.started.wait()

    dispatch.cancel()
    gateway.release.set()
    with pytest.raises(asyncio.CancelledError):
        await dispatch

    assert gateway.committed == [start]
    assert not coordinator.claim_control("s", start)


class StalledTransferGateway(LiveDirectiveGateway):
    def __init__(self, clock: ManualClock, budgets: LatencyBudgets) -> None:
        super().__init__(clock)
        self.budgets = budgets
        self.aborted = False
        self.transfer_cancelled = asyncio.Event()
        self._never = asyncio.Event()

    async def send(self, envelope: Envelope, payload: object) -> None:
        if isinstance(payload, Transfer):
            self.clock.advance(self.budgets.control_commit_ms + 1)
            try:
                await self._never.wait()
            except asyncio.CancelledError:
                self.transfer_cancelled.set()
                raise
            return
        await super().send(envelope, payload)

    async def abort(self) -> None:
        self.aborted = True


class StalledTerminalGateway(StalledTransferGateway):
    async def send(self, envelope: Envelope, payload: object) -> None:
        if isinstance(payload, (SessionEnd, TtsStreamEnd)):
            self.clock.advance(self.budgets.control_commit_ms + 1)
            try:
                await self._never.wait()
            except asyncio.CancelledError:
                raise
            return
        await LiveDirectiveGateway.send(self, envelope, payload)


async def test_stalled_non_tts_directive_aborts_without_late_side_effect():
    clock = ManualClock()
    budgets = LatencyBudgets(control_commit_ms=10)
    gateway = StalledTransferGateway(clock, budgets)

    async def responder(text: str) -> str:
        return text

    session = VoiceSession("s", gateway, responder, clock=clock, budgets=budgets)
    session._closed = False
    await session._send_driver_directive(
        _ActiveTurn(turn_id="t0", user_text="sales", stt_ms=0),
        Transfer(target="sales"),
    )

    assert gateway.aborted is True
    assert gateway.transfer_cancelled.is_set()
    assert not any(isinstance(event.payload, Transfer) for event in gateway.sent)


@pytest.mark.parametrize("terminal", [SessionEnd(reason="done"), TtsStreamEnd()])
async def test_timed_out_terminal_still_blocks_later_directives(terminal):
    clock = ManualClock()
    budgets = LatencyBudgets(control_commit_ms=10)
    gateway = StalledTerminalGateway(clock, budgets)

    async def responder(text: str) -> str:
        return text

    session = VoiceSession("s", gateway, responder, clock=clock, budgets=budgets)
    session._closed = False
    turn = _ActiveTurn(turn_id="t0", user_text="done", stt_ms=0)

    await session._send_driver_directive(turn, terminal)

    with pytest.raises(DirectiveOrderError, match="after terminal directive"):
        await session._send_driver_directive(turn, Transfer(target="late"))
    assert gateway.aborted is True
    assert not any(isinstance(event.payload, Transfer) for event in gateway.sent)


class InvalidOrderDriver:
    async def run_turn(self, user_text, history, *, turn_context=None):
        yield SessionEnd(reason="done")
        yield Transfer(target="late")
        yield TurnDriverReport(assistant_text="", llm_ms=0.0, usage=None)


async def test_session_rejects_late_directive_from_non_graph_driver():
    gateway = EagerEndGateway()
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=InvalidOrderDriver(),
        clock=ManualClock(),
    )

    with pytest.raises(DirectiveOrderError, match="after terminal directive"):
        await session.run()
    assert not any(
        isinstance(event.payload, Transfer) and event.payload.target == "late"
        for event in gateway.sent
    )
