"""Per-turn span tree (ADR 0011) emitted through ``lucy.observe``.

A `TurnSpanTree` builds the ``lucy.session -> lucy.turn -> lucy.node.*``
hierarchy for one call. It is the local structural model; :meth:`emit` fans it
out to the open telemetry seam (``lucy.observe.Tracer``), which handles
sampling, redaction, and export.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Span:
    span_id: str
    parent_id: Optional[str]
    name: str
    started_at_ms: int
    ended_at_ms: int
    attributes: Dict[str, str] = field(default_factory=dict)
    status: str = "ok"


class TurnSpanTree:
    """Builds and emits the span hierarchy for a single call."""

    def __init__(
        self, session_id: str, *, id_factory: Optional[Callable[[], str]] = None
    ) -> None:
        self.session_id = session_id
        self._id = id_factory or (lambda: uuid.uuid4().hex)
        self.spans: List[Span] = []
        self.session_span: Optional[Span] = None

    def start_session(self, started_at_ms: int) -> Span:
        span = Span(self._id(), None, "lucy.session", started_at_ms, started_at_ms)
        self.session_span = span
        self.spans.append(span)
        return span

    def end_session(self, ended_at_ms: int) -> None:
        if self.session_span is not None:
            self.session_span.ended_at_ms = ended_at_ms

    def turn_span(
        self, turn_id: str, started_at_ms: int, ended_at_ms: int, *, status: str = "ok"
    ) -> Span:
        parent_id = self.session_span.span_id if self.session_span else None
        span = Span(
            self._id(),
            parent_id,
            "lucy.turn",
            started_at_ms,
            ended_at_ms,
            {"turn_id": turn_id},
            status,
        )
        self.spans.append(span)
        return span

    def node_span(
        self,
        turn: Span,
        node_name: str,
        started_at_ms: int,
        ended_at_ms: int,
        *,
        status: str = "ok",
        attributes: Optional[Dict[str, str]] = None,
    ) -> Span:
        span = Span(
            self._id(),
            turn.span_id,
            "lucy.node.%s" % node_name,
            started_at_ms,
            max(ended_at_ms, started_at_ms),
            dict(attributes or {}),
            status,
        )
        self.spans.append(span)
        return span

    def emit(self, tracer) -> None:
        """Fan the tree out through a ``lucy.observe`` tracer. No-op if none."""
        if tracer is None:
            return
        for span in self.spans:
            tracer.span(
                session_id=self.session_id,
                turn_id=span.attributes.get("turn_id", ""),
                span_id=span.span_id,
                name=span.name,
                status=span.status,
                started_at_ms=span.started_at_ms,
                ended_at_ms=span.ended_at_ms,
                parent_id=span.parent_id,
                attributes=span.attributes,
            )
