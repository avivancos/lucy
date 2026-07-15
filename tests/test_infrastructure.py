from pathlib import Path
import wave

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


def test_telephony_lab_profile_exists():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for service_name in ["asterisk", "telephony-caller"]:
        assert service_name in services
        assert services[service_name]["profiles"] == ["telephony-lab"]

    asterisk = services["asterisk"]
    assert asterisk["image"].startswith("andrius/asterisk:22@sha256:")
    assert "env_file" not in asterisk
    assert set(asterisk["environment"]) == {
        "LUCY_TELEPHONY_ARI_PASSWORD",
        "LUCY_TELEPHONY_ARI_PORT",
        "LUCY_TELEPHONY_ARI_USER",
        "LUCY_TELEPHONY_AUDIO_SOCKET_HOST",
        "LUCY_TELEPHONY_AUDIO_SOCKET_PORT",
        "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS",
        "LUCY_TELEPHONY_PJSIP_PASSWORD",
        "LUCY_TELEPHONY_PJSIP_PORT",
        "LUCY_TELEPHONY_PJSIP_USER",
        "LUCY_TELEPHONY_RTP_PORT_END",
        "LUCY_TELEPHONY_RTP_PORT_START",
    }
    assert asterisk["healthcheck"]["test"] == [
        "CMD",
        "/opt/lucy-asterisk/scripts/healthcheck.sh",
    ]
    assert all(volume.endswith(":ro") for volume in asterisk["volumes"])
    assert all(volume.startswith("./infra/asterisk/") for volume in asterisk["volumes"])
    healthcheck = (ROOT / "infra/asterisk/scripts/healthcheck.sh").read_text(
        encoding="utf-8"
    )
    assert "pjsip show endpoint lucy-lab" in healthcheck

    caller = services["telephony-caller"]
    assert "env_file" not in caller
    assert set(caller["environment"]) == {
        "LUCY_TELEPHONY_ARI_HOST",
        "LUCY_TELEPHONY_ARI_PASSWORD",
        "LUCY_TELEPHONY_ARI_PORT",
        "LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS",
        "LUCY_TELEPHONY_ARI_USER",
        "LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS",
        "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS",
    }
    assert caller["depends_on"] == {"asterisk": {"condition": "service_healthy"}}
    assert caller["entrypoint"] == [
        "python3",
        "/opt/lucy-asterisk/scripts/originate_call.py",
    ]


def test_asterisk_lab_files_are_committed():
    required_paths = [
        "infra/asterisk/config/ari.conf.template",
        "infra/asterisk/config/asterisk.conf",
        "infra/asterisk/config/extensions.conf.template",
        "infra/asterisk/config/http.conf.template",
        "infra/asterisk/config/logger.conf",
        "infra/asterisk/config/modules.conf",
        "infra/asterisk/config/pjsip.conf.template",
        "infra/asterisk/config/rtp.conf.template",
        "infra/asterisk/fixtures/booking_caller_8k.wav",
        "infra/asterisk/scripts/healthcheck.sh",
        "infra/asterisk/scripts/originate_call.py",
        "infra/asterisk/scripts/render_config.py",
        "infra/asterisk/scripts/telephony_settings.py",
    ]

    for relative_path in required_paths:
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert path.stat().st_size > 0, relative_path

    with wave.open(
        str(ROOT / "infra/asterisk/fixtures/booking_caller_8k.wav"), "rb"
    ) as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 8_000


def test_telephony_lab_example_environment_is_complete():
    settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("LUCY_TELEPHONY_")
    )

    assert settings == {
        "LUCY_TELEPHONY_ARI_HOST": "asterisk",
        "LUCY_TELEPHONY_ARI_HOST_PORT": "18088",
        "LUCY_TELEPHONY_ARI_PASSWORD": "lucy-lab-only",
        "LUCY_TELEPHONY_ARI_PORT": "8088",
        "LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS": "2",
        "LUCY_TELEPHONY_ARI_USER": "lucy-lab",
        "LUCY_TELEPHONY_AUDIO_SOCKET_HOST": "lucy-media-gateway",
        "LUCY_TELEPHONY_AUDIO_SOCKET_PORT": "9092",
        "LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS": "0.1",
        "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS": "15",
        "LUCY_TELEPHONY_HEALTH_INTERVAL": "2s",
        "LUCY_TELEPHONY_HEALTH_RETRIES": "15",
        "LUCY_TELEPHONY_HEALTH_START_PERIOD": "2s",
        "LUCY_TELEPHONY_HEALTH_TIMEOUT": "2s",
        "LUCY_TELEPHONY_PJSIP_PASSWORD": "lucy-lab-only",
        "LUCY_TELEPHONY_PJSIP_PORT": "5060",
        "LUCY_TELEPHONY_PJSIP_USER": "lucy-lab",
        "LUCY_TELEPHONY_RTP_PORT_END": "10019",
        "LUCY_TELEPHONY_RTP_PORT_START": "10000",
        "LUCY_TELEPHONY_SIP_HOST_PORT": "15060",
    }


def test_telephony_lab_keeps_media_out_of_python_and_host_ports_local():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert all(
        published_port.startswith("127.0.0.1:")
        for published_port in compose["services"]["asterisk"]["ports"]
    )

    dialplan = (ROOT / "infra/asterisk/config/extensions.conf.template").read_text(
        encoding="utf-8"
    )
    assert "AudioSocket(" in dialplan
    assert "$LUCY_TELEPHONY_AUDIO_SOCKET_HOST" in dialplan
    assert "$LUCY_TELEPHONY_AUDIO_SOCKET_PORT" in dialplan

    guide = (ROOT / "docs/local-telephony-lab.md").read_text(encoding="utf-8")
    assert "docker compose --profile telephony-lab run --rm telephony-caller" in guide
    assert "audio frames never enter Python" in guide
