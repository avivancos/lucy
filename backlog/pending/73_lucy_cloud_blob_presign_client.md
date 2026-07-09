# 73 - Add lucy-cloud blob presign client

**Sprint:** S7 - Cloud observability seam
**Epic:** Observability
**Estimated effort:** ~6 h
**Depends on:** 30, 71, 79*
**State:** pending

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

Add `CloudBlobStore` to `packages/lucy-cloud`, implementing the SDK
`BlobStore` Protocol. It calls platform `POST /v1/blobs` to negotiate a
presigned PUT and `POST /v1/blobs/{blob_id}/complete` after upload metadata is
known. Authentication uses the same API key and endpoint configuration as the
cloud exporter.

The client is fail-open from the call's perspective: presign failures suppress
recording and emit a local warning/drop counter, never crashing the call. The
wire docs gain a "Blob uploads" section describing request and response shapes.

## Files to create/modify

- `packages/lucy-cloud/src/lucy_cloud/blob.py` - `CloudBlobStore`.
- `packages/lucy-cloud/tests/test_blob.py` - ASGI platform fixture tests.
- `docs/telemetry-wire-v1.md` - blob upload section.
- `packages/lucy-cloud/src/lucy_cloud/__init__.py` - public export.

## Chips

- [ ] **C1 - Presign client.** Write failing tests against an in-process ASGI platform fixture, then implement `presign_put`. Files: `packages/lucy-cloud/src/lucy_cloud/blob.py`, `packages/lucy-cloud/tests/test_blob.py`. Verify: `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_blob.py -q -k presign` -> selected tests pass.
- [ ] **C2 - Complete and fail-open.** Add tests for complete, 401, 429, and unreachable endpoint behavior, then implement fail-open handling. Files: `packages/lucy-cloud/src/lucy_cloud/blob.py`, `packages/lucy-cloud/tests/test_blob.py`. Verify: `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_blob.py -q` -> blob tests pass.
- [ ] **C3 - Docs and gates.** Export the class, document blob uploads, run package and SDK gates, and move the card. Files: `packages/lucy-cloud/src/lucy_cloud/__init__.py`, `docs/telemetry-wire-v1.md`. Verify: `docker compose run --rm lucy-api pytest` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use an in-process ASGI app and real httpx transport.
- Do not hardcode endpoints, API keys, retry windows, blob paths, or content types outside typed settings/constants.
- Do not upload audio bytes through Python application code.
- Do not add platform server implementation to the SDK repo.

## Definition of Done

- [ ] `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_blob.py -q` -> blob tests pass
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If platform card 79 changes the blob API shape, stop and align the card spec
before implementing a divergent client.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
