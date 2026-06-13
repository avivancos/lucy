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
