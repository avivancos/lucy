from lucy.testing.record import _configured_secrets, write_fixture
from lucy.testing.replay import RecordedFrame, load_fixture


def _frames(secret: str):
    return [
        RecordedFrame(
            direction="sent",
            at_ms=0,
            payload={
                "headers": {
                    "Authorization": "Bearer %s" % secret,
                    "xi-api-key": secret,
                    "xi_api_key": secret,
                    "content-type": "application/json",
                },
                "url": "wss://provider.test/session?api_key=%s&model=fast" % secret,
                "secret_copy": secret,
                "embedded_secret": "prefix-%s-suffix" % secret,
                "header-%s" % secret: "safe",
                "request_id": "request-live",
                "session": {"id": "session-live", "mode": "voice"},
            },
        ),
        RecordedFrame(
            direction="received",
            at_ms=10,
            payload={"event_id": "event-live", "status": "ok"},
        ),
    ]


def test_recorder_scrubs_credentials_from_frames(tmp_path):
    secret = "top-secret"
    path = tmp_path / "recording.jsonl"

    write_fixture(path, _frames(secret), secrets=[secret])

    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "Authorization" not in raw
    assert "xi-api-key" not in raw
    assert "xi_api_key" not in raw
    assert "api_key" not in raw
    assert "model=fast" in raw
    assert "<scrubbed>" in raw


def test_recorder_uses_shared_credential_key_policy(tmp_path):
    path = tmp_path / "recording.jsonl"
    frame = RecordedFrame(
        direction="sent",
        at_ms=0,
        payload={
            "headers": {
                "Cookie": "session=secret-cookie",
                "private_key": "private-material",
                "credentials": "credential-material",
            },
            "url": "wss://provider.test/session?aws_access_key_id=cloud-key&model=fast",
            "diagnostic": "Cookie=session=diagnostic-cookie",
            "database": "postgresql://alice:url-password@db.local/app",
        },
    )

    write_fixture(path, [frame])

    raw = path.read_text(encoding="utf-8")
    for sensitive in (
        "secret-cookie",
        "private_key",
        "private-material",
        "credentials",
        "credential-material",
        "aws_access_key_id",
        "cloud-key",
        "diagnostic-cookie",
        "url-password",
    ):
        assert sensitive not in raw
    assert "model=fast" in raw


def test_recorder_scrubs_percent_encoded_embedded_configured_secret(tmp_path):
    secret = "p@ss/word"
    path = tmp_path / "recording.jsonl"
    frame = RecordedFrame(
        direction="sent",
        at_ms=0,
        payload={
            "url": (
                "wss://provider.test/session?"
                "note=prefix-p%40ss%2Fword-suffix&model=fast"
            )
        },
    )

    write_fixture(path, [frame], secrets=[secret])

    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "p%40ss%2Fword" not in raw
    assert "model=fast" in raw


def test_environment_secret_collection_uses_shared_key_policy(monkeypatch):
    expected = {
        "REDIS_PASSWORD": "redis-opaque-value",
        "SESSION_COOKIE": "cookie-opaque-value",
        "STRIPE_SECRET_KEY": "stripe-opaque-value",
        "SECRET_KEY_BASE": "base-opaque-value",
        "SECRET_KEY": "secret-key-opaque-value",
        "PROVIDER_AUTHORIZATION": "authorization-opaque-value",
        "PROVIDER_AUTH": "auth-opaque-value",
        "PROVIDER_JWT": "jwt-opaque-value",
        "REDIS_PASSWD": "passwd-opaque-value",
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SAFE_SETTING", "public-value")

    secrets = _configured_secrets()

    assert set(expected.values()) <= set(secrets)
    assert "public-value" not in secrets


def test_environment_alias_secrets_are_removed_from_recorded_payload(
    monkeypatch, tmp_path
):
    aliases = {
        "PROVIDER_AUTHORIZATION": "authorization-env-secret",
        "PROVIDER_AUTH": "auth-env-secret",
        "PROVIDER_JWT": "jwt-env-secret",
        "REDIS_PASSWD": "passwd-env-secret",
        "STRIPE_SECRET_KEY": "stripe-env-secret",
        "SECRET_KEY_BASE": "base-env-secret",
        "SECRET_KEY": "secret-key-env-secret",
    }
    for name, value in aliases.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "recording.jsonl"
    frame = RecordedFrame(
        direction="sent",
        at_ms=0,
        payload={
            "diagnostic": " ".join(aliases.values()),
        },
    )

    write_fixture(path, [frame], secrets=_configured_secrets())

    raw = path.read_text(encoding="utf-8")
    assert not any(secret in raw for secret in aliases.values())


def test_recorder_masks_volatile_fields(tmp_path):
    path = tmp_path / "recording.jsonl"

    write_fixture(path, _frames("secret"), secrets=["secret"])
    frames = load_fixture(path)

    assert frames[0].payload["request_id"] == "<masked>"
    assert frames[0].payload["session"]["id"] == "<masked>"
    assert frames[1].payload["event_id"] == "<masked>"


def test_recorder_writes_loadable_jsonl(tmp_path):
    path = tmp_path / "recording.jsonl"

    write_fixture(path, _frames("secret"), secrets=["secret"])
    loaded = load_fixture(path)

    assert [frame.direction for frame in loaded] == ["sent", "received"]
    assert [frame.at_ms for frame in loaded] == [0, 10]
    assert loaded[1].payload["status"] == "ok"
