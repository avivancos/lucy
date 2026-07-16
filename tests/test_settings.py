import pytest
from pydantic import ValidationError

from lucy.settings import (
    AsteriskTransportSettings,
    GatewayControlSettings,
    LatencyBudgets,
)
from lucy.specs import AsteriskTransportMode


def test_defaults_match_adr_0011_table():
    b = LatencyBudgets()
    assert b.endpoint_silence_ms == 150
    assert b.stt_final_ms == 60
    assert b.control_transport_ms == 10
    assert b.control_commit_ms == 1_000
    assert b.graph_dispatch_ms == 10
    assert b.llm_first_clause_ms == 380
    assert b.tts_first_byte_ms == 150
    assert b.gateway_pacing_ms == 30
    assert b.turn_total_ms == 800
    assert b.max_tool_rounds_per_turn == 3


def test_env_var_overrides_a_budget(monkeypatch):
    monkeypatch.setenv("LUCY_BUDGET_TURN_TOTAL_MS", "500")
    assert LatencyBudgets().turn_total_ms == 500


def test_explicit_argument_wins_over_default():
    assert LatencyBudgets(turn_total_ms=650).turn_total_ms == 650


def test_gateway_control_token_is_typed_and_redacted(monkeypatch):
    monkeypatch.setenv("LUCY_GATEWAY_CONTROL_TOKEN", "control-secret")
    settings = GatewayControlSettings()

    assert settings.control_token.get_secret_value() == "control-secret"
    assert "control-secret" not in repr(settings)


def test_empty_gateway_control_token_environment_disables_endpoint_auth(monkeypatch):
    monkeypatch.setenv("LUCY_GATEWAY_CONTROL_TOKEN", "")

    assert GatewayControlSettings().control_token is None


@pytest.mark.parametrize("invalid", ("", "has space", "has\nnewline"))
def test_gateway_control_token_rejects_unsafe_values(invalid):
    with pytest.raises(ValidationError):
        GatewayControlSettings(control_token=invalid)


@pytest.mark.parametrize("invalid", (0, -1, True))
def test_control_commit_budget_is_strictly_positive(invalid):
    with pytest.raises(ValidationError):
        LatencyBudgets(control_commit_ms=invalid)


def test_asterisk_transport_settings_have_private_network_defaults(monkeypatch):
    monkeypatch.delenv("LUCY_TELEPHONY_ARI_USER", raising=False)
    monkeypatch.delenv("LUCY_TELEPHONY_ARI_PASSWORD", raising=False)
    settings = AsteriskTransportSettings()

    assert settings.mode is AsteriskTransportMode.AUDIO_SOCKET
    assert settings.audio_socket_host == "lucy-media-gateway"
    assert settings.audio_socket_port == 9092
    assert settings.media_websocket_host == "lucy-media-gateway"
    assert settings.media_websocket_port == 9093
    assert settings.ari_host == "asterisk"
    assert settings.ari_port == 8088
    assert settings.ari_user is None
    assert settings.ari_password is None
    assert settings.external_media_rtp_host == "0.0.0.0"
    assert settings.external_media_rtp_port == 10000
    assert "lucy-lab-only" not in repr(settings)


def test_asterisk_transport_settings_read_telephony_environment(monkeypatch):
    monkeypatch.setenv("LUCY_TELEPHONY_TRANSPORT_MODE", "media_websocket")
    monkeypatch.setenv("LUCY_TELEPHONY_MEDIA_WEBSOCKET_PORT", "19093")
    monkeypatch.setenv("LUCY_TELEPHONY_ARI_PASSWORD", "changed-locally")

    settings = AsteriskTransportSettings()

    assert settings.mode is AsteriskTransportMode.MEDIA_WEBSOCKET
    assert settings.media_websocket_port == 19093
    assert settings.ari_password.get_secret_value() == "changed-locally"


def test_asterisk_transport_settings_accept_typed_mode_argument():
    settings = AsteriskTransportSettings(
        mode=AsteriskTransportMode.ARI_EXTERNAL_MEDIA,
        ari_user="lucy-test",
        ari_password="secret",
    )

    assert settings.mode is AsteriskTransportMode.ARI_EXTERNAL_MEDIA


def test_asterisk_ari_mode_requires_explicit_credentials(monkeypatch):
    monkeypatch.delenv("LUCY_TELEPHONY_ARI_USER", raising=False)
    monkeypatch.delenv("LUCY_TELEPHONY_ARI_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="ARI credentials"):
        AsteriskTransportSettings(mode=AsteriskTransportMode.ARI_EXTERNAL_MEDIA)


@pytest.mark.parametrize("field", ["audio_socket_port", "media_websocket_port"])
@pytest.mark.parametrize("invalid", [0, 65536, True])
def test_asterisk_transport_settings_reject_invalid_listener_ports(field, invalid):
    with pytest.raises(ValidationError):
        AsteriskTransportSettings(**{field: invalid})


@pytest.mark.parametrize(
    "field",
    [
        "audio_socket_host",
        "media_websocket_host",
        "ari_host",
        "external_media_rtp_host",
    ],
)
def test_asterisk_transport_settings_reject_blank_hosts(field):
    with pytest.raises(ValidationError):
        AsteriskTransportSettings(**{field: "  "})
