# Map AGENTS.md / agent_index → Cursor rules

Keep synchronized when obligations in `AGENTS.md` or `backlog/agent_index.md`
change.

| Source | Cursor rule | alwaysApply |
| --- | --- | --- |
| Backlog read-before-work, auto-carding, states | `00-backlog-session.mdc` | true |
| Docker Compose runtime | `01-docker-only.mdc` | true |
| Spec → TDD → chips | `02-tdd-chips.mdc` | true |
| Definition of Done + BLOCKED | `03-definition-of-done.mdc` | true |
| Plane / open-core / no-mocks / typed config / telemetry | `04-lucy-invariants.mdc` | true |
| Risk tiers T0–T3 + Cursor model routing | `05-risk-tiers.mdc` | true |
| Constraints before code / stop-and-ask | `06-planning-gate.mdc` | true |
| Think / Surgical / Goal-driven | `07-anti-vibecoding.mdc` | true |

## Hierarchy

1. `AGENTS.md` — source of truth.
2. `backlog/agent_index.md` — backlog process.
3. `docs/agents/workflows/*` — detailed contracts.
4. `.cursor/rules/*.mdc` — what Cursor injects every session.
5. `.cursor/agents/*` — specialist prompts (not always-on context).

On conflict, `AGENTS.md` wins over Cursor rules.
