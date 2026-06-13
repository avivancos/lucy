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
