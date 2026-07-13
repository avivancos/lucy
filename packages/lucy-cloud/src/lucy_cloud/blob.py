"""Fail-open recording presign client for Lucy's hosted platform."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Callable, Dict, Iterable, Literal, NamedTuple, Optional, Set
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lucy.privacy import contains_sensitive_text
from lucy.observe.events import NonEmptyString
from lucy.recording import (
    RECORDING_CONTENT_TYPE,
    RECORDING_UPLOAD_HEADERS,
    RecordingUnavailableError,
    RecordingUploadTarget,
)
from lucy.transport.schema import OpaqueRecordingRef, RecordingLeg, RecordingUploaded

from lucy_cloud._wire import HEADER_API_KEY, LOGGER_NAME
from lucy_cloud.client import _validated_endpoint, _validated_http_url
from lucy_cloud.config import (
    blob_upload_origins_from_env,
    cloud_enabled_from_env,
    resolve_cloud_connection,
)

BLOBS_PATH = "/v1/blobs"
DEFAULT_BLOB_TIMEOUT_S = 5.0
DEFAULT_RETENTION_CLASS = "standard"
PRESIGN_ACCEPTED_STATUS = 201
COMPLETE_ACCEPTED_STATUS = 200
ABORT_ACCEPTED_STATUS = 200
_OPAQUE_REF_ADAPTER = TypeAdapter(OpaqueRecordingRef)
_RECORDING_LEG_ADAPTER = TypeAdapter(RecordingLeg)
_SESSION_ID_ADAPTER = TypeAdapter(NonEmptyString)
_UUID_DIGIT_TRANSLATION = str.maketrans("0123456789", "ghijklmnop")
RefFactory = Callable[[], str]
RETENTION_CLASS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")


class _Origin(NamedTuple):
    scheme: str
    hostname: str
    port: int


def _opaque_upload_ref() -> str:
    return f"upload-{uuid.uuid4().hex.translate(_UUID_DIGIT_TRANSLATION)}"


class _PresignResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    blob_id: str
    upload_url: str
    expires_at_ms: int = Field(gt=0)
    method: Literal["PUT"]
    headers: Dict[str, str]


class _CompleteResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    blob_id: str
    session_id: str
    turn_id: Optional[str] = None
    leg: str
    duration_ms: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    storage_container: str = Field(min_length=1)
    content_type: str
    consent_ref: str
    retention_class: str


class _AbortResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    blob_id: str
    state: Literal["aborted"]


@dataclass(frozen=True, repr=False)
class _UploadTarget:
    blob_id: str
    upload_url: str
    headers: Dict[str, str]
    session_id: str
    leg: str
    consent_ref: str

    def __repr__(self) -> str:
        return "_UploadTarget(signed_material=<redacted>)"


@dataclass(frozen=True)
class _TargetState:
    target: _UploadTarget
    lock: asyncio.Lock


def _validated_upload_url(
    value: str,
    *,
    allowed_origins: frozenset[_Origin],
    endpoint_origin: _Origin,
) -> str:
    validated = _validated_http_url(value, allow_query=True, label="upload URL")
    origin = _url_origin(validated)
    if origin not in allowed_origins:
        raise ValueError("upload URL origin is not allowed")
    try:
        address = ip_address(origin.hostname)
    except ValueError:
        return validated
    if not address.is_global and origin != endpoint_origin:
        raise ValueError("upload URL cannot target an untrusted local network")
    return validated


def _url_origin(value: str, *, configured: bool = False) -> _Origin:
    validated = _validated_http_url(
        value,
        allow_query=not configured,
        label="upload origin" if configured else "upload URL",
    )
    parsed = urlsplit(validated)
    if configured and parsed.path not in {"", "/"}:
        raise ValueError("upload origin cannot contain a path")
    default_port = 443 if parsed.scheme == "https" else 80
    return _Origin(
        parsed.scheme,
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


def _privacy_safe_ref(value: str) -> str:
    safe = _OPAQUE_REF_ADAPTER.validate_python(value)
    if contains_sensitive_text(safe):
        raise ValueError("recording identity cannot contain sensitive text")
    return safe


def _privacy_safe_session_id(value: str) -> str:
    safe = _SESSION_ID_ADAPTER.validate_python(value)
    if contains_sensitive_text(safe):
        raise ValueError("session identity cannot contain sensitive text")
    return safe


class CloudBlobStore:
    """Negotiate media-plane uploads without moving audio through Python."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        timeout_s: float = DEFAULT_BLOB_TIMEOUT_S,
        retention_class: str = DEFAULT_RETENTION_CLASS,
        upload_origins: Optional[Iterable[str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        ref_factory: RefFactory = _opaque_upload_ref,
    ) -> None:
        if (
            not api_key
            or timeout_s <= 0
            or not RETENTION_CLASS_PATTERN.fullmatch(retention_class)
            or contains_sensitive_text(retention_class)
        ):
            raise ValueError("api key and positive blob settings are required")
        self.endpoint = _validated_endpoint(endpoint)
        self._endpoint_origin = _url_origin(self.endpoint)
        configured_values = tuple(upload_origins or ())
        if any(not value.strip() for value in configured_values):
            raise ValueError("at least one upload origin is required")
        self._upload_origins = frozenset(
            (
                self._endpoint_origin,
                *(
                    _url_origin(value.strip(), configured=True)
                    for value in configured_values
                ),
            )
        )
        self.api_key = api_key
        self.retention_class = retention_class
        self._ref_factory = ref_factory
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=timeout_s,
            follow_redirects=False,
        )
        self._targets: Dict[str, _TargetState] = {}
        self._pending_blob_ids: Set[str] = set()
        self._inflight_tasks: Set[asyncio.Task[Any]] = set()
        self._close_task: Optional[asyncio.Task[None]] = None
        self._dropped_recordings = 0
        self._logger = logging.getLogger(LOGGER_NAME)

    @classmethod
    def from_env(cls) -> Optional["CloudBlobStore"]:
        if not cloud_enabled_from_env():
            return None
        endpoint, api_key = resolve_cloud_connection()
        return cls(
            endpoint,
            api_key,
            upload_origins=blob_upload_origins_from_env(),
        )

    @property
    def dropped_recordings(self) -> int:
        return self._dropped_recordings

    async def prepare_upload_with_context(
        self,
        blob_id: str,
        *,
        session_id: str,
        leg: str,
        consent_ref: str,
    ) -> str:
        task = asyncio.current_task()
        if task is not None:
            self._inflight_tasks.add(task)
        safe_blob_id: Optional[str] = None
        pending_registered = False
        request_dispatched = False
        try:
            if self._close_task is not None:
                raise RuntimeError("blob client is closing")
            safe_blob_id = _privacy_safe_ref(blob_id)
            safe_session_id = _privacy_safe_session_id(session_id)
            safe_consent_ref = _privacy_safe_ref(consent_ref)
            safe_leg = _RECORDING_LEG_ADAPTER.validate_python(leg)
            if safe_blob_id in self._pending_blob_ids or any(
                state.target.blob_id == safe_blob_id for state in self._targets.values()
            ):
                raise ValueError("blob and session identity must be unique")
            self._pending_blob_ids.add(safe_blob_id)
            pending_registered = True
            request_dispatched = True
            response = await self._http.post(
                f"{self.endpoint}{BLOBS_PATH}",
                headers={HEADER_API_KEY: self.api_key},
                json={
                    "blob_id": safe_blob_id,
                    "session_id": safe_session_id,
                    "leg": safe_leg,
                    "content_type": RECORDING_CONTENT_TYPE,
                    "consent_ref": safe_consent_ref,
                    "retention_class": self.retention_class,
                },
            )
            if response.status_code != PRESIGN_ACCEPTED_STATUS:
                request_dispatched = False
                response.raise_for_status()
                raise ValueError("platform returned an unexpected presign status")
            presign = _PresignResponse.model_validate(response.json())
            if presign.blob_id != safe_blob_id:
                raise ValueError("platform changed the external blob identifier")
            upload_url = _validated_upload_url(
                presign.upload_url,
                allowed_origins=self._upload_origins,
                endpoint_origin=self._endpoint_origin,
            )
            headers = {key.lower(): value for key, value in presign.headers.items()}
            if headers != RECORDING_UPLOAD_HEADERS:
                raise ValueError("platform returned incompatible upload headers")
            upload_ref = _OPAQUE_REF_ADAPTER.validate_python(self._ref_factory())
            if upload_ref in self._targets:
                raise ValueError("upload reference already exists")
            self._targets[upload_ref] = _TargetState(
                target=_UploadTarget(
                    blob_id=safe_blob_id,
                    upload_url=upload_url,
                    headers=headers,
                    session_id=safe_session_id,
                    leg=safe_leg,
                    consent_ref=safe_consent_ref,
                ),
                lock=asyncio.Lock(),
            )
            request_dispatched = False
            return upload_ref
        except asyncio.CancelledError:
            if request_dispatched and safe_blob_id is not None:
                await asyncio.shield(
                    self._abort_blob_id(safe_blob_id, record_failure=False)
                )
            raise
        except Exception:
            if request_dispatched and safe_blob_id is not None:
                await self._abort_blob_id(safe_blob_id, record_failure=False)
            self._record_drop("recording presign unavailable")
            raise RecordingUnavailableError("recording storage unavailable") from None
        finally:
            if safe_blob_id is not None and pending_registered:
                self._pending_blob_ids.discard(safe_blob_id)
            if task is not None:
                self._inflight_tasks.discard(task)

    async def discard_upload(self, upload_url_ref: str) -> None:
        state = self._targets.get(upload_url_ref)
        if state is None:
            return
        async with state.lock:
            current = self._targets.get(upload_url_ref)
            if current is not None:
                await self._abort_blob_id(current.target.blob_id)
                self._targets.pop(upload_url_ref, None)

    async def confirm_upload(self, event: RecordingUploaded) -> bool:
        state = self._targets.get(event.upload_url_ref)
        if state is None:
            return False
        async with state.lock:
            current = self._targets.get(event.upload_url_ref)
            target = current.target if current is not None else None
            if (
                self._close_task is not None
                or target is None
                or target.blob_id != event.blob_id
                or target.leg != event.leg
                or target.consent_ref != event.consent_ref
            ):
                return False
            task = asyncio.current_task()
            if task is not None:
                self._inflight_tasks.add(task)
            try:
                response = await self._http.post(
                    (
                        f"{self.endpoint}{BLOBS_PATH}/"
                        f"{quote(event.blob_id, safe='')}/complete"
                    ),
                    headers={HEADER_API_KEY: self.api_key},
                    json={
                        "blob_id": event.blob_id,
                        "session_id": target.session_id,
                        "leg": event.leg,
                        "duration_ms": event.duration_ms,
                        "sha256": event.sha256,
                        "byte_count": event.byte_count,
                        "content_type": RECORDING_CONTENT_TYPE,
                        "consent_ref": event.consent_ref,
                        "retention_class": self.retention_class,
                    },
                )
                if response.status_code != COMPLETE_ACCEPTED_STATUS:
                    response.raise_for_status()
                    raise ValueError(
                        "platform returned an unexpected completion status"
                    )
                completed = _CompleteResponse.model_validate(response.json())
                if self._close_task is not None:
                    return False
                accepted = (
                    completed.blob_id == event.blob_id
                    and completed.session_id == target.session_id
                    and completed.leg == event.leg
                    and completed.duration_ms == event.duration_ms
                    and completed.sha256 == event.sha256
                    and completed.byte_count == event.byte_count
                    and completed.content_type == RECORDING_CONTENT_TYPE
                    and completed.consent_ref == event.consent_ref
                    and completed.retention_class == self.retention_class
                )
            except Exception:
                self._record_drop("recording completion unavailable")
                return False
            finally:
                if task is not None:
                    self._inflight_tasks.discard(task)
            if accepted:
                self._targets.pop(event.upload_url_ref, None)
                return True
            self._record_drop("recording completion mismatch")
            return False

    async def discard_blob(self, blob_id: str) -> None:
        for upload_ref, state in list(self._targets.items()):
            if state.target.blob_id == blob_id:
                await self.discard_upload(upload_ref)

    def resolve_upload_target(self, upload_url_ref: str) -> RecordingUploadTarget:
        if self._close_task is not None:
            raise KeyError("unknown upload target")
        try:
            target = self._targets[upload_url_ref].target
        except KeyError as exc:
            raise KeyError("unknown upload target") from exc
        return RecordingUploadTarget(
            url=target.upload_url,
            headers=dict(target.headers),
        )

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _close(self) -> None:
        try:
            inflight = list(self._inflight_tasks)
            for task in inflight:
                task.cancel()
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)
            for upload_ref in list(self._targets):
                await self.discard_upload(upload_ref)
        finally:
            try:
                await self._http.aclose()
            finally:
                self.api_key = ""

    async def _abort_blob_id(
        self,
        blob_id: str,
        *,
        record_failure: bool = True,
    ) -> None:
        try:
            response = await self._http.delete(
                f"{self.endpoint}{BLOBS_PATH}/{quote(blob_id, safe='')}",
                headers={HEADER_API_KEY: self.api_key},
            )
            if response.status_code != ABORT_ACCEPTED_STATUS:
                response.raise_for_status()
                raise ValueError("platform returned an unexpected abort status")
            aborted = _AbortResponse.model_validate(response.json())
            if aborted.blob_id != blob_id:
                raise ValueError("platform changed the aborted blob identifier")
        except Exception:
            if record_failure:
                self._record_drop("recording abort unavailable")

    def _record_drop(self, warning: str) -> None:
        self._dropped_recordings += 1
        self._logger.warning(warning)
