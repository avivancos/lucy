import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_gateway(profile: str, service: str) -> dict:
    environment = {**os.environ, "LUCY_API_HOST_PORT": "0"}
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            profile,
            "run",
            "--rm",
            "--build",
            service,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode:
        details = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        pytest.fail(f"{service} failed\n{details}")
    report_line = next(
        line for line in reversed(completed.stdout.splitlines()) if line.startswith("{")
    )
    return json.loads(report_line)


@pytest.fixture(scope="module", autouse=True)
def stop_gateway_stack():
    yield
    subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "gateway-it",
            "--profile",
            "test",
            "down",
            "--remove-orphans",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.gateway_it
def test_rust_gateway_oneshot_session_reports_clean_close():
    report = run_gateway("gateway-it", "lucy-gateway-session")

    assert report["clean_close"] is True
    assert report["turns"] == 2
    assert report["playback_finished"] == report["tts_speak_received"] >= 2
    assert report["rtt_ms_last"] > 0


@pytest.mark.gateway_it
def test_dev_and_rust_gateways_report_identical_turn_counts():
    rust = run_gateway("gateway-it", "lucy-gateway-session")
    dev = run_gateway("test", "lucy-dev-gateway")

    assert dev["clean_close"] is True
    assert dev["turns"] == rust["turns"]
    assert dev["playback_finished"] == rust["playback_finished"]
