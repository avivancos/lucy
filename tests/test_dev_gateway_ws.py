import asyncio
import socket

import uvicorn

from lucy.evals import booking_happy_path
from lucy.serve.app import create_app
from lucy.transport.dev_gateway_ws import run_dev_gateway


def test_dev_gateway_ws_runner_completes_scenario_against_local_server():
    report = asyncio.run(_run_scenario())

    assert report["clean_close"] is True
    assert report["turns"] == 2
    assert report["tts_speak_received"] == 2
    assert report["playback_finished"] == 2


async def _run_scenario():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), log_level="error", lifespan="off")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0)

    try:
        report = await run_dev_gateway(
            f"ws://127.0.0.1:{port}/v1/session/ws", booking_happy_path()
        )
    finally:
        server.should_exit = True
        await task

    return report
