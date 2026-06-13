from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_docker_compose_defines_lucy_local_stack():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for service in [
        "lucy-api",
        "lucy-worker",
        "lucy-dashboard",
        "lucy-media-gateway",
        "postgres",
        "redis",
        "otel-collector",
    ]:
        assert service in services

    assert services["lucy-api"]["ports"] == ["8000:8000"]
    assert services["lucy-dashboard"]["ports"] == ["3000:3000"]
    assert services["lucy-media-gateway"]["ports"] == ["8081:8081"]
    assert services["otel-collector"]["volumes"] == [
        "./infra/otel-collector-config.yaml:/etc/otelcol/config.yaml:ro"
    ]


def test_dockerfiles_and_otel_config_exist():
    for path in [
        "Dockerfile.api",
        "dashboard/Dockerfile",
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

