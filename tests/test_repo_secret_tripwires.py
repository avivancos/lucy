# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
"""Tripwires: committed sources must not contain scanner-shaped secret literals."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAN_ROOTS = (
    "src",
    "tests",
    "examples",
    "docs",
    "packages",
    "media-gateway-rust/src",
    "media-gateway-rust/tests",
)

TEXT_SUFFIXES = {".py", ".rs", ".md", ".yml", ".yaml", ".toml", ".env.example"}

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
)


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for relative in SCAN_ROOTS:
        base = ROOT / relative
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix in TEXT_SUFFIXES or base.name == ".env.example":
                files.append(base)
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.name.endswith(".lock"):
                continue
            if path.suffix in TEXT_SUFFIXES or path.name == ".env.example":
                files.append(path)
    return files


def test_committed_tree_has_no_github_pat_literals() -> None:
    violations: list[str] = []
    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                violations.append(f"{path.relative_to(ROOT)} ({label})")
    assert not violations, "forbidden secret-shaped literals:\n" + "\n".join(violations)
