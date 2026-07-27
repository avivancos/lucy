---
name: final-review
description: Orchestrate Lucy final review by risk tier, spawn Cursor reviewers with native models, write Review evidence. Use before moving a card to done.
---

# Final review

1. Confirm entry conditions in `docs/agents/workflows/final-review.md` (risk tier, Decision log, red→green, Docker evidence). If missing, return BLOCKED — do not fake review.
2. Spawn reviewers required by tier using `.cursor/agents/` and models from `AGENTS.md` Cursor table:
   - fast (`composer-2.5-fast`): docs-reviewer, simplicity-reviewer, card-writer (if needed)
   - high (`cursor-grok-4.5-high-fast`): code-reviewer, test-auditor, security-reviewer
   - session (`inherit`): final-integrator; T3 escalations for code/security
3. Collect PASS/FAIL/BLOCKED/NOT_APPLICABLE. P0/P1 block done. P2/P3 need disposition.
4. At T3 require mutation testing or BLOCKED.
5. Write substantive `## Review evidence` on the card (no leftover template rows).
6. Hand off to human for commit/push; never auto-land to main.
