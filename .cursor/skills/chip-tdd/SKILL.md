---
name: chip-tdd
description: Execute one Lucy backlog chip with Spec → red test → green minimal code → Verify in Docker. Use when working an in_progress card chip.
---

# Chip TDD

1. Read the active card Spec, the single assigned chip, Context primer files, and ADRs cited.
2. Write or extend the failing behavioral test named in the chip first.
3. Run Verify via Docker Compose; confirm red for the new assertion.
4. Implement the minimal code to pass. No mocks (ADR 0003). Use ManualClock when time matters.
5. Re-run Verify; confirm green. Refactor only inside chip scope.
6. Update `## Decision log` for any spec-silent choice.
7. Check the chip checkbox only after Verify passed. Stop and report if blocked.
