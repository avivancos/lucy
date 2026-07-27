# Implementation decision log

## Purpose

The implementing agent quietly makes decisions, assumptions, and trade-offs
that do not contradict the requirements, so review can pass without ever
surfacing them. The decision log makes those decisions reviewable. It is
written **during** implementation, not reconstructed at the end.

The log lives inside the backlog card as a `## Decision log` section. Contract
tests enforce presence for cards at or above the configured id threshold.
A log exceeding roughly 15 entries means the card was too big — split it.

## What must be logged

An entry is mandatory whenever the agent:

- makes a choice **the spec is silent on** (naming, data shape, library,
  error semantics, defaults);
- finds a **contradiction** between spec, code, tests, docs, or observed
  reality;
- accepts a **trade-off** (performance vs clarity, scope cut, deferred edge
  case);
- hits a **blocker** and routes around it;
- changes the card's **risk tier** or deviates from a planned chip.

## Entry format

Every entry starts `- **D<n> —` so contract tests can verify presence
mechanically:

```markdown
## Decision log
- **D1 — <short decision title>**
  - Decision: <what was chosen>
  - Trigger: spec-silent | contradiction | trade-off | blocker | tier-change
  - Why: <1–2 lines>
  - Alternatives rejected: <option — why not; or "none considered">
  - Spec impact: none | needs-spec-update | needs-ADR
```

## The empty-log rule

An empty log is valid **only** with the explicit line:

```markdown
No decisions: implementation followed the spec exactly.
```

That claim is itself reviewed. An empty or missing log on a non-trivial diff
is a **P1 finding at T2/T3 and a P2 finding at T1**.

## How reviewers consume it

- **`code-reviewer`** cross-checks the log against the diff in both
  directions: decisions visible in the diff but absent from the log are
  findings, and log claims the code does not implement are findings.
- **`docs-reviewer`** verifies that every entry marked `needs-ADR` became an
  ADR, and that every `needs-spec-update` produced a doc change or follow-up
  card.
- **`security-reviewer`** treats spec-silent decisions on trust boundaries as
  new attack surface.
