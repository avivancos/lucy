import pytest
from pydantic import ValidationError

from lucy.metrics import LatencyWaterfall
from lucy.api.schemas import (
    AgentSummary,
    DeploymentSummary,
    HealthResponse,
    LucyApiModel,
    McpServerSummary,
    SessionSummary,
    TraceSummary,
)
from lucy.api.app import create_app


def test_lucy_api_model_forbids_unexpected_fields():
    class ExampleSchema(LucyApiModel):
        id: str

    with pytest.raises(ValidationError):
        ExampleSchema(id="ok", unexpected="nope")


def test_base_api_schemas_serialize_contract_payloads():
    health = HealthResponse(service="lucy-api", status="ok", version="0.1.0")
    agent = AgentSummary(
        id="agent_sales_booking",
        name="Sales Booking Agent",
        goal="Qualify leads and book appointments.",
    )
    deployment = DeploymentSummary(
        id="dep_local",
        agent_id=agent.id,
        environment="local",
        status="healthy",
    )
    session = SessionSummary(
        id="sess_demo",
        agent_id=agent.id,
        status="live",
        cost_per_minute=0.031667,
    )
    trace = TraceSummary(
        session_id=session.id,
        waterfall=LatencyWaterfall(stt_ms=145),
    )
    mcp_server = McpServerSummary(
        name="crm",
        status="configured",
        allowed_tools=["crm.create_lead", "crm.book_meeting"],
    )

    assert health.model_dump()["service"] == "lucy-api"
    assert deployment.model_dump()["agent_id"] == "agent_sales_booking"
    assert session.model_dump()["cost_per_minute"] == 0.031667
    assert trace.model_dump()["waterfall"]["stt_ms"] == 145
    assert mcp_server.model_dump()["allowed_tools"] == [
        "crm.create_lead",
        "crm.book_meeting",
    ]


def test_api_openapi_uses_named_schema_components():
    schema = create_app().openapi()
    schemas = schema["components"]["schemas"]

    for component_name in [
        "HealthResponse",
        "AgentSummary",
        "DeploymentSummary",
        "SessionSummary",
        "TraceSummary",
        "McpServerSummary",
    ]:
        assert component_name in schemas
