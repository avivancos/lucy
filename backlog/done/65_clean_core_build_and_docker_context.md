# 65 - Keep the core API build lean

**Sprint:** S10 - Agent operations
**Epic:** Agent operations
**Estimated effort:** ~1 h
**Depends on:** 63
**State:** done

## Goal

Keep the sanctioned Docker runtime fast enough for every MVP card to use. The
core `lucy-api` image must not copy local caches, virtualenvs, git history, or
generated build artifacts into the Docker context, and the default install must
stay free of optional heavyweight provider/research stacks.

## Context primer

- `Dockerfile.api` - builds the `lucy-api` image every card uses for gates.
- `pyproject.toml` - declares core dependencies and optional extras.
- `tests/test_infrastructure.py` - infrastructure contract tests.
- `backlog/done/63_lint_and_typecheck_in_docker.md` - the prior card that made
  Docker lint/type gates real and exposed the heavy default dependency issue.
- `agents.md` - Docker Compose is the sanctioned runtime.

## Spec

- Add a committed `.dockerignore` that excludes local caches, virtualenvs,
  git metadata, Python build outputs, frontend artifacts, Rust `target/`, and
  local env files from Docker build contexts.
- Preserve source, tests, docs, backlog, examples, infra, and runtime manifests
  in the build context unless a Dockerfile explicitly chooses not to copy them.
- Keep heavyweight optional stacks outside the default `dependencies` list.
  `headroom-ai[all]` remains available only through an optional extra.
- Prove `docker compose build lucy-api` succeeds from the current tree.

## Files to create/modify

- `.dockerignore` - committed build-context exclusions.
- `tests/test_infrastructure.py` - contract coverage for build-context hygiene.
- `backlog/sprints.md` - maps this card to S10.
- This card file.

## Chips

- [x] **C1 - Docker context contract.** Add `.dockerignore` and a test that
  asserts it excludes `.git`, `.venv`, cache folders, build outputs,
  `node_modules`, `.next`, `target`, and `.env`, while not excluding `src`,
  `tests`, `docs`, `backlog`, `examples`, `infra`, or `media-gateway-rust`.
  Files: `.dockerignore`, `tests/test_infrastructure.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> all pass.
- [x] **C2 - clean API build smoke.** Rebuild only the API image and verify the
  quickstart still runs on the rebuilt image. Files: no code changes unless the
  smoke exposes an issue. Verify: `docker compose build lucy-api` -> success,
  then `docker compose run --rm lucy-api python examples/quickstart_voice_agent.py`
  -> prints a local transcript, response, and telemetry.
- [x] **C3 - full gates and bookkeeping.** Run the full Docker test/lint/type
  gates, fill review evidence, and move this card to `done/`. Verify:
  `docker compose run --rm lucy-api pytest -q` -> all pass.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003).
- Do not move provider packages or implement plugin mechanics; card 28 owns
  plugin workspace structure.
- Do not hide required project files from Docker builds.
- Do not reintroduce heavyweight optional dependencies into the core install.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> all pass
- [x] `docker compose build lucy-api` -> success without pulling Torch/CUDA
  from the default core dependency set
- [x] `docker compose run --rm lucy-api python examples/quickstart_voice_agent.py`
  -> offline quickstart succeeds
- [x] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api mypy src` -> exit 0
- [x] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, the Docker build regresses, or the ignore file hides a required
project file: do NOT check boxes, do NOT move to `done/`, and report the exact
command/output under "Improvements noted".

## Improvements noted

- None. The API image rebuilt successfully with a 29.81kB Docker context and
  without pulling Torch/CUDA from the default dependency set.

## Review evidence

- code-reviewer: PASS - local fallback review; `.dockerignore` only excludes
  generated/local artifacts and the contract test protects required project
  sources from accidental exclusion.
- test-auditor: PASS - local fallback review; targeted infrastructure test,
  quickstart smoke, lint, format, type, full pytest, and backlog contract were
  all executed in Docker.
- docs-reviewer: PASS - local fallback review; `backlog/sprints.md` and this
  card record the new S10 hygiene task and completion evidence.
- simplicity-reviewer: PASS - local fallback review; implementation is a small
  committed `.dockerignore` plus one infrastructure contract test.
- security-reviewer: PASS - local fallback review; `.env` is excluded from the
  Docker context and no runtime secrets/config behavior changed.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
