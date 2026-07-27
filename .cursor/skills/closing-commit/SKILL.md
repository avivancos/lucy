---
name: closing-commit
description: Prepare Lucy start/closing commit and card stamp with explicit paths only. Never push or merge to main unless the human asks.
---

# Closing commit

Per `docs/agents/workflows/closing-commit.md`:

1. **Start** (on move to in_progress): stage only the card path; propose
   `chore(backlog): start card <NNN>` — commit when the human approves.
2. **Closing** (on move to done): stage only this card's code/tests/docs/card by
   explicit paths. Subject ends with `(card <NNN>)`. Never `git add -A`.
3. **Stamp**: append `## Closing commit` with hash, branch, file count, date;
   `Landed on main: pending human merge`.
4. Do **not** push, merge, or land onto `main` unless the human explicitly asks.
5. Honor-system vs machinery: only claim "blocks" when a named test/CI enforces it.
