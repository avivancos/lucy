# Lucy Analytics Model v1

## Status

Normative. Version 1. The schema identifier is `analytics-model/v1`. Additive,
backward-compatible fields produce a minor revision. Removing or renaming a
field, changing a grain, or changing a formula requires a new major version.

## Scope and boundary

This open semantic model defines portable facts, dimensions, measures, formulas,
and rollup snapshots derived from telemetry wire v1. Open `lucy.analytics`
functions and the closed platform warehouse consume this same contract as each
rollup capability is implemented.

The open model defines no persistence and no cross-run or cross-tenant
aggregation. `lucy.analytics` currently implements the measured talk-duration
rollup defined below; the remaining v1 formulas are normative consumer
contracts, not a claim that a complete in-process rollup engine already ships.
Warehouse storage, tenant isolation, cross-run queries, comparisons, and hosted
BI are closed platform concerns under ADR 0010 and ADR 0013.

## Facts

`turn_fact` has one row per `turn` event and key `turn_id`. Its source contains
turn order, interruption state, timeout events, and the latency waterfall.

`session_fact` has one row per voice session and key `session_id`. It combines
`session.started`, `session.ended`, all session `cost` events, and the latest
`business` event. Session cost measures are sums, never the last event value.

`tool_call_fact` has one row per `tool_call` event and composite key
`(turn_id, server, tool, emitted_at_ms)`. It preserves permission outcome,
latency, and error presence after client-side redaction.

## Dimensions

- `agent`: low-cardinality agent name from `session.started.agent_name`.
- `deployment`: low-cardinality deployment identity from the session
  `deployment` tag; absent values remain null.
- `provider_stt`: bounded provider registry identity attributed to `stt_cost`.
- `provider_llm`: bounded provider registry identity attributed to `llm_cost`.
- `provider_tts`: bounded provider registry identity attributed to `tts_cost`.
- `funnel_stage`: bounded latest `business.funnel_stage`; supported values are
  `qualified`, `interested`, `objection`, `booked`, `escalation`, and
  `failed_booking`.
- `sentiment_label`: bounded latest `business.sentiment_label`; normalized
  analytical values are `positive`, `neutral`, and `negative`.
- `project`: high-cardinality project identity supplied by the wire envelope.
- `tenant`: high-cardinality tenant identity resolved by the platform API key;
  local rollups leave it null.
- `time`: high-cardinality UTC date/time derived from `emitted_at_ms`.

Provider dimensions consume typed per-component cost attribution when present.
They remain null when the runtime cannot identify a provider; consumers do not
infer identities from prices, endpoints, or class names.

## Measures

Cost measures reuse `CostBreakdown` names and sum over their fact grain:
`stt_cost`, `llm_cost`, `tts_cost`, `telephony_cost`, `rag_cost`,
`mcp_tool_cost`, `infra_cost`, and `billable_audio_minutes`. `total_cost` is the
sum of the seven cost components, not a separately priced component.

Latency measures reuse `LatencyWaterfall` names: `stt_ms`, `rag_ms`, `llm_ms`,
`mcp_tools_ms`, `tts_ms`, and `transport_ms`. `total_ms` is their sum per turn.

Speech-activity measures are additive `caller_talk_ms` and `agent_talk_ms`.
`caller_talk_ms` comes from media-plane VAD speech intervals and
`agent_talk_ms` comes from media-plane playback intervals. Producers must not
derive either measure from transcript length, token counts, call duration,
billable audio minutes, or TTS text length.

Count measures are `turn_count`, `session_count`, `tool_call_count`,
`tool_error_count`, `barge_in_count`, and `deadline_miss_count`. A tool call is
an error when `tool_call.error` is non-null. A barge-in is an interrupted turn.
A deadline miss is one entry in `turn.timeout_events`.

## Derived metrics

All division uses aggregate numerators and denominators. A derived metric is
null when its denominator is zero.

- `cost_per_minute = total_cost / billable_audio_minutes`.
- `cost_per_booked_outcome = total_cost / count(funnel_stage == booked)`.
- `conversion_rate = count(funnel_stage == booked) / session_count`.
- `latency_p50`, `latency_p95`, and `latency_p99` are the nearest-rank
  percentiles for each latency segment and `total_ms` over turn facts.
- `deadline_miss_rate = deadline_miss_count / turn_count`.
- `barge_in_rate = barge_in_count / turn_count`.
- `tool_success_rate = 1 - tool_error_count / tool_call_count`.
- `talk_ratio = agent_talk_ms / (agent_talk_ms + caller_talk_ms)`.

## Rollup snapshot schema

`SessionRollup` contains `schema_version`, `session_id`, `project`, `tenant`,
`agent`, `deployment`, `provider_stt`, `provider_llm`, `provider_tts`,
`funnel_stage`, `sentiment_label`, `started_at_ms`, `ended_at_ms`, `measures`,
`latency_percentiles`, and `derived_metrics`.

`RunRollup` contains `schema_version`, `run_id`, `project`, `tenant`,
`window_start_ms`, `window_end_ms`, `group_by`, `dimensions`, `measures`,
`latency_percentiles`, and `derived_metrics`. `dimensions` is the selected
dimension-value map. `measures` always includes every measure named above.
`latency_percentiles` contains `p50`, `p95`, and `p99` for each of `stt_ms`,
`rag_ms`, `llm_ms`, `mcp_tools_ms`, `tts_ms`, `transport_ms`, and `total_ms`.

Worked `RunRollup` example:

```json
{
  "schema_version": "analytics-model/v1",
  "run_id": "run_fixture_sales_booking",
  "project": "proj_fixture",
  "tenant": null,
  "window_start_ms": 1772700000000,
  "window_end_ms": 1772700060000,
  "group_by": ["agent", "funnel_stage"],
  "dimensions": {
    "agent": "sales_booking_agent",
    "deployment": "fixture",
    "provider_stt": "fixture-stt",
    "provider_llm": "fixture-llm",
    "provider_tts": "fixture-tts",
    "funnel_stage": "booked",
    "sentiment_label": "neutral",
    "time": "2026-03-05T10:00:00Z"
  },
  "measures": {
    "stt_cost": 0.01,
    "llm_cost": 0.04,
    "tts_cost": 0.02,
    "telephony_cost": 0.015,
    "rag_cost": 0.003,
    "mcp_tool_cost": 0.002,
    "infra_cost": 0.005,
    "total_cost": 0.095,
    "billable_audio_minutes": 1.5,
    "stt_ms": 240.0,
    "rag_ms": 72.0,
    "llm_ms": 205.0,
    "mcp_tools_ms": 35.0,
    "tts_ms": 95.0,
    "transport_ms": 84.0,
    "total_ms": 731.0,
    "turn_count": 3,
    "session_count": 1,
    "tool_call_count": 1,
    "tool_error_count": 0,
    "barge_in_count": 0,
    "deadline_miss_count": 0,
    "caller_talk_ms": 840,
    "agent_talk_ms": 1160
  },
  "latency_percentiles": {
    "stt_ms": {"p50": 120.0, "p95": 120.0, "p99": 120.0},
    "rag_ms": {"p50": 24.0, "p95": 24.0, "p99": 24.0},
    "llm_ms": {"p50": 0.0, "p95": 205.0, "p99": 205.0},
    "mcp_tools_ms": {"p50": 0.0, "p95": 35.0, "p99": 35.0},
    "tts_ms": {"p50": 0.0, "p95": 95.0, "p99": 95.0},
    "transport_ms": {"p50": 28.0, "p95": 28.0, "p99": 28.0},
    "total_ms": {"p50": 207.0, "p95": 352.0, "p99": 352.0}
  },
  "derived_metrics": {
    "cost_per_minute": 0.063333,
    "cost_per_booked_outcome": 0.095,
    "conversion_rate": 1.0,
    "deadline_miss_rate": 0.0,
    "barge_in_rate": 0.0,
    "tool_success_rate": 1.0,
    "talk_ratio": 0.58
  }
}
```

## Conformance

A producer or consumer claiming `analytics-model/v1` must populate every
measure key. Absent count measures are `0`; absent additive measures are `0.0`;
derived metrics are null when their denominator is zero. Dimension values may
be null only when their source attribution is unavailable.

Implementations must preserve the fact grains, formulas, and snapshot keys.
They must not add persistence, warehouse-engine, cross-run storage, or
cross-tenant fields under this version. Those concerns stay behind the closed
semantic API.
