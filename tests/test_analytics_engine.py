# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
"""Behavioral tests for the in-process analytics-model/v1 engine (card 103)."""

from __future__ import annotations

import ast
import inspect
import json
import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lucy.analytics import (
    ANALYTICS_MODEL_V1,
    RunRollup,
    SessionRollup,
    TalkMeasures,
    ToolCallFact,
    TurnFact,
    aggregate_talk_measures,
    build_facts,
    derived_metrics,
    nearest_rank_percentiles,
    session_rollup,
    run_rollup,
)
from lucy.metrics import CostBreakdown, CostComponent, LatencyWaterfall
from lucy.observe.events import (
    BusinessEvent,
    CostEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SpanEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnEvent,
)
from lucy.providers import ProviderIdentity

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_SRC = ROOT / "src/lucy/analytics.py"
MODEL_DOC = ROOT / "docs/analytics-model-v1.md"

WINDOW_START_MS = 1_772_700_000_000
WINDOW_END_MS = 1_772_700_060_000
TIME_MS = 1_772_704_800_000  # 2026-03-05T10:00:00Z


def _eid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _tags(
    *, project: str = "proj_fixture", deployment: str = "fixture"
) -> dict[str, str]:
    return {"project": project, "deployment": deployment}


def _session_started(
    *,
    session_id: str = "sess_fixture",
    emitted_at_ms: int = TIME_MS,
    agent_name: str = "sales_booking_agent",
    tags: dict[str, str] | None = None,
) -> SessionStartedEvent:
    return SessionStartedEvent(
        event_id=_eid(1),
        session_id=session_id,
        emitted_at_ms=emitted_at_ms,
        agent_name=agent_name,
        spec_hash="spec",
        environment="test",
        transport="fixture",
        tags=tags if tags is not None else _tags(),
    )


def _session_ended(
    *,
    session_id: str = "sess_fixture",
    emitted_at_ms: int = TIME_MS + 60_000,
    billable_audio_minutes: float = 1.5,
) -> SessionEndedEvent:
    return SessionEndedEvent(
        event_id=_eid(2),
        session_id=session_id,
        emitted_at_ms=emitted_at_ms,
        reason="completed",
        duration_ms=60_000,
        billable_audio_minutes=billable_audio_minutes,
        tags=_tags(),
    )


def _turn(
    n: int,
    *,
    session_id: str = "sess_fixture",
    turn_id: str | None = None,
    turn_index: int | None = None,
    emitted_at_ms: int | None = None,
    waterfall: LatencyWaterfall | None = None,
    interrupted: bool = False,
    timeout_events: list[str] | None = None,
    caller_talk_ms: int = 0,
    agent_talk_ms: int = 0,
) -> TurnEvent:
    return TurnEvent(
        event_id=_eid(10 + n),
        session_id=session_id,
        emitted_at_ms=TIME_MS
        + (emitted_at_ms if emitted_at_ms is not None else n * 1000),
        turn_id=turn_id or f"turn-{n}",
        turn_index=n if turn_index is None else turn_index,
        latency_waterfall=waterfall or LatencyWaterfall(),
        interrupted=interrupted,
        timeout_events=list(timeout_events or []),
        caller_talk_ms=caller_talk_ms,
        agent_talk_ms=agent_talk_ms,
        tags=_tags(),
    )


def _cost(
    *,
    session_id: str = "sess_fixture",
    emitted_at_ms: int = TIME_MS + 500,
    cost: CostBreakdown | None = None,
    provider_attribution: dict[CostComponent, ProviderIdentity] | None = None,
    event_n: int = 30,
) -> CostEvent:
    attribution = (
        {
            CostComponent.STT_COST: ProviderIdentity(provider="fixture-stt"),
            CostComponent.LLM_COST: ProviderIdentity(provider="fixture-llm"),
            CostComponent.TTS_COST: ProviderIdentity(provider="fixture-tts"),
        }
        if provider_attribution is None
        else provider_attribution
    )
    return CostEvent(
        event_id=_eid(event_n),
        session_id=session_id,
        emitted_at_ms=emitted_at_ms,
        cost=cost
        or CostBreakdown(
            stt_cost=0.01,
            llm_cost=0.04,
            tts_cost=0.02,
            telephony_cost=0.015,
            rag_cost=0.003,
            mcp_tool_cost=0.002,
            infra_cost=0.005,
            billable_audio_minutes=1.5,
        ),
        provider_attribution=attribution,
        tags=_tags(),
    )


def _business(
    *,
    session_id: str = "sess_fixture",
    emitted_at_ms: int = TIME_MS + 900,
    funnel_stage: str = "booked",
    sentiment_label: str = "neutral",
    event_n: int = 40,
) -> BusinessEvent:
    return BusinessEvent(
        event_id=_eid(event_n),
        session_id=session_id,
        emitted_at_ms=emitted_at_ms,
        funnel_stage=funnel_stage,
        funnel_confidence=0.9,
        sentiment_label=sentiment_label,
        sentiment_confidence=0.8,
        tags=_tags(),
    )


def _tool(
    *,
    session_id: str = "sess_fixture",
    turn_id: str = "turn-3",
    server: str = "crm",
    tool: str = "book",
    emitted_at_ms: int = TIME_MS + 3000,
    error: str | None = None,
) -> ToolCallEvent:
    return ToolCallEvent(
        event_id=_eid(50),
        session_id=session_id,
        emitted_at_ms=emitted_at_ms,
        turn_id=turn_id,
        server=server,
        tool=tool,
        allowed=True,
        latency_ms=35.0,
        error=error,
        tags=_tags(),
    )


def _worked_example_turns() -> list[TurnEvent]:
    # Totals [172, 207, 352]; component percentiles match the normative example.
    return [
        _turn(
            1,
            waterfall=LatencyWaterfall(
                stt_ms=120.0,
                rag_ms=24.0,
                llm_ms=0.0,
                mcp_tools_ms=0.0,
                tts_ms=0.0,
                transport_ms=28.0,
            ),
            caller_talk_ms=280,
            agent_talk_ms=400,
        ),
        _turn(
            2,
            waterfall=LatencyWaterfall(
                stt_ms=120.0,
                rag_ms=24.0,
                llm_ms=0.0,
                mcp_tools_ms=35.0,
                tts_ms=0.0,
                transport_ms=28.0,
            ),
            caller_talk_ms=280,
            agent_talk_ms=380,
        ),
        _turn(
            3,
            waterfall=LatencyWaterfall(
                stt_ms=0.0,
                rag_ms=24.0,
                llm_ms=205.0,
                mcp_tools_ms=0.0,
                tts_ms=95.0,
                transport_ms=28.0,
            ),
            caller_talk_ms=280,
            agent_talk_ms=380,
        ),
    ]


def _worked_example_events() -> list[object]:
    return [
        _session_started(),
        *_worked_example_turns(),
        _tool(),
        _cost(),
        _business(),
        _session_ended(),
    ]


# --- C1: typed facts and deterministic event parsing ---------------------------------


def test_build_facts_emits_typed_turn_session_tool_facts_and_counts_drops():
    events = [
        _session_started(),
        _turn(1, caller_talk_ms=10, agent_talk_ms=20),
        _tool(turn_id="turn-1", emitted_at_ms=TIME_MS + 1500),
        _cost(
            cost=CostBreakdown(
                stt_cost=0.01, llm_cost=0.02, billable_audio_minutes=1.0
            ),
            provider_attribution={},
            event_n=31,
        ),
        _cost(
            emitted_at_ms=TIME_MS + 600,
            cost=CostBreakdown(tts_cost=0.03, billable_audio_minutes=0.5),
            provider_attribution={
                CostComponent.TTS_COST: ProviderIdentity(provider="fixture-tts"),
            },
            event_n=32,
        ),
        _business(
            emitted_at_ms=TIME_MS + 100,
            funnel_stage="qualified",
            event_n=41,
        ),
        _business(
            emitted_at_ms=TIME_MS + 200,
            funnel_stage="booked",
            event_n=42,
        ),
        _session_ended(billable_audio_minutes=1.5),
        SpanEvent(
            event_id=_eid(90),
            session_id="sess_fixture",
            emitted_at_ms=TIME_MS,
            span_id="span-1",
            name="ignored",
            status="ok",
            started_at_ms=TIME_MS,
            ended_at_ms=TIME_MS + 1,
        ),
        TranscriptEvent(
            event_id=_eid(91),
            session_id="sess_fixture",
            emitted_at_ms=TIME_MS,
            turn_id="turn-1",
            role="caller",
            text="hi",
        ),
        "not-an-event",
        {"type": "turn"},
        _turn(2, session_id="other-session"),
    ]

    result = build_facts(
        events,
        session_id="sess_fixture",
        project="proj_fixture",
    )

    assert len(result.turn_facts) == 1
    turn = result.turn_facts[0]
    assert isinstance(turn, TurnFact)
    assert turn.turn_id == "turn-1"
    assert turn.session_id == "sess_fixture"
    assert turn.caller_talk_ms == 10
    assert turn.agent_talk_ms == 20
    assert turn.project == "proj_fixture"
    assert turn.deployment == "fixture"

    assert len(result.session_facts) == 1
    session = result.session_facts[0]
    assert session.session_id == "sess_fixture"
    assert session.agent == "sales_booking_agent"
    assert session.funnel_stage == "booked"
    assert session.sentiment_label == "neutral"
    assert session.stt_cost == pytest.approx(0.01)
    assert session.llm_cost == pytest.approx(0.02)
    assert session.tts_cost == pytest.approx(0.03)
    assert session.billable_audio_minutes == pytest.approx(1.5)
    assert session.provider_stt is None
    assert session.provider_tts == "fixture-tts"

    assert len(result.tool_call_facts) == 1
    tool = result.tool_call_facts[0]
    assert isinstance(tool, ToolCallFact)
    assert tool.key == ("turn-1", "crm", "book", TIME_MS + 1500)
    assert tool.error is None

    # span + transcript + str + dict + other-session turn
    assert result.dropped_events == 5


def test_fact_models_reject_invalid_typed_values():
    with pytest.raises(ValidationError):
        TurnFact(
            turn_id="t",
            session_id="s",
            turn_index=0,
            emitted_at_ms=0,
            interrupted=False,
            timeout_events=[],
            latency_waterfall=LatencyWaterfall(),
            caller_talk_ms=-1,
            agent_talk_ms=0,
        )


# --- C2: dimensions and additive measures --------------------------------------------


def test_session_rollup_populates_every_v1_dimension_and_measure():
    rollup = session_rollup(
        _worked_example_events(),
        session_id="sess_fixture",
        project="proj_fixture",
    )

    assert isinstance(rollup, SessionRollup)
    assert rollup.schema_version == ANALYTICS_MODEL_V1
    assert rollup.session_id == "sess_fixture"
    assert rollup.project == "proj_fixture"
    assert rollup.tenant is None
    assert rollup.agent == "sales_booking_agent"
    assert rollup.deployment == "fixture"
    assert rollup.provider_stt == "fixture-stt"
    assert rollup.provider_llm == "fixture-llm"
    assert rollup.provider_tts == "fixture-tts"
    assert rollup.funnel_stage == "booked"
    assert rollup.sentiment_label == "neutral"
    assert rollup.started_at_ms == TIME_MS
    assert rollup.ended_at_ms == TIME_MS + 60_000

    measures = rollup.measures
    assert measures["stt_cost"] == pytest.approx(0.01)
    assert measures["llm_cost"] == pytest.approx(0.04)
    assert measures["tts_cost"] == pytest.approx(0.02)
    assert measures["telephony_cost"] == pytest.approx(0.015)
    assert measures["rag_cost"] == pytest.approx(0.003)
    assert measures["mcp_tool_cost"] == pytest.approx(0.002)
    assert measures["infra_cost"] == pytest.approx(0.005)
    assert measures["total_cost"] == pytest.approx(0.095)
    assert measures["billable_audio_minutes"] == pytest.approx(1.5)
    assert measures["stt_ms"] == pytest.approx(240.0)
    assert measures["rag_ms"] == pytest.approx(72.0)
    assert measures["llm_ms"] == pytest.approx(205.0)
    assert measures["mcp_tools_ms"] == pytest.approx(35.0)
    assert measures["tts_ms"] == pytest.approx(95.0)
    assert measures["transport_ms"] == pytest.approx(84.0)
    assert measures["total_ms"] == pytest.approx(731.0)
    assert measures["caller_talk_ms"] == 840
    assert measures["agent_talk_ms"] == 1160
    assert measures["turn_count"] == 3
    assert measures["session_count"] == 1
    assert measures["tool_call_count"] == 1
    assert measures["tool_error_count"] == 0
    assert measures["barge_in_count"] == 0
    assert measures["deadline_miss_count"] == 0

    empty = session_rollup([], session_id="missing", project="proj")
    assert empty.measures["turn_count"] == 0
    assert empty.measures["total_cost"] == 0.0
    assert empty.provider_stt is None
    assert empty.tenant is None


def test_session_rollup_counts_barge_ins_and_deadline_misses():
    events = [
        _session_started(),
        _turn(1, interrupted=True, timeout_events=["stt", "llm"]),
        _turn(2, interrupted=False, timeout_events=["tts"]),
        _tool(error="boom"),
        _session_ended(),
    ]
    measures = session_rollup(
        events, session_id="sess_fixture", project="proj_fixture"
    ).measures
    assert measures["barge_in_count"] == 1
    assert measures["deadline_miss_count"] == 3
    assert measures["tool_error_count"] == 1
    assert measures["tool_call_count"] == 1


# --- C3: percentiles and derived metrics ---------------------------------------------


def test_percentiles_and_derived_metrics_match_analytics_model_v1():
    # Nearest-rank: rank = ceil(p * n); empty -> 0.0; single -> that value.
    assert nearest_rank_percentiles([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    assert nearest_rank_percentiles([42.0]) == {
        "p50": 42.0,
        "p95": 42.0,
        "p99": 42.0,
    }
    # n=3 sorted [10, 20, 30]: p50 -> rank 2 = 20; p95/p99 -> rank 3 = 30
    assert nearest_rank_percentiles([30.0, 10.0, 20.0]) == {
        "p50": 20.0,
        "p95": 30.0,
        "p99": 30.0,
    }

    rollup = session_rollup(
        _worked_example_events(),
        session_id="sess_fixture",
        project="proj_fixture",
    )
    assert rollup.latency_percentiles == {
        "stt_ms": {"p50": 120.0, "p95": 120.0, "p99": 120.0},
        "rag_ms": {"p50": 24.0, "p95": 24.0, "p99": 24.0},
        "llm_ms": {"p50": 0.0, "p95": 205.0, "p99": 205.0},
        "mcp_tools_ms": {"p50": 0.0, "p95": 35.0, "p99": 35.0},
        "tts_ms": {"p50": 0.0, "p95": 95.0, "p99": 95.0},
        "transport_ms": {"p50": 28.0, "p95": 28.0, "p99": 28.0},
        "total_ms": {"p50": 207.0, "p95": 352.0, "p99": 352.0},
    }

    derived = rollup.derived_metrics
    assert derived["cost_per_minute"] == pytest.approx(0.063333, abs=1e-6)
    assert derived["cost_per_booked_outcome"] == pytest.approx(0.095)
    assert derived["conversion_rate"] == pytest.approx(1.0)
    assert derived["deadline_miss_rate"] == pytest.approx(0.0)
    assert derived["barge_in_rate"] == pytest.approx(0.0)
    assert derived["tool_success_rate"] == pytest.approx(1.0)
    assert derived["talk_ratio"] == pytest.approx(0.58)


def test_derived_metrics_are_none_for_every_zero_denominator():
    metrics = derived_metrics(
        total_cost=1.0,
        billable_audio_minutes=0.0,
        booked_outcome_count=0,
        session_count=0,
        turn_count=0,
        barge_in_count=0,
        deadline_miss_count=0,
        tool_call_count=0,
        tool_error_count=0,
        caller_talk_ms=0,
        agent_talk_ms=0,
    )
    assert metrics["cost_per_minute"] is None
    assert metrics["cost_per_booked_outcome"] is None
    assert metrics["conversion_rate"] is None
    assert metrics["deadline_miss_rate"] is None
    assert metrics["barge_in_rate"] is None
    assert metrics["tool_success_rate"] is None
    assert metrics["talk_ratio"] is None


# --- C4: SessionRollup / RunRollup serialization and scope guards --------------------


def _worked_example_payload() -> dict[str, object]:
    section = MODEL_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"## Rollup snapshot schema\n.*?```json\n(.*?)\n```",
        section,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_run_rollup_matches_worked_example_and_stays_in_process():
    payload = _worked_example_payload()
    rollup = run_rollup(
        _worked_example_events(),
        run_id="run_fixture_sales_booking",
        project="proj_fixture",
        window_start_ms=WINDOW_START_MS,
        window_end_ms=WINDOW_END_MS,
        group_by=["agent", "funnel_stage"],
    )
    assert isinstance(rollup, RunRollup)
    assert rollup.to_dict() == payload

    # Round-trip the normative JSON through the model.
    restored = RunRollup.from_dict(payload)
    assert restored.to_dict() == payload
    assert restored.tenant is None

    source = ANALYTICS_SRC.read_text(encoding="utf-8")
    assert (
        "Scope cap (ADR 0010): no storage, no cross-run comparisons, "
        "no cross-tenant aggregation."
    ) in source
    tree = ast.parse(source)
    forbidden = {"load", "save", "persist", "store", "connect"}
    defined = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not (defined & forbidden)

    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any(
        module.startswith(("lucy_platform", "lucy_cloud", "pili")) for module in imports
    )


def test_run_rollup_aggregates_only_explicit_in_process_sessions():
    events_a = _worked_example_events()
    events_b = [
        _session_started(session_id="sess_b", agent_name="other_agent"),
        _turn(1, session_id="sess_b", caller_talk_ms=100, agent_talk_ms=100),
        _business(session_id="sess_b", funnel_stage="qualified"),
        _session_ended(session_id="sess_b", billable_audio_minutes=1.0),
    ]
    rollup = run_rollup(
        [*events_a, *events_b],
        run_id="run_multi",
        project="proj_fixture",
        window_start_ms=WINDOW_START_MS,
        window_end_ms=WINDOW_END_MS,
        group_by=["agent"],
        session_ids=["sess_fixture"],
    )
    assert rollup.measures["session_count"] == 1
    assert rollup.measures["turn_count"] == 3
    assert rollup.dimensions["agent"] == "sales_booking_agent"
    assert rollup.tenant is None


# --- C5: Card 99 compatibility -------------------------------------------------------


def test_existing_card_99_talk_measure_contract_remains_green():
    turns = [
        _turn(1, caller_talk_ms=400, agent_talk_ms=100),
        _turn(2, caller_talk_ms=100, agent_talk_ms=400),
    ]
    measures = aggregate_talk_measures(turns)
    assert isinstance(measures, TalkMeasures)
    assert measures.caller_talk_ms == 500
    assert measures.agent_talk_ms == 500
    assert measures.talk_ratio == 0.5
    assert aggregate_talk_measures([]).talk_ratio is None


def test_analytics_engine_module_imports_no_mocks():
    impl_source = ANALYTICS_SRC.read_text(encoding="utf-8")
    assert "unittest" not in impl_source
    assert "MagicMock" not in impl_source
    assert "pytest_mock" not in impl_source
    assert math.ceil(0.5 * 3) == 2
    assert inspect.isfunction(build_facts)
