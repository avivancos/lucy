# 8_1 - Add provider catalog revalidation

**Epic:** Providers
**Estimated effort:** ~6 h
**State:** done

## Goal

Keep Lucy's model registry fresh as provider catalogs change.

## Spec

Add an explicit revalidation workflow that compares the checked-in model registry
against live provider metadata or curated recorded fixtures, then reports added,
removed, renamed, and capability-changed models.

## Files to create/modify

- `src/lucy/providers.py` - validation metadata if needed
- `tests/test_registry_mcp_metrics.py` - registry revalidation contract
- `docs/` - provider registry maintenance notes

## Definition of Done

- [x] Registry revalidation is explicit and repeatable.
- [x] Provider changes are reported without mutating the registry automatically.
- [x] Tests remain deterministic and no-mocks.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add a CLI wrapper once maintenance commands are introduced.
