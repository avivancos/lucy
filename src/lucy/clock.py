"""Injectable clock (ADR 0011): all runtime pacing goes through a ``Clock`` so
tests advance time deterministically instead of sleeping on the wall clock.
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Protocol, Tuple, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float:
        """Seconds since an arbitrary epoch; only differences are meaningful."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class MonotonicClock:
    """Real clock backed by ``time.monotonic`` and ``asyncio.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class ManualClock:
    """Virtual clock: time only moves when :meth:`advance` is called, which
    resolves every pending :meth:`sleep` whose deadline has been reached, in
    deadline order. No wall time is ever consumed."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._sleepers: List[Tuple[float, "asyncio.Future[None]"]] = []

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        future: "asyncio.Future[None]" = asyncio.get_event_loop().create_future()
        self._sleepers.append((self._now + seconds, future))
        await future

    def advance(self, ms: float) -> None:
        """Advance virtual time by ``ms`` milliseconds, waking due sleepers."""
        self._now += ms / 1000.0
        due = sorted(
            (item for item in self._sleepers if item[0] <= self._now),
            key=lambda item: item[0],
        )
        self._sleepers = [item for item in self._sleepers if item[0] > self._now]
        for _, future in due:
            if not future.done():
                future.set_result(None)
