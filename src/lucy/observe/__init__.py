"""lucy.observe - the open/closed telemetry seam (wire spec v1, ADR 0010).

``configure()`` builds a :class:`Tracer` whose typed emit methods enqueue
telemetry events. Events pass a deterministic per-session sampling gate and a
client-side privacy pass before being buffered in a bounded, fail-open queue and
flushed to pluggable exporters. The wire protocol (docs/telemetry-wire-v1.md),
not shared code, is the contract with any ingest service.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Sequence

from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe.events import (
    WIRE_VERSION,
    AudioRefEvent,
    BusinessEvent,
    CostEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SpanEvent,
    TelemetryEvent,
    TelemetryEventBase,
    ToolCallEvent,
    TranscriptEvent,
    TurnEvent,
)
from lucy.observe.exporters import (
    ConsoleExporter,
    JsonlFileExporter,
    OtlpBridgeExporter,
    TraceExporter,
)
from lucy.observe.otel import (
    ObservabilityEvent,
    OtelExporterBridge,
    OtelSpan,
    OtelSpanExporter,
    TraceAttribute,
)
from lucy.observe.redact import (
    PrivacyTraversalError,
    _approve_event,
    redact_event,
    redact_text,
)

DEFAULT_QUEUE_MAXLEN = 2048
EXPORTER_ENTRY_POINT_GROUP = "lucy.exporters"
TAGS_ENV_VAR = "LUCY_TAGS"
TAG_PAIR_SEPARATOR = ","
TAG_KEY_VALUE_SEPARATOR = "="

__all__ = [
    "Tracer",
    "configure",
    "get_tracer",
    "set_tracer",
    "TraceExporter",
    "ConsoleExporter",
    "JsonlFileExporter",
    "OtlpBridgeExporter",
    "TelemetryEvent",
    "TelemetryEventBase",
    "SessionStartedEvent",
    "SessionEndedEvent",
    "TurnEvent",
    "SpanEvent",
    "CostEvent",
    "BusinessEvent",
    "ToolCallEvent",
    "TranscriptEvent",
    "AudioRefEvent",
    "redact_event",
    "redact_text",
    "WIRE_VERSION",
    # backward-compatible OTel bridge
    "ObservabilityEvent",
    "OtelExporterBridge",
    "OtelSpanExporter",
    "OtelSpan",
    "TraceAttribute",
]

Clock = Callable[[], int]
IdFactory = Callable[[], str]


def _default_clock() -> int:
    return int(time.time() * 1000)


def _default_id_factory() -> str:
    return uuid.uuid4().hex


class Tracer:
    """Buffers telemetry events and flushes them to exporters, fail-open.

    Typed emit methods build validated :class:`TelemetryEvent` models. Each
    event is gated by deterministic per-session sampling, run through the
    privacy pass, then appended to a bounded queue. Operational, privacy, and
    exporter failures are fail-open and increment ``dropped_events``; invalid
    caller-supplied event schema values still raise validation errors.
    """

    def __init__(
        self,
        *,
        exporters: Sequence[TraceExporter],
        sample_rate: float = 1.0,
        redact_pii: bool = True,
        record_audio: bool = False,
        transcripts_enabled: bool = True,
        enabled: bool = True,
        queue_maxlen: int = DEFAULT_QUEUE_MAXLEN,
        clock: Optional[Clock] = None,
        id_factory: Optional[IdFactory] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        self._exporters: List[TraceExporter] = list(exporters)
        self._sample_rate = sample_rate
        self._redact_pii = redact_pii
        self._record_audio = record_audio
        self._transcripts_enabled = transcripts_enabled
        self._enabled = enabled
        self._maxlen = queue_maxlen
        self._queue: Deque[TelemetryEvent] = deque()
        self._clock = clock or _default_clock
        self._id_factory = id_factory or _default_id_factory
        self._tags = dict(tags or {})
        self.dropped_events = 0

    # -- introspection (used by runtime instrumentation) --------------------

    @property
    def enabled(self) -> bool:
        """Whether events are recorded. Instrumentation checks this first so a
        disabled tracer costs nothing (no event model built, no enqueue)."""
        return self._enabled

    def now_ms(self) -> int:
        """Current time in epoch milliseconds from the tracer's clock."""
        return self._clock()

    def new_id(self) -> str:
        """Mint an id from the tracer's id factory (e.g. for span ids)."""
        return self._id_factory()

    def audio_recording_allowed(self, session_id: str) -> bool:
        """Whether recording may create an asset that this tracer can reference."""
        return (
            self._enabled and self._record_audio and self._session_sampled(session_id)
        )

    # -- sampling -----------------------------------------------------------

    def _session_sampled(self, session_id: str) -> bool:
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
        fraction = int(digest, 16) / 0xFFFFFFFF
        return fraction < self._sample_rate

    # -- enqueue path -------------------------------------------------------

    def _enqueue(self, event: TelemetryEvent) -> None:
        if not self._enabled:
            return
        if not self._session_sampled(event.session_id):
            return
        try:
            safe = redact_event(
                event,
                redact_pii=self._redact_pii,
                record_audio=self._record_audio,
                transcripts_enabled=self._transcripts_enabled,
            )
        except PrivacyTraversalError:
            self.dropped_events += 1
            return
        if safe is None:
            return
        if len(self._queue) >= self._maxlen:
            self.dropped_events += 1
            return
        self._queue.append(_approve_event(safe))

    def _now(self, emitted_at_ms: Optional[int]) -> int:
        return self._clock() if emitted_at_ms is None else emitted_at_ms

    def _id(self, event_id: Optional[str]) -> str:
        return self._id_factory() if event_id is None else event_id

    def _event_tags(self, tags: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged = dict(self._tags)
        merged.update(tags or {})
        return {str(key): str(value) for key, value in merged.items()}

    # -- typed emit methods -------------------------------------------------

    def session_started(
        self,
        *,
        session_id: str,
        agent_name: str,
        spec_hash: str,
        environment: str,
        transport: str,
        agent_version: Optional[str] = None,
        graph_hash: Optional[str] = None,
        thread_id: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            SessionStartedEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                agent_name=agent_name,
                spec_hash=spec_hash,
                environment=environment,
                transport=transport,
                agent_version=agent_version,
                graph_hash=graph_hash,
                thread_id=thread_id,
                tags=self._event_tags(tags),
            )
        )

    def session_ended(
        self,
        *,
        session_id: str,
        reason: str,
        duration_ms: int,
        billable_audio_minutes: float,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            SessionEndedEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                reason=reason,
                duration_ms=duration_ms,
                billable_audio_minutes=billable_audio_minutes,
                tags=self._event_tags(tags),
            )
        )

    def turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        turn_index: int,
        latency_waterfall: LatencyWaterfall,
        interrupted: bool = False,
        timeout_events: Optional[List[str]] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            TurnEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                turn_id=turn_id,
                turn_index=turn_index,
                latency_waterfall=latency_waterfall,
                interrupted=interrupted,
                timeout_events=list(timeout_events or []),
                tags=self._event_tags(tags),
            )
        )

    def span(
        self,
        *,
        session_id: str,
        turn_id: Optional[str],
        span_id: str,
        name: str,
        status: str,
        started_at_ms: int,
        ended_at_ms: int,
        parent_id: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            SpanEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                turn_id=turn_id,
                span_id=span_id,
                name=name,
                status=status,  # type: ignore[arg-type]
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                parent_id=parent_id,
                attributes=dict(attributes or {}),
                tags=self._event_tags(tags),
            )
        )

    def cost(
        self,
        *,
        session_id: str,
        cost: CostBreakdown,
        turn_id: Optional[str] = None,
        pricebook_version: Optional[str] = None,
        attribution: Optional[Dict[str, float]] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            CostEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                cost=cost,
                turn_id=turn_id,
                pricebook_version=pricebook_version,
                attribution=dict(attribution or {}),
                tags=self._event_tags(tags),
            )
        )

    def business(
        self,
        *,
        session_id: str,
        funnel_stage: str,
        funnel_confidence: float,
        sentiment_label: str,
        sentiment_confidence: float,
        turn_id: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            BusinessEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                funnel_stage=funnel_stage,
                funnel_confidence=funnel_confidence,
                sentiment_label=sentiment_label,
                sentiment_confidence=sentiment_confidence,
                turn_id=turn_id,
                tags=self._event_tags(tags),
            )
        )

    def tool_call(
        self,
        *,
        session_id: str,
        turn_id: str,
        server: str,
        tool: str,
        allowed: bool,
        latency_ms: float,
        arguments: Optional[Dict[str, object]] = None,
        error: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            ToolCallEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                turn_id=turn_id,
                server=server,
                tool=tool,
                allowed=allowed,
                latency_ms=latency_ms,
                arguments=dict(arguments or {}),
                error=error,
                tags=self._event_tags(tags),
            )
        )

    def transcript(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        text: str,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            TranscriptEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                turn_id=turn_id,
                role=role,  # type: ignore[arg-type]
                text=text,
                tags=self._event_tags(tags),
            )
        )

    def audio_ref(
        self,
        *,
        session_id: str,
        blob_id: str,
        turn_id: Optional[str] = None,
        upload_url_requested: bool = False,
        recording_id: Optional[str] = None,
        leg: Optional[str] = None,
        duration_ms: Optional[int] = None,
        byte_count: Optional[int] = None,
        sha256: Optional[str] = None,
        container: Optional[str] = None,
        consent_ref: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        emitted_at_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> None:
        self._enqueue(
            AudioRefEvent(
                event_id=self._id(event_id),
                session_id=session_id,
                emitted_at_ms=self._now(emitted_at_ms),
                blob_id=blob_id,
                turn_id=turn_id,
                upload_url_requested=upload_url_requested,
                recording_id=recording_id,
                leg=leg,  # type: ignore[arg-type]
                duration_ms=duration_ms,
                byte_count=byte_count,
                sha256=sha256,
                container=container,
                consent_ref=consent_ref,
                tags=self._event_tags(tags),
            )
        )

    # -- flush --------------------------------------------------------------

    def flush(self) -> None:
        if not self._queue:
            return
        batch = list(self._queue)
        self._queue.clear()
        for exporter in self._exporters:
            try:
                exporter.export_batch(batch)
            except Exception:
                # Fail-open: telemetry must never crash a call (wire spec).
                self.dropped_events += len(batch)

    def close(self) -> None:
        self.flush()


# -- configuration ----------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_tags(raw: Optional[str]) -> Dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    tags: Dict[str, str] = {}
    for item in raw.split(TAG_PAIR_SEPARATOR):
        if not item.strip() or TAG_KEY_VALUE_SEPARATOR not in item:
            continue
        key, value = item.split(TAG_KEY_VALUE_SEPARATOR, 1)
        key = key.strip()
        if not key:
            continue
        tags[key] = value.strip()
    return tags


def _discover_exporters() -> List[TraceExporter]:
    """Load exporters registered under the ``lucy.exporters`` entry-point group.

    Each entry point resolves to a zero-arg factory returning a
    ``TraceExporter`` or ``None`` (when it should not activate, e.g. the cloud
    exporter with no ``LUCY_API_KEY``). Discovery is fail-open.
    """
    discovered: List[TraceExporter] = []
    try:
        from importlib.metadata import entry_points

        points = entry_points(group=EXPORTER_ENTRY_POINT_GROUP)
    except Exception:
        return discovered
    for point in points:
        try:
            factory = point.load()
            exporter = factory()
        except Exception:
            continue
        if exporter is not None:
            discovered.append(exporter)
    return discovered


def _default_exporters() -> List[TraceExporter]:
    exporters: List[TraceExporter] = [ConsoleExporter()]
    trace_file = os.environ.get("LUCY_TRACE_FILE")
    if trace_file:
        exporters.append(JsonlFileExporter(Path(trace_file)))
    exporters.extend(_discover_exporters())
    return exporters


def configure(
    *,
    exporters: Optional[Sequence[TraceExporter]] = None,
    sample_rate: Optional[float] = None,
    redact_pii: Optional[bool] = None,
    record_audio: Optional[bool] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Tracer:
    """Build a tracer. Explicit arguments win; otherwise environment variables.

    With no arguments and no env, returns a console-exporting tracer. Setting
    ``LUCY_TRACE_FILE`` adds the JSONL exporter whose lines match the wire spec.
    ``LUCY_TRACING=0`` yields a disabled (no-op) tracer.
    """
    enabled = _env_bool("LUCY_TRACING", default=True)
    if exporters is not None:
        chosen = list(exporters)
    elif enabled:
        chosen = _default_exporters()
    else:
        # A disabled tracer never exports, so skip exporter construction and
        # entry-point discovery entirely: building the global tracer with
        # LUCY_TRACING=0 stays cheap (zero-overhead-when-off, card 25 review).
        chosen = []
    rate = (
        sample_rate if sample_rate is not None else _env_float("LUCY_TRACE_SAMPLE", 1.0)
    )
    redaction = True if redact_pii is None else redact_pii
    configured_tags = _parse_tags(os.environ.get(TAGS_ENV_VAR))
    configured_tags.update(tags or {})
    return Tracer(
        exporters=chosen,
        sample_rate=rate,
        redact_pii=redaction,
        record_audio=False if record_audio is None else record_audio,
        enabled=enabled,
        tags=configured_tags,
    )


# -- process-global tracer --------------------------------------------------
#
# Runtime components (GraphExecutor, VoiceSession, McpClient, the cost
# helper) accept an explicit tracer but fall back to this shared instance when
# none is injected, so a plain `pip install` quickstart emits telemetry with no
# wiring. It is built lazily on first use to avoid import-time side effects
# (exporter discovery), and is overridable for tests and for the future
# VoiceAgent facade (card 26) that owns session lifecycle.

_GLOBAL_TRACER: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Return the process-global tracer, building it from the environment on
    first access (see :func:`configure`)."""
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        _GLOBAL_TRACER = configure()
    return _GLOBAL_TRACER


def set_tracer(tracer: Optional[Tracer]) -> None:
    """Install (or, with ``None``, reset) the process-global tracer."""
    global _GLOBAL_TRACER
    _GLOBAL_TRACER = tracer


_MOVED_TO_TESTING = ("InMemoryOtelSpanExporter",)


def __getattr__(name: str) -> object:
    """Deprecation shim: the in-memory OTel exporter moved to lucy.testing (card 22)."""
    if name in _MOVED_TO_TESTING:
        import warnings

        from lucy import testing

        warnings.warn(
            "lucy.observe.%s moved to lucy.testing; import it from lucy.testing" % name,
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(testing, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
