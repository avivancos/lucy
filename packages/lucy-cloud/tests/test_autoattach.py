from lucy.observe import configure

from lucy_cloud.exporter import CloudTraceExporter
from .ingest_app import create_ingest_app, run_local_ingest


API_KEY = "test-project-key"


def test_configure_skips_cloud_exporter_without_api_key(monkeypatch):
    monkeypatch.delenv("LUCY_API_KEY", raising=False)
    tracer = configure()
    assert not any(isinstance(item, CloudTraceExporter) for item in tracer._exporters)


async def test_configure_attaches_cloud_exporter_when_api_key_set(monkeypatch):
    app = create_ingest_app(API_KEY)
    with run_local_ingest(app) as endpoint:
        monkeypatch.setenv("LUCY_API_KEY", API_KEY)
        monkeypatch.setenv("LUCY_ENDPOINT", endpoint)
        monkeypatch.setenv("LUCY_PROJECT", "project-a")
        tracer = configure()
        clouds = [
            item for item in tracer._exporters if isinstance(item, CloudTraceExporter)
        ]
        assert len(clouds) == 1
        await clouds[0].aclose()


def test_session_events_reach_ingest_with_zero_code_changes(monkeypatch):
    app = create_ingest_app(API_KEY)
    with run_local_ingest(app) as endpoint:
        monkeypatch.setenv("LUCY_API_KEY", API_KEY)
        monkeypatch.setenv("LUCY_ENDPOINT", endpoint)
        monkeypatch.setenv("LUCY_PROJECT", "project-a")
        tracer = configure()
        tracer.session_started(
            session_id="session-1",
            agent_name="booking",
            spec_hash="spec-1",
            environment="test",
            transport="sim",
        )
        tracer.close()
        assert app.state.ingest.accepted.wait(1)
    assert [event["type"] for event in app.state.ingest.stored_events] == [
        "session.started"
    ]
    assert app.state.ingest.stored_batches[0]["project"] == "project-a"
