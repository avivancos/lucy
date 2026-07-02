# CLAUDE.md - Claude Code loader for lucy

This file wires the operating contract into Claude Code. It adds no rules of
its own: `agents.md` and `backlog/agent_index.md` remain the source of truth.

@agents.md
@backlog/agent_index.md

## Reviewer subagents (review gate, card 61)

Six subagents live in `.claude/agents/`. The review gate itself (when it
applies, what blocks, how to record evidence) is defined in
`backlog/agent_index.md` - this table only maps agents to triggers and models:

| Agent | Run when | Model |
| --- | --- | --- |
| `code-reviewer` | always (any code change) | sonnet |
| `test-auditor` | always (any code change) | sonnet |
| `simplicity-reviewer` | always (any code change) | sonnet |
| `docs-reviewer` | docs/ADRs/README touched, or code drifted from docs | haiku |
| `security-reviewer` | telemetry, secrets, MCP permissions, public surface | inherit |
| `card-writer` | writing or upgrading backlog cards | sonnet |

Model policy (token economy - tier by consequence-of-error): haiku for
mechanical cross-referencing, sonnet for scoped judgment, `inherit` (the
session model) only where a wrong verdict ships a bug. Implementation work on
chips defaults to sonnet; adversarial verification and final verdicts stay on
the session model. The dev-agent dashboard (`/config/agents/{role}`) holds the
same table for orchestrated runs; this file documents it for interactive ones.

## Orchestrator (dev-agent MCP)

State moves and review routing through the orchestrator (and the git-mv
fallback) are defined in `backlog/agent_index.md`. Additionally, after closing
a card, push metrics with `dashboard_report` (card id, tests passed, tokens).
The backlog folders remain the source of truth; the orchestrator is transport,
not state.
