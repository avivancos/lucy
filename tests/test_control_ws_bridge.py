import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lucy.serve.app import create_app
from lucy.serve.control_ws import CONTROL_WS_PATH
from lucy.transport.golden import canonical_dumps

CONTROL_TOKEN = "test-control-token"
CONTROL_HEADERS = {"authorization": f"Bearer {CONTROL_TOKEN}"}


@pytest.fixture(autouse=True)
def configured_control_token(monkeypatch):
    monkeypatch.setenv("LUCY_GATEWAY_CONTROL_TOKEN", CONTROL_TOKEN)


def wire(type_, *, seq=0, turn_id=None, **payload):
    return {
        "v": 1,
        "type": type_,
        "session_id": "sess-ws-test",
        "turn_id": turn_id,
        "seq": seq,
        "ts_ms": 1_700_000_000_000 + seq,
        **payload,
    }


def start_session(socket):
    socket.send_json(
        wire(
            "session.started",
            transport="test",
            caller="fixture-caller",
            codecs=["pcm16/8000"],
            features=[],
        )
    )
    configured = socket.receive_text()
    assert canonical_dumps(json.loads(configured)) == configured
    configured_wire = json.loads(configured)
    assert configured_wire["type"] == "session.configure"
    assert configured_wire["seq"] == 0


def test_first_message_must_be_session_started():
    with TestClient(create_app()).websocket_connect(
        CONTROL_WS_PATH, headers=CONTROL_HEADERS
    ) as socket:
        socket.send_json(
            wire(
                "stt.final",
                text="too early",
                provider="fixture",
                stt_ms=0,
            )
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
        assert closed.value.code == 1002


def test_ws_session_runs_turns_and_returns_tts_speak():
    app = create_app()
    with TestClient(app).websocket_connect(
        CONTROL_WS_PATH, headers=CONTROL_HEADERS
    ) as socket:
        start_session(socket)
        socket.send_json(
            wire(
                "stt.final",
                seq=1,
                turn_id="turn-1",
                text="hello",
                provider="fixture",
                stt_ms=12,
            )
        )
        speak_raw = socket.receive_text()
        barrier_raw = socket.receive_text()
        assert canonical_dumps(json.loads(speak_raw)) == speak_raw
        speak_wire = json.loads(speak_raw)
        assert speak_wire["type"] == "tts.speak"
        assert speak_wire["seq"] == 1
        assert json.loads(barrier_raw)["type"] == "tts.stream_end"
        utterance_id = json.loads(speak_raw)["utterance_id"]
        socket.send_json(
            wire(
                "tts.playback",
                seq=2,
                turn_id="turn-1",
                utterance_id=utterance_id,
                state="started",
                mark_chars=0,
            )
        )
        socket.send_json(
            wire(
                "tts.playback",
                seq=3,
                turn_id="turn-1",
                utterance_id=utterance_id,
                state="finished",
                mark_chars=len("You said: hello"),
            )
        )
        socket.send_json(wire("session.ended", seq=4, reason="complete"))

    records = app.state.control_session_records["sess-ws-test"]
    assert [record.user_text for record in records] == ["hello"]
    assert records[0].assistant_text == "You said: hello"


def test_transport_metrics_rtt_fills_waterfall_transport_ms():
    app = create_app()
    with TestClient(app).websocket_connect(
        CONTROL_WS_PATH, headers=CONTROL_HEADERS
    ) as socket:
        start_session(socket)
        socket.send_json(
            wire(
                "stt.final",
                seq=1,
                turn_id="turn-rtt",
                text="measure me",
                provider="fixture",
                stt_ms=8,
            )
        )
        speak = json.loads(socket.receive_text())
        socket.receive_text()
        socket.send_json(
            wire(
                "transport.metrics",
                seq=2,
                turn_id="turn-rtt",
                jitter_ms=1.0,
                rtt_ms=17.5,
                packet_loss=0.0,
            )
        )
        socket.send_json(
            wire(
                "tts.playback",
                seq=3,
                turn_id="turn-rtt",
                utterance_id=speak["utterance_id"],
                state="started",
                mark_chars=0,
            )
        )
        socket.send_json(
            wire(
                "tts.playback",
                seq=4,
                turn_id="turn-rtt",
                utterance_id=speak["utterance_id"],
                state="finished",
                mark_chars=len(speak["text"]),
            )
        )
        socket.send_json(wire("session.ended", seq=5, reason="complete"))

    records = app.state.control_session_records["sess-ws-test"]
    assert records[0].waterfall.transport_ms == 17.5


@pytest.mark.parametrize(
    "headers",
    ({}, {"authorization": "Bearer wrong-control-token"}),
)
def test_control_socket_rejects_missing_or_incorrect_bearer_before_accept(headers):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with TestClient(create_app()).websocket_connect(
            CONTROL_WS_PATH, headers=headers
        ):
            pass
    assert rejected.value.code == 1008
