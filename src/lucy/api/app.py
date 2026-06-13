"""FastAPI control plane for Lucy."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from lucy import __version__
from lucy.api.schemas import (
    AgentSummary,
    AgentSummaryList,
    DeploymentSummary,
    DeploymentSummaryList,
    HealthResponse,
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
from lucy.evals import booking_happy_path
from lucy.metrics import (
    CostBreakdown,
    FunnelEvent,
    LatencyWaterfall,
    LocalMetricEventChannel,
    RealtimeMetricEvent,
    SentimentScore,
)
from lucy.mcp import LocalMcpCommandTransport, McpClient, McpPermissionError
from lucy.providers import default_model_registry, registry_summary
from lucy.specs import FunnelStage, SentimentLabel


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


def _demo_realtime_metric_event() -> RealtimeMetricEvent:
    sentiment = SentimentScore(
        label=SentimentLabel.POSITIVE,
        confidence=0.86,
        model="registry:sentiment-default",
    )
    funnel = FunnelEvent(
        session_id="sess_demo",
        stage=FunnelStage.BOOKED,
        confidence=0.91,
        crm_payload={"lead_id": "lead_demo"},
    )
    return RealtimeMetricEvent(
        event_id="metric_sess_demo_1",
        session_id="sess_demo",
        lead_id="lead_demo",
        sentiment=sentiment,
        funnel=funnel,
        cost=_demo_cost_breakdown(),
        emitted_at_ms=1200,
    )


def create_app(
    allowed_pili_tools: Optional[Sequence[str]] = None,
) -> FastAPI:
    app = FastAPI(
        title="Lucy API",
        version=__version__,
        description="Control plane for Lucy voice-agent infrastructure.",
    )
    pili_mcp = McpClient(
        LocalMcpCommandTransport(),
        allowed_tools=list(allowed_pili_tools or DEFAULT_PILI_MCP_TOOLS),
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service="lucy-api", status="ok", version=__version__)

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

    @app.get("/metrics", tags=["metrics"])
    async def metrics() -> Dict[str, object]:
        return {
            "primary_metric": "cost_per_minute",
            "latency_p95_ms": 620,
            "sentiment": SentimentScore(
                label=SentimentLabel.POSITIVE,
                confidence=0.86,
                model="registry:sentiment-default",
            ).model_dump(),
            "funnel": FunnelEvent(
                session_id="sess_demo",
                stage=FunnelStage.BOOKED,
                confidence=0.91,
                crm_payload={"lead_id": "lead_demo"},
            ).model_dump(),
        }

    @app.get("/metrics/realtime", tags=["metrics"])
    async def realtime_metrics() -> StreamingResponse:
        channel = LocalMetricEventChannel(events=[_demo_realtime_metric_event()])
        return StreamingResponse(channel.sse(), media_type="text/event-stream")

    @app.get("/models", tags=["models"])
    async def models() -> Dict[str, object]:
        registry = default_model_registry()
        return {
            "version": registry.version,
            "summary": registry_summary(registry),
            "models": [model.model_dump() for model in registry.models],
        }

    @app.get("/evals", tags=["evals"])
    async def evals() -> Dict[str, object]:
        return {"scenarios": [booking_happy_path().model_dump()]}

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
