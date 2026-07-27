# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
"""Portable in-process analytics derived from telemetry wire v1.

Scope cap (ADR 0010): no storage, no cross-run comparisons, no cross-tenant aggregation.

Nearest-rank percentiles use rank = ceil(p * n) on the sorted sample (1-based).
Empty samples yield 0.0; a single sample is returned for p50, p95, and p99.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field, field_validator

from lucy.limits import MAX_CONTROL_DURATION_MS
from lucy.metrics import CostComponent, LatencyWaterfall
from lucy.observe.events import (
    BusinessEvent,
    CostEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    TelemetryEventBase,
    ToolCallEvent,
    TurnEvent,
)
from lucy.specs import FunnelStage, SentimentLabel

ANALYTICS_MODEL_V1 = "analytics-model/v1"

COST_COMPONENT_KEYS = (
    "stt_cost",
    "llm_cost",
    "tts_cost",
    "telephony_cost",
    "rag_cost",
    "mcp_tool_cost",
    "infra_cost",
)
LATENCY_SEGMENT_KEYS = (
    "stt_ms",
    "rag_ms",
    "llm_ms",
    "mcp_tools_ms",
    "tts_ms",
    "transport_ms",
)
MEASURE_KEYS = (
    *COST_COMPONENT_KEYS,
    "total_cost",
    "billable_audio_minutes",
    *LATENCY_SEGMENT_KEYS,
    "total_ms",
    "caller_talk_ms",
    "agent_talk_ms",
    "turn_count",
    "session_count",
    "tool_call_count",
    "tool_error_count",
    "barge_in_count",
    "deadline_miss_count",
)
DERIVED_METRIC_KEYS = (
    "cost_per_minute",
    "cost_per_booked_outcome",
    "conversion_rate",
    "deadline_miss_rate",
    "barge_in_rate",
    "tool_success_rate",
    "talk_ratio",
)
FUNNEL_STAGES = frozenset(stage.value for stage in FunnelStage)
SENTIMENT_LABELS = frozenset(label.value for label in SentimentLabel)
PROVIDER_COST_COMPONENTS = {
    "provider_stt": CostComponent.STT_COST,
    "provider_llm": CostComponent.LLM_COST,
    "provider_tts": CostComponent.TTS_COST,
}
_RELEVANT_EVENT_TYPES = frozenset(
    {
        "session.started",
        "session.ended",
        "turn",
        "cost",
        "business",
        "tool_call",
    }
)


class TalkMeasures(BaseModel):
    """Additive measured speech durations for a telemetry rollup."""

    caller_talk_ms: int = Field(default=0, ge=0)
    agent_talk_ms: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    def talk_ratio(self) -> Optional[float]:
        total = self.agent_talk_ms + self.caller_talk_ms
        if total == 0:
            return None
        return self.agent_talk_ms / total


def aggregate_talk_measures(turns: Iterable[TurnEvent]) -> TalkMeasures:
    """Sum only measured turn counters; never infer speech from other fields."""

    caller_talk_ms = 0
    agent_talk_ms = 0
    for turn in turns:
        caller_talk_ms += turn.caller_talk_ms
        agent_talk_ms += turn.agent_talk_ms
    return TalkMeasures(
        caller_talk_ms=caller_talk_ms,
        agent_talk_ms=agent_talk_ms,
    )


class TurnFact(BaseModel):
    """One analytics row per turn event, keyed by ``turn_id``."""

    turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    emitted_at_ms: int = Field(ge=0)
    interrupted: bool = False
    timeout_events: list[str] = Field(default_factory=list)
    latency_waterfall: LatencyWaterfall
    caller_talk_ms: int = Field(ge=0, le=MAX_CONTROL_DURATION_MS)
    agent_talk_ms: int = Field(ge=0, le=MAX_CONTROL_DURATION_MS)
    project: Optional[str] = None
    deployment: Optional[str] = None


class SessionFact(BaseModel):
    """One analytics row per voice session, keyed by ``session_id``."""

    session_id: str = Field(min_length=1)
    agent: Optional[str] = None
    deployment: Optional[str] = None
    project: Optional[str] = None
    tenant: None = None
    started_at_ms: Optional[int] = None
    ended_at_ms: Optional[int] = None
    funnel_stage: Optional[str] = None
    sentiment_label: Optional[str] = None
    provider_stt: Optional[str] = None
    provider_llm: Optional[str] = None
    provider_tts: Optional[str] = None
    stt_cost: float = 0.0
    llm_cost: float = 0.0
    tts_cost: float = 0.0
    telephony_cost: float = 0.0
    rag_cost: float = 0.0
    mcp_tool_cost: float = 0.0
    infra_cost: float = 0.0
    billable_audio_minutes: float = 0.0
    time: Optional[str] = None

    @field_validator("funnel_stage")
    @classmethod
    def validate_funnel_stage(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in FUNNEL_STAGES:
            raise ValueError(f"unsupported funnel_stage: {value}")
        return value

    @field_validator("sentiment_label")
    @classmethod
    def validate_sentiment_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in SENTIMENT_LABELS:
            raise ValueError(f"unsupported sentiment_label: {value}")
        return value

    @property
    def total_cost(self) -> float:
        return sum(getattr(self, key) for key in COST_COMPONENT_KEYS)


class ToolCallFact(BaseModel):
    """One analytics row per tool_call event."""

    turn_id: str = Field(min_length=1)
    server: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    emitted_at_ms: int = Field(ge=0)
    session_id: str = Field(min_length=1)
    allowed: bool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error: Optional[str] = None
    project: Optional[str] = None
    deployment: Optional[str] = None

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.turn_id, self.server, self.tool, self.emitted_at_ms)


class FactBuildResult(BaseModel):
    """Typed fact grains plus a counter for dropped malformed/unrelated input."""

    turn_facts: list[TurnFact] = Field(default_factory=list)
    session_facts: list[SessionFact] = Field(default_factory=list)
    tool_call_facts: list[ToolCallFact] = Field(default_factory=list)
    dropped_events: int = 0


def _tag_value(tags: Mapping[str, str], key: str) -> Optional[str]:
    value = tags.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _utc_time(emitted_at_ms: int) -> str:
    instant = datetime.fromtimestamp(emitted_at_ms / 1000.0, tz=timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def _provider_name(
    attribution: Mapping[CostComponent, Any],
    component: CostComponent,
) -> Optional[str]:
    identity = attribution.get(component)
    if identity is None:
        return None
    provider = getattr(identity, "provider", None)
    if not isinstance(provider, str) or not provider:
        return None
    return provider


def nearest_rank_percentile(samples: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank percentile for ``percentile`` in ``(0, 1]``."""

    if not samples:
        return 0.0
    ordered = sorted(float(value) for value in samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = int(math.ceil(percentile * len(ordered)))
    rank = min(max(rank, 1), len(ordered))
    return ordered[rank - 1]


def nearest_rank_percentiles(samples: Sequence[float]) -> dict[str, float]:
    """Return p50/p95/p99 using nearest-rank on the sorted sample."""

    return {
        "p50": nearest_rank_percentile(samples, 0.50),
        "p95": nearest_rank_percentile(samples, 0.95),
        "p99": nearest_rank_percentile(samples, 0.99),
    }


def derived_metrics(
    *,
    total_cost: float,
    billable_audio_minutes: float,
    booked_outcome_count: int,
    session_count: int,
    turn_count: int,
    barge_in_count: int,
    deadline_miss_count: int,
    tool_call_count: int,
    tool_error_count: int,
    caller_talk_ms: int,
    agent_talk_ms: int,
) -> dict[str, Optional[float]]:
    """Compute every analytics-model/v1 derived metric; null on zero denominators."""

    talk_total = agent_talk_ms + caller_talk_ms
    return {
        "cost_per_minute": (
            None if billable_audio_minutes == 0 else total_cost / billable_audio_minutes
        ),
        "cost_per_booked_outcome": (
            None if booked_outcome_count == 0 else total_cost / booked_outcome_count
        ),
        "conversion_rate": (
            None if session_count == 0 else booked_outcome_count / session_count
        ),
        "deadline_miss_rate": (
            None if turn_count == 0 else deadline_miss_count / turn_count
        ),
        "barge_in_rate": None if turn_count == 0 else barge_in_count / turn_count,
        "tool_success_rate": (
            None if tool_call_count == 0 else 1.0 - (tool_error_count / tool_call_count)
        ),
        "talk_ratio": None if talk_total == 0 else agent_talk_ms / talk_total,
    }


def _empty_measures() -> dict[str, float | int]:
    measures: dict[str, float | int] = {}
    for key in MEASURE_KEYS:
        if key.endswith("_count") or key in {"caller_talk_ms", "agent_talk_ms"}:
            measures[key] = 0
        else:
            measures[key] = 0.0
    return measures


def _round_derived(
    metrics: Mapping[str, Optional[float]],
) -> dict[str, Optional[float]]:
    rounded: dict[str, Optional[float]] = {}
    for key in DERIVED_METRIC_KEYS:
        value = metrics.get(key)
        if value is None:
            rounded[key] = None
        else:
            rounded[key] = round(float(value), 6)
    return rounded


class _SessionScratch:
    __slots__ = (
        "session_id",
        "project",
        "agent",
        "deployment",
        "started_at_ms",
        "ended_at_ms",
        "funnel_stage",
        "sentiment_label",
        "business_emitted_at_ms",
        "provider_stt",
        "provider_llm",
        "provider_tts",
        "provider_emitted",
        "costs",
        "billable_audio_minutes",
        "turn_facts",
        "tool_call_facts",
    )

    def __init__(self, session_id: str, project: Optional[str]) -> None:
        self.session_id = session_id
        self.project = project
        self.agent: Optional[str] = None
        self.deployment: Optional[str] = None
        self.started_at_ms: Optional[int] = None
        self.ended_at_ms: Optional[int] = None
        self.funnel_stage: Optional[str] = None
        self.sentiment_label: Optional[str] = None
        self.business_emitted_at_ms: Optional[int] = None
        self.provider_stt: Optional[str] = None
        self.provider_llm: Optional[str] = None
        self.provider_tts: Optional[str] = None
        self.provider_emitted: dict[str, int] = {
            "provider_stt": -1,
            "provider_llm": -1,
            "provider_tts": -1,
        }
        self.costs = {key: 0.0 for key in COST_COMPONENT_KEYS}
        self.billable_audio_minutes = 0.0
        self.turn_facts: dict[str, TurnFact] = {}
        self.tool_call_facts: dict[tuple[str, str, str, int], ToolCallFact] = {}

    def to_session_fact(self) -> SessionFact:
        time_value = None
        if self.started_at_ms is not None:
            time_value = _utc_time(self.started_at_ms)
        return SessionFact(
            session_id=self.session_id,
            agent=self.agent,
            deployment=self.deployment,
            project=self.project,
            started_at_ms=self.started_at_ms,
            ended_at_ms=self.ended_at_ms,
            funnel_stage=self.funnel_stage,
            sentiment_label=self.sentiment_label,
            provider_stt=self.provider_stt,
            provider_llm=self.provider_llm,
            provider_tts=self.provider_tts,
            stt_cost=self.costs["stt_cost"],
            llm_cost=self.costs["llm_cost"],
            tts_cost=self.costs["tts_cost"],
            telephony_cost=self.costs["telephony_cost"],
            rag_cost=self.costs["rag_cost"],
            mcp_tool_cost=self.costs["mcp_tool_cost"],
            infra_cost=self.costs["infra_cost"],
            billable_audio_minutes=self.billable_audio_minutes,
            time=time_value,
        )


def build_facts(
    events: Iterable[object],
    *,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
    session_ids: Optional[Sequence[str]] = None,
) -> FactBuildResult:
    """Deterministically fold typed telemetry events into analytics fact grains."""

    allowed_sessions: Optional[set[str]] = None
    if session_ids is not None:
        allowed_sessions = set(session_ids)
    elif session_id is not None:
        allowed_sessions = {session_id}

    scratches: dict[str, _SessionScratch] = {}
    dropped_events = 0

    def scratch_for(sid: str) -> _SessionScratch:
        existing = scratches.get(sid)
        if existing is not None:
            return existing
        created = _SessionScratch(sid, project)
        scratches[sid] = created
        return created

    for raw in events:
        if not isinstance(raw, TelemetryEventBase):
            dropped_events += 1
            continue
        event_type = getattr(raw, "type", None)
        if event_type not in _RELEVANT_EVENT_TYPES:
            dropped_events += 1
            continue
        sid = raw.session_id
        if allowed_sessions is not None and sid not in allowed_sessions:
            dropped_events += 1
            continue

        bucket = scratch_for(sid)
        deployment = _tag_value(raw.tags, "deployment")
        event_project = (
            project if project is not None else _tag_value(raw.tags, "project")
        )
        if event_project is not None:
            bucket.project = event_project
        if deployment is not None and bucket.deployment is None:
            bucket.deployment = deployment

        if isinstance(raw, SessionStartedEvent):
            bucket.agent = raw.agent_name
            bucket.started_at_ms = raw.emitted_at_ms
            if deployment is not None:
                bucket.deployment = deployment
            continue

        if isinstance(raw, SessionEndedEvent):
            bucket.ended_at_ms = raw.emitted_at_ms
            continue

        if isinstance(raw, TurnEvent):
            bucket.turn_facts[raw.turn_id] = TurnFact(
                turn_id=raw.turn_id,
                session_id=sid,
                turn_index=raw.turn_index,
                emitted_at_ms=raw.emitted_at_ms,
                interrupted=raw.interrupted,
                timeout_events=list(raw.timeout_events),
                latency_waterfall=raw.latency_waterfall,
                caller_talk_ms=raw.caller_talk_ms,
                agent_talk_ms=raw.agent_talk_ms,
                project=bucket.project,
                deployment=bucket.deployment or deployment,
            )
            continue

        if isinstance(raw, CostEvent):
            for key in COST_COMPONENT_KEYS:
                bucket.costs[key] += float(getattr(raw.cost, key))
            bucket.billable_audio_minutes += float(raw.cost.billable_audio_minutes)
            for dim_name, component in PROVIDER_COST_COMPONENTS.items():
                name = _provider_name(raw.provider_attribution, component)
                if name is None:
                    continue
                previous = bucket.provider_emitted[dim_name]
                if raw.emitted_at_ms >= previous:
                    setattr(bucket, dim_name, name)
                    bucket.provider_emitted[dim_name] = raw.emitted_at_ms
            continue

        if isinstance(raw, BusinessEvent):
            if (
                raw.funnel_stage not in FUNNEL_STAGES
                or raw.sentiment_label not in SENTIMENT_LABELS
            ):
                dropped_events += 1
                continue
            if (
                bucket.business_emitted_at_ms is None
                or raw.emitted_at_ms >= bucket.business_emitted_at_ms
            ):
                bucket.funnel_stage = raw.funnel_stage
                bucket.sentiment_label = raw.sentiment_label
                bucket.business_emitted_at_ms = raw.emitted_at_ms
            continue

        if isinstance(raw, ToolCallEvent):
            fact = ToolCallFact(
                turn_id=raw.turn_id,
                server=raw.server,
                tool=raw.tool,
                emitted_at_ms=raw.emitted_at_ms,
                session_id=sid,
                allowed=raw.allowed,
                latency_ms=raw.latency_ms,
                error=raw.error,
                project=bucket.project,
                deployment=bucket.deployment or deployment,
            )
            bucket.tool_call_facts[fact.key] = fact
            continue

        dropped_events += 1

    turn_facts: list[TurnFact] = []
    tool_call_facts: list[ToolCallFact] = []
    session_facts: list[SessionFact] = []
    for sid in sorted(scratches):
        bucket = scratches[sid]
        session_facts.append(bucket.to_session_fact())
        turn_facts.extend(bucket.turn_facts[key] for key in sorted(bucket.turn_facts))
        tool_call_facts.extend(
            bucket.tool_call_facts[key] for key in sorted(bucket.tool_call_facts)
        )

    return FactBuildResult(
        turn_facts=turn_facts,
        session_facts=session_facts,
        tool_call_facts=tool_call_facts,
        dropped_events=dropped_events,
    )


def _aggregate_measures(
    *,
    session_facts: Sequence[SessionFact],
    turn_facts: Sequence[TurnFact],
    tool_call_facts: Sequence[ToolCallFact],
) -> dict[str, float | int]:
    measures = _empty_measures()
    for session in session_facts:
        for key in COST_COMPONENT_KEYS:
            measures[key] = float(measures[key]) + float(getattr(session, key))
        measures["billable_audio_minutes"] = float(
            measures["billable_audio_minutes"]
        ) + float(session.billable_audio_minutes)
    measures["total_cost"] = sum(float(measures[key]) for key in COST_COMPONENT_KEYS)
    measures["session_count"] = len(session_facts)

    for turn in turn_facts:
        waterfall = turn.latency_waterfall
        for key in LATENCY_SEGMENT_KEYS:
            measures[key] = float(measures[key]) + float(getattr(waterfall, key))
        measures["total_ms"] = float(measures["total_ms"]) + float(waterfall.total_ms)
        measures["caller_talk_ms"] = (
            int(measures["caller_talk_ms"]) + turn.caller_talk_ms
        )
        measures["agent_talk_ms"] = int(measures["agent_talk_ms"]) + turn.agent_talk_ms
        if turn.interrupted:
            measures["barge_in_count"] = int(measures["barge_in_count"]) + 1
        measures["deadline_miss_count"] = int(measures["deadline_miss_count"]) + len(
            turn.timeout_events
        )
    measures["turn_count"] = len(turn_facts)

    measures["tool_call_count"] = len(tool_call_facts)
    measures["tool_error_count"] = sum(
        1 for tool in tool_call_facts if tool.error is not None
    )
    return measures


def _latency_percentile_block(
    turn_facts: Sequence[TurnFact],
) -> dict[str, dict[str, float]]:
    samples: dict[str, list[float]] = {key: [] for key in LATENCY_SEGMENT_KEYS}
    samples["total_ms"] = []
    for turn in turn_facts:
        waterfall = turn.latency_waterfall
        for key in LATENCY_SEGMENT_KEYS:
            samples[key].append(float(getattr(waterfall, key)))
        samples["total_ms"].append(float(waterfall.total_ms))
    return {key: nearest_rank_percentiles(values) for key, values in samples.items()}


def _booked_count(session_facts: Sequence[SessionFact]) -> int:
    return sum(1 for session in session_facts if session.funnel_stage == "booked")


def _dimension_map(session_facts: Sequence[SessionFact]) -> dict[str, Optional[str]]:
    if not session_facts:
        return {
            "agent": None,
            "deployment": None,
            "provider_stt": None,
            "provider_llm": None,
            "provider_tts": None,
            "funnel_stage": None,
            "sentiment_label": None,
            "time": None,
        }
    primary = session_facts[0]
    return {
        "agent": primary.agent,
        "deployment": primary.deployment,
        "provider_stt": primary.provider_stt,
        "provider_llm": primary.provider_llm,
        "provider_tts": primary.provider_tts,
        "funnel_stage": primary.funnel_stage,
        "sentiment_label": primary.sentiment_label,
        "time": primary.time,
    }


class SessionRollup(BaseModel):
    """analytics-model/v1 snapshot for one in-process session."""

    schema_version: str = ANALYTICS_MODEL_V1
    session_id: str
    project: Optional[str] = None
    tenant: None = None
    agent: Optional[str] = None
    deployment: Optional[str] = None
    provider_stt: Optional[str] = None
    provider_llm: Optional[str] = None
    provider_tts: Optional[str] = None
    funnel_stage: Optional[str] = None
    sentiment_label: Optional[str] = None
    started_at_ms: Optional[int] = None
    ended_at_ms: Optional[int] = None
    measures: dict[str, float | int] = Field(default_factory=_empty_measures)
    latency_percentiles: dict[str, dict[str, float]] = Field(default_factory=dict)
    derived_metrics: dict[str, Optional[float]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "project": self.project,
            "tenant": self.tenant,
            "agent": self.agent,
            "deployment": self.deployment,
            "provider_stt": self.provider_stt,
            "provider_llm": self.provider_llm,
            "provider_tts": self.provider_tts,
            "funnel_stage": self.funnel_stage,
            "sentiment_label": self.sentiment_label,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "measures": dict(self.measures),
            "latency_percentiles": {
                key: dict(values) for key, values in self.latency_percentiles.items()
            },
            "derived_metrics": dict(self.derived_metrics),
        }


class RunRollup(BaseModel):
    """analytics-model/v1 snapshot for an explicit in-process session set."""

    schema_version: str = ANALYTICS_MODEL_V1
    run_id: str
    project: Optional[str] = None
    tenant: None = None
    window_start_ms: int = 0
    window_end_ms: int = 0
    group_by: list[str] = Field(default_factory=list)
    dimensions: dict[str, Optional[str]] = Field(default_factory=dict)
    measures: dict[str, float | int] = Field(default_factory=_empty_measures)
    latency_percentiles: dict[str, dict[str, float]] = Field(default_factory=dict)
    derived_metrics: dict[str, Optional[float]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "project": self.project,
            "tenant": self.tenant,
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "group_by": list(self.group_by),
            "dimensions": dict(self.dimensions),
            "measures": dict(self.measures),
            "latency_percentiles": {
                key: dict(values) for key, values in self.latency_percentiles.items()
            },
            "derived_metrics": dict(self.derived_metrics),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunRollup":
        return cls.model_validate(dict(payload))


def session_rollup(
    events: Iterable[object],
    *,
    session_id: str,
    project: Optional[str] = None,
) -> SessionRollup:
    """Build a ``SessionRollup`` for one session from in-process telemetry events."""

    facts = build_facts(events, session_id=session_id, project=project)
    session = (
        facts.session_facts[0]
        if facts.session_facts
        else SessionFact(session_id=session_id, project=project)
    )
    measures = _aggregate_measures(
        session_facts=facts.session_facts,
        turn_facts=facts.turn_facts,
        tool_call_facts=facts.tool_call_facts,
    )
    if not facts.session_facts:
        measures["session_count"] = 0
    percentiles = _latency_percentile_block(facts.turn_facts)
    metrics = _round_derived(
        derived_metrics(
            total_cost=float(measures["total_cost"]),
            billable_audio_minutes=float(measures["billable_audio_minutes"]),
            booked_outcome_count=_booked_count(facts.session_facts),
            session_count=int(measures["session_count"]),
            turn_count=int(measures["turn_count"]),
            barge_in_count=int(measures["barge_in_count"]),
            deadline_miss_count=int(measures["deadline_miss_count"]),
            tool_call_count=int(measures["tool_call_count"]),
            tool_error_count=int(measures["tool_error_count"]),
            caller_talk_ms=int(measures["caller_talk_ms"]),
            agent_talk_ms=int(measures["agent_talk_ms"]),
        )
    )
    return SessionRollup(
        session_id=session_id,
        project=session.project if session.project is not None else project,
        agent=session.agent,
        deployment=session.deployment,
        provider_stt=session.provider_stt,
        provider_llm=session.provider_llm,
        provider_tts=session.provider_tts,
        funnel_stage=session.funnel_stage,
        sentiment_label=session.sentiment_label,
        started_at_ms=session.started_at_ms,
        ended_at_ms=session.ended_at_ms,
        measures=measures,
        latency_percentiles=percentiles,
        derived_metrics=metrics,
    )


def run_rollup(
    events: Iterable[object],
    *,
    run_id: str,
    project: Optional[str] = None,
    window_start_ms: int,
    window_end_ms: int,
    group_by: Sequence[str] | None = None,
    session_ids: Sequence[str] | None = None,
) -> RunRollup:
    """Aggregate explicitly supplied in-process sessions into a run snapshot."""

    facts = build_facts(events, project=project, session_ids=session_ids)
    measures = _aggregate_measures(
        session_facts=facts.session_facts,
        turn_facts=facts.turn_facts,
        tool_call_facts=facts.tool_call_facts,
    )
    percentiles = _latency_percentile_block(facts.turn_facts)
    metrics = _round_derived(
        derived_metrics(
            total_cost=float(measures["total_cost"]),
            billable_audio_minutes=float(measures["billable_audio_minutes"]),
            booked_outcome_count=_booked_count(facts.session_facts),
            session_count=int(measures["session_count"]),
            turn_count=int(measures["turn_count"]),
            barge_in_count=int(measures["barge_in_count"]),
            deadline_miss_count=int(measures["deadline_miss_count"]),
            tool_call_count=int(measures["tool_call_count"]),
            tool_error_count=int(measures["tool_error_count"]),
            caller_talk_ms=int(measures["caller_talk_ms"]),
            agent_talk_ms=int(measures["agent_talk_ms"]),
        )
    )
    return RunRollup(
        run_id=run_id,
        project=project,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        group_by=list(group_by or []),
        dimensions=_dimension_map(facts.session_facts),
        measures=measures,
        latency_percentiles=percentiles,
        derived_metrics=metrics,
    )
