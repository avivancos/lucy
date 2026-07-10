"""Credential-safe recorder for real provider fixture scenarios."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lucy.testing.replay import RecordedFrame, mask_volatile

VOLATILE_FIELDS = (
    "request_id",
    "event_id",
    "id",
    "created",
    "session.id",
)
CREDENTIAL_FIELDS = frozenset(
    {
        "authorization",
        "api_key",
        "xi-api-key",
        "xi_api_key",
        "token",
        "access_token",
    }
)


@dataclass(frozen=True)
class RecordingResult:
    frames: List[RecordedFrame]
    artifacts: Dict[str, bytes]


def _scrub_url(value: str, secrets: Set[str]) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.query:
        return value
    clean_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in CREDENTIAL_FIELDS and item not in secrets
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(clean_query),
            parsed.fragment,
        )
    )


def scrub_payload(payload: dict, secrets: Sequence[str]) -> dict:
    secret_values = {secret for secret in secrets if secret}

    def visit(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: visit(item)
                for key, item in value.items()
                if key.casefold() not in CREDENTIAL_FIELDS
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, str):
            if value in secret_values:
                return "<scrubbed>"
            return _scrub_url(value, secret_values)
        return value

    scrubbed = visit(payload)
    assert isinstance(scrubbed, dict)
    return mask_volatile(scrubbed, VOLATILE_FIELDS)


def write_fixture(
    path: Path,
    frames: Iterable[RecordedFrame],
    *,
    secrets: Sequence[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for frame in frames:
        lines.append(
            json.dumps(
                {
                    "direction": frame.direction,
                    "at_ms": frame.at_ms,
                    "payload": scrub_payload(frame.payload, secrets),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _configured_secrets() -> List[str]:
    markers = ("KEY", "TOKEN", "SECRET")
    return [
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in markers)
    ]


async def _run_scenario(plugin: str, scenario_name: str) -> RecordingResult:
    module = importlib.import_module("lucy_%s.recording" % plugin.replace("-", "_"))
    scenarios = getattr(module, "SCENARIOS", {})
    scenario = scenarios.get(scenario_name)
    if scenario is None:
        raise ValueError(
            "unknown recording scenario %r for plugin %r" % (scenario_name, plugin)
        )
    result: Union[RecordingResult, Iterable[RecordedFrame]] = await scenario()
    if hasattr(result, "frames") and hasattr(result, "artifacts"):
        return RecordingResult(
            frames=list(result.frames), artifacts=dict(result.artifacts)
        )
    return RecordingResult(frames=list(result), artifacts={})


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True)
    parser.add_argument(
        "--capability", choices=("stt", "tts", "llm", "realtime"), required=True
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    result = asyncio.run(_run_scenario(args.plugin, args.scenario))
    destination = args.fixture_dir / ("%s_%s.jsonl" % (args.capability, args.scenario))
    write_fixture(destination, result.frames, secrets=_configured_secrets())
    for filename, data in result.artifacts.items():
        (args.fixture_dir / filename).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
