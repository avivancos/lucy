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

    assert services["lucy-api"]["ports"] == [
        "127.0.0.1:${LUCY_API_HOST_PORT:-8000}:8000"
    ]
    assert services["lucy-media-gateway"]["ports"] == [
        "127.0.0.1:${LUCY_GATEWAY_HEALTH_HOST_PORT:-8081}:8081",
        "127.0.0.1:${LUCY_CPAAAS_MEDIA_HOST_PORT:-19094}:9094",
    ]
    assert services["otel-collector"]["volumes"] == [
        "./infra/otel-collector-config.yaml:/etc/otelcol/config.yaml:ro"
    ]


def test_dockerfiles_and_otel_config_exist():
    for path in [
        ".dockerignore",
        "Dockerfile.api",
        "media-gateway-rust/Dockerfile",
        "media-gateway-rust/Dockerfile.test",
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
    assert services["lucy-gateway-tests"]["build"] == {
        "context": "./media-gateway-rust",
        "dockerfile": "Dockerfile.test",
    }
    assert "image" not in services["lucy-gateway-tests"]
    assert services["cpaas-smoke"]["profiles"] == ["cpaas-smoke"]
    assert services["cpaas-smoke"]["command"] == (
        "python -m lucy.transport.cpaas_smoke"
    )
    assert "env_file" not in services["cpaas-smoke"]


def test_cpaas_smoke_compose_passes_only_named_environment_inputs():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["cpaas-smoke"]["environment"]

    assert set(environment) == {
        "LUCY_CPAAAS_PROVIDER",
        "LUCY_CPAAAS_ACCOUNT_ID",
        "LUCY_CPAAAS_API_KEY",
        "LUCY_CPAAAS_FROM_NUMBER",
        "LUCY_CPAAAS_TO_NUMBER",
        "LUCY_CPAAAS_API_BASE_URL",
        "LUCY_CPAAAS_PUBLIC_WS_URL",
        "LUCY_CPAAAS_STREAM_AUTH_TOKEN",
        "LUCY_CPAAAS_ALLOW_INSECURE_LOCAL",
        "LUCY_CPAAAS_REQUEST_TIMEOUT_SECONDS",
    }
    assert all(value.startswith("${LUCY_CPAAAS_") for value in environment.values())


def test_media_gateway_compose_wires_cpaas_runtime_without_secrets_file():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    gateway = compose["services"]["lucy-media-gateway"]
    environment = gateway["environment"]

    assert "env_file" not in gateway
    for name in {
        "LUCY_CPAAAS_PROVIDER",
        "LUCY_CPAAAS_ACCOUNT_ID",
        "LUCY_CPAAAS_API_KEY",
        "LUCY_CPAAAS_API_BASE_URL",
        "LUCY_CPAAAS_PUBLIC_WS_URL",
        "LUCY_CPAAAS_STREAM_AUTH_TOKEN",
        "LUCY_CPAAAS_MEDIA_BIND",
        "LUCY_CPAAAS_ALLOWED_CIDRS",
        "LUCY_CPAAAS_HANDSHAKE_TIMEOUT_MS",
        "LUCY_CPAAAS_IDLE_TIMEOUT_MS",
        "LUCY_CPAAAS_MAX_SESSIONS",
        "LUCY_CPAAAS_ALLOW_INSECURE_LOCAL",
    }:
        assert environment[name].startswith("${LUCY_CPAAAS_")
    assert environment["LUCY_CPAAAS_MEDIA_BIND"] == (
        "${LUCY_CPAAAS_MEDIA_BIND:-0.0.0.0:9094}"
    )


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
        "LUCY_TELEPHONY_TEST_EXTENSION",
        "LUCY_GATEWAY_HEALTH_HOST",
        "LUCY_GATEWAY_HEALTH_PORT",
    }
    assert caller["depends_on"] == {
        "asterisk": {"condition": "service_healthy"},
        "lucy-media-gateway": {"condition": "service_healthy"},
    }
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
        "LUCY_TELEPHONY_TEST_EXTENSION": "lucy-audiosocket",
        "LUCY_TELEPHONY_TRANSPORT_MODE": "audiosocket",
    }

    gateway_settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith(("LUCY_ASTERISK_", "LUCY_GATEWAY_"))
    )
    assert {
        "LUCY_ASTERISK_ARI_APP",
        "LUCY_ASTERISK_ARI_BASE_URL",
        "LUCY_ASTERISK_ARI_CALLER_CHANNEL_ID",
        "LUCY_ASTERISK_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS",
        "LUCY_ASTERISK_ARI_EVENTS_IDLE_TIMEOUT_MS",
        "LUCY_ASTERISK_ARI_EVENTS_WS_URL",
        "LUCY_ASTERISK_ARI_PASSWORD",
        "LUCY_ASTERISK_ARI_REQUEST_TIMEOUT_MS",
        "LUCY_ASTERISK_ARI_USERNAME",
        "LUCY_GATEWAY_ARI_RTP_ADVERTISED",
        "LUCY_GATEWAY_ARI_RTP_BIND",
        "LUCY_GATEWAY_ARI_RTP_CLOCK_RATE_HZ",
        "LUCY_GATEWAY_ARI_RTP_IDLE_TIMEOUT_MS",
        "LUCY_GATEWAY_ARI_RTP_PAYLOAD_TYPE",
        "LUCY_GATEWAY_CONTROL_CONNECT_TIMEOUT_MS",
        "LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND",
    } <= gateway_settings.keys()


def test_gateway_control_auth_is_fail_closed_in_committed_compose():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    example_settings = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("LUCY_GATEWAY_CONTROL_TOKEN=")
    )

    assert example_settings == {"LUCY_GATEWAY_CONTROL_TOKEN": ""}
    for service_name in [
        "lucy-api",
        "lucy-media-gateway",
        "lucy-gateway-session",
        "lucy-dev-gateway",
    ]:
        assert services[service_name]["environment"]["LUCY_GATEWAY_CONTROL_TOKEN"] == (
            "${LUCY_GATEWAY_CONTROL_TOKEN:-}"
        )


def test_gateway_compose_wires_every_asterisk_transport_mode():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["lucy-media-gateway"]["environment"]

    expected = {
        "LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND": (
            "${LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND:-0.0.0.0:9093}"
        ),
        "LUCY_GATEWAY_CONTROL_CONNECT_TIMEOUT_MS": (
            "${LUCY_GATEWAY_CONTROL_CONNECT_TIMEOUT_MS:-5000}"
        ),
        "LUCY_ASTERISK_ARI_BASE_URL": (
            "${LUCY_ASTERISK_ARI_BASE_URL:-http://asterisk:8088/ari/}"
        ),
        "LUCY_ASTERISK_ARI_EVENTS_WS_URL": (
            "${LUCY_ASTERISK_ARI_EVENTS_WS_URL:-ws://asterisk:8088/ari/events?app=lucy-voice}"
        ),
        "LUCY_ASTERISK_ARI_USERNAME": "${LUCY_ASTERISK_ARI_USERNAME:-lucy-lab}",
        "LUCY_ASTERISK_ARI_PASSWORD": ("${LUCY_ASTERISK_ARI_PASSWORD:-lucy-lab-only}"),
        "LUCY_ASTERISK_ARI_APP": "${LUCY_ASTERISK_ARI_APP:-lucy-voice}",
        "LUCY_ASTERISK_ARI_CALLER_CHANNEL_ID": (
            "${LUCY_ASTERISK_ARI_CALLER_CHANNEL_ID:-replace-with-active-channel-id}"
        ),
        "LUCY_ASTERISK_ARI_REQUEST_TIMEOUT_MS": (
            "${LUCY_ASTERISK_ARI_REQUEST_TIMEOUT_MS:-5000}"
        ),
        "LUCY_ASTERISK_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS": (
            "${LUCY_ASTERISK_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS:-5000}"
        ),
        "LUCY_ASTERISK_ARI_EVENTS_IDLE_TIMEOUT_MS": (
            "${LUCY_ASTERISK_ARI_EVENTS_IDLE_TIMEOUT_MS:-30000}"
        ),
        "LUCY_ASTERISK_ARI_ALLOW_INSECURE_HTTP": (
            "${LUCY_ASTERISK_ARI_ALLOW_INSECURE_HTTP:-true}"
        ),
        "LUCY_GATEWAY_ARI_RTP_BIND": "${LUCY_GATEWAY_ARI_RTP_BIND:-0.0.0.0:9094}",
        "LUCY_GATEWAY_ARI_RTP_ADVERTISED": (
            "${LUCY_GATEWAY_ARI_RTP_ADVERTISED:-lucy-media-gateway:9094}"
        ),
        "LUCY_GATEWAY_ARI_RTP_PAYLOAD_TYPE": (
            "${LUCY_GATEWAY_ARI_RTP_PAYLOAD_TYPE:-118}"
        ),
        "LUCY_GATEWAY_ARI_RTP_CLOCK_RATE_HZ": (
            "${LUCY_GATEWAY_ARI_RTP_CLOCK_RATE_HZ:-16000}"
        ),
        "LUCY_GATEWAY_ARI_RTP_IDLE_TIMEOUT_MS": (
            "${LUCY_GATEWAY_ARI_RTP_IDLE_TIMEOUT_MS:-30000}"
        ),
    }
    assert expected.items() <= environment.items()

    dockerfile = (ROOT / "media-gateway-rust/Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8081/tcp 9092/tcp 9093/tcp 9094/tcp 9094/udp" in dockerfile


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


def test_telephony_lab_routes_adapter_smoke_through_rust_gateway():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    gateway = services["lucy-media-gateway"]
    caller = services["telephony-caller"]

    assert gateway["environment"]["LUCY_GATEWAY_AUDIO_SOCKET_BIND"] == (
        "${LUCY_GATEWAY_AUDIO_SOCKET_BIND:-0.0.0.0:9092}"
    )
    assert gateway["environment"]["LUCY_TELEPHONY_TRANSPORT_MODE"] == (
        "${LUCY_TELEPHONY_TRANSPORT_MODE:-audiosocket}"
    )
    assert gateway["environment"]["LUCY_GATEWAY_MEDIA_BACKEND"] == (
        "${LUCY_GATEWAY_MEDIA_BACKEND:-fixture}"
    )
    provider_environment = {
        "LUCY_GATEWAY_DEEPGRAM_URL",
        "LUCY_GATEWAY_DEEPGRAM_MODEL",
        "LUCY_GATEWAY_DEEPGRAM_API_KEY",
        "LUCY_GATEWAY_DEEPGRAM_SAMPLE_RATE_HZ",
        "LUCY_GATEWAY_DEEPGRAM_FRAME_BYTES",
        "LUCY_GATEWAY_DEEPGRAM_ENDPOINTING_MS",
        "LUCY_GATEWAY_ELEVENLABS_URL",
        "LUCY_GATEWAY_ELEVENLABS_MODEL",
        "LUCY_GATEWAY_ELEVENLABS_VOICE_ID",
        "LUCY_GATEWAY_ELEVENLABS_API_KEY",
        "LUCY_GATEWAY_ELEVENLABS_OUTPUT_FORMAT",
        "LUCY_GATEWAY_ELEVENLABS_SAMPLE_RATE_HZ",
        "LUCY_GATEWAY_ELEVENLABS_PLAYBACK_FRAME_MS",
        "LUCY_GATEWAY_PROVIDER_CONNECT_TIMEOUT_MS",
        "LUCY_GATEWAY_PROVIDER_IDLE_TIMEOUT_MS",
    }
    assert provider_environment <= gateway["environment"].keys()
    assert gateway["environment"]["LUCY_GATEWAY_DEEPGRAM_API_KEY"] == (
        "${LUCY_GATEWAY_DEEPGRAM_API_KEY:-}"
    )
    assert gateway["environment"]["LUCY_GATEWAY_ELEVENLABS_API_KEY"] == (
        "${LUCY_GATEWAY_ELEVENLABS_API_KEY:-}"
    )
    assert gateway["volumes"] == ["./tests/fixtures/audio:/fixtures/audio:ro"]
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway["security_opt"] == ["no-new-privileges:true"]
    assert caller["environment"]["LUCY_TELEPHONY_TEST_EXTENSION"] == (
        "${LUCY_TELEPHONY_TEST_EXTENSION:-lucy-audiosocket}"
    )
    assert caller["environment"]["LUCY_GATEWAY_HEALTH_HOST"] == (
        "${LUCY_GATEWAY_HEALTH_HOST:-lucy-media-gateway}"
    )
    assert caller["environment"]["LUCY_GATEWAY_HEALTH_PORT"] == (
        "${LUCY_GATEWAY_HEALTH_PORT:-8081}"
    )
    assert caller["depends_on"] == {
        "asterisk": {"condition": "service_healthy"},
        "lucy-media-gateway": {"condition": "service_healthy"},
    }

    dialplan = (ROOT / "infra/asterisk/config/extensions.conf.template").read_text(
        encoding="utf-8"
    )
    assert "exten = play-fixture,1,Playback(lucy/booking_caller_8k)" in dialplan
    dockerfile = (ROOT / "media-gateway-rust/Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8081/tcp 9092/tcp" in dockerfile
    assert 'CMD ["lucy-media-gateway", "healthcheck"]' in dockerfile
    assert "USER lucy" in dockerfile
