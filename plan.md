# Lucy S8 Launch — plan

## Goal

Close sprint S8 (Launch): public repo installable from scratch — hygiene,
CI, docs, and pre-publication scrub/publish.

## Order

1. [x] **93** — Launch base hygiene (`uvicorn lucy.serve` entrypoints)
2. [x] **50** — Public CI pipeline (GitHub Actions + tripwire + build)
3. [x] **49** — README + six public guides + docs contract tests
4. [ ] **46** — Pre-publication scrub and publish (needs PyPI tokens)

## Current

- Card **49** closed: `README.md` quickstart rewrite, six guides under
  `docs/guides/`, `tests/test_docs_guides.py` (23 tests, every python
  block runs offline), ADR 0010 implementation-status refresh, follow-up
  **111** for a future docs site generator.
- Next: card **46** pre-publication scrub and publish.

## Notes

- Card 46 stops before live publish if TestPyPI/PyPI tokens are missing;
  park under Pending human testing / `need_human_testing/` as needed.
- Durable-store tests skip under `--disable-socket` when Docker DNS IPs
  are blocked (CI has no Postgres/Redis; local Compose uses service IPs).
