# 73 - Add lucy-cloud blob presign client

**Sprint:** S7 - Cloud observability seam
**Epic:** Observability
**Estimated effort:** ~6 h
**Depends on:** 30, 71, 118*
**State:** done

## Goal

Let the open `lucy-cloud` package request recording upload targets from the
closed platform while keeping audio bytes in the media plane and out of Python
application code.

## Context primer

- `agents.md` - no secrets in code and typed configuration.
- `docs/adr/0014-media-plane-recording.md` - blob upload boundary.
- `docs/telemetry-wire-v1.md` - blob upload contract section to extend.
- `packages/lucy-cloud/` - cloud exporter package from card 30.
- `src/lucy/recording.py` - `BlobStore` Protocol from card 71.

## Spec

Add `CloudBlobStore` to `packages/lucy-cloud` as the hosted extension of the
SDK `BlobStore` seam. Preserve the frozen `prepare_upload(blob_id)` ABI and add
`prepare_upload_with_context` for the session, leg, and consent context required
by hosted tenancy. The coordinator detects the extension and legacy stores keep
working unchanged. Authentication and environment resolution use the same API
key and endpoint configuration as the cloud exporter.

The client calls platform `POST /v1/blobs` to negotiate a presigned PUT,
requires signed `content-type: audio/wav` and `if-none-match: *`, and resolves
the opaque control reference to one `RecordingUploadTarget` containing both URL
and headers. Presigned origins use an exact scheme/host/port allowlist,
additional headers are rejected, redirects are disabled, and signed targets
are redacted from representations and transport logs. The media gateway
consumes that complete target. After
`recording.uploaded`, `POST /v1/blobs/{blob_id}/complete` sends the gateway's
duration, SHA-256, and byte count and validates the immutable response including
`storage_container`.

The platform-118 presign request carries Lucy's candidate `blob_id`, and the
platform returns that same external identifier while namespacing its private
object key by project. This is the pre-public contract alignment required by
the failure protocol: card 79 originally generated a different identifier,
which could not remain the `audio_ref` join key across the control channel and
platform recording row.

Cancellation calls authenticated `DELETE /v1/blobs/{blob_id}` so the platform
persists a tombstone and performs immediate plus post-expiry deletion. Abort
cancellation retains local state for retry; ordinary presign, completion, and
abort failures suppress recording, increment a local warning/drop counter, and
never crash the call. Ambiguous transport failure after dispatch or an invalid
`201` performs a compensating abort; explicit non-`201` responses do not claim
a reservation. Single-flight close rejects new work, cancels and drains
in-flight presigns and completions, rejects late completion responses, then
aborts every resolved reservation; caller cancellation waits for cleanup and
credential zeroization. The wire and ADR document the trust split and late-PUT
cleanup guarantee.

## Files to create/modify

- `packages/lucy-cloud/src/lucy_cloud/blob.py` - `CloudBlobStore`.
- `packages/lucy-cloud/src/lucy_cloud/config.py` - shared cloud environment.
- `packages/lucy-cloud/src/lucy_cloud/client.py` - shared URL validation.
- `packages/lucy-cloud/src/lucy_cloud/exporter.py` - shared configuration use.
- `packages/lucy-cloud/lucy_cloud_tests/test_blob.py` - ASGI platform fixture tests.
- `docs/telemetry-wire-v1.md` - blob upload section.
- `docs/adr/0014-media-plane-recording.md` - trust and cleanup decision.
- `src/lucy/recording.py` - ABI-compatible contextual extension and target.
- `src/lucy/transport/dev_gateway.py` - consume URL plus signed headers.
- `tests/test_recording.py` - legacy ABI and real HTTP media-plane coverage.
- `packages/lucy-cloud/src/lucy_cloud/__init__.py` - public export.
- `Dockerfile.api`, `pyproject.toml`, `tests/test_package_metadata.py` - collect
  the namespaced cloud suite in the sanctioned root Docker gate.
- `README.md` - current Rust media-plane boundary.

## Chips

- [x] **C1 - Presign client.** Write failing tests against an in-process ASGI platform fixture, then implement `prepare_upload_with_context` and atomic target resolution. Files: `packages/lucy-cloud/src/lucy_cloud/blob.py`, `packages/lucy-cloud/lucy_cloud_tests/test_blob.py`. Verify: `.venv/bin/python -m pytest packages/lucy-cloud/lucy_cloud_tests/test_blob.py -q -k presign` -> selected tests pass.
- [x] **C2 - Complete, abort, and fail-open.** Add tests for immutable completion fields, 401, 429, malformed responses, independent transport timeouts, cancellation-safe abort, and outstanding-target close, then implement fail-open handling. Files: `packages/lucy-cloud/src/lucy_cloud/blob.py`, `packages/lucy-cloud/lucy_cloud_tests/test_blob.py`. Verify: `.venv/bin/python -m pytest packages/lucy-cloud/lucy_cloud_tests/test_blob.py -q` -> blob tests pass.
- [x] **C3 - Docs and gates.** Export the class, document blob uploads, run package and SDK gates, and move the card. Files: `packages/lucy-cloud/src/lucy_cloud/__init__.py`, `docs/telemetry-wire-v1.md`. Verify: `docker compose run --rm lucy-api pytest` -> 962 passed, 2 deselected.

## Do NOT

- Do not use mocks or mocking frameworks; use an in-process ASGI app and real httpx transport.
- Do not hardcode endpoints, API keys, retry windows, blob paths, or content types outside typed settings/constants.
- Do not upload audio bytes through Python application code.
- Do not add platform server implementation to the SDK repo.

## Definition of Done

- [x] `.venv/bin/python -m pytest packages/lucy-cloud/lucy_cloud_tests/test_blob.py -q` -> 66 passed
- [x] `docker compose run --rm lucy-api pytest` -> 962 passed, 2 deselected
- [x] Docker ruff, format check, and mypy gates are clean
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If platform card 118 changes the blob API shape, stop and align the normative
wire plus both repositories before implementing a divergent client.

## Improvements noted

- The root Docker test path did not collect card 73's cloud tests; this card
  installs `lucy-cloud` in the image, moves the package suite to the unique
  `lucy_cloud_tests` namespace, and collects the entire suite in the canonical
  root gate.
- The first design changed `BlobStore.prepare_upload`; review caught the frozen
  ABI violation. The final design preserves the method and uses an additive
  contextual extension detected by the coordinator.
- Review also found signed-target repr/log exposure, permissive target
  forwarding, and presign close races. The final client redacts signed material,
  enforces exact origins and headers with no redirects, compensates failed
  reservations, and drains in-flight presigns before close.
- Existing FastAPI/httpx and websocket deprecation warnings remain owned by
  launch-base card 93 and platform dependency card 108; no new follow-up is
  needed here.

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

- code-reviewer (`019f5cbc-f4f6-7492-85c0-bd3e8631fa9c`): PASS - public ABI, lifecycle, packaging, Docker suite, and static gates are clean.
- test-auditor (`019f5cc0-cf0a-76c2-8aa1-6d434a2b2afe`): PASS - negative paths cover reservation ambiguity, explicit rejection, origin enforcement, concurrent close, and cancellation without mocks.
- docs-reviewer (`019f5cc0-d41f-7a33-9a7e-95505e1eda92`): PASS - wire, ADR, README, required consent, and in-flight close semantics match the implementation.
- simplicity-reviewer (`019f5cc0-d839-7202-b70d-be4fae31271e`): PASS - one close task and one in-flight task set govern shutdown without duplicate state.
- security-reviewer (`019f5cb6-4b74-7863-857c-218204fc7578`): PASS - exact origins, redirect rejection, credential zeroization, signed-target redaction, and fail-safe cleanup are enforced.

Findings disposition:

- Review findings around the frozen `BlobStore` ABI, signed URL leakage, SSRF origin matching, cleanup cancellation, duplicate presigns, and confirm/close races were resolved before the final PASS reviews.
