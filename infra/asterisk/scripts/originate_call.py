from __future__ import annotations

import base64
from collections.abc import Callable
from http.client import HTTPException
import json
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
    _request(
        "POST",
        "/asterisk/variable",
        {"variable": RESULT_VARIABLE, "value": "pending"},
    )
    _request(
        "POST",
        "/channels",
        {
            "endpoint": "Local/lab-check@lucy-lab",
            "extension": "observe",
            "context": "lucy-lab",
            "priority": "1",
            "timeout": str(round(timeout * 1_000)),
            "callerId": "Lucy Lab <1000>",
        },
    )

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        result = _request(
            "GET", "/asterisk/variable", {"variable": RESULT_VARIABLE}
        ).get("value")
        if result == EXPECTED_RESULT:
            print(
                "Lucy telephony lab call completed: WAV playback observed by Asterisk"
            )
            return
        sleep(poll_interval)
    raise SystemExit(f"Call did not reach {EXPECTED_RESULT!r} within {timeout:g}s")


if __name__ == "__main__":
    main()
