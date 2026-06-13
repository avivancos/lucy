# 46 - Pre-publication scrub and first PyPI publish

**Sprint:** S8 - Launch
**Epic:** Launch
**Estimated effort:** ~9 h
**Depends on:** 28, 49, 50
**State:** pending

## Goal

Execute the "pre-publication scrub milestone" that ADR 0010 deferred: lock
the public distribution name (`lucy-ai` dist, `lucy` import), move the
private operating docs to the lucy-platform repo, add SPDX headers and a
trademark policy, rewrite the README around the zero-key quickstart, and
ship the first release to PyPI with a matching `v0.x.0` git tag. After this
card the S8 exit demo holds: `pip install` + quickstart from scratch on a
clean machine.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  values); this file itself MOVES to lucy-platform in this card.
- `docs/adr/0010-open-core-split.md` - the decision this card completes:
  naming was deferred to this scrub, and the Consequences section says
  operating docs "stay in this repo until the pre-publication scrub
  milestone, then move private".
- `docs/adr/0003-no-mocks-testing-policy.md` - why publish verification
  runs against real TestPyPI/PyPI and real artifacts, never stubs.
- `LICENSE` and `NOTICE` - Apache-2.0 with Section 6 trademark reservation;
  `TRADEMARKS.md` builds on both.
- `pyproject.toml` - `name = "lucy"` today; the rename target. Also the
  dev extras where build/twine get pinned.
- `src/lucy/__init__.py` - `__version__`; must equal the pyproject version
  and the git tag (already asserted by `tests/test_package_metadata.py`).
- `README.md` - current platform-era text; fully rewritten here.
- `tests/test_package_metadata.py` - asserts `project["name"] == "lucy"`;
  the first test you flip red in C1.
- `tests/test_project_contract.py`, `tests/test_product_docs.py`,
  `tests/test_backlog_contract.py` - operating-doc contract tests that
  move to lucy-platform with the docs they validate.
- `tests/test_architecture_adrs.py` -
  `test_no_mocks_policy_is_recorded_in_adr_and_operating_docs` reads
  `agents.md` and `backlog/agent_index.md`; it stays here and must be
  trimmed when those files leave.
- `backlog/sprints.md` - S8 definition and exit demo; moves with the
  backlog tree.
- `backlog/pending/26_add_voice_agent_facade_and_public_api.md` - defines
  `examples/quickstart_voice_agent.py` (<30 lines, offline, zero keys);
  the README centers on that exact file.
- `backlog/pending/28_add_plugin_mechanism_and_workspace.md` - the
  `packages/` workspace whose member pyprojects declare `lucy` as a
  dependency; every one of those declarations renames in C1.
- `media-gateway-rust/src/main.rs` - Rust source that also gets an SPDX
  header.
- `Dockerfile.api` - copies only `pyproject.toml`, `README.md`, `src/`,
  `tests/`; confirms no operating docs ever reach the image.

## Spec

### Preconditions (verify before C1, stop if any fails)

- Cards 20/21 landed: `dashboard/` and all Pili code are gone from this
  repo; `/Users/agustin/Desarrollo/lucy-platform` exists and is a git repo.
- Card 27 landed: this repo is a git repo on `main` with a clean tree.
- Card 26 landed: `examples/quickstart_voice_agent.py` exists, is under
  30 lines, and runs offline with exit code 0.
- Cards 49 and 50 (S8 siblings, authored separately) are in `done/`.
- The full suite is green before anything in this card starts.

### Distribution name (C1)

- PyPI name `lucy` is taken by a third party (verified 2026-06-11); the
  decided dist name is `lucy-ai`, import name stays `lucy` (ADR 0010
  deferred exactly this; the pipecat precedent: dist != import).
- Before claiming it, re-check availability:
  `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/lucy-ai/json`
  must print `404`. If it prints `200`, the name was taken since the
  decision - STOP, follow the Failure protocol; the human re-decides.
- Rename is mechanical, exactly these edits:
  - `pyproject.toml`: `[project] name = "lucy-ai"`. Nothing else in the
    `[project]` table changes. `[tool.setuptools.packages.find]
    where = ["src"]` stays - the import package remains `lucy`.
  - Every `packages/*/pyproject.toml` (fixture plugin from card 28,
    provider plugins from card 29): replace the exact dependency string
    `"lucy"` with `"lucy-ai"` in `dependencies`. Entry-point group
    `"lucy.plugins"` does NOT change (it is an import-name namespace).
  - Run `grep -rn "pip install lucy" README.md docs/` and update every
    hit to `pip install lucy-ai` (extras become `lucy-ai[<extra>]`).
- Directory `src/lucy/` and every `import lucy` are untouched.
- After the edits, reinstall editables so metadata and entry points stay
  coherent: `.venv/bin/python -m pip install -e ".[dev]"` and
  `.venv/bin/python -m pip install -e packages/<member>` for each member.

### Operating-docs relocation (C2, C3)

Source -> destination (move = copy into lucy-platform, `git rm` here):

- `agents.md` -> `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/agents.md`
- `MEMORY.md` -> `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/MEMORY.md`
- `backlog/` (entire tree: `agent_index.md`, `_TEMPLATE.md`, `sprints.md`,
  all seven state folders with their `.gitkeep` markers and cards,
  including THIS card, which continues execution from its new home) ->
  `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/backlog/`
- `tests/test_project_contract.py` and `tests/test_backlog_contract.py` ->
  `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/tests/`. Both
  compute `ROOT = Path(__file__).resolve().parents[1]`, which now resolves
  to `ops/lucy-sdk/` where `agents.md` and `backlog/` are siblings - the
  relative layout is preserved on purpose.
- `tests/test_product_docs.py` ->
  `/Users/agustin/Desarrollo/lucy-platform/tests/test_product_docs.py`
  (platform root, next to the `docs/product/` tree card 21 moved). If
  card 21 already relocated or rewrote it, verify it passes there and
  skip the move.

Adjustments to the moved/remaining tests, exactly these:

- Moved `test_project_contract.py`: in
  `test_bootstrap_contract_is_complete`, delete the two trailing asserts
  on `docs/adr/0001-*.md` and `docs/adr/0002-*.md` (ADRs stay in the
  public SDK repo) and align the asserted heading list with the headings
  actually present in the moved `backlog/_TEMPLATE.md`.
- Moved `test_backlog_contract.py`: no code changes; it must pass in the
  new home unchanged.
- Remaining `tests/test_architecture_adrs.py`: rename
  `test_no_mocks_policy_is_recorded_in_adr_and_operating_docs` to
  `test_no_mocks_policy_is_recorded_in_adr`, delete the `agents.md` and
  `backlog/agent_index.md` reads and the loop over them, keep every ADR
  assertion. The operating-docs half of the coverage lives on in
  lucy-platform via the moved `test_project_contract.py`
  (`assert "no-mocks project" in agents`).

New permanent guard in this repo, `tests/test_publication_scrub.py`:

- `test_no_operating_docs_in_public_repo` - asserts none of `agents.md`,
  `MEMORY.md`, `backlog/`, `tests/test_project_contract.py`,
  `tests/test_product_docs.py`, `tests/test_backlog_contract.py` exists
  under the repo root. Paths live in one module-level tuple
  `PRIVATE_OPERATING_PATHS`, not scattered literals.

### SPDX headers and trademark policy (C4)

- Header, verbatim, as the first two lines of every Python file (after
  the shebang line when one exists, before the module docstring):

  ```python
  # Copyright 2026 Lucy contributors
  # SPDX-License-Identifier: Apache-2.0
  ```

- Rust variant with `//` comments, same two lines, in every `*.rs` under
  `media-gateway-rust/src/`.
- Scope: every `*.py` under `src/`, `tests/`, `examples/`, and
  `packages/*/src/` (skip `__pycache__`); every `*.rs` under
  `media-gateway-rust/src/`.
- Guard tests in `tests/test_publication_scrub.py`:
  - `test_every_source_file_carries_spdx_header` - walks the scope dirs
    (one module-level tuple `HEADER_SCOPE_DIRS`), asserts
    `SPDX-License-Identifier: Apache-2.0` within the first 3 lines of
    each file and a line matching `Copyright \d{4} Lucy contributors`.
  - `test_trademarks_policy_allows_built_with_lucy` - `TRADEMARKS.md`
    exists and its lowercased text contains all of: `built with lucy`,
    `apache license`, `section 6`, `endorsement`.
- `TRADEMARKS.md` (new, repo root, CNCF-style), required content:
  - The "Lucy" name and logo are trademarks of the project owners and are
    NOT licensed under the Apache License (Section 6 of `LICENSE`;
    restated in `NOTICE`).
  - Allowed without permission: unmodified redistribution keeping
    `LICENSE`/`NOTICE`; truthful descriptive statements such as
    "built with Lucy", "works with Lucy", "plugin for Lucy".
  - Not allowed: using the marks (or confusingly similar ones) in
    product, company, domain, or package names in a way that implies
    sponsorship or endorsement; shipping modified forks under the Lucy
    name; altering the logo.
  - A contact line for permission requests.

### README rewrite (C5)

Replace `README.md` wholesale. Required content, in order:

1. Title + one-paragraph positioning: open-source, provider-agnostic,
   telephony-native voice-agent SDK; Apache-2.0.
2. Install: `pip install lucy-ai` (and that the import name is `lucy`).
3. Quickstart: ONE fenced `python` block that is byte-identical to
   `examples/quickstart_voice_agent.py` (under 30 lines, zero keys,
   offline), preceded by one line saying exactly that.
4. Feature bullets - each claim verified against a card already in
   `done/` before writing it (graph runtime, voice session, provider
   plugins via spec strings, `lucy.testing` simulators, `lucy.observe`
   local exporters, local trace viewer, telephony transports).
5. License section linking `LICENSE`, `NOTICE`, `TRADEMARKS.md`.
6. One line noting the hosted observability platform consumes the same
   public wire spec (`docs/telemetry-wire-v1.md`); no closed features
   are documented here.

Drop the platform-era content: dashboard architecture bullets and the
`cost_per_minute` formula block (the metric lives in code and SDK docs).

Guard tests in `tests/test_publication_scrub.py`:

- `test_readme_quickstart_matches_runnable_example` - the first fenced
  `python` block in `README.md` equals
  `examples/quickstart_voice_agent.py` exactly, and that file has fewer
  than 30 lines.
- `test_readme_names_the_install_command` - `pip install lucy-ai`
  appears in `README.md`.

### Build, scrub check, and publish (C6, C7, C8)

- Pin tooling in `pyproject.toml` dev extras: add `build>=1.2.0` and
  `twine>=6.0.0`. Guard test
  `test_release_tooling_pinned_in_dev_extras` in
  `tests/test_publication_scrub.py` asserts both appear in
  `optional-dependencies.dev` (parse with `tomli` like
  `tests/test_package_metadata.py` does).
- Launch version: `[project] version` must match `0.<minor>.0` (patch 0
  for a first publish). If the current version is mid-patch, bump minor
  and reset patch in BOTH `pyproject.toml` and `src/lucy/__init__.py`
  (`tests/test_package_metadata.py` already asserts they agree).
- Build: `rm -rf dist && .venv/bin/python -m build` produces exactly
  `dist/lucy_ai-<version>.tar.gz` and
  `dist/lucy_ai-<version>-py3-none-any.whl` (PEP 625 normalizes `-` to
  `_`). `dist/` is already gitignored; never commit artifacts.
- Scrub check (the publish gate): both greps must produce NO output:
  - `tar -tzf dist/lucy_ai-*.tar.gz | grep -E "agents\.md|MEMORY\.md|backlog/|test_project_contract|test_backlog_contract"`
  - `unzip -l dist/lucy_ai-*.whl | grep -E "agents\.md|MEMORY\.md|backlog"`
- `twine check dist/*` -> `PASSED` for both artifacts.
- Credentials: `TWINE_USERNAME=__token__` and `TWINE_PASSWORD` set
  inline from tokens the human provides at run time (TestPyPI token for
  C7, PyPI token for C8). Tokens never land in files, code, logs, or
  command output. If a token is missing, STOP per Failure protocol.
- TestPyPI upload:
  `twine upload --repository-url https://test.pypi.org/legacy/ dist/*`.
- Clean-room install check (both C7 against TestPyPI and C8 against
  PyPI): create a fresh venv under `/tmp`, `pip install
  "lucy-ai==<version>"` (for TestPyPI add `--index-url
  https://test.pypi.org/simple/ --extra-index-url
  https://pypi.org/simple/` so dependencies resolve from real PyPI),
  then from `/tmp` run the repo's
  `examples/quickstart_voice_agent.py` with that venv's interpreter and
  no `LUCY_*` env vars -> exit 0, transcript and trace events printed,
  no network needed at runtime. Running from `/tmp` guarantees the
  installed wheel is imported, not `src/`.
- PyPI upload: `twine upload dist/*` with the PyPI token. A version can
  never be re-uploaded; if an artifact is wrong after upload, the fix is
  a patch bump and a new release, not a retry.
- Tag (after the PyPI install check passes):
  `git tag -a "v$(.venv/bin/python -c 'from lucy import __version__; print(__version__)')" -m "lucy-ai first public release"`.
  Pushing the tag/repo to the public remote happens only if `git remote`
  already lists one; otherwise record it under "Pending human testing".

## Chips

- [ ] **C1 - Lock the dist name lucy-ai, keep import lucy.** Re-check
  name availability (curl per Spec -> `404`). Test first: flip
  `test_python_package_metadata_and_dependencies` in
  `tests/test_package_metadata.py` to assert `name == "lucy-ai"` and add
  `test_distribution_lucy_ai_serves_import_lucy`
  (`importlib.metadata.version("lucy-ai") == lucy.__version__`). Then
  edit `pyproject.toml`, every `packages/*/pyproject.toml` dependency,
  and the `pip install` doc hits per Spec; reinstall all editables.
  Verify: `.venv/bin/python -m pip install -e ".[dev]" &&
  .venv/bin/python -m pytest tests/test_package_metadata.py
  tests/test_plugins.py -q` -> all pass (plugins prove entry points
  survived the rename).
- [ ] **C2 - Move operating docs to lucy-platform.** Test first: create
  `tests/test_publication_scrub.py` with
  `test_no_operating_docs_in_public_repo` (red while the files exist).
  Then perform every move in the Spec table (copy into
  `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/`, `git rm` here,
  commit in both repos). This card keeps executing from its new home.
  Verify: `.venv/bin/python -m pytest tests/test_publication_scrub.py -q`
  -> all pass; `ls /Users/agustin/Desarrollo/lucy/agents.md` -> "No such
  file or directory".
- [ ] **C3 - Re-home the moved tests, trim the remaining one.** Apply the
  exact test adjustments in the Spec: edit the moved
  `test_project_contract.py`, leave `test_backlog_contract.py` untouched,
  place/verify `test_product_docs.py` at the platform root, and trim
  `tests/test_architecture_adrs.py` to
  `test_no_mocks_policy_is_recorded_in_adr`. Files:
  `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/tests/*.py`,
  `/Users/agustin/Desarrollo/lucy-platform/tests/test_product_docs.py`,
  `tests/test_architecture_adrs.py`. Verify: `.venv/bin/python -m pytest
  /Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/tests -q &&
  .venv/bin/python -m pytest tests/test_architecture_adrs.py -q` -> all
  pass in both runs.
- [ ] **C4 - SPDX headers and TRADEMARKS.md.** Tests first in
  `tests/test_publication_scrub.py`:
  `test_every_source_file_carries_spdx_header` and
  `test_trademarks_policy_allows_built_with_lucy` (both red). Then add
  the two-line header to every in-scope `*.py` and `*.rs` file and write
  `TRADEMARKS.md` with the required content. Files: `src/**/*.py`,
  `tests/**/*.py`, `examples/*.py`, `packages/*/src/**/*.py`,
  `media-gateway-rust/src/*.rs`, `TRADEMARKS.md`. Verify:
  `.venv/bin/python -m pytest tests/test_publication_scrub.py -q` -> all
  pass.
- [ ] **C5 - README rewrite around the quickstart.** Tests first:
  `test_readme_quickstart_matches_runnable_example` and
  `test_readme_names_the_install_command` in
  `tests/test_publication_scrub.py` (red against the old README). Then
  rewrite `README.md` per the Spec outline, verifying each feature
  bullet against `done/` cards. Verify: `.venv/bin/python -m pytest
  tests/test_publication_scrub.py -q && .venv/bin/python
  examples/quickstart_voice_agent.py` -> tests pass, quickstart exits 0
  offline.
- [ ] **C6 - Build artifacts and run the scrub gate.** Test first:
  `test_release_tooling_pinned_in_dev_extras` (red), then add
  `build>=1.2.0` and `twine>=6.0.0` to dev extras in `pyproject.toml`,
  reinstall, confirm the launch version matches `0.<minor>.0` (bump per
  Spec if not), and build. Verify: `rm -rf dist &&
  .venv/bin/python -m build && .venv/bin/twine check dist/*` -> two
  artifacts, both `PASSED`; both scrub greps from the Spec -> no output
  (exit code 1).
- [ ] **C7 - TestPyPI publish and clean-room quickstart.** No new pytest
  test: per ADR 0003 the red/green here is real TestPyPI, not a stub.
  Preconditions first, in order: `.venv/bin/python -m pytest -q` -> full
  suite green (NEVER publish otherwise), scrub greps still empty. Then
  upload with the TestPyPI token per Spec. Verify: `curl -s -o /dev/null
  -w "%{http_code}" https://test.pypi.org/pypi/lucy-ai/json` -> `200`;
  fresh `/tmp` venv installs `lucy-ai==<version>` from TestPyPI (with
  the extra-index per Spec) and runs
  `examples/quickstart_voice_agent.py` from `/tmp` -> exit 0 with
  transcript output.
- [ ] **C8 - PyPI publish and v0.x.0 tag.** No new pytest test (same
  ADR 0003 rationale). Re-run `.venv/bin/python -m pytest -q` -> green,
  then `twine upload dist/*` with the PyPI token, repeat the clean-room
  install + quickstart against real PyPI, and create the annotated tag
  per Spec. Verify: `curl -s -o /dev/null -w "%{http_code}"
  https://pypi.org/pypi/lucy-ai/json` -> `200`; `git tag --points-at
  HEAD` -> prints the `v0.<minor>.0` tag.
- [ ] **C9 - Full suite + bookkeeping.** Run the lucy suite and the
  relocated operating-docs suite, fill "Improvements noted", move this
  card to `done/` inside
  `/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/backlog/` (its
  home since C2), and record the public-remote push status under
  "Pending human testing" if no remote existed. Verify:
  `.venv/bin/python -m pytest -q && .venv/bin/python -m pytest
  /Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/tests -q` -> both
  green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003): no stubbed `twine`,
  no fake index servers, no monkeypatched `importlib.metadata`. Publish
  verification runs against real TestPyPI/PyPI; file-scrub tests inspect
  the real tree and real built artifacts.
- Do not hardcode secrets or scatter literals (agents.md): tokens exist
  only as env vars at invocation time and never in files, code, logs, or
  echoed commands; scrub paths and header scope live in the named
  module-level tuples `PRIVATE_OPERATING_PATHS` and `HEADER_SCOPE_DIRS`.
- Do not touch files outside the ones this card lists.
- Do not check a Definition of Done box without running its command.
- Do not cross the ADR 0010 boundary in either direction: operating docs
  and backlog go private, but `LICENSE`, `NOTICE`, `TRADEMARKS.md`, the
  ADRs, and `docs/telemetry-wire-v1.md` (public normative spec) stay in
  this repo; no platform code or docs come back in.
- Do not rename the import package: `src/lucy/` and `import lucy` are
  untouched; only the dist name becomes `lucy-ai`. The entry-point group
  `"lucy.plugins"` keeps its name.
- Do not rewrite ADR history to scrub `agents.md` mentions; ADRs are a
  decision log, their text stands as written.
- Do not publish to PyPI before the TestPyPI clean-room install and
  quickstart pass, and never with a non-green full suite or a non-empty
  scrub grep.
- Do not re-upload a published version after a mistake; bump the patch
  and release again.
- Do not commit `dist/` artifacts or the `/tmp` venvs.

## Definition of Done

- [ ] `grep -n 'name = "lucy-ai"' pyproject.toml` -> exactly one match
- [ ] `.venv/bin/python -m pytest tests/test_publication_scrub.py
      tests/test_package_metadata.py -q` -> all pass
- [ ] `ls agents.md MEMORY.md backlog 2>&1 | grep -c "No such file"` ->
      `3` (operating docs gone from the public repo)
- [ ] `.venv/bin/python -m pytest
      /Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/tests -q` ->
      all pass (moved contract tests green in their new home)
- [ ] `tar -tzf dist/lucy_ai-*.tar.gz | grep -E
      "agents\.md|MEMORY\.md|backlog/"` -> no output, exit code 1
- [ ] `.venv/bin/twine check dist/*` -> `PASSED` for sdist and wheel
- [ ] `curl -s -o /dev/null -w "%{http_code}"
      https://pypi.org/pypi/lucy-ai/json` -> `200`
- [ ] Fresh `/tmp` venv: `pip install "lucy-ai==<version>"` from real
      PyPI, then run `examples/quickstart_voice_agent.py` from `/tmp` ->
      exit 0, transcript printed, no keys, no network at runtime
- [ ] `git tag --points-at HEAD` -> prints the `v0.<minor>.0` launch tag
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing (lucy-platform not seeded, the
quickstart absent, `lucy-ai` taken on PyPI, a token unavailable), or the
spec turns out wrong: do NOT check boxes, do NOT force tests green, and
above all do NOT publish. Leave the card in `in_progress/`, document what
happened under "Improvements noted", and report. Partial honest work beats
fake completion - an unpublished release is recoverable, a bad PyPI upload
is not.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. Expected entries:
pushing main + the release tag to the public remote, and making the
GitHub repo public, if no remote was configured at execution time. -->
