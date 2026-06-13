# 15_1 - Add eval scoring rubrics

**Epic:** Evals
**Estimated effort:** ~6 h
**State:** done

## Goal

Score synthetic call scenarios with repeatable quality rubrics.

## Spec

Add deterministic rubric scoring for booking correctness, escalation behavior,
RAG grounding, interruption handling, and policy adherence.

## Files to create/modify

- `src/lucy/evals.py` - scoring primitives
- `tests/test_evals.py` - rubric tests

## Definition of Done

- [x] Scoring returns typed pass/fail and reason fields.
- [x] Booking, escalation, interruption, and failed booking are covered.
- [x] Tests are deterministic and no-mocks.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add weighted rubrics once eval datasets are versioned.
