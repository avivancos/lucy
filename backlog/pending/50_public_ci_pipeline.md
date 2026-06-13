# 50 - Add public CI pipeline on GitHub Actions

**Sprint:** S8 - Launch
**Epic:** Release engineering
**Estimated effort:** ~6 h
**Depends on:** 28
**State:** pending

## Goal

Every push and pull request to the public repo proves the launch claim -
installable, keyless, offline-testable - through five GitHub Actions jobs:
lint, a Python 3.10-3.12 no-mocks test matrix with network egress blocked,
the backlog contract, buildable dists for every workspace package, and
Compose config validity. A fresh fork goes green with zero secrets.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  versions or URLs outside declared registries).
- `docs/adr/0003-no-mocks-testing-policy.md` - the default suite is offline
  by design; paid/network providers live in explicit manual profiles, never
  in CI. This is why the pipeline needs no API keys.
- `docs/adr/0010-open-core-split.md` - the open/closed boundary. CI here
  serves the open SDK repo only; dashboard and platform pieces move private
  and get their own pipelines. Note `backlog/` also moves private at the
  pre-publication scrub.
- `pyproject.toml` - single source for dependencies. The `dev` extra gains
  the CI toolchain here; `[tool.uv.workspace]` (added by card 28) is what
  the build job enumerates. There is NO `[tool.mypy]` table and no
  `mypy.ini`/`.mypy.ini`/`setup.cfg` in the repo (verified at card-writing
  time) - see Spec for what that means.
- `tests/test_package_metadata.py` - house style for pyproject contract
  tests (`tomli.loads`); it also pins `requires-python == ">=3.9"`, which
  this card must not change.
- `tests/test_infrastructure.py` - house style for YAML contract tests
  (`yaml.safe_load` over a checked-in file); copy this pattern for
  `tests/test_ci_pipeline.py`.
- `tests/test_api.py` - in-process `TestClient` style; evidence the suite
  needs no network. The tripwire in this card turns that design claim into
  an enforced invariant.
- `tests/test_backlog_contract.py` - the test job (3) runs; it validates
  card structure and the sprint index.
- `docker-compose.yml` and `.env.example` - job (5) parses the Compose
  file; `env_file: .env.example` means the example file must stay committed
  or `docker compose config` fails.
- `README.md` - the badge lands directly under the `# Lucy` heading.
- `backlog/pending/28_add_plugin_mechanism_and_workspace.md` - dependency:
  defines the uv workspace (`packages/*`) and the fixture plugin that the
  suite needs installed; the CI test job mirrors what `Dockerfile.api`
  does locally.
- `backlog/sprints.md` - S8 exit demo this card serves: public repo
  installable from scratch on a clean machine.

## Spec

### CI toolchain pins (`pyproject.toml`)

- Add to `[project.optional-dependencies] dev`: `ruff>=0.8` and
  `pytest-socket>=0.7`. Versions live ONLY here; the workflow installs
  `-e ".[dev]"` and never repeats a version pin.
- Add a `[tool.ruff]` table with `src = ["src", "tests"]`. Keep the
  default lint rule set. Do NOT set `target-version`; ruff derives it from
  `requires-python` and `test_package_metadata.py` pins that at `>=3.9`.
- mypy: verified absent on 2026-06-11 (`grep -n "tool.mypy"
  pyproject.toml` -> nothing; no `mypy.ini`, `.mypy.ini`, or `setup.cfg`
  exists). Re-verify at execution time with exactly:
  `grep -rn "tool.mypy" pyproject.toml mypy.ini .mypy.ini setup.cfg`.
  If still absent: the lint job has NO mypy step and this card does not
  invent a config (note the gap under "Improvements noted" for a follow-up
  typing card). If a config has appeared by S8: add `mypy` to the dev
  extra, append a `python -m mypy src` step to the lint job, and add
  `test_ci_lint_job_runs_mypy_when_config_exists` to the contract tests.

### Network tripwire semantics (ADR 0003 compliance)

- `pytest-socket` is an enforcement tripwire, not a test double: it can
  only turn a cheating test red, never fabricate behavior, so it is
  compatible with the no-mocks policy.
- Canonical invocation (identical locally and in CI):
  `pytest -q --disable-socket --allow-unix-socket
  --allow-hosts=127.0.0.1,::1`.
- Loopback stays allowed because local protocol servers are a sanctioned
  ADR 0003 boundary; everything else is blocked, which mechanically
  asserts "no test requires network".

### Workflow file (`.github/workflows/ci.yml`, new)

Top level:

- `name: CI` (this string renders in the badge).
- Triggers: `push` to branch `main` and `pull_request` targeting `main`.
- `permissions: contents: read`.
- `concurrency: group: ci-${{ github.ref }}, cancel-in-progress: true`.
- Zero `secrets.` references anywhere in the file. No provider key env
  vars. The keyless default suite IS the product claim.
- Action versions used, and only these four actions:
  `actions/checkout@v4`, `actions/setup-python@v5`,
  `astral-sh/setup-uv@v6`. Bump majors only if the runner deprecation
  notice demands it during execution.

Job 1 `lint` (ubuntu-latest):

1. `actions/checkout@v4`.
2. `actions/setup-python@v5` with `python-version: "3.12"`,
   `cache: "pip"`, `cache-dependency-path: pyproject.toml`.
3. `python -m pip install -e ".[dev]"`.
4. `ruff check src tests packages`.
5. `ruff format --check src tests packages`.
6. (Only if the mypy re-verification above found a config:) `python -m
   mypy src`.

Job 2 `tests` (ubuntu-latest):

- `strategy: fail-fast: false, matrix: python-version: ["3.10", "3.11",
  "3.12"]` - the matrix is the ONLY place versions are listed.
- Steps: checkout; setup-python with `${{ matrix.python-version }}`,
  `cache: "pip"`, `cache-dependency-path: pyproject.toml`;
  `python -m pip install -e ".[dev]"`;
  `for pkg in packages/*/; do python -m pip install -e "$pkg"; done`
  (generic loop - card 29 may have added members beyond the fixture
  plugin; never name plugins individually); then the canonical tripwire
  pytest invocation from above.

Job 3 `backlog-contract` (ubuntu-latest):

- Job-level guard `if: hashFiles('tests/test_backlog_contract.py') != ''`
  so the job vanishes gracefully when the pre-publication scrub moves
  `backlog/` private (ADR 0010) - that removal belongs to the scrub card,
  not this one.
- Steps: checkout; setup-python `"3.12"` with the same pip cache config;
  `python -m pip install -e ".[dev]"`;
  `python -m pytest tests/test_backlog_contract.py -q`.

Job 4 `build` (ubuntu-latest):

- Steps: checkout; `astral-sh/setup-uv@v6` with `enable-cache: true`;
  `uv build --all-packages --out-dir dist` (expected artifacts: one sdist
  + one wheel for `lucy` AND for every `packages/*` workspace member; if
  the root dist turns out missing, add a plain `uv build --out-dir dist`
  step rather than reshaping the workspace);
  `uvx twine check --strict dist/*`.
- Build only. No publishing, no PyPI tokens; releasing is launch-card
  scope, not CI scope.

Job 5 `compose-config` (ubuntu-latest):

- Steps: checkout; `docker compose config --quiet` (parses
  `docker-compose.yml` plus the committed `.env.example`; exit 0, no
  output). Never `docker compose up` or `build` in CI.

### Contract tests (`tests/test_ci_pipeline.py`, new)

Parse with `yaml.safe_load` (workflow), `tomli.loads` (pyproject), and
`re` (README), mirroring `tests/test_infrastructure.py` and
`tests/test_package_metadata.py`. PyYAML gotcha: YAML 1.1 parses the
unquoted `on:` key as boolean `True`, so read triggers via
`workflow.get("on", workflow.get(True))`. Exactly these eleven tests:

- `test_dev_extra_declares_ci_toolchain` - `ruff` and `pytest-socket` in
  the dev extra; `[tool.ruff]` table present with `src` configured.
- `test_ci_workflow_triggers_on_main_push_and_pull_request`.
- `test_ci_workflow_defines_all_five_jobs` - job ids exactly `lint`,
  `tests`, `backlog-contract`, `build`, `compose-config`.
- `test_ci_lint_job_runs_ruff_check_and_format` - both commands, in that
  order, over `src tests packages`.
- `test_ci_test_matrix_covers_python_310_to_312` - matrix equals
  `["3.10", "3.11", "3.12"]` and `fail-fast` is false.
- `test_ci_tests_job_blocks_network_egress` - the pytest step contains
  `--disable-socket`, `--allow-unix-socket`, and
  `--allow-hosts=127.0.0.1,::1`.
- `test_ci_workflow_references_no_secrets` - raw file text contains zero
  occurrences of `secrets.`.
- `test_ci_jobs_cache_dependencies` - every `setup-python` step sets
  `cache: "pip"`; the `setup-uv` step sets `enable-cache: true`.
- `test_ci_build_job_builds_workspace_and_twine_checks` - contains
  `uv build --all-packages` and `twine check --strict`.
- `test_ci_compose_job_validates_config` - contains
  `docker compose config --quiet`.
- `test_readme_carries_ci_workflow_badge` - README matches
  `https://github\.com/[\w.-]+/[\w.-]+/actions/workflows/ci\.yml/badge\.svg`
  exactly once (pattern match, NOT an exact slug, so forks stay green).

### README badge (`README.md`)

- Derive `<owner>/<repo>` at execution time from
  `git remote get-url origin` - never guess it (the public name was
  deferred per ADR 0010, so guessing WILL be wrong). No origin remote ->
  failure protocol, do not invent a slug.
- Insert on the line directly under the `# Lucy` heading:
  `[![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/<owner>/<repo>/actions/workflows/ci.yml)`

## Chips

- [ ] **C1 - Pin the CI toolchain.** Test first in
  `tests/test_ci_pipeline.py`: `test_dev_extra_declares_ci_toolchain`
  (red: ruff/pytest-socket absent). Then add `ruff>=0.8` and
  `pytest-socket>=0.7` to the dev extra and the `[tool.ruff]` table in
  `pyproject.toml`; reinstall with `.venv/bin/python -m pip install -e
  ".[dev]"`. Verify: `.venv/bin/python -m pytest
  tests/test_ci_pipeline.py tests/test_package_metadata.py -q` -> all
  pass (metadata test proves the extra still parses).
- [ ] **C2 - Make the tree ruff-clean.** Check first (red):
  `.venv/bin/ruff check src tests packages && .venv/bin/ruff format
  --check src tests packages` -> note every finding. Apply
  `.venv/bin/ruff check --fix` then `.venv/bin/ruff format` over the same
  paths; review the diff - mechanical changes only, zero behavior edits;
  if already clean, record that and move on. Verify: the two check
  commands -> both exit 0, AND `.venv/bin/python -m pytest -q` -> full
  suite green (fixes were behavior-neutral).
- [ ] **C3 - Rehearse the offline tripwire locally.** The tripwire flags
  are the failing test: run `.venv/bin/python -m pytest -q
  --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,::1`. If
  any test trips the socket guard, fix that test at its real local
  boundary (ADR 0003) or stop per the failure protocol - never widen
  allow-hosts. Verify: the command above -> full suite green with the
  network blocked.
- [ ] **C4 - Author lint, tests, and backlog-contract jobs.** Tests first
  in `tests/test_ci_pipeline.py`:
  `test_ci_workflow_triggers_on_main_push_and_pull_request`,
  `test_ci_lint_job_runs_ruff_check_and_format`,
  `test_ci_test_matrix_covers_python_310_to_312`,
  `test_ci_tests_job_blocks_network_egress`,
  `test_ci_workflow_references_no_secrets`,
  `test_ci_jobs_cache_dependencies` (all red - no workflow yet). Run the
  mypy re-verification grep from the Spec and record the result in this
  card. Then create `.github/workflows/ci.yml` with the top-level config
  and jobs 1-3 exactly as specced. Verify: `.venv/bin/python -m pytest
  tests/test_ci_pipeline.py -q` -> only the C5/C6 tests still fail.
- [ ] **C5 - Author build and compose-config jobs.** Tests first:
  `test_ci_workflow_defines_all_five_jobs`,
  `test_ci_build_job_builds_workspace_and_twine_checks`,
  `test_ci_compose_job_validates_config`. Add jobs 4-5 to
  `.github/workflows/ci.yml`, then rehearse both locally:
  `uv build --all-packages --out-dir /tmp/lucy-ci-dist && uvx twine
  check --strict /tmp/lucy-ci-dist/*` and `docker compose config
  --quiet`. Verify: `.venv/bin/python -m pytest
  tests/test_ci_pipeline.py -q` -> all pass except the badge test; both
  rehearsal commands -> exit 0, twine prints PASSED per dist.
- [ ] **C6 - Add the README badge.** Test first:
  `test_readme_carries_ci_workflow_badge` (red). Derive the slug with
  `git remote get-url origin` (no remote -> failure protocol) and insert
  the badge line from the Spec under the `# Lucy` heading in `README.md`.
  Verify: `.venv/bin/python -m pytest tests/test_ci_pipeline.py -q` ->
  all 11 pass.
- [ ] **C7 - Full suite + bookkeeping.** Run everything (including one
  final tripwire run), fill "Improvements noted" (at minimum: the typing
  follow-up if mypy stayed absent), move this card to `done/`. After the
  first push, eyeball the Actions tab once: five jobs listed, all green.
  Verify: `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003). `pytest-socket` is a
  tripwire that can only fail cheating tests, never fake a provider; do
  not use it, monkeypatching, or recorded-fixture edits to force green.
- Do not add provider API keys as GitHub secrets or workflow env vars.
  The default suite is keyless by design; live provider runs stay in
  explicit manual profiles (ADR 0003). Zero `secrets.` in the workflow.
- Do not hardcode tool versions in the workflow (pins live in the
  pyproject dev extra), do not guess the badge slug (derive it from the
  origin remote), and do not repeat matrix versions outside the strategy
  block (agents.md).
- Do not widen `--allow-hosts` beyond loopback or drop `--disable-socket`
  to make a test pass; fix the offending test at its real boundary.
- Do not touch files outside `.github/workflows/ci.yml`,
  `tests/test_ci_pipeline.py`, `pyproject.toml`, `README.md`, and
  mechanical ruff fixes inside `src/`, `tests/`, `packages/`.
- Do not change `requires-python` (pinned by
  `tests/test_package_metadata.py`) or extend the matrix past 3.10-3.12;
  if the 3.9 floor looks inconsistent, raise a follow-up card.
- Do not invent a mypy config if none exists at execution time.
- Do not check a Definition of Done box without running its command.
- Do not cross the ADR 0010 boundary: no jobs that build or publish
  `dashboard/` or platform code, no PyPI publishing, no platform
  telemetry keys. The Rust gateway gets CI in card 51, not here.
- Do not run `docker compose up` or image builds in CI; job 5 is config
  parsing only.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_ci_pipeline.py -q` -> all
      pass (exactly 11 tests, or 12 if a mypy config existed)
- [ ] `.venv/bin/ruff check src tests packages && .venv/bin/ruff format
      --check src tests packages` -> both exit 0
- [ ] `.venv/bin/python -m pytest -q --disable-socket
      --allow-unix-socket --allow-hosts=127.0.0.1,::1` -> full suite
      green with network blocked
- [ ] `uv build --all-packages --out-dir /tmp/lucy-ci-dist && uvx twine
      check --strict /tmp/lucy-ci-dist/*` -> sdist + wheel for `lucy` and
      every `packages/*` member, twine prints PASSED for each
- [ ] `docker compose config --quiet` -> exit 0, no output
- [ ] `grep -n "secrets\." .github/workflows/ci.yml` -> no matches
      (exit code 1)
- [ ] `grep -c "actions/workflows/ci.yml/badge.svg" README.md` -> `1`
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
