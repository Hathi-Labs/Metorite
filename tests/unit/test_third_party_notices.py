"""Ported Docmost code is marked and listed (D107, knowledge_base.md §21.1).

Docmost core is AGPL-3.0. The owner allowed ports on 2026-10-19. This test
holds three rules: a file with the marker is listed in the notice file, a
listed file exists, and no listed path comes from an enterprise folder.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
MARKER = "Adapted from Docmost"
HEAD_LINES = 30
EXTS = {".ts", ".tsx", ".js", ".mjs", ".css", ".py", ".sql"}
ENTERPRISE = ("apps/server/src/ee", "apps/client/src/ee", "packages/ee")
SELF = "tests/unit/test_third_party_notices.py"
BLOCK = re.compile(
    r"<!-- PORTED-FILES-START -->(.*?)<!-- PORTED-FILES-END -->", re.S
)


def _listed() -> list[tuple[str, str]]:
    text = NOTICES.read_text(encoding="utf-8")
    block = BLOCK.search(text)
    assert block, "THIRD_PARTY_NOTICES.md lost its PORTED-FILES markers"
    rows = []
    for line in block.group(1).splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        ours, _, theirs = (part.strip(" `") for part in line.partition(" <- "))
        rows.append((ours, theirs))
    return rows


def _marked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    found = []
    for rel in filter(None, out.split("\0")):
        if rel == SELF or Path(rel).suffix not in EXTS:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                head = [next(fh, "") for _ in range(HEAD_LINES)]
        except UnicodeDecodeError:
            continue
        if any(MARKER in line for line in head):
            found.append(rel)
    return found


def test_the_notice_file_exists_and_names_the_licence():
    text = NOTICES.read_text(encoding="utf-8")
    assert "AGPL-3.0" in text
    assert "apps/server/src/ee" in text


def test_every_marked_file_is_listed():
    listed = {ours for ours, _ in _listed()}
    missing = [rel for rel in _marked() if rel not in listed]
    assert not missing, f"Add these files to THIRD_PARTY_NOTICES.md: {missing}"


def test_every_listed_file_exists_and_carries_the_marker():
    marked = set(_marked())
    for ours, theirs in _listed():
        assert theirs, f"{ours}: name the path in the Docmost repo"
        assert (ROOT / ours).is_file(), f"{ours} is listed and does not exist"
        assert ours in marked, f"{ours} is listed and has no '{MARKER}' header"


def test_nothing_comes_from_an_enterprise_folder():
    for ours, theirs in _listed():
        for folder in ENTERPRISE:
            assert folder not in theirs, f"{ours} comes from {folder}"
            assert folder not in ours, f"{ours} sits in {folder}"
