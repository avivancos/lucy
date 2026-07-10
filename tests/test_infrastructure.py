from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_docker_compose_defines_lucy_local_stack():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for service in [
        "lucy-api",
        "lucy-worker",
        "lucy-media-gateway",
        "postgres",
        "redis",
        "otel-collector",
    ]:
        assert service in services

    # The dashboard moved to lucy-platform (card 21); lucy no longer ships it.
    assert "lucy-dashboard" not in services

    assert services["lucy-api"]["ports"] == ["${LUCY_API_HOST_PORT:-8000}:8000"]
    assert services["lucy-media-gateway"]["ports"] == ["8081:8081"]
    assert services["otel-collector"]["volumes"] == [
        "./infra/otel-collector-config.yaml:/etc/otelcol/config.yaml:ro"
    ]


def test_dockerfiles_and_otel_config_exist():
    for path in [
        ".dockerignore",
        "Dockerfile.api",
        "media-gateway-rust/Dockerfile",
        "media-gateway-rust/Cargo.toml",
        "media-gateway-rust/src/main.rs",
        "infra/otel-collector-config.yaml",
        ".env.example",
    ]:
        assert (ROOT / path).is_file(), path


def test_rust_media_gateway_exposes_health_contract():
    source = (ROOT / "media-gateway-rust/src/main.rs").read_text(encoding="utf-8")

    assert "lucy-media-gateway" in source
    assert '"/health"' in source
    assert "8081" in source


def test_docker_compose_gateway_profiles():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "profiles" not in services["lucy-media-gateway"]
    assert services["lucy-dev-gateway"]["profiles"] == ["test"]
    assert services["lucy-gateway-session"]["profiles"] == ["gateway-it"]
    assert services["lucy-gateway-tests"]["profiles"] == ["gateway-it"]


def test_dockerignore_keeps_build_context_lean_without_hiding_project_sources():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    for pattern in {
        ".git/",
        ".venv/",
        ".pytest_cache/",
        ".ruff_cache/",
        ".mypy_cache/",
        "dist/",
        "build/",
        "*.egg-info/",
        ".env",
        "node_modules/",
        ".next/",
        "coverage/",
        "htmlcov/",
        "target/",
    }:
        assert pattern in patterns

    for project_path in {
        "src/",
        "tests/",
        "docs/",
        "backlog/",
        "examples/",
        "infra/",
        "media-gateway-rust/",
        "pyproject.toml",
        "README.md",
    }:
        assert project_path not in patterns
