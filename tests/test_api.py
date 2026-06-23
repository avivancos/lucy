"""lucy.api.app is the DEPRECATED transitional app (card 23). After the fleet
routes moved to lucy-platform (card 21) and the Pili vertical to the pili repo
(card 20), it now just re-exposes the open framework serving app under the
legacy import path. Framework-route coverage lives in test_serve_app.py; this
file guards the deprecation warning and that the shim serves ONLY process-local
routes (no fleet, no Pili).
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from lucy.api.app import create_app

FRAMEWORK_ROUTES = ["/health", "/metrics", "/metrics/realtime", "/models", "/evals"]
EXTRACTED_ROUTES = [
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


def test_shim_serves_only_process_local_routes():
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    for route in FRAMEWORK_ROUTES:
        assert route in paths, route
    for route in EXTRACTED_ROUTES:
        assert route not in paths, route
