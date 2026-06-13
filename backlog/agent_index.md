# Backlog - Canonical Rules

The current state of a task is the folder it sits in.

## Read Before Work

Before formal implementation work, list open work from `pending/` and
`in_progress/`, show the user a one-line summary per task, and wait for an
explicit instruction on which task to attack. The only override is the user
literally saying "ignore the backlog".

## States

```text
pending/             # not started
in_progress/         # active, at most one per agent
done/                # implemented, not yet verified
need_human_testing/  # implemented, blocked on human-only verification
testing/             # verified by the agent
production/          # deployed
decisions/           # ADR-style decision log, not a work state
```

Mandatory flow:

```text
pending -> in_progress -> done -> testing -> production
                          -> need_human_testing -> testing/production
```

## Sprints

`backlog/sprints.md` maps every card to one scope-based sprint with an exit
demo. The folder remains the state; the sprint is metadata in the card header
(`**Sprint:** S0..S8 plus the sprint name`). Work sprints in order unless the dependency
notes in `sprints.md` allow an early start.

## Card Quality Standard

Cards are written so an agent with NO prior project knowledge can execute
them. `_TEMPLATE.md` is normative; the required sections are: header with
Sprint/Epic/effort/Depends on/State, Goal, Context primer (exact files to
read first), Spec (everything enumerated, no "etc."), Chips (ordered atomic
subtasks with test-first instruction and verify command + expected outcome),
Do NOT (guardrails), Definition of Done (mechanically verifiable), Failure
protocol, Improvements noted. `tests/test_backlog_contract.py` enforces this
for upgraded pending cards.

## Spec And TDD

Every formal task follows Spec -> red test -> green code -> refactor -> docs.
Lucy is a no-mocks project: automated tests use real local implementations,
deterministic in-process simulators, local protocol servers, or recorded
fixtures from real interactions. Do not use mocking frameworks or invented
provider behavior to make tests pass.

## Definition Of Done

- Targeted tests pass in Docker Compose.
- API/CLI changes are smoke-tested, or UI changes are visually audited.
- Improvements noticed during implementation are recorded and follow-up cards are
  created.
- The task card is moved to `done/` or `need_human_testing/`.
- A closing commit stages only task-related files by explicit path.
