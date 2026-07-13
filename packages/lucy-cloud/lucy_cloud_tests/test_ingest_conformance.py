from __future__ import annotations

import gzip
import json
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx
import pytest

from .helpers import wire_event
from .ingest_app import IngestState, create_ingest_app

API_KEY = "test-project-key"
DEFAULT_IDEMPOTENCY_KEY = "00000000-0000-4000-8000-000000000001"
ENV_CONFORMANCE_ENDPOINT = "LUCY_CONFORMANCE_ENDPOINT"
ENV_CONFORMANCE_API_KEY = "LUCY_CONFORMANCE_API_KEY"
ENV_CONFORMANCE_RATE_LIMITED_API_KEY = "LUCY_CONFORMANCE_RATE_LIMITED_API_KEY"
HEADER_API_KEY = "x-api-key"
HEADER_IDEMPOTENCY = "idempotency-key"
HEADER_WIRE = "x-lucy-wire"
MAX_BATCH_BYTES = 1_048_576
_DEFAULT_API_KEY = object()


@dataclass
class ConformanceTarget:
    base_url: str
    api_key: str
    transport: Optional[httpx.AsyncBaseTransport]
    state: Optional[IngestState]


def _local_target(app) -> ConformanceTarget:
    return ConformanceTarget(
        base_url="http://ingest",
        api_key=API_KEY,
        transport=httpx.ASGITransport(app=app),
        state=app.state.ingest,
    )


@pytest.fixture
def conformance_target() -> ConformanceTarget:
    endpoint = os.environ.get(ENV_CONFORMANCE_ENDPOINT)
    if endpoint:
        return ConformanceTarget(
            base_url=endpoint,
            api_key=os.environ[ENV_CONFORMANCE_API_KEY],
            transport=None,
            state=None,
        )
    return _local_target(create_ingest_app(API_KEY))


def _encode_vector(events, *, project="project-a", sdk=None):
    envelope = {
        "project": project,
        "sdk": {"name": "lucy", "version": "0.1.0"} if sdk is None else sdk,
        "events": events,
    }
    return gzip.compress(
        json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode(),
        mtime=0,
    )


def _invalid_indices(response: httpx.Response) -> list[int]:
    body = response.json()
    if "invalid_indices" in body:
        return body["invalid_indices"]
    return [error["index"] for error in body.get("errors", [])]


def _common(event_type: str, index: int) -> dict:
    return {
        "type": event_type,
        "event_id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"malformed-{event_type}-{index}")
        ),
        "session_id": "session-1",
        "emitted_at_ms": index,
        "tags": {},
    }


def _valid_known_events() -> dict[str, dict]:
    return {
        "session.started": {
            **_common("session.started", 30),
            "agent_name": "booking",
            "spec_hash": "spec",
            "environment": "test",
            "transport": "sim",
        },
        "session.ended": {
            **_common("session.ended", 31),
            "reason": "completed",
            "duration_ms": 1,
            "billable_audio_minutes": 1.0,
        },
        "turn": wire_event(32),
        "span": {
            **_common("span", 33),
            "span_id": "span-1",
            "parent_id": None,
            "turn_id": "turn-1",
            "name": "node",
            "status": "ok",
            "started_at_ms": 1,
            "ended_at_ms": 2,
            "attributes": {},
        },
        "cost": {
            **_common("cost", 34),
            "stt_cost": 0.0,
            "llm_cost": 0.0,
            "tts_cost": 0.0,
            "telephony_cost": 0.0,
            "rag_cost": 0.0,
            "mcp_tool_cost": 0.0,
            "infra_cost": 0.0,
            "billable_audio_minutes": 1.0,
            "total_cost": 0.0,
            "cost_per_minute": 0.0,
            "pricebook_version": "voice-2026-07",
            "attribution": {"llm_prompt_tokens": 10.0},
        },
        "business": {
            **_common("business", 35),
            "funnel_stage": "booked",
            "funnel_confidence": 1.0,
            "sentiment_label": "positive",
            "sentiment_confidence": 1.0,
        },
        "tool_call": {
            **_common("tool_call", 36),
            "turn_id": "turn-1",
            "server": "calendar",
            "tool": "book",
            "allowed": True,
            "latency_ms": 1.0,
            "arguments": {},
        },
        "transcript": {
            **_common("transcript", 37),
            "turn_id": "turn-1",
            "role": "caller",
            "text": "hello",
        },
        "audio_ref": {
            **_common("audio_ref", 38),
            "blob_id": "blob-1",
            "upload_url_requested": False,
            "duration_ms": 1,
            "byte_count": 1,
        },
    }


def _required_string_vectors() -> list[tuple[dict, str]]:
    events = _valid_known_events()
    fields = {
        "session.started": (
            "type",
            "session_id",
            "agent_name",
            "spec_hash",
            "environment",
            "transport",
        ),
        "session.ended": ("type", "session_id", "reason"),
        "turn": ("type", "session_id", "turn_id"),
        "span": ("type", "session_id", "span_id", "name"),
        "cost": ("type", "session_id"),
        "business": ("type", "session_id", "funnel_stage", "sentiment_label"),
        "tool_call": ("type", "session_id", "turn_id", "server", "tool"),
        "transcript": ("type", "session_id", "turn_id"),
        "audio_ref": ("type", "session_id", "blob_id"),
    }
    return [
        (events[event_type], field)
        for event_type, event_fields in fields.items()
        for field in event_fields
    ]


def _nonfinite_vectors() -> list[tuple[dict, tuple[str, ...]]]:
    events = _valid_known_events()
    fields = {
        "session.started": (("emitted_at_ms",),),
        "session.ended": (
            ("emitted_at_ms",),
            ("duration_ms",),
            ("billable_audio_minutes",),
        ),
        "turn": (
            ("emitted_at_ms",),
            ("turn_index",),
            *(
                ("latency_waterfall", field)
                for field in (
                    "stt_ms",
                    "rag_ms",
                    "llm_ms",
                    "mcp_tools_ms",
                    "tts_ms",
                    "transport_ms",
                )
            ),
        ),
        "span": (("started_at_ms",), ("ended_at_ms",)),
        "cost": tuple(
            (field,)
            for field in (
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
            )
        ),
        "business": (("funnel_confidence",), ("sentiment_confidence",)),
        "tool_call": (("latency_ms",),),
        "audio_ref": (("duration_ms",), ("byte_count",)),
    }
    return [
        (events[event_type], path)
        for event_type, event_fields in fields.items()
        for path in event_fields
    ]


async def _post(
    target: ConformanceTarget,
    events,
    *,
    api_key=_DEFAULT_API_KEY,
    idem=DEFAULT_IDEMPOTENCY_KEY,
    project="project-a",
    sdk=None,
    content_type="application/json",
):
    headers = {
        HEADER_WIRE: "1",
        HEADER_IDEMPOTENCY: idem,
        "content-type": content_type,
        "content-encoding": "gzip",
    }
    if api_key is not None:
        headers[HEADER_API_KEY] = (
            target.api_key if api_key is _DEFAULT_API_KEY else str(api_key)
        )
    async with httpx.AsyncClient(
        transport=target.transport,
        base_url=target.base_url,
    ) as client:
        return await client.post(
            "/v1/events",
            content=_encode_vector(events, project=project, sdk=sdk),
            headers=headers,
        )


async def test_valid_batch_accepted_with_202(conformance_target):
    response = await _post(conformance_target, [wire_event()])
    assert response.status_code == 202


async def test_exact_event_count_limit_accepted(conformance_target):
    response = await _post(
        conformance_target,
        [wire_event(index) for index in range(100)],
    )
    assert response.status_code == 202


async def test_exact_decompressed_byte_limit_accepted(conformance_target):
    event = wire_event(payload="")
    base_size = len(
        json.dumps(
            {
                "project": "project-a",
                "sdk": {"name": "lucy", "version": "0.1.0"},
                "events": [event],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    event["payload"] = "x" * (MAX_BATCH_BYTES - base_size)
    assert (await _post(conformance_target, [event])).status_code == 202


@pytest.mark.parametrize("api_key", [None, "wrong"])
async def test_missing_or_wrong_api_key_rejected_401(conformance_target, api_key):
    assert (
        await _post(conformance_target, [wire_event()], api_key=api_key)
    ).status_code == 401


async def test_oversized_batch_rejected_413(conformance_target):
    events = [wire_event(index) for index in range(101)]
    assert (await _post(conformance_target, events)).status_code == 413


async def test_oversized_decompressed_body_rejected_413(conformance_target):
    event = wire_event(payload="x" * MAX_BATCH_BYTES)
    assert (await _post(conformance_target, [event])).status_code == 413


async def test_malformed_events_rejected_422_with_indices(conformance_target):
    response = await _post(conformance_target, [wire_event(), {"type": "turn"}])
    assert response.status_code == 422
    assert _invalid_indices(response) == [1]


async def test_missing_type_specific_fields_rejected_422(conformance_target):
    event = wire_event()
    event.pop("turn_id")
    response = await _post(conformance_target, [event])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


@pytest.mark.parametrize(("event", "field"), _required_string_vectors())
async def test_empty_required_strings_rejected_422_with_index(
    conformance_target, event, field
):
    invalid = {**event, field: ""}
    response = await _post(conformance_target, [invalid])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


@pytest.mark.parametrize(("event", "path"), _nonfinite_vectors())
@pytest.mark.parametrize("value", [float("inf"), float("nan")])
async def test_nonfinite_numbers_rejected_422_with_index(
    conformance_target, event, path, value
):
    invalid = {**event}
    if len(path) == 1:
        invalid[path[0]] = value
    else:
        invalid[path[0]] = {**invalid[path[0]], path[1]: value}
    response = await _post(conformance_target, [invalid])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


async def test_span_optional_parent_and_turn_ids_can_be_omitted(conformance_target):
    event = {
        **_common("span", 9),
        "span_id": "span-session",
        "name": "lucy.session",
        "status": "ok",
        "started_at_ms": 1,
        "ended_at_ms": 2,
        "attributes": {},
    }
    assert (await _post(conformance_target, [event])).status_code == 202


async def test_cost_additive_pricing_metadata_is_accepted(conformance_target):
    event = _valid_known_events()["cost"]
    assert (await _post(conformance_target, [event])).status_code == 202


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pricebook_version", ""),
        ("attribution", []),
        ("attribution", {"llm_prompt_tokens": -1.0}),
        ("attribution", {"llm_prompt_tokens": float("inf")}),
        ("attribution", {"": 1.0}),
    ],
)
async def test_invalid_cost_pricing_metadata_is_rejected(
    conformance_target, field, value
):
    event = {**_valid_known_events()["cost"], field: value}
    response = await _post(conformance_target, [event])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


@pytest.mark.parametrize(
    "event",
    [
        {**wire_event(), "event_id": "not-a-uuid"},
        {**wire_event(), "emitted_at_ms": -1},
        {
            **_common("session.started", 10),
            "agent_name": 7,
            "spec_hash": "spec",
            "environment": "test",
            "transport": "sim",
        },
        {
            **_common("session.ended", 11),
            "reason": "done",
            "duration_ms": -1,
            "billable_audio_minutes": 1.0,
        },
        {
            **wire_event(12),
            "turn_index": "first",
            "latency_waterfall": None,
            "interrupted": "no",
            "timeout_events": {},
        },
        {
            **_common("span", 13),
            "span_id": "span-1",
            "parent_id": None,
            "turn_id": "turn-1",
            "name": "node",
            "status": "unknown",
            "started_at_ms": 1,
            "ended_at_ms": 2,
            "attributes": {},
        },
        {
            **_common("cost", 14),
            "stt_cost": "free",
            "llm_cost": 0.0,
            "tts_cost": 0.0,
            "telephony_cost": 0.0,
            "rag_cost": 0.0,
            "mcp_tool_cost": 0.0,
            "infra_cost": 0.0,
            "billable_audio_minutes": 1.0,
            "total_cost": 0.0,
            "cost_per_minute": 0.0,
        },
        {
            **_common("business", 15),
            "funnel_stage": "booked",
            "funnel_confidence": 2.0,
            "sentiment_label": "positive",
            "sentiment_confidence": 1.0,
        },
        {
            **_common("tool_call", 16),
            "turn_id": "turn-1",
            "server": "calendar",
            "tool": "book",
            "allowed": True,
            "latency_ms": 1.0,
            "arguments": [],
        },
        {
            **_common("transcript", 17),
            "turn_id": "turn-1",
            "role": "system",
            "text": "hello",
        },
        {
            **_common("audio_ref", 18),
            "blob_id": "blob-1",
            "upload_url_requested": "yes",
        },
    ],
)
async def test_malformed_type_specific_values_rejected_422(conformance_target, event):
    response = await _post(conformance_target, [event])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("blob_id", "recording_id", "consent_ref")
        for value in (
            "credential@example.com",
            "api_key:super-secret",
            "Basic:dXNlcjpwYXNz",
            "aws_access_key_id:example",
            "aws_secret_access_key:example",
            "cookie:value",
            "x" * 129,
            "12345678",
        )
    ],
)
async def test_unsafe_recording_references_rejected_422(
    conformance_target, field, value
):
    event = {
        **_common("audio_ref", 19),
        "blob_id": "blob-safe",
        "upload_url_requested": False,
        field: value,
    }
    response = await _post(conformance_target, [event])
    assert response.status_code == 422
    assert _invalid_indices(response) == [0]


@pytest.mark.parametrize(
    "idem", ["", "not-a-uuid", "00000000-0000-0000-0000-00000000000x"]
)
async def test_missing_or_invalid_idempotency_rejected_422(conformance_target, idem):
    assert (
        await _post(conformance_target, [wire_event()], idem=idem)
    ).status_code == 422


async def test_wrong_content_type_rejected_422(conformance_target):
    response = await _post(
        conformance_target,
        [wire_event()],
        content_type="text/plain",
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("project", "sdk"),
    [
        ("", {"name": "lucy", "version": "0.1.0"}),
        ("token=project-secret", {"name": "lucy", "version": "0.1.0"}),
        ("owner@example.com", {"name": "lucy", "version": "0.1.0"}),
        ("Basic:dXNlcjpwYXNz", {"name": "lucy", "version": "0.1.0"}),
        ("Basic=dXNlcjpwYXNz", {"name": "lucy", "version": "0.1.0"}),
        ("basic = dXNlcjpwYXNz", {"name": "lucy", "version": "0.1.0"}),
        (
            "redis://:project-password@cache.local/0",
            {"name": "lucy", "version": "0.1.0"},
        ),
        ("cookie = project-value", {"name": "lucy", "version": "0.1.0"}),
        ("authorization: \t", {"name": "lucy", "version": "0.1.0"}),
        ("proxy-authorization = ", {"name": "lucy", "version": "0.1.0"}),
        (
            "-----BEGIN PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----",
            {"name": "lucy", "version": "0.1.0"},
        ),
        (
            "-----BEGIN PRIVATE KEY-----\ntruncated-material",
            {"name": "lucy", "version": "0.1.0"},
        ),
        ("aws_access_key_id:example", {"name": "lucy", "version": "0.1.0"}),
        ("aws_secret_access_key:example", {"name": "lucy", "version": "0.1.0"}),
        ("project-a", {}),
        ("project-a", {"name": "other", "version": "0.1.0"}),
    ],
)
async def test_malformed_envelope_rejected_422(conformance_target, project, sdk):
    assert (
        await _post(conformance_target, [wire_event()], project=project, sdk=sdk)
    ).status_code == 422


@pytest.mark.parametrize(
    "project",
    [
        "token=",
        "cookie:",
        "Cookie=   ",
        "cookie: \t",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
async def test_incomplete_credential_marker_project_is_not_a_secret(
    conformance_target, project
):
    assert (
        await _post(conformance_target, [wire_event()], project=project)
    ).status_code == 202


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("blob_id", "recording_id", "consent_ref")
        for value in ("token:", "cookie:")
    ],
)
async def test_incomplete_credential_marker_recording_ref_is_opaque(
    conformance_target, field, value
):
    event = {
        **_common("audio_ref", 20),
        "blob_id": "blob-safe",
        "upload_url_requested": False,
        field: value,
    }
    assert (await _post(conformance_target, [event])).status_code == 202


async def test_rate_limit_includes_retry_after_header(conformance_target):
    if conformance_target.state is not None:
        target = _local_target(create_ingest_app(API_KEY, rate_limit_after=0))
        api_key = _DEFAULT_API_KEY
    else:
        target = conformance_target
        api_key = os.environ.get(ENV_CONFORMANCE_RATE_LIMITED_API_KEY)
        if not api_key:
            pytest.skip(
                f"{ENV_CONFORMANCE_RATE_LIMITED_API_KEY} is required for external 429"
            )
    response = await _post(target, [wire_event()], api_key=api_key)
    assert response.status_code == 429
    retry_after = float(response.headers["retry-after"])
    assert retry_after >= 0
    assert retry_after < float("inf")


async def test_duplicate_idempotency_key_is_accepted_idempotently(conformance_target):
    idem = "00000000-0000-4000-8000-000000000002"
    assert (
        await _post(conformance_target, [wire_event()], idem=idem)
    ).status_code == 202
    assert (
        await _post(conformance_target, [wire_event()], idem=idem)
    ).status_code == 202
    if conformance_target.state is not None:
        assert len(conformance_target.state.stored_batches) == 1


async def test_unknown_event_type_accepted_opaquely(conformance_target):
    event = wire_event()
    event["type"] = "future.event"
    response = await _post(conformance_target, [event])
    assert response.status_code == 202
    if conformance_target.state is not None:
        assert conformance_target.state.stored_events[-1] == event


@pytest.mark.parametrize(
    "invalid_body",
    [
        json.dumps({"events": []}).encode(),
        gzip.compress(b"{}")[:-4],
        b"\x1f\x8b\x08\x00corrupt",
        gzip.compress(b"\xff"),
    ],
)
async def test_invalid_gzip_is_rejected_422(conformance_target, invalid_body):
    headers = {
        HEADER_API_KEY: conformance_target.api_key,
        HEADER_IDEMPOTENCY: DEFAULT_IDEMPOTENCY_KEY,
        HEADER_WIRE: "1",
        "content-type": "application/json",
        "content-encoding": "gzip",
    }
    async with httpx.AsyncClient(
        transport=conformance_target.transport,
        base_url=conformance_target.base_url,
    ) as client:
        response = await client.post(
            "/v1/events", content=invalid_body, headers=headers
        )
    assert response.status_code == 422


async def test_unsupported_content_encoding_is_rejected_422(conformance_target):
    headers = {
        HEADER_API_KEY: conformance_target.api_key,
        HEADER_IDEMPOTENCY: DEFAULT_IDEMPOTENCY_KEY,
        HEADER_WIRE: "1",
        "content-type": "application/json",
        "content-encoding": "br",
    }
    async with httpx.AsyncClient(
        transport=conformance_target.transport,
        base_url=conformance_target.base_url,
    ) as client:
        response = await client.post(
            "/v1/events",
            content=json.dumps({"project": "project-a", "sdk": {}, "events": []}),
            headers=headers,
        )
    assert response.status_code == 422
