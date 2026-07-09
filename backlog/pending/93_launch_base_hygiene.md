# 93 - Clean launch-base runtime shims

**Sprint:** S8 - Launch
**Epic:** Launch hygiene
**Estimated effort:** ~5 h
**Depends on:** 23, 65
**State:** pending

## Goal

Remove stale launch blockers before the public ABI freezes: Compose and worker
entrypoints should use `lucy.serve`, deprecated API shims should be retired, and
tracked operating files should be intentional.

## Context primer

- `agents.md` - launch Definition of Done and commit handoff rules.
- `docs/adr/0010-open-core-split.md` - framework serving stays in `lucy.serve`.
- `docker-compose.yml` - currently owns runtime entrypoints.
- `Dockerfile.api` - public image entrypoint must avoid deprecated shims.
- `src/lucy/api/app.py` and `src/lucy/worker.py` - deprecated compatibility surfaces to inspect.

## Spec

Switch Docker Compose and API image entrypoints from `lucy.api.app` to
`lucy.serve.app:create_app`, and from `lucy.worker` to `lucy.serve.worker` where
the landed runtime supports it. Retire deprecated shim modules only after tests
prove no public quickstart imports them.

Resolve `.codex/` tracking with the user at execution time: either commit the
needed app metadata like `.claude/`, or add a gitignore rule. Sweep older done
cards whose `**State:**` header disagrees with their folder, without changing
their review evidence.

## Files to create/modify

- `docker-compose.yml` - serve app entrypoint.
- `Dockerfile.api` - image command/import path.
- `src/lucy/api/app.py` and `src/lucy/worker.py` - retire or reduce shims.
- `.gitignore` or `.codex/` - one intentional decision.
- `backlog/done/*.md` - state header consistency only if drift exists.

## Chips

- [ ] **C1 - Runtime entrypoints.** Write or update tests that assert Compose and Dockerfile reference `lucy.serve`, then switch stale imports. Files: `docker-compose.yml`, `Dockerfile.api`, `tests/test_infrastructure.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q` -> infrastructure tests pass.
- [ ] **C2 - Shim retirement.** Add import/quickstart compatibility tests, then retire deprecated API and worker shims where safe. Files: `src/lucy/api/app.py`, `src/lucy/worker.py`, `tests/test_api.py`. Verify: `docker compose run --rm lucy-api pytest tests/test_api.py -q` -> API tests pass.
- [ ] **C3 - Hygiene gates.** Resolve `.codex/` tracking, sweep card state metadata if needed, run clean Docker build and full gates, then move the card. Files: `.gitignore`, `backlog/done/*.md`. Verify: `docker compose up --build` -> app boots from clean state.

## Do NOT

- Do not use mocks or mocking frameworks; smoke the real Docker/serve entrypoints.
- Do not hardcode ports, hosts, URLs, or runtime commands outside Compose, Dockerfile, or typed settings.
- Do not remove public imports until README and tests prove they are not part of the launch API.
- Do not edit unrelated backlog content while sweeping state fields.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_infrastructure.py tests/test_api.py -q` -> targeted tests pass
- [ ] `docker compose up --build` -> API boots from a clean state
- [ ] `docker compose run --rm lucy-api pytest` -> full suite green
- [ ] Docker ruff, format check, and mypy gates are clean
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a deprecated shim is still required by public docs or packaging, keep it and
record the reason under Improvements noted rather than breaking compatibility.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->
