"""Pluggable trace exporters - the sinks telemetry batches are written to.

An exporter implements ``export_batch``. Exporters must not raise into caller
code; the tracer isolates failures and counts drops (wire spec: fail-open).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Protocol, TextIO, runtime_checkable

from lucy.observe.events import TelemetryEvent, TurnEvent
from lucy.observe.otel import OtelExporterBridge, OtelSpan, OtelSpanExporter


@runtime_checkable
class TraceExporter(Protocol):
    def export_batch(self, events: List[TelemetryEvent]) -> None: ...


class ConsoleExporter:
    """Human-readable console exporter with a per-turn latency waterfall."""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self._stream = stream

    def export_batch(self, events: List[TelemetryEvent]) -> None:
        stream = self._stream or sys.stdout
        for event in events:
            if isinstance(event, TurnEvent):
                w = event.latency_waterfall
                stream.write(
                    "lucy.turn %s idx=%d stt=%.0f rag=%.0f llm=%.0f mcp=%.0f "
                    "tts=%.0f transport=%.0f total=%.0fms%s\n"
                    % (
                        event.turn_id,
                        event.turn_index,
                        w.stt_ms,
                        w.rag_ms,
                        w.llm_ms,
                        w.mcp_tools_ms,
                        w.tts_ms,
                        w.transport_ms,
                        w.total_ms,
                        " [interrupted]" if event.interrupted else "",
                    )
                )
            else:
                stream.write("lucy.%s %s\n" % (event.type, event.event_id))


class JsonlFileExporter:
    """Append each event as one wire-shaped JSON line to ``path``.

    This is the file the local trace viewer (card 31) reads.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def export_batch(self, events: List[TelemetryEvent]) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_wire(), sort_keys=True))
                handle.write("\n")


class OtlpBridgeExporter:
    """Bridge telemetry events onto an OpenTelemetry span exporter.

    Wraps the legacy ``OtelExporterBridge`` span shape so existing OTel
    collectors keep receiving Lucy spans.
    """

    def __init__(
        self,
        span_exporter: OtelSpanExporter,
        service_name: str = "lucy-api",
    ) -> None:
        self._bridge = OtelExporterBridge(
            exporter=span_exporter, service_name=service_name
        )

    def export_batch(self, events: List[TelemetryEvent]) -> None:
        for event in events:
            span: OtelSpan = {
                "name": "lucy.%s" % event.type,
                "kind": "internal",
                "resource": {"service.name": self._bridge.service_name},
                "attributes": event.to_wire(),
            }
            self._bridge.exporter.export(span)
