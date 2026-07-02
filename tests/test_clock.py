import asyncio

from lucy.clock import ManualClock, MonotonicClock


async def test_manual_advance_resolves_a_pending_sleep_without_wall_time():
    clock = ManualClock()
    done: list[float] = []

    async def waiter():
        await clock.sleep(1.5)
        done.append(clock.monotonic())

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let the waiter register its sleep (a yield, not wall time)
    assert done == []  # nothing resolves until the clock advances

    clock.advance(1500)  # 1.5 s of virtual time
    await task
    assert done == [1.5]


async def test_two_sleeps_resolve_in_deadline_order():
    clock = ManualClock()
    order: list[str] = []

    async def w(name: str, secs: float):
        await clock.sleep(secs)
        order.append(name)

    later = asyncio.create_task(w("b", 2.0))
    sooner = asyncio.create_task(w("a", 1.0))
    await asyncio.sleep(0)

    clock.advance(1000)
    await asyncio.sleep(0)
    assert order == ["a"]  # only the 1 s sleeper fired

    clock.advance(1000)
    await asyncio.gather(sooner, later)
    assert order == ["a", "b"]


async def test_zero_sleep_returns_immediately():
    clock = ManualClock()
    await clock.sleep(0)  # must not block or need an advance
    assert clock.monotonic() == 0.0


async def test_monotonic_clock_is_real_and_nondecreasing():
    clock = MonotonicClock()
    t0 = clock.monotonic()
    await clock.sleep(0)
    assert clock.monotonic() >= t0
