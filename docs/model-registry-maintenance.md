# Lucy Model Registry Maintenance

Lucy keeps provider compatibility in `src/lucy/providers.py`. The checked-in
registry is the source of truth for product behavior; revalidation reports drift
but never mutates the registry automatically.

## Revalidation Sources

- Curated recorded fixtures for automated tests and CI.
- Live provider metadata adapters for manual or scheduled maintenance runs.
- Release notes reviewed by a maintainer before registry changes are accepted.

## Required Review

Every registry update must record:

- Added models.
- Removed models.
- Renamed models.
- Capability changes.
- Low-latency suitability changes.
- Date of the observed catalog snapshot.

Automated tests must not call provider networks. Live checks belong in explicit
maintenance commands or scheduled jobs with credentials outside the test suite.

## Router Assumptions

`lucy.router` receives provider instances only after plugin resolution. Route
keys identify deployments, not secrets, and remain stable for telemetry. The
registry's `low_latency` flag is the cold-start prior; measured first-token EWMA
orders routes within that prior once observations exist. Priority and weight are
deployment policy, so applications must supply them through typed `Route` and
`RouterSettings` values rather than editing the model catalog.

A failed route enters an in-process cooldown. LLM failover is allowed only before
the first token reaches the caller; after that boundary, the original provider's
error is surfaced to prevent mixed responses. STT and TTS selection happens at
session start, while mid-call media failover remains gateway-owned.
