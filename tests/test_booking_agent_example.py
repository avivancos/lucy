"""The sanitized booking-agent example must run offline on public lucy APIs."""

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_booking_agent_example_runs_offline(capsys):
    path = ROOT / "examples" / "booking_agent" / "booking_agent.py"
    assert path.exists()
    runpy.run_path(str(path), run_name="__main__")
    out = capsys.readouterr().out
    assert "crm upsert:" in out
    assert "calendar hold:" in out
