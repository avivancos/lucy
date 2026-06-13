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


def pending_cards() -> list[Path]:
    return sorted((BACKLOG / "pending").glob("[0-9]*_*.md"))


def card_id(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def upgraded_cards() -> list[Path]:
    return [p for p in pending_cards() if "**Sprint:**" in p.read_text(encoding="utf-8")]


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
        listed.extend(int(n) for n in re.findall(r"\b(\d{2})\b", row))
    duplicated = sorted({n for n in listed if listed.count(n) > 1})
    assert not duplicated, f"cards mapped to more than one sprint: {duplicated}"
    orphans = []
    for state in STATE_DIRS:
        for path in (BACKLOG / state).glob("[0-9]*_*.md"):
            cid = card_id(path)
            if cid >= 17 and cid not in listed:
                orphans.append(path.name)
    assert not orphans, f"cards not mapped to any sprint in sprints.md: {orphans}"


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
            problems.append(f"{path.name}: sprint {sprint_match.group(1)} not in sprints.md")
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
