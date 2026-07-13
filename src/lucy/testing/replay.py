"""Strict deterministic replay for recorded provider wire fixtures."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from lucy.privacy import (
    configured_secret_values,
    contains_configured_secret,
    is_secret_key,
    scrub_configured_secrets,
    scrub_secrets,
)

DEFAULT_VOLATILE_FIELDS = (
    "request_id",
    "event_id",
    "id",
    "created",
    "session.id",
)


@dataclass(frozen=True)
class RecordedFrame:
    direction: str
    at_ms: int
    payload: dict


class _RecordedFrames(list):
    def __init__(self, frames: Iterable[RecordedFrame], path: Path) -> None:
        super().__init__(frames)
        self.path = path


class FixtureMismatch(AssertionError):
    """Raised when live transport calls diverge from a recording."""


def load_fixture(path: Path) -> List[RecordedFrame]:
    frames = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        parsed = json.loads(raw)
        direction = parsed.get("direction")
        payload = parsed.get("payload")
        if direction not in {"sent", "received"} or not isinstance(payload, dict):
            raise ValueError("invalid recorded frame at %s:%d" % (path, line_number))
        frames.append(
            RecordedFrame(
                direction=direction,
                at_ms=int(parsed["at_ms"]),
                payload=payload,
            )
        )
    return _RecordedFrames(frames, path)


def mask_volatile(payload: dict, fields: Tuple[str, ...]) -> dict:
    targets = set(fields)

    def visit(value: object, path: Tuple[str, ...]) -> object:
        if isinstance(value, dict):
            masked: Dict[str, object] = {}
            for key, item in value.items():
                child_path = (*path, key)
                if key in targets or ".".join(child_path) in targets:
                    masked[key] = "<masked>"
                else:
                    masked[key] = visit(item, child_path)
            return masked
        if isinstance(value, list):
            return [visit(item, path) for item in value]
        return value

    result = visit(payload, ())
    assert isinstance(result, dict)
    return result


class ReplayTransport:
    def __init__(
        self, frames: List[RecordedFrame], *, secrets: Sequence[str] = ()
    ) -> None:
        self._frames = list(frames)
        self._path = getattr(frames, "path", Path("<memory>"))
        self._index = 0
        self._secrets = tuple(set(secrets).union(configured_secret_values(os.environ)))

    async def send(self, payload: dict) -> None:
        frame = self._next("sent", payload)
        expected = _comparable_payload(frame.payload, self._secrets)
        actual = _comparable_payload(payload, self._secrets)
        if actual != expected:
            raise self._mismatch(expected, actual)
        self._index += 1

    async def receive(self) -> dict:
        frame = self._next("received", None)
        self._index += 1
        return dict(frame.payload)

    def _next(self, direction: str, actual: object) -> RecordedFrame:
        if self._index >= len(self._frames):
            raise self._mismatch("<end-of-fixture>", actual)
        frame = self._frames[self._index]
        if frame.direction != direction:
            raise self._mismatch(
                {"expected_direction": frame.direction, "payload": frame.payload},
                {"actual_direction": direction, "payload": actual},
            )
        return frame

    def _mismatch(self, expected: object, actual: object) -> FixtureMismatch:
        return FixtureMismatch(
            "%s frame %d mismatch: expected %r, actual %r"
            % (
                self._path,
                self._index,
                _scrub_diagnostic(expected, self._secrets),
                _scrub_diagnostic(actual, self._secrets),
            )
        )


def _comparable_payload(payload: dict, secrets: Sequence[str]) -> dict:
    stripped = _scrub_diagnostic(payload, secrets)
    assert isinstance(stripped, dict)
    return mask_volatile(stripped, DEFAULT_VOLATILE_FIELDS)


def _scrub_diagnostic(value: object, secrets: Sequence[str]) -> object:
    if isinstance(value, dict):
        return {
            key: _scrub_diagnostic(item, secrets)
            for key, item in value.items()
            if not is_secret_key(key) and not contains_configured_secret(key, secrets)
        }
    if isinstance(value, list):
        return [_scrub_diagnostic(item, secrets) for item in value]
    if isinstance(value, str):
        return scrub_configured_secrets(scrub_secrets(value), secrets)
    return value


class StalledTransport:
    async def send(self, payload: dict) -> None:
        return None

    async def receive(self) -> dict:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")
