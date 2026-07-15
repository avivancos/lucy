from __future__ import annotations

import math
import os
import re


IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
SECRET_PATTERN = re.compile(r"[A-Za-z0-9_.!@%+=:-]{1,128}\Z")
HOST_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,253}\Z")
MAX_CALL_TIMEOUT_SECONDS = 300.0


def invalid_setting(name: str) -> None:
    raise SystemExit(f"Invalid telephony setting: {name}")


def required_setting(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required telephony setting: {name}")
    return value


def validated_port(name: str, value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        invalid_setting(name)
    if not 1 <= port <= 65_535:
        invalid_setting(name)
    return port


def validated_identifier(name: str, value: str) -> str:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        invalid_setting(name)
    return value


def validated_secret(name: str, value: str) -> str:
    if SECRET_PATTERN.fullmatch(value) is None:
        invalid_setting(name)
    return value


def validated_host(name: str, value: str) -> str:
    if HOST_PATTERN.fullmatch(value) is None:
        invalid_setting(name)
    return value


def validated_float(
    name: str,
    value: str,
    *,
    minimum: float,
    maximum: float,
    minimum_inclusive: bool = True,
) -> float:
    try:
        number = float(value)
    except ValueError:
        invalid_setting(name)
    above_minimum = number >= minimum if minimum_inclusive else number > minimum
    if not math.isfinite(number) or not above_minimum or number > maximum:
        invalid_setting(name)
    return number
