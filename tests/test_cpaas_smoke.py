# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import base64

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from lucy.settings import CpaasTransportSettings
from lucy.transport.cpaas_smoke import (
    MAX_SMOKE_RESPONSE_BYTES,
    _run,
    place_cpaas_call,
)
from tests.test_secret_fixtures import (
    synthetic_cpaas_api_key,
    synthetic_cpaas_stream_token,
    synthetic_twilio_auth_token,
)

_CPaaS_API_KEY = synthetic_cpaas_api_key()
_CPaaS_STREAM_TOKEN = synthetic_cpaas_stream_token()
_TWILIO_AUTH_TOKEN = synthetic_twilio_auth_token()


@pytest.mark.asyncio
async def test_telnyx_smoke_uses_typed_stream_configuration_and_bearer_auth():
    app = FastAPI()
    seen = {}

    @app.post("/v2/calls")
    async def dial(request: Request):
        seen["authorization"] = request.headers["authorization"]
        seen["payload"] = await request.json()
        return {"data": {"call_control_id": "redacted"}}

    settings = CpaasTransportSettings(
        provider="telnyx",
        account_id="connection-redacted",
        api_key=_CPaaS_API_KEY,
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url="http://127.0.0.1",
        public_ws_url="wss://voice.example.test/cpaas/telnyx",
        stream_auth_token=_CPaaS_STREAM_TOKEN,
        allow_insecure_local=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await place_cpaas_call(settings, client=client)

    assert result.provider == "telnyx"
    assert result.accepted is True
    assert seen["authorization"] == f"Bearer {_CPaaS_API_KEY}"
    assert seen["payload"] == {
        "connection_id": "connection-redacted",
        "to": "+34600000002",
        "from": "+34600000001",
        "stream_url": "wss://voice.example.test/cpaas/telnyx",
        "stream_track": "inbound_track",
        "stream_bidirectional_mode": "rtp",
        "stream_bidirectional_codec": "L16",
        "stream_bidirectional_sampling_rate": 16000,
        "stream_auth_token": _CPaaS_STREAM_TOKEN,
    }
    assert _CPaaS_API_KEY not in repr(result)


@pytest.mark.asyncio
async def test_twilio_smoke_uses_basic_auth_and_bidirectional_twiml():
    app = FastAPI()
    seen = {}

    @app.post("/2010-04-01/Accounts/ACREDACTED/Calls.json")
    async def dial(request: Request):
        seen["authorization"] = request.headers["authorization"]
        seen["form"] = (await request.body()).decode()
        return {"sid": "redacted", "status": "queued"}

    settings = CpaasTransportSettings(
        provider="twilio",
        account_id="ACREDACTED",
        api_key=_TWILIO_AUTH_TOKEN,
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url="http://127.0.0.1",
        public_ws_url="wss://voice.example.test/cpaas/twilio",
        allow_insecure_local=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await place_cpaas_call(settings, client=client)

    expected_auth = base64.b64encode(
        f"ACREDACTED:{_TWILIO_AUTH_TOKEN}".encode()
    ).decode()
    assert seen["authorization"] == f"Basic {expected_auth}"
    assert "To=%2B34600000002" in seen["form"]
    assert "From=%2B34600000001" in seen["form"]
    assert "%3CConnect%3E%3CStream" in seen["form"]
    assert result.provider == "twilio"


def test_smoke_settings_report_stream_and_live_call_requirements(monkeypatch):
    monkeypatch.setenv("LUCY_CPAAAS_PROVIDER", "telnyx")
    monkeypatch.setenv("LUCY_CPAAAS_ACCOUNT_ID", "connection-redacted")
    monkeypatch.setenv("LUCY_CPAAAS_API_KEY", "fixture-cpaas-credential")
    monkeypatch.setenv("LUCY_CPAAAS_FROM_NUMBER", "+34600000001")
    monkeypatch.setenv("LUCY_CPAAAS_TO_NUMBER", "+34600000002")

    settings = CpaasTransportSettings()

    assert settings.missing_credentials == ()
    assert settings.missing_stream_configuration == (
        "LUCY_CPAAAS_API_BASE_URL",
        "LUCY_CPAAAS_PUBLIC_WS_URL",
        "LUCY_CPAAAS_STREAM_AUTH_TOKEN",
    )


@pytest.mark.asyncio
async def test_smoke_rejects_non_https_provider_endpoint_outside_localhost():
    settings = CpaasTransportSettings(
        provider="telnyx",
        account_id="connection-redacted",
        api_key="fixture-cpaas-credential",
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url="http://api.example.test",
        public_ws_url="wss://voice.example.test/cpaas/telnyx",
        stream_auth_token=_CPaaS_STREAM_TOKEN,
    )

    with pytest.raises(ValueError, match="HTTPS"):
        await place_cpaas_call(settings)


def test_unconfigured_smoke_entrypoint_skips_with_named_safe_message(
    monkeypatch, capsys
):
    for name in (
        "LUCY_CPAAAS_PROVIDER",
        "LUCY_CPAAAS_ACCOUNT_ID",
        "LUCY_CPAAAS_API_KEY",
        "LUCY_CPAAAS_FROM_NUMBER",
        "LUCY_CPAAAS_TO_NUMBER",
        "LUCY_CPAAAS_API_BASE_URL",
        "LUCY_CPAAAS_PUBLIC_WS_URL",
        "LUCY_CPAAAS_STREAM_AUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    assert asyncio.run(_run()) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.startswith("CPaaS live smoke skipped; missing: ")
    assert "LUCY_CPAAAS_API_KEY" in output.out


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (401, 429, 503))
async def test_smoke_reports_provider_http_failures_without_response_or_secrets(
    status_code,
):
    app = FastAPI()

    @app.post("/v2/calls")
    async def dial():
        return Response(
            status_code=status_code,
            content=b"fixture-credential +34600000001 provider-body",
        )

    settings = CpaasTransportSettings(
        provider="telnyx",
        account_id="connection-redacted",
        api_key=_CPaaS_API_KEY,
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url="http://127.0.0.1",
        public_ws_url="wss://voice.example.test/cpaas/telnyx",
        stream_auth_token=_CPaaS_STREAM_TOKEN,
        allow_insecure_local=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        with pytest.raises(RuntimeError) as failed:
            await place_cpaas_call(settings, client=client)

    assert str(failed.value) == f"CPaaS smoke returned HTTP {status_code}"


@pytest.mark.asyncio
async def test_smoke_rejects_oversized_provider_response():
    app = FastAPI()

    @app.post("/v2/calls")
    async def dial():
        return Response(
            status_code=200,
            content=b"x" * (MAX_SMOKE_RESPONSE_BYTES + 1),
        )

    settings = CpaasTransportSettings(
        provider="telnyx",
        account_id="connection-redacted",
        api_key=_CPaaS_API_KEY,
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url="http://127.0.0.1",
        public_ws_url="wss://voice.example.test/cpaas/telnyx",
        stream_auth_token=_CPaaS_STREAM_TOKEN,
        allow_insecure_local=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        with pytest.raises(RuntimeError, match="size limit"):
            await place_cpaas_call(settings, client=client)


@pytest.mark.asyncio
async def test_smoke_read_timeout_is_bounded_by_typed_setting():
    release = asyncio.Event()

    async def stall(_reader, writer):
        try:
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = CpaasTransportSettings(
        provider="telnyx",
        account_id="connection-redacted",
        api_key=_CPaaS_API_KEY,
        from_number="+34600000001",
        to_number="+34600000002",
        api_base_url=f"http://127.0.0.1:{port}",
        public_ws_url="wss://voice.example.test/cpaas/telnyx",
        stream_auth_token=_CPaaS_STREAM_TOKEN,
        allow_insecure_local=True,
        request_timeout_seconds=0.01,
    )
    try:
        with pytest.raises(httpx.ReadTimeout):
            await place_cpaas_call(settings)
    finally:
        release.set()
        server.close()
        await server.wait_closed()
