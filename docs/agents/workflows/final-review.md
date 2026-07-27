# Final review workflow

## Purpose

Every implementation receives independent specialist reviews before its task
is considered verified. Review depth follows the card's declared risk tier
([risk-tiers.md](risk-tiers.md)). Cards with id >= 61 also record verdicts in
`## Review evidence` (see `backlog/agent_index.md`).

## Reviewer set

- **T0** — no reviewer agents; deterministic gates only.
- **T1** — `code-reviewer` only.
- **T2** (default) — all five plus `final-integrator`:
  1. `docs-reviewer`
  2. `test-auditor`
  3. `code-reviewer`
  4. `simplicity-reviewer`
  5. `security-reviewer` (or `NOT_APPLICABLE` with reason when no security
     trigger applies — still record the disposition)
  6. `final-integrator` consolidates evidence
- **T3** — same set with model escalations from the tool adapter (Cursor:
  `inherit` for code/security/final-integrator when unresolved risk remains).

Spawn paths: `.cursor/agents/` (Cursor), `.claude/agents/` (Claude Code),
orchestrator MCP when connected. A project may add specialists but must not
run fewer reviewers than the card's tier requires.

## Entry conditions

Start final review only when:

- The selected card is in `in_progress/`.
- The card declares a risk tier with a one-line justification.
- The card's `## Decision log` is present and current, or carries the
  explicit no-decisions line ([implementation-log.md](implementation-log.md)).
- The hard specification and applicable ADRs are identifiable.
- The implementation diff is complete enough to review.
- The red-to-green cycle has completed.
- Targeted tests pass in Docker Compose
  (`docker compose run --rm lucy-api pytest …`).
- The implementer recorded exact commands and observed results so far.

If an entry condition is missing, do not pretend final review ran. Report the
missing evidence and return the task to implementation.

## Orchestration

1. Freeze the review target: card, risk tier, decision log, diff, file list,
   specs, ADRs, verification evidence.
2. Spawn the specialist agents required by the tier (Cursor: pass the model
   from the Cursor adapter table in `AGENTS.md`).
3. Run in parallel when capacity permits; otherwise in waves without omitting
   required reviewers.
4. Reviewers are read-only and do not coordinate conclusions before first
   reports.
5. Any reviewer may escalate the tier; restart review at the new set.
6. Merge findings without losing evidence. At T3, adjudication uses the
   strongest model allowed by the adapter (`inherit` on Cursor).
7. P0/P1: fix before verification. P2/P3: fix, follow-up card, or written
   rejection.
8. After fixes, rerun targeted tests and every reviewer whose area changed.
9. Repeat until required reviewers return `PASS`, or report a blocker.

If sub-agents cannot spawn, run labeled sequential passes with the same
contracts and disclose the degraded mode. Evidence and pass criteria do not
shrink.

## Final verification gate

After required reviews pass, run in Docker Compose:

1. Formatting and lint (`ruff check`, `ruff format --check`)
2. Static type checks (`mypy src`)
3. Targeted tests
4. Relevant regression tests
5. Full suite when required — **mandatory at T3**
6. API/CLI smoke or UI visual audit
7. Documentation validation when docs changed

At T3, scoped mutation testing over changed code is mandatory: run it or mark
`BLOCKED` with the reason. Never invent survivor counts.

Record exact commands, exit status, and observed results. A command that was
not executed must be marked `BLOCKED`, never assumed green.

## Closure semantics

- `done/` — implementation complete, not yet fully verified (Lucy flow), with
  Review evidence filled for cards >= 61.
- `need_human_testing/` — final evidence needs a human-only action.
- `testing/` / `production/` — per `backlog/agent_index.md`.

State moves follow [closing-commit.md](closing-commit.md): start commit on
`in_progress/`, closing commit plus stamp on `done/`. Landing onto `main` is
a **human gate** (honor-system) — agents do not auto-merge or push unless the
human explicitly requests it.

## Required aggregate report

```markdown
## Final review — <card id and title>

### Review target
- Card: `<path>`
- Risk tier: T<n> — <justification or escalation note>
- Diff or commit: `<identifier>`
- Specifications: <paths>
- ADRs: <paths or "None applicable">

### Specialist verdicts
- Documentation: PASS | FAIL | BLOCKED | NOT REQUIRED (T<n>) — summary
- Testing: PASS | FAIL | BLOCKED | NOT REQUIRED (T<n>) — summary
- Code: PASS | FAIL | BLOCKED — summary
- Simplicity: PASS | FAIL | BLOCKED | NOT REQUIRED (T<n>) — summary
- Security: PASS | FAIL | BLOCKED | NOT_APPLICABLE | NOT REQUIRED (T<n>) — summary
- Final integrator: PASS | FAIL | BLOCKED — summary

### Findings disposition
- [P0|P1|P2|P3] <finding> — fixed | follow-up card | rejected with rationale

### Verification evidence
- `<exact command>` — PASS | FAIL | BLOCKED — observed result

### Final verdict
PASS | FAIL | BLOCKED
```

`PASS` only when all required specialist verdicts pass, no P0/P1 remain, every
P2/P3 has a disposition, and all required verification gates have evidence.
