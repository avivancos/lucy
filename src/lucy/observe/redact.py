"""Client-side telemetry privacy pass (wire controls, ADR 0010).

Before export, this module applies sampling-adjacent suppression, always-on
secret scrubbing, optional PII redaction, bounded recursive traversal,
structural-identifier rejection, and identity/payload-bound export approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Dict, Optional

from lucy.privacy import (
    REDACTED,
    is_secret_key as _is_secret_key,
    redact_pii as _redact_pii,
    redact_text as _redact_text,
    scrub_secrets as _scrub_secrets,
    contains_configured_secret as _contains_configured_secret,
)
from lucy.observe.events import (
    RAG_CHUNKS_ATTRIBUTE,
    RAG_PROMPT_GROUNDING_IDS_ATTRIBUTE,
    RAG_QUERY_ATTRIBUTE,
    AudioRefEvent,
    BusinessEvent,
    CostEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SpanEvent,
    TelemetryEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnEvent,
)

MAX_REDACTION_DEPTH = 32
_EXPORT_APPROVAL = object()


def redact_text(text: str) -> str:
    """Preserve the observability package's public sanitization helper."""
    return _redact_text(text)


def _sanitize_text(text: str, *, redact_pii: bool) -> str:
    scrubbed = _scrub_secrets(text)
    return _redact_pii(scrubbed) if redact_pii else scrubbed


class PrivacyTraversalError(ValueError):
    """Reject arguments that cannot be processed safely for export."""


def _process_json(
    value: object,
    *,
    redact_pii: bool,
    depth: int = 0,
    active: Optional[set[int]] = None,
) -> object:
    if isinstance(value, str):
        return _sanitize_text(value, redact_pii=redact_pii)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PrivacyTraversalError("tool arguments contain a nonfinite number")
        return value
    if depth >= MAX_REDACTION_DEPTH:
        raise PrivacyTraversalError("tool arguments exceed privacy depth limit")
    active = active if active is not None else set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise PrivacyTraversalError("tool arguments contain a cycle")
        active.add(identity)
        try:
            processed = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PrivacyTraversalError("tool argument keys must be strings")
                safe_key = _sanitize_text(key, redact_pii=redact_pii)
                processed[safe_key] = (
                    REDACTED
                    if _is_secret_key(key)
                    else _process_json(
                        item,
                        redact_pii=redact_pii,
                        depth=depth + 1,
                        active=active,
                    )
                )
            return processed
        finally:
            active.remove(identity)
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise PrivacyTraversalError("tool arguments contain a cycle")
        active.add(identity)
        try:
            return [
                _process_json(
                    item,
                    redact_pii=redact_pii,
                    depth=depth + 1,
                    active=active,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    raise PrivacyTraversalError("tool arguments contain a non-JSON value")


def _sanitize_string_map(values: Dict[str, str], *, redact_pii: bool) -> Dict[str, str]:
    return {
        _sanitize_text(key, redact_pii=redact_pii): (
            REDACTED
            if _is_secret_key(key)
            else _sanitize_text(value, redact_pii=redact_pii)
        )
        for key, value in values.items()
    }


def _sanitize_json_attribute(value: str, *, redact_pii: bool) -> object:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PrivacyTraversalError("span attribute contains invalid JSON") from exc
    return _process_json(parsed, redact_pii=redact_pii)


def _sanitize_span_attributes(
    values: Dict[str, str],
    *,
    redact_pii: bool,
    transcripts_enabled: bool,
) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    for key, value in values.items():
        safe_key = _sanitize_text(key, redact_pii=redact_pii)
        if _is_secret_key(key):
            sanitized[safe_key] = REDACTED
            continue
        if key == RAG_QUERY_ATTRIBUTE and not transcripts_enabled:
            continue
        if key == RAG_CHUNKS_ATTRIBUTE:
            chunks = _sanitize_json_attribute(value, redact_pii=redact_pii)
            if not isinstance(chunks, list) or any(
                not isinstance(chunk, dict) for chunk in chunks
            ):
                raise PrivacyTraversalError("rag.chunks must encode a list of objects")
            if not transcripts_enabled:
                for chunk in chunks:
                    chunk.pop("text", None)
            sanitized[safe_key] = json.dumps(
                chunks,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            continue
        if key == RAG_PROMPT_GROUNDING_IDS_ATTRIBUTE:
            grounding_ids = _sanitize_json_attribute(value, redact_pii=redact_pii)
            if not isinstance(grounding_ids, list) or any(
                not isinstance(item, str) for item in grounding_ids
            ):
                raise PrivacyTraversalError(
                    "rag.prompt_included_grounding_ids must encode a string list"
                )
            sanitized[safe_key] = json.dumps(
                grounding_ids,
                allow_nan=False,
                separators=(",", ":"),
            )
            continue
        sanitized[safe_key] = _sanitize_text(value, redact_pii=redact_pii)
    return sanitized


def _structural_values(event: TelemetryEvent) -> list[str]:
    values = [event.session_id]
    turn_id = getattr(event, "turn_id", None)
    if isinstance(turn_id, str):
        values.append(turn_id)
    if isinstance(event, SessionStartedEvent):
        values.extend(
            item
            for item in (event.spec_hash, event.graph_hash, event.thread_id)
            if item is not None
        )
    if isinstance(event, SpanEvent):
        values.extend(
            item for item in (event.span_id, event.parent_id) if item is not None
        )
    return values


def _reject_unsafe_structural_values(
    event: TelemetryEvent, *, redact_pii: bool
) -> None:
    for value in _structural_values(event):
        if _sanitize_text(value, redact_pii=redact_pii) != value:
            raise PrivacyTraversalError(
                "structural telemetry identifiers must be opaque"
            )


def _reject_configured_secrets(
    value: object,
    secrets: Sequence[str],
    *,
    depth: int = 0,
) -> None:
    if isinstance(value, str):
        if _contains_configured_secret(value, secrets):
            raise PrivacyTraversalError("telemetry contains a configured secret")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if depth >= MAX_REDACTION_DEPTH:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_configured_secrets(key, secrets, depth=depth + 1)
            _reject_configured_secrets(item, secrets, depth=depth + 1)
        return
    if isinstance(value, Iterable):
        for item in value:
            _reject_configured_secrets(item, secrets, depth=depth + 1)


def redact_event(
    event: TelemetryEvent,
    *,
    redact_pii: bool,
    record_audio: bool,
    transcripts_enabled: bool,
    configured_secrets: Sequence[str] = (),
) -> Optional[TelemetryEvent]:
    """Return a sanitized event, or ``None`` to suppress it.

    Cloud approval is deliberately absent here and is issued only by
    ``Tracer._enqueue`` after sampling and all configured policy gates.
    """
    if isinstance(event, TranscriptEvent) and not transcripts_enabled:
        return None
    if isinstance(event, AudioRefEvent) and not record_audio:
        return None
    _reject_configured_secrets(
        event.__dict__,
        configured_secrets,
    )
    _reject_unsafe_structural_values(event, redact_pii=redact_pii)
    safe = event
    if isinstance(safe, ToolCallEvent):
        arguments = _process_json(safe.arguments, redact_pii=redact_pii)
        assert isinstance(arguments, dict)
        safe = safe.model_copy(update={"arguments": arguments})
    safe = safe.model_copy(
        update={
            "tags": _sanitize_string_map(safe.tags, redact_pii=redact_pii),
        }
    )
    if isinstance(safe, SessionStartedEvent):
        safe = safe.model_copy(
            update={
                "agent_name": _sanitize_text(safe.agent_name, redact_pii=redact_pii),
                "environment": _sanitize_text(safe.environment, redact_pii=redact_pii),
                "transport": _sanitize_text(safe.transport, redact_pii=redact_pii),
                "agent_version": _sanitize_optional(
                    safe.agent_version, redact_pii=redact_pii
                ),
            }
        )
    if isinstance(safe, SessionEndedEvent):
        safe = safe.model_copy(
            update={"reason": _sanitize_text(safe.reason, redact_pii=redact_pii)}
        )
    if isinstance(safe, TurnEvent):
        safe = safe.model_copy(
            update={
                "timeout_events": [
                    _sanitize_text(item, redact_pii=redact_pii)
                    for item in safe.timeout_events
                ]
            }
        )
    if isinstance(safe, SpanEvent):
        safe = safe.model_copy(
            update={
                "name": _sanitize_text(safe.name, redact_pii=redact_pii),
                "attributes": _sanitize_span_attributes(
                    safe.attributes,
                    redact_pii=redact_pii,
                    transcripts_enabled=transcripts_enabled,
                ),
            }
        )
    if isinstance(safe, BusinessEvent):
        safe = safe.model_copy(
            update={
                "funnel_stage": _sanitize_text(
                    safe.funnel_stage, redact_pii=redact_pii
                ),
                "sentiment_label": _sanitize_text(
                    safe.sentiment_label, redact_pii=redact_pii
                ),
            }
        )
    if isinstance(safe, CostEvent):
        safe = safe.model_copy(
            update={
                "pricebook_version": _sanitize_optional(
                    safe.pricebook_version, redact_pii=redact_pii
                ),
                "attribution": {
                    _sanitize_text(key, redact_pii=redact_pii): value
                    for key, value in safe.attribution.items()
                },
                "provider_attribution": {
                    component: identity.model_copy(
                        update={
                            "provider": _sanitize_text(
                                identity.provider, redact_pii=redact_pii
                            ),
                            "model": _sanitize_optional(
                                identity.model, redact_pii=redact_pii
                            ),
                        }
                    )
                    for component, identity in safe.provider_attribution.items()
                },
            }
        )
    if isinstance(safe, TranscriptEvent):
        safe = safe.model_copy(
            update={"text": _sanitize_text(safe.text, redact_pii=redact_pii)}
        )
    if isinstance(safe, ToolCallEvent):
        safe = safe.model_copy(
            update={
                "server": _sanitize_text(safe.server, redact_pii=redact_pii),
                "tool": _sanitize_text(safe.tool, redact_pii=redact_pii),
                "error": _sanitize_optional(safe.error, redact_pii=redact_pii),
            }
        )
    return safe


def _sanitize_optional(value: Optional[str], *, redact_pii: bool) -> Optional[str]:
    return _sanitize_text(value, redact_pii=redact_pii) if value is not None else None


def _approve_event(event: TelemetryEvent) -> TelemetryEvent:
    event._export_approval = (
        _EXPORT_APPROVAL,
        id(event),
        _event_fingerprint(event),
    )
    return event


def _is_export_approved(event: TelemetryEvent) -> bool:
    approval = event._export_approval
    return (
        isinstance(approval, tuple)
        and len(approval) == 3
        and approval[0] is _EXPORT_APPROVAL
        and approval[1] == id(event)
        and isinstance(approval[2], bytes)
        and hmac.compare_digest(approval[2], _event_fingerprint(event))
    )


def _event_fingerprint(event: TelemetryEvent) -> bytes:
    try:
        payload = json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PrivacyTraversalError("event cannot be fingerprinted safely") from exc
    return hashlib.sha256(payload).digest()
