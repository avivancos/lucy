# Risk tiers

## Purpose

Constraint level follows risk. Review depth and model strength scale with the
blast radius of a change, so routine work stays cheap and dangerous work gets
the full gauntlet.

The tier is assigned when the card is created, before any code exists, and is
consumed by the card template, the [final review workflow](final-review.md),
and each tool adapter's model policy (`AGENTS.md` Cursor / Claude / Codex
sections).

## The tiers

| Tier | Name | Typical changes | Reviewer agents | Verification gate |
|------|------|-----------------|-----------------|-------------------|
| **T0** | Text-only | Docs, copy, comments, non-behavioral config text | None | Deterministic only: format/lint, docs validation |
| **T1** | Routine | Small bugfix with regression test, isolated refactor | `code-reviewer` | Targeted tests, lint, type check |
| **T2** | Standard (default) | Any behavior-changing feature or multi-file change | All five + `final-integrator` | Full final-review gate |
| **T3** | Critical | Auth, secrets, PII, plane boundary, public ABI, irreversible side effects | All five + `final-integrator` (strongest models) | Full gate, full suite, mandatory scoped mutation testing or `BLOCKED` |

Lucy reviewer names: `docs-reviewer`, `test-auditor`, `code-reviewer`,
`simplicity-reviewer`, `security-reviewer`. Cursor model routing for these
roles lives in `AGENTS.md` (Cursor adapter) — never copy Claude or Codex
model identifiers into the Cursor adapter.

## Lucy T3 path signals

Any single match classifies the card as **T3**:

- `**/media-gateway/**`, Rust sidecar / media plane
- Control-channel schema / WebSocket events and directives
- Telemetry wire / PII redaction / audio suppression
- Secrets or `LUCY_*` credential handling
- MCP permissions / authz
- Voice provider Protocols / spec models (frozen public ABI)
- Plane boundary (audio frames crossing into Python)

## Classification rules

Apply in precedence order:

1. **T3 triggers** — any Lucy path signal above, or auth/session/token,
   authorization, personal-data export/retention, destructive migration,
   irreversible external side effects (calls, SMS, third-party writes),
   secrets, AI prompt/tool surface, CI/deploy permissions.
2. **Path signals** — enforce with grep/hooks over the diff file list when
   machinery exists; otherwise honor-system against this list.
3. **T0 requires exclusivity.** T0 applies only when the diff is prose, copy,
   comment, or non-behavioral text. **Contract and policy files are never
   T0**: `AGENTS.md`, `CLAUDE.md`, `.cursor/`, `.claude/agents/`,
   `.codex/agents/`, backlog rules, hooks, and CI — at least T1, and T2 if
   the change weakens a gate.
4. **Default is T2** for any behavior-changing task without a T3 trigger.
5. **When in doubt, escalate one tier.** Never argue a change down on
   ambiguity.

## Who classifies

- The **implementer proposes** the tier at card creation:
  `**Risk tier:** T<n> — <one-line justification>`.
- **Any reviewer or orchestrator may escalate** at any time; the review
  restarts at the new tier's reviewer set.
- **De-escalation requires explicit human sign-off** in the decision log.
- Every tier change is a decision-log entry with trigger `tier-change`
  (see [implementation-log.md](implementation-log.md)).

## Cost policy

- **T0 spends zero model budget on review** — linters, builds, contract
  tests, and greps are the reviewers.
- **Save cost by tiering**, not by degrading judges. Mechanical checks belong
  to deterministic tools. A verdict never comes from the cheapest available
  model when the verdict can ship a bug.
- Tool-specific model tables (Cursor / Claude / Codex) must not be translated
  across adapters.

## Closure by tier

- **T0** may close on green deterministic gates alone.
- **T1 and T2** follow [final-review.md](final-review.md).
- **T3 defaults toward `need_human_testing/`** when final proof needs real
  credentials, irreversible calls, or human-only evidence. Mutation testing
  at T3 is mandatory: run it or return `BLOCKED` — never invent numbers.
