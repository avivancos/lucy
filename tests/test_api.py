"""lucy.api.app is the DEPRECATED transitional app (card 23): it builds on
lucy.serve.app and still mounts the fleet + Pili routes until cards 21/20
extract them. Framework-route coverage lives in test_serve_app.py; this file
guards the full transitional surface and the deprecation warning.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from lucy.api.app import create_app


def test_importing_lucy_api_app_emits_deprecation_warning():
    import lucy.api.app

    with pytest.warns(DeprecationWarning):
        importlib.reload(lucy.api.app)


def test_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "lucy-api"
    assert response.json()["status"] == "ok"


def test_fastapi_app_metadata_and_developer_docs():
    client = TestClient(create_app())

    schema = client.get("/openapi.json")
    docs = client.get("/docs")
    redoc = client.get("/redoc")

    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Lucy API"
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text
    assert redoc.status_code == 200
    assert "ReDoc" in redoc.text


def test_openapi_contains_public_routes():
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    for route in [
        "/health",
        "/agents",
        "/deployments",
        "/sessions",
        "/traces",
        "/metrics",
        "/metrics/realtime",
        "/models",
        "/evals",
        "/mcp/servers",
        "/crm/events",
    ]:
        assert route in paths


def test_control_plane_routes_return_deterministic_contract_payloads():
    client = TestClient(create_app())

    agents = client.get("/agents").json()
    deployments = client.get("/deployments").json()
    sessions = client.get("/sessions").json()
    traces = client.get("/traces").json()
    metrics = client.get("/metrics").json()
    models = client.get("/models").json()
    evals = client.get("/evals").json()
    mcp_servers = client.get("/mcp/servers").json()
    crm_events = client.get("/crm/events").json()

    assert agents[0]["id"] == "agent_sales_booking"
    assert deployments[0]["environment"] == "local"
    assert sessions[0]["status"] == "live"
    assert sessions[0]["cost_per_minute"] > 0
    assert traces[0]["waterfall"]["stt_ms"] == 145
    assert metrics["primary_metric"] == "cost_per_minute"
    assert models["summary"]["stt"] >= 1
    assert evals["scenarios"][0]["expected_outcome"] == "booked"
    assert mcp_servers[0]["allowed_tools"] == ["crm.upsert_lead"]
    assert mcp_servers[1]["allowed_tools"] == ["calendar.hold_slot"]
    assert crm_events["events"][0]["stage"] == "booked"


def test_realtime_metrics_sse_route_streams_typed_metric_event():
    client = TestClient(create_app())

    response = client.get("/metrics/realtime")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: metric" in response.text

    data_line = [
        line for line in response.text.splitlines() if line.startswith("data: ")
    ][0]
    payload = json.loads(data_line.removeprefix("data: "))

    assert payload["session_id"] == "sess_demo"
    assert payload["sentiment"]["label"] == "positive"
    assert payload["funnel"]["stage"] == "booked"
    assert payload["crm"]["lead_id"] == "lead_demo"
    assert payload["cost"]["cost_per_minute"] > 0
