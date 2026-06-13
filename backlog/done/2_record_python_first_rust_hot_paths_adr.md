# 2 - Record Python-first Rust-hot-paths ADR

**Epic:** Architecture
**Estimated effort:** ~1 h
**State:** done

## Goal

Lock the architectural decision that Lucy starts Python-first and reserves Rust
for measured media and streaming hot paths.

## Spec

An ADR explains context, decision, consequences, Python ownership, Rust sidecar
boundaries, and the rule that Rust enters only after instrumentation proves a
bottleneck or concurrency limit.

## Files to create/modify

- `docs/adr/0001-python-first-rust-hot-paths.md` - ADR

## Definition of Done

- [x] ADR status is accepted.
- [x] ADR names Python-owned and Rust-owned responsibilities.
- [x] ADR requires explicit and observable Python/Rust boundaries.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
