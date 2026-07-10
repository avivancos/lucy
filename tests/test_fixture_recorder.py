from lucy.testing.record import write_fixture
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
