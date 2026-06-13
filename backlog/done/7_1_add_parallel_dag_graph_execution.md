# 7_1 - Add parallel DAG graph execution

**Epic:** Runtime
**Estimated effort:** ~10 h
**State:** done

## Goal

Extend the ordered graph runtime into a DAG executor that can run independent
voice-agent nodes in parallel.

## Spec

Add explicit dependencies between nodes, parallel execution for ready nodes,
deadline propagation across branches, cancellation fan-out, and deterministic
trace ordering for completed spans.

## Files to create/modify

- `src/lucy/runtime.py` - DAG execution model
- `tests/test_runtime.py` - parallel graph contract tests

## Definition of Done

- [x] Independent nodes run concurrently.
- [x] Dependent nodes wait for required upstream results.
- [x] Cancellation propagates to active branches.
- [x] Trace output remains deterministic enough for tests.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add graph visualization/export once the dashboard trace detail view exists.
