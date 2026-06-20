"""Local SSE metric event channel fixture (ADR 0003)."""

from __future__ import annotations

import json
from typing import AsyncIterator, List

from lucy.metrics import RealtimeMetricEvent


class LocalMetricEventChannel:
    def __init__(self, events: List[RealtimeMetricEvent]):
        self.events = events

    async def sse(self) -> AsyncIterator[str]:
        for event in self.events:
            yield "event: metric\n"
            yield "data: %s\n\n" % json.dumps(
                event.dashboard_payload(),
                sort_keys=True,
            )
