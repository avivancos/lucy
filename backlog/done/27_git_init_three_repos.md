# 27 - Initialize git history for the three repos

**Epic:** Open-core restructure
**Estimated effort:** ~1 h
**State:** done

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

- [x] Three repos on `main` with a clean initial commit each: pili (5f35d38, 7
      files), lucy-platform (bb281d4, 25 files). lucy was already a git repo on
      `main` (its "restructure baseline" is the existing history, ef8e4bb onward).
- [x] `git status` clean in all three; no junk tracked (verified `git ls-files`
      excludes __pycache__/egg-info/.pytest_cache/.venv/.DS_Store/node_modules/.next).
- [x] Lucy test suite green at the committed baseline (153 passed in Docker).
- [x] Post-task audit done

## Improvements noted

- The card's premise ("Lucy has never been a git repo") was stale by the time
  it ran: lucy gained history earlier in S1. So lucy was NOT re-initialized
  (that would destroy history) - only verified clean + its outstanding S9
  backlog cards committed to reach a clean status.
- pili's .gitignore was missing `.DS_Store`; added it before the initial commit.
- pili and lucy-platform are now enrollable in the dev-agent fleet loop (they
  were blocked on having git history).
