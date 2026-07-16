"""WebSocket bridge between the media gateway and ``VoiceSession``."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from lucy.session import TurnRecord, VoiceSession
from lucy.settings import CpaasTransportSettings, GatewayControlSettings
from lucy.specs import InvalidCpaasTransportSpecError, resolve_cpaas_transport
from lucy.transport.golden import canonical_dumps
from lucy.transport.schema import (
    ControlEvent,
    Envelope,
    SessionConfigure,
    SessionStarted,
    TransportMetrics,
    parse_event,
    to_wire,
)

CONTROL_WS_PATH = "/v1/session/ws"
PROTOCOL_ERROR_CODE = 1002
POLICY_VIOLATION_CODE = 1008
DEFAULT_STT_PROFILE = "gateway"
DEFAULT_TTS_PROFILE = "gateway"
DEFAULT_VAD_PROFILE = "gateway"

Responder = Callable[[str], Awaitable[str]]


async def echo_responder(text: str) -> str:
    return f"You said: {text}"


class WebSocketGatewayTransport:
    def __init__(self, websocket: WebSocket, started: ControlEvent) -> None:
        self.websocket = websocket
        self.started = started
        self.rtt_by_turn: dict[str, float] = {}
        self._outbound_seq = 0

    async def events(self) -> AsyncIterator[ControlEvent]:
        yield self.started
        while True:
            try:
                raw = await self.websocket.receive_text()
            except WebSocketDisconnect:
                return
            event = parse_event(json.loads(raw))
            if isinstance(event.payload, TransportMetrics) and event.envelope.turn_id:
                self.rtt_by_turn[event.envelope.turn_id] = event.payload.rtt_ms
            yield event

    async def send(self, envelope: Envelope, payload: object) -> None:
        wire_envelope = envelope.model_copy(update={"seq": self._outbound_seq})
        self._outbound_seq += 1
        await self.websocket.send_text(canonical_dumps(to_wire(wire_envelope, payload)))

    def apply_transport_metrics(self, records: list[TurnRecord]) -> None:
        for record in records:
            record.waterfall.transport_ms = self.rtt_by_turn.get(record.turn_id, 0.0)


def register_control_ws(
    app: FastAPI,
    responder: Responder = echo_responder,
    settings: GatewayControlSettings | None = None,
) -> None:
    if not hasattr(app.state, "control_session_records"):
        app.state.control_session_records = {}
    chosen_settings = settings or GatewayControlSettings()
    expected_token = (
        chosen_settings.control_token.get_secret_value()
        if chosen_settings.control_token is not None
        else None
    )

    @app.websocket(CONTROL_WS_PATH)
    async def control_session(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization", "")
        supplied_token = (
            authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ")
            else ""
        )
        if expected_token is None or not secrets.compare_digest(
            supplied_token, expected_token
        ):
            await websocket.close(code=POLICY_VIOLATION_CODE)
            return
        await websocket.accept()
        try:
            first = parse_event(json.loads(await websocket.receive_text()))
        except (ValueError, TypeError, WebSocketDisconnect):
            await websocket.close(code=PROTOCOL_ERROR_CODE)
            return
        if not isinstance(first.payload, SessionStarted):
            await websocket.close(code=PROTOCOL_ERROR_CODE)
            return

        cpaas_provider = None
        telephony_country_code = None
        if first.payload.transport.startswith("cpaas/"):
            try:
                cpaas_provider = resolve_cpaas_transport(
                    first.payload.transport
                ).provider
            except InvalidCpaasTransportSpecError:
                await websocket.close(code=PROTOCOL_ERROR_CODE)
                return
            cpaas_settings = CpaasTransportSettings()
            if (
                cpaas_settings.provider is not None
                and cpaas_settings.provider is not cpaas_provider
            ) or cpaas_settings.country_code is None:
                await websocket.close(code=PROTOCOL_ERROR_CODE)
                return
            telephony_country_code = cpaas_settings.country_code

        transport = WebSocketGatewayTransport(websocket, first)
        await transport.send(
            Envelope(
                type="session.configure",
                session_id=first.envelope.session_id,
                seq=0,
                ts_ms=0,
            ),
            SessionConfigure(
                stt=DEFAULT_STT_PROFILE,
                tts=DEFAULT_TTS_PROFILE,
                vad=DEFAULT_VAD_PROFILE,
            ),
        )
        session = VoiceSession(
            first.envelope.session_id,
            transport,
            responder=responder,
            cpaas_provider=cpaas_provider,
            telephony_country_code=telephony_country_code,
        )
        records = await session.run()
        transport.apply_transport_metrics(records)
        app.state.control_session_records[first.envelope.session_id] = records
        try:
            await websocket.close()
        except RuntimeError:
            pass
