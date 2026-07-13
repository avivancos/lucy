"""TraceExporter adapter for Lucy cloud ingestion."""

from __future__ import annotations

import os
from typing import List, Optional

from lucy.observe import TelemetryEvent
from lucy.observe.redact import _is_export_approved

from lucy_cloud._wire import DEFAULT_PROJECT
from lucy_cloud.client import IngestClient

ENV_API_KEY = "LUCY_API_KEY"
ENV_ENDPOINT = "LUCY_ENDPOINT"
ENV_PROJECT = "LUCY_PROJECT"


class CloudTraceExporter:
    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        client: Optional[IngestClient] = None,
    ) -> None:
        if client is not None:
            self.client = client
            return
        resolved_endpoint = (
            endpoint if endpoint is not None else os.environ.get(ENV_ENDPOINT)
        )
        resolved_api_key = (
            api_key if api_key is not None else os.environ.get(ENV_API_KEY)
        )
        resolved_project = (
            project
            if project is not None
            else os.environ.get(ENV_PROJECT, DEFAULT_PROJECT)
        )
        if not resolved_endpoint or not resolved_api_key:
            raise ValueError("Lucy cloud endpoint and API key are required")
        self.client = IngestClient(
            resolved_endpoint,
            resolved_api_key,
            project=resolved_project,
        )

    @classmethod
    def from_env(cls) -> Optional["CloudTraceExporter"]:
        if not os.environ.get(ENV_API_KEY):
            return None
        return cls()

    def export_batch(self, events: List[TelemetryEvent]) -> None:
        for event in events:
            try:
                if not _is_export_approved(event):
                    self.client._record_drop(1)
                    continue
                wire_event = event.to_wire()
                if wire_event.get("type") == "cost":
                    cost = wire_event.pop("cost")
                    if isinstance(cost, dict):
                        wire_event.update(cost)
                self.client._submit_wire(wire_event)
            except Exception:
                self.client._record_drop(1)
        try:
            self.client.request_flush()
        except Exception:
            return

    async def aclose(self) -> None:
        await self.client.aclose()

    def close(self) -> None:
        self.client.close()
