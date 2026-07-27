import asyncio
import hashlib
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from lucy.observe import Tracer
from lucy.recording import RecordingCoordinator, RecordingUnavailableError
from lucy.specs import RecordingSpec
from lucy.transport.schema import RecordingUploaded
from lucy_cloud import CloudBlobStore
from lucy_cloud.blob import _UploadTarget


class BlobPlatformFixture:
    def __init__(
        self,
        *,
        presign_status: int = 201,
        complete_status: int = 200,
        abort_status: int = 200,
        presign_overrides: dict[str, Any] | None = None,
        complete_overrides: dict[str, Any] | None = None,
        complete_omissions: set[str] | None = None,
        abort_overrides: dict[str, Any] | None = None,
    ):
        self.presign_status = presign_status
        self.complete_status = complete_status
        self.abort_status = abort_status
        self.presign_overrides = presign_overrides or {}
        self.complete_overrides = complete_overrides or {}
        self.complete_omissions = complete_omissions or set()
        self.abort_overrides = abort_overrides or {}
        self.requests: list[tuple[str, str | None, dict[str, Any]]] = []
        self.app = FastAPI()

        @self.app.post("/v1/blobs", response_model=None, status_code=201)
        async def presign(request: Request) -> Response | dict[str, Any]:
            body = await request.json()
            self.requests.append(("presign", request.headers.get("x-api-key"), body))
            if self.presign_status != 201:
                return Response(status_code=self.presign_status)
            blob_id = body["blob_id"]
            result = {
                "blob_id": blob_id,
                "upload_url": (
                    f"http://127.0.0.1:59000/upload/{blob_id}?signature=opaque"
                ),
                "expires_at_ms": 2_000_000_000_000,
                "method": "PUT",
                "headers": {
                    "content-type": "audio/wav",
                    "if-none-match": "*",
                },
            }
            result.update(self.presign_overrides)
            return result

        @self.app.post("/v1/blobs/{blob_id}/complete", response_model=None)
        async def complete(blob_id: str, request: Request) -> Response | dict[str, Any]:
            body = await request.json()
            self.requests.append(("complete", request.headers.get("x-api-key"), body))
            if self.complete_status != 200:
                return Response(status_code=self.complete_status)
            result = {
                "blob_id": blob_id,
                "session_id": body["session_id"],
                "turn_id": body.get("turn_id"),
                "leg": body["leg"],
                "duration_ms": body["duration_ms"],
                "sha256": body["sha256"],
                "byte_count": body["byte_count"],
                "storage_container": "lucy-recordings",
                "content_type": body["content_type"],
                "consent_ref": body.get("consent_ref"),
                "retention_class": body["retention_class"],
            }
            result.update(self.complete_overrides)
            for field in self.complete_omissions:
                result.pop(field, None)
            return result

        @self.app.delete("/v1/blobs/{blob_id}", response_model=None)
        async def abort(blob_id: str, request: Request) -> Response | dict[str, Any]:
            self.requests.append(
                ("abort", request.headers.get("x-api-key"), {"blob_id": blob_id})
            )
            if self.abort_status != 200:
                return Response(status_code=self.abort_status)
            result = {"blob_id": blob_id, "state": "aborted"}
            result.update(self.abort_overrides)
            return result


class TimeoutTransport(httpx.AsyncBaseTransport):
    """Local protocol transport that times out one selected blob operation."""

    def __init__(self, app: FastAPI, operation: str) -> None:
        self._asgi = httpx.ASGITransport(app=app)
        self._operation = operation

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        operation = (
            "abort"
            if request.method == "DELETE"
            else "complete"
            if path.endswith("/complete")
            else "presign"
        )
        if operation == self._operation:
            raise httpx.ReadTimeout("deterministic blob timeout", request=request)
        return await self._asgi.handle_async_request(request)

    async def aclose(self) -> None:
        await self._asgi.aclose()


class PostDispatchTimeoutTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: FastAPI) -> None:
        self._asgi = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._asgi.handle_async_request(request)
        if request.method == "POST" and request.url.path == "/v1/blobs":
            raise httpx.ReadTimeout("timeout after dispatch", request=request)
        return response

    async def aclose(self) -> None:
        await self._asgi.aclose()


class ConnectErrorTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("deterministic connection failure", request=request)


class PresignGateTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: FastAPI) -> None:
        self._asgi = httpx.ASGITransport(app=app)
        self.presign_started = asyncio.Event()
        self.release_presign = asyncio.Event()
        self.presign_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/blobs":
            self.presign_count += 1
            self.presign_started.set()
            await self.release_presign.wait()
        return await self._asgi.handle_async_request(request)

    async def aclose(self) -> None:
        await self._asgi.aclose()


class CompleteGateTransport(httpx.AsyncBaseTransport):
    """Let completion commit even when the client task is cancelled."""

    def __init__(self, app: FastAPI) -> None:
        self._asgi = httpx.ASGITransport(app=app)
        self.complete_started = asyncio.Event()
        self.cancel_observed = asyncio.Event()
        self.release_complete = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/complete"):
            self.complete_started.set()
            try:
                await self.release_complete.wait()
            except asyncio.CancelledError:
                self.cancel_observed.set()
        return await self._asgi.handle_async_request(request)

    async def aclose(self) -> None:
        await self._asgi.aclose()


class AbortGateTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: FastAPI) -> None:
        self._asgi = httpx.ASGITransport(app=app)
        self.abort_started = asyncio.Event()
        self.release_abort = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            self.abort_started.set()
            await self.release_abort.wait()
        return await self._asgi.handle_async_request(request)

    async def aclose(self) -> None:
        await self._asgi.aclose()


def test_cloud_blob_store_presigns_opaque_target_with_call_context() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> tuple[str, str, dict[str, str], dict[str, Any]]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-alpha",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-safe-alpha",
            session_id="sess-safe-alpha",
            leg="agent",
            consent_ref="consent-safe-alpha",
        )
        upload_target = store.resolve_upload_target(upload_ref)
        await store.aclose()
        return (
            upload_ref,
            upload_target.url,
            upload_target.headers,
            fixture.requests[0][2],
        )

    upload_ref, upload_url, upload_headers, body = asyncio.run(scenario())

    assert upload_ref == "upload-ref-alpha"
    assert "signature=opaque" in upload_url
    assert upload_headers == {
        "content-type": "audio/wav",
        "if-none-match": "*",
    }
    assert body == {
        "blob_id": "blob-safe-alpha",
        "session_id": "sess-safe-alpha",
        "leg": "agent",
        "content_type": "audio/wav",
        "consent_ref": "consent-safe-alpha",
        "retention_class": "standard",
    }
    assert fixture.requests[0][1] == "lucy-secret"


def test_presign_accepts_privacy_safe_session_outside_recording_ref_grammar() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> str:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-session-grammar",
            session_id="tenant/session",
            leg="mixed",
            consent_ref="consent-session-grammar",
        )
        session_id = fixture.requests[0][2]["session_id"]
        await store.discard_upload(upload_ref)
        await store.aclose()
        return session_id

    assert asyncio.run(scenario()) == "tenant/session"


def test_cloud_blob_store_completes_and_verifies_platform_metadata() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> bool:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-complete",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-safe-complete",
            session_id="sess-safe-complete",
            leg="caller",
            consent_ref="consent-safe-complete",
        )
        accepted = await store.confirm_upload(
            RecordingUploaded(
                recording_id="recording-safe-complete",
                leg="caller",
                blob_id="blob-safe-complete",
                upload_url_ref=upload_ref,
                duration_ms=100,
                byte_count=len(b"recording"),
                sha256=hashlib.sha256(b"recording").hexdigest(),
                container="wav",
                consent_ref="consent-safe-complete",
            )
        )
        await store.aclose()
        return accepted

    assert asyncio.run(scenario()) is True
    assert fixture.requests[-1] == (
        "complete",
        "lucy-secret",
        {
            "blob_id": "blob-safe-complete",
            "session_id": "sess-safe-complete",
            "leg": "caller",
            "duration_ms": 100,
            "sha256": hashlib.sha256(b"recording").hexdigest(),
            "byte_count": len(b"recording"),
            "content_type": "audio/wav",
            "consent_ref": "consent-safe-complete",
            "retention_class": "standard",
        },
    )


@pytest.mark.parametrize("status_code", [401, 429])
def test_presign_http_failures_suppress_recording_without_crashing_call(
    status_code: int,
) -> None:
    fixture = BlobPlatformFixture(presign_status=status_code)

    async def scenario() -> tuple[list[Any], int]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["recording-fail-open", "blob-fail-open"]).__next__,
        )
        directives = await coordinator.start(
            "sess-fail-open", consent_ref="consent-fail-open"
        )
        dropped = store.dropped_recordings
        await store.aclose()
        return directives, dropped

    assert asyncio.run(scenario()) == ([], 1)
    assert [action for action, _, _ in fixture.requests] == ["presign"]


def test_ambiguous_post_dispatch_timeout_compensates_reservation() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=PostDispatchTimeoutTransport(fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-post-dispatch-timeout",
                session_id="sess-post-dispatch-timeout",
                leg="mixed",
                consent_ref="consent-post-dispatch-timeout",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1
    assert [action for action, _, _ in fixture.requests] == ["presign", "abort"]


def test_unreachable_platform_is_fail_open() -> None:
    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=ConnectErrorTransport(),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-unreachable",
                session_id="sess-unreachable",
                leg="mixed",
                consent_ref="consent-unreachable",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_closed_transport_is_fail_open() -> None:
    async def scenario() -> int:
        store = CloudBlobStore("http://127.0.0.1:59000", "lucy-secret")
        await store.aclose()
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-closed",
                session_id="sess-closed",
                leg="mixed",
                consent_ref="consent-closed",
            )
        return store.dropped_recordings

    assert asyncio.run(scenario()) == 1


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_completion_failures_return_false_and_increment_drop_counter(
    status_code: int,
) -> None:
    fixture = BlobPlatformFixture(complete_status=status_code)

    async def scenario() -> tuple[bool, int]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-rejected",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-rejected",
            session_id="sess-rejected",
            leg="mixed",
            consent_ref="consent-rejected",
        )
        accepted = await store.confirm_upload(
            RecordingUploaded(
                recording_id="recording-rejected",
                leg="mixed",
                blob_id="blob-rejected",
                upload_url_ref=upload_ref,
                duration_ms=100,
                byte_count=len(b"recording"),
                sha256=hashlib.sha256(b"recording").hexdigest(),
                container="wav",
                consent_ref="consent-rejected",
            )
        )
        dropped = store.dropped_recordings
        await store.aclose()
        return accepted, dropped

    assert asyncio.run(scenario()) == (False, 1)


def test_discard_aborts_durable_platform_reservation() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> tuple[int, bool]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-abort",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-abort",
            session_id="sess-abort",
            leg="mixed",
            consent_ref="consent-abort",
        )
        await store.discard_upload(upload_ref)
        with pytest.raises(KeyError, match="unknown upload target"):
            store.resolve_upload_target(upload_ref)
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped, bool(fixture.requests)

    assert asyncio.run(scenario()) == (0, True)
    assert fixture.requests[-1] == (
        "abort",
        "lucy-secret",
        {"blob_id": "blob-abort"},
    )


@pytest.mark.parametrize("status_code", [401, 429, 503])
def test_abort_failure_is_fail_open_and_forgets_signed_target(
    status_code: int,
) -> None:
    fixture = BlobPlatformFixture(abort_status=status_code)

    async def scenario() -> tuple[int, bool]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-abort-failure",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-abort-failure",
            session_id="sess-abort-failure",
            leg="mixed",
            consent_ref="consent-abort-failure",
        )
        await store.discard_upload(upload_ref)
        try:
            store.resolve_upload_target(upload_ref)
        except KeyError:
            forgotten = True
        else:
            forgotten = False
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped, forgotten

    assert asyncio.run(scenario()) == (1, True)


def test_abort_cancellation_retains_target_for_retry() -> None:
    fixture = BlobPlatformFixture()
    transport = AbortGateTransport(fixture.app)

    async def scenario() -> tuple[bool, bool]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
            ref_factory=lambda: "upload-ref-cancelled-abort",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-cancelled-abort",
            session_id="sess-cancelled-abort",
            leg="mixed",
            consent_ref="consent-cancelled-abort",
        )
        task = asyncio.create_task(store.discard_upload(upload_ref))
        await transport.abort_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        retained = store.resolve_upload_target(upload_ref).url != ""
        transport.release_abort.set()
        await store.discard_upload(upload_ref)
        try:
            store.resolve_upload_target(upload_ref)
        except KeyError:
            removed = True
        else:
            removed = False
        await store.aclose()
        return retained, removed

    assert asyncio.run(scenario()) == (True, True)


def test_close_cancellation_waits_for_cleanup_and_clears_credentials() -> None:
    fixture = BlobPlatformFixture()
    transport = AbortGateTransport(fixture.app)

    async def scenario() -> tuple[str, bool, bool]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
            ref_factory=lambda: "upload-ref-cancelled-close",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-cancelled-close",
            session_id="sess-cancelled-close",
            leg="mixed",
            consent_ref="consent-cancelled-close",
        )
        close = asyncio.create_task(store.aclose())
        await transport.abort_started.wait()
        close.cancel()
        await asyncio.sleep(0)
        still_closing = not close.done()
        transport.release_abort.set()
        with pytest.raises(asyncio.CancelledError):
            await close
        try:
            store.resolve_upload_target(upload_ref)
        except KeyError:
            removed = True
        else:
            removed = False
        return store.api_key, store._http.is_closed, still_closing and removed

    assert asyncio.run(scenario()) == ("", True, True)


def test_concurrent_close_callers_share_one_cleanup_operation() -> None:
    fixture = BlobPlatformFixture()
    transport = AbortGateTransport(fixture.app)

    async def scenario() -> tuple[bool, int, str]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
        )
        await store.prepare_upload_with_context(
            "blob-concurrent-close",
            session_id="sess-concurrent-close",
            leg="mixed",
            consent_ref="consent-concurrent-close",
        )
        first = asyncio.create_task(store.aclose())
        await transport.abort_started.wait()
        second = asyncio.create_task(store.aclose())
        await asyncio.sleep(0)
        both_waiting = not first.done() and not second.done()
        transport.release_abort.set()
        await asyncio.gather(first, second)
        abort_count = sum(action == "abort" for action, _, _ in fixture.requests)
        return both_waiting, abort_count, store.api_key

    assert asyncio.run(scenario()) == (True, 1, "")


def test_close_aborts_all_outstanding_reservations_and_clears_api_key() -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> tuple[list[str], str]:
        refs = iter(["upload-ref-close-a", "upload-ref-close-b"])
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=refs.__next__,
        )
        for suffix in ("a", "b"):
            await store.prepare_upload_with_context(
                f"blob-close-{suffix}",
                session_id="sess-close",
                leg="mixed",
                consent_ref="consent-close",
            )
        await store.aclose()
        return (
            [
                body["blob_id"]
                for action, _, body in fixture.requests
                if action == "abort"
            ],
            store.api_key,
        )

    assert asyncio.run(scenario()) == (["blob-close-a", "blob-close-b"], "")
    abort_api_keys = [
        api_key for action, api_key, _ in fixture.requests if action == "abort"
    ]
    assert abort_api_keys == [
        "lucy-secret",
        "lucy-secret",
    ]


def test_close_cancels_inflight_presign_and_rejects_new_work() -> None:
    fixture = BlobPlatformFixture()
    transport = PresignGateTransport(fixture.app)

    async def scenario() -> tuple[bool, bool, list[str]]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
            ref_factory=lambda: "upload-ref-after-close",
        )
        prepare = asyncio.create_task(
            store.prepare_upload_with_context(
                "blob-inflight-close",
                session_id="sess-inflight-close",
                leg="mixed",
                consent_ref="consent-inflight-close",
            )
        )
        await transport.presign_started.wait()
        await store.aclose()
        cancelled = prepare.cancelled()
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-after-close",
                session_id="sess-after-close",
                leg="mixed",
                consent_ref="consent-after-close",
            )
        unresolved = False
        try:
            store.resolve_upload_target("upload-ref-after-close")
        except KeyError:
            unresolved = True
        aborted = [
            body["blob_id"] for action, _, body in fixture.requests if action == "abort"
        ]
        return cancelled, unresolved, aborted

    assert asyncio.run(scenario()) == (
        True,
        True,
        ["blob-inflight-close"],
    )


def test_close_rejects_completion_that_commits_after_cancellation() -> None:
    fixture = BlobPlatformFixture()
    transport = CompleteGateTransport(fixture.app)

    async def scenario() -> tuple[bool, list[str], bool]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
            ref_factory=lambda: "upload-ref-complete-close",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-complete-close",
            session_id="sess-complete-close",
            leg="mixed",
            consent_ref="consent-complete-close",
        )
        completion = asyncio.create_task(
            store.confirm_upload(
                RecordingUploaded(
                    recording_id="recording-complete-close",
                    leg="mixed",
                    blob_id="blob-complete-close",
                    upload_url_ref=upload_ref,
                    duration_ms=100,
                    byte_count=len(b"recording"),
                    sha256=hashlib.sha256(b"recording").hexdigest(),
                    container="wav",
                    consent_ref="consent-complete-close",
                )
            )
        )
        await transport.complete_started.wait()
        close = asyncio.create_task(store.aclose())
        await asyncio.sleep(0)
        transport.release_complete.set()
        await close
        accepted = await completion
        actions = [action for action, _, _ in fixture.requests]
        try:
            store.resolve_upload_target(upload_ref)
        except KeyError:
            removed = True
        else:
            removed = False
        return accepted, actions, removed

    assert asyncio.run(scenario()) == (
        False,
        ["presign", "complete", "abort"],
        True,
    )


def test_duplicate_prepare_cannot_release_an_inflight_blob_identity() -> None:
    fixture = BlobPlatformFixture()
    transport = PresignGateTransport(fixture.app)

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=transport,
        )
        first = asyncio.create_task(
            store.prepare_upload_with_context(
                "blob-concurrent",
                session_id="sess-concurrent",
                leg="mixed",
                consent_ref="consent-concurrent",
            )
        )
        await transport.presign_started.wait()
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-concurrent",
                session_id="sess-concurrent",
                leg="mixed",
                consent_ref="consent-concurrent",
            )
        third = asyncio.create_task(
            store.prepare_upload_with_context(
                "blob-concurrent",
                session_id="sess-concurrent",
                leg="mixed",
                consent_ref="consent-concurrent",
            )
        )
        await asyncio.sleep(0)
        await store.aclose()
        assert first.cancelled()
        with pytest.raises((RecordingUnavailableError, asyncio.CancelledError)):
            await third
        return transport.presign_count

    assert asyncio.run(scenario()) == 1


def test_presign_rejects_platform_blob_id_drift() -> None:
    app = FastAPI()

    @app.post("/v1/blobs", status_code=201)
    async def presign() -> dict[str, Any]:
        return {
            "blob_id": "different-blob",
            "upload_url": "http://127.0.0.1:59000/upload/different",
            "expires_at_ms": 2_000_000_000_000,
            "method": "PUT",
            "headers": {"content-type": "audio/wav", "if-none-match": "*"},
        }

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-expected",
                session_id="sess-expected",
                leg="mixed",
                consent_ref="consent-expected",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_presign_rejects_missing_one_write_header() -> None:
    app = FastAPI()

    @app.post("/v1/blobs", status_code=201)
    async def presign(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {
            "blob_id": body["blob_id"],
            "upload_url": "http://127.0.0.1:59000/upload/unsigned-overwrite",
            "expires_at_ms": 2_000_000_000_000,
            "method": "PUT",
            "headers": {"content-type": "audio/wav"},
        }

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-missing-one-write",
                session_id="sess-missing-one-write",
                leg="mixed",
                consent_ref="consent-missing-one-write",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_presign_rejects_malformed_json() -> None:
    app = FastAPI()
    aborted: list[str] = []

    @app.post("/v1/blobs", status_code=201)
    async def presign() -> Response:
        return Response(
            content=b"not-json",
            media_type="application/json",
            status_code=201,
        )

    @app.delete("/v1/blobs/{blob_id}")
    async def abort(blob_id: str) -> dict[str, str]:
        aborted.append(blob_id)
        return {"blob_id": blob_id, "state": "aborted"}

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-malformed-json",
                session_id="sess-malformed-json",
                leg="mixed",
                consent_ref="consent-malformed-json",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1
    assert aborted == ["blob-malformed-json"]


def test_presign_redirect_is_not_followed() -> None:
    app = FastAPI()
    sentinel_visits = 0

    @app.post("/v1/blobs")
    async def redirect() -> Response:
        return Response(status_code=307, headers={"location": "/sentinel"})

    @app.post("/sentinel")
    async def sentinel() -> Response:
        nonlocal sentinel_visits
        sentinel_visits += 1
        return Response(status_code=201)

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-redirect",
                session_id="sess-redirect",
                leg="mixed",
                consent_ref="consent-redirect",
            )
        await store.aclose()
        return sentinel_visits

    assert asyncio.run(scenario()) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"headers": {"if-none-match": "*"}},
        {"headers": {"content-type": "text/plain", "if-none-match": "*"}},
        {"method": "POST"},
        {"expires_at_ms": 0},
        {"expires_at_ms": True},
        {"expires_at_ms": "2000000000000"},
    ],
)
def test_presign_rejects_each_malformed_response_contract(
    overrides: dict[str, Any],
) -> None:
    fixture = BlobPlatformFixture(presign_overrides=overrides)

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-malformed-presign",
                session_id="sess-malformed-presign",
                leg="mixed",
                consent_ref="consent-malformed-presign",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_fail_open_warning_never_logs_credentials_or_signed_urls(caplog) -> None:
    fixture = BlobPlatformFixture(presign_status=401)
    api_key = "lucy-do-not-log-this-key"

    async def scenario() -> None:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            api_key,
            transport=httpx.ASGITransport(app=fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-log-safe",
                session_id="sess-log-safe",
                leg="mixed",
                consent_ref="consent-log-safe",
            )
        await store.aclose()

    with caplog.at_level(logging.WARNING, logger="lucy_cloud"):
        asyncio.run(scenario())

    assert api_key not in caplog.text
    assert "signature" not in caplog.text
    assert caplog.messages == ["recording presign unavailable"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("blob_id", "api_key:super-secret"),
        ("session_id", "person@example.com"),
        ("session_id", "sess-34612345678"),
        ("session_id", "Bearer " + "fixture-blob-session"),
        ("consent_ref", "owner@example.com"),
    ],
)
def test_presign_rejects_sensitive_identity_before_network(
    field: str,
    value: str,
) -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        values = {
            "blob_id": "blob-safe-session",
            "session_id": "sess-safe-session",
            "consent_ref": "consent-safe-session",
        }
        values[field] = value
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                values["blob_id"],
                session_id=values["session_id"],
                leg="mixed",
                consent_ref=values["consent_ref"],
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1
    assert fixture.requests == []


def test_presign_rejects_insecure_non_loopback_upload_url() -> None:
    app = FastAPI()

    @app.post("/v1/blobs", status_code=201)
    async def presign(request: Request) -> dict[str, Any]:
        body = await request.json()
        return {
            "blob_id": body["blob_id"],
            "upload_url": "http://storage.example/recording.wav?signature=opaque",
            "expires_at_ms": 2_000_000_000_000,
            "method": "PUT",
            "headers": {"content-type": "audio/wav", "if-none-match": "*"},
        }

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-insecure-url",
                session_id="sess-insecure-url",
                leg="mixed",
                consent_ref="consent-insecure-url",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


@pytest.mark.parametrize(
    ("upload_url", "upload_origins"),
    [
        ("https://storage.example/recording.wav?signature=opaque", None),
        (
            "https://169.254.169.254/recording.wav?signature=opaque",
            ["https://169.254.169.254"],
        ),
    ],
)
def test_presign_rejects_untrusted_upload_networks(
    upload_url: str,
    upload_origins: list[str] | None,
) -> None:
    fixture = BlobPlatformFixture(presign_overrides={"upload_url": upload_url})

    async def scenario() -> int:
        store = CloudBlobStore(
            "https://platform.example",
            "lucy-secret",
            upload_origins=upload_origins,
            transport=httpx.ASGITransport(app=fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-network-policy",
                session_id="sess-network-policy",
                leg="mixed",
                consent_ref="consent-network-policy",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


@pytest.mark.parametrize(
    ("endpoint", "upload_url"),
    [
        (
            "https://platform.example",
            "https://platform.example:8443/recording.wav?signature=opaque",
        ),
        (
            "http://127.0.0.1:59000",
            "http://127.0.0.1:22/recording.wav?signature=opaque",
        ),
        (
            "http://127.0.0.1:59000",
            "https://127.0.0.1:59000/recording.wav?signature=opaque",
        ),
    ],
)
def test_presign_rejects_cross_origin(
    endpoint: str,
    upload_url: str,
) -> None:
    fixture = BlobPlatformFixture(presign_overrides={"upload_url": upload_url})

    async def scenario() -> int:
        store = CloudBlobStore(
            endpoint,
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-cross-port",
                session_id="sess-cross-port",
                leg="mixed",
                consent_ref="consent-cross-port",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_configured_public_upload_host_is_accepted() -> None:
    fixture = BlobPlatformFixture(
        presign_overrides={
            "upload_url": "https://storage.example/recording.wav?signature=opaque"
        }
    )

    async def scenario() -> str:
        store = CloudBlobStore(
            "https://platform.example",
            "lucy-secret",
            upload_origins=["https://storage.example"],
            transport=httpx.ASGITransport(app=fixture.app),
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-allowed-host",
            session_id="sess-allowed-host",
            leg="mixed",
            consent_ref="consent-allowed-host",
        )
        host = store.resolve_upload_target(upload_ref).url
        await store.aclose()
        return host

    assert asyncio.run(scenario()).startswith("https://storage.example/")


def test_presign_rejects_additional_upload_headers() -> None:
    fixture = BlobPlatformFixture(
        presign_overrides={
            "headers": {
                "content-type": "audio/wav",
                "if-none-match": "*",
                "authorization": "do-not-forward",
            }
        }
    )

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        with pytest.raises(RecordingUnavailableError):
            await store.prepare_upload_with_context(
                "blob-extra-header",
                session_id="sess-extra-header",
                leg="mixed",
                consent_ref="consent-extra-header",
            )
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


@pytest.mark.parametrize(
    ("overrides", "omissions"),
    [
        ({"blob_id": "blob-other"}, set()),
        ({"session_id": "sess-other"}, set()),
        ({"leg": "agent"}, set()),
        ({"duration_ms": 101}, set()),
        ({"duration_ms": "100"}, set()),
        ({"sha256": "b" * 64}, set()),
        ({"byte_count": 999}, set()),
        ({"byte_count": float(len(b"recording"))}, set()),
        ({"content_type": "text/plain"}, set()),
        ({"consent_ref": "consent-other"}, set()),
        ({"retention_class": "archive"}, set()),
        ({"storage_container": ""}, set()),
        ({}, {"storage_container"}),
    ],
)
def test_completion_metadata_mismatch_is_rejected(
    overrides: dict[str, Any],
    omissions: set[str],
) -> None:
    fixture = BlobPlatformFixture(
        complete_overrides=overrides,
        complete_omissions=omissions,
    )

    async def scenario() -> tuple[bool, int]:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
            ref_factory=lambda: "upload-ref-mismatch",
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-mismatch",
            session_id="sess-mismatch",
            leg="mixed",
            consent_ref="consent-mismatch",
        )
        accepted = await store.confirm_upload(
            RecordingUploaded(
                recording_id="recording-mismatch",
                leg="mixed",
                blob_id="blob-mismatch",
                upload_url_ref=upload_ref,
                duration_ms=100,
                byte_count=len(b"recording"),
                sha256=hashlib.sha256(b"recording").hexdigest(),
                container="wav",
                consent_ref="consent-mismatch",
            )
        )
        dropped = store.dropped_recordings
        await store.aclose()
        return accepted, dropped

    assert asyncio.run(scenario()) == (False, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"blob_id": "blob-other"},
        {"state": "completed"},
    ],
)
def test_abort_rejects_malformed_success_response(overrides: dict[str, Any]) -> None:
    fixture = BlobPlatformFixture(abort_overrides=overrides)

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=httpx.ASGITransport(app=fixture.app),
        )
        upload_ref = await store.prepare_upload_with_context(
            "blob-malformed-abort",
            session_id="sess-malformed-abort",
            leg="mixed",
            consent_ref="consent-malformed-abort",
        )
        await store.discard_upload(upload_ref)
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


@pytest.mark.parametrize("operation", ["presign", "complete", "abort"])
def test_each_blob_operation_is_fail_open_on_transport_timeout(operation: str) -> None:
    fixture = BlobPlatformFixture()

    async def scenario() -> int:
        store = CloudBlobStore(
            "http://127.0.0.1:59000",
            "lucy-secret",
            transport=TimeoutTransport(fixture.app, operation),
            ref_factory=lambda: "upload-ref-timeout",
        )
        if operation == "presign":
            with pytest.raises(RecordingUnavailableError):
                await store.prepare_upload_with_context(
                    "blob-timeout",
                    session_id="sess-timeout",
                    leg="mixed",
                    consent_ref="consent-timeout",
                )
        else:
            upload_ref = await store.prepare_upload_with_context(
                "blob-timeout",
                session_id="sess-timeout",
                leg="mixed",
                consent_ref="consent-timeout",
            )
            if operation == "complete":
                accepted = await store.confirm_upload(
                    RecordingUploaded(
                        recording_id="recording-timeout",
                        leg="mixed",
                        blob_id="blob-timeout",
                        upload_url_ref=upload_ref,
                        duration_ms=100,
                        byte_count=len(b"recording"),
                        sha256=hashlib.sha256(b"recording").hexdigest(),
                        container="wav",
                        consent_ref="consent-timeout",
                    )
                )
                assert accepted is False
            else:
                await store.discard_upload(upload_ref)
        dropped = store.dropped_recordings
        await store.aclose()
        return dropped

    assert asyncio.run(scenario()) == 1


def test_blob_client_uses_shared_cloud_environment(monkeypatch) -> None:
    monkeypatch.delenv("LUCY_API_KEY", raising=False)
    assert CloudBlobStore.from_env() is None

    monkeypatch.setenv("LUCY_ENDPOINT", "http://127.0.0.1:59000")
    monkeypatch.setenv("LUCY_API_KEY", "lucy-env-secret")
    monkeypatch.setenv(
        "LUCY_BLOB_UPLOAD_ORIGINS",
        "https://storage-a.example, https://storage-b.example",
    )
    store = CloudBlobStore.from_env()
    assert store is not None
    assert store.endpoint == "http://127.0.0.1:59000"
    assert store.api_key == "lucy-env-secret"
    assert store._upload_origins == frozenset(
        {
            ("http", "127.0.0.1", 59000),
            ("https", "storage-a.example", 443),
            ("https", "storage-b.example", 443),
        }
    )
    asyncio.run(store.aclose())


def test_internal_upload_target_repr_redacts_signed_material() -> None:
    target = _UploadTarget(
        blob_id="blob-safe",
        upload_url="https://storage.example/object?signature=do-not-log",
        headers={"authorization": "do-not-log"},
        session_id="session-safe",
        leg="mixed",
        consent_ref="consent-safe",
    )

    rendered = repr(target)
    assert "signature" not in rendered
    assert "authorization" not in rendered
    assert "do-not-log" not in rendered
