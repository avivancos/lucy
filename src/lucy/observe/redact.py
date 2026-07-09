"""Client-side privacy pass (wire spec privacy controls, ADR 0010).

Nothing sensitive leaves the process unless explicitly enabled. ``redact_event``
runs over every event BEFORE it is enqueued for export: it drops suppressed
event types (transcripts kill-switch, audio when ``record_audio`` is false) and
redacts PII from transcript text and tool-call arguments.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from lucy.observe.events import (
    AudioRefEvent,
    TelemetryEvent,
    ToolCallEvent,
    TranscriptEvent,
)

REDACTED = "[REDACTED]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def redact_text(text: str) -> str:
    """Mask emails and phone-number-like digit runs deterministically."""
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE.sub(REDACTED, text)
    return text


def _redact_arguments(arguments: Dict[str, object]) -> Dict[str, object]:
    return {
        key: redact_text(value) if isinstance(value, str) else value
        for key, value in arguments.items()
    }


def _redact_tags(tags: Dict[str, str]) -> Dict[str, str]:
    return {redact_text(key): redact_text(value) for key, value in tags.items()}


def redact_event(
    event: TelemetryEvent,
    *,
    redact_pii: bool,
    record_audio: bool,
    transcripts_enabled: bool,
) -> Optional[TelemetryEvent]:
    """Return the export-safe event, or ``None`` to drop it entirely."""
    if isinstance(event, TranscriptEvent) and not transcripts_enabled:
        return None
    if isinstance(event, AudioRefEvent) and not record_audio:
        return None
    if not redact_pii:
        return event
    safe = event.model_copy(update={"tags": _redact_tags(event.tags)})
    if isinstance(safe, TranscriptEvent):
        return safe.model_copy(update={"text": redact_text(safe.text)})
    if isinstance(safe, ToolCallEvent):
        return safe.model_copy(update={"arguments": _redact_arguments(safe.arguments)})
    return safe
