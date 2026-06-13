# 27 - Initialize git history for the three repos

**Epic:** Open-core restructure
**Estimated effort:** ~1 h
**State:** pending

## Goal

Lucy has never been a git repo. Initialize clean, intentional histories for
the public-to-be SDK and the two private repos after the extraction cards.

## Spec

Order matters (clean public history): init `/Users/agustin/Desarrollo/pili`
and `/Users/agustin/Desarrollo/lucy-platform` first with their extracted
content, then init lucy. Each repo gets: `.gitignore` (lucy already has one;
write appropriate ones for the others), default branch `main`, one initial
commit staging only intended files (no `.venv`, `.pytest_cache`, `.DS_Store`,
`node_modules`). Lucy's commit message records the restructure baseline.

## Files to create/modify

- `/Users/agustin/Desarrollo/pili/.gitignore` - new
- `/Users/agustin/Desarrollo/lucy-platform/.gitignore` - new
- `.gitignore` - verify coverage before the initial commit

## Definition of Done

- [ ] Three repos on `main` with exactly one clean initial commit each.
- [ ] `git status` clean in all three; no junk files tracked.
- [ ] Lucy test suite green at the committed baseline.
- [ ] Post-task audit done

## Improvements noted

<!-- Fill during execution. -->
