# 110 - Import Cursor agents and harden Spec/TDD

**Sprint:** S10 - Agent operations
**Epic:** Agent operations / Cursor adapter
**Estimated effort:** ~8 h
**Depends on:** 61, 62
**State:** done
**Risk tier:** T2 - changes agent control system (AGENTS.md, backlog rules, Cursor adapters); never T0 for control-system files

## Goal

Wire Lucy's operating contract and backlog process into Cursor, and harden
Spec→TDD discipline by importing complementary contracts from agents-specs
(risk tiers, decision log, BLOCKED verification, final-review entry
conditions, start/closing commit stamp) without replacing Lucy's chip and
Review-evidence discipline and without auto-landing to main.

## Context primer

- `AGENTS.md` - operating rules and tool adapters
- `backlog/agent_index.md` - backlog process and review gate
- `backlog/_TEMPLATE.md` - card shape
- `tests/test_backlog_contract.py` - mechanical card/adapter contract
- `.claude/agents/` and `.codex/agents/` - existing specialist prompts
- `docs/agents/workflows/` - risk tiers, final review, decision log, closing commit
- `docs/cursor-agents-adapter.md` - Cursor adapter documentation

## Spec

1. `docs/agents/workflows/` contains adapted `risk-tiers.md`, `final-review.md`,
   `implementation-log.md`, `closing-commit.md` with Lucy T3 path signals and
   explicit no auto-land to main (honor-system human gate).
2. `AGENTS.md` documents Constraints before code, auto-carding, risk tier,
   decision log, BLOCKED, no fictional machinery, git-visible lifecycle, and
   Cursor-native model routing only (`composer-2.5-fast`,
   `cursor-grok-4.5-high-fast`, `inherit`).
3. `backlog/agent_index.md` reviews by tier; spawn paths include
   `.cursor/agents/`; start/closing commits documented.
4. `backlog/_TEMPLATE.md` adds Risk tier, Decision log, Closing commit; keeps
   chips and Review evidence.
5. `tests/test_backlog_contract.py` enforces Risk tier + Decision log for
   pending cards id >= 110 and asserts Cursor adapter files exist without
   foreign model ids.
6. `.cursor/rules/` has eight alwaysApply rules; `.cursor/agents/` has eight
   agents; `.cursor/skills/` has `chip-tdd`, `final-review`, `closing-commit`.
7. Docs: `docs/cursor-agents-adapter.md`, `docs/agents/README.md`,
   `docs/cursor-rules-map.md`. Card mapped in `backlog/sprints.md` S10.

## Files to create/modify

- `docs/agents/**`, `AGENTS.md`, `backlog/agent_index.md`, `backlog/_TEMPLATE.md`
- `tests/test_backlog_contract.py`, `backlog/sprints.md`
- `.cursor/agents/**`, `.cursor/rules/**`, `.cursor/skills/**`
- `docs/cursor-agents-adapter.md`, `docs/cursor-rules-map.md`
- this card

## Chips

- [x] **C1 - Adapt agent workflows.** Copy/adapt workflows into
  `docs/agents/workflows/` and `docs/agents/README.md`. Test first:
  `test_agent_workflows_exist_with_lucy_signals`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py::test_agent_workflows_exist_with_lucy_signals -q`
  -> pass.
- [x] **C2 - Harden canon + contract.** Update `AGENTS.md`,
  `backlog/agent_index.md`, `_TEMPLATE.md`, extend
  `tests/test_backlog_contract.py` for Risk tier/Decision log from id 110.
  Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> green once Cursor files exist.
- [x] **C3 - Cursor rules, agents, skills.** Create eight `.mdc` rules, eight
  `.cursor/agents/*.md`, three skills. Test first:
  `test_cursor_adapter_files_exist_without_foreign_model_ids`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py::test_cursor_adapter_files_exist_without_foreign_model_ids -q`
  -> pass.
- [x] **C4 - Adapter docs + sprint map.** Write
  `docs/cursor-agents-adapter.md`, `docs/cursor-rules-map.md`; map card 110
  in `backlog/sprints.md`. Verify: files exist; contract suite green.
- [x] **C5 - Full suite + bookkeeping.** Run full contract-focused suite,
  fill Improvements noted and Review evidence, prepare closing. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> all pass (13 passed).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003).
- Do not hardcode provider names, model names, URLs, thresholds, or budgets
  outside typed settings, registries, or named constants.
- Do not put Anthropic-adapter or Codex-adapter model ids in `.cursor/`
  or the Cursor routing section of `AGENTS.md`.
- Do not auto-merge or push to `main`.
- Do not remove chips or `## Review evidence` from the template.
- Do not edit `src/lucy/` product code in this card.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
      -> 13 passed
- [x] `test -d .cursor/agents && test -d .cursor/rules && test -d .cursor/skills`
      -> directories exist with required files
- [x] `test -f docs/cursor-agents-adapter.md && test -f docs/cursor-rules-map.md`
      -> adapter docs present

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do NOT
check boxes, do NOT force tests green. Leave the card in `in_progress/`,
document what happened under "Improvements noted", and report. Mark un-run
commands `BLOCKED`.

## Decision log

- **D1 — Cursor-native models only**
  - Decision: Route Cursor subagents with `composer-2.5-fast`,
    `cursor-grok-4.5-high-fast`, and `inherit` only.
  - Trigger: trade-off
  - Why: User required Cursor-owned models; avoid translating Claude/Codex ids.
  - Alternatives rejected: reuse Codex gpt-5.6 table — cross-adapter confusion
  - Spec impact: none

- **D2 — No auto-land to main**
  - Decision: Strip auto-merge from closing-commit workflow; label human gate honor-system.
  - Trigger: contradiction
  - Why: Conflicts with Lucy commit handoff; landing.md cost diagnosis kept as prose.
  - Alternatives rejected: full landing.md import — violates AGENTS.md push policy
  - Spec impact: none

## Improvements noted

- Follow-up: add Cursor hooks or a `backlog-verify` script so start/closing
  commits become machinery instead of honor-system.
- Follow-up: optional mutation-testing Docker target for T3 cards.
- Follow-up: sync `agents-specs/projects/lucy/` mirror with live Review evidence
  + Risk tier template (currently divergent).

## Review evidence

- code-reviewer: PASS - scope matches Spec; no src/lucy product changes; Cursor adapter omits foreign model ids; Decision log D1/D2 present
- test-auditor: PASS - `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q` -> 13 passed; ENFORCE_TIER_AND_LOG_FROM=110; no mocks added
- docs-reviewer: PASS - docs/agents/workflows, cursor-agents-adapter, cursor-rules-map, agent_index, AGENTS.md Cursor section aligned with on-disk .cursor files
- simplicity-reviewer: PASS - eight thin alwaysApply rules + agent prompt copies; no new product abstractions
- security-reviewer: NOT_APPLICABLE - no telemetry, secrets, MCP permissions, or public SDK surface changed; control-system docs/adapters only
- final-integrator: PASS - T2 entry conditions met (risk tier, decision log, red-to-green contract suite); P0/P1 none; security N/A justified; ready for closing commit on main (human requested)

Findings disposition:

- None


## Closing commit
- Hash: `c2e951b` — Wire Cursor agents and harden Spec/TDD contracts (card 110).
- Branch: `main` · Files: 33 · Date: 2026-07-27
- Landed on main: `c2e951b` · 2026-07-27


## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
