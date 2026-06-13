# 6 - Define orthogonal spec framework

**Epic:** Specs
**Estimated effort:** ~5 h
**State:** done

## Goal

Create independent typed specs for every major Lucy concern so behavior stays
composable and testable.

## Spec

Implement `AgentSpec`, `VoiceSpec`, `RagSpec`, `McpSpec`, `CrmSpec`,
`ObservabilitySpec`, and `EvalSpec`. Each spec validates independently and
serializes through a combined `LucySpec`.

## Files to create/modify

- `src/lucy/specs.py` - orthogonal Pydantic specs
- `tests/test_specs.py` - validation and serialization tests

## Definition of Done

- [x] Blank critical text fields are rejected.
- [x] Voice naturalizer/modulator settings are typed and bounded.
- [x] `LucySpec` composes specs without merging unrelated concerns.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
