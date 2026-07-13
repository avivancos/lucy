# ADR 0015 - In-Process Cost Governance And Routing

## Status

Accepted

## Context

Lucy already emits cost telemetry and provider metadata, but the MVP platform
needs LiteLLM-style spend visibility without moving the hot path into the hosted
platform. Voice costs are multi-modal: STT minutes, LLM tokens, TTS characters or
seconds, telephony minutes, RAG, MCP tools, and infrastructure. The SDK also needs
local budget protection for offline and self-host users.

## Decision

Cost governance starts in the open SDK. A typed price book computes reported
voice costs inside the user's process and emits the existing `cost` telemetry
event with additive attribution metadata. The platform stores and compares these
reported costs, then reconciles them against provider invoices later; it does not
recompute pricing on the SDK's behalf.

The SDK owns three open capabilities:

- `lucy.pricing`: a typed voice price book loaded from settings or a JSON file,
  with per-modality prices and a version string.
- `lucy.budget`: in-process per-session and per-agent budget/rate-limit checks
  using the injectable clock, with soft warnings and typed hard actions.
- `lucy.router`: provider-instance routing for the frozen provider Protocols,
  with priority, latency, weighted selection, cooldowns, and failover before the
  first streamed token.

Hosted multi-tenant budgets, virtual keys, spend ledgers, key management, and
cross-project cost comparisons stay in `lucy-platform` under ADR 0010. Provider,
model, region, quota, latency, and budget values live in typed settings,
registries, or named constants, never inline literals.

## Consequences

- The SDK remains useful offline while producing the same cost facts the platform
  consumes.
- Provider routing will be observable through spans and cost attribution after
  card 98 adds provider identity to the cost event, so platform views can explain
  fallback and spend differences.
- Budget enforcement inside a call is deterministic and testable with
  `ManualClock`; hosted key budgets remain observed and alerted, not ingest-gated.
- Future hosted cost optimizations must integrate through the public pricing,
  routing, and telemetry seams instead of importing platform code into the SDK.

Cost attribution uses provider and media-plane facts, not latency proxies. STT
minutes come from VAD speech duration, LLM tokens from provider usage reports,
TTS usage from sent text or playback duration, and telephony plus infrastructure
minutes from the control-channel session boundary. The session event carries
the price-book version and raw numeric attribution so hosted storage can audit
the SDK calculation without moving price computation into the platform.
