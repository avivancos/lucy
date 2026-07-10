import asyncio

import pytest

from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.nodes.telephony import (
    DTMF_TIMEOUT,
    DialConfig,
    DialNode,
    DtmfMenuConfig,
    DtmfMenuNode,
    EndCallConfig,
    EndCallNode,
    EndReason,
    HoldConfig,
    HoldNode,
    TransferConfig,
    TransferNode,
    VoicemailDetectConfig,
    VoicemailDetectNode,
)
from lucy.runtime import TurnContext
from lucy.state import ConversationState
from lucy.transport.schema import (
    AmdResult,
    Dial,
    Envelope,
    Hold,
    SessionEnd,
    Transfer,
    TtsSpeak,
    UnknownControlMessage,
    parse_event,
)
from lucy.transport.dev_gateway import LocalGatewaySimulator


def _event(message_type: str, **payload):
    return {
        "v": 1,
        "type": message_type,
        "session_id": "session-1",
        "seq": 1,
        "ts_ms": 1,
        **payload,
    }


def test_dial_hold_amd_round_trip_through_parse_event():
    dial = parse_event(_event("dial", target="100", caller_id="200", timeout_ms=500))
    hold = parse_event(_event("hold", state="hold", music=True))
    amd = parse_event(_event("amd.result", outcome="human", confidence=0.9))

    assert dial.payload == Dial(target="100", caller_id="200", timeout_ms=500)
    assert hold.payload == Hold(state="hold", music=True)
    assert amd.payload == AmdResult(outcome="human", confidence=0.9)


def test_unknown_type_still_rejected_after_additions():
    with pytest.raises(UnknownControlMessage):
        parse_event(_event("telephony.unknown"))


async def test_transfer_node_emits_transfer_directive():
    emitted = []
    node = TransferNode(
        TransferConfig(
            target="sales",
            mode="attended",
            announce_template="Connecting you to sales.",
        )
    )

    await node(ConversationState(), TurnContext(emit=emitted.append))

    assert isinstance(emitted[0], TtsSpeak)
    assert emitted[1] == Transfer(target="sales")


async def test_dtmf_menu_routes_digit_and_returns_timeout_after_retries():
    clock = ManualClock()
    node = DtmfMenuNode(
        DtmfMenuConfig(
            prompt_template="Press one for sales.",
            options={"1": "sales"},
            timeout_ms=10,
            max_retries=1,
        )
    )

    selected = await node(
        ConversationState(),
        TurnContext(payload={"dtmf_digits": ["1"]}),
    )
    emitted = []
    timeout_task = asyncio.create_task(
        node(
            ConversationState(),
            TurnContext(payload={"dtmf_digits": []}, emit=emitted.append, clock=clock),
        )
    )
    await asyncio.sleep(0)
    clock.advance(10)
    await asyncio.sleep(0)
    clock.advance(10)
    timed_out = await timeout_task

    assert selected["agent_state"]["dtmf_selection"] == "sales"
    assert timed_out["agent_state"]["dtmf_selection"] == DTMF_TIMEOUT
    assert len(emitted) == 2


async def test_voicemail_detect_routes_amd_outcome():
    node = VoicemailDetectNode(VoicemailDetectConfig())

    update = await node(
        ConversationState(),
        TurnContext(
            payload={"amd_result": AmdResult(outcome="machine", confidence=0.8)}
        ),
    )

    assert update["agent_state"]["amd_outcome"] == "machine"


async def test_dial_node_emits_dial_and_reports_answer_outcome():
    emitted = []
    node = DialNode(DialConfig(target="100", caller_id="200", timeout_ms=500))

    update = await node(
        ConversationState(),
        TurnContext(payload={"dial_outcome": "answered"}, emit=emitted.append),
    )

    assert emitted == [Dial(target="100", caller_id="200", timeout_ms=500)]
    assert update["agent_state"]["dial_outcome"] == "answered"


async def test_dial_node_uses_injected_clock_for_no_answer_timeout():
    clock = ManualClock()
    node = DialNode(DialConfig(target="100", caller_id="200", timeout_ms=500))
    task = asyncio.create_task(
        node(ConversationState(), TurnContext(payload={}, clock=clock))
    )
    await asyncio.sleep(0)
    assert task.done() is False

    clock.advance(500)
    update = await task

    assert update["agent_state"]["dial_outcome"] == "no_answer"


async def test_hold_node_emits_hold_then_resume():
    clock = ManualClock()
    emitted = []
    node = HoldNode(HoldConfig(hold_ms=100, music=True))
    task = asyncio.create_task(
        node(ConversationState(), TurnContext(clock=clock, emit=emitted.append))
    )
    await asyncio.sleep(0)
    clock.advance(100)
    await task

    assert emitted == [Hold(state="hold", music=True), Hold(state="resume", music=True)]


async def test_end_call_emits_session_end_with_typed_reason():
    emitted = []
    node = EndCallNode(EndCallConfig(reason=EndReason.BOOKED))

    await node(ConversationState(), TurnContext(emit=emitted.append))

    assert emitted == [SessionEnd(reason="booked")]


async def test_gateway_scripts_dtmf_amd_and_records_directives():
    gateway = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        dtmf_steps=["1"],
        amd_steps=[AmdResult(outcome="human", confidence=0.9)],
    )
    events = gateway.events()

    assert (await events.__anext__()).envelope.type == "session.started"
    assert (await events.__anext__()).payload.model_dump() == {"digit": "1"}
    assert (await events.__anext__()).payload == AmdResult(
        outcome="human", confidence=0.9
    )

    directive = Transfer(target="sales")
    await gateway.send(
        Envelope(
            type="transfer",
            session_id="sess_sim",
            seq=1,
            ts_ms=1,
        ),
        directive,
    )
    assert gateway.directives == [directive]
    await events.aclose()
