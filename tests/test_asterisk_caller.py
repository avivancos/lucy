# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
from socketserver import BaseRequestHandler, TCPServer
import sys
from threading import Event, Thread
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_secret_fixtures import synthetic_ari_password

ARI_USER = "local-ari-user"
ARI_PASSWORD = synthetic_ari_password()
SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "infra/asterisk/scripts/originate_call.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SCRIPT_SPEC = importlib.util.spec_from_file_location("originate_call", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
originate_call = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(originate_call)


class _AriServer(ThreadingHTTPServer):
    complete_on_originate: bool
    requests: list[tuple[str, str, dict[str, list[str]]]]
    response_gate: Event
    stall_requests: bool


class _GatewayServer(ThreadingHTTPServer):
    requests: int
    complete_after_first_request: bool
    payload_override: object | None


class _AriHandler(BaseHTTPRequestHandler):
    server: _AriServer

    def _authorized(self) -> bool:
        encoded = base64.b64encode(f"{ARI_USER}:{ARI_PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == f"Basic {encoded}"

    def _respond(self, status: int, payload: object | None = None) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append((self.command, parsed.path, query))
        return parsed.path, query

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract.
        if self.server.stall_requests:
            self.server.response_gate.wait()
            return
        if not self._authorized():
            self._respond(401, {"message": "Authentication required"})
            return
        path, _ = self._record()
        if path == "/ari/channels" and self.server.complete_on_originate:
            self.server.result = "completed"
        self._respond(204)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract.
        if not self._authorized():
            self._respond(401, {"message": "Authentication required"})
            return
        path, _ = self._record()
        if path == "/ari/malformed":
            body = b"{not-json"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/ari/non-object":
            self._respond(200, ["not", "an", "object"])
        else:
            self._respond(200, {"value": self.server.result})

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _GatewayHandler(BaseHTTPRequestHandler):
    server: _GatewayServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract.
        self.server.requests += 1
        completed = int(
            self.server.complete_after_first_request and self.server.requests > 1
        )
        payload = self.server.payload_override
        if payload is None:
            payload = {
                "service": "lucy-media-gateway",
                "status": "ok",
                "asterisk_sessions_started": completed,
                "asterisk_sessions_completed": completed,
                "asterisk_sessions_failed": 0,
                "asterisk_audio_bytes_received": completed * 640,
                "asterisk_audio_bytes_sent": completed * 640,
                "control_messages_forwarded": completed * 2,
            }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ManualPacing:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _ResetHandler(BaseRequestHandler):
    def handle(self) -> None:
        self.request.close()


@contextmanager
def _ari_server(
    *, complete_on_originate: bool, stall_requests: bool = False
) -> Iterator[_AriServer]:
    server = _AriServer(("127.0.0.1", 0), _AriHandler)
    server.complete_on_originate = complete_on_originate
    server.requests = []
    server.result = "pending"
    server.response_gate = Event()
    server.stall_requests = stall_requests
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.response_gate.set()
        server.shutdown()
        thread.join()
        server.server_close()


@contextmanager
def _gateway_server(
    *, complete_after_first_request: bool, payload_override: object | None = None
) -> Iterator[_GatewayServer]:
    server = _GatewayServer(("127.0.0.1", 0), _GatewayHandler)
    server.requests = 0
    server.complete_after_first_request = complete_after_first_request
    server.payload_override = payload_override
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _configure(monkeypatch: pytest.MonkeyPatch, server: _AriServer) -> None:
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_HOST", "127.0.0.1")
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_PORT", str(server.server_port))
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_USER", ARI_USER)
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_PASSWORD", ARI_PASSWORD)
    monkeypatch.setenv("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("LUCY_TELEPHONY_TEST_EXTENSION", "lab-check")


def _configure_gateway(monkeypatch: pytest.MonkeyPatch, server: _GatewayServer) -> None:
    monkeypatch.setenv("LUCY_TELEPHONY_TEST_EXTENSION", "lucy-audiosocket")
    monkeypatch.setenv("LUCY_GATEWAY_HEALTH_HOST", "127.0.0.1")
    monkeypatch.setenv("LUCY_GATEWAY_HEALTH_PORT", str(server.server_port))


def test_caller_uses_real_ari_requests(monkeypatch, capsys):
    with _ari_server(complete_on_originate=True) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "1")
        originate_call.main()

    assert "WAV playback observed by Asterisk" in capsys.readouterr().out
    assert [(method, path) for method, path, _query in server.requests] == [
        ("POST", "/ari/asterisk/variable"),
        ("POST", "/ari/channels"),
        ("GET", "/ari/asterisk/variable"),
    ]
    originate_query = server.requests[1][2]
    assert originate_query["endpoint"] == ["Local/lab-check@lucy-lab"]
    assert "channelId" not in originate_query


def test_caller_proves_real_audio_socket_adapter_progress(monkeypatch, capsys):
    with (
        _ari_server(complete_on_originate=False) as ari_server,
        _gateway_server(complete_after_first_request=True) as gateway_server,
    ):
        _configure(monkeypatch, ari_server)
        _configure_gateway(monkeypatch, gateway_server)
        originate_call.main()

    assert "AudioSocket adapter completed" in capsys.readouterr().out
    assert [(method, path) for method, path, _query in ari_server.requests] == [
        ("POST", "/ari/channels"),
    ]
    originate_query = ari_server.requests[0][2]
    assert originate_query["endpoint"] == ["Local/lucy-audiosocket@lucy-lab"]
    assert originate_query["extension"] == ["play-fixture"]
    assert gateway_server.requests == 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (["not", "an", "object"], "non-object"),
        (
            {
                "service": "lucy-media-gateway",
                "status": "ok",
                "asterisk_sessions_started": True,
                "asterisk_sessions_completed": 0,
                "asterisk_sessions_failed": 0,
                "asterisk_audio_bytes_received": 0,
                "asterisk_audio_bytes_sent": 0,
                "control_messages_forwarded": 0,
            },
            "invalid asterisk_sessions_started metric",
        ),
        (
            {
                "service": "unexpected",
                "status": "ok",
                "asterisk_sessions_started": 0,
                "asterisk_sessions_completed": 0,
                "asterisk_sessions_failed": 0,
                "asterisk_audio_bytes_received": 0,
                "asterisk_audio_bytes_sent": 0,
                "control_messages_forwarded": 0,
            },
            "unexpected service status",
        ),
    ],
)
def test_caller_rejects_invalid_gateway_health(monkeypatch, payload, message):
    with _gateway_server(
        complete_after_first_request=False,
        payload_override=payload,
    ) as server:
        _configure_gateway(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "1")
        with pytest.raises(SystemExit, match=message):
            originate_call._gateway_health()


def test_caller_reports_invalid_ari_credentials(monkeypatch):
    with _ari_server(complete_on_originate=True) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_PASSWORD", "wrong-password")
        with pytest.raises(
            SystemExit,
            match="ARI request POST /asterisk/variable failed with HTTP 401",
        ):
            originate_call.main()


def test_caller_reports_deadline_when_call_never_completes(monkeypatch):
    pacing = _ManualPacing()
    with _ari_server(complete_on_originate=False) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "0.1")
        monkeypatch.setenv("LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS", "0.1")
        with pytest.raises(SystemExit, match="within 0.1s"):
            originate_call.main(
                monotonic=pacing.monotonic,
                sleep=pacing.sleep,
            )

    assert "/ari/channels" in [path for _method, path, _query in server.requests]
    assert server.requests[-1][1] == "/ari/asterisk/variable"


def test_caller_reports_ari_response_timeout(monkeypatch):
    with _ari_server(complete_on_originate=False, stall_requests=True) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "0.1")
        with pytest.raises(
            SystemExit,
            match="ARI request POST /asterisk/variable timed out after 0.1s",
        ):
            originate_call.main()


def test_caller_deadline_uses_injected_pacing(monkeypatch):
    pacing = _ManualPacing()
    with _ari_server(complete_on_originate=False) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "0.2")
        monkeypatch.setenv("LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS", "0.1")
        with pytest.raises(SystemExit, match="within 0.2s"):
            originate_call.main(
                monotonic=pacing.monotonic,
                sleep=pacing.sleep,
            )

    assert pacing.sleeps == [0.1, 0.1]


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "-1"),
        ("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "0"),
        ("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "nan"),
        ("LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS", "inf"),
        ("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_caller_rejects_unsafe_timeouts(monkeypatch, setting, value):
    with _ari_server(complete_on_originate=True) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv(setting, value)
        with pytest.raises(SystemExit, match=setting):
            originate_call.main()


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("LUCY_TELEPHONY_ARI_HOST", "asterisk;include bad.conf"),
        ("LUCY_TELEPHONY_ARI_PORT", "70000"),
        ("LUCY_TELEPHONY_ARI_USER", "lucy\n[injected]"),
        ("LUCY_TELEPHONY_ARI_PASSWORD", "lucy\rmalicious"),
    ],
)
def test_caller_rejects_unsafe_ari_endpoint_settings(monkeypatch, setting, value):
    with _ari_server(complete_on_originate=True) as server:
        _configure(monkeypatch, server)
        monkeypatch.setenv(setting, value)
        with pytest.raises(SystemExit, match=setting):
            originate_call.main()


def test_caller_reports_ari_connection_failure(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_HOST", "127.0.0.1")
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_PORT", str(port))
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_USER", ARI_USER)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_PASSWORD", ARI_PASSWORD)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "1")
        with pytest.raises(SystemExit, match="ARI request GET /asterisk/info failed"):
            originate_call._request("GET", "/asterisk/info", {})


def test_caller_reports_ari_peer_reset(monkeypatch):
    server = TCPServer(("127.0.0.1", 0), _ResetHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_HOST", "127.0.0.1")
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_PORT", str(server.server_address[1]))
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_USER", ARI_USER)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_PASSWORD", ARI_PASSWORD)
        monkeypatch.setenv("LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS", "1")
        with pytest.raises(SystemExit, match="ARI request GET /asterisk/info failed"):
            originate_call._request("GET", "/asterisk/info", {})
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/malformed", "returned invalid JSON"),
        ("/non-object", "returned a non-object payload"),
    ],
)
def test_caller_rejects_invalid_ari_payloads(monkeypatch, path, message):
    with _ari_server(complete_on_originate=True) as server:
        _configure(monkeypatch, server)
        with pytest.raises(SystemExit, match=message):
            originate_call._request("GET", path, {})
