# 63 - Make ruff and mypy runnable in the Docker image

**Sprint:** S10 - Agent operations
**Epic:** Agent operations
**Estimated effort:** ~2 h
**Depends on:** none
**State:** pending

## Goal

The agents.md global Definition of Done requires `ruff check`, `ruff format
--check`, and `mypy` to be clean, but none of them are installed in the
`lucy-api` image, so no card's lint/type gate can actually be verified in the
sanctioned Docker runtime. Install and wire them so the gate is real.

## Context primer

Read, in order, before writing anything:

- `agents.md` - the "Definition of Done" and "Security and commit handoff"
  sections that name `ruff`/`mypy` as hard gates.
- `pyproject.toml` - the `[project.optional-dependencies] dev` group and any
  `[tool.ruff]`/`[tool.mypy]` config (add config if absent).
- `Dockerfile.api` - the image the test/lint commands run in (`docker compose
  run --rm lucy-api ...`); the dev extras may not be installed there.
- `docker-compose.yml` - the `lucy-api` service definition.

## Spec

- `ruff` and `mypy` are importable and runnable inside the `lucy-api` image:
  `docker compose run --rm lucy-api ruff check src tests` and
  `docker compose run --rm lucy-api mypy src` both execute (exit 0 or with real
  findings, not "command not found").
- Add `ruff` and `mypy` to the `dev` optional-dependencies in `pyproject.toml`
  (pinned with `>=`), and ensure the image installs the dev extras (or a
  dedicated lint layer).
- Add minimal `[tool.ruff]` and `[tool.mypy]` config to `pyproject.toml` if not
  present: line length, target version, and an initial rule/strictness level
  the current tree already satisfies (do not mass-refactor to satisfy new
  rules in this card - pick a baseline that is green now).
- The three commands are documented in `agents.md`'s Definition of Done as the
  exact Docker invocations to run.

## Files to create/modify

- `pyproject.toml` - dev deps + tool config
- `Dockerfile.api` - ensure dev extras / lint tools are installed
- `agents.md` - record the exact `docker compose run` lint/type commands

## Chips

- [ ] **C1 - deps + config.** Add `ruff`/`mypy` to `[project.optional-
  dependencies].dev` and a baseline `[tool.ruff]`/`[tool.mypy]` in
  `pyproject.toml`. Files: `pyproject.toml`. Test first: a new
  `tests/test_toolchain.py::test_pyproject_declares_lint_and_type_tools` reads
  `pyproject.toml` and asserts `ruff` and `mypy` are in the dev extras. Verify:
  `docker compose run --rm lucy-api pytest tests/test_toolchain.py -q` -> pass.
- [ ] **C2 - image installs the tools.** Update `Dockerfile.api` so the tools
  land in the image; rebuild. Files: `Dockerfile.api`. Verify:
  `docker compose run --rm lucy-api ruff --version` and
  `docker compose run --rm lucy-api mypy --version` -> both print a version.
- [ ] **C3 - baseline green + docs.** Run `ruff check src tests`,
  `ruff format --check`, and `mypy src` in Docker; fix only what is trivially
  needed to reach a green baseline (or set the config level so it is green
  now). Record the commands in `agents.md`. Files: `agents.md`, plus any
  trivial lint fixes. Verify:
  `docker compose run --rm lucy-api ruff check src tests` -> exit 0.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); `test_toolchain.py` reads
  the real `pyproject.toml`.
- Do not hardcode tool versions inline anywhere except `pyproject.toml`'s
  declared dependency specifiers.
- Do not mass-refactor the codebase to satisfy an aggressive new lint ruleset;
  choose a baseline the current tree passes and tighten in later cards.
- Do not add a second, non-Docker lint path that diverges from the sanctioned
  runtime.

## Definition of Done

- [ ] `docker compose run --rm lucy-api ruff --version` -> prints a version
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0 (or only pre-agreed
      baseline ignores)
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite still green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do NOT
check boxes, do NOT force tests green. Leave the card in `in_progress/`,
document what happened under "Improvements noted", and report. Partial honest
work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before done/ (review gate, card 61). -->
