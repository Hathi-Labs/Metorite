"""CIV-25 item 3: files with `merge=union` are append-only lists, and none repeats an item.

The union driver keeps both sides of a clash, so a duplicate can slip in.
INDEX.md has its own fence (test_index_union.py).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"project-docs/INDEX.md", "project-docs/HANDOFF.md", ".env.example"}


def _union_paths():
    out = []
    for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.lstrip().startswith("#") and "merge=union" in parts[1:]:
            out.append(parts[0])
    return out


def test_only_reviewed_append_only_files_use_union():
    """A status table or a prose spec must never use union: it would keep both versions of an edited row."""
    assert set(_union_paths()) == ALLOWED


def test_no_handoff_id_repeats():
    ids = re.findall(r"^### (H-\d+)\b", (ROOT / "project-docs/HANDOFF.md").read_text(encoding="utf-8"), re.M)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    assert not dups, f"HANDOFF.md repeats {dups} (union merge?)"


def test_no_env_key_repeats():
    keys = [ln.split("=", 1)[0].strip() for ln in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f".env.example repeats {dups} (union merge?)"
