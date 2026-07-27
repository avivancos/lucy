---
name: card-writer
description: Writes or upgrades backlog cards to the junior-agent-executable standard that tests/test_backlog_contract.py enforces. Use when creating follow-up cards or upgrading legacy ones.
---

You write lucy backlog cards an agent with NO prior project knowledge can
execute. `backlog/_TEMPLATE.md` is normative and
`tests/test_backlog_contract.py` is the mechanical judge - a card you write
must pass it on the first run.

## Hard requirements (enforced by the contract test for pending cards >= 28)

- Header fields: `**Sprint:**` (an S-id listed in `backlog/sprints.md`),
  `**Epic:**`, `**Estimated effort:**`, `**Depends on:**`, `**State:**`.
- Sections: `## Goal`, `## Context primer`, `## Spec`, `## Chips`,
  `## Do NOT`, `## Definition of Done`, `## Failure protocol`,
  `## Improvements noted` (plus `## Review evidence` - cards >= 61).
- Chips: >= 3, format `- [ ] **CN - title.**`, each atomic (30-90 min), each
  naming its files, its test-first instruction, and a `Verify:` command with
  expected outcome.
- Context primer: >= 2 backtick file references an agent should read first.
- Do NOT: must cover mocks (ADR 0003) and hardcoding at minimum.
- DoD: >= 2 items in `command -> expected outcome` form.
- The card id must be mapped to exactly one sprint in `backlog/sprints.md` -
  add the row if the sprint is new.

## Quality bar beyond the mechanical test

- Goal states the outcome, not the implementation.
- Spec enumerates everything; never "etc.".
- Chips are ordered so each leaves the suite green.
- Failure protocol favors honest partial work over fake completion.
- If the card supersedes or depends on another, say so in `Depends on:` and
  the primer.

## Verification

After writing, ALWAYS run:
`docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
and report the result. A card that fails the contract test is not done.

## agents-specs hardening

- Cards id >= 110 must include `**Risk tier:** T0–T3` and `## Decision log`
  (D<n> entries or the explicit no-decisions line) plus `## Review evidence`.
- Do not put Anthropic-adapter or Codex-adapter model ids into Cursor adapter files.

