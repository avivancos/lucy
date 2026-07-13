"""Internal bounded, fail-open transport for privacy-filtered telemetry."""

from __future__ import annotations

import asyncio
import atexit
import logging
import math
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from typing import Awaitable, Callable, Deque, List, Optional
from urllib.parse import urlsplit

import httpx

from lucy.privacy import contains_sensitive_text

from lucy_cloud._wire import (
    ACCEPTED_STATUS,
    CLOSE_TIMEOUT_S,
    DEFAULT_MAX_QUEUE,
    DEFAULT_MAX_RETRIES,
    EVENTS_PATH,
    FLUSH_INTERVAL_S,
    HEADER_API_KEY,
    HEADER_IDEMPOTENCY,
    HEADER_RETRY_AFTER,
    LOGGER_NAME,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    RATE_LIMITED_STATUS,
    RETRY_AFTER_MAX_S,
    RETRY_BASE_S,
    RETRY_MAX_S,
    WORKER_START_TIMEOUT_S,
    build_envelope,
    encode_batch,
    json_bytes,
    plan_batches,
)

_RETRYABLE_SERVER_STATUS_MIN = 500
_RETRYABLE_SERVER_STATUS_MAX = 599
Sleeper = Callable[[float], Awaitable[None]]
Jitter = Callable[[float, float], float]
Pacer = Callable[[asyncio.Event, float], Awaitable[None]]
Monotonic = Callable[[], float]


class _WorkerUnavailable(RuntimeError):
    """Signal that bounded shutdown cannot be scheduled on the worker."""


async def _default_pace(wake: asyncio.Event, interval_s: float) -> None:
    try:
        await asyncio.wait_for(wake.wait(), timeout=interval_s)
    except asyncio.TimeoutError:
        return


def _lucy_version() -> str:
    try:
        return version("lucy")
    except PackageNotFoundError:
        return "unknown"


def _validated_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("endpoint must be a valid HTTP URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must be a credential-free HTTP URL")
    loopback = hostname == "localhost"
    if not loopback:
        try:
            loopback = ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme != "https" and not loopback:
        raise ValueError("endpoint must use HTTPS outside loopback hosts")
    return endpoint.rstrip("/")


def _validated_project(project: str) -> str:
    if not project or contains_sensitive_text(project):
        raise ValueError("project must be a nonempty opaque identifier")
    return project


class IngestClient:
    """Transport used by ``CloudTraceExporter`` after core privacy filtering."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        project: str,
        max_queue: int = DEFAULT_MAX_QUEUE,
        flush_interval_s: float = FLUSH_INTERVAL_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Sleeper = asyncio.sleep,
        jitter: Jitter = random.uniform,
        close_timeout_s: float = CLOSE_TIMEOUT_S,
        pace: Pacer = _default_pace,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        if not endpoint or not api_key:
            raise ValueError("endpoint, api_key, and project are required")
        if (
            max_queue < 1
            or flush_interval_s <= 0
            or max_retries < 0
            or close_timeout_s <= 0
        ):
            raise ValueError("queue, flush interval, and retries must be valid")
        self.endpoint = _validated_endpoint(endpoint)
        self.api_key = api_key
        self.project = _validated_project(project)
        self.max_queue = max_queue
        self.flush_interval_s = flush_interval_s
        self.max_retries = max_retries
        self.close_timeout_s = close_timeout_s
        self.sdk_version = _lucy_version()
        self._sleep = sleep
        self._jitter = jitter
        self._pace = pace
        self._monotonic = monotonic
        self._queue: Deque[dict] = deque()
        self._empty_envelope_bytes = len(
            json_bytes(build_envelope(self.project, self.sdk_version, []))
        )
        self._queued_bytes = self._empty_envelope_bytes
        self._state_lock = threading.Lock()
        self._drop_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        self._flush_lock: Optional[asyncio.Lock] = None
        self._thread: Optional[threading.Thread] = None
        self._flush_requested = False
        self._stop_requested = False
        self._closing = False
        self._closed = False
        self._close_future: Optional[Future[None]] = None
        self._inflight_count = 0
        self._inflight_abandoned = False
        self._dropped_events = 0
        self._last_drop_warning_at = float("-inf")
        self._logger = logging.getLogger(LOGGER_NAME)
        self._http = httpx.AsyncClient(transport=transport)
        self._atexit_callback = self.close
        atexit.register(self._atexit_callback)

    @property
    def dropped_events(self) -> int:
        with self._drop_lock:
            return self._dropped_events

    @property
    def queued_events(self) -> int:
        with self._state_lock:
            return len(self._queue)

    def _submit_wire(self, event: dict) -> None:
        queued = False
        try:
            candidate = dict(event)
            event_bytes = len(json_bytes(candidate))
            with self._state_lock:
                if self._closing or self._closed or len(self._queue) >= self.max_queue:
                    self._record_drop(1)
                    return
                if self._empty_envelope_bytes + event_bytes > MAX_BATCH_BYTES:
                    self._record_drop(1)
                    return
                separator_bytes = 1 if self._queue else 0
                self._queue.append(candidate)
                queued = True
                self._queued_bytes += separator_bytes + event_bytes
                should_flush = (
                    len(self._queue) >= MAX_BATCH_EVENTS
                    or self._queued_bytes >= MAX_BATCH_BYTES
                )
            self._start_worker()
            if should_flush:
                self.request_flush()
        except Exception:
            if queued:
                self._drop_queued()
            else:
                self._record_drop(1)

    def request_flush(self) -> None:
        self._flush_requested = True
        self._start_worker()
        with self._state_lock:
            if self._closing or self._closed:
                return
            loop = self._loop
            wake = self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                return

    async def flush(self) -> None:
        with self._state_lock:
            if self._closing or self._closed:
                return
        self._start_worker()
        ready = await asyncio.to_thread(self._ready.wait, WORKER_START_TIMEOUT_S)
        if not ready:
            self._drop_queued()
            return
        with self._state_lock:
            loop = self._loop
            if self._closing or self._closed or loop is None or loop.is_closed():
                return
            coroutine = self._flush_on_worker()
            try:
                future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            except RuntimeError:
                coroutine.close()
                return
        await asyncio.shield(asyncio.wrap_future(future))

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.close_timeout_s
        try:
            future = await asyncio.to_thread(
                self._begin_close,
                max(0.0, deadline - loop.time()),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._drop_queued()
            self._finalize_closed()
            await asyncio.to_thread(
                self._close_http_without_worker,
                max(0.0, deadline - loop.time()),
            )
            return
        if future is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=max(0.0, deadline - loop.time()),
            )
        except asyncio.CancelledError:
            task = asyncio.current_task()
            cancelling = getattr(task, "cancelling", None)
            if (callable(cancelling) and cancelling()) or not future.cancelled():
                raise
            self._drop_queued()
            self._finalize_closed()
        except asyncio.TimeoutError:
            self._abandon_inflight()
            future.cancel()
            await asyncio.to_thread(
                self._closed_event.wait,
                max(0.0, deadline - loop.time()),
            )
            self._drop_queued()
            self._finalize_closed()
        except Exception:
            self._drop_queued()
            self._finalize_closed()
        if self._thread is not None:
            await asyncio.to_thread(
                self._thread.join,
                max(0.0, deadline - loop.time()),
            )

    def close(self) -> None:
        deadline = time.monotonic() + self.close_timeout_s
        try:
            future = self._begin_close(max(0.0, deadline - time.monotonic()))
        except Exception:
            self._drop_queued()
            self._finalize_closed()
            self._close_http_without_worker(max(0.0, deadline - time.monotonic()))
            return
        if future is None:
            return
        try:
            future.result(timeout=max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            self._abandon_inflight()
            future.cancel()
            self._closed_event.wait(max(0.0, deadline - time.monotonic()))
            self._drop_queued()
            self._finalize_closed()
        except Exception:
            self._drop_queued()
            self._finalize_closed()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _begin_close(self, startup_timeout_s: float) -> Optional[Future[None]]:
        with self._close_lock:
            with self._state_lock:
                if self._closed:
                    return None
                if self._close_future is not None:
                    return self._close_future
                self._closing = True
            self._start_worker()
            startup_timeout = min(WORKER_START_TIMEOUT_S, startup_timeout_s)
            if not self._ready.wait(startup_timeout) or self._loop is None:
                raise _WorkerUnavailable("worker did not become ready")
            coroutine = self._close_on_worker()
            try:
                future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
            except RuntimeError:
                coroutine.close()
                raise
            self._close_future = future
            return future

    def _start_worker(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._closed:
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="lucy-cloud-exporter",
                daemon=True,
            )
            self._thread.start()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        if self._flush_requested:
            self._wake.set()
        self._ready.set()
        try:
            loop.run_until_complete(self._run_worker())
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(self._close_http_fail_open())
            loop.close()
            with self._state_lock:
                if self._loop is loop:
                    self._loop = None
                    self._wake = None
                    self._flush_lock = None

    async def _run_worker(self) -> None:
        assert self._wake is not None
        while not self._stop_requested:
            await self._pace(self._wake, self.flush_interval_s)
            self._wake.clear()
            if not self._stop_requested:
                try:
                    await self._flush_on_worker()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._drop_queued()

    async def _flush_on_worker(self) -> None:
        assert self._flush_lock is not None
        async with self._flush_lock:
            events = self._take_queued_for_flush()
            if not events:
                return
            try:
                batches = plan_batches(
                    events,
                    project=self.project,
                    sdk_version=self.sdk_version,
                )
            except (TypeError, ValueError):
                if not self._finish_inflight(len(events)):
                    self._record_drop(len(events))
                return
            for index, batch in enumerate(batches):
                if self._inflight_was_abandoned():
                    return
                try:
                    delivered = await self._send_batch(batch)
                except asyncio.CancelledError:
                    remaining = [event for group in batches[index:] for event in group]
                    self._requeue_front(remaining)
                    raise
                except Exception:
                    delivered = False
                abandoned = self._finish_inflight(len(batch))
                if abandoned:
                    return
                if not delivered:
                    self._record_drop(len(batch))

    async def _close_on_worker(self) -> None:
        try:
            try:
                await self._flush_on_worker()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._drop_queued()
        finally:
            self._stop_requested = True
            try:
                await self._close_http_fail_open()
            except asyncio.CancelledError:
                raise
            finally:
                self._finalize_closed()
                if self._wake is not None:
                    self._wake.set()

    async def _close_http_fail_open(self) -> None:
        try:
            await self._http.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _close_http_without_worker(self, timeout_s: float) -> None:
        closer = threading.Thread(
            target=lambda: asyncio.run(self._close_http_fail_open()),
            name="lucy-cloud-emergency-close",
            daemon=True,
        )
        try:
            closer.start()
        except Exception:
            return
        closer.join(timeout=timeout_s)

    def _finalize_closed(self) -> None:
        with self._state_lock:
            self._closed = True
            self._stop_requested = True
            self.api_key = ""
            loop = self._loop
            wake = self._wake
        self._closed_event.set()
        atexit.unregister(self._atexit_callback)
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                return

    def _take_queued(self) -> List[dict]:
        with self._state_lock:
            events = list(self._queue)
            self._queue.clear()
            self._queued_bytes = self._empty_envelope_bytes
            return events

    def _take_queued_for_flush(self) -> List[dict]:
        with self._state_lock:
            if self._closed or self._inflight_abandoned:
                return []
            events = list(self._queue)
            self._queue.clear()
            self._queued_bytes = self._empty_envelope_bytes
            self._inflight_count = len(events)
            return events

    def _requeue_front(self, events: List[dict]) -> None:
        dropped = 0
        with self._state_lock:
            if self._inflight_abandoned:
                self._inflight_count = 0
                return
            if self._closed:
                dropped = len(events)
            else:
                for event in reversed(events):
                    self._queue.appendleft(event)
                self._queued_bytes = len(
                    json_bytes(
                        build_envelope(
                            self.project,
                            self.sdk_version,
                            list(self._queue),
                        )
                    )
                )
            self._inflight_count = 0
        if dropped:
            self._record_drop(dropped)

    def _finish_inflight(self, count: int) -> bool:
        with self._state_lock:
            if self._inflight_abandoned:
                return True
            self._inflight_count = max(0, self._inflight_count - count)
            return False

    def _inflight_was_abandoned(self) -> bool:
        with self._state_lock:
            return self._inflight_abandoned

    def _abandon_inflight(self) -> None:
        with self._state_lock:
            if self._inflight_abandoned:
                count = 0
            else:
                count = self._inflight_count
                self._inflight_count = 0
                self._inflight_abandoned = True
        if count:
            self._record_drop(count)

    def _drop_queued(self) -> None:
        events = self._take_queued()
        if events:
            self._record_drop(len(events))

    async def _send_batch(self, events: List[dict]) -> bool:
        envelope = build_envelope(self.project, self.sdk_version, events)
        body, base_headers = encode_batch(envelope)
        headers = {
            **base_headers,
            HEADER_API_KEY: self.api_key,
            HEADER_IDEMPOTENCY: str(uuid.uuid4()),
        }
        for attempt in range(self.max_retries + 1):
            retry_after: Optional[float] = None
            try:
                response = await self._http.post(
                    f"{self.endpoint}{EVENTS_PATH}",
                    content=body,
                    headers=headers,
                )
                if response.status_code == ACCEPTED_STATUS:
                    return True
                retryable = (
                    response.status_code == RATE_LIMITED_STATUS
                    or _RETRYABLE_SERVER_STATUS_MIN
                    <= response.status_code
                    <= _RETRYABLE_SERVER_STATUS_MAX
                )
                if response.status_code == RATE_LIMITED_STATUS:
                    retry_after = self._retry_after(response)
            except asyncio.CancelledError:
                raise
            except httpx.TransportError:
                retryable = True
            except Exception:
                return False
            if not retryable or attempt >= self.max_retries:
                return False
            delay = (
                retry_after
                if retry_after is not None
                else self._jitter(
                    0.0,
                    min(RETRY_MAX_S, RETRY_BASE_S * (2**attempt)),
                )
            )
            await self._sleep(delay)
        return False

    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        raw = response.headers.get(HEADER_RETRY_AFTER)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        return min(RETRY_AFTER_MAX_S, max(0.0, value))

    def _record_drop(self, count: int) -> None:
        with self._drop_lock:
            self._dropped_events += count
            now = self._monotonic()
            if now - self._last_drop_warning_at >= self.flush_interval_s:
                self._logger.warning("dropped %d Lucy telemetry event(s)", count)
                self._last_drop_warning_at = now
