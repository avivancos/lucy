import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lucy.analytics import aggregate_talk_measures
from lucy.limits import MAX_CONTROL_DURATION_MS
from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.observe.events import TurnEvent

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/adr/0013-analytics-and-sre-observability.md"
MODEL = ROOT / "docs/analytics-model-v1.md"
REQUIRED_SECTIONS = [
    "Status",
    "Scope and boundary",
    "Facts",
    "Dimensions",
    "Measures",
    "Derived metrics",
    "Rollup snapshot schema",
    "Conformance",
]


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, heading
    return match.group(1).strip()


def test_adr_0013_states_open_closed_split():
    text = ADR.read_text(encoding="utf-8")
    decision = _section(text, "Decision")
    assert "lucy.analytics" in decision
    assert "analytics warehouse and ETL" in decision
    assert "Open (SRE seam)" in decision
    assert "Closed" in decision


def test_analytics_model_has_all_required_sections():
    text = MODEL.read_text(encoding="utf-8")
    offsets = []
    for heading in REQUIRED_SECTIONS:
        offsets.append(text.index(f"## {heading}"))
        assert _section(text, heading)
    assert offsets == sorted(offsets)


def test_analytics_model_enumerates_all_metric_fields():
    measures = _section(MODEL.read_text(encoding="utf-8"), "Measures")
    fields = set(CostBreakdown.model_fields) | set(LatencyWaterfall.model_fields)
    assert fields
    assert all(f"`{field}`" in measures for field in fields)


def test_analytics_model_forbids_etc():
    for path in (ADR, MODEL):
        assert "etc." not in path.read_text(encoding="utf-8").lower()


def test_rollup_example_payload_is_valid_json():
    section = _section(MODEL.read_text(encoding="utf-8"), "Rollup snapshot schema")
    match = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["schema_version"] == "analytics-model/v1"
    assert payload["measures"]
    assert payload["derived_metrics"]
    measures = payload["measures"]
    assert measures["total_cost"] == sum(
        measures[field]
        for field in CostBreakdown.model_fields
        if field != "billable_audio_minutes"
    )
    assert measures["total_ms"] == sum(
        measures[field] for field in LatencyWaterfall.model_fields
    )


def _turn(
    turn_index: int,
    *,
    caller_talk_ms: int = 0,
    agent_talk_ms: int = 0,
    tags: dict[str, str] | None = None,
) -> TurnEvent:
    return TurnEvent(
        event_id=f"00000000-0000-4000-8000-{turn_index:012d}",
        session_id="session-talk-ratio",
        emitted_at_ms=turn_index,
        turn_id=f"turn-{turn_index}",
        turn_index=turn_index,
        latency_waterfall=LatencyWaterfall(),
        caller_talk_ms=caller_talk_ms,
        agent_talk_ms=agent_talk_ms,
        tags=tags or {},
    )


def test_talk_measures_sum_turns_and_use_aggregate_ratio():
    measures = aggregate_talk_measures(
        [
            _turn(1, caller_talk_ms=400, agent_talk_ms=100),
            _turn(2, caller_talk_ms=100, agent_talk_ms=400),
        ]
    )

    assert measures.caller_talk_ms == 500
    assert measures.agent_talk_ms == 500
    assert measures.talk_ratio == 0.5


def test_talk_ratio_is_none_without_measured_speech():
    measures = aggregate_talk_measures([])

    assert measures.caller_talk_ms == 0
    assert measures.agent_talk_ms == 0
    assert measures.talk_ratio is None


@pytest.mark.parametrize(
    ("caller_talk_ms", "agent_talk_ms", "expected"),
    [(500, 0, 0.0), (0, 500, 1.0)],
)
def test_talk_ratio_preserves_one_sided_speech_endpoints(
    caller_talk_ms, agent_talk_ms, expected
):
    measures = aggregate_talk_measures(
        [
            _turn(
                1,
                caller_talk_ms=caller_talk_ms,
                agent_talk_ms=agent_talk_ms,
            )
        ]
    )

    assert measures.talk_ratio == expected


def test_talk_measures_ignore_billing_and_text_proxies():
    measures = aggregate_talk_measures(
        [
            _turn(
                1,
                tags={
                    "billable_audio_minutes": "99",
                    "transcript": "a deliberately long transcript",
                    "token_count": "1000000",
                },
            )
        ]
    )

    assert measures.caller_talk_ms == 0
    assert measures.agent_talk_ms == 0
    assert measures.talk_ratio is None


def test_legacy_turn_events_default_missing_talk_measures_to_zero():
    wire = _turn(1, caller_talk_ms=12, agent_talk_ms=34).to_wire()
    del wire["caller_talk_ms"]
    del wire["agent_talk_ms"]

    event = TurnEvent.model_validate(wire)

    assert event.caller_talk_ms == 0
    assert event.agent_talk_ms == 0
    assert event.to_wire()["caller_talk_ms"] == 0
    assert event.to_wire()["agent_talk_ms"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("caller_talk_ms", True),
        ("agent_talk_ms", False),
        ("caller_talk_ms", -1),
        ("agent_talk_ms", MAX_CONTROL_DURATION_MS + 1),
    ],
)
def test_turn_talk_measures_reject_invalid_wire_values(field, value):
    payload = _turn(1).model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        TurnEvent.model_validate(payload)
