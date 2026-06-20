import tomllib
from pathlib import Path

from lucy import __version__


ROOT = Path(__file__).resolve().parents[1]


def load_pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_python_package_metadata_and_dependencies():
    pyproject = load_pyproject()
    project = pyproject["project"]

    assert project["name"] == "lucy"
    assert project["version"] == __version__
    assert project["requires-python"] == ">=3.9"

    dependencies = "\n".join(project["dependencies"])
    for dependency in [
        "fastapi",
        "pydantic",
        "uvicorn",
        "opentelemetry-api",
    ]:
        assert dependency in dependencies

    dev_dependencies = "\n".join(project["optional-dependencies"]["dev"])
    assert "pytest" in dev_dependencies
    assert "httpx" in dev_dependencies

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]

