from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_ops_command_center_spec_covers_core_views():
    doc = (ROOT / "docs/product/dashboard-ops-command-center.md").read_text(
        encoding="utf-8"
    ).lower()

    for required in [
        "live calls",
        "trace waterfall",
        "cost board",
        "rag inspector",
        "sentiment",
        "funnel",
        "crm sync",
        "model/version diffs",
        "evals",
        "dense",
        "operational",
    ]:
        assert required in doc


def test_dashboard_ops_command_center_wireframes_are_implementation_ready():
    doc = (ROOT / "docs/product/dashboard-ops-command-center.md").read_text(
        encoding="utf-8"
    ).lower()

    for view in [
        "live calls wireframe",
        "trace waterfall wireframe",
        "cost board wireframe",
        "rag inspector wireframe",
        "sentiment funnel crm wireframe",
        "model diffs wireframe",
        "evals wireframe",
    ]:
        assert view in doc

    for state in ["empty state", "loading state", "error state"]:
        assert state in doc

    assert "acceptance criteria" in doc
    assert "follow-up implementation cards" in doc
