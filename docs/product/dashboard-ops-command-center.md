# Dashboard Ops Command Center

Lucy's dashboard is an operator and builder cockpit for production voice agents.

## Core Views

- Live calls: transcript, waveform placeholder, current node, sentiment, funnel
  stage, CRM sync state, and escalation status.
- Trace waterfall: STT, RAG, LLM, MCP tools, TTS, transport, and total latency.
- Cost board: cost per minute by provider, deployment, agent, tenant, and call
  outcome.
- RAG inspector: retrieved chunks, scores, cache hits, grounding, and prompt
  inclusion.
- Model/version diffs: compare outcomes, latency, cost, and regressions.
- Evals: synthetic call runs, golden transcripts, quality gates, and failures.

## Quality Bar

The dashboard must be dense, legible, responsive, and operational. It should feel
like infrastructure software, not a marketing page.

## Live Calls Wireframe

Layout: left column with active session list, center transcript stream with turn
metadata, right rail with current graph node, sentiment, funnel stage, CRM sync,
barge-in state, and escalation controls.

Acceptance criteria:

- Operators can identify stuck calls without opening a modal.
- Transcript rows show speaker, timestamp, partial/final state, and provider.
- CRM sync state is visible beside the funnel stage.

States:

- Empty state: no live sessions, show last completed session summary.
- Loading state: skeleton rows for calls and transcript.
- Error state: degraded realtime banner with retry timestamp.

## Trace Waterfall Wireframe

Layout: full-width latency waterfall with lanes for STT, RAG, LLM, MCP tools,
TTS, transport, and total perceived latency. Selecting a lane opens the span
attributes and provider/model/version metadata.

Acceptance criteria:

- P50, P95, and latest latency are visible at the same time.
- Deadline misses and fallback paths are marked inline.
- Trace order stays stable when new events stream in.

States:

- Empty state: prompt to select a session or trace.
- Loading state: fixed-height lane placeholders.
- Error state: trace unavailable message with raw trace id.

## Cost Board Wireframe

Layout: metric grid for cost per minute, total cost, billable minutes, provider
mix, and cost by outcome. Drilldowns group by tenant, agent, deployment,
provider, and funnel result.

Acceptance criteria:

- `cost_per_minute` is the primary metric.
- STT, LLM, TTS, telephony, RAG, MCP, and infra costs are separated.
- Operators can compare successful bookings against failed bookings.

States:

- Empty state: no billable calls in selected range.
- Loading state: stable metric tiles with pending values.
- Error state: cost accounting incomplete with missing component names.

## RAG Inspector Wireframe

Layout: query panel, retrieved chunk table, score components, cache hit status,
grounding ids, prompt inclusion toggle, and source preview.

Acceptance criteria:

- Lexical and vector scores are visible when hybrid retrieval is used.
- Grounding ids remain copyable and stable.
- Cache hit/miss and deadline fallback are explicit.

States:

- Empty state: no retrieval for selected turn.
- Loading state: chunk table skeleton with fixed columns.
- Error state: retrieval failed with deadline/provider reason.

## Sentiment Funnel CRM Wireframe

Layout: per-turn sentiment strip above a funnel timeline, with CRM event payloads
and sync/audit status in a right panel.

Acceptance criteria:

- Sentiment confidence and funnel confidence are shown separately.
- CRM-ready payload can be inspected without exposing credentials.
- Escalation and failed booking are first-class states.

States:

- Empty state: no CRM events for selected session.
- Loading state: timeline placeholders.
- Error state: CRM sync failed with MCP audit reference.

## Model Diffs Wireframe

Layout: two-column comparison for model/provider/version A and B, with outcome,
latency, cost, sentiment drift, fallback rate, and regression badges.

Acceptance criteria:

- Diffs can be tied back to real calls and eval runs.
- Provider capability changes are visible from registry revalidation.
- Low-latency recommendations are labeled, not hardcoded into UI logic.

States:

- Empty state: select two model versions.
- Loading state: comparison table placeholders.
- Error state: insufficient sample size or missing registry version.

## Evals Wireframe

Layout: scenario list, run status, synthetic transcript, expected outcome,
actual outcome, quality gates, and failure reasons.

Acceptance criteria:

- Booking, objection, interruption, silence, escalation, and failed booking are
  represented.
- Red/green outcome is backed by explicit rubric fields.
- Failed evals link to trace waterfall and replay artifacts.

States:

- Empty state: no eval runs yet.
- Loading state: run queue with pending scenarios.
- Error state: eval harness failed with deterministic input id.

## Follow-Up Implementation Cards

- Build routed dashboard views for live calls, traces, costs, RAG, CRM, model
  diffs, and evals.
- Connect `/metrics/realtime` SSE to TanStack Query or a lightweight event store.
- Add Playwright coverage for empty state, loading state, and error state for
  each view.
