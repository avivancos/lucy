# 111 - Add a docs site generator when traffic justifies it

**Sprint:** S8 - Launch
**Epic:** Launch docs
**Estimated effort:** ~8 h
**Depends on:** 49
**State:** pending
**Risk tier:** T1 - docs tooling only; no SDK ABI change

## Goal

Replace in-repo markdown-only guides with a generated docs site (mkdocs
or similar) when public traffic justifies navigation, search, and
versioned pages. Card 49 deliberately shipped markdown-only.

## Context primer

- `docs/guides/` - the six public guides and index shipped by card 49
- `tests/test_docs_guides.py` - docs contract that must keep executing
  every python fence offline after any generator is added
- `backlog/done/49_docs_site_and_readme.md` - launch decision: generator
  out of scope
- `agents.md` - English docs, no mocks

## Spec

- Choose one static-site generator (default candidate: mkdocs-material)
  and document the choice in an ADR or short design note.
- Keep every guide's runnable python fences executable by
  `tests/test_docs_guides.py` without a skip list.
- Do not invent a public PyPI docs package name; naming follows ADR 0010.
- CI builds the site as an artifact; publishing the hosted URL is a
  separate ops decision.

## Files to create/modify

- docs site config (e.g. `mkdocs.yml`)
- `docs/guides/*` (nav only; content already exists)
- CI workflow step for the docs build
- `tests/test_docs_guides.py` remains green

## Chips

- [ ] **C1 - Pick generator and add config.** Files: site config,
  nav mapping the six guides. Verify:
  `mkdocs build` (or chosen tool) -> exit 0.
- [ ] **C2 - Wire CI artifact.** Files: `.github/workflows/*`. Verify:
  workflow builds the site artifact.
- [ ] **C3 - Docs contract still green.** Verify:
  `docker compose run --rm lucy-api pytest tests/test_docs_guides.py -q`
  -> all pass; `docker compose run --rm lucy-api pytest -q` -> full
  suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003).
- Do not hardcode provider names, URLs, or thresholds outside typed
  settings.
- Do not break the offline docs contract tests.
- Do not move platform/dashboard docs into the open SDK site (ADR 0010).
- Do not invent the public distribution name.

## Definition of Done

- [ ] Local docs site build -> exit 0
- [ ] `docker compose run --rm lucy-api pytest tests/test_docs_guides.py -q`
      -> all pass
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green

## Failure protocol

If the generator would force skipping executable blocks, stop and keep
markdown-only.

## Decision log

No decisions: implementation followed the spec exactly.

## Improvements noted

<!-- Fill during execution. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
