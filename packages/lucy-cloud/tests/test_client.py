import asyncio
import gc
import logging
import threading
import uuid
import weakref

import httpx
import pytest
from fastapi import FastAPI, Response

from lucy_cloud._wire import (
    FLUSH_INTERVAL_S,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    RETRY_AFTER_MAX_S,
    RETRY_BASE_S,
    RETRY_MAX_S,
    build_envelope,
    json_bytes,
)
from lucy_cloud.client import IngestClient
from .helpers import wire_event
from .ingest_app import create_ingest_app, create_status_app


API_KEY = "test-project-key"


class RecordingSleeper:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


class BlockingSleeper:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    async def __call__(self, delay):
        self.started.set()
        await asyncio.to_thread(self.release.wait)


class CloseFailingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return httpx.Response(202, request=request)

    async def aclose(self):
        raise RuntimeError("close failed")


class BlockingCloseTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    async def handle_async_request(self, request):
        return httpx.Response(202, request=request)

    async def aclose(self):
        self.started.set()
        await asyncio.to_thread(self.release.wait)


class CancellationResistantTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    async def handle_async_request(self, request):
        self.started.set()
        try:
            await asyncio.to_thread(self.release.wait)
        except asyncio.CancelledError:
            self.cancelled.set()
            await asyncio.to_thread(self.release.wait)
        return httpx.Response(202, request=request)

    async def aclose(self):
        self.closed.set()


class QueueHandoffClient(IngestClient):
    def __init__(self, *args, **kwargs):
        self.handoff_complete = threading.Event()
        self.release_handoff = threading.Event()
        super().__init__(*args, **kwargs)

    def _take_queued_for_flush(self):
        events = super()._take_queued_for_flush()
        self.handoff_complete.set()
        self.release_handoff.wait()
        return events


class DelayedWorkerClient(IngestClient):
    def __init__(self, *args, **kwargs):
        self.worker_entered = threading.Event()
        self.release_worker = threading.Event()
        super().__init__(*args, **kwargs)

    def _thread_main(self):
        self.worker_entered.set()
        self.release_worker.wait()
        super()._thread_main()


class WorkerStartFailureClient(IngestClient):
    def _start_worker(self):
        raise RuntimeError("worker cannot start")


class WorkerScheduleFailureClient(IngestClient):
    def _start_worker(self):
        if self._loop is None:
            loop = asyncio.new_event_loop()
            loop.close()
            self._loop = loop
        self._ready.set()


class NeverReadyWorkerClient(IngestClient):
    def _start_worker(self):
        return


class CloseTrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.closed = threading.Event()

    async def handle_async_request(self, request):
        return httpx.Response(202, request=request)

    async def aclose(self):
        self.closed.set()


class ManualPacer:
    def __init__(self):
        self.started = threading.Event()
        self._loop = None
        self._release = None
        self.intervals = []

    async def __call__(self, wake, interval):
        self.intervals.append(interval)
        self.started.clear()
        self._loop = asyncio.get_running_loop()
        self._release = asyncio.Event()
        self.started.set()
        wake_task = asyncio.create_task(wake.wait())
        release_task = asyncio.create_task(self._release.wait())
        _, pending = await asyncio.wait(
            {wake_task, release_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def tick(self):
        assert self._loop is not None
        assert self._release is not None
        self._loop.call_soon_threadsafe(self._release.set)


class ManualMonotonic:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _client(app, **kwargs):
    return IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
        **kwargs,
    )


async def test_submit_never_blocks_when_queue_full_drops_and_counts():
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app, max_queue=1, flush_interval_s=60)
    client._submit_wire(wire_event(1))
    client.request_flush()
    assert await asyncio.to_thread(started.wait, 1)
    client._submit_wire(wire_event(2))
    submitter = threading.Thread(
        target=lambda: (client._submit_wire(wire_event(3)), returned.set()),
        daemon=True,
    )
    submitter.start()
    try:
        assert await asyncio.to_thread(returned.wait, 1)
        assert client.dropped_events == 1
    finally:
        release.set()
        submitter.join(timeout=1)
        await client.aclose()


async def test_drop_warning_is_limited_to_once_per_flush_interval(caplog):
    app = create_ingest_app(API_KEY)
    monotonic = ManualMonotonic()
    client = _client(
        app,
        max_queue=1,
        flush_interval_s=60,
        monotonic=monotonic,
    )
    with caplog.at_level(logging.WARNING, logger="lucy_cloud"):
        client._submit_wire(wire_event(1))
        client._submit_wire(wire_event(2))
        client._submit_wire(wire_event(3))
        assert len(caplog.records) == 1
        monotonic.advance(60)
        client._submit_wire(wire_event(4))
    assert len(caplog.records) == 2
    await client.aclose()


async def test_flush_interval_sends_partial_batch():
    app = create_ingest_app(API_KEY)
    pacer = ManualPacer()
    client = _client(app, pace=pacer)
    client._submit_wire(wire_event())
    assert await asyncio.to_thread(pacer.started.wait, 1)
    pacer.tick()
    assert await asyncio.to_thread(app.state.ingest.accepted.wait, 1)
    assert pacer.intervals[0] == FLUSH_INTERVAL_S
    assert len(app.state.ingest.stored_events) == 1
    await client.aclose()


async def test_batch_caps_trigger_immediate_flush():
    app = create_ingest_app(API_KEY)
    client = _client(app, flush_interval_s=60)
    for index in range(MAX_BATCH_EVENTS):
        client._submit_wire(wire_event(index))
    assert await asyncio.to_thread(app.state.ingest.accepted.wait, 1)
    assert len(app.state.ingest.stored_events) == MAX_BATCH_EVENTS
    await client.aclose()


async def test_byte_cap_triggers_immediate_split_flush():
    app = create_ingest_app(API_KEY)
    client = _client(app, flush_interval_s=60)
    client._submit_wire(wire_event(1, payload="a" * 600_000))
    client._submit_wire(wire_event(2, payload="b" * 600_000))
    assert await asyncio.to_thread(app.state.ingest.accepted.wait, 1)
    await client.aclose()
    assert len(app.state.ingest.stored_batches) == 2
    assert len(app.state.ingest.stored_events) == 2


async def test_exact_envelope_byte_cap_triggers_immediate_flush():
    app = create_ingest_app(API_KEY)
    client = _client(app, flush_interval_s=60)
    event = wire_event()
    empty_size = len(
        json_bytes(build_envelope(client.project, client.sdk_version, [event]))
    )
    event["payload"] = "x" * (MAX_BATCH_BYTES - empty_size)
    assert (
        len(json_bytes(build_envelope(client.project, client.sdk_version, [event])))
        == MAX_BATCH_BYTES
    )
    client._submit_wire(event)
    assert await asyncio.to_thread(app.state.ingest.accepted.wait, 1)
    await client.aclose()


async def test_aclose_flushes_remaining_events():
    app = create_ingest_app(API_KEY)
    client = _client(app, flush_interval_s=60)
    client._submit_wire(wire_event())
    await client.aclose()
    assert len(app.state.ingest.stored_events) == 1


async def test_5xx_retries_with_same_idempotency_key():
    app = create_ingest_app(API_KEY, fail_first_n=2)
    client = _client(app, max_retries=2)
    client._submit_wire(wire_event())
    await client.flush()
    assert app.state.ingest.request_count == 3
    assert len(app.state.ingest.idempotency_keys) == 3
    assert len(set(app.state.ingest.idempotency_keys)) == 1
    assert len(app.state.ingest.stored_batches) == 1
    await client.aclose()


async def test_429_honors_retry_after_then_succeeds():
    sleeper = RecordingSleeper()
    app = create_ingest_app(API_KEY, rate_limit_after=0, retry_after="1.25")
    client = _client(app, max_retries=1, sleep=sleeper)
    client._submit_wire(wire_event())
    await client.flush()
    assert app.state.ingest.request_count == 2
    assert len(app.state.ingest.stored_batches) == 1
    assert sleeper.delays == [1.25]
    await client.aclose()


async def test_non_finite_retry_after_falls_back_to_bounded_backoff():
    sleeper = RecordingSleeper()
    app = create_ingest_app(API_KEY, rate_limit_after=0, retry_after="inf")
    client = _client(
        app,
        max_retries=1,
        sleep=sleeper,
        jitter=lambda low, high: high,
    )
    client._submit_wire(wire_event())
    await client.flush()
    assert sleeper.delays == [RETRY_BASE_S]
    await client.aclose()


async def test_large_retry_after_is_clamped():
    sleeper = RecordingSleeper()
    app = create_ingest_app(API_KEY, rate_limit_after=0, retry_after="999")
    client = _client(app, max_retries=1, sleep=sleeper)
    client._submit_wire(wire_event())
    await client.flush()
    assert sleeper.delays == [RETRY_AFTER_MAX_S]
    await client.aclose()


async def test_exponential_backoff_progresses_and_caps():
    sleeper = RecordingSleeper()
    app = create_ingest_app(API_KEY, fail_first_n=6)
    client = _client(
        app,
        max_retries=5,
        sleep=sleeper,
        jitter=lambda low, high: high,
    )
    client._submit_wire(wire_event())
    await client.flush()
    assert sleeper.delays == [
        RETRY_BASE_S,
        RETRY_BASE_S * 2,
        RETRY_BASE_S * 4,
        RETRY_BASE_S * 8,
        RETRY_MAX_S,
    ]
    await client.aclose()


async def test_401_drops_batch_without_retry():
    app = create_ingest_app("different-key")
    client = _client(app, max_retries=5)
    client._submit_wire(wire_event())
    await client.flush()
    assert app.state.ingest.request_count == 1
    assert client.dropped_events == 1
    await client.aclose()


@pytest.mark.parametrize("status_code", [413, 422])
async def test_permanent_payload_errors_drop_without_retry(status_code):
    app = create_status_app(API_KEY, status_code)
    client = _client(app, max_retries=5)
    client._submit_wire(wire_event())
    await client.flush()
    assert app.state.ingest.request_count == 1
    assert client.dropped_events == 1
    await client.aclose()


async def test_unreachable_endpoint_drops_after_max_retries_without_raising():
    client = IngestClient(
        "http://127.0.0.1:1",
        API_KEY,
        project="project-a",
        max_retries=1,
    )
    client._submit_wire(wire_event())
    await client.flush()
    assert client.dropped_events == 1
    await client.aclose()


async def test_real_asgi_exception_is_fail_open_and_counted():
    app = FastAPI()
    app.state.request_count = 0

    @app.post("/v1/events")
    async def fail():
        app.state.request_count += 1
        raise RuntimeError("fixture failure")

    client = _client(app, max_retries=5)
    client._submit_wire(wire_event())
    await client.flush()
    assert app.state.request_count == 1
    assert client.dropped_events == 1
    assert client.queued_events == 0
    await client.aclose()


async def test_cancelled_flush_continues_delivery_without_losing_event():
    sleeper = BlockingSleeper()
    app = create_ingest_app(API_KEY, fail_first_n=1)
    client = _client(app, max_retries=1, sleep=sleeper)
    client._submit_wire(wire_event())
    flush_task = asyncio.create_task(client.flush())
    assert await asyncio.to_thread(sleeper.started.wait, 1)
    flush_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flush_task
    sleeper.release.set()
    assert await asyncio.to_thread(app.state.ingest.accepted.wait, 1)
    assert client.dropped_events == 0
    assert client.queued_events == 0
    await client.aclose()


async def test_concurrent_submit_during_close_is_dropped_not_stranded():
    started = threading.Event()
    release = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app, flush_interval_s=60)
    client._submit_wire(wire_event(1))
    close_task = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(started.wait, 1)
    client._submit_wire(wire_event(2))
    release.set()
    await close_task
    assert client.dropped_events == 1
    assert client.queued_events == 0


async def test_concurrent_close_callers_wait_for_the_same_drain():
    started = threading.Event()
    release = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app, flush_interval_s=60)
    client._submit_wire(wire_event())
    first_close = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(started.wait, 1)
    second_close = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    assert not first_close.done()
    assert not second_close.done()
    release.set()
    await asyncio.gather(first_close, second_close)
    assert client.dropped_events == 0
    assert client.queued_events == 0


async def test_flush_after_close_is_a_safe_noop():
    app = create_ingest_app(API_KEY)
    client = _client(app)
    await client.aclose()
    await client.flush()
    assert client.dropped_events == 0


async def test_aclose_suppresses_transport_cleanup_failure():
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=CloseFailingTransport(),
    )
    client._submit_wire(wire_event())
    await client.aclose()
    assert client.dropped_events == 0
    assert client.api_key == ""
    client_ref = weakref.ref(client)
    del client
    gc.collect()
    assert client_ref() is None


async def test_aclose_wait_is_bounded_while_cleanup_continues():
    transport = BlockingCloseTransport()
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())
    await asyncio.wait_for(client.aclose(), timeout=1)
    assert transport.started.is_set()
    transport.release.set()
    client.close_timeout_s = 1
    await client.aclose()
    assert client.api_key == ""


async def test_aclose_deadline_cancels_stalled_post_and_accounts_batch():
    started = threading.Event()
    release = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app, close_timeout_s=0.01)
    client._submit_wire(wire_event())
    close_task = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(started.wait, 1)
    try:
        await asyncio.wait_for(close_task, timeout=1)
        assert client.api_key == ""
        assert client.queued_events == 0
        assert client.dropped_events == 1
    finally:
        release.set()


async def test_concurrent_aclose_deadline_is_fail_open_for_both_callers():
    started = threading.Event()
    release = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app, close_timeout_s=0.01)
    client._submit_wire(wire_event())
    first_close = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(started.wait, 1)
    client.close_timeout_s = 1
    second_close = asyncio.create_task(client.aclose())
    try:
        results = await asyncio.gather(
            first_close,
            second_close,
            return_exceptions=True,
        )
        assert results == [None, None]
        assert client.api_key == ""
        assert client.queued_events == 0
        assert client.dropped_events == 1
    finally:
        release.set()


async def test_aclose_accounts_inflight_when_transport_ignores_cancellation():
    transport = CancellationResistantTransport()
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())
    close_task = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(transport.started.wait, 1)
    try:
        await asyncio.wait_for(close_task, timeout=1)
        assert await asyncio.to_thread(transport.cancelled.wait, 1)
        assert client.api_key == ""
        assert client.dropped_events == 1
        assert client._thread is not None
        assert client._thread.is_alive()
    finally:
        transport.release.set()
    await asyncio.to_thread(client._thread.join, 1)
    assert not client._thread.is_alive()
    assert transport.closed.is_set()


def test_close_deadline_accounts_stalled_inflight_and_clears_secret():
    transport = CancellationResistantTransport()
    client = IngestClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())
    returned = threading.Event()

    def close_client():
        client.close()
        returned.set()

    close_thread = threading.Thread(target=close_client)
    close_thread.start()
    assert transport.started.wait(1)
    try:
        assert returned.wait(1)
        close_thread.join(1)
        assert not close_thread.is_alive()
        assert transport.cancelled.wait(1)
        assert client.api_key == ""
        assert client.queued_events == 0
        assert client.dropped_events == 1
        assert client._thread is not None
        assert client._thread.is_alive()
    finally:
        transport.release.set()
    assert transport.closed.wait(1)
    assert client._thread is not None
    client._thread.join(1)
    assert not client._thread.is_alive()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example",
        "ftp://remote.example",
        "https://user:secret@remote.example",
    ],
)
def test_endpoint_rejects_plaintext_remote_or_embedded_credentials(endpoint):
    with pytest.raises(ValueError, match="endpoint"):
        IngestClient(endpoint, API_KEY, project="project-a")


def test_remote_plaintext_is_rejected_even_with_explicit_http_transport():
    transport = httpx.AsyncHTTPTransport()
    with pytest.raises(ValueError, match="endpoint"):
        IngestClient(
            "http://remote.example",
            API_KEY,
            project="project-a",
            transport=transport,
        )


async def test_close_timeout_cannot_race_atomic_queue_handoff():
    app = create_ingest_app(API_KEY)
    client = QueueHandoffClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=httpx.ASGITransport(app=app),
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())
    close_task = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(client.handoff_complete.wait, 1)
    try:
        await asyncio.wait_for(close_task, timeout=1)
        assert client.api_key == ""
        assert client.queued_events == 0
        assert client.dropped_events == 1
        assert app.state.ingest.request_count == 0
    finally:
        client.release_handoff.set()


async def test_worker_readiness_wait_does_not_block_caller_event_loop():
    transport = CloseTrackingTransport()
    client = DelayedWorkerClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.5,
    )
    client._submit_wire(wire_event())
    assert client.worker_entered.wait(1)
    order = []

    async def release_from_event_loop():
        await asyncio.sleep(0)
        order.append("heartbeat")
        client.release_worker.set()

    release_task = asyncio.create_task(release_from_event_loop())
    await client.aclose()
    order.append("closed")
    await release_task
    assert order == ["heartbeat", "closed"]
    assert transport.closed.is_set()


async def test_worker_start_timeout_is_terminal_and_closes_transport():
    transport = CloseTrackingTransport()
    client = DelayedWorkerClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())
    assert client.worker_entered.wait(1)
    await client.aclose()
    assert client.api_key == ""
    assert client.dropped_events == 1
    client.release_worker.set()
    assert client._thread is not None
    await asyncio.to_thread(client._thread.join, 1)
    assert not client._thread.is_alive()
    assert transport.closed.is_set()


def test_sync_close_is_fail_open_after_worker_start_failure():
    transport = CloseTrackingTransport()
    client = WorkerStartFailureClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
    )

    client._submit_wire(wire_event())
    client.close()

    assert client.queued_events == 0
    assert client.dropped_events == 1
    assert client.api_key == ""
    assert client._closed is True
    assert transport.closed.wait(1)


async def test_async_close_is_fail_open_after_worker_start_failure():
    transport = CloseTrackingTransport()
    client = WorkerStartFailureClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
    )

    client._submit_wire(wire_event())
    await client.aclose()

    assert client.queued_events == 0
    assert client.dropped_events == 1
    assert client.api_key == ""
    assert client._closed is True
    assert transport.closed.is_set()


@pytest.mark.parametrize("close_mode", ["sync", "async"])
async def test_close_is_fail_open_after_worker_scheduling_failure(close_mode):
    transport = CloseTrackingTransport()
    client = WorkerScheduleFailureClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
    )
    client._submit_wire(wire_event())

    if close_mode == "sync":
        client.close()
    else:
        await client.aclose()

    assert client.queued_events == 0
    assert client.dropped_events == 1
    assert client.api_key == ""
    assert client._closed is True
    assert transport.closed.wait(1)


@pytest.mark.parametrize("close_mode", ["sync", "async"])
async def test_close_cleans_transport_when_worker_never_becomes_ready(close_mode):
    transport = CloseTrackingTransport()
    client = NeverReadyWorkerClient(
        "http://127.0.0.1",
        API_KEY,
        project="project-a",
        transport=transport,
        close_timeout_s=0.01,
    )
    client._submit_wire(wire_event())

    if close_mode == "sync":
        client.close()
    else:
        await client.aclose()

    assert client.queued_events == 0
    assert client.dropped_events == 1
    assert client.api_key == ""
    assert client._closed is True
    assert transport.closed.wait(1)


async def test_caller_cancellation_of_aclose_still_propagates():
    started = threading.Event()
    release = threading.Event()
    app = FastAPI()

    @app.post("/v1/events")
    async def stalled():
        started.set()
        await asyncio.to_thread(release.wait)
        return Response(status_code=202)

    client = _client(app)
    client._submit_wire(wire_event())
    close_task = asyncio.create_task(client.aclose())
    assert await asyncio.to_thread(started.wait, 1)
    close_task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await close_task
    finally:
        release.set()
        await client.aclose()


async def test_each_planned_batch_gets_a_distinct_uuid_key():
    app = create_ingest_app(API_KEY)
    client = _client(app, flush_interval_s=60)
    for index in range(MAX_BATCH_EVENTS + 1):
        client._submit_wire(wire_event(index))
    await client.flush()
    assert len(app.state.ingest.idempotency_keys) == 2
    assert len(set(app.state.ingest.idempotency_keys)) == 2
    assert all(uuid.UUID(key).version == 4 for key in app.state.ingest.idempotency_keys)
    await client.aclose()
