# Lucy Telemetry Wire Protocol v1

Status: normative draft. This document is the contract between the open-source
SDK (`lucy.observe` and the `lucy-cloud` exporter) and any ingest service,
including the closed platform. The document, not shared code, is the contract
(ADR 0010).

## Transport

- `POST {LUCY_ENDPOINT}/v1/events`
- Headers: `x-api-key: <key>`, `x-lucy-wire: 1`, `content-type: application/json`,
  optional `content-encoding: gzip`, `idempotency-key: <uuid>` per batch.
- Batching: max 100 events or 1 MiB per request, flushed at least every 2 s.
- Responses: `202` accepted; `401` invalid key; `413` batch too large; `422`
  malformed events (body lists indices); `429` with `retry-after`.

## Delivery semantics

Telemetry must never add latency to a voice turn or crash a call:

- Bounded in-memory queue with a background flush task.
- Fail-open: on errors or full queue, events are dropped and a local drop
  counter is incremented and surfaced as a warning metric.
- Retries with jittered backoff on 5xx/429 only; batches are idempotent via
  `idempotency-key`.

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

Within wire v1, changes are additive only. Unknown fields must be ignored by
servers; unknown event types must be accepted and stored opaquely.

Every event may carry `tags`, a string-to-string map for user-provided run,
environment, experiment, and scenario dimensions. Tags pass through the same
client-side PII redaction path as transcripts and tool arguments.

## Event types

| type | payload (beyond common fields) |
| --- | --- |
| `session.started` | `agent_name`, `spec_hash`, `environment`, `transport`, optional `agent_version`, `graph_hash`, `thread_id` |
| `session.ended` | `reason`, `duration_ms`, `billable_audio_minutes` |
| `turn` | `turn_id`, `turn_index`, `latency_waterfall` (stt_ms, rag_ms, llm_ms, mcp_tools_ms, tts_ms, transport_ms), `interrupted`, `timeout_events` |
| `span` | `span_id`, `parent_id`, `turn_id`, `name`, `status` (ok/fallback/error/cancelled), `started_at_ms`, `ended_at_ms`, `attributes` |
| `cost` | `turn_id?`, full `CostBreakdown` components plus derived `total_cost`, `cost_per_minute` |
| `business` | `turn_id?`, `funnel_stage`, `funnel_confidence`, `sentiment_label`, `sentiment_confidence` |
| `tool_call` | `turn_id`, `server`, `tool`, `allowed`, `latency_ms`, `error?`, `arguments` (post-redaction only) |
| `transcript` | `turn_id`, `role` (caller/agent), `text` (final segments only, post-redaction) |
| `audio_ref` | `turn_id?`, `blob_id`, `upload_url_requested` (audio bytes never inline) |

`audio_ref` is emitted only after the media plane reports a successful
`recording.uploaded` control event. Its `blob_id` is the join key; recording
metadata such as leg, duration, byte count, SHA-256, container, and consent
reference remains on the control event and can be copied into additive span
attributes. `upload_url_ref` is opaque and must never contain credentials or raw
audio.

## Privacy controls (enforced client-side, in open code)

Nothing sensitive leaves the process unless explicitly enabled:

- `redact_pii` (default true, from `ObservabilitySpec`): redaction pass runs
  over `transcript.text` and `tool_call.arguments` before enqueueing.
- `record_audio` (default false): suppresses `audio_ref` events entirely.
- `trace_sample_rate`: whole-session head sampling, deterministic on a hash of
  `session_id`.
- `transcripts=false`: drops `transcript` events wholesale for regulated
  tenants.

## Client configuration

Explicit `lucy.observe.configure(...)` wins; otherwise environment variables:

| variable | effect |
| --- | --- |
| `LUCY_TRACING` | enable/disable (default: enabled, console exporter) |
| `LUCY_ENDPOINT` | ingest base URL |
| `LUCY_API_KEY` | enables the cloud exporter when installed |
| `LUCY_PROJECT` | project name in the envelope |
| `LUCY_TRACE_SAMPLE` | session sample rate 0..1 |
| `LUCY_TRACE_FILE` | JSONL file exporter path |

## Versioning

The major version rides the URL path (`/v1/`). Additive changes (new event
types, new optional fields) do not bump it. Removing or renaming fields
requires `/v2/` and a deprecation window.
