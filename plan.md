# Lucy S8 Launch — plan

## Goal

Close sprint S8 (Launch): public repo installable from scratch — hygiene,
CI, docs, and pre-publication scrub/publish.

## Order

1. [x] **93** — Launch base hygiene (`uvicorn lucy.serve` entrypoints)
2. [x] **50** — Public CI pipeline (GitHub Actions + tripwire + build)
3. [x] **49** — README + six public guides + docs contract tests
4. [~] **46** — Pre-publication scrub (C1–C6 done; C7/C8 blocked on TWINE tokens)

## Current

- **GitGuardian remediation (2026-07):** synthetic PAT and bearer-shaped test
  literals removed; gitleaks CI + pytest tripwires; see
  `docs/security/gitguardian-incidents-2026-07.md`. Close incidents in
  GitGuardian UI after confirming invalid/test validity.
- Card **46** scrub executed through C6 in the SDK repo; operating docs and
  backlog live under `lucy-platform/ops/lucy-sdk/`. Dist name is `lucy-ai`
  (import still `lucy`). Card parked at
  `lucy-platform/ops/lucy-sdk/backlog/need_human_testing/46_pre_publication_scrub_and_publish.md`
  pending TestPyPI/PyPI tokens for C7/C8.
- See `docs/publication-scrub.md` for the move/rename record.

## Notes

- Card 46 stops before live publish if TestPyPI/PyPI tokens are missing;
  park under Pending human testing / `need_human_testing/` as needed.
- Durable-store tests skip under `--disable-socket` when Docker DNS IPs
  are blocked (CI has no Postgres/Redis; local Compose uses service IPs).
