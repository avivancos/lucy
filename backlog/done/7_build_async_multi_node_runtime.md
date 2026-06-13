# 7 - Build async multi-node runtime

**Epic:** Runtime
**Estimated effort:** ~8 h
**State:** done

## Goal

Create the first async runtime for streaming multi-node voice-agent workflows.

## Spec

Implement graph nodes, context, ordered execution, deadlines, retries, fallbacks,
cancellation propagation, and trace events. The v0 executor may be ordered, but
its public contract must support future DAG execution.

## Files to create/modify

- `src/lucy/runtime.py` - graph runtime
- `tests/test_runtime.py` - runtime behavior tests

## Definition of Done

- [x] Nodes run and write named results.
- [x] Deadlines trigger fallback when configured.
- [x] Failures raise typed runtime errors when no fallback exists.
- [x] Trace events include node, status, and latency.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add DAG/parallel edge execution in a follow-up once ordered runtime semantics
  are fully consumed by the voice pipeline.
- Docker Compose verification should be rerun once the Docker daemon is active.
