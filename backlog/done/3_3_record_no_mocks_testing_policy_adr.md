# 3_3 - Record no-mocks testing policy ADR

**Epic:** Architecture
**Estimated effort:** ~45 min
**State:** done

## Goal

Lock Lucy's testing posture as no-mocks so future TDD work tests real local
behavior rather than invented replacements.

## Spec

Create an ADR stating that tests use real local implementations, deterministic
in-process simulators, local protocol servers, or recorded fixtures from real
interactions. Mocking frameworks and invented provider behavior are forbidden.

## Files to create/modify

- `docs/adr/0003-no-mocks-testing-policy.md` - no-mocks ADR
- `agents.md` - operating rule
- `backlog/agent_index.md` - backlog/TDD rule

## Definition of Done

- [x] ADR status is accepted.
- [x] Operating docs say Lucy is no-mocks.
- [x] Backlog rules say TDD targets real local behavior boundaries.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
