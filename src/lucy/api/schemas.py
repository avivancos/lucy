"""Deprecated: framework schemas moved to lucy.serve.schemas (card 23).

This module re-exports the framework schemas (`LucyApiModel`, `HealthResponse`)
from `lucy.serve.schemas` and, transitionally, still defines the platform fleet
and Pili response models until they move to lucy-platform (card 21) and pili
(card 20). Importing from here emits DeprecationWarning.
"""

from __future__ import annotations

import warnings
from typing import List

from pydantic import Field

from lucy.metrics import LatencyWaterfall
from lucy.serve.schemas import HealthResponse, LucyApiModel
from lucy.specs import FunnelStage, SentimentLabel

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
    "McpCommandSummary",
    "PiliHealthResponse",
    "PiliVoiceEventRequest",
    "PiliVoiceEventResponse",
    "PiliBookingHoldRequest",
    "PiliBookingHoldResponse",
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


class PiliHealthResponse(LucyApiModel):
    service: str
    status: str
    lucy_compatible: bool


class PiliVoiceEventRequest(LucyApiModel):
    session_id: str
    lead_id: str
    funnel_stage: FunnelStage
    sentiment: SentimentLabel
    cost_per_minute: float = Field(ge=0)
    transcript_excerpt: str


class McpCommandSummary(LucyApiModel):
    command_id: str
    server: str
    tool: str
    status: str


class PiliVoiceEventResponse(LucyApiModel):
    event_id: str
    accepted: bool
    crm_sync_status: str
    mcp_commands: List[McpCommandSummary]
    mcp_audit_count: int


class PiliBookingHoldRequest(LucyApiModel):
    session_id: str
    lead_id: str
    requested_slot: str
    timezone: str
    source: str


class PiliBookingHoldResponse(LucyApiModel):
    booking_id: str
    status: str
    crm_sync_status: str
    mcp_commands: List[McpCommandSummary]
    mcp_audit_count: int


AgentSummaryList = List[AgentSummary]
DeploymentSummaryList = List[DeploymentSummary]
SessionSummaryList = List[SessionSummary]
TraceSummaryList = List[TraceSummary]
McpServerSummaryList = List[McpServerSummary]
