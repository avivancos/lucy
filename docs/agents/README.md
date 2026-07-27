# Lucy agent contracts

Canonical agent workflows adapted from `agents-specs` for this repository.
`AGENTS.md` and `backlog/agent_index.md` remain the operating source of truth;
these files are the detailed contracts those documents point at.

## Workflows

| File | Purpose |
| --- | --- |
| [workflows/risk-tiers.md](workflows/risk-tiers.md) | T0–T3 classification, Lucy T3 path signals, escalate-on-doubt |
| [workflows/final-review.md](workflows/final-review.md) | Entry conditions, reviewer set by tier, BLOCKED verification |
| [workflows/implementation-log.md](workflows/implementation-log.md) | `## Decision log` format and empty-log rule |
| [workflows/closing-commit.md](workflows/closing-commit.md) | Start commit, closing commit, stamp; no auto-land to main |

## Adapters

| Tool | Location |
| --- | --- |
| Cursor | `.cursor/agents/`, `.cursor/rules/`, `.cursor/skills/` — see `docs/cursor-agents-adapter.md` |
| Claude Code | `.claude/agents/`, `CLAUDE.md` |
| Codex | `.codex/agents/`, model table in `AGENTS.md` |

Adapters must not weaken these contracts. Cursor uses only Cursor-native
models (`composer-2.5-fast`, `cursor-grok-4.5-high-fast`, `inherit`).
