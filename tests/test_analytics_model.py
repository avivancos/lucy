import json
import re
from pathlib import Path

from lucy.metrics import CostBreakdown, LatencyWaterfall

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
