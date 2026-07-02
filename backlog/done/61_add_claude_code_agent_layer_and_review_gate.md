# 61 - Add the Claude Code agent layer and the backlog review gate

**Sprint:** S10 - Agent operations
**Epic:** Agent operations
**Estimated effort:** ~5 h
**Depends on:** none
**State:** done

## Goal

Make lucy's operating contract auto-load in every Claude Code session and make
reviewer verdicts a machine-enforced part of Done, porting the reviewer-roster
pattern from english-tutor with per-agent model tiering for token economy.

## Context primer

- `agents.md` - the operating contract this card wires into Claude Code
- `backlog/agent_index.md` - canonical backlog process (gains the review gate)
- `tests/test_backlog_contract.py` - the enforcement point for card quality
- `/Users/agustin/Desarrollo/english-tutor/agents/sub-agents/` - the reviewer
  roster being ported (report shape, severity ladder P0-P3)
- `/Users/agustin/Desarrollo/vibe/dev-agent/orchestrator/` - the MCP whose
  `backlog_next`/`backlog_move`/`request_*` tools this card wires in

## Spec

- `CLAUDE.md` (repo root): thin loader importing `@agents.md` and
  `@backlog/agent_index.md`; documents reviewer routing, the review gate, the
  role->model policy, and orchestrator usage with git-mv fallback. No rule may
  live only in CLAUDE.md (agents.md stays the source of truth).
- `.claude/agents/`: six subagents - `code-reviewer`, `test-auditor`,
  `docs-reviewer`, `simplicity-reviewer`, `security-reviewer`, `card-writer` -
  each with frontmatter (`name`, `description`, `tools: Read, Grep, Glob,
  Bash`, tiered `model:`) and the english-tutor report contract (Verdict,
  Findings `[P0..P3][reviewer-NNN]`, Checks executed, Missing evidence,
  Required follow-ups), adapted to lucy invariants (no-mocks ADR 0003,
  open-core ADR 0010, typed-config, telemetry wire).
- Review gate: `_TEMPLATE.md` gains `## Review evidence`;
  `agent_index.md` documents the gate (P0/P1 block done);
  `test_backlog_contract.py` enforces substantive Review evidence for cards
  >= 61 in done/testing/production, plus template and index assertions.
- `sprints.md`: S10 row (cards 61, 62) + dependency note.
- Standing authorization: `agent_index.md` records the supervisor-loop
  carve-out (sprint-order autonomy; parks on P0/P1 and need_human_testing;
  never pushes).

## Files to create/modify

- `CLAUDE.md`, `.claude/agents/*.md` - new
- `backlog/_TEMPLATE.md`, `backlog/agent_index.md`, `backlog/sprints.md`
- `tests/test_backlog_contract.py`
- `backlog/pending/62_*.md` - follow-up card (full enforcement tier)

## Chips

- [x] **C1 - red contract test.** Add review-gate assertions (template
  section, index documentation, reviewed-state evidence) to
  `tests/test_backlog_contract.py`. Test first:
  `test_card_template_carries_review_evidence_section`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> 2 failures (template + index), reviewed-states vacuously green.
- [x] **C2 - green backlog gate.** `_TEMPLATE.md` Review evidence section,
  `agent_index.md` gate + orchestrator wiring + standing authorization,
  `sprints.md` S10. Files: `backlog/_TEMPLATE.md`, `backlog/agent_index.md`,
  `backlog/sprints.md`. Verify: same pytest command -> all green.
- [x] **C3 - CLAUDE.md + agents roster.** Write `CLAUDE.md` and the six
  `.claude/agents/*.md` with tiered models. Files: `CLAUDE.md`,
  `.claude/agents/`. Verify: `docker compose run --rm lucy-api pytest -q` ->
  full suite green (no python surface changed; contract tests still pass).
- [x] **C4 - dogfood + close.** Run the reviewer roster on this card's diff,
  record verdicts below, move to `done/`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> green with this card in done/ carrying Review evidence.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003) in any test touched.
- Do not hardcode model names/thresholds outside the documented policy tables
  (CLAUDE.md + dashboard config are the named homes; agents.md rule).
- Do not duplicate agents.md rules into CLAUDE.md - import, don't copy.
- Do not weaken existing contract tests while extending them.
- Do not add pre-push hooks or attestation scripts here (that is card 62).

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
      -> green, including the three new review-gate tests
- [x] `docker compose run --rm lucy-api pytest -q` -> full suite green (153)
- [x] This card sits in `done/` with substantive `## Review evidence`
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do NOT
check boxes, do NOT force tests green. Leave the card in `in_progress/`,
document what happened under "Improvements noted", and report. Partial honest
work beats fake completion.

## Improvements noted

- The gate's very first dogfood run caught two real defects in its own
  detection logic (see dispositions) - evidence that independent reviewers
  add value over self-review. The fix replaced regex placeholder-guessing
  with verbatim comparison against the template's own section body.
- Reviewer subagents defined mid-session are not yet registered as native
  subagent types; they were spawned as general-purpose agents instructed to
  adopt their definition files. From the next session on, Claude Code loads
  `.claude/agents/` natively.
- Card 62's spec depth was challenged (simp-003); kept as design capture
  from english-tutor's proven implementation, to be re-validated at pickup.

## Review evidence

Reviewed diff: card 61 working tree (tests/test_backlog_contract.py,
backlog/_TEMPLATE.md, backlog/agent_index.md, backlog/sprints.md, CLAUDE.md,
.claude/agents/*, cards 61/62). Models per policy: sonnet x3, haiku x1.

- code-reviewer: FAIL -> PASS after fixes - initial [P2][code-001],
  [P3][code-002]; verified REVIEW_GATE_FROM boundary, need_human_testing
  exemption, model-table consistency.
- test-auditor: FAIL -> PASS after fixes - initial [P1][test-001] proven by
  mutation tests A-F in an isolated scratch copy; confirmed no-mocks and
  clock rules hold; template-deletion mutation correctly fails.
- docs-reviewer: PASS - imports resolve, routing table matches frontmatter,
  S10 row consistent, no stale claims.
- simplicity-reviewer: FAIL -> PASS after fixes - initial [P1][simp-001],
  [P1][simp-002], [P2][simp-003].
- security-reviewer: NOT_APPLICABLE - no telemetry, secrets, MCP-permission,
  or public-surface change in this diff (process files and contract tests
  only).

Findings disposition:

- [P1][test-001] one filled line unlocked the gate while template rows
  remained - fixed: `_is_substantive` now voids evidence on any leftover
  unfilled template row; unit test `test_is_substantive_detects_unfilled_
  and_accepts_real_evidence` locks the mutation cases in-suite.
- [P1][simp-001] placeholder regex over-engineered with false positives -
  fixed: verbatim template-line-set comparison replaced the 5-branch regex.
- [P1][simp-002] CLAUDE.md restated agent_index rules (P0/P1 blocking,
  orchestrator fallback) - fixed: trimmed to pointers; only the routing/model
  table and dashboard_report remain as CLAUDE.md-unique content.
- [P2][code-001] angle-bracket prose misclassified as placeholder - fixed by
  the simp-001 rewrite (superset).
- [P3][code-002] no true-positive coverage of the detection logic - fixed:
  the new unit test exercises unfilled, partial, bare, and filled bodies.
- [P3][test-002] agent_index gate test is a loose substring check - rejected:
  proportionate for a doc-presence assertion; revisit only if the section is
  reorganized.
- [P2][simp-003] card 62 over-specified before the gate is proven - rejected
  with rationale: the spec is captured design from english-tutor's working
  implementation (reference port, not invention); the card must be
  re-validated at pickup and S10 gates nothing (sprints.md note).

Post-fix verification: `docker compose run --rm lucy-api pytest -q` -> 153
passed (contract suite includes the new true-positive unit test).
