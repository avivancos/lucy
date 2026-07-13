import asyncio
from pathlib import Path

import pytest

from lucy.testing.replay import (
    FixtureMismatch,
    RecordedFrame,
    ReplayTransport,
    StalledTransport,
    load_fixture,
)

FIXTURE = Path(__file__).parent / "fixtures" / "replay_echo_session.jsonl"


async def test_replay_yields_received_frames_in_order():
    transport = ReplayTransport(load_fixture(FIXTURE))

    await transport.send({"type": "echo", "text": "hello", "request_id": "live-value"})

    assert await transport.receive() == {
        "type": "echo.result",
        "text": "hello",
        "request_id": "recorded-2",
    }
    assert await transport.receive() == {"type": "done"}


async def test_sent_frame_mismatch_raises_fixture_mismatch():
    transport = ReplayTransport(load_fixture(FIXTURE))

    with pytest.raises(FixtureMismatch) as excinfo:
        await transport.send({"type": "echo", "text": "wrong"})

    message = str(excinfo.value)
    assert str(FIXTURE) in message
    assert "frame 0" in message
    assert "hello" in message
    assert "wrong" in message


async def test_replay_ignores_credentials_removed_by_recorder():
    transport = ReplayTransport(
        [RecordedFrame(direction="sent", at_ms=0, payload={"text": "hello"})]
    )

    await transport.send({"text": "hello", "xi_api_key": "live-secret"})


async def test_replay_uses_shared_credential_key_policy():
    transport = ReplayTransport(
        [RecordedFrame(direction="sent", at_ms=0, payload={"text": "hello"})]
    )

    await transport.send(
        {
            "text": "hello",
            "Cookie": "session=live-secret",
            "private_key": "private-material",
            "credentials": "credential-material",
            "aws_access_key_id": "cloud-key",
        }
    )


async def test_replay_mismatch_never_prints_embedded_credentials():
    transport = ReplayTransport(
        [RecordedFrame(direction="sent", at_ms=0, payload={"text": "hello"})]
    )

    with pytest.raises(FixtureMismatch) as excinfo:
        await transport.send(
            {
                "text": "wrong",
                "error": "Cookie=session=diagnostic-cookie",
                "database": "postgresql://alice:url-password@db.local/app",
            }
        )

    message = str(excinfo.value)
    assert "diagnostic-cookie" not in message
    assert "url-password" not in message
    assert "[REDACTED]" in message


@pytest.mark.parametrize(
    "env_name",
    [
        "LUCY_API_KEY",
        "PROVIDER_AUTHORIZATION",
        "PROVIDER_AUTH",
        "PROVIDER_JWT",
        "REDIS_PASSWD",
        "STRIPE_SECRET_KEY",
        "SECRET_KEY_BASE",
        "SECRET_KEY",
    ],
)
async def test_replay_mismatch_scrubs_opaque_environment_secret(monkeypatch, env_name):
    secret = f"opaque-{env_name.lower()}-credential"
    monkeypatch.setenv(env_name, secret)
    transport = ReplayTransport(
        [RecordedFrame(direction="sent", at_ms=0, payload={"text": "hello"})]
    )

    with pytest.raises(FixtureMismatch) as excinfo:
        await transport.send(
            {"text": "wrong", "diagnostic": f"provider returned {secret}"}
        )

    message = str(excinfo.value)
    assert secret not in message
    assert "[REDACTED]" in message


async def test_stalled_transport_never_yields():
    transport = StalledTransport()

    await transport.send({"anything": True})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(transport.receive(), timeout=0.01)
