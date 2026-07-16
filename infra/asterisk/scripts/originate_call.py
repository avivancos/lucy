from __future__ import annotations

import base64
from collections.abc import Callable
from http.client import HTTPException
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telephony_settings import (
    MAX_CALL_TIMEOUT_SECONDS,
    required_setting,
    validated_float,
    validated_host,
    validated_identifier,
    validated_port,
    validated_secret,
)


RESULT_VARIABLE = "LUCY_LAB_RESULT"
EXPECTED_RESULT = "completed"
MAX_POLL_INTERVAL_SECONDS = 10.0
MAX_ARI_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_TEST_EXTENSION = "lab-check"
ADAPTER_TEST_EXTENSION = "lucy-audiosocket"
ADAPTER_PLAYBACK_EXTENSION = "play-fixture"
GATEWAY_METRICS = (
    "asterisk_sessions_started",
    "asterisk_sessions_completed",
    "asterisk_sessions_failed",
    "asterisk_audio_bytes_received",
    "asterisk_audio_bytes_sent",
    "control_messages_forwarded",
)


def _bounded_float(
    name: str, *, minimum: float, maximum: float, minimum_inclusive: bool = True
) -> float:
    return validated_float(
        name,
        required_setting(name),
        minimum=minimum,
        maximum=maximum,
        minimum_inclusive=minimum_inclusive,
    )


def _request(method: str, path: str, query: dict[str, str]) -> dict[str, object]:
    host_name = "LUCY_TELEPHONY_ARI_HOST"
    port_name = "LUCY_TELEPHONY_ARI_PORT"
    user_name = "LUCY_TELEPHONY_ARI_USER"
    password_name = "LUCY_TELEPHONY_ARI_PASSWORD"
    host = validated_host(host_name, required_setting(host_name))
    port = validated_port(port_name, required_setting(port_name))
    username = validated_identifier(user_name, required_setting(user_name))
    password = validated_secret(password_name, required_setting(password_name))
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    url = f"http://{host}:{port}/ari{path}?{urlencode(query)}"
    request = Request(url, data=b"" if method == "POST" else None, method=method)
    request.add_header("Authorization", f"Basic {credentials}")
    request_timeout = _bounded_float(
        "LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS",
        minimum=0.0,
        maximum=MAX_ARI_REQUEST_TIMEOUT_SECONDS,
        minimum_inclusive=False,
    )
    try:
        with urlopen(  # noqa: S310 - host is explicit lab config.
            request, timeout=request_timeout
        ) as response:
            payload = response.read()
    except HTTPError as error:
        raise SystemExit(
            f"ARI request {method} {path} failed with HTTP {error.code}"
        ) from None
    except TimeoutError:
        raise SystemExit(
            f"ARI request {method} {path} timed out after {request_timeout:g}s"
        ) from None
    except URLError as error:
        raise SystemExit(
            f"ARI request {method} {path} failed: {error.reason}"
        ) from None
    except (HTTPException, OSError):
        raise SystemExit(
            f"ARI request {method} {path} failed due to a transport error"
        ) from None
    if not payload:
        return {}
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(f"ARI request {method} {path} returned invalid JSON") from None
    if not isinstance(result, dict):
        raise SystemExit(f"ARI request {method} {path} returned a non-object payload")
    return result


def _gateway_health() -> dict[str, int]:
    host_name = "LUCY_GATEWAY_HEALTH_HOST"
    port_name = "LUCY_GATEWAY_HEALTH_PORT"
    host = validated_host(host_name, required_setting(host_name))
    port = validated_port(port_name, required_setting(port_name))
    request_timeout = _bounded_float(
        "LUCY_TELEPHONY_ARI_REQUEST_TIMEOUT_SECONDS",
        minimum=0.0,
        maximum=MAX_ARI_REQUEST_TIMEOUT_SECONDS,
        minimum_inclusive=False,
    )
    request = Request(f"http://{host}:{port}/health", method="GET")
    try:
        with urlopen(  # noqa: S310 - host is explicit local gateway config.
            request, timeout=request_timeout
        ) as response:
            payload = response.read()
    except HTTPError as error:
        raise SystemExit(f"Gateway health failed with HTTP {error.code}") from None
    except TimeoutError:
        raise SystemExit(
            f"Gateway health timed out after {request_timeout:g}s"
        ) from None
    except URLError as error:
        raise SystemExit(f"Gateway health failed: {error.reason}") from None
    except (HTTPException, OSError):
        raise SystemExit("Gateway health failed due to a transport error") from None
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit("Gateway health returned invalid JSON") from None
    if not isinstance(result, dict):
        raise SystemExit("Gateway health returned a non-object payload")
    if result.get("service") != "lucy-media-gateway" or result.get("status") != "ok":
        raise SystemExit("Gateway health returned an unexpected service status")
    metrics: dict[str, int] = {}
    for name in GATEWAY_METRICS:
        value = result.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SystemExit(f"Gateway health returned an invalid {name} metric")
        metrics[name] = value
    return metrics


def _adapter_completed(baseline: dict[str, int], current: dict[str, int]) -> bool:
    return (
        current["asterisk_sessions_started"] > baseline["asterisk_sessions_started"]
        and current["asterisk_sessions_completed"]
        > baseline["asterisk_sessions_completed"]
        and current["asterisk_audio_bytes_received"]
        > baseline["asterisk_audio_bytes_received"]
        and current["asterisk_audio_bytes_sent"]
        > baseline["asterisk_audio_bytes_sent"]
        and current["control_messages_forwarded"]
        >= baseline["control_messages_forwarded"] + 2
        and current["asterisk_sessions_failed"] == baseline["asterisk_sessions_failed"]
    )


def main(
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    timeout = _bounded_float(
        "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS",
        minimum=0.0,
        maximum=MAX_CALL_TIMEOUT_SECONDS,
        minimum_inclusive=False,
    )
    poll_interval = _bounded_float(
        "LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS",
        minimum=0.0,
        maximum=MAX_POLL_INTERVAL_SECONDS,
    )
    if timeout > 0 and poll_interval > timeout:
        raise SystemExit(
            "LUCY_TELEPHONY_CALL_POLL_INTERVAL_SECONDS must not exceed "
            "LUCY_TELEPHONY_CALL_TIMEOUT_SECONDS"
        )
    extension = validated_identifier(
        "LUCY_TELEPHONY_TEST_EXTENSION",
        os.environ.get("LUCY_TELEPHONY_TEST_EXTENSION", DEFAULT_TEST_EXTENSION),
    )
    if extension not in {DEFAULT_TEST_EXTENSION, ADAPTER_TEST_EXTENSION}:
        raise SystemExit("Unsupported LUCY_TELEPHONY_TEST_EXTENSION")
    adapter_baseline = (
        _gateway_health() if extension == ADAPTER_TEST_EXTENSION else None
    )
    if extension == DEFAULT_TEST_EXTENSION:
        _request(
            "POST",
            "/asterisk/variable",
            {"variable": RESULT_VARIABLE, "value": "pending"},
        )
    _request(
        "POST",
        "/channels",
        {
            "endpoint": f"Local/{extension}@lucy-lab",
            "extension": (
                ADAPTER_PLAYBACK_EXTENSION
                if extension == ADAPTER_TEST_EXTENSION
                else "observe"
            ),
            "context": "lucy-lab",
            "priority": "1",
            "timeout": str(round(timeout * 1_000)),
            "callerId": "Lucy Lab <1000>",
        },
    )

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if adapter_baseline is not None:
            current = _gateway_health()
            if _adapter_completed(adapter_baseline, current):
                audio_bytes = (
                    current["asterisk_audio_bytes_received"]
                    - adapter_baseline["asterisk_audio_bytes_received"]
                )
                control_messages = (
                    current["control_messages_forwarded"]
                    - adapter_baseline["control_messages_forwarded"]
                )
                outbound_audio_bytes = (
                    current["asterisk_audio_bytes_sent"]
                    - adapter_baseline["asterisk_audio_bytes_sent"]
                )
                print(
                    "Lucy AudioSocket adapter completed: "
                    f"{audio_bytes} inbound audio bytes, "
                    f"{outbound_audio_bytes} outbound audio bytes, "
                    f"{control_messages} control messages"
                )
                return
            sleep(poll_interval)
            continue
        result = _request(
            "GET", "/asterisk/variable", {"variable": RESULT_VARIABLE}
        ).get("value")
        if result == EXPECTED_RESULT:
            print(
                "Lucy telephony lab call completed: WAV playback observed by Asterisk"
            )
            return
        sleep(poll_interval)
    if adapter_baseline is not None:
        raise SystemExit(f"AudioSocket adapter did not complete within {timeout:g}s")
    raise SystemExit(f"Call did not reach {EXPECTED_RESULT!r} within {timeout:g}s")


if __name__ == "__main__":
    main()
