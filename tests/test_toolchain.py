from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_lint_and_type_tools():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    dev_dependencies = data["project"]["optional-dependencies"]["dev"]

    assert any(dependency.startswith("ruff>=") for dependency in dev_dependencies)
    assert any(dependency.startswith("mypy>=") for dependency in dev_dependencies)
