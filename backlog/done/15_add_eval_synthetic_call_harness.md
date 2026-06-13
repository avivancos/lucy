# 15 - Add eval and synthetic call harness

**Epic:** Evals
**Estimated effort:** ~8 h
**State:** done

## Goal

Create synthetic call scenarios so Lucy agents can be regression-tested without
real phone calls.

## Spec

Implement synthetic turns, call scenarios, expected outcomes, golden transcript
fixtures, and initial scenarios for happy-path booking, objection, interruption,
silence, escalation, and failed booking.

## Files to create/modify

- `src/lucy/evals.py` - synthetic call primitives
- `tests/test_evals.py` - eval harness tests

## Definition of Done

- [x] Happy-path booking scenario exists.
- [x] Scenarios serialize into API-safe payloads.
- [x] No eval test performs a real provider call.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add scoring/rubric evaluation over synthetic transcripts after the scenario
  catalog stabilizes.
- Docker Compose verification should be rerun once the Docker daemon is active.
