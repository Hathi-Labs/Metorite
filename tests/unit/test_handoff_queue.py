"""Structural fence over `project-docs/HANDOFF.md` — the cross-session queue.

HANDOFF.md states its own id rule, in the template block near the top:

    Ids are never reused. Delete the whole block — do not tick it off in
    place, or this file grows a graveyard and the graveyard is what goes
    stale.

Until now that rule named no test, so under R7 it was advisory — and it broke
twice in two days. Both breaks came from the same mechanism: two branches in
flight each mint "the next free id" against the `main` they were cut from, and
whichever merges second carries an id `main` has since taken. The merge is
CLEAN — the two entries sit in different parts of the file and git has no
reason to object — so nothing surfaces it.

    2026-08-25  H-27 collided (PR #47 took it) — caught by hand, renumbered
                to H-32.
    2026-08-25  H-28 collided (PR #46 took it) — NOT caught; both entries
                shipped to `main` in PR #91 and the session-start hook listed
                two different obligations under one id.

That is the defect this file exists to prevent: the hook (D39) injects the
queue into every session, so a duplicate id means "do H-28" resolves to two
different pieces of work, and closing one reads as closing the other.

The fence is deliberately structural rather than an example. It re-derives
every id from the document on each run, so a new collision fails the moment it
is committed — which is the merge-time re-check R1 does for migration numbers,
applied to the other counter this repo mints across branches.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFF = REPO_ROOT / "project-docs" / "HANDOFF.md"

# `### H-12 · <title> · [AGENT]`. The numeric group is load-bearing: the
# template block near the top of the file is a literal `### H-<n> · ...` inside
# a code fence, and it must not read as an entry.
ENTRY_RE = re.compile(r"^### H-(\d+)\s*·", re.MULTILINE)


def _text() -> str:
    # utf-8 explicitly — the file carries `·`, `⚠️` and `→`, and cp1252 is the
    # Windows default (CLAUDE.md §6).
    return HANDOFF.read_text(encoding="utf-8")


def test_handoff_file_exists_and_has_entries() -> None:
    """A fence that silently matches nothing passes forever.

    If the file is moved or the heading shape changes, every assertion below
    becomes vacuously true. This is the tripwire that makes that visible.
    """
    assert HANDOFF.is_file(), f"{HANDOFF} is missing — the queue moved?"
    ids = ENTRY_RE.findall(_text())
    assert len(ids) >= 5, (
        f"only {len(ids)} handoff entries parsed from {HANDOFF.name} — the "
        "heading shape probably changed and this fence has gone blind"
    )


def test_handoff_ids_are_unique() -> None:
    """`Ids are never reused` — HANDOFF.md's own rule, now with a fence.

    Fails naming the collision, because the fix depends on WHICH id doubled:
    the entry that arrived second renumbers to the next free id and records the
    move in its `Added:` line (the H-27→H-32 precedent), so the reviewer needs
    the number, not just the fact.
    """
    ids = ENTRY_RE.findall(_text())
    dupes = sorted(
        (int(n) for n, count in Counter(ids).items() if count > 1),
    )
    assert not dupes, (
        "handoff ids are reused: "
        + ", ".join(f"H-{n} (x{ids.count(str(n))} times)" for n in dupes)
        + ". Two branches minted the same 'next free id' against different "
        "bases. Renumber the entry that merged SECOND to the next free id and "
        "note the move in its `Added:` line — ids are never reused, so do not "
        "reclaim a number by deleting the other entry."
    )
