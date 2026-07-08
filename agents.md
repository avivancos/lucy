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

## Security and commit handoff

- No secrets in images, logs, or git. Only `.env.example` is committed; `.env` is
  gitignored. Configuration is environment-driven through typed `LUCY_*` settings.
- PII redaction and audio suppression happen client-side before telemetry leaves the
  process (see Invariants).
- The pre-publication scrub (card 46, sprint S8) gates anything going public: license
  headers, sensitive data, and internal URLs are reviewed before publication.
- Commit handoff: stage only the files for the current task, propose a commit message,
  and let the human approve and author the commit.
