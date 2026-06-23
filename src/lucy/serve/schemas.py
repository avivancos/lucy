"""Framework (non-fleet, non-Pili) response schemas for the serving runtime.

These are the open serving-runtime schemas (ADR 0010, card 23). The platform
fleet response models were extracted to lucy-platform (card 21) and the Pili
response models to the pili repo (card 20).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LucyApiModel(BaseModel):
    """Base schema for API responses exposed through FastAPI/OpenAPI."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(LucyApiModel):
    service: str
    status: str
    version: str
