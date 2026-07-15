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

`BudgetPolicy` reads optional USD, RPM, and TPM soft/hard thresholds from the
`LUCY_GOVERNANCE_` environment namespace. A shared `BudgetController` retains
agent spend across sessions while session-scoped state is released when a call
ends. Environment strings are parsed into strict numeric fields; programmatic
booleans, fractional rate limits, non-finite values, and reversed soft/hard
pairs are rejected. Each live session identity is bound to exactly one agent
identity and one opaque lifecycle lease. Every ledger mutation authenticates
that lease. Duplicate opens fail without revealing the existing owner, and only
the current lease can mutate or close the session, so a stale caller cannot
reset or charge a new lifecycle. Cost ledgers accept finite
non-negative priced results and saturate above the largest configured threshold,
preserving a deterministic hard action without retaining unbounded numeric
state.

USD limits require a USD price book. `VoiceSession` validates that currency and
owns the complete multimodal cost calculation. `BudgetedLlmProvider` decorates
any `LlmProvider`: RPM is checked before dispatch, actual provider usage feeds
the rolling TPM window, and an explicitly supplied USD `PriceBook` also enables
standalone LLM cost enforcement without a voice session. Soft and hard decisions
automatically emit `budget.check` spans through `lucy.observe`; a hard decision
becomes a typed `BudgetExceeded` for ordinary LLM use and
`SessionEnd(reason="budget_exceeded")` inside `VoiceSession`. Rolling windows use
the injected clock, keep bounded in-memory state, and do not sleep. A governed
stream cancelled or closed before usage arrives poisons its lease; further work
is rejected until the owner closes that lifecycle.
An already-breached USD ledger is checked before provider dispatch and before a
voice session accepts work. Calls also reconcile elapsed telephony and
infrastructure cost on each control event, including terminal events, so an idle
call cannot bypass a hard limit. When TPM or priced USD governance requires
provider usage, a terminal LLM stream without `UsageReport` raises the typed
`BudgetMeteringError`; a governed voice session converts it into the same budget
termination directive.

The controller is the lifecycle authority. `VoiceSession` explicitly acquires
and closes one lease, marking that lease as externally priced by the voice
session. A standalone `BudgetedLlmProvider` acquires its lease on first use and
exposes `close_budget_session()` for the caller's `try`/`finally`; standalone USD
governance fails closed before provider dispatch unless the decorator owns an
explicit price book.
When an unpriced decorator is used by a cascaded voice driver, `VoiceSession`
derives controller and agent identity from that single binding and shares the
provider-owned lease. Duplicate budget arguments and priced driver decorators
are rejected. Closing a lease releases session state; shared per-agent spend and
live rate windows remain owned by the controller, while disabled or expired
rate-only state is retired. The bounded token deque keeps a synchronized O(1)
total: at the 100,000-entry ceiling, a Docker `timeit` check measured summation
at about 1.36 ms per budget check versus under 0.01 microseconds for the cached
total, a material difference on the voice latency path.

Pricing has one owner per execution mode. `VoiceSession` owns complete call
pricing and records only increases in its cumulative multimodal total. A
decorator inside a voice driver is unpriced and governs RPM/TPM. Standalone
decorators may own an explicit price book and USD ledger. Incompatible priced
compositions fail before the call starts.

An explicit `VoiceSession` controller may govern responder-mode multimodal USD
costs. Driver-mode RPM/TPM requires an unpriced `BudgetedLlmProvider` binding;
an unwrapped USD-governed driver must still report usage or the session ends
fail-closed. Cancellation performs one final monotonic cost reconciliation,
including elapsed call cost and any already-reported active-turn usage, before
the lease closes.
