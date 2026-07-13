# 30 - Build lucy-cloud telemetry client

**Sprint:** S7 - Cloud observability seam
**Epic:** Observability
**Estimated effort:** ~7 h
**Depends on:** 24
**State:** done

## Goal

The open client for the closed platform (ADR 0010): `LUCY_API_KEY` plus the
deployment's `LUCY_ENDPOINT` flips local tracing to cloud export, with
LangSmith-grade ergonomics. Ship package `lucy-cloud` implementing wire-v1 batching,
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
- `backlog/done/24_build_lucy_observe_package.md` - the seam this card
  plugs into: `TraceExporter` protocol, `TelemetryEvent` union, env
  handling, and the `lucy.exporters` entry-point group. Read its landed
  `src/lucy/observe/exporters.py` and `src/lucy/observe/__init__.py` before
  wiring anything.
- `src/lucy/specs.py` - `ObservabilitySpec` (`redact_pii`, `record_audio`,
  `trace_sample_rate`): privacy runs in core BEFORE export; this card must
  not re-implement it.
- `src/lucy/metrics.py` - `CostBreakdown`; its components feed the wire
  `cost` event payload.
- `tests/test_observability.py` - house test style: plain pytest,
  asyncio_mode auto, no mocks.
- `pyproject.toml` - workspace and tool configuration. This package must install
  standalone via `pip install -e packages/lucy-cloud`.

## Spec

New package `lucy-cloud` (import name `lucy_cloud`) under
`packages/lucy-cloud/`, depending on `lucy` and `httpx`.

`packages/lucy-cloud/pyproject.toml`:

- Distribution `lucy-cloud`, version `0.1.0`, src layout
  (`src/lucy_cloud/`).
- Entry point: group `lucy.exporters`, name `cloud`, target
  `lucy_cloud.exporter:CloudTraceExporter.from_env` - the zero-argument factory
  `lucy.observe.configure()` discovers (match card 24's landed contract;
  if it differs, follow the Failure protocol, do not patch core here).

`packages/lucy-cloud/src/lucy_cloud/_wire.py` - every production-client
wire-v1 literal lives here as a named constant; independent conformance vectors
repeat normative literals intentionally so implementation and tests cannot drift
together:

- `WIRE_VERSION = 1`, `EVENTS_PATH = "/v1/events"`,
  `MAX_BATCH_EVENTS = 100`, `MAX_BATCH_BYTES = 1_048_576` (1 MiB),
  `FLUSH_INTERVAL_S = 2.0`, `HEADER_API_KEY = "x-api-key"`,
  `HEADER_WIRE = "x-lucy-wire"`, `HEADER_IDEMPOTENCY = "idempotency-key"`,
  `RETRY_BASE_S`, `RETRY_MAX_S` (backoff knobs).
- `build_envelope(project: str, sdk_version: str, events: list[dict])
  -> dict`: `{"project": ..., "sdk": {"name": "lucy", "version": ...},
  "events": [...]}`; `sdk_version` read from installed `lucy` dist
  metadata by callers, never hardcoded.
- `plan_batches(events: list[dict], *, project: str = "", sdk_version: str = "")
  -> list[list[dict]]`: greedy split so no complete serialized envelope exceeds
  `MAX_BATCH_EVENTS` events or `MAX_BATCH_BYTES`.
- `encode_batch(envelope: dict, *, use_gzip: bool = True) ->
  tuple[bytes, dict[str, str]]`: body plus headers (`content-type:
  application/json`, `x-lucy-wire: 1`, `content-encoding: gzip` when
  compressed).

`packages/lucy-cloud/src/lucy_cloud/client.py` is the exporter's internal
transport; `CloudTraceExporter` is the package's only public API:

- `IngestClient(endpoint: str, api_key: str, *, project: str,
  max_queue: int = 10_000, flush_interval_s: float = FLUSH_INTERVAL_S,
  max_retries: int = 5, transport: httpx.AsyncBaseTransport | None = None,
  sleep: Sleeper = asyncio.sleep, jitter: Jitter = random.uniform,
  close_timeout_s: float = CLOSE_TIMEOUT_S, pace: Pacer = _default_pace,
  monotonic: Monotonic = time.monotonic)` - `sleep`/`jitter`, `pace`, and
  `monotonic` provide deterministic retry/cadence tests,
  `close_timeout_s` bounds shutdown, and
  `transport` lets tests inject `httpx.ASGITransport(app=...)` (real client,
  real ASGI app, no network, no mocks).
- Private `_submit_wire(event: dict) -> None`: non-blocking and fail-open; when
  the bounded queue is full the event is dropped and counted. It accepts only
  events already filtered by Lucy core and is not exported from `lucy_cloud`.
- An owned background worker flushes independently of the caller's asyncio
  context at least every `flush_interval_s` (wire spec: 2 s) and immediately
  when caps are hit or the synchronous exporter receives a batch.
- `async flush() -> None` force-drains for deterministic tests. `async
  aclose() -> None` starts a flush and shutdown under one end-to-end deadline,
  and on timeout atomically abandons and accounts the in-flight batch, clears
  the credential, and returns fail-open while lower-level transport cleanup
  finishes.
- `dropped_events: int` property; a `logging.getLogger("lucy_cloud")`
  warning surfaced at most once per flush interval while drops occur.
- Each batch gets ONE `idempotency-key` (uuid4) kept across all retries
  of that batch.
- Retries with jittered exponential backoff on 5xx, 429, and transient
  transport failures only; honor a finite, bounded
  `retry-after` on 429; 401/413/422 are never retried - the batch is
  dropped and counted. After `max_retries` the batch is dropped and
  counted. Operational failures never raise into caller code; caller
  cancellation propagates while the owned worker continues the shielded drain.

`packages/lucy-cloud/src/lucy_cloud/exporter.py`:

- `CloudTraceExporter(*, endpoint: str | None = None,
  api_key: str | None = None, project: str | None = None,
  client: IngestClient | None = None)` implementing the `TraceExporter`
  protocol from `lucy.observe` (`export_batch`). Constructor args win;
  otherwise env: `LUCY_ENDPOINT`, `LUCY_API_KEY`, `LUCY_PROJECT`.
- `CloudTraceExporter.from_env() -> CloudTraceExporter | None`: returns
  `None` when `LUCY_API_KEY` is unset, so `configure()` auto-attaches the
  exporter only when a key exists.
- `close() -> None` and `async aclose() -> None` expose the same bounded,
  fail-open drain for synchronous and asynchronous application lifecycles.
- Converts `TelemetryEvent` objects (card 24's union: session.started,
  session.ended, turn, span, cost, business, tool_call, transcript,
  audio_ref) to wire-v1 dicts. Events arrive post-redaction from
  `lucy.observe`; no redaction logic here.

Tests (no mocks) under `packages/lucy-cloud/tests/`:

- `ingest_app.py`: `create_ingest_app(api_key: str, *,
  rate_limit_after: int | None = None, retry_after: str = "0",
  fail_first_n: int = 0)
  -> FastAPI` plus an `IngestState` recorder. Implements wire v1
  faithfully: `202` on valid batch; `401` on missing/wrong `x-api-key`;
  `413` over `MAX_BATCH_EVENTS`/`MAX_BATCH_BYTES`; `422` listing
  malformed event indices in the body; `429` with `retry-after` when
  `rate_limit_after` trips; `5xx` for the first `fail_first_n` requests;
  gzip decoding; dedupe by `idempotency-key`; unknown event types
  accepted and stored opaquely. Also `run_local_ingest(app) ->
  Iterator[str]` context manager serving it via uvicorn on an ephemeral
  `127.0.0.1` port (a real local protocol server).
- `test_ingest_conformance.py` exposes a base URL/transport target fixture and
  `LUCY_CONFORMANCE_ENDPOINT`/`LUCY_CONFORMANCE_API_KEY`. Request/response
  vectors run against external ingest; deterministic rate-limit injection and
  storage-side dedupe assertions remain local because wire v1 exposes no admin
  probe. Set `LUCY_CONFORMANCE_RATE_LIMITED_API_KEY` to exercise external 429;
  platform persistence tests own server-internal dedupe guarantees.

## Chips

- [x] **C1 - Package scaffold + entry point.** Write
  `packages/lucy-cloud/tests/test_packaging.py` first:
  `test_entry_point_exposes_cloud_exporter_factory` loads entry-point group
  `lucy.exporters`, name `cloud`, and asserts it resolves to the bound
  `CloudTraceExporter.from_env` factory. Then create `packages/lucy-cloud/pyproject.toml`,
  `packages/lucy-cloud/src/lucy_cloud/__init__.py`, and a class skeleton
  in `packages/lucy-cloud/src/lucy_cloud/exporter.py` (constructor
  signature only; behavior lands in C6). Verify:
  `.venv/bin/pip install -e packages/lucy-cloud && .venv/bin/python -m
  pytest packages/lucy-cloud/tests/test_packaging.py -q` -> all pass.
- [x] **C2 - Wire constants, envelope, batch planning.** Test first in
  `packages/lucy-cloud/tests/test_wire.py`:
  `test_envelope_matches_wire_v1_shape`,
  `test_plan_batches_splits_at_max_events` (101 events -> 2 batches),
  `test_plan_batches_splits_at_max_bytes` (oversized payloads split
  under 1 MiB), `test_encode_batch_gzip_round_trips` (gzip body decodes
  to the same JSON; headers carry `x-lucy-wire: 1`). Implement
  `packages/lucy-cloud/src/lucy_cloud/_wire.py`. Verify:
  `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_wire.py -q`
  -> all pass (>=4 tests).
- [x] **C3 - Ingest conformance fixture.** Test first in
  `packages/lucy-cloud/tests/test_ingest_conformance.py`:
  `test_valid_batch_accepted_with_202`,
  `test_missing_or_wrong_api_key_rejected_401`,
  `test_oversized_batch_rejected_413`,
  `test_malformed_events_rejected_422_with_indices`,
  `test_rate_limit_includes_retry_after_header`,
  `test_duplicate_idempotency_key_is_accepted_idempotently`,
  `test_unknown_event_type_accepted_opaquely`. Drive via
  `httpx.AsyncClient` over `httpx.ASGITransport` locally and the configured
  external endpoint in conformance mode. Implement
  `packages/lucy-cloud/tests/ingest_app.py`. Verify:
  `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_ingest_conformance.py -q` -> all pass
  (>=7 tests).
- [x] **C4 - Bounded queue + background flush.** Test first in
  `packages/lucy-cloud/tests/test_client.py`:
  `test_submit_never_blocks_when_queue_full_drops_and_counts`
  (tiny `max_queue` -> submission returns instantly,
  `dropped_events` grows), `test_flush_interval_sends_partial_batch`
  (manual injected pacer advances the interval without wall-clock sleeps),
  `test_batch_caps_trigger_immediate_flush`,
  `test_aclose_flushes_remaining_events`. Implement
  `packages/lucy-cloud/src/lucy_cloud/client.py` happy path against the
  C3 app via injected ASGI transport. Verify:
  `.venv/bin/python -m pytest packages/lucy-cloud/tests/test_client.py
  -q` -> all pass in < 5 s (proves no real 2 s sleeps in tests).
- [x] **C5 - Retry, idempotency, fail-open.** Test first, extending
  `packages/lucy-cloud/tests/test_client.py`:
  `test_5xx_retries_with_same_idempotency_key` (`fail_first_n=2` ->
  success on 3rd attempt, ingest stores the batch once),
  `test_429_honors_retry_after_then_succeeds`,
  `test_401_drops_batch_without_retry` (one request only, drop counted),
  `test_unreachable_endpoint_drops_after_max_retries_without_raising`.
  Implement retry/backoff in `client.py` using `RETRY_BASE_S`/
  `RETRY_MAX_S`. Verify: `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_client.py -q` -> all pass.
- [x] **C6 - CloudTraceExporter.** Test first in
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
- [x] **C7 - Auto-attach end-to-end.** Test first in
  `packages/lucy-cloud/tests/test_autoattach.py`:
  `test_configure_attaches_cloud_exporter_when_api_key_set`,
  `test_configure_skips_cloud_exporter_without_api_key`,
  `test_session_events_reach_ingest_with_zero_code_changes` - run the
  ingest app with `run_local_ingest`, set `LUCY_ENDPOINT`/`LUCY_API_KEY`
  via env, call `lucy.observe.configure()` with NO exporter args, emit
  events, assert they land in `IngestState`. Implementation work only if
  discovery glue in `exporter.py`/entry point needs adjusting; core exporter
  discovery and sampling decisions stay read-only; only the narrow pre-freeze
  producer/privacy/runtime alignments authorized under Do NOT may update core.
  Verify:
  `.venv/bin/python -m pytest
  packages/lucy-cloud/tests/test_autoattach.py -q` -> all pass.
- [x] **C8 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", collect review evidence, and prepare the final state
  move. Verify:
  `.venv/bin/python -m pytest -q && .venv/bin/python -m pytest
  packages/lucy-cloud/tests -q` -> both green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003): no `unittest.mock`,
  `respx`, or `responses`. The sanctioned doubles are the real FastAPI
  ingest app over `httpx.ASGITransport` and the real uvicorn server on an
  ephemeral local port.
- Do not hardcode batch caps, flush intervals, retry backoff, header names,
  URLs, or paths at production call sites; every production wire literal is a
  named constant in `_wire.py` or a constructor parameter (agents.md).
  Conformance vectors repeat normative literals independently by design.
- Do not cross the ADR 0010 boundary: no ingest storage, search, or
  dashboard code here; the test app exists only to verify the client and
  export the conformance suite. The wire document is the contract - never
  import platform code.
- Exporter discovery, sampling decisions, PII patterns, and suppression policy
  in `src/lucy/observe/` remain read-only. The narrow recursive traversal and
  redaction of every free-form telemetry field, fail-open privacy rejection in
  `Tracer._enqueue`, `Tracer.span` optional-turn alignment,
  `AudioRefEvent.blob_id`, and UUID
  event-ID producer validation, nonempty required strings, finite event/cost/
  latency values, optional `SpanEvent.turn_id`, suppression of graph spans that
  have no session identity, session-span emission alignment in
  `src/lucy/tracing.py`, the internal identity/payload-bound post-privacy approval
  used by cloud export, the shared outbound-secret policy consumed by telemetry
  and recorded-fixture tooling, stricter sensitive-value rejection on telemetry
  `AudioRefEvent` fields without changing the frozen control-channel ABI,
  normative wire documentation, and shared cost/latency value constraints are
  explicitly in scope because conformance and security review exposed
  producer/server and direct-export disagreements; no other core runtime changes
  belong here.
- Do not retry 401/413/422; HTTP status retries are for 5xx/429 only. Bounded
  transient transport failures share the retry budget.
- Do not let `_submit_wire`/`export_batch` block or raise operational failures
  into caller code;
  telemetry must never add latency to a voice turn.
- Do not re-implement PII redaction, audio suppression, or sampling; that
  runs client-side in `lucy.observe` before events reach this exporter.
- Do not check a box without running its Verify command.

## Definition of Done

- [x] Docker editable `pip install -e packages/lucy-cloud` -> installs cleanly
- [x] Docker pytest `packages/lucy-cloud/tests -q` -> all
      pass (packaging, wire, conformance, client, exporter, auto-attach)
- [x] Docker pytest `packages/lucy-cloud/tests/test_autoattach.py -q` -> proves
      `LUCY_API_KEY` + `LUCY_ENDPOINT` yield cloud export with zero code
      changes
- [x] `grep -rn "unittest.mock\|MagicMock\|respx\|responses" \
      packages/lucy-cloud` -> no matches
- [x] `docker compose run --rm lucy-api pytest` -> full suite green
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

- Exporter discovery requires a zero-argument factory, so the entry point uses
  `CloudTraceExporter.from_env`; core discovery remains unchanged.
- No hosted endpoint is defined in either repo, so API key and deployment
  endpoint are both required rather than inventing an internal URL.
- The client owns a daemon worker loop and an `atexit` close path, making sync
  tracer use non-blocking while preserving deterministic forced flush and a
  bounded, fail-open close path.
- Complete decompressed envelopes, not event arrays, define the 1 MiB boundary.
- Conformance vectors independently validate auth, UUID idempotency, envelope,
  every required string and finite numeric field, opaque recording references,
  gzip, future event types, and external targets.
- Cost and latency components are now nonnegative and finite in both core models
  and the normative wire; derived totals reject overflow, closing a pre-existing
  SDK/platform validation mismatch.
- `audio_ref` now applies one `OpaqueRecordingRef` contract to `blob_id`,
  `recording_id`, and `consent_ref` across public events, wire, and conformance.
- Wire producers now enforce UUID event IDs and the normative draft records all
  server-side bounds before the first public freeze. Session-level spans retain
  the platform's optional `turn_id`, and graph runs without a session identity
  suppress spans instead of inventing invalid identifiers.
- Cloud export accepts only identity- and payload-bound events approved by Lucy's
  recursive privacy path; direct, copied, and mutated exporter calls are dropped.
  Remote endpoints require credential-free HTTPS, while loopback URLs retain
  deterministic local transports and tests.
- Recursive tool-argument privacy processing is cycle-aware and bounded by the
  named 32-level limit; cyclic, over-depth, non-JSON, and nonfinite values are
  dropped and counted without replacing MCP permission or provider errors.
- Sanitization now covers every free-form telemetry string, including configured
  labels, reasons/timeouts, span attributes, and tool errors. Runtime secrets are
  scrubbed even when PII redaction is disabled; unsafe structural IDs reject and
  count the event instead of collapsing distinct join keys onto one redacted ID.
  UUID event IDs and validated opaque recording refs remain unchanged.
- Runtime telemetry, project identifiers, recording references, fixture
  recording, and fixture replay now share one credential-key policy. Basic auth,
  cookies, PEM private keys, cloud access keys, and credential-bearing map keys
  are covered even when PII redaction is disabled.
- Bounded close keeps cancellation-resistant worker tasks supervised after the
  caller deadline, then releases the event loop and HTTP transport when the
  lower-level operation settles.
- No finding is deferred; card 73 will add the blob-specific platform client.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->

## Review evidence

- `code-reviewer` (`019f5b97-2c6e-7f81-8498-cdad174fd038`): PASS; final
  package 313 passed, root 577 passed, fixture/replay/backlog 30 passed, and
  direct `SECRET_KEY` probe passed.
- `test-auditor` (`019f5b97-31b5-76b2-b6e9-81761a0eeca0`): PASS; no-mocks
  scan clean and all privacy, conformance, lifecycle, UUID-boundary, and
  fixture regressions audited.
- `simplicity-reviewer` (`019f5b97-3657-79c3-a711-e75521f49fc0`): PASS; one
  shared credential policy, no dead public API, and no unnecessary abstraction.
- `docs-reviewer` (`019f5b97-3a7f-7f30-9255-18090db18cad`): PASS; wire,
  producer, independent conformance fixture, approval boundary, and card prose
  aligned.
- `security-reviewer` (`019f5b97-4087-7891-ba90-f1db3ba22b10`): PASS; exact
  runtime, project, recording-reference, recorder, replay, endpoint, approval,
  and shutdown probes found no remaining exposure.
- Final Docker gates: `313 passed` for `packages/lucy-cloud/tests`; `577 passed,
  2 deselected` for the SDK; backlog contract `9 passed`; Ruff, format, mypy,
  no-mocks scan, and `git diff --check` clean.
- Hosted external conformance was not run because no external endpoint
  credentials were supplied; the card defines it as optional and the external
  seam plus independent local protocol vectors are covered.
