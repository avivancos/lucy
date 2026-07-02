# 62 - Add executable review enforcement (hooks, validators, attestations)

**Sprint:** S10 - Agent operations
**Epic:** Agent operations
**Estimated effort:** ~6 h
**Depends on:** 61
**State:** pending

## Goal

Upgrade the card-61 review gate from contract-test enforcement to executable
delivery gates: a pre-push hook that validates card transitions and review
evidence, and content-addressed attestations that bind reviewer reports to the
reviewed diff so stale reviews invalidate automatically.

## Context primer

- `backlog/agent_index.md` - the review gate this card hardens (card 61)
- `tests/test_backlog_contract.py` - current enforcement; stays as the fast
  in-suite layer
- `/Users/agustin/Desarrollo/english-tutor/.githooks/pre-push` - the hook
  pattern to port (card detection, transition validation, review validation)
- `/Users/agustin/Desarrollo/english-tutor/scripts/validate_final_review.py`
  and `scripts/review_target.py` - digest + attestation reference
  implementation (SHA-256 over changed files + immutable card sections;
  receipt in `.git/review-attestations/<card>.json`)
- `agents.md` - commit-handoff and security rules the hook must respect

## Spec

- `scripts/validate_card_transition.py`: legal state moves only (the
  `agent_index.md` flow); never delete, never copy between states.
- `scripts/review_target.py`: deterministic SHA-256 digest over changed file
  contents (domain-separated: path, mode, length, deletion) plus the card's
  immutable sections (Goal, Spec, Chips).
- `scripts/validate_review_evidence.py`: for cards >= 61 entering
  done/testing/production - Review evidence present and substantive, verdicts
  PASS, no unresolved P0/P1, every P2/P3 with a disposition, digest matches
  the receipt.
- Receipt: `.git/review-attestations/<card-id>.json` (untracked) mapping
  reviewer -> report hash + frozen digest, written when reviews run.
- `.githooks/pre-push` + a `make install-hooks` (or documented `git config
  core.hooksPath .githooks`) wiring; hook runs the validators and the
  targeted contract tests in Docker.
- All validators run with `docker compose run --rm lucy-api` for isolation.

## Files to create/modify

- `scripts/validate_card_transition.py`, `scripts/review_target.py`,
  `scripts/validate_review_evidence.py`, `.githooks/pre-push` - new
- `tests/test_review_enforcement.py` - new, drives the validators
- `backlog/agent_index.md` - reference the executable gate

## Chips

- [ ] **C1 - transition validator.** Test first:
  `test_transition_validator_rejects_illegal_moves` in
  `tests/test_review_enforcement.py`. Files:
  `scripts/validate_card_transition.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_review_enforcement.py -q`
  -> new tests pass.
- [ ] **C2 - digest + receipt.** Test first:
  `test_digest_changes_when_reviewed_file_changes`. Files:
  `scripts/review_target.py`. Verify: same command -> pass, digest stable
  across runs on identical content.
- [ ] **C3 - review-evidence validator.** Test first:
  `test_unresolved_p1_blocks_done_move`. Files:
  `scripts/validate_review_evidence.py`. Verify: same command -> pass.
- [ ] **C4 - pre-push hook + docs.** Wire validators into `.githooks/pre-push`
  and document installation in `agent_index.md`. Verify:
  `git push --dry-run` on a branch with an illegal card move -> hook rejects;
  legal push -> passes.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); drive validators with
  real temp git repos and real card files.
- Do not hardcode state names, digest parameters, or paths outside named
  constants shared with `tests/test_backlog_contract.py`.
- Do not make the hook require network access or the dashboard.
- Do not block lifecycle-only pushes whose digest is unchanged (english-tutor
  reuse rule).

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_review_enforcement.py -q`
      -> green
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] A push moving a card >= 61 to done/ without Review evidence is rejected
      by the hook -> observed rejection message
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
