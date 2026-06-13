# 2_1 - Write Python/Rust ADR context

**Epic:** Architecture
**Estimated effort:** ~30 min
**State:** done

## Goal

Document why Lucy starts Python-first while still reserving Rust for measured
audio and streaming bottlenecks.

## Spec

The ADR context explains product velocity, provider-dominated latency, p99
media risks, and the need for explicit Python/Rust boundaries.

## Files to create/modify

- `docs/adr/0001-python-first-rust-hot-paths.md` - ADR context

## Definition of Done

- [x] Context names product velocity as the Python-first reason.
- [x] Context names media/high-concurrency p99 as the Rust reason.
- [x] Context does not claim Rust improves external provider latency.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
