# Lucy Telemetry Wire Protocol v1

Status: normative draft. This document is the contract between the open-source
SDK (`lucy.observe` and the `lucy-cloud` exporter) and any ingest service,
including the closed platform. The document, not shared code, is the contract
(ADR 0010).

## Transport

- `POST {LUCY_ENDPOINT}/v1/events`
- Network endpoints require HTTPS and must not contain URL credentials, query
  parameters, or fragments. Plain HTTP is permitted only for loopback hosts,
  including in-process conformance services addressed through loopback URLs.
- Headers: `x-api-key: <key>`, `x-lucy-wire: 1`, `content-type: application/json`,
  optional `content-encoding: gzip`, `idempotency-key: <uuid>` per batch.
- Batching: max 100 events or 1 MiB per complete decompressed envelope, flushed
  at least every 2 s.
- Responses: `202` accepted; `401` invalid key; `413` batch too large; `422`
  malformed events (body lists indices); `429` with `retry-after` as finite,
  nonnegative delta-seconds.

## Delivery semantics

Telemetry must never add latency to a voice turn or crash a call:

- Bounded in-memory queue with a background flush task.
- Fail-open: on errors or full queue, events are dropped and a local drop
  counter is incremented and surfaced as a warning metric.
- Retries with jittered backoff on 5xx/429 and transient transport failures;
  no other HTTP status is retried. Delays and retry budgets are bounded, and
  batches are idempotent via `idempotency-key`.

## Envelope

```json
{
  "project": "string",
  "sdk": {"name": "lucy", "version": "0.1.0"},
  "events": [ { "type": "...", "event_id": "uuid", "session_id": "string",
                "emitted_at_ms": 0, "tags": {"key": "value"},
                "...": "type-specific fields" } ]
}
```

`project` is a nonempty opaque identifier that must not contain secrets, email,
or phone-like PII; `sdk.version` is a nonempty string; `sdk.name` is the literal
`lucy`; and `events` is an array subject to the transport batch limits.

After the first public wire-v1 release, changes within v1 are additive only.
This normative draft may still correct producer/server disagreements before the
card 46 publication freeze. The UUID event-ID rule, nonnegative numeric bounds,
and opaque recording-reference bounds in this revision are pre-freeze contract
corrections, not changes to a released wire. Unknown fields must be ignored by
servers; unknown event types must be accepted and stored opaquely after their
common fields validate.

Every event requires a nonempty string `type`, UUID string `event_id`, nonempty
string `session_id`, nonnegative integer `emitted_at_ms`, and `tags` as a
string-to-string map. Tags may carry user-provided run, environment, experiment,
and scenario dimensions and pass through the same client-side PII redaction path
as transcripts and tool arguments. Numeric values below must be finite JSON
numbers; integers do not accept booleans.

## Event types

| type | payload (beyond common fields) |
| --- | --- |
| `session.started` | Nonempty `agent_name`, `spec_hash`, `environment`, `transport`; optional string `agent_version`, `graph_hash`, `thread_id` |
| `session.ended` | Nonempty `reason`; nonnegative integer `duration_ms`; nonnegative `billable_audio_minutes` |
| `turn` | Nonempty `turn_id`; nonnegative integer `turn_index`; `latency_waterfall` with all six nonnegative numbers `stt_ms`, `rag_ms`, `llm_ms`, `mcp_tools_ms`, `tts_ms`, `transport_ms`; boolean `interrupted`; string-list `timeout_events` |
| `span` | Nonempty `span_id`, `name`; optional nonempty `turn_id`; optional string `parent_id`; `status` in `ok`, `fallback`, `error`, `cancelled`; nonnegative integer `started_at_ms`, `ended_at_ms`; string-to-string `attributes` |
| `cost` | Optional string `turn_id`; flattened nonnegative `stt_cost`, `llm_cost`, `tts_cost`, `telephony_cost`, `rag_cost`, `mcp_tool_cost`, `infra_cost`, `total_cost`, `cost_per_minute`; strictly positive `billable_audio_minutes` |
| `business` | Optional string `turn_id`; nonempty `funnel_stage`, `sentiment_label`; `funnel_confidence` and `sentiment_confidence` in `0..1` |
| `tool_call` | Nonempty `turn_id`, `server`, `tool`; boolean `allowed`; nonnegative `latency_ms`; optional string `error`; object `arguments` whose nested string keys and values are recursively redacted |
| `transcript` | Nonempty `turn_id`; `role` in `caller`, `agent`; string `text` containing final, post-redaction segments only |
| `audio_ref` | Opaque `blob_id`; boolean `upload_url_requested`; optional string `turn_id`; optional opaque `recording_id`, `consent_ref`; optional `leg` in `caller`, `agent`, `mixed`; optional nonnegative integers `duration_ms`, `byte_count`; optional lowercase 64-hex `sha256`; optional `container` matching `[a-z0-9][a-z0-9._-]{0,31}` (audio bytes never inline) |

An `audio_ref` derived from recording is emitted only after the media plane
reports `recording.uploaded` and the configured `BlobStore` confirms object
existence, byte count, SHA-256, container, and duration. Its `blob_id` is the
join key; validated recording metadata is copied into additive `audio_ref`
fields. `blob_id`, `recording_id`, and `consent_ref` are opaque identifiers
limited to 128 characters using only letters, digits, `.`, `_`, `:`, and `-`.
Phone-like numeric references are rejected; none may contain credentials, PII,
URLs, or raw audio. The public `Tracer.audio_ref` method remains available for
non-recording asset references and enforces the same metadata schema.

## Blob uploads

Recording bytes use a separate media-plane flow. The open `lucy-cloud` client
negotiates targets and validates completion metadata, but the media gateway
performs the presigned PUT directly; audio bytes never enter telemetry events or
Python application code.

Presign request:

- `POST {LUCY_ENDPOINT}/v1/blobs`
- Header: `x-api-key: <key>`
- Body: nonempty privacy-safe `session_id`; opaque `blob_id` and
  `consent_ref`; `leg` in `caller`, `agent`, `mixed`; `content_type` equal to
  `audio/wav`; and a nonempty `retention_class`.
- Success: `201` with the unchanged external `blob_id`, absolute presigned
  `upload_url`, positive `expires_at_ms`, method `PUT`, and required upload
  headers. The gateway must send every returned header; wire v1 requires
  `content-type: audio/wav` and `if-none-match: *` so one reservation cannot
  overwrite an existing object. No additional upload header is accepted.

The client accepts an upload target only when its exact scheme, hostname, and
port match `LUCY_ENDPOINT` or an explicitly configured
`LUCY_BLOB_UPLOAD_ORIGINS` entry. Non-global IP literals are accepted only when
they are the configured endpoint origin. The media gateway performs the PUT
with redirects disabled. These rules
keep a compromised or malformed presign response from turning recording into a
credential-forwarding or internal-network request.

The platform namespaces its private object key by tenant while preserving
Lucy's external `blob_id`. That external identifier is the single join key in
the control channel, `audio_ref`, recording row, playback route, retention, and
erasure. The presigned URL is held behind an opaque `upload_url_ref`; neither
the URL nor its signature may enter the control wire, telemetry, or logs.

After the media plane reports `recording.uploaded`, the client sends:

- `POST {LUCY_ENDPOINT}/v1/blobs/{blob_id}/complete`
- Header: `x-api-key: <key>`
- Body: the original `blob_id`, `session_id`, `leg`, `content_type`,
  `consent_ref`, and `retention_class`, plus the gateway-attested nonnegative
  `duration_ms`, lowercase 64-hex `sha256`, and nonnegative `byte_count` from
  `recording.uploaded`.
- Success: `200` recording metadata containing `blob_id`, `session_id`,
  optional `turn_id`, `leg`, `duration_ms`, lowercase SHA-256, `byte_count`,
  nonempty `storage_container`, `content_type`, required `consent_ref`, and
  `retention_class`.

The platform independently verifies object byte count and content type through
storage `HEAD`; it does not read audio to recompute duration or SHA-256. Those
two fields remain media-gateway attestations. The client accepts completion
only when the immutable response's external identity, duration, hash, byte
count, leg, consent, content type, and retention class match the gateway event
and prepared target.

Cancellation uses authenticated `DELETE
{LUCY_ENDPOINT}/v1/blobs/{blob_id}`. Success is `200` with the unchanged
`blob_id` and state `aborted`. The platform persists a one-use tombstone before
best-effort deletion. An issued presigned PUT cannot be revoked, so a platform
sweeper repeats deletion after expiry to close late-PUT races. The client
forgets the signed target even when abort is unavailable; the durable expiry
sweep remains the final cleanup boundary.

Presign, completion, and abort failures; `401`; `429`; 5xx; malformed
responses; and transport failures suppress optional recording, increment a
local drop counter, and warn without credentials. They never fail the call.
An ambiguous transport failure after dispatch or an invalid `201` response
triggers a best-effort compensating abort. Explicit non-`201` responses do not
claim a reservation. Client close rejects new work, cancels and drains in-flight
presigns and completions, rejects completion responses that arrive after close
starts, and aborts resolved reservations before closing its HTTP transport.
Close is single-flight; caller cancellation waits for cleanup and credential
zeroization before it is propagated.
Network endpoints and upload targets require HTTPS, with plain HTTP allowed
only for loopback development.

## Privacy controls (enforced client-side, in open code)

Nothing sensitive leaves the process unless explicitly enabled:

- `redact_pii` (default true, from `ObservabilitySpec`): redaction pass runs
  over `transcript.text` and `tool_call.arguments` before enqueueing.
- `record_audio` (default false): suppresses `audio_ref` events entirely.
- `trace_sample_rate`: whole-session head sampling, deterministic on a hash of
  `session_id`.
- `transcripts=false`: drops `transcript` events wholesale for regulated
  tenants.
- Exportable event instances receive an identity- and payload-bound internal
  approval only after this sampling, suppression, and recursive-redaction path.
  Cloud exporters discard direct, copied, mutated, or otherwise unapproved
  `export_batch` inputs instead of treating the exporter as an alternate privacy
  entry point.
- Tool-argument privacy traversal is cycle-aware and bounded by the named
  `MAX_REDACTION_DEPTH` limit (32 container levels). Cyclic, over-depth,
  non-JSON, or nonfinite argument values are dropped and counted fail-open;
  telemetry processing never replaces the underlying MCP result or error.
- Runtime secrets are scrubbed from every free-form string regardless of the
  `redact_pii` setting, including secret-bearing map keys and common bearer/JWT
  forms. With `redact_pii=true`, emails and phone-like values are redacted too.
- Structural session/turn/span/thread/hash identifiers are never transformed:
  unsafe values cause the event to be dropped and counted so two distinct IDs
  cannot collapse onto one redacted join key. Configured labels, diagnostic
  reasons and timeout lists, span attribute keys/values, tool
  server/name/error, arguments, transcripts, and tags are sanitized before
  approval. UUID event IDs and validated opaque recording references are
  validated separately and remain unchanged.

## Client configuration

Explicit `lucy.observe.configure(...)` wins; otherwise environment variables:

| variable | effect |
| --- | --- |
| `LUCY_TRACING` | enable/disable (default: enabled, console exporter) |
| `LUCY_ENDPOINT` | required credential-free HTTPS ingest base URL; HTTP only for loopback development |
| `LUCY_API_KEY` | enables the cloud exporter when installed |
| `LUCY_BLOB_UPLOAD_ORIGINS` | optional comma-separated exact scheme/host/port allowlist for recording PUT targets; defaults to the `LUCY_ENDPOINT` origin |
| `LUCY_PROJECT` | project name in the envelope |
| `LUCY_TRACE_SAMPLE` | session sample rate 0..1 |
| `LUCY_TRACE_FILE` | JSONL file exporter path |

## Versioning

The major version rides the URL path (`/v1/`). Additive changes (new event
types, new optional fields) do not bump it. Removing or renaming fields
requires `/v2/` and a deprecation window.
