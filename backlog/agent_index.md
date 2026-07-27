# Backlog - Canonical Rules

The current state of a task is the folder it sits in.

## Read Before Work

Before formal implementation work, list open work from `pending/` and
`in_progress/`, show the user a one-line summary per task, and wait for an
explicit instruction on which task to attack. The only override is the user
literally saying "ignore the backlog".

Standing authorization (card 61): the dev-agent supervisor loop, when this
repo is enrolled with `autonomy: sprint-order`, may select cards WITHOUT a
per-card human instruction - but only in `sprints.md` order respecting its
dependency notes. It parks at `need_human_testing`, parks on unresolved P0/P1
review findings, and never pushes. Interactive sessions still follow the
paragraph above.

**Auto-carding:** if a prompt requests new implementation work not covered by
an existing card, first create a card from `_TEMPLATE.md`, map it in
`sprints.md`, then proceed. Search all state folders for duplicates first.
Questions, explorations, and reviews are not carded.

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

State moves prefer the orchestrator MCP when connected -
`mcp__dev-agent-orchestrator__backlog_next` (pending -> in_progress) and
`backlog_move` (everything else), which validate the move and notify the
dashboard. Fallback when the server is not connected: `git mv` between state
folders (the folder remains the source of truth either way).

Moving a card into `in_progress/` triggers a **start commit** of the card
alone (`chore(backlog): start card <NNN>`). Moving to `done/` triggers a
**closing commit** (explicit paths) plus a `## Closing commit` stamp. Agents
do not auto-merge or push to `main` (human gate, honor-system). See
`docs/agents/workflows/closing-commit.md`.

## Review gate (cards >= 61)

Moving a card `in_progress -> done` requires recorded reviewer verdicts in the
card's `## Review evidence` section (template: `_TEMPLATE.md`; enforced by
`tests/test_backlog_contract.py`). Review depth follows the card's
`**Risk tier:**` (`docs/agents/workflows/risk-tiers.md` and
`docs/agents/workflows/final-review.md`):

- **T0** — deterministic gates only; no reviewer agents.
- **T1** — `code-reviewer` only.
- **T2** (default) — `code-reviewer`, `test-auditor`, `simplicity-reviewer`,
  plus `docs-reviewer` when docs/ADRs/README were touched or could go stale,
  plus `security-reviewer` when telemetry/secrets/MCP/public surface changed
  (otherwise record `NOT_APPLICABLE` with a reason), then `final-integrator`.
- **T3** — full set with strongest-model escalations; mutation testing or
  `BLOCKED`.

P0/P1 findings BLOCK the move to `done/` until fixed; P2/P3 need an explicit
disposition (fixed, follow-up card raised, or rejected with rationale). A
command not executed is `BLOCKED`, never assumed green.

Request reviews through the orchestrator (`request_review`,
`request_test_audit`) when connected; otherwise spawn reviewers from
`.cursor/agents/` (Cursor), `.claude/agents/` (Claude Code), or the matching
Codex agents. Cards earlier than 61 predate the gate.

Cards from id >= 110 also require `**Risk tier:**` and `## Decision log`
(enforced by the contract test). Keep chips, Context primer, Failure
protocol, and Review evidence — do not replace them.

## Sprints

`backlog/sprints.md` maps every card to one scope-based sprint with an exit
demo. The folder remains the state; the sprint is metadata in the card header
(`**Sprint:** S0..S8 plus the sprint name`). Work sprints in order unless the dependency
notes in `sprints.md` allow an early start.

## Card Quality Standard

Cards are written so an agent with NO prior project knowledge can execute
them. `_TEMPLATE.md` is normative; the required sections are: header with
Sprint/Epic/effort/Depends on/State/Risk tier, Goal, Context primer (exact
files to read first), Spec (everything enumerated, no "etc."), Chips (ordered
atomic subtasks with test-first instruction and verify command + expected
outcome), Do NOT (guardrails), Definition of Done (mechanically verifiable),
Failure protocol, Improvements noted, Decision log, Review evidence (cards
>= 61), Closing commit stamp. `tests/test_backlog_contract.py` enforces this
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
- A closing commit lands immediately as part of the move to `done/`: one commit
  per card, staging only that card's files by explicit path. Never leave a done
  card uncommitted and never batch several cards into one commit.
