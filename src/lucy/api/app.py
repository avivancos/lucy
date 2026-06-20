"""Deprecated: the framework app moved to lucy.serve.app (card 23).

Transitional shim: `create_app` builds on `lucy.serve.app.create_app` and ALSO
mounts the platform fleet routes and the Pili vertical routes until they are
extracted to lucy-platform (card 21) and pili (card 20). Importing this module
emits DeprecationWarning. `uvicorn lucy.api.app:app` keeps working until the
compose/Dockerfile target flips to `lucy.serve.app:create_app` in card 21.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Sequence

from fastapi import FastAPI, HTTPException

from lucy.api.schemas import (
    AgentSummary,
    AgentSummaryList,
    DeploymentSummary,
    DeploymentSummaryList,
    McpCommandSummary,
    McpServerSummary,
    McpServerSummaryList,
    PiliBookingHoldRequest,
    PiliBookingHoldResponse,
    PiliHealthResponse,
    PiliVoiceEventRequest,
    PiliVoiceEventResponse,
    SessionSummary,
    SessionSummaryList,
    TraceSummary,
    TraceSummaryList,
)
from lucy.mcp import McpClient, McpPermissionError
from lucy.metrics import CostBreakdown, FunnelEvent, LatencyWaterfall
from lucy.serve.app import create_app as _create_framework_app
from lucy.specs import FunnelStage
from lucy.testing import LocalMcpCommandTransport

warnings.warn(
    "lucy.api.app is deprecated; use lucy.serve.app:create_app",
    DeprecationWarning,
    stacklevel=2,
)


def _pili_booking_id(lead_id: str, requested_slot: str) -> str:
    normalized_slot = "".join(
        character.lower()
        for character in requested_slot
        if character.isalnum()
    )
    return "pili_booking_%s_%s" % (lead_id, normalized_slot)


DEFAULT_PILI_MCP_TOOLS = ["crm.upsert_lead", "calendar.hold_slot"]


def _mcp_command_summary(command: Dict[str, object]) -> McpCommandSummary:
    return McpCommandSummary(
        command_id=str(command["command_id"]),
        server=str(command["server"]),
        tool=str(command["tool"]),
        status=str(command["status"]),
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


def create_app(
    allowed_pili_tools: Optional[Sequence[str]] = None,
) -> FastAPI:
    # Framework routes (health, metrics, realtime SSE, models, evals) come from
    # the open serving runtime; this shim adds the fleet and Pili routes on top.
    app = _create_framework_app()
    pili_mcp = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=list(allowed_pili_tools or DEFAULT_PILI_MCP_TOOLS),
    )

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

    @app.get("/pili/health", response_model=PiliHealthResponse, tags=["pili"])
    async def pili_health() -> PiliHealthResponse:
        return PiliHealthResponse(
            service="pili-api",
            status="ok",
            lucy_compatible=True,
        )

    @app.post(
        "/pili/voice/events",
        response_model=PiliVoiceEventResponse,
        tags=["pili"],
    )
    async def pili_voice_events(
        event: PiliVoiceEventRequest,
    ) -> PiliVoiceEventResponse:
        try:
            command = await pili_mcp.call_tool(
                "crm",
                "upsert_lead",
                {
                    "session_id": event.session_id,
                    "lead_id": event.lead_id,
                    "funnel_stage": event.funnel_stage.value,
                    "sentiment": event.sentiment.value,
                    "cost_per_minute": event.cost_per_minute,
                    "transcript_excerpt": event.transcript_excerpt,
                },
            )
        except McpPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        return PiliVoiceEventResponse(
            event_id="pili_evt_%s_%s" % (event.session_id, event.funnel_stage.value),
            accepted=True,
            crm_sync_status="queued",
            mcp_commands=[_mcp_command_summary(command)],
            mcp_audit_count=len(pili_mcp.audit_log),
        )

    @app.post(
        "/pili/bookings",
        response_model=PiliBookingHoldResponse,
        tags=["pili"],
    )
    async def pili_bookings(
        booking: PiliBookingHoldRequest,
    ) -> PiliBookingHoldResponse:
        commands: List[Dict[str, object]] = []
        try:
            commands.append(
                await pili_mcp.call_tool(
                    "crm",
                    "upsert_lead",
                    {
                        "session_id": booking.session_id,
                        "lead_id": booking.lead_id,
                        "source": booking.source,
                        "booking_status": "held",
                    },
                )
            )
            commands.append(
                await pili_mcp.call_tool(
                    "calendar",
                    "hold_slot",
                    {
                        "lead_id": booking.lead_id,
                        "requested_slot": booking.requested_slot,
                        "timezone": booking.timezone,
                        "source": booking.source,
                    },
                )
            )
        except McpPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        return PiliBookingHoldResponse(
            booking_id=_pili_booking_id(booking.lead_id, booking.requested_slot),
            status="held",
            crm_sync_status="queued",
            mcp_commands=[_mcp_command_summary(command) for command in commands],
            mcp_audit_count=len(pili_mcp.audit_log),
        )

    return app


app = create_app()
