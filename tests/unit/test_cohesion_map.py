"""The cohesion map of `building_blocks.md` §10 stays honest (R7).

Spec: `project-docs/specs/building_blocks.md` §10 · D235 · Metorite task 4163 (CI-0).

The map gives each kind of knowledge ONE home. A row that names no fence, or
names a fence as built (V) when the file does not exist, is a promise that no
test keeps. This fence reads the map and fails on both.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "project-docs" / "specs" / "building_blocks.md"
TEST_NAME = re.compile(r"`(test_[a-z0-9_]+\.py)`\s*\((V|P)\b")


def _section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    end = text.find(next_heading, start + len(heading))
    return text[start:] if end < 0 else text[start:end]


def _map_rows() -> list[list[str]]:
    text = SPEC.read_text(encoding="utf-8")
    body = _section(text, "### 10.1 The map", "### 10.2")
    rows = []
    for line in body.splitlines():
        if not line.startswith("| ") or line.startswith("| The thing") or line.startswith("|---"):
            continue
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def test_the_fence_still_sees_the_map():
    """A parser that finds nothing passes forever."""
    assert len(_map_rows()) >= 10, "the map of §10.1 lost rows, or its shape changed. This fence went blind."


def test_each_row_has_one_home_and_a_fence_or_says_advisory():
    for cells in _map_rows():
        assert len(cells) == 4, f"a map row needs 4 cells: {cells[0]!r}"
        thing, home, never, fence = cells
        assert home and never, f"the row {thing!r} needs its home and its 'never kept in' cell"
        assert "advisory" in fence or TEST_NAME.search(fence), (
            f"the row {thing!r} names no fence. Name a test with (V) or (P, <slice>), or say advisory (R7)."
        )


def test_each_built_fence_exists():
    missing = []
    for cells in _map_rows():
        for name, state in TEST_NAME.findall(cells[3]):
            if state == "V" and not (ROOT / "tests" / "unit" / name).exists():
                missing.append(name)
    assert not missing, f"the map marks these fences as built (V), and they do not exist: {missing}"


def test_the_two_blocks_of_d235_are_in_the_block_table():
    text = SPEC.read_text(encoding="utf-8")
    blocks = _section(text, "## 2. The building blocks", "## 3.")
    for name in ("**Memory**", "**Roles and reporting lines**"):
        assert f"| {name} |" in blocks, f"the block {name} of D235.3 left the table of §2"
