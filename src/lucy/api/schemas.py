"""Deprecated: framework schemas moved to lucy.serve.schemas (card 23).

This module re-exports the framework schemas (`LucyApiModel`, `HealthResponse`)
from `lucy.serve.schemas` and, transitionally, still defines the platform fleet
response models until they move to lucy-platform (card 21). The Pili response
models were extracted to the pili repo (card 20). Importing from here emits
DeprecationWarning.
"""

from __future__ import annotations

import warnings
from typing import List

from lucy.metrics import LatencyWaterfall
from lucy.serve.schemas import HealthResponse, LucyApiModel

warnings.warn(
    "lucy.api.schemas is deprecated; framework schemas live in lucy.serve.schemas",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "LucyApiModel",
    "HealthResponse",
    "AgentSummary",
    "DeploymentSummary",
    "SessionSummary",
    "TraceSummary",
    "McpServerSummary",
    "AgentSummaryList",
    "DeploymentSummaryList",
    "SessionSummaryList",
    "TraceSummaryList",
    "McpServerSummaryList",
]


class AgentSummary(LucyApiModel):
    id: str
    name: str
    goal: str


class DeploymentSummary(LucyApiModel):
    id: str
    agent_id: str
    environment: str
    status: str


class SessionSummary(LucyApiModel):
    id: str
    agent_id: str
    status: str
    cost_per_minute: float


class TraceSummary(LucyApiModel):
    session_id: str
    waterfall: LatencyWaterfall


class McpServerSummary(LucyApiModel):
    name: str
    status: str
    allowed_tools: List[str]


AgentSummaryList = List[AgentSummary]
DeploymentSummaryList = List[DeploymentSummary]
SessionSummaryList = List[SessionSummary]
TraceSummaryList = List[TraceSummary]
McpServerSummaryList = List[McpServerSummary]
