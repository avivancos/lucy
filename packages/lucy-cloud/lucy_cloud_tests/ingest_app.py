from __future__ import annotations

import gzip
import json
import math
import re
import socket
import threading
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Set

import uvicorn
from fastapi import FastAPI, Request, Response

from lucy_cloud._wire import (
    CONTENT_TYPE_JSON,
    HEADER_API_KEY,
    HEADER_CONTENT_TYPE,
    HEADER_IDEMPOTENCY,
    HEADER_RETRY_AFTER,
    HEADER_WIRE,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    WIRE_VERSION,
)

_PROJECT_SENSITIVE_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<![A-Za-z0-9])\+?\d[\d\s().-]{7,}\d(?![A-Za-z0-9])"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|authorization|"
        r"client[_-]?secret|credentials?|private[_-]?key|"
        r"aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key|"
        r"access[_-]?key[_-]?id|secret[_-]?access[_-]?key|"
        r"access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*[^\r\n]+"),
    re.compile(
        r"(?i)\b(?:cookie|set-cookie)"
        r"\s*[:=]\s*(?=\S)[^\r\n]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bbasic(?:\s*[:=]\s*|\s+)[A-Za-z0-9+/=]+"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/@\s:]*:[^/@\s]+@"),
    re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
        r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----(?=\s*\S)[\s\S]+"),
)


@dataclass
class IngestState:
    request_count: int = 0
    stored_batches: List[dict] = field(default_factory=list)
    stored_events: List[dict] = field(default_factory=list)
    idempotency_keys: List[str] = field(default_factory=list)
    seen_keys: Set[str] = field(default_factory=set)
    accepted: threading.Event = field(default_factory=threading.Event)


def _json_response(status_code: int, content: dict) -> Response:
    return Response(
        status_code=status_code,
        content=json.dumps(content),
        media_type="application/json",
    )


def _valid_idempotency_key(value: Optional[str]) -> bool:
    if value is None:
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _valid_project(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(pattern.search(value) for pattern in _PROJECT_SENSITIVE_PATTERNS)
    )


def _valid_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    if not _nonempty_string(event.get("type")):
        return False
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return False
    try:
        uuid.UUID(event_id)
    except ValueError:
        return False
    if not _nonempty_string(event.get("session_id")):
        return False
    if not _nonnegative_int(event.get("emitted_at_ms")):
        return False
    tags = event.get("tags")
    if not _string_map(tags):
        return False
    required_fields = {
        "session.started": {"agent_name", "spec_hash", "environment", "transport"},
        "session.ended": {"reason", "duration_ms", "billable_audio_minutes"},
        "turn": {
            "turn_id",
            "turn_index",
            "latency_waterfall",
            "interrupted",
            "timeout_events",
        },
        "span": {
            "span_id",
            "name",
            "status",
            "started_at_ms",
            "ended_at_ms",
            "attributes",
        },
        "cost": {
            "stt_cost",
            "llm_cost",
            "tts_cost",
            "telephony_cost",
            "rag_cost",
            "mcp_tool_cost",
            "infra_cost",
            "billable_audio_minutes",
            "total_cost",
            "cost_per_minute",
        },
        "business": {
            "funnel_stage",
            "funnel_confidence",
            "sentiment_label",
            "sentiment_confidence",
        },
        "tool_call": {
            "turn_id",
            "server",
            "tool",
            "allowed",
            "latency_ms",
            "arguments",
        },
        "transcript": {"turn_id", "role", "text"},
        "audio_ref": {"blob_id", "upload_url_requested"},
    }.get(event["type"])
    if required_fields is None:
        return True
    if not required_fields.issubset(event):
        return False
    event_type = event["type"]
    if event_type == "session.started":
        return all(
            _nonempty_string(event[field])
            for field in ("agent_name", "spec_hash", "environment", "transport")
        ) and all(
            _optional_string(event, field)
            for field in ("agent_version", "graph_hash", "thread_id")
        )
    if event_type == "session.ended":
        return (
            _nonempty_string(event["reason"])
            and _nonnegative_int(event["duration_ms"])
            and _nonnegative_number(event["billable_audio_minutes"])
        )
    if event_type == "turn":
        waterfall = event["latency_waterfall"]
        waterfall_fields = {
            "stt_ms",
            "rag_ms",
            "llm_ms",
            "mcp_tools_ms",
            "tts_ms",
            "transport_ms",
        }
        return (
            _nonempty_string(event["turn_id"])
            and _nonnegative_int(event["turn_index"])
            and isinstance(waterfall, dict)
            and waterfall_fields.issubset(waterfall)
            and all(_nonnegative_number(waterfall[field]) for field in waterfall_fields)
            and isinstance(event["interrupted"], bool)
            and _string_list(event["timeout_events"])
        )
    if event_type == "span":
        return (
            all(_nonempty_string(event[field]) for field in ("span_id", "name"))
            and (event.get("turn_id") is None or _nonempty_string(event.get("turn_id")))
            and _optional_string(event, "parent_id")
            and event["status"] in ("ok", "fallback", "error", "cancelled")
            and _nonnegative_int(event["started_at_ms"])
            and _nonnegative_int(event["ended_at_ms"])
            and _string_map(event["attributes"])
        )
    if event_type == "cost":
        return (
            all(_nonnegative_number(event[field]) for field in required_fields)
            and event["billable_audio_minutes"] > 0
            and _optional_string(event, "turn_id")
            and _optional_nonempty_string(event, "pricebook_version")
            and (
                "attribution" not in event
                or (
                    isinstance(event["attribution"], dict)
                    and all(
                        _nonempty_string(key) and _nonnegative_number(value)
                        for key, value in event["attribution"].items()
                    )
                )
            )
        )
    if event_type == "business":
        return (
            _nonempty_string(event["funnel_stage"])
            and _bounded_confidence(event["funnel_confidence"])
            and _nonempty_string(event["sentiment_label"])
            and _bounded_confidence(event["sentiment_confidence"])
            and _optional_string(event, "turn_id")
        )
    if event_type == "tool_call":
        return (
            all(
                _nonempty_string(event[field])
                for field in ("turn_id", "server", "tool")
            )
            and isinstance(event["allowed"], bool)
            and _nonnegative_number(event["latency_ms"])
            and isinstance(event["arguments"], dict)
            and _optional_string(event, "error")
        )
    if event_type == "transcript":
        return (
            _nonempty_string(event["turn_id"])
            and event["role"] in ("caller", "agent")
            and isinstance(event["text"], str)
        )
    if event_type == "audio_ref":
        return (
            _opaque_recording_ref(event["blob_id"])
            and isinstance(event["upload_url_requested"], bool)
            and _optional_string(event, "turn_id")
            and _optional_opaque_recording_ref(event, "recording_id")
            and _optional_choice(event, "leg", {"caller", "agent", "mixed"})
            and _optional_nonnegative_int(event, "duration_ms")
            and _optional_nonnegative_int(event, "byte_count")
            and _optional_pattern(event, "sha256", r"[0-9a-f]{64}")
            and _optional_pattern(event, "container", r"[a-z0-9][a-z0-9._-]{0,31}")
            and _optional_opaque_recording_ref(event, "consent_ref")
        )
    return False


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _bounded_confidence(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _string_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    )


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _optional_string(event: dict, field: str) -> bool:
    value = event.get(field)
    return value is None or isinstance(value, str)


def _optional_nonempty_string(event: dict, field: str) -> bool:
    value = event.get(field)
    return value is None or _nonempty_string(value)


def _optional_nonnegative_int(event: dict, field: str) -> bool:
    value = event.get(field)
    return value is None or _nonnegative_int(value)


def _optional_choice(event: dict, field: str, choices: set[str]) -> bool:
    value = event.get(field)
    return value is None or (isinstance(value, str) and value in choices)


def _optional_pattern(event: dict, field: str, pattern: str) -> bool:
    value = event.get(field)
    return value is None or (
        isinstance(value, str) and re.fullmatch(pattern, value) is not None
    )


def _opaque_recording_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and re.fullmatch(r"[A-Za-z0-9._:-]+", value) is not None
        and sum(character.isdigit() for character in value) < 8
        and not any(pattern.search(value) for pattern in _PROJECT_SENSITIVE_PATTERNS)
    )


def _optional_opaque_recording_ref(event: dict, field: str) -> bool:
    value = event.get(field)
    return value is None or _opaque_recording_ref(value)


def create_ingest_app(
    api_key: str,
    *,
    rate_limit_after: Optional[int] = None,
    retry_after: str = "0",
    fail_first_n: int = 0,
) -> FastAPI:
    app = FastAPI()
    state = IngestState()
    app.state.ingest = state

    @app.post("/v1/events")
    async def ingest(request: Request) -> Response:
        state.request_count += 1
        if request.headers.get(HEADER_API_KEY) != api_key:
            return _json_response(401, {"detail": "invalid key"})
        if request.headers.get(HEADER_WIRE) != str(WIRE_VERSION):
            return _json_response(422, {"invalid_indices": []})
        if request.headers.get(HEADER_CONTENT_TYPE) != CONTENT_TYPE_JSON:
            return _json_response(422, {"invalid_indices": []})
        key = request.headers.get(HEADER_IDEMPOTENCY)
        if not _valid_idempotency_key(key):
            return _json_response(422, {"invalid_indices": []})
        assert key is not None
        state.idempotency_keys.append(key)
        if state.request_count <= fail_first_n:
            return Response(status_code=503)
        if rate_limit_after is not None and state.request_count == rate_limit_after + 1:
            return Response(
                status_code=429,
                headers={HEADER_RETRY_AFTER: retry_after},
            )

        body = await request.body()
        content_encoding = request.headers.get("content-encoding")
        if content_encoding == "gzip":
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError, zlib.error):
                return _json_response(422, {"invalid_indices": []})
        elif content_encoding is not None:
            return _json_response(422, {"invalid_indices": []})
        if len(body) > MAX_BATCH_BYTES:
            return Response(status_code=413)
        try:
            envelope = json.loads(body)
            project = envelope["project"]
            sdk = envelope["sdk"]
            events = envelope["events"]
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return _json_response(422, {"invalid_indices": []})
        if (
            not _valid_project(project)
            or not isinstance(sdk, dict)
            or sdk.get("name") != "lucy"
            or not isinstance(sdk.get("version"), str)
            or not sdk["version"]
        ):
            return _json_response(422, {"invalid_indices": []})
        if not isinstance(events, list):
            return _json_response(422, {"invalid_indices": []})
        if len(events) > MAX_BATCH_EVENTS:
            return Response(status_code=413)

        invalid = [
            index for index, event in enumerate(events) if not _valid_event(event)
        ]
        if invalid:
            return _json_response(422, {"invalid_indices": invalid})

        if key not in state.seen_keys:
            state.seen_keys.add(key)
            state.stored_batches.append(envelope)
            state.stored_events.extend(events)
            state.accepted.set()
        return Response(status_code=202)

    return app


def create_status_app(api_key: str, status_code: int) -> FastAPI:
    app = FastAPI()
    state = IngestState()
    app.state.ingest = state

    @app.post("/v1/events")
    async def ingest(request: Request) -> Response:
        state.request_count += 1
        if request.headers.get(HEADER_API_KEY) != api_key:
            return Response(status_code=401)
        return Response(status_code=status_code)

    return app


@contextmanager
def run_local_ingest(app: FastAPI) -> Iterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    ready = threading.Event()
    while not server.started:
        ready.wait(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
