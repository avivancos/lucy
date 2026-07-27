# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the public GitHub Actions CI pipeline (card 50)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load_workflow() -> dict:
    raw = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)
    return data


def _workflow_on(workflow: dict) -> dict:
    # YAML 1.1 may parse unquoted `on:` as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def _job_run_scripts(job: dict) -> list[str]:
    scripts: list[str] = []
    for step in job.get("steps", []):
        run = step.get("run")
        if isinstance(run, str):
            scripts.append(run)
    return scripts


def test_dev_extra_declares_ci_toolchain():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = "\n".join(pyproject["project"]["optional-dependencies"]["dev"])
    assert "ruff" in dev
    assert "pytest-socket" in dev
    ruff = pyproject["tool"]["ruff"]
    assert ruff.get("src") == ["src", "tests"]


def test_ci_workflow_triggers_on_main_push_and_pull_request():
    triggers = _workflow_on(_load_workflow())
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]


def test_ci_workflow_defines_all_six_jobs():
    jobs = _load_workflow()["jobs"]
    assert set(jobs) == {
        "lint",
        "tests",
        "backlog-contract",
        "build",
        "compose-config",
        "secret-scan",
    }


def test_ci_lint_job_runs_ruff_check_and_format():
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["lint"]))
    check_idx = scripts.find("ruff check src tests packages")
    format_idx = scripts.find("ruff format --check src tests packages")
    assert check_idx >= 0
    assert format_idx > check_idx


def test_ci_lint_job_runs_mypy_when_config_exists():
    """mypy config is present in pyproject.toml — lint job must run it."""
    assert (ROOT / "pyproject.toml").read_text(encoding="utf-8").count("[tool.mypy]")
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["lint"]))
    assert "python -m mypy src" in scripts


def test_ci_test_matrix_covers_python_310_to_312():
    job = _load_workflow()["jobs"]["tests"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["python-version"] == ["3.10", "3.11", "3.12"]


def test_ci_tests_job_blocks_network_egress():
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["tests"]))
    assert "--disable-socket" in scripts
    assert "--allow-unix-socket" in scripts
    assert "--allow-hosts=127.0.0.1,::1" in scripts


def test_ci_workflow_references_no_secrets():
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in raw


def test_ci_jobs_cache_dependencies():
    workflow = _load_workflow()
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/setup-python@"):
                assert step.get("with", {}).get("cache") == "pip", job_id
            if uses.startswith("astral-sh/setup-uv@"):
                assert step.get("with", {}).get("enable-cache") is True, job_id


def test_ci_build_job_builds_workspace_and_twine_checks():
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["build"]))
    assert "uv build --all-packages" in scripts
    assert "twine check --strict" in scripts


def test_ci_compose_job_validates_config():
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["compose-config"]))
    assert "docker compose config --quiet" in scripts


def test_ci_secret_scan_job_runs_gitleaks_with_repo_config():
    scripts = "\n".join(_job_run_scripts(_load_workflow()["jobs"]["secret-scan"]))
    assert "gitleaks" in scripts
    assert ".gitleaks.toml" in scripts


def test_readme_carries_ci_workflow_badge():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    matches = re.findall(
        r"https://github\.com/[\w.-]+/[\w.-]+/actions/workflows/ci\.yml/badge\.svg",
        readme,
    )
    assert len(matches) == 1
