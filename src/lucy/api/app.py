"""Deprecated: the framework app moved to lucy.serve.app (card 23).

Transitional shim: `create_app` builds on `lucy.serve.app.create_app` and ALSO
mounts the platform fleet routes until they are extracted to lucy-platform
(card 21). The Pili vertical was extracted to its own repo (card 20), so this
module serves no `/pili/*` route. Importing this module emits DeprecationWarning.
`uvicorn lucy.api.app:app` keeps working until the compose/Dockerfile target
flips to `lucy.serve.app:create_app` in card 21.
"""

from __future__ import annotations

import warnings
from typing import Dict

from fastapi import FastAPI

from lucy.api.schemas import (
    AgentSummary,
    AgentSummaryList,
    DeploymentSummary,
    DeploymentSummaryList,
    McpServerSummary,
    McpServerSummaryList,
    SessionSummary,
    SessionSummaryList,
    TraceSummary,
    TraceSummaryList,
)
from lucy.metrics import CostBreakdown, FunnelEvent, LatencyWaterfall
from lucy.serve.app import create_app as _create_framework_app
from lucy.specs import FunnelStage

warnings.warn(
    "lucy.api.app is deprecated; use lucy.serve.app:create_app",
    DeprecationWarning,
    stacklevel=2,
)


def _demo_cost_breakdown() -> CostBreakdown:
    return CostBreakdown(
        stt_cost=0.01,
        llm_cost=0.04,
        tts_cost=0.02,
        telephony_cost=0.015,
        rag_cost=0.003,
        mcp_tool_cost=0.002,
        infra_cost=0.005,
        billable_audio_minutes=3.0,
    )


def create_app() -> FastAPI:
    # Framework routes (health, metrics, realtime SSE, models, evals) come from
    # the open serving runtime; this shim adds the platform fleet routes on top
    # until they move to lucy-platform (card 21).
    app = _create_framework_app()

    @app.get("/agents", response_model=AgentSummaryList, tags=["agents"])
    async def agents() -> AgentSummaryList:
        return [
            AgentSummary(
                id="agent_sales_booking",
                name="Sales Booking Agent",
                goal="Qualify leads and book appointments.",
            )
        ]

    @app.get(
        "/deployments",
        response_model=DeploymentSummaryList,
        tags=["deployments"],
    )
    async def deployments() -> DeploymentSummaryList:
        return [
            DeploymentSummary(
                id="dep_local",
                agent_id="agent_sales_booking",
                environment="local",
                status="healthy",
            )
        ]

    @app.get("/sessions", response_model=SessionSummaryList, tags=["sessions"])
    async def sessions() -> SessionSummaryList:
        costs = _demo_cost_breakdown()
        return [
            SessionSummary(
                id="sess_demo",
                agent_id="agent_sales_booking",
                status="live",
                cost_per_minute=round(costs.cost_per_minute, 6),
            )
        ]

    @app.get("/traces", response_model=TraceSummaryList, tags=["traces"])
    async def traces() -> TraceSummaryList:
        return [
            TraceSummary(
                session_id="sess_demo",
                waterfall=LatencyWaterfall(
                    stt_ms=145,
                    rag_ms=38,
                    llm_ms=210,
                    mcp_tools_ms=42,
                    tts_ms=95,
                    transport_ms=32,
                ),
            )
        ]

    @app.get("/mcp/servers", response_model=McpServerSummaryList, tags=["mcp"])
    async def mcp_servers() -> McpServerSummaryList:
        return [
            McpServerSummary(
                name="crm",
                status="configured",
                allowed_tools=["crm.upsert_lead"],
            ),
            McpServerSummary(
                name="calendar",
                status="configured",
                allowed_tools=["calendar.hold_slot"],
            )
        ]

    @app.get("/crm/events", tags=["crm"])
    async def crm_events() -> Dict[str, object]:
        return {
            "events": [
                FunnelEvent(
                    session_id="sess_demo",
                    stage=FunnelStage.BOOKED,
                    confidence=0.91,
                    crm_payload={"lead_id": "lead_demo", "next_step": "demo"},
                ).model_dump()
            ]
        }

    return app


app = create_app()
