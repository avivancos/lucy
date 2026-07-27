# N - short imperative title

**Sprint:** S? - sprint name (see `backlog/sprints.md`)
**Epic:** epic / area
**Estimated effort:** ~X h
**Depends on:** card ids, or "none"
**State:** pending
**Risk tier:** T2 - one-line justification (see `docs/agents/workflows/risk-tiers.md`; default T2, escalate when in doubt)

## Goal

What and why, 2-4 lines. Describe the outcome, not the implementation.

## Context primer

What already exists and what to READ, in order, before writing anything.
An agent with no prior knowledge of this project must be able to orient
itself from this section alone.

- `path/to/file.py` - why it matters for this card
- `docs/adr/00XX-*.md` - the decision this card implements
- `agents.md` - project operating rules (always)

## Spec

Contracts, endpoints, schemas, validations, edge cases, and exact expected
behavior. Enumerate everything; never write "etc.". Include exact class and
function signatures when the design already fixed them.

## Files to create/modify

Optional summary when it helps the cross-card audit; chips remain the
authoritative per-step file lists.

- `path/to/file` - note

## Chips

Ordered atomic subtasks, 30-90 minutes each. Each chip names its action, the
exact files, the failing test to write FIRST, and the verify command with
expected outcome.

- [ ] **C1 - imperative chip title.** Action description. Files:
  `path/a.py`, `tests/test_a.py`. Test first:
  `test_specific_behavior_name`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_a.py -q` -> all pass,
  includes the new test.
- [ ] **C2 - next chip.** ... Verify: `command` -> expected outcome.
- [ ] **C3 - final chip: full suite + card bookkeeping.** Run the whole
  suite, fill "Improvements noted", move this card to `done/`. Verify:
  `docker compose run --rm lucy-api pytest -q` -> all pass.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); use `lucy.testing`
  simulators, local protocol servers, or recorded fixtures.
- Do not hardcode provider names, model names, URLs, thresholds, or budgets
  outside typed settings, registries, or named constants (agents.md).
- Do not touch files outside the ones this card lists.
- Do not check a Definition of Done box without running its command.
- Do not put platform/Pili concerns inside the SDK or vice versa (ADR 0010).
- Do not auto-merge or push to `main` (human gate; see
  `docs/agents/workflows/closing-commit.md`).

## Definition of Done

Each item is mechanically verifiable: a command and its expected outcome.

- [ ] `command` -> expected outcome
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do NOT
check boxes, do NOT force tests green. Leave the card in `in_progress/`,
document what happened under "Improvements noted", and report. Partial honest
work beats fake completion. Mark un-run verification commands `BLOCKED`,
never assumed green.

## Decision log

<!-- Written during implementation. Empty only with the explicit no-decisions
line. See docs/agents/workflows/implementation-log.md. -->

No decisions: implementation followed the spec exactly.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61).
One line per applicable reviewer with its verdict, then finding dispositions.
P0/P1 findings block the move until fixed. Depth follows Risk tier. -->

- code-reviewer: PASS | FAIL - <report ref or summary>
- test-auditor: PASS | FAIL - <report ref or summary>
- docs-reviewer: PASS | FAIL - <report ref or summary>
- simplicity-reviewer: PASS | FAIL - <report ref or summary>
- security-reviewer: PASS | FAIL - <report ref or summary, or NOT_APPLICABLE with reason>
- final-integrator: PASS | FAIL | BLOCKED - <report ref or summary>

Findings disposition:

- [P0|P1|P2|P3][reviewer-NNN] finding - fixed | follow-up card NN | rejected: rationale

## Closing commit

<!-- Filled after the closing commit. See docs/agents/workflows/closing-commit.md. -->

- Hash: `<short-sha>` — <subject line>
- Branch: `<branch>` · Files: <n> · Date: <YYYY-MM-DD>
- Landed on main: pending human merge

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
