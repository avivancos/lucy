"""Optional real CPaaS PSTN smoke with secret-safe output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import sys
from typing import Optional
from urllib.parse import quote, urlencode, urlsplit
from xml.sax.saxutils import escape

import httpx

from lucy.settings import CpaasTransportSettings
from lucy.specs import CpaasTransportMode


MAX_SMOKE_RESPONSE_BYTES = 65_536
TELNYX_DIAL_PATH = "/v2/calls"
TWILIO_API_VERSION = "2010-04-01"
TELNYX_STREAM_CODEC = "L16"
TELNYX_STREAM_SAMPLE_RATE_HZ = 16_000


@dataclass(frozen=True)
class CpaasSmokeResult:
    provider: str
    accepted: bool


async def place_cpaas_call(
    settings: CpaasTransportSettings,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> CpaasSmokeResult:
    missing = settings.missing_credentials + settings.missing_stream_configuration
    if missing:
        raise ValueError("missing CPaaS configuration: " + ", ".join(missing))
    assert settings.provider is not None
    assert settings.account_id is not None
    assert settings.api_key is not None
    assert settings.from_number is not None
    assert settings.to_number is not None
    assert settings.api_base_url is not None
    assert settings.public_ws_url is not None
    _validate_endpoints(settings)

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=settings.request_timeout_seconds,
        )
    try:
        if settings.provider is CpaasTransportMode.TELNYX:
            assert settings.stream_auth_token is not None
            response = await client.post(
                settings.api_base_url + TELNYX_DIAL_PATH,
                headers={
                    "Authorization": ("Bearer " + settings.api_key.get_secret_value())
                },
                json={
                    "connection_id": settings.account_id.get_secret_value(),
                    "to": settings.to_number.get_secret_value(),
                    "from": settings.from_number.get_secret_value(),
                    "stream_url": settings.public_ws_url,
                    "stream_track": "inbound_track",
                    "stream_bidirectional_mode": "rtp",
                    "stream_bidirectional_codec": TELNYX_STREAM_CODEC,
                    "stream_bidirectional_sampling_rate": (
                        TELNYX_STREAM_SAMPLE_RATE_HZ
                    ),
                    "stream_auth_token": (
                        settings.stream_auth_token.get_secret_value()
                    ),
                },
            )
        else:
            account_id = settings.account_id.get_secret_value()
            call_path = (
                f"/{TWILIO_API_VERSION}/Accounts/{quote(account_id, safe='')}"
                "/Calls.json"
            )
            twiml = (
                '<Response><Connect><Stream url="'
                + escape(settings.public_ws_url, {'"': "&quot;"})
                + '"/></Connect></Response>'
            )
            response = await client.post(
                settings.api_base_url + call_path,
                auth=(account_id, settings.api_key.get_secret_value()),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urlencode(
                    {
                        "To": settings.to_number.get_secret_value(),
                        "From": settings.from_number.get_secret_value(),
                        "Twiml": twiml,
                    }
                ),
            )
        if len(response.content) > MAX_SMOKE_RESPONSE_BYTES:
            raise RuntimeError("CPaaS smoke response exceeded the size limit")
        if not response.is_success:
            raise RuntimeError(f"CPaaS smoke returned HTTP {response.status_code}")
        return CpaasSmokeResult(provider=settings.provider.value, accepted=True)
    finally:
        if owns_client:
            await client.aclose()


def _validate_endpoints(settings: CpaasTransportSettings) -> None:
    assert settings.api_base_url is not None
    assert settings.public_ws_url is not None
    api = urlsplit(settings.api_base_url)
    stream = urlsplit(settings.public_ws_url)
    api_is_loopback = _is_loopback(api.hostname)
    stream_is_loopback = _is_loopback(stream.hostname)
    if api.scheme != "https" and not (
        settings.allow_insecure_local and api.scheme == "http" and api_is_loopback
    ):
        raise ValueError("the CPaaS API endpoint must use HTTPS")
    if stream.scheme != "wss" and not (
        settings.allow_insecure_local and stream.scheme == "ws" and stream_is_loopback
    ):
        raise ValueError("the CPaaS media endpoint must use WSS")


def _is_loopback(hostname: Optional[str]) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


async def _run() -> int:
    settings = CpaasTransportSettings()
    missing = settings.missing_credentials + settings.missing_stream_configuration
    if missing:
        print("CPaaS live smoke skipped; missing: " + ", ".join(missing))
        return 0
    try:
        result = await place_cpaas_call(settings)
    except (ValueError, RuntimeError, httpx.HTTPError) as error:
        print(f"CPaaS live smoke failed: {error}", file=sys.stderr)
        return 1
    print(f"CPaaS live smoke accepted by {result.provider}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
