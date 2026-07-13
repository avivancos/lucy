"""Named literals and pure helpers for telemetry wire v1."""

from __future__ import annotations

import gzip
import json
from typing import Dict, List, Tuple

WIRE_VERSION = 1
EVENTS_PATH = "/v1/events"
MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 1_048_576
FLUSH_INTERVAL_S = 2.0
DEFAULT_MAX_QUEUE = 10_000
DEFAULT_MAX_RETRIES = 5
RETRY_BASE_S = 0.01
RETRY_MAX_S = 0.1
RETRY_AFTER_MAX_S = 30.0
CLOSE_TIMEOUT_S = 5.0
WORKER_START_TIMEOUT_S = 2.0
DEFAULT_PROJECT = "default"

HEADER_API_KEY = "x-api-key"
HEADER_WIRE = "x-lucy-wire"
HEADER_IDEMPOTENCY = "idempotency-key"
HEADER_RETRY_AFTER = "retry-after"
HEADER_CONTENT_TYPE = "content-type"
HEADER_CONTENT_ENCODING = "content-encoding"
CONTENT_TYPE_JSON = "application/json"
CONTENT_ENCODING_GZIP = "gzip"
SDK_NAME = "lucy"
LOGGER_NAME = "lucy_cloud"
ACCEPTED_STATUS = 202
RATE_LIMITED_STATUS = 429


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def build_envelope(project: str, sdk_version: str, events: List[dict]) -> dict:
    return {
        "project": project,
        "sdk": {"name": SDK_NAME, "version": sdk_version},
        "events": events,
    }


def plan_batches(
    events: List[dict], *, project: str = "", sdk_version: str = ""
) -> List[List[dict]]:
    batches: List[List[dict]] = []
    current: List[dict] = []
    for event in events:
        if (
            len(json_bytes(build_envelope(project, sdk_version, [event])))
            > MAX_BATCH_BYTES
        ):
            raise ValueError("event exceeds telemetry batch byte limit")
        candidate = [*current, event]
        if current and (
            len(candidate) > MAX_BATCH_EVENTS
            or len(json_bytes(build_envelope(project, sdk_version, candidate)))
            > MAX_BATCH_BYTES
        ):
            batches.append(current)
            current = [event]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def encode_batch(
    envelope: dict, *, use_gzip: bool = True
) -> Tuple[bytes, Dict[str, str]]:
    body = json_bytes(envelope)
    if len(body) > MAX_BATCH_BYTES:
        raise ValueError("telemetry envelope exceeds batch byte limit")
    headers = {
        HEADER_CONTENT_TYPE: CONTENT_TYPE_JSON,
        HEADER_WIRE: str(WIRE_VERSION),
    }
    if use_gzip:
        body = gzip.compress(body, mtime=0)
        headers[HEADER_CONTENT_ENCODING] = CONTENT_ENCODING_GZIP
    return body, headers
