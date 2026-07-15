from __future__ import annotations

import os
from pathlib import Path
import shutil
from string import Template
import sys

from telephony_settings import (
    MAX_CALL_TIMEOUT_SECONDS,
    invalid_setting,
    validated_float,
    validated_host,
    validated_identifier,
    validated_port,
    validated_secret,
)


SOURCE_DIRECTORY = Path("/opt/lucy-asterisk/config")
TARGET_DIRECTORY = Path("/etc/asterisk")
REQUIRED_SETTINGS = (
    "LUCY_TELEPHONY_ARI_PORT",
    "LUCY_TELEPHONY_ARI_USER",
    "LUCY_TELEPHONY_ARI_PASSWORD",
    "LUCY_TELEPHONY_PJSIP_PORT",
    "LUCY_TELEPHONY_PJSIP_USER",
    "LUCY_TELEPHONY_PJSIP_PASSWORD",
    "LUCY_TELEPHONY_RTP_PORT_START",
    "LUCY_TELEPHONY_RTP_PORT_END",
    "LUCY_TELEPHONY_AUDIO_SOCKET_HOST",
    "LUCY_TELEPHONY_AUDIO_SOCKET_PORT",
    "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS",
)
PORT_SETTINGS = (
    "LUCY_TELEPHONY_ARI_PORT",
    "LUCY_TELEPHONY_PJSIP_PORT",
    "LUCY_TELEPHONY_RTP_PORT_START",
    "LUCY_TELEPHONY_RTP_PORT_END",
    "LUCY_TELEPHONY_AUDIO_SOCKET_PORT",
)
IDENTIFIER_SETTINGS = (
    "LUCY_TELEPHONY_ARI_USER",
    "LUCY_TELEPHONY_PJSIP_USER",
)
SECRET_SETTINGS = (
    "LUCY_TELEPHONY_ARI_PASSWORD",
    "LUCY_TELEPHONY_PJSIP_PASSWORD",
)


def _validate_settings(settings: dict[str, str]) -> None:
    ports: dict[str, int] = {}
    for name in PORT_SETTINGS:
        ports[name] = validated_port(name, settings[name])

    if ports["LUCY_TELEPHONY_RTP_PORT_START"] > ports["LUCY_TELEPHONY_RTP_PORT_END"]:
        invalid_setting("LUCY_TELEPHONY_RTP_PORT_START")

    for name in IDENTIFIER_SETTINGS:
        validated_identifier(name, settings[name])
    for name in SECRET_SETTINGS:
        validated_secret(name, settings[name])
    host_name = "LUCY_TELEPHONY_AUDIO_SOCKET_HOST"
    validated_host(host_name, settings[host_name])

    timeout_name = "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS"
    validated_float(
        timeout_name,
        settings[timeout_name],
        minimum=0.0,
        maximum=MAX_CALL_TIMEOUT_SECONDS,
        minimum_inclusive=False,
    )


def _settings() -> dict[str, str]:
    missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required telephony settings: {joined}")
    settings = {name: os.environ[name] for name in REQUIRED_SETTINGS}
    _validate_settings(settings)
    return settings


def _render_configuration(settings: dict[str, str]) -> None:
    TARGET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for source in SOURCE_DIRECTORY.iterdir():
        if source.suffix == ".template":
            target = TARGET_DIRECTORY / source.stem
            rendered = Template(source.read_text(encoding="utf-8")).substitute(settings)
            target.write_text(rendered, encoding="utf-8")
        elif source.suffix == ".conf":
            shutil.copyfile(source, TARGET_DIRECTORY / source.name)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Asterisk command is required")
    _render_configuration(_settings())
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
