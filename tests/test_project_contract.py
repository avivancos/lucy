from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_operating_docs_exist_and_name_lucy_runtime():
    required_docs = [
        "agents.md",
        "MEMORY.md",
        "backlog/agent_index.md",
        "backlog/_TEMPLATE.md",
    ]

    for path in required_docs:
        assert (ROOT / path).is_file(), path

    agents = read("agents.md")
    backlog_rules = read("backlog/agent_index.md")

    assert "Lucy" in agents
    assert "Docker Compose" in agents
    assert "Docker Compose" in backlog_rules


def test_project_placeholders_are_resolved():
    checked_paths = [
        "agents.md",
        "backlog/agent_index.md",
        "backlog/_TEMPLATE.md",
    ]

    unresolved = []
    placeholder_pattern = re.compile(r"<[A-Z_]+>")
    for path in checked_paths:
        for match in placeholder_pattern.finditer(read(path)):
            unresolved.append((path, match.group(0)))

    assert unresolved == []


def test_backlog_state_folders_exist_with_markers():
    states = [
        "pending",
        "in_progress",
        "done",
        "need_human_testing",
        "testing",
        "production",
        "decisions",
    ]

    for state in states:
        folder = ROOT / "backlog" / state
        assert folder.is_dir(), state
        assert (folder / ".gitkeep").is_file(), state


def test_bootstrap_contract_is_complete():
    template = read("backlog/_TEMPLATE.md")
    agents = read("agents.md")
    backlog_rules = read("backlog/agent_index.md")

    for heading in [
        "## Goal",
        "## Spec",
        "## Files to create/modify",
        "## Definition of Done",
        "## Improvements noted",
        "## Pending human testing",
    ]:
        assert heading in template

    assert "Spec -> red test -> green code -> refactor -> docs -> move" in agents
    assert "Spec -> red test -> green code -> refactor -> docs" in backlog_rules
    assert "no-mocks project" in agents
    assert "Docker Compose" in agents

    assert (ROOT / "docs/adr/0001-python-first-rust-hot-paths.md").is_file()
    assert (ROOT / "docs/adr/0002-nextjs-dashboard.md").is_file()
