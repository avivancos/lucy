# ADR 0013 - Analytics and SRE Observability

## Status

Accepted

## Context

Lucy exposes typed telemetry and local observability, while the hosted platform
stores and compares many runs and tenants. Without a shared semantic model, the
open runtime rollups and closed warehouse could assign different grains,
dimensions, or formulas to the same voice-agent measures.

## Decision

Adopt `analytics-model/v1` as the frozen semantic contract derived from telemetry
wire v1.

Open:

- The analytics semantic model in `docs/analytics-model-v1.md`.
- The in-process rollup engine `lucy.analytics` delivered by card 53.
- The runtime-local analytics surface delivered by card 54.

Open (SRE seam):

- Service RED/USE metrics exposed in-process by card 55.
- Self-host operations definitions delivered by card 56.

Closed:

- Trace and event storage.
- The analytics warehouse and ETL delivered by platform card 58.
- The cross-run query and semantic API delivered by platform card 59.
- Hosted BI dashboards delivered by platform card 60.

The open model defines semantics and portable rollup shapes. It does not define
tenant storage, cross-run persistence, hosted query execution, or dashboard
implementation.

## Consequences

- Open rollups and the closed warehouse must pass the same model conformance
  fixtures before either can claim `analytics-model/v1` compatibility.
- The model is governed by SemVer like the telemetry wire and public spec ABI:
  additive compatible fields use a minor revision; removed, renamed, or changed
  semantics require a new major version.
- Storage engines can change without changing dashboard contracts. Postgres is
  the initial warehouse; a later engine remains behind the semantic API.
- A metric absent from this model requires an additive model revision rather
  than a private warehouse-only interpretation.
