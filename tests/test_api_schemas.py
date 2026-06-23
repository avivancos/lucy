"""lucy.api.schemas is the DEPRECATED shim. The fleet response models moved to
lucy-platform (card 21) and the Pili models to the pili repo (card 20); only the
framework base schemas are re-exported here now.
"""

import pytest
from pydantic import ValidationError

from lucy.api.schemas import HealthResponse, LucyApiModel
from lucy.api.app import create_app


def test_lucy_api_model_forbids_unexpected_fields():
    class ExampleSchema(LucyApiModel):
        id: str

    with pytest.raises(ValidationError):
        ExampleSchema(id="ok", unexpected="nope")


def test_health_response_serializes_contract_payload():
    health = HealthResponse(service="lucy-api", status="ok", version="0.1.0")
    assert health.model_dump() == {"service": "lucy-api", "status": "ok", "version": "0.1.0"}


def test_api_openapi_uses_named_framework_schema_component():
    schemas = create_app().openapi()["components"]["schemas"]
    assert "HealthResponse" in schemas
