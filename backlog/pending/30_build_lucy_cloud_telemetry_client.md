# 30 - Build lucy-cloud telemetry client

**Sprint:** S7 - Cloud observability seam
**Epic:** Observability
**Estimated effort:** ~7 h
**Depends on:** 24
**State:** pending

## Goal

The open client for the closed platform (ADR 0010): one env var
(`LUCY_API_KEY`) flips local tracing to cloud export, with LangSmith-grade
ergonomics. Ship package `lucy-cloud` implementing the wire-v1 batching,
idempotency, and fail-open rules so telemetry never adds latency to a voice
turn and never crashes a call.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  thresholds or URLs).
- `docs/telemetry-wire-v1.md` - THE normative contract this card implements
  client-side. Every constant, header, status code, and retry rule in this
  card comes from there; when in doubt, the spec wins.
- `docs/adr/0010-open-core-split.md` - the open-client/closed-server
  boundary; the wire document, not shared code, is the contract.
- `docs/adr/0003-no-mocks-testing-policy.md` - why tests use a real
  in-process FastAPI ingest app and a real local HTTP server, never mocks.
- `backlog/pending/24_build_lucy_observe_package.md` - the seam this card
  plugs into: `TraceExporter` protocol, `TelemetryEvent` union, env
  handling, and the `lucy.exporters` entry-point group. Card 24 turns
  `src/lucy/observe.py` into the package `src/lucy/observe/`; read its
  `exporters.py` and `__init__.py` as landed before wiring anything.
- `src/lucy/specs.py` - `ObservabilitySpec` (`redact_pii`, `record_audio`,
  `trace_sample_rate`): privacy runs in core BEFORE export; this card must
  not re-implement it.
- `src/lucy/metrics.py` - `CostBreakdown`; its components feed the wire
  `cost` event payload.
- `tests/test_observability.py` - house test style: plain pytest,
  asyncio_mode auto, no mocks.
- `pyproject.toml` - `httpx`, `fastapi`, `uvicorn` are already core deps;
  the uv workspace conversion is card 28's job, not yours. This package
  must install standalone via `pip install -e packages/lucy-cloud`.

## Spec

New package `lucy-cloud` (import name `lucy_cloud`) under
`packages/lucy-cloud/`, depending on `lucy` and `httpx`.

`packages/lucy-cloud/pyproject.toml`:

- Distribution `lucy-cloud`, version `0.1.0`, src layout
  (`src/lucy_cloud/`).
- Entry point: group `lucy.exporters`, name `cloud`, target
  `lucy_cloud.exporter:CloudTraceExporter` - the exact shape
  `lucy.observe.configure()` discovers (match card 24's landed contract;
  if it differs, follow the Failure protocol, do not patch core here).

`packages/lucy-cloud/src/lucy_cloud/_wire.py` - every wire-v1 literal lives
here as a named constant; nothing inline at call sites:

- `WIRE_VERSION = 1`, `EVENTS_PATH = "/v1/events"`,
  `MAX_BATCH_EVENTS = 100`, `MAX_BATCH_BYTES = 1_048_576` (1 MiB),
  `FLUSH_INTERVAL_S = 2.0`, `HEADER_API_KEY = "x-api-key"`,
  `HEADER_WIRE = "x-lucy-wire"`, `HEADER_IDEMPOTENCY = "idempotency-key"`,
  `RETRY_BASE_S`, `RETRY_MAX_S` (backoff knobs).
- `build_envelope(project: str, sdk_version: str, events: list[dict])
  -> dict`: `{"project": ..., "sdk": {"name": "lucy", "version": ...},
  "events": [...]}`; `sdk_version` read from installed `lucy` dist
  metadata by callers, never hardcoded.
- `plan_batches(events: list[dict]) -> list[list[dict]]`: greedy split so
  no batch exceeds `MAX_BATCH_EVENTS` events or `MAX_BATCH_BYTES` of
  serialized JSON.
- `encode_batch(envelope: dict, *, use_gzip: bool = True) ->
  tuple[bytes, dict[str, str]]`: body plus headers (`content-type:
  application/json`, `x-lucy-wire: 1`, `content-encoding: gzip` when
  compressed).

`packages/lucy-cloud/src/lucy_cloud/client.py`:

- `IngestClient(endpoint: str, api_key: str, *, project: str,
  max_queue: int = 10_000, flush_interval_s: float = FLUSH_INTERVAL_S,
  max_retries: int = 5,
  transport: httpx.AsyncBaseTransport | None = None)` - `transport` lets
  tests inject `httpx.ASGITransport(app=...)` (real client, real ASGI
  app, no network, no mocks).
- `submit(event: dict) -> None`: non-blocking, never raises; when the
  bounded queue is full the event is dropped and counted.
- Background flush task: flushes at least every `flush_interval_s` (wire
  spec: 2 s) and immediately when `plan_batches` caps are hit.
- `async flush() -> None` (force-drain, for deterministic tests) and
  `async aclose() -> None` (flush then shut down).
- `dropped_events: int` property; a `logging.getLogger("lucy_cloud")`
  warning surfaced at most once per flush interval while drops occur.
- Each batch gets ONE `idempotency-key` (uuid4) kept across all retries
  of that batch.
- Retries with jittered exponential backoff on 5xx and 429 ONLY; honor
  `retry-after` on 429; 401/413/422 are never retried - the batch is
  dropped and counted. After `max_retries` the batch is dropped and
  counted. Nothing ever raises into caller code.

`packages/lucy-cloud/src/lucy_cloud/exporter.py`:

- `CloudTraceExporter(*, endpoint: str | None = None,
  api_key: str | None = None, project: str | None = None,
  client: IngestClient | None = None)` implementing the `TraceExporter`
  protocol from `lucy.observe` (`export_batch`). Constructor args win;
  otherwise env: `LUCY_ENDPOINT`, `LUCY_API_KEY`, `LUCY_PROJECT`.
- `CloudTraceExporter.from_env() -> CloudTraceExporter | None`: returns
  `None` when `LUCY_API_KEY` is unset, so `configure()` auto-attaches the
  exporter only when a key exists.
- Converts `TelemetryEvent` objects (card 24's union: session.started,
  session.ended, turn, span, cost, business, tool_call, transcript,
  audio_ref) to wire-v1 dicts. Events arrive post-redaction from
  `lucy.observe`; no redaction logic here.

Tests (no mocks) under `packages/lucy-cloud/tests/`:

- `ingest_app.py`: `create_ingest_app(api_key: str, *,
  rate_limit_after: int | None = None, fail_first_n: int = 0)
  -> FastAPI` plus an `IngestState` recorder. Implements wire v1
  faithfully: `202` on valid batch; `401` on missing/wrong `x-api-key`;
  `413` over `MAX_BATCH_EVENTS`/`MAX_BATCH_BYTES`; `422` listing
  malformed event indices in the body; `429` with `retry-after` when
  `rate_limit_after` trips; `5xx` for the first `fail_first_n` requests;
  gzip decoding; dedupe by `idempotency-key`; unknown event types
  accepted and stored opaquely. Also `run_local_ingest(app) ->
  Iterator[str]` context manager serving it via uvicorn on an ephemeral
  `127.0.0.1` port (a real local protocol server).
- `test_ingest_conformance.py` is parameterized over a base
  URL/transport so platform card 47 can aim the same suite at the real
  ingest: this file IS the conformance contract both sides must pass.

## Chips

- [ ] **C1 - Package scaffold + entry point.** Write
  `packages/lucy-cloud/tests/test_packaging.py` first:
  `test_entry_point_exposes_cloud_exporter` loads entry-point group
  `lucy.exporters`, name `cloud`, and asserts it resolves to
  `CloudTraceExporter`. Then create `packages/lucy-cloud/pyproject.toml`,
  `packages/lucy-cloud/src/lucy_cloud/__init__.py`, and a class skeleton
  in `packages/lucy-cloud/src/lucy_cloud/exporter.py` (constructor
  signature only; behavior lands in C6). Verify:
  `.venv/bin/pip install -e packages/lucy-cloud && .venv/bin/python -m
  pytest packages/lucy-cloud/tests/test_packaging.py -q` -> all pass.
- [ ] **C2 - Wire constants, envelope, batch planning.** Test first in
  `packages/lucy-cloud/tests/test_wire.py`:
  `test_envelope_matches_wire_v1_shape`,
  `test_plan_batches_splits_at_max_events` (101 events -> 2 batches),
  `test_plan_batches_splits_at_max_bytes` (oversized payloads split
  under 1 MiB), `test_encode_batch_gzip_round_trips` (gzip body decodes
  to the same JSON; headers carry `x-lucy-wire: 1`). Implement
  `packages/lucy-cloud/src/lucy_cloud/_wire.py`. Verify:
  `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_wire.py -q`
  -> all pass (>=4 tests).
- [ ] **C3 - Ingest conformance fixture.** Test first in
  `packages/lucy-cloud/tests/test_ingest_conformance.py`:
  `test_valid_batch_accepted_with_202`,
  `test_missing_or_wrong_api_key_rejected_401`,
  `test_oversized_batch_rejected_413`,
  `test_malformed_events_rejected_422_with_indices`,
  `test_429_includes_retry_after_header`,
  `test_duplicate_idempotency_key_stored_once`,
  `test_unknown_event_type_accepted_opaquely`. Drive via
  `httpx.AsyncClient` over `httpx.ASGITransport`. Implement
  `packages/lucy-cloud/tests/ingest_app.py`. Verify:
  `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_ingest_conformance.py -q` -> all pass
  (>=7 tests).
- [ ] **C4 - Bounded queue + background flush.** Test first in
  `packages/lucy-cloud/tests/test_client.py`:
  `test_submit_never_blocks_when_queue_full_drops_and_counts`
  (tiny `max_queue`, server stalled -> `submit` returns instantly,
  `dropped_events` grows), `test_flush_interval_sends_partial_batch`
  (small `flush_interval_s` like 0.05 - configured, not hardcoded),
  `test_batch_caps_trigger_immediate_flush`,
  `test_aclose_flushes_remaining_events`. Implement
  `packages/lucy-cloud/src/lucy_cloud/client.py` happy path against the
  C3 app via injected ASGI transport. Verify:
  `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_client.py
  -q` -> all pass in < 5 s (proves no real 2 s sleeps in tests).
- [ ] **C5 - Retry, idempotency, fail-open.** Test first, extending
  `packages/lucy-cloud/tests/test_client.py`:
  `test_5xx_retries_with_same_idempotency_key` (`fail_first_n=2` ->
  success on 3rd attempt, ingest stores the batch once),
  `test_429_honors_retry_after_then_succeeds`,
  `test_401_drops_batch_without_retry` (one request only, drop counted),
  `test_unreachable_endpoint_drops_after_max_retries_without_raising`.
  Implement retry/backoff in `client.py` using `RETRY_BASE_S`/
  `RETRY_MAX_S`. Verify: `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_client.py -q` -> all pass.
- [ ] **C6 - CloudTraceExporter.** Test first in
  `packages/lucy-cloud/tests/test_exporter.py`:
  `test_export_batch_delivers_wire_events_to_ingest` (TelemetryEvents in,
  wire-v1 dicts stored by `IngestState`),
  `test_from_env_returns_none_without_api_key`,
  `test_env_configuration_builds_working_exporter` (monkeypatch.setenv
  of `LUCY_ENDPOINT`/`LUCY_API_KEY`/`LUCY_PROJECT` - env setup, not a
  mock), `test_export_batch_never_raises_on_server_error`. Implement
  `packages/lucy-cloud/src/lucy_cloud/exporter.py` for real. Verify:
  `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_exporter.py -q` -> all pass.
- [ ] **C7 - Auto-attach end-to-end.** Test first in
  `packages/lucy-cloud/tests/test_autoattach.py`:
  `test_configure_attaches_cloud_exporter_when_api_key_set`,
  `test_configure_skips_cloud_exporter_without_api_key`,
  `test_session_events_reach_ingest_with_zero_code_changes` - run the
  ingest app with `run_local_ingest`, set `LUCY_ENDPOINT`/`LUCY_API_KEY`
  via env, call `lucy.observe.configure()` with NO exporter args, emit
  events, assert they land in `IngestState`. Implementation work only if
  discovery glue in `exporter.py`/entry point needs adjusting; core
  `src/lucy/observe/` is read-only for this card. Verify:
  `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_autoattach.py -q` -> all pass.
- [ ] **C8 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q && .venv/bin/python -m pytest
  packages/lucy-cloud/tests -q` -> both green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003): no `unittest.mock`,
  `respx`, or `responses`. The sanctioned doubles are the real FastAPI
  ingest app over `httpx.ASGITransport` and the real uvicorn server on an
  ephemeral local port.
- Do not hardcode batch caps, flush intervals, retry backoff, header
  names, URLs, or paths at call sites; every wire literal is a named
  constant in `_wire.py` or a constructor parameter (agents.md).
- Do not cross the ADR 0010 boundary: no ingest storage, search, or
  dashboard code here; the test app exists only to verify the client and
  export the conformance suite. The wire document is the contract - never
  import platform code.
- Do not touch files outside `packages/lucy-cloud/`. In particular
  `src/lucy/observe/` (card 24's output) is read-only; if its discovery
  contract does not fit, stop and follow the Failure protocol.
- Do not retry 401/413/422; retries are for 5xx/429 only (wire spec).
- Do not let `submit`/`export_batch` block or raise into caller code;
  telemetry must never add latency to a voice turn.
- Do not re-implement PII redaction, audio suppression, or sampling; that
  runs client-side in `lucy.observe` before events reach this exporter.
- Do not check a box without running its Verify command.

## Definition of Done

- [ ] `.venv/bin/pip install -e packages/lucy-cloud` -> installs cleanly
- [ ] `.venv/bin/python -m pytest packages/lucy-cloud/tests -q` -> all
      pass (packaging, wire, conformance, client, exporter, auto-attach)
- [ ] `.venv/bin/python -m pytest
      packages/lucy-cloud/tests/test_autoattach.py -q` -> proves
      `LUCY_API_KEY` + `LUCY_ENDPOINT` yield cloud export with zero code
      changes
- [ ] `grep -rn "unittest.mock\|MagicMock\|respx\|responses" \
      packages/lucy-cloud` -> no matches
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
