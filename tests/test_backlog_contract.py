"""Contract tests for backlog card quality (see backlog/agent_index.md).

Cards carrying a ``**Sprint:**`` header are "upgraded" cards and must follow
the junior-agent-executable standard in ``backlog/_TEMPLATE.md``. Cards
without the marker are legacy-format cards mid-execution (the S1 restructure
chain); they are exempt until upgraded.

``ENFORCE_UPGRADE_FROM`` is the tightening knob: once the upgrade wave lands,
every pending card with id >= that number must carry the marker.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "backlog"
STATE_DIRS = [
    "pending",
    "in_progress",
    "done",
    "need_human_testing",
    "testing",
    "production",
]

# Cards 20-27 are the S1 restructure chain executing in legacy format.
LEGACY_EXEMPT_IDS = set(range(20, 28))
# Every pending card from 28 on must follow the junior-agent standard.
ENFORCE_UPGRADE_FROM = 28

REQUIRED_SECTIONS = [
    "## Goal",
    "## Context primer",
    "## Spec",
    "## Chips",
    "## Do NOT",
    "## Definition of Done",
    "## Failure protocol",
    "## Improvements noted",
]

REQUIRED_HEADER_FIELDS = [
    "**Sprint:**",
    "**Epic:**",
    "**Estimated effort:**",
    "**Depends on:**",
    "**State:**",
]

# Cards from this id on must declare Risk tier and Decision log (card 110).
ENFORCE_TIER_AND_LOG_FROM = 110

CURSOR_AGENT_NAMES = (
    "code-reviewer",
    "test-auditor",
    "simplicity-reviewer",
    "docs-reviewer",
    "security-reviewer",
    "card-writer",
    "implementation-agent",
    "final-integrator",
)

CURSOR_RULE_NAMES = (
    "00-backlog-session",
    "01-docker-only",
    "02-tdd-chips",
    "03-definition-of-done",
    "04-lucy-invariants",
    "05-risk-tiers",
    "06-planning-gate",
    "07-anti-vibecoding",
)

WORKFLOW_FILES = (
    "risk-tiers.md",
    "final-review.md",
    "implementation-log.md",
    "closing-commit.md",
)


def pending_cards() -> list[Path]:
    return sorted((BACKLOG / "pending").glob("[0-9]*_*.md"))


def card_id(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def upgraded_cards() -> list[Path]:
    return [
        p for p in pending_cards() if "**Sprint:**" in p.read_text(encoding="utf-8")
    ]


def section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^{re.escape(heading)}\s*$(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    return match.group(1) if match else ""


def sprint_ids_from_index() -> set[str]:
    text = (BACKLOG / "sprints.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| (S\d+) ", text, re.M))


def test_sprint_index_exists_and_covers_known_sprints():
    ids = sprint_ids_from_index()
    assert {"S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"} <= ids


def test_every_existing_card_is_mapped_to_exactly_one_sprint():
    """sprints.md may list future (not yet written) cards - it is the roadmap.

    The invariant is the other direction: no existing card from the open-core
    era (id >= 17) may be orphaned from the sprint index, and none may appear
    in two sprints.
    """
    text = (BACKLOG / "sprints.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| S\d+ [^|]*\| [^|]*\| ([^|]*)\|", text, re.M)
    listed: list[int] = []
    for row in rows:
        listed.extend(int(n) for n in re.findall(r"\b(\d{2,3})\b", row))
    duplicated = sorted({n for n in listed if listed.count(n) > 1})
    assert not duplicated, f"cards mapped to more than one sprint: {duplicated}"
    orphans = []
    for state in STATE_DIRS:
        for path in (BACKLOG / state).glob("[0-9]*_*.md"):
            cid = card_id(path)
            if cid >= 17 and cid not in listed:
                orphans.append(path.name)
    assert not orphans, f"cards not mapped to any sprint in sprints.md: {orphans}"


def test_sprint_index_non_platform_cards_exist_locally():
    text = (BACKLOG / "sprints.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| S\d+ [^|]*\| [^|]*\| ([^|]*)\|", text, re.M)
    existing = {
        card_id(path)
        for state in STATE_DIRS
        for path in (BACKLOG / state).glob("[0-9]*_*.md")
    }
    missing = []
    for row in rows:
        for number, platform_marker in re.findall(r"\b(\d{2,3})(\*)?", row):
            card = int(number)
            if not platform_marker and card not in existing:
                missing.append(card)

    assert not missing, f"sprints.md lists local cards with no file: {missing}"


def test_upgraded_pending_cards_follow_the_junior_standard():
    problems = []
    valid_sprints = sprint_ids_from_index()
    for path in upgraded_cards():
        text = path.read_text(encoding="utf-8")
        for field in REQUIRED_HEADER_FIELDS:
            if field not in text:
                problems.append(f"{path.name}: missing header field {field}")
        sprint_match = re.search(r"\*\*Sprint:\*\* (S\d+)", text)
        if sprint_match and sprint_match.group(1) not in valid_sprints:
            problems.append(
                f"{path.name}: sprint {sprint_match.group(1)} not in sprints.md"
            )
        for heading in REQUIRED_SECTIONS:
            if heading not in text:
                problems.append(f"{path.name}: missing section {heading}")
        chips = re.findall(r"- \[[ x]\] \*\*C\d+", section(text, "## Chips"))
        if len(chips) < 3:
            problems.append(f"{path.name}: needs >=3 chips, found {len(chips)}")
        chip_body = section(text, "## Chips")
        if chip_body.count("Verify:") < max(len(chips), 1):
            problems.append(f"{path.name}: every chip needs a Verify: command")
        primer = section(text, "## Context primer")
        if len(re.findall(r"`[^`]+`", primer)) < 2:
            problems.append(f"{path.name}: Context primer needs >=2 file references")
        do_not = section(text, "## Do NOT").lower()
        if "mock" not in do_not or "hardcod" not in do_not:
            problems.append(f"{path.name}: Do NOT must cover mocks and hardcoding")
        dod = section(text, "## Definition of Done")
        if dod.count("->") < 2:
            problems.append(f"{path.name}: DoD needs >=2 'command -> outcome' items")
    assert not problems, "\n".join(problems)


def test_pending_cards_past_threshold_are_upgraded():
    stale = [
        p.name
        for p in pending_cards()
        if card_id(p) >= ENFORCE_UPGRADE_FROM
        and card_id(p) not in LEGACY_EXEMPT_IDS
        and "**Sprint:**" not in p.read_text(encoding="utf-8")
    ]
    assert not stale, f"pending cards not yet upgraded to the standard: {stale}"


# -- review gate (card 61): reviewer verdicts are part of Done ----------------

# Cards from this id on may only sit in done/testing/production with recorded
# reviewer verdicts in '## Review evidence'. Earlier cards predate the gate.
REVIEW_GATE_FROM = 61

REVIEWED_STATES = ("done", "testing", "production")

# Bare non-answers ("None", "pending") are not evidence.
_BARE_PLACEHOLDER = re.compile(r"(?i)^-?\s*(?:pending|todo|tbd|none|not run)\.?$")


def _strip_comments(body: str) -> list[str]:
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return [line.strip() for line in body.splitlines() if line.strip()]


def _template_unfilled_lines() -> set[str]:
    """The template's own Review evidence list items ARE the unfilled state.

    Comparing card lines against the template verbatim (instead of guessing
    placeholder shapes with regexes) means: leftover template rows always
    fail, while real evidence containing angle brackets, 'PASS', or concrete
    severity tags always passes. Label lines ('Findings disposition:') may
    legitimately remain, so only list items count as unfilled.
    """
    template = (BACKLOG / "_TEMPLATE.md").read_text(encoding="utf-8")
    body = section(template, "## Review evidence")
    return {line for line in _strip_comments(body) if line.startswith("-")}


def _is_substantive(body: str) -> bool:
    lines = _strip_comments(body)
    if not lines:
        return False
    unfilled = _template_unfilled_lines()
    if any(line in unfilled for line in lines):
        return False  # a leftover unfilled template row voids the evidence
    return not all(_BARE_PLACEHOLDER.match(line) for line in lines)


def test_is_substantive_detects_unfilled_and_accepts_real_evidence():
    template_body = section(
        (BACKLOG / "_TEMPLATE.md").read_text(encoding="utf-8"), "## Review evidence"
    )
    # verbatim template copy: not evidence
    assert not _is_substantive(template_body)
    # partially filled (one real verdict, template rows left behind): not evidence
    partial = template_body.replace(
        "- code-reviewer: PASS | FAIL - <report ref or summary>",
        "- code-reviewer: PASS - report ok",
        1,
    )
    assert not _is_substantive(partial)
    # bare non-answer: not evidence
    assert not _is_substantive("\nNone.\n")
    # realistic filled evidence (angle brackets and severity tags are fine)
    filled = (
        "- code-reviewer: PASS - see <docker-run-log-2026-07-02>\n"
        "- test-auditor: PASS - mutation tests A-F executed\n"
        "Findings disposition:\n"
        "- [P2][code-001] regex false positive - fixed\n"
    )
    assert _is_substantive(filled)


def test_card_template_carries_review_evidence_section():
    template = (BACKLOG / "_TEMPLATE.md").read_text(encoding="utf-8")
    assert "## Review evidence" in template


def test_agent_index_documents_the_review_gate():
    text = (BACKLOG / "agent_index.md").read_text(encoding="utf-8")
    assert "Review evidence" in text
    assert "P0" in text and "P1" in text  # blocking severities are stated


def test_reviewed_states_require_substantive_review_evidence():
    missing = []
    for state in REVIEWED_STATES:
        for path in (BACKLOG / state).glob("[0-9]*_*.md"):
            if card_id(path) < REVIEW_GATE_FROM:
                continue
            body = section(path.read_text(encoding="utf-8"), "## Review evidence")
            if not _is_substantive(body):
                missing.append(f"{state}/{path.name}")
    assert not missing, (
        f"cards past the review gate lack substantive '## Review evidence': {missing}"
    )


# -- risk tier + decision log (card 110) --------------------------------------


def test_card_template_carries_risk_tier_and_decision_log():
    template = (BACKLOG / "_TEMPLATE.md").read_text(encoding="utf-8")
    assert "**Risk tier:**" in template
    assert "## Decision log" in template
    assert "## Closing commit" in template


def test_pending_cards_from_110_declare_risk_tier_and_decision_log():
    problems = []
    for path in upgraded_cards():
        if card_id(path) < ENFORCE_TIER_AND_LOG_FROM:
            continue
        text = path.read_text(encoding="utf-8")
        if "**Risk tier:**" not in text:
            problems.append(f"{path.name}: missing **Risk tier:**")
        elif not re.search(r"\*\*Risk tier:\*\*\s*T[0-3]\b", text):
            problems.append(f"{path.name}: Risk tier must be T0–T3")
        if "## Decision log" not in text:
            problems.append(f"{path.name}: missing ## Decision log")
        else:
            body = section(text, "## Decision log")
            lines = [
                line.strip()
                for line in re.sub(r"<!--.*?-->", "", body, flags=re.S).splitlines()
                if line.strip()
            ]
            has_entry = any(re.match(r"- \*\*D\d+", line) for line in lines)
            has_empty = any(
                line.startswith("No decisions: implementation followed the spec")
                for line in lines
            )
            if not (has_entry or has_empty):
                problems.append(
                    f"{path.name}: Decision log needs D<n> entries or the "
                    "explicit no-decisions line"
                )
    assert not problems, "\n".join(problems)


# -- agent workflows + Cursor adapter (card 110) ------------------------------


def test_agent_workflows_exist_with_lucy_signals():
    workflows = ROOT / "docs" / "agents" / "workflows"
    missing = [name for name in WORKFLOW_FILES if not (workflows / name).is_file()]
    assert not missing, f"missing workflow files: {missing}"
    risk = (workflows / "risk-tiers.md").read_text(encoding="utf-8")
    assert "media-gateway" in risk
    assert "plane boundary" in risk.lower() or "Plane boundary" in risk
    closing = (workflows / "closing-commit.md").read_text(encoding="utf-8")
    assert "do **not** auto-merge" in closing.lower() or "do not auto-merge" in closing.lower()
    assert "honor-system" in closing


def test_cursor_adapter_files_exist_without_foreign_model_ids():
    agents_dir = ROOT / ".cursor" / "agents"
    rules_dir = ROOT / ".cursor" / "rules"
    skills_dir = ROOT / ".cursor" / "skills"
    missing_agents = [
        name for name in CURSOR_AGENT_NAMES if not (agents_dir / f"{name}.md").is_file()
    ]
    missing_rules = [
        name for name in CURSOR_RULE_NAMES if not (rules_dir / f"{name}.mdc").is_file()
    ]
    missing_skills = [
        name
        for name in ("chip-tdd", "final-review", "closing-commit")
        if not (skills_dir / name / "SKILL.md").is_file()
    ]
    assert not missing_agents, f"missing Cursor agents: {missing_agents}"
    assert not missing_rules, f"missing Cursor rules: {missing_rules}"
    assert not missing_skills, f"missing Cursor skills: {missing_skills}"

    foreign = re.compile(r"\b(sonnet|haiku|gpt-5\.6-)\b")
    offenders = []
    for path in list(agents_dir.glob("*.md")) + list(rules_dir.glob("*.mdc")):
        text = path.read_text(encoding="utf-8")
        if foreign.search(text):
            offenders.append(path.as_posix())
    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    cursor_section = section(agents_md, "## Cursor subagent model routing")
    if not cursor_section:
        # AGENTS.md uses the same content under agents.md title; find by marker
        idx = agents_md.find("## Cursor subagent model routing")
        assert idx >= 0, "AGENTS.md missing Cursor subagent model routing section"
        cursor_section = agents_md[idx:]
        next_h = re.search(r"\n## ", cursor_section[3:])
        if next_h:
            cursor_section = cursor_section[: next_h.start() + 3]
    for needle in ("composer-2.5-fast", "cursor-grok-4.5-high-fast", "inherit"):
        assert needle in cursor_section, f"Cursor routing missing {needle}"
    # Foreign ids may still appear in the Codex section; Cursor section must not.
    cursor_only = cursor_section
    assert not re.search(r"\b(sonnet|haiku)\b", cursor_only)
    assert "gpt-5.6-" not in cursor_only
    assert not offenders, f"foreign model ids in Cursor adapter files: {offenders}"
