# ADR 0010 - Open-Core Split

## Status

Accepted

## Context

Lucy is becoming two products with one codebase: an open-source, provider-agnostic
voice-agent SDK (the LangGraph analog) and a commercial observability and fleet
platform (the LangSmith analog). Without an explicit boundary, framework code,
platform code, and vertical product code (Pili) keep growing in one package, which
blocks publishing the SDK and blurs what is monetizable.

## Decision

Adopt an open-core model with one guiding rule: **anything that runs inside the
user's process is open; anything that stores, aggregates, or compares across runs
or tenants is closed.**

Open (SDK, Apache-2.0 license, public repo):

- Specs, graph runtime, voice contracts, provider registry, MCP client, RAG
  primitives, metric models, and local evals.
- `lucy.observe`: the instrumentation API and local exporters (console, JSONL,
  OpenTelemetry bridge). This is the telemetry client seam.
- `lucy.testing`: the deterministic simulators, shipped as a public, documented
  subpackage so the no-mocks policy (ADR 0003) extends to plugin authors.
- `lucy.serve`: the framework serving runtime (health, realtime SSE, session
  control channel) plus a deliberately minimal local trace viewer.
- The Rust media gateway (ADR 0004).
- Provider plugins and the `lucy-cloud` telemetry client ship as open
  packages under `packages/` (open client, closed server).

Closed (platform, private repo):

- Trace ingest, storage, and search; the hosted dashboard (the existing Next.js
  app moves there); evals at scale and model-diff regression gates; fleet and
  tenant management; hosted LoRA adapters (ADR 0006) and voice identity
  (ADR 0008); the managed SIP edge service.

Out of the framework entirely:

- Pili routes and schemas are a vertical product. They move to a private `pili`
  repository that depends on Lucy like any customer, with a sanitized copy kept
  as a public example.

Licensing is Apache-2.0 for everything open (patent grant matters in
voice/telephony; section 6 withholds trademark rights). The public distribution
name is deferred; `lucy` remains the import name until the pre-publication
scrub, and the rename is a mechanical find-replace recorded in the roadmap.

The telemetry wire protocol (`docs/telemetry-wire-v1.md`) is a public, normative
spec. The SDK emits to local exporters by default and to the platform only when
an API key is configured. PII redaction, audio suppression, and sampling are
enforced client-side in open code before anything leaves the process.

## Consequences

- `api/app.py` splits three ways: framework serving stays in `lucy.serve`,
  fleet-shaped routes seed the private platform, Pili routes leave with Pili.
- The voice provider Protocols and the spec models become the frozen public ABI;
  changes to them are breaking changes governed by SemVer.
- The platform consumes the SDK from the outside (same wire spec, same public
  APIs), which keeps the SDK genuinely useful standalone.
- Operating docs (`agents.md`, `MEMORY.md`, `backlog/`) stay in this repo until
  the pre-publication scrub milestone, then move private.
