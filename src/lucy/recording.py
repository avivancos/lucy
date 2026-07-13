"""Recording policy and local blob seam for the media-plane protocol."""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
import wave
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Dict, List, Optional, Protocol, Union, runtime_checkable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import TypeAdapter

from lucy.observe import Tracer
from lucy.specs import RecordingSpec
from lucy.transport.schema import (
    OpaqueRecordingRef,
    RecordingFailed,
    RecordingStart,
    RecordingUploaded,
)

CONSENT_NOT_REQUIRED_REF = "consent-not-required"
RECORDING_CONTENT_TYPE = "audio/wav"
RECORDING_UPLOAD_HEADERS = MappingProxyType(
    {"content-type": RECORDING_CONTENT_TYPE, "if-none-match": "*"}
)
LOCAL_BLOB_MAX_BYTES = 10 * 1024 * 1024
_OPAQUE_REF_ADAPTER = TypeAdapter(OpaqueRecordingRef)
_UUID_DIGIT_TRANSLATION = str.maketrans("0123456789", "ghijklmnop")


def _opaque_uuid() -> str:
    return uuid.uuid4().hex.translate(_UUID_DIGIT_TRANSLATION)


class RecordingCleanupError(RuntimeError):
    """Report every failed storage cleanup operation for a retryable plan."""

    def __init__(self, message: str, errors: List[BaseException]) -> None:
        super().__init__(message)
        self.errors = tuple(errors)


class RecordingUnavailableError(RuntimeError):
    """Signal that optional recording cannot safely continue."""


@dataclass(frozen=True, repr=False)
class RecordingUploadTarget:
    """Trusted media-plane destination resolved from an opaque control ref."""

    url: str
    headers: Dict[str, str]

    def __repr__(self) -> str:
        return "RecordingUploadTarget(url=<redacted>, headers=<redacted>)"


class BlobStore(Protocol):
    async def prepare_upload(self, blob_id: str) -> str: ...

    async def discard_upload(self, upload_url_ref: str) -> None: ...

    async def confirm_upload(self, event: RecordingUploaded) -> bool: ...

    async def discard_blob(self, blob_id: str) -> None: ...


@runtime_checkable
class ContextualBlobStore(Protocol):
    """Additive hosted-storage capability that leaves ``BlobStore`` frozen."""

    async def prepare_upload_with_context(
        self,
        blob_id: str,
        *,
        session_id: str,
        leg: str,
        consent_ref: str,
    ) -> str: ...

    async def discard_upload(self, upload_url_ref: str) -> None: ...

    async def confirm_upload(self, event: RecordingUploaded) -> bool: ...

    async def discard_blob(self, blob_id: str) -> None: ...


class LocalBlobStore:
    """In-process ASGI blob service used by the deterministic media simulator."""

    def __init__(
        self,
        base_url: str,
        *,
        upload_error_status: Optional[int] = None,
        max_blob_bytes: int = LOCAL_BLOB_MAX_BYTES,
        upload_gate: Optional[asyncio.Event] = None,
        upload_started: Optional[asyncio.Event] = None,
        ref_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.upload_error_status = upload_error_status
        self.max_blob_bytes = max_blob_bytes
        self.upload_gate = upload_gate
        self.upload_started = upload_started
        self._ref_factory = ref_factory or _opaque_uuid
        self._targets: Dict[str, tuple[str, str]] = {}
        self._receipts: Dict[str, str] = {}
        self._blobs: Dict[str, bytes] = {}
        self.app = FastAPI()

        @self.app.put("/blobs/{blob_id}")
        async def upload(blob_id: str, upload_ref: str, request: Request) -> Response:
            target = self._targets.get(upload_ref)
            if target is None or target[0] != blob_id:
                raise HTTPException(status_code=404, detail="unknown upload target")
            if self.upload_error_status is not None:
                return Response(status_code=self.upload_error_status)
            if self.upload_started is not None:
                self.upload_started.set()
            if self.upload_gate is not None:
                await self.upload_gate.wait()
            if self._targets.get(upload_ref) != target:
                raise HTTPException(status_code=410, detail="upload target revoked")
            if request.headers.get("content-type") != RECORDING_CONTENT_TYPE:
                raise HTTPException(status_code=415, detail="audio/wav required")
            if request.headers.get("if-none-match") != "*":
                raise HTTPException(status_code=412, detail="one-write header required")
            chunks: List[bytes] = []
            byte_count = 0
            async for chunk in request.stream():
                byte_count += len(chunk)
                if byte_count > self.max_blob_bytes:
                    raise HTTPException(status_code=413, detail="recording too large")
                chunks.append(chunk)
            if self._targets.get(upload_ref) != target:
                raise HTTPException(status_code=410, detail="upload target revoked")
            self._blobs[blob_id] = b"".join(chunks)
            self._receipts[upload_ref] = blob_id
            self._targets.pop(upload_ref, None)
            return Response(status_code=204)

    @property
    def prepared_count(self) -> int:
        return len(self._targets)

    @property
    def blob_count(self) -> int:
        return len(self._blobs)

    async def prepare_upload(self, blob_id: str) -> str:
        if blob_id in self._blobs or any(
            target_blob_id == blob_id for target_blob_id, _ in self._targets.values()
        ):
            raise ValueError("blob id already exists")
        upload_ref = f"upload-{self._ref_factory()}"
        if upload_ref in self._targets or upload_ref in self._receipts:
            raise ValueError("upload reference already exists")
        safe_blob_id = quote(blob_id, safe="")
        safe_ref = quote(upload_ref, safe="")
        upload_url = f"{self.base_url}/blobs/{safe_blob_id}?upload_ref={safe_ref}"
        self._targets[upload_ref] = (blob_id, upload_url)
        return upload_ref

    async def discard_upload(self, upload_url_ref: str) -> None:
        self._targets.pop(upload_url_ref, None)
        self._receipts.pop(upload_url_ref, None)

    async def confirm_upload(self, event: RecordingUploaded) -> bool:
        if self._receipts.get(event.upload_url_ref) != event.blob_id:
            return False
        blob = self._blobs.get(event.blob_id)
        if blob is None or event.container != "wav":
            return False
        try:
            with wave.open(io.BytesIO(blob), "rb") as wav:
                duration_ms = round(wav.getnframes() / wav.getframerate() * 1000)
        except (EOFError, wave.Error, ZeroDivisionError):
            return False
        confirmed = (
            event.byte_count == len(blob)
            and event.sha256 == hashlib.sha256(blob).hexdigest()
            and event.duration_ms == duration_ms
        )
        if confirmed:
            self._receipts.pop(event.upload_url_ref, None)
        return confirmed

    async def discard_blob(self, blob_id: str) -> None:
        self._blobs.pop(blob_id, None)
        for upload_ref, receipt_blob_id in list(self._receipts.items()):
            if receipt_blob_id == blob_id:
                self._receipts.pop(upload_ref, None)

    def resolve_upload_target(self, upload_url_ref: str) -> RecordingUploadTarget:
        try:
            upload_url = self._targets[upload_url_ref][1]
        except KeyError as exc:
            raise KeyError("unknown upload target") from exc
        return RecordingUploadTarget(
            url=upload_url,
            headers=dict(RECORDING_UPLOAD_HEADERS),
        )

    def read(self, blob_id: str) -> bytes:
        return self._blobs[blob_id]


class RecordingCoordinator:
    """Apply SDK recording policy and project confirmed uploads to telemetry."""

    def __init__(
        self,
        spec: RecordingSpec,
        blob_store: Union[BlobStore, ContextualBlobStore],
        tracer: Tracer,
        *,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self.spec = spec
        self.blob_store = blob_store
        self.tracer = tracer
        self._id_factory = id_factory or _opaque_uuid
        self._planned: Dict[str, tuple[str, RecordingStart]] = {}
        self._reserved_recording_ids: set[str] = set()

    async def start(
        self, session_id: str, *, consent_ref: Optional[str] = None
    ) -> List[RecordingStart]:
        if not self._recording_allowed(session_id, consent_ref):
            return []

        effective_consent = _OPAQUE_REF_ADAPTER.validate_python(
            consent_ref or CONSENT_NOT_REQUIRED_REF
        )
        legs = ["caller", "agent"] if self.spec.channels == "dual" else ["mixed"]
        directives: List[RecordingStart] = []
        prepared_refs: List[str] = []
        prepared_blob_ids: List[str] = []
        owned_reservations: List[str] = []
        try:
            for leg in legs:
                recording_id = _OPAQUE_REF_ADAPTER.validate_python(self._id_factory())
                if (
                    recording_id in self._planned
                    or recording_id in self._reserved_recording_ids
                ):
                    raise ValueError("recording id already exists")
                self._reserved_recording_ids.add(recording_id)
                owned_reservations.append(recording_id)
                blob_id = _OPAQUE_REF_ADAPTER.validate_python(self._id_factory())
                if isinstance(self.blob_store, ContextualBlobStore):
                    upload_url_ref = await self.blob_store.prepare_upload_with_context(
                        blob_id,
                        session_id=session_id,
                        leg=leg,
                        consent_ref=effective_consent,
                    )
                else:
                    upload_url_ref = await self.blob_store.prepare_upload(blob_id)
                prepared_refs.append(upload_url_ref)
                prepared_blob_ids.append(blob_id)
                directive = RecordingStart(
                    recording_id=recording_id,
                    leg=leg,
                    blob_id=blob_id,
                    upload_url_ref=upload_url_ref,
                    container="wav",
                    consent_ref=effective_consent,
                )
                self._planned[recording_id] = (session_id, directive)
                self._reserved_recording_ids.remove(recording_id)
                directives.append(directive)
        except BaseException as cause:
            cleanup_errors: List[BaseException] = []
            for upload_ref, blob_id in zip(prepared_refs, prepared_blob_ids):
                errors = await self._cleanup_storage(upload_ref, blob_id)
                cleanup_errors.extend(errors)
                if not errors:
                    for directive in directives:
                        if directive.upload_url_ref == upload_ref:
                            self._planned.pop(directive.recording_id, None)
            for recording_id in owned_reservations:
                self._reserved_recording_ids.discard(recording_id)
            if cleanup_errors:
                cancellation = next(
                    (
                        error
                        for error in cleanup_errors
                        if isinstance(error, asyncio.CancelledError)
                    ),
                    None,
                )
                if cancellation is not None:
                    raise cancellation from cause
                raise RecordingCleanupError(
                    "recording start rollback failed", cleanup_errors
                ) from cause
            if isinstance(cause, RecordingUnavailableError):
                return []
            raise
        return directives

    async def recording_uploaded(self, event: RecordingUploaded) -> bool:
        correlated_id = self._recording_id_for_upload(event)
        if correlated_id is None:
            return False
        if correlated_id != event.recording_id:
            await self.cancel(correlated_id)
            return False
        plan = self._planned.get(event.recording_id)
        if plan is None:
            return False
        session_id, planned = plan
        if not self._matches(
            planned, event
        ) or not await self.blob_store.confirm_upload(event):
            await self.cancel(event.recording_id)
            return False
        self._planned.pop(event.recording_id, None)
        self.tracer.audio_ref(
            session_id=session_id,
            blob_id=event.blob_id,
            upload_url_requested=True,
            recording_id=event.recording_id,
            leg=event.leg,
            duration_ms=event.duration_ms,
            byte_count=event.byte_count,
            sha256=event.sha256,
            container=event.container,
            consent_ref=event.consent_ref,
        )
        return True

    async def recording_failed(self, event: RecordingFailed) -> bool:
        return await self.cancel(event.recording_id)

    async def cancel(self, recording_id: str) -> bool:
        plan = self._planned.get(recording_id)
        if plan is None:
            return False
        _, directive = plan
        errors = await self._cleanup_storage(
            directive.upload_url_ref,
            directive.blob_id,
        )
        if errors:
            cancellation = next(
                (
                    error
                    for error in errors
                    if isinstance(error, asyncio.CancelledError)
                ),
                None,
            )
            if cancellation is not None:
                raise cancellation
            raise RecordingCleanupError("recording cleanup failed", errors)
        self._planned.pop(recording_id, None)
        return True

    def _recording_allowed(self, session_id: str, consent_ref: Optional[str]) -> bool:
        return (
            self.spec.enabled
            and self.tracer.audio_recording_allowed(session_id)
            and (not self.spec.require_consent or bool(consent_ref))
        )

    async def _cleanup_storage(
        self, upload_url_ref: str, blob_id: str
    ) -> List[BaseException]:
        errors: List[BaseException] = []
        try:
            await self.blob_store.discard_upload(upload_url_ref)
        except BaseException as exc:
            errors.append(exc)
        try:
            await self.blob_store.discard_blob(blob_id)
        except BaseException as exc:
            errors.append(exc)
        return errors

    def _recording_id_for_upload(self, event: RecordingUploaded) -> Optional[str]:
        for recording_id, (_, planned) in self._planned.items():
            if self._same_upload(planned, event):
                return recording_id
        return None

    @staticmethod
    def _same_upload(planned: RecordingStart, event: RecordingUploaded) -> bool:
        return (
            planned.blob_id == event.blob_id
            and planned.upload_url_ref == event.upload_url_ref
        )

    @staticmethod
    def _matches(planned: RecordingStart, event: RecordingUploaded) -> bool:
        return (
            planned.leg == event.leg
            and planned.container == event.container
            and planned.consent_ref == event.consent_ref
        )
