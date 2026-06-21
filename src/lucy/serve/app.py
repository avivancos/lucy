"""Framework serving runtime: process-local routes only (ADR 0010).

This app holds the routes that describe the LOCAL process - health, metrics,
realtime SSE, models, evals - so "run your agent" never requires the platform.
Platform fleet routes (agents, deployments, sessions, traces, mcp servers, crm
events) are NOT mounted here; they live in lucy-platform and, during the
transition, in the deprecated lucy.api.app shim. The Pili vertical routes were
extracted to the pili repo (card 20).
"""

from __future__ import annotations

from typing import Dict

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from lucy import __version__
from lucy.evals import booking_happy_path
from lucy.metrics import (
    CostBreakdown,
    FunnelEvent,
    RealtimeMetricEvent,
    SentimentScore,
)
from lucy.providers import default_model_registry, registry_summary
from lucy.serve.schemas import HealthResponse
from lucy.specs import FunnelStage, SentimentLabel
from lucy.testing import LocalMetricEventChannel


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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lucy API",
        version=__version__,
        description="Control plane for Lucy voice-agent infrastructure.",
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service="lucy-api", status="ok", version=__version__)

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

    return app
