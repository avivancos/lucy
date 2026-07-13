import asyncio
import hashlib
import io
import logging
import socket
import wave
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Response
from pydantic import ValidationError

from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.observe import Tracer
from lucy.recording import (
    LocalBlobStore,
    RecordingCleanupError,
    RecordingCoordinator,
    RecordingUploadTarget,
)
from lucy.specs import RecordingSpec
from lucy.testing import InMemoryTraceExporter, RecordingBlobStoreSimulator
from lucy.transport.dev_gateway import LocalGatewaySimulator, _SignedUploadLogFilter
from lucy.transport.schema import (
    Envelope,
    RecordingFailed,
    RecordingStart,
    RecordingUploaded,
)


def _ids():
    values = iter(
        [
            "rec-caller",
            "blob-caller",
            "rec-agent",
            "blob-agent",
        ]
    )
    return values.__next__


def test_recording_policy_defaults_to_disabled_and_creates_no_upload_targets():
    store = LocalBlobStore("http://127.0.0.1:1")
    tracer = Tracer(exporters=[], record_audio=True)

    disabled = RecordingCoordinator(
        RecordingSpec(),
        store,
        tracer,
        id_factory=_ids(),
    )
    missing_consent = RecordingCoordinator(
        RecordingSpec(enabled=True),
        store,
        tracer,
        id_factory=_ids(),
    )
    audio_killed = RecordingCoordinator(
        RecordingSpec(enabled=True),
        store,
        Tracer(exporters=[], record_audio=False),
        id_factory=_ids(),
    )
    unsampled = RecordingCoordinator(
        RecordingSpec(enabled=True),
        store,
        Tracer(exporters=[], record_audio=True, sample_rate=0.0),
        id_factory=_ids(),
    )

    assert asyncio.run(disabled.start("sess-disabled", consent_ref="consent-1")) == []
    assert asyncio.run(missing_consent.start("sess-no-consent")) == []
    assert asyncio.run(audio_killed.start("sess-killed", consent_ref="consent-1")) == []
    assert asyncio.run(unsampled.start("sess-unsampled", consent_ref="consent-1")) == []
    assert store.prepared_count == 0


def test_recording_policy_prepares_dual_or_mixed_opaque_uploads_only():
    store = LocalBlobStore("http://127.0.0.1:1")
    tracer = Tracer(exporters=[], record_audio=True)
    dual = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="dual"),
        store,
        tracer,
        id_factory=_ids(),
    )

    directives = asyncio.run(dual.start("sess-dual", consent_ref="consent-1"))

    assert [directive.leg for directive in directives] == ["caller", "agent"]
    assert all(directive.container == "wav" for directive in directives)
    assert all("http" not in directive.upload_url_ref for directive in directives)
    assert store.prepared_count == 2
    assert store.blob_count == 0

    mixed_store = LocalBlobStore("http://127.0.0.1:1")
    mixed = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed", require_consent=False),
        mixed_store,
        tracer,
        id_factory=iter(["rec-mixed", "blob-mixed"]).__next__,
    )
    mixed_directives = asyncio.run(mixed.start("sess-mixed"))
    assert [directive.leg for directive in mixed_directives] == ["mixed"]
    assert mixed_directives[0].consent_ref == "consent-not-required"


def test_recording_policy_uses_deterministic_fractional_head_sampling():
    rejected_store = LocalBlobStore("http://127.0.0.1:1")
    accepted_store = LocalBlobStore("http://127.0.0.1:1")
    rejected = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        rejected_store,
        Tracer(exporters=[], record_audio=True, sample_rate=0.5),
        id_factory=iter(["rec-a", "blob-a"]).__next__,
    )
    accepted = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        accepted_store,
        Tracer(exporters=[], record_audio=True, sample_rate=0.5),
        id_factory=iter(["rec-b", "blob-b"]).__next__,
    )

    assert asyncio.run(rejected.start("sess-a", consent_ref="consent")) == []
    assert len(asyncio.run(accepted.start("sess-b", consent_ref="consent"))) == 1
    assert rejected_store.prepared_count == 0
    assert accepted_store.prepared_count == 1


@pytest.mark.parametrize(
    "consent_ref",
    [
        "   ",
        "person@example.com",
        "34612345678",
        "consent:34612345678",
        "tel-34612345678",
        "consent:346:123:456:78",
        "consent_346_123_456_78",
        "ref3x4y6z1q2w3e4r5t6",
        "x" * 129,
    ],
)
def test_recording_policy_rejects_non_opaque_consent_without_leaking_target(
    consent_ref,
):
    store = LocalBlobStore("http://127.0.0.1:1")
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-invalid", "blob-invalid"]).__next__,
    )

    with pytest.raises(ValidationError):
        asyncio.run(coordinator.start("sess-invalid", consent_ref=consent_ref))
    assert store.prepared_count == 0

    with pytest.raises(ValidationError):
        Tracer(exporters=[], record_audio=True).audio_ref(
            session_id="sess-invalid",
            blob_id="blob-invalid",
            consent_ref=consent_ref,
        )

    with pytest.raises(ValidationError):
        Tracer(exporters=[], record_audio=True).audio_ref(
            session_id="sess-invalid",
            blob_id="blob-invalid",
            recording_id="person@example.com",
            container="person@example.com",
        )


def test_recording_refs_share_bounded_opaque_schema():
    base = {
        "recording_id": "rec-1",
        "leg": "mixed",
        "blob_id": "blob-1",
        "container": "wav",
        "consent_ref": "consent-1",
    }

    assert RecordingStart(upload_url_ref="upload:ref-1", **base).upload_url_ref == (
        "upload:ref-1"
    )
    with pytest.raises(ValidationError):
        RecordingStart(upload_url_ref="a" * 129, **base)


def test_audio_ref_rejects_non_opaque_blob_id():
    with pytest.raises(ValidationError):
        Tracer(exporters=[], record_audio=True).audio_ref(
            session_id="sess-invalid",
            blob_id="person@example.com",
        )


def test_simulator_uploads_real_dual_leg_wavs_and_emits_audio_refs():
    result = asyncio.run(_record_dual_leg_call())
    repeated = asyncio.run(_record_dual_leg_call())

    assert result["legs"] == ["caller", "agent"]
    assert result["wave_shapes"] == [(1, 2, 8000, 800), (1, 2, 8000, 800)]
    assert result["distinct_hashes"] is True
    assert result["stored_hashes"] == repeated["stored_hashes"]
    assert result["audio_ref_legs"] == ["caller", "agent"]
    assert result["audio_ref_consent"] == {"consent-42"}
    assert result["metadata_matches"] is True


def test_simulator_reports_unknown_upload_reference_without_creating_blob():
    store = LocalBlobStore("http://127.0.0.1:1")
    gateway = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        recording_upload_resolver=store.resolve_upload_target,
    )
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-mixed", "blob-mixed"]).__next__,
    )
    directive = asyncio.run(coordinator.start("sess_sim", consent_ref="consent-mixed"))[
        0
    ]
    invalid = directive.model_copy(update={"upload_url_ref": "missing-ref"})

    events = asyncio.run(gateway.execute_recording(invalid))

    assert isinstance(events[-1].payload, RecordingFailed)
    assert events[-1].payload.error_code == "upload_target_missing"
    assert asyncio.run(coordinator.recording_failed(events[-1].payload)) is True
    assert store.blob_count == 0
    assert store.prepared_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("leg", "agent"),
        ("blob_id", "other-blob"),
        ("upload_url_ref", "other-ref"),
        ("container", "mp3"),
        ("consent_ref", "other-consent"),
    ],
)
def test_coordinator_rejects_tampered_upload_metadata(field, value):
    store = LocalBlobStore("http://127.0.0.1:1")
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter], record_audio=True)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        tracer,
        id_factory=iter(["rec-1", "blob-1"]).__next__,
    )
    directive = asyncio.run(coordinator.start("sess-1", consent_ref="consent-1"))[0]
    uploaded = RecordingUploaded(
        recording_id=directive.recording_id,
        leg=directive.leg,
        blob_id=directive.blob_id,
        upload_url_ref=directive.upload_url_ref,
        duration_ms=100,
        byte_count=1644,
        sha256="a" * 64,
        container=directive.container,
        consent_ref=directive.consent_ref,
    ).model_copy(update={field: value})

    assert asyncio.run(coordinator.recording_uploaded(uploaded)) is False
    tracer.flush()
    assert exporter.events == []
    if field in {"blob_id", "upload_url_ref"}:
        assert store.prepared_count == 1
        assert asyncio.run(coordinator.cancel(directive.recording_id)) is True
    else:
        assert store.prepared_count == 0
        assert asyncio.run(coordinator.cancel(directive.recording_id)) is False
    assert store.prepared_count == 0


def test_coordinator_rejects_fabricated_completion_without_uploaded_blob():
    store = LocalBlobStore("http://127.0.0.1:1")
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter], record_audio=True)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        tracer,
        id_factory=iter(["rec-forged", "blob-forged"]).__next__,
    )
    directive = asyncio.run(
        coordinator.start("sess-forged", consent_ref="consent-forged")
    )[0]
    forged = RecordingUploaded(
        recording_id=directive.recording_id,
        leg=directive.leg,
        blob_id=directive.blob_id,
        upload_url_ref=directive.upload_url_ref,
        duration_ms=100,
        byte_count=1644,
        sha256="a" * 64,
        container=directive.container,
        consent_ref=directive.consent_ref,
    )

    assert asyncio.run(coordinator.recording_uploaded(forged)) is False
    tracer.flush()
    assert exporter.events == []
    assert store.prepared_count == 0
    assert store.blob_count == 0


@pytest.mark.parametrize(
    ("status_code", "retryable"), [(400, False), (408, True), (503, True)]
)
def test_http_upload_failure_emits_failed_and_coordinator_cleans_target(
    status_code, retryable
):
    result = asyncio.run(_failed_http_upload(status_code))

    assert result == {
        "error_code": "upload_failed",
        "retryable": retryable,
        "prepared_count": 0,
        "blob_count": 0,
    }


def test_partial_dual_target_preparation_and_explicit_cancel_leave_no_targets():
    failing_store = RecordingBlobStoreSimulator(fail_on_prepare=2)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="dual"),
        failing_store,
        Tracer(exporters=[], record_audio=True),
        id_factory=_ids(),
    )
    with pytest.raises(RuntimeError, match="target 2 failed"):
        asyncio.run(coordinator.start("sess-partial", consent_ref="consent"))
    assert failing_store.prepared == []

    store = LocalBlobStore("http://127.0.0.1:1")
    cancellable = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-cancel", "blob-cancel"]).__next__,
    )
    directive = asyncio.run(cancellable.start("sess-cancel", consent_ref="consent"))[0]
    assert asyncio.run(cancellable.cancel(directive.recording_id)) is True
    assert store.prepared_count == 0


def test_cancel_retains_plan_and_attempts_both_cleanup_steps_after_store_failure():
    store = RecordingBlobStoreSimulator(fail_on_discard_upload=1)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-cleanup", "blob-cleanup"]).__next__,
    )
    directive = asyncio.run(coordinator.start("sess-cleanup", consent_ref="consent"))[0]

    with pytest.raises(RecordingCleanupError):
        asyncio.run(coordinator.cancel(directive.recording_id))

    assert store.discard_blob_calls == 1
    assert store.prepared == [directive.upload_url_ref]
    assert asyncio.run(coordinator.cancel(directive.recording_id)) is True
    assert store.prepared == []


def test_partial_start_cleanup_failure_keeps_plan_retryable():
    store = RecordingBlobStoreSimulator(
        fail_on_prepare=2,
        fail_on_discard_upload=1,
    )
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="dual"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=_ids(),
    )

    with pytest.raises(RecordingCleanupError):
        asyncio.run(coordinator.start("sess-cleanup", consent_ref="consent"))

    assert store.discard_blob_calls == 1
    assert asyncio.run(coordinator.cancel("rec-caller")) is True
    assert store.prepared == []


def test_blob_store_rejects_live_target_and_receipt_reference_collisions():
    assert asyncio.run(_reject_upload_reference_collisions()) == (1, 1)


def test_cancellation_during_second_prepare_cleans_first_target():
    assert asyncio.run(_cancel_during_second_prepare()) == []


def test_cancellation_during_cleanup_propagates_and_keeps_plan_retryable():
    assert asyncio.run(_cancel_during_cleanup()) == (1, True, [])


def test_duplicate_recording_id_is_rejected_before_second_upload_preparation():
    store = LocalBlobStore("http://127.0.0.1:1")
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(
            ["rec-duplicate", "blob-first", "rec-duplicate", "blob-second"]
        ).__next__,
    )
    first = asyncio.run(coordinator.start("sess-first", consent_ref="consent"))[0]

    with pytest.raises(ValueError, match="recording id already exists"):
        asyncio.run(coordinator.start("sess-second", consent_ref="consent"))

    assert store.prepared_count == 1
    assert asyncio.run(coordinator.cancel(first.recording_id)) is True
    assert store.prepared_count == 0


def test_concurrent_duplicate_recording_id_is_reserved_before_prepare_yields():
    assert asyncio.run(_reject_concurrent_recording_id_collision()) == (1, True, [])


def test_failed_prepare_releases_recording_id_reservation_for_retry():
    assert asyncio.run(_retry_recording_id_after_prepare_rollback()) == (2, True, [])


def test_simulator_rejects_unsupported_container_and_non_loopback_upload():
    store = LocalBlobStore("http://127.0.0.1:1")
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-safe", "blob-safe"]).__next__,
    )
    directive = asyncio.run(coordinator.start("sess-safe", consent_ref="consent"))[0]
    unsupported = directive.model_copy(update={"container": "mp3"})
    gateway = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        recording_upload_resolver=lambda _: RecordingUploadTarget(
            url="http://169.254.169.254/audio",
            headers={"content-type": "audio/wav"},
        ),
    )

    unsupported_events = asyncio.run(gateway.execute_recording(unsupported))
    assert unsupported_events[-1].payload.error_code == "unsupported_container"
    unsafe_events = asyncio.run(gateway.execute_recording(directive))
    assert unsafe_events[-1].payload.error_code == "upload_target_invalid"
    assert unsafe_events[-1].payload.retryable is False
    assert asyncio.run(coordinator.recording_failed(unsafe_events[-1].payload)) is True
    assert store.prepared_count == 0


def test_late_recording_directive_is_merged_into_live_control_stream():
    result = asyncio.run(_late_recording_directive())

    assert result == ["recording.started", "recording.uploaded"]


def test_local_blob_server_rejects_wrong_content_type_and_oversize_body():
    statuses, blob_count = asyncio.run(_invalid_blob_uploads())

    assert statuses == [415, 413]
    assert blob_count == 0


def test_recording_upload_target_repr_redacts_signed_material():
    target = RecordingUploadTarget(
        url="https://storage.example/object?signature=do-not-log",
        headers={"authorization": "do-not-log"},
    )

    assert repr(target) == "RecordingUploadTarget(url=<redacted>, headers=<redacted>)"


@pytest.mark.parametrize("upload_error_status", [None, 503])
def test_gateway_http_logs_redact_signed_upload_target(
    upload_error_status,
    caplog,
):
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        with caplog.at_level(logging.DEBUG, logger="httpcore"):
            upload_ref, upload_url = asyncio.run(
                _capture_gateway_upload_logs(upload_error_status)
            )

    transport_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(("httpx", "httpcore"))
    )
    assert upload_ref not in transport_logs
    assert upload_url not in transport_logs
    assert "<redacted-upload-url>" in transport_logs


@pytest.mark.parametrize(
    "url",
    [
        "https://storage.example/private-bearer-token",
        "https://storage.example/private-bearer-token?signature=query-secret",
    ],
)
def test_gateway_log_filter_redacts_entire_upload_url(url):
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        0,
        "HTTP Request: PUT %s",
        (url,),
        None,
    )

    assert _SignedUploadLogFilter().filter(record) is True
    rendered = record.getMessage()
    assert "private-bearer-token" not in rendered
    assert "query-secret" not in rendered
    assert rendered == "HTTP Request: PUT <redacted-upload-url>"


def test_gateway_does_not_follow_recording_upload_redirects():
    assert asyncio.run(_redirected_gateway_upload()) == ("upload_failed", 0)


def test_real_http_timeout_and_task_cancellation_cleanup_targets():
    timeout_result = asyncio.run(_timed_out_upload())
    cancellation_result = asyncio.run(_cancelled_upload())

    assert timeout_result == ("upload_failed", True, 0)
    assert cancellation_result == (0, 0)


def test_revoked_http_upload_finishes_before_zero_blob_assertion():
    assert asyncio.run(_revoke_live_upload()) == (410, 0, 0)


def test_failure_after_persisted_upload_deletes_blob_and_receipt():
    assert asyncio.run(_uploaded_then_failed()) == (0, 0)


def test_blob_id_cannot_reuse_stale_object_or_receipt():
    assert asyncio.run(_reject_stale_blob_reuse()) is True


def test_tampered_recording_id_cleans_correlated_uploaded_blob():
    assert asyncio.run(_reject_tampered_recording_id()) == (False, 0, 0)


def test_swapped_live_recording_id_cleans_upload_owner_not_claimed_plan():
    assert asyncio.run(_reject_swapped_live_recording_id()) == (False, 0, 1, True)


async def _record_dual_leg_call():
    async with _running_blob_store() as store:
        exporter = InMemoryTraceExporter()
        tracer = Tracer(
            exporters=[exporter],
            record_audio=True,
            clock=lambda: 1000,
            id_factory=iter(
                [
                    "069b3f9b-39f4-5ee7-a195-61e3fd85f52a",
                    "2794c550-b2df-5c6c-b3fb-a30212a13db1",
                ]
            ).__next__,
        )
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="dual"),
            store,
            tracer,
            id_factory=_ids(),
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directives = await coordinator.start("sess_sim", consent_ref="consent-42")
        stream = gateway.events()
        started = await stream.__anext__()
        assert "recording" in started.payload.features
        for index, directive in enumerate(directives, start=1):
            await gateway.send(
                Envelope(
                    type="recording.start",
                    session_id="sess_sim",
                    seq=index,
                    ts_ms=index,
                ),
                directive,
            )
        uploaded = []
        for _ in directives:
            recording_started = await stream.__anext__()
            assert recording_started.envelope.type == "recording.started"
            event = (await stream.__anext__()).payload
            assert isinstance(event, RecordingUploaded)
            assert await coordinator.recording_uploaded(event) is True
            assert await coordinator.recording_uploaded(event) is False
            uploaded.append(event)
        tracer.flush()

        assert store.prepared_count == 0
        assert store.blob_count == 2

        shapes = []
        stored_hashes = []
        for event in uploaded:
            blob = store.read(event.blob_id)
            stored_hashes.append(hashlib.sha256(blob).hexdigest())
            assert event.byte_count == len(blob)
            assert event.sha256 == stored_hashes[-1]
            assert event.duration_ms == 100
            assert event.container == "wav"
            with wave.open(io.BytesIO(blob), "rb") as wav:
                shapes.append(
                    (
                        wav.getnchannels(),
                        wav.getsampwidth(),
                        wav.getframerate(),
                        wav.getnframes(),
                    )
                )
        audio_refs = [event for event in exporter.events if event.type == "audio_ref"]
        metadata_matches = all(
            audio_ref.blob_id == uploaded_event.blob_id
            and audio_ref.recording_id == uploaded_event.recording_id
            and audio_ref.leg == uploaded_event.leg
            and audio_ref.duration_ms == uploaded_event.duration_ms
            and audio_ref.byte_count == uploaded_event.byte_count
            and audio_ref.sha256 == uploaded_event.sha256
            and audio_ref.container == uploaded_event.container
            and audio_ref.consent_ref == uploaded_event.consent_ref
            and audio_ref.upload_url_requested is True
            for audio_ref, uploaded_event in zip(audio_refs, uploaded)
        )
        return {
            "legs": [event.leg for event in uploaded],
            "wave_shapes": shapes,
            "distinct_hashes": len({event.sha256 for event in uploaded}) == 2,
            "stored_hashes": stored_hashes,
            "audio_ref_legs": [event.leg for event in audio_refs],
            "audio_ref_consent": {event.consent_ref for event in audio_refs},
            "metadata_matches": metadata_matches,
        }


async def _failed_http_upload(status_code):
    async with _running_blob_store(upload_error_status=status_code) as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-failed", "blob-failed"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (await coordinator.start("sess_sim", consent_ref="consent"))[0]
        failed = (await gateway.execute_recording(directive))[-1].payload
        assert isinstance(failed, RecordingFailed)
        assert await coordinator.recording_failed(failed) is True
        return {
            "error_code": failed.error_code,
            "retryable": failed.retryable,
            "prepared_count": store.prepared_count,
            "blob_count": store.blob_count,
        }


async def _capture_gateway_upload_logs(upload_error_status):
    async with _running_blob_store(upload_error_status=upload_error_status) as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-log-redaction", "blob-log-redaction"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (
            await coordinator.start("sess-log-redaction", consent_ref="consent")
        )[0]
        upload_url = store.resolve_upload_target(directive.upload_url_ref).url
        result = (await gateway.execute_recording(directive))[-1].payload
        if isinstance(result, RecordingUploaded):
            assert await coordinator.recording_uploaded(result) is True
        else:
            assert isinstance(result, RecordingFailed)
            assert await coordinator.recording_failed(result) is True
        return directive.upload_url_ref, upload_url


async def _redirected_gateway_upload():
    listener, base_url = _bound_listener()
    app = FastAPI()
    sentinel_visits = 0

    @app.put("/upload")
    async def redirect() -> Response:
        return Response(status_code=307, headers={"location": f"{base_url}/sentinel"})

    @app.put("/sentinel")
    async def sentinel() -> Response:
        nonlocal sentinel_visits
        sentinel_visits += 1
        return Response(status_code=204)

    directive = RecordingStart(
        recording_id="recording-redirect",
        leg="mixed",
        blob_id="blob-redirect",
        upload_url_ref="upload-redirect",
        container="wav",
        consent_ref="consent-redirect",
    )
    gateway = LocalGatewaySimulator(
        booking_happy_path(),
        ManualClock(),
        recording_upload_resolver=lambda _: RecordingUploadTarget(
            url=f"{base_url}/upload?signature=opaque",
            headers={"content-type": "audio/wav", "if-none-match": "*"},
        ),
    )
    async with _serving_app(app, listener):
        failed = (await gateway.execute_recording(directive))[-1].payload
    assert isinstance(failed, RecordingFailed)
    return failed.error_code, sentinel_visits


async def _late_recording_directive():
    async with _running_blob_store() as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-late", "blob-late"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (await coordinator.start("sess_sim", consent_ref="consent"))[0]
        stream = gateway.events()
        await stream.__anext__()
        vad_start = await stream.__anext__()
        assert vad_start.envelope.type == "vad.speech_start"
        partial = await stream.__anext__()
        assert partial.envelope.type == "stt.partial"
        await gateway.send(
            Envelope(type="recording.start", session_id="sess_sim", seq=1, ts_ms=1),
            directive,
        )
        events = [await stream.__anext__(), await stream.__anext__()]
        uploaded = events[-1].payload
        assert isinstance(uploaded, RecordingUploaded)
        assert await coordinator.recording_uploaded(uploaded) is True
        return [event.envelope.type for event in events]


async def _invalid_blob_uploads():
    async with _running_blob_store(max_blob_bytes=4) as store:
        first_ref = await store.prepare_upload("blob-type")
        second_ref = await store.prepare_upload("blob-size")
        async with httpx.AsyncClient() as client:
            first_target = store.resolve_upload_target(first_ref)
            second_target = store.resolve_upload_target(second_ref)
            wrong_type = await client.put(
                first_target.url,
                content=b"data",
                headers={**first_target.headers, "content-type": "text/plain"},
            )
            too_large = await client.put(
                second_target.url,
                content=b"12345",
                headers=second_target.headers,
            )
        await store.discard_upload(first_ref)
        await store.discard_upload(second_ref)
        return [wrong_type.status_code, too_large.status_code], store.blob_count


async def _cancel_during_second_prepare():
    store = RecordingBlobStoreSimulator(block_on_prepare=2)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="dual"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=_ids(),
    )
    task = asyncio.create_task(coordinator.start("sess-cancel", consent_ref="consent"))
    await store.prepare_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return store.prepared


async def _cancel_during_cleanup():
    store = RecordingBlobStoreSimulator(block_on_discard_upload=1)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-cleanup-cancel", "blob-cleanup-cancel"]).__next__,
    )
    directive = (await coordinator.start("sess-cancel", consent_ref="consent"))[0]
    task = asyncio.create_task(coordinator.cancel(directive.recording_id))
    await store.discard_upload_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cleanup_attempts = store.discard_blob_calls
    retry_succeeded = await coordinator.cancel(directive.recording_id)
    return cleanup_attempts, retry_succeeded, store.prepared


async def _reject_concurrent_recording_id_collision():
    store = RecordingBlobStoreSimulator(block_on_prepare=1)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(["rec-concurrent", "blob-first", "rec-concurrent"]).__next__,
    )
    first_task = asyncio.create_task(
        coordinator.start("sess-first", consent_ref="consent")
    )
    await store.prepare_started.wait()

    with pytest.raises(ValueError, match="recording id already exists"):
        await coordinator.start("sess-second", consent_ref="consent")

    store.release_prepare.set()
    first = (await first_task)[0]
    cancelled = await coordinator.cancel(first.recording_id)
    return store.prepare_calls, cancelled, store.prepared


async def _retry_recording_id_after_prepare_rollback():
    store = RecordingBlobStoreSimulator(fail_on_prepare=1)
    coordinator = RecordingCoordinator(
        RecordingSpec(enabled=True, channels="mixed"),
        store,
        Tracer(exporters=[], record_audio=True),
        id_factory=iter(
            ["rec-retry", "blob-failed", "rec-retry", "blob-success"]
        ).__next__,
    )
    with pytest.raises(RuntimeError, match="target 1 failed"):
        await coordinator.start("sess-failed", consent_ref="consent")

    retried = (await coordinator.start("sess-retry", consent_ref="consent"))[0]
    cancelled = await coordinator.cancel(retried.recording_id)
    return store.prepare_calls, cancelled, store.prepared


async def _timed_out_upload():
    gate = asyncio.Event()
    started = asyncio.Event()
    async with _running_blob_store(upload_gate=gate, upload_started=started) as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-timeout", "blob-timeout"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
            recording_upload_timeout_seconds=0.01,
        )
        directive = (await coordinator.start("sess_sim", consent_ref="consent"))[0]
        task = asyncio.create_task(gateway.execute_recording(directive))
        await started.wait()
        failed = (await task)[-1].payload
        assert isinstance(failed, RecordingFailed)
        await coordinator.recording_failed(failed)
        gate.set()
        return failed.error_code, failed.retryable, store.prepared_count


async def _cancelled_upload():
    gate = asyncio.Event()
    started = asyncio.Event()
    async with _running_blob_store(upload_gate=gate, upload_started=started) as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-cancel-http", "blob-cancel-http"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (await coordinator.start("sess_sim", consent_ref="consent"))[0]
        task = asyncio.create_task(gateway.execute_recording(directive))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await coordinator.cancel(directive.recording_id)
        gate.set()
        return store.prepared_count, store.blob_count


async def _revoke_live_upload():
    gate = asyncio.Event()
    started = asyncio.Event()
    async with _running_blob_store(upload_gate=gate, upload_started=started) as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-revoke", "blob-revoke"]).__next__,
        )
        directive = (await coordinator.start("sess-revoke", consent_ref="consent"))[0]
        upload_target = store.resolve_upload_target(directive.upload_url_ref)
        async with httpx.AsyncClient() as client:
            upload_task = asyncio.create_task(
                client.put(
                    upload_target.url,
                    content=_wav_bytes(),
                    headers=upload_target.headers,
                )
            )
            await started.wait()
            assert await coordinator.cancel(directive.recording_id) is True
            gate.set()
            response = await upload_task
        return response.status_code, store.prepared_count, store.blob_count


async def _uploaded_then_failed():
    async with _running_blob_store() as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-persisted", "blob-persisted"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (await coordinator.start("sess_sim", consent_ref="consent"))[0]
        uploaded = (await gateway.execute_recording(directive))[-1].payload
        assert isinstance(uploaded, RecordingUploaded)
        assert store.blob_count == 1
        failed = RecordingFailed(
            recording_id=directive.recording_id,
            error_code="completion_ambiguous",
            retryable=True,
        )
        assert await coordinator.recording_failed(failed) is True
        return store.prepared_count, store.blob_count


async def _reject_stale_blob_reuse():
    refs = iter(["old", "new"])
    async with _running_blob_store(ref_factory=refs.__next__) as store:
        old_ref = await store.prepare_upload("blob-stale")
        old_event = await _upload_directly(store, "rec-old", "blob-stale", old_ref)
        await store.discard_blob("blob-stale")

        new_ref = await store.prepare_upload("blob-stale")
        new_event = await _upload_directly(store, "rec-new", "blob-stale", new_ref)

        old_rejected = not await store.confirm_upload(old_event)
        new_accepted = await store.confirm_upload(new_event)
        return old_rejected and new_accepted


async def _reject_upload_reference_collisions():
    async with _running_blob_store(ref_factory=lambda: "same") as store:
        first_ref = await store.prepare_upload("blob-live")
        with pytest.raises(ValueError, match="upload reference already exists"):
            await store.prepare_upload("blob-live-collision")
        live_target_count = store.prepared_count

        await _upload_directly(store, "rec-live", "blob-live", first_ref)
        with pytest.raises(ValueError, match="upload reference already exists"):
            await store.prepare_upload("blob-receipt-collision")
        return live_target_count, store.blob_count


async def _reject_tampered_recording_id():
    async with _running_blob_store() as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="mixed"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=iter(["rec-real", "blob-real"]).__next__,
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        directive = (await coordinator.start("sess-real", consent_ref="consent"))[0]
        uploaded = (await gateway.execute_recording(directive))[-1].payload
        assert isinstance(uploaded, RecordingUploaded)
        tampered = uploaded.model_copy(update={"recording_id": "rec-tampered"})
        accepted = await coordinator.recording_uploaded(tampered)
        return accepted, store.prepared_count, store.blob_count


async def _reject_swapped_live_recording_id():
    async with _running_blob_store() as store:
        coordinator = RecordingCoordinator(
            RecordingSpec(enabled=True, channels="dual"),
            store,
            Tracer(exporters=[], record_audio=True),
            id_factory=_ids(),
        )
        gateway = LocalGatewaySimulator(
            booking_happy_path(),
            ManualClock(),
            recording_upload_resolver=store.resolve_upload_target,
        )
        caller, agent = await coordinator.start("sess-swap", consent_ref="consent")
        uploaded = (await gateway.execute_recording(caller))[-1].payload
        assert isinstance(uploaded, RecordingUploaded)
        swapped = uploaded.model_copy(update={"recording_id": agent.recording_id})

        accepted = await coordinator.recording_uploaded(swapped)
        blob_count = store.blob_count
        prepared_count = store.prepared_count
        claimed_plan_retained = await coordinator.cancel(agent.recording_id)
        return accepted, blob_count, prepared_count, claimed_plan_retained


async def _upload_directly(store, recording_id, blob_id, upload_ref):
    body = _wav_bytes()
    target = store.resolve_upload_target(upload_ref)
    async with httpx.AsyncClient() as client:
        response = await client.put(
            target.url,
            content=body,
            headers=target.headers,
        )
    assert response.status_code == 204
    return RecordingUploaded(
        recording_id=recording_id,
        leg="mixed",
        blob_id=blob_id,
        upload_url_ref=upload_ref,
        duration_ms=100,
        byte_count=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        container="wav",
        consent_ref="consent",
    )


def _wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)
    return output.getvalue()


@asynccontextmanager
async def _running_blob_store(
    upload_error_status=None,
    max_blob_bytes=10 * 1024 * 1024,
    upload_gate=None,
    upload_started=None,
    ref_factory=None,
):
    listener, base_url = _bound_listener()
    store = LocalBlobStore(
        base_url,
        upload_error_status=upload_error_status,
        max_blob_bytes=max_blob_bytes,
        upload_gate=upload_gate,
        upload_started=upload_started,
        ref_factory=ref_factory,
    )
    async with _serving_app(store.app, listener):
        yield store


def _bound_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    return listener, f"http://127.0.0.1:{listener.getsockname()[1]}"


@asynccontextmanager
async def _serving_app(app, listener):
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0)
    try:
        yield
    finally:
        server.should_exit = True
        await task
