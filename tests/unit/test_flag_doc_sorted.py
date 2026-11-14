"""The flag paragraphs of docs/flag_reload.md stay sorted by flag name.

CIV-33 (task 3284, docs/merge_hotspots.md). A PR that adds a flag puts its
paragraph at its sorted place, not at the tail. Two PRs then edit different
lines and merge with no conflict, unless their flag names are neighbours.
"""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "flag_reload.md"


def _flag_blocks() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## The flags that reload")
    end = text.index("A reload does NOT apply any other key")
    blocks = text[start:end].split("\n\n")[2:]  # the heading, then the intro paragraph
    return [b for b in blocks if b.strip()]


def test_each_flag_paragraph_starts_with_its_flag_name():
    for b in _flag_blocks():
        assert re.match(r"`[A-Z][A-Z0-9_]+`", b), b[:60]


def test_the_flag_paragraphs_are_sorted_by_flag_name():
    names = [re.match(r"`([A-Z][A-Z0-9_]+)`", b).group(1) for b in _flag_blocks()]
    assert names == sorted(names), "put the paragraph of a new flag at its sorted place"
    assert len(names) == len(set(names))
