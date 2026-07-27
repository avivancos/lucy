# agents.md - Lucy Operating Guide

This file is the source of truth for project operating rules.

## Project Placeholders

| Key | Value |
| --- | --- |
| Project name | Lucy |
| Project path | `/Users/agustin/Desarrollo/lucy` |
| Code root | `src/lucy/` |
| Tests root | `tests/` |
| Docs root | `docs/` |
| Runtime | Docker Compose |
| Dev command | `docker compose up --build` |
| Test command | `docker compose run --rm lucy-api pytest` |
| UI audit tool | Browser / Playwright |
| Project backlog | `backlog/` |
| Document language | English |
| Default branch | `main` |

## Rules

- Use English for documentation, code comments, backlog cards, commits, and PRs.
- Treat `backlog/agent_index.md` as the canonical backlog process.
- Formal tasks follow Spec -> red test -> green code -> refactor -> docs -> move.
- Use Docker Compose as the sanctioned runtime for tests, services, and audits.
- Lucy is a no-mocks project. Tests exercise real local implementations,
  deterministic in-process simulators, local protocol servers, or recorded
  fixtures from real interactions. Do not use mocking frameworks or invented
  provider behavior to make tests pass.
- Do not hardcode provider names, model names, URLs, secrets, thresholds, regions,
  currencies, quotas, or latency budgets outside typed settings, registries, or
  named constants.
- Stage only files related to the current task when committing.
- For UI work, run a visual audit before closing the task.
- For API/runtime work, smoke-test the affected endpoint or CLI before closing.

## Invariants

These hold across every card and PR. They consolidate decisions already made in the
ADRs; the ADR is the source of each rule and this list is the quick reference.

- Plane boundary (ADR 0004, ADR 0011): audio frames never cross into Python. The media
  plane (Rust sidecar) owns the socket and forks audio directly to STT/TTS providers;
  the control channel is a versioned WebSocket carrying events and directives only.
- Open-core boundary (ADR 0010): anything that runs inside the user's process is open
  (the `src/lucy/` SDK); anything that stores, aggregates, or compares across runs or
  tenants is closed (the platform). The SDK never imports platform or Pili code. The
  voice provider Protocols and spec models are the frozen public ABI; changing them is
  a SemVer-breaking change.
- Telemetry stays client-side-safe (ADR 0010, `docs/telemetry-wire-v1.md`): PII
  redaction, audio suppression, and sampling are enforced in open code before anything
  leaves the process. The telemetry wire is a normative spec; do not add fields ad hoc.
- Typed config, never inline: latency budgets, provider and model names, URLs,
  thresholds, regions, currencies, and quotas live in typed settings, registries, or
  named constants (restates the no-hardcode rule above as a hard invariant; ADR 0011).
- No-mocks plane (ADR 0003): test doubles are deterministic simulators implementing the
  same control-channel schema, local protocol servers, or recorded fixtures, never a
  mocking framework.

## Definition of Done

A global hard gate that complements the per-card Definition of Done in
`backlog/_TEMPLATE.md`. A change is done only when all of these pass:

- Spec -> red test -> green code -> refactor complete; the suite is green via
  `docker compose run --rm lucy-api pytest`.
- `docker compose run --rm lucy-api ruff check src tests`,
  `docker compose run --rm lucy-api ruff format --check src tests`, and
  `docker compose run --rm lucy-api mypy src` are clean.
- The app boots from a clean state: `docker compose up --build` from an empty database,
  never relying on committed state.
- Audit done: UI work has had a visual audit; API or runtime work has been smoke-tested
  on the affected endpoint or CLI.
- Card bookkeeping done: "Improvements noted" filled, follow-up cards raised for anything
  noticed, and the card moved to its next state folder.

## Testing contract

Lucy already practices this; stating it here makes it enforceable.

- TDD: red (a failing behavioral test) -> green (minimal implementation) -> refactor.
- No mocks (ADR 0003): simulators, local protocol servers, or recorded fixtures only.
- Inject the clock (ManualClock, the injectable clock from ADR 0011); no wall-time
  sleeps. Tests are deterministic and paced.
- Cover error branches (timeouts, cancellation, MCP permission, schema, and timeout
  failures), not just the happy path.
- Every feature ships at least one negative test a naive implementation would fail, such
  as barge-in mid-tool, a breached latency budget, or a speculation revision-abort.

## Constraints before code

Before implementing anything, the agent must be able to state:

1. **Intended product behavior** — the approved backlog card Spec (and ADRs it
   cites).
2. **Engineering rules** — this file plus `backlog/agent_index.md`.
3. **Prior decisions** — ADRs under `docs/adr/` / `backlog/decisions/`.
4. **Risk tier** — `T0`–`T3` with a one-line justification
   (`docs/agents/workflows/risk-tiers.md`).

If any of the four is missing or ambiguous: **stop and ask — do not infer.**

## Auto-carding

If a prompt requests new implementation work not covered by an existing card,
first create a card from `backlog/_TEMPLATE.md`, map it in `backlog/sprints.md`,
then proceed. Search all state folders first — never two open cards for the
same work. Questions, explorations, and reviews are not carded. The only
override is the user literally saying "ignore the backlog".

## Risk tier and decision log

Every card declares `**Risk tier:** T0–T3` with justification. Lucy T3 path
signals (any one suffices): `**/media-gateway/**` / Rust media plane,
control-channel schema, telemetry/PII/audio suppression, secrets / `LUCY_*`
credentials, MCP permissions, voice provider Protocols / public ABI, plane
boundary (audio into Python). Default for behavior-changing work is **T2**.
Contract and policy files are never T0. Details:
`docs/agents/workflows/risk-tiers.md`.

Every card maintains `## Decision log` during execution per
`docs/agents/workflows/implementation-log.md`. An empty log requires the
explicit line `No decisions: implementation followed the spec exactly.`

## Verification and BLOCKED

A command that was not executed must be marked `BLOCKED`, never assumed green.
Every "verified" claim names the command and observed result. At T3, scoped
mutation testing is mandatory or `BLOCKED` — never invent numbers. Final review
entry conditions: `docs/agents/workflows/final-review.md`.

## No fictional machinery

A document may claim an action "blocks" only if a named mechanism enforces it
(contract test, CI job, hook). Honor-system rules must say "honor-system".

## Git-visible card lifecycle

Moving a card to `in_progress/` gets a **start commit** of the card alone.
Moving to `done/` gets a **closing commit** (explicit paths only) plus a
`## Closing commit` stamp. Agents do **not** auto-merge or push to `main` —
that remains a human gate (honor-system). Contract:
`docs/agents/workflows/closing-commit.md`.

## Security and commit handoff

- No secrets in images, logs, or git. Only `.env.example` is committed; `.env` is
  gitignored. Configuration is environment-driven through typed `LUCY_*` settings.
- PII redaction and audio suppression happen client-side before telemetry leaves the
  process (see Invariants).
- The pre-publication scrub (card 46, sprint S8) gates anything going public: license
  headers, sensitive data, and internal URLs are reviewed before publication.
- Commit handoff: stage only the files for the current task, propose a commit message,
  and let the human approve and author the commit.

## Cursor subagent model routing

Project-scoped Cursor subagents live in `.cursor/agents/`. Rules live in
`.cursor/rules/`. Skills live in `.cursor/skills/`. Use **only** Cursor-native
models — never Anthropic-adapter or Codex-adapter model identifiers in this
adapter:

| Agent | Model | Modality | Run when |
| --- | --- | --- | --- |
| `docs-reviewer` | `composer-2.5-fast` | fast | Docs/ADRs touched or could go stale |
| `card-writer` | `composer-2.5-fast` | fast | Writing or upgrading backlog cards |
| `simplicity-reviewer` | `composer-2.5-fast` | fast | Any code change (T2+); escalate to grok on ABI/plane abstractions |
| `implementation-agent` | `cursor-grok-4.5-high-fast` | high | One bounded chip with TDD |
| `code-reviewer` | `cursor-grok-4.5-high-fast` | high | T1+ code changes; T3 may escalate to `inherit` |
| `test-auditor` | `cursor-grok-4.5-high-fast` | high | T2+ code or test changes |
| `security-reviewer` | `cursor-grok-4.5-high-fast` | high | Security trigger or T2+; T3 → `inherit` |
| `final-integrator` | `inherit` | session | Consolidating evidence before a card closes |

Escalate `composer-2.5-fast` → `cursor-grok-4.5-high-fast` → `inherit`. Save
cost by risk tier (T0 skips agents), not by weakening judges. Conditional
reviewers without a trigger record `NOT_APPLICABLE` — do not spawn them only
to fabricate PASS. See `docs/cursor-agents-adapter.md`.

## Codex subagent model routing

Project-scoped Codex subagents live in `.codex/agents/`. Pin each role to the
least expensive model and reasoning effort that can safely reach its verdict:

| Agent | Model | Reasoning effort | Run when |
| --- | --- | --- | --- |
| `card-writer` | `gpt-5.6-luna` | `medium` | Writing or upgrading backlog cards |
| `docs-reviewer` | `gpt-5.4-nano` | `low` | Documentation changed or could be stale |
| `simplicity-reviewer` | `gpt-5.6-luna` | `medium` | Any code change |
| `test-auditor` | `gpt-5.6-terra` | `high` | Any code or test change |
| `code-reviewer` | `gpt-5.6-terra` | `high` | Any code change |
| `security-reviewer` | `gpt-5.6-sol` | `high` | A security trigger in the review gate applies |
| `implementation-agent` | `gpt-5.6-terra` | `medium` | Executing one bounded card chip with TDD |
| `final-integrator` | `gpt-5.6-sol` | `high` | Consolidating evidence before a card closes |

`max` is never a default. Escalate only when the current agent cannot resolve a
concrete risk from repository evidence: Nano to Luna for non-mechanical judgment,
Luna to Terra for ambiguous behavior, and Terra to Sol for a possible P0/P1,
public ABI change, concurrency hazard, security boundary, or plane-boundary issue.
Use `gpt-5.6-sol` with `max` only when the same concrete risk remains unresolved
after a `high`-effort review. Do not run conditional reviewers merely to obtain a
PASS; record the gate's required `NOT_APPLICABLE` disposition instead.
