"""Run the deterministic development gateway over the real WS protocol."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from lucy.evals import SyntheticCallScenario, booking_happy_path
from lucy.settings import GatewayControlSettings
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.golden import canonical_dumps
from lucy.transport.schema import SttFinal, TtsPlayback, TtsSpeak, parse_event, to_wire

SESSION_WS_URL_ENV = "LUCY_SESSION_WS_URL"


async def run_dev_gateway(url: str, scenario: SyntheticCallScenario) -> dict[str, Any]:
    settings = GatewayControlSettings()
    if settings.control_token is None:
        raise RuntimeError("LUCY_GATEWAY_CONTROL_TOKEN is required")
    authorization = f"Bearer {settings.control_token.get_secret_value()}"
    simulator = LocalGatewaySimulator(scenario)
    report: dict[str, Any] = {
        "session_id": simulator.session_id,
        "turns": 0,
        "tts_speak_received": 0,
        "playback_finished": 0,
        "marks_emitted": 0,
        "rtt_ms_last": 0.0,
        "jitter_ms_last": 0.0,
        "clean_close": False,
    }

    async with websockets.connect(
        url, additional_headers={"Authorization": authorization}
    ) as websocket:

        async def send_events() -> None:
            async for event in simulator.events():
                if isinstance(event.payload, SttFinal):
                    report["turns"] += 1
                elif isinstance(event.payload, TtsPlayback):
                    if event.payload.state == "mark":
                        report["marks_emitted"] += 1
                    elif event.payload.state == "finished":
                        report["playback_finished"] += 1
                await websocket.send(
                    canonical_dumps(to_wire(event.envelope, event.payload))
                )

        sender = asyncio.create_task(send_events())
        try:
            async for raw in websocket:
                event = parse_event(json.loads(raw))
                if isinstance(event.payload, TtsSpeak):
                    report["tts_speak_received"] += 1
                await simulator.send(event.envelope, event.payload)
        except ConnectionClosed:
            pass
        await sender
        report["clean_close"] = True
    return report


async def _main() -> None:
    url = os.environ.get(SESSION_WS_URL_ENV)
    if not url:
        raise RuntimeError(f"{SESSION_WS_URL_ENV} is required")
    report = await run_dev_gateway(url, booking_happy_path())
    print(canonical_dumps(report))


if __name__ == "__main__":
    asyncio.run(_main())
