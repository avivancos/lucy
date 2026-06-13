"""Observability bridge for Lucy metrics and traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Protocol, Union

from lucy.metrics import CostBreakdown, LatencyWaterfall


TraceAttribute = Union[str, float]
OtelSpan = Dict[str, object]


@dataclass(frozen=True)
class ObservabilityEvent:
    session_id: str
    primary_metric: str
    cost: CostBreakdown
    latency: LatencyWaterfall

    def as_trace_attributes(self) -> Dict[str, TraceAttribute]:
        return {
            "session_id": self.session_id,
            "primary_metric": self.primary_metric,
            "cost_total": self.cost.total_cost,
            "cost_per_minute": self.cost.cost_per_minute,
            "latency_total_ms": self.latency.total_ms,
            "latency_stt_ms": self.latency.stt_ms,
            "latency_rag_ms": self.latency.rag_ms,
            "latency_llm_ms": self.latency.llm_ms,
            "latency_mcp_tools_ms": self.latency.mcp_tools_ms,
            "latency_tts_ms": self.latency.tts_ms,
            "latency_transport_ms": self.latency.transport_ms,
        }


class OtelSpanExporter(Protocol):
    def export(self, span: OtelSpan) -> None:
        ...


class InMemoryOtelSpanExporter:
    def __init__(self) -> None:
        self.spans: List[OtelSpan] = []

    def export(self, span: OtelSpan) -> None:
        self.spans.append(span)


@dataclass
class OtelExporterBridge:
    exporter: OtelSpanExporter
    service_name: str = "lucy-api"

    def export_event(self, event: ObservabilityEvent) -> OtelSpan:
        span: OtelSpan = {
            "name": "lucy.session",
            "kind": "internal",
            "resource": {"service.name": self.service_name},
            "attributes": event.as_trace_attributes(),
        }
        self.exporter.export(span)
        return span
