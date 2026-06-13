# 2_2 - Define Python/Rust boundary

**Epic:** Architecture
**Estimated effort:** ~45 min
**State:** done

## Goal

Make the Python/Rust split precise enough that future implementation does not
blur platform logic with media hot paths.

## Spec

The ADR states Python owns API, specs, runtime v0, MCP, RAG, CRM, evals, model
registry, and metrics. Rust may own media gateway, WebRTC/SIP routing, jitter
buffers, VAD, frame processing, backpressure, and high-concurrency streaming.

## Files to create/modify

- `docs/adr/0001-python-first-rust-hot-paths.md` - decision and consequences

## Definition of Done

- [x] Python-owned responsibilities are listed.
- [x] Rust-eligible responsibilities are listed.
- [x] Rust entry is gated by instrumentation or concurrency limits.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
