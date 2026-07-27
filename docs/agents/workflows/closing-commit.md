# Closing commit — the git contract

## Purpose

Card state changes that leave no git trace cannot be engineered against. This
contract makes the card lifecycle git-visible and the closing commit
traceable from the card itself.

## 1. Start commit

Moving a card into `in_progress/` triggers a **start commit** of the card
alone:

```text
chore(backlog): start card <NNN>
```

Consequence: a card can never reach `done/` untracked. A skipped closing
commit becomes a visible rename in `git status` instead of leaving zero
trace.

## 2. Closing commit

Moving the card to `done/` (or `need_human_testing/` when that is the exit)
triggers the closing commit:

- Contents: touched code, tests, specs/docs, and the moved card — **only**
  that card's files.
- Staging **by explicit path** (`git add <path> …`). Never `git add -A`,
  `git add .`, or `git commit -a`.
- Conventional Commit whose subject ends with `(card <NNN>)`.

In Lucy interactive sessions, create the commit only when the human asks (see
`AGENTS.md` commit handoff). The obligation to *prepare* the explicit path
list and message remains. Orchestrated runs follow their enrollment policy
but still never push unless authorized.

## 3. Branch honesty — no auto-land to main

The closing commit lands on the **current working branch**.

**Lucy policy (explicit):** agents do **not** auto-merge or push to `main`.
Landing onto the default branch is a **human gate (honor-system)** — the
human reviews and merges. Do not claim that merge-to-main is mechanically
enforced unless a named CI check or script does so.

Diagnosis retained from the shared contract: work that never lands on the
branch others read becomes invisible and gets re-implemented. Prefer landing
promptly after human review; measure landing debt when tooling exists.

## 4. Commit stamp

Immediately after the closing commit (or as a follow-up commit the human
approves), append to the card:

```markdown
## Closing commit
- Hash: `<short-sha>` — <subject line>
- Branch: `<branch>` · Files: <n> · Date: <YYYY-MM-DD>
- Landed on main: pending human merge
```

When a human merges, update `Landed on main` with the merge sha and date.

```text
docs(backlog): stamp closing commit <short-sha> (card <NNN>)
```

## 5. Deterministic verification (aspirational)

When a `backlog-verify` script exists it should report (advisory unless a
named gate blocks):

- every card in `done/` is git-tracked and has at least one commit touching
  it;
- closing commit subjects match `(card \d+)`;
- no card number appears in two state folders.

Until that script exists in this repo, the above is **honor-system**.

## 6. No fictional machinery

A document may claim an action "blocks" only if a named mechanism enforces
it (contract test, CI job, hook). Honor-system rules must say
"honor-system". Claiming enforcement that does not exist is worse than
having none.
