# Cursor agents adapter (Lucy)

How Cursor loads Lucy's operating contract and review gate. Canon remains
`AGENTS.md` and `backlog/agent_index.md`. This adapter must not weaken them.

## Layout

| Path | Role |
| --- | --- |
| `.cursor/rules/*.mdc` | alwaysApply session rules (backlog, Docker, TDD, DoD, invariants, tiers, planning, anti-vibecoding) |
| `.cursor/agents/*.md` | Specialist subagents (reviewers, card-writer, implementation, final-integrator) |
| `.cursor/skills/*/SKILL.md` | Invocable workflows: `chip-tdd`, `final-review`, `closing-commit` |
| `docs/agents/workflows/` | Shared Spec/TDD contracts (risk tiers, final review, decision log, closing commit) |

## Model routing (Cursor-native only)

Do **not** translate Anthropic-adapter or Codex-adapter model identifiers into
this adapter.

| Agent | Model | Modality |
| --- | --- | --- |
| `docs-reviewer` | `composer-2.5-fast` | fast |
| `card-writer` | `composer-2.5-fast` | fast |
| `simplicity-reviewer` | `composer-2.5-fast` | fast (escalate to grok on ABI/plane) |
| `implementation-agent` | `cursor-grok-4.5-high-fast` | high |
| `code-reviewer` | `cursor-grok-4.5-high-fast` | high (T3 → `inherit`) |
| `test-auditor` | `cursor-grok-4.5-high-fast` | high |
| `security-reviewer` | `cursor-grok-4.5-high-fast` | high (T3 → `inherit`) |
| `final-integrator` | `inherit` | session |

Escalate: `composer-2.5-fast` → `cursor-grok-4.5-high-fast` → `inherit`.
Save cost by risk tier (T0 skips agents), not by weak judges.

## Invocation

- Manual: `Use the code-reviewer subagent to review card 110`.
- Gate: skill `final-review` before `in_progress → done` for cards >= 61.
- Chip work: skill `chip-tdd` for one chip at a time.

## Honor-system vs machinery

| Claim | Enforcement |
| --- | --- |
| Risk tier + Decision log on cards >= 110 | `tests/test_backlog_contract.py` (machinery) |
| Review evidence on done cards >= 61 | `tests/test_backlog_contract.py` (machinery) |
| Cursor files present without foreign model ids | `tests/test_backlog_contract.py` (machinery) |
| No auto-land to `main` | honor-system + commit handoff in `AGENTS.md` |
| Start/closing commit discipline | honor-system until a backlog-verify script exists |

## Related

- [cursor-rules-map.md](cursor-rules-map.md)
- [agents/README.md](agents/README.md)
- Claude adapter: `CLAUDE.md` + `.claude/agents/`
- Codex adapter: `AGENTS.md` Codex table + `.codex/agents/`
