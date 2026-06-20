"""In-memory observability exporters for tests (ADR 0003)."""

from __future__ import annotations

from typing import List

from lucy.observe import OtelSpan, TelemetryEvent


class InMemoryOtelSpanExporter:
    def __init__(self) -> None:
        self.spans: List[OtelSpan] = []

    def export(self, span: OtelSpan) -> None:
        self.spans.append(span)


class InMemoryTraceExporter:
    """Collects exported telemetry batches so tests can assert on them."""

    def __init__(self) -> None:
        self.batches: List[List[TelemetryEvent]] = []
        self.events: List[TelemetryEvent] = []

    def export_batch(self, events: List[TelemetryEvent]) -> None:
        batch = list(events)
        self.batches.append(batch)
        self.events.extend(batch)
