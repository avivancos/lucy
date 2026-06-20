"""Card 23: lucy.serve is the framework serving runtime (ADR 0010).

The serving app exposes only process-local routes (health, metrics, realtime
SSE, models, evals). Platform fleet routes and the Pili vertical must NOT be
mounted here - that separation is the open/closed boundary this card draws.
"""

import json
import warnings

from fastapi.testclient import TestClient

from lucy.serve import create_app
from lucy.serve.schemas import HealthResponse

FRAMEWORK_ROUTES = ["/health", "/metrics", "/metrics/realtime", "/models", "/evals"]
EXCLUDED_ROUTES = [
    "/agents",
    "/deployments",
    "/sessions",
    "/traces",
    "/mcp/servers",
    "/crm/events",
    "/pili/health",
    "/pili/voice/events",
    "/pili/bookings",
]


def test_importing_lucy_serve_emits_no_deprecation_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        import importlib

        import lucy.serve.app

        importlib.reload(lucy.serve.app)


def test_serve_app_exposes_only_framework_routes():
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]

    for route in FRAMEWORK_ROUTES:
        assert route in paths, route
    for route in EXCLUDED_ROUTES:
        assert route not in paths, route


def test_health_and_docs_served():
    client = TestClient(create_app())

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "lucy-api"
    assert health.json()["status"] == "ok"

    schema = client.get("/openapi.json")
    assert schema.json()["info"]["title"] == "Lucy API"
    assert client.get("/docs").status_code == 200


def test_models_and_evals_return_deterministic_payloads():
    client = TestClient(create_app())

    models = client.get("/models").json()
    evals = client.get("/evals").json()
    metrics = client.get("/metrics").json()

    assert models["summary"]["stt"] >= 1
    assert evals["scenarios"][0]["expected_outcome"] == "booked"
    assert metrics["primary_metric"] == "cost_per_minute"


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
    assert payload["cost"]["cost_per_minute"] > 0


def test_health_response_schema_lives_in_serve_schemas():
    health = HealthResponse(service="lucy-api", status="ok", version="0.1.0")
    assert health.model_dump()["service"] == "lucy-api"
