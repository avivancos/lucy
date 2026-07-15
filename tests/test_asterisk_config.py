from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "infra/asterisk/scripts/render_config.py"
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SCRIPT_SPEC = importlib.util.spec_from_file_location("render_config", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
render_config = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(render_config)


VALID_SETTINGS = {
    "LUCY_TELEPHONY_ARI_PORT": "8088",
    "LUCY_TELEPHONY_ARI_USER": "lucy-lab",
    "LUCY_TELEPHONY_ARI_PASSWORD": "lucy-lab-only",
    "LUCY_TELEPHONY_PJSIP_PORT": "5060",
    "LUCY_TELEPHONY_PJSIP_USER": "lucy-lab",
    "LUCY_TELEPHONY_PJSIP_PASSWORD": "lucy-lab-only",
    "LUCY_TELEPHONY_RTP_PORT_START": "10000",
    "LUCY_TELEPHONY_RTP_PORT_END": "10019",
    "LUCY_TELEPHONY_AUDIO_SOCKET_HOST": "lucy-media-gateway",
    "LUCY_TELEPHONY_AUDIO_SOCKET_PORT": "9092",
    "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS": "15",
}


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name, value in VALID_SETTINGS.items():
        monkeypatch.setenv(name, overrides.get(name, value))


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("LUCY_TELEPHONY_ARI_USER", "valid\n[injected]"),
        ("LUCY_TELEPHONY_PJSIP_PASSWORD", "valid\rmalicious"),
        ("LUCY_TELEPHONY_AUDIO_SOCKET_HOST", "gateway;include bad.conf"),
        ("LUCY_TELEPHONY_ARI_PORT", "70000"),
        ("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "0"),
        ("LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS", "inf"),
    ],
)
def test_renderer_rejects_unsafe_settings_without_echoing_values(
    monkeypatch, setting, value
):
    _configure(monkeypatch, **{setting: value})

    with pytest.raises(SystemExit) as error:
        render_config._settings()

    assert setting in str(error.value)
    assert value not in str(error.value)


def test_renderer_rejects_reversed_rtp_range(monkeypatch):
    _configure(
        monkeypatch,
        LUCY_TELEPHONY_RTP_PORT_START="10019",
        LUCY_TELEPHONY_RTP_PORT_END="10000",
    )

    with pytest.raises(SystemExit, match="LUCY_TELEPHONY_RTP_PORT_START"):
        render_config._settings()


def test_renderer_writes_only_validated_values(monkeypatch, tmp_path):
    _configure(monkeypatch)
    monkeypatch.setattr(
        render_config,
        "SOURCE_DIRECTORY",
        SCRIPT_PATH.parent.parent / "config",
    )
    monkeypatch.setattr(render_config, "TARGET_DIRECTORY", tmp_path)

    render_config._render_configuration(render_config._settings())

    ari_config = (tmp_path / "ari.conf").read_text(encoding="utf-8")
    assert "[lucy-lab]" in ari_config
    assert "password = lucy-lab-only" in ari_config
    assert "[injected]" not in ari_config
