"""Docs contract tests: every ```python block in README + guides runs offline."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lucy.evals import booking_happy_path

ROOT = Path(__file__).resolve().parents[1]
GUIDE_DIR = ROOT / "docs" / "guides"
GUIDES: tuple[str, ...] = (
    "getting-started.md",
    "providers-and-plugins.md",
    "agent-graphs.md",
    "observability.md",
    "testing-without-mocks.md",
    "transports-and-telephony.md",
)
DOC_FILES = (ROOT / "README.md",) + tuple(GUIDE_DIR / name for name in GUIDES)
STRIPPED_ENV_VARS = (
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "OPENAI_API_KEY",
    "LUCY_API_KEY",
    "LUCY_ENDPOINT",
)
BLOCK_TIMEOUT_S = 120

_PYTHON_FENCE = re.compile(r"^```python\n(.*?)```", re.MULTILINE | re.DOTALL)
_MD_LINK = re.compile(r"\]\(([^)]+)\)")


def extract_python_blocks(text: str) -> list[str]:
    return [match.group(1) for match in _PYTHON_FENCE.finditer(text)]


def run_block(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        key: value for key, value in os.environ.items() if key not in STRIPPED_ENV_VARS
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=BLOCK_TIMEOUT_S,
    )


def _non_blank_lines(code: str) -> list[str]:
    return [line for line in code.splitlines() if line.strip()]


def _doc_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_guides_exist_with_h1_title() -> None:
    for name in GUIDES:
        path = GUIDE_DIR / name
        assert path.is_file(), "missing guide: %s" % name
        lines = path.read_text(encoding="utf-8").splitlines()
        first = next((line for line in lines if line.strip()), "")
        assert first.startswith("# "), "%s must start with an H1" % name


def test_every_guide_has_at_least_one_python_block() -> None:
    for name in GUIDES:
        blocks = extract_python_blocks(_doc_text(GUIDE_DIR / name))
        assert blocks, "%s needs at least one python block" % name


@pytest.mark.parametrize(
    "doc_path,block_index",
    [
        (path, index)
        for path in DOC_FILES
        for index, _ in enumerate(extract_python_blocks(_doc_text(path)))
    ],
    ids=lambda value: value.name if isinstance(value, Path) else "block-%s" % value,
)
def test_every_python_block_runs_offline_with_exit_zero(
    doc_path: Path, block_index: int, tmp_path: Path
) -> None:
    code = extract_python_blocks(_doc_text(doc_path))[block_index]
    result = run_block(code, tmp_path)
    if result.returncode != 0:
        print(result.stderr)
    assert result.returncode == 0


def test_readme_quickstart_block_is_at_most_30_code_lines() -> None:
    blocks = extract_python_blocks(_doc_text(ROOT / "README.md"))
    assert blocks, "README.md needs a python quickstart block"
    assert len(_non_blank_lines(blocks[0])) <= 30


def test_readme_quickstart_prints_a_transcript(tmp_path: Path) -> None:
    blocks = extract_python_blocks(_doc_text(ROOT / "README.md"))
    assert blocks, "README.md needs a python quickstart block"
    result = run_block(blocks[0], tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip()
    caller_lines = [
        turn.text for turn in booking_happy_path().turns if turn.speaker == "caller"
    ]
    assert caller_lines
    assert any(line in result.stdout for line in caller_lines)


def test_readme_documents_cloud_env_var() -> None:
    assert "LUCY_API_KEY" in _doc_text(ROOT / "README.md")


def test_relative_markdown_links_resolve() -> None:
    for path in DOC_FILES:
        text = _doc_text(path)
        for target in _MD_LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("#"):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            resolved = (path.parent / file_part).resolve()
            assert resolved.is_file(), "%s links to missing %s" % (path, target)


def test_getting_started_walks_install_first_agent_and_trace() -> None:
    text = _doc_text(GUIDE_DIR / "getting-started.md").lower()
    assert "pip install -e" in text
    assert "lucy_trace_file" in text
    assert "deepgram/nova-3" in text
    assert "next steps" in text


def test_providers_guide_names_entry_point_group_and_contract_suites() -> None:
    text = _doc_text(GUIDE_DIR / "providers-and-plugins.md").lower()
    assert "lucy.plugins" in text
    assert "lucyplugin" in text
    assert "sttcontractsuite" in text
    assert "lucy.testing.record" in text
    assert "replaytransport" in text


def test_agent_graphs_guide_covers_builder_api_and_prebuilt_catalog() -> None:
    text = _doc_text(GUIDE_DIR / "agent-graphs.md").lower()
    assert "agentgraph" in text
    assert "add_conditional_edge" in text
    assert "checkpoint" in text
    assert "booking_agent" in text
    assert "prebuilt" in text


def test_observability_guide_links_wire_spec_and_env_vars() -> None:
    text = _doc_text(GUIDE_DIR / "observability.md").lower()
    assert "configure(" in text
    assert "jsonlfileexporter" in text
    assert "lucy_api_key" in text
    assert "telemetry-wire-v1.md" in text
    assert "redact_pii" in text


def test_testing_guide_cites_adr_0003_and_simulator_inventory() -> None:
    text = _doc_text(GUIDE_DIR / "testing-without-mocks.md").lower()
    assert "adr 0003" in text
    assert "localsttsimulator" in text
    assert "manualclock" in text
    assert "conversationharness" in text
    assert "no mocks" in text


def test_transports_guide_links_connectivity_doc() -> None:
    text = _doc_text(GUIDE_DIR / "transports-and-telephony.md").lower()
    assert "audiosocket" in text
    assert "asterisk" in text
    assert "sip trunk" in text
    assert "telephony-connectivity.md" in text
