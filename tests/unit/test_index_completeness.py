"""Every spec is named in `INDEX.md`. R7's fence for CLAUDE.md §5 (H-57).

CLAUDE.md §5: *"Do not add product specs to `docs/` or leave a new spec out of
`INDEX.md` — a spec enters the index in the PR that creates it."*
`INDEX.md`'s own header: *"A spec missing from INDEX is a defect — say so."*

Both statements were **advisory** until this file existed, and R7 calls that a
defect in itself: a rule with no test is a rule that degrades silently. Measured
2026-08-26 while adding `operator_identity_and_access.md`.

⚠️ **This checks presence, never classification.** Whether a spec belongs under
ACTIVE, DEFERRED or HISTORICAL is a judgement about the work, and a test that
guessed would be wrong in the interesting cases. The gap this closes is narrower
and real: a spec that no section mentions **at all** is invisible to every agent
that navigates from the index, and the author is the last person able to notice.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "project-docs" / "INDEX.md"
SPECS = ROOT / "project-docs" / "specs"


def _index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


def _spec_files() -> list[pathlib.Path]:
    return sorted(SPECS.glob("*.md"))


def test_the_index_and_the_spec_folder_both_exist() -> None:
    """If either moved, every assertion below would pass while checking nothing."""
    assert INDEX.is_file(), f"{INDEX} is gone — this fence now guards nothing"
    assert SPECS.is_dir(), f"{SPECS} is gone — this fence now guards nothing"
    assert _spec_files(), "no specs found; the glob is wrong, not the tree"


@pytest.mark.parametrize("spec", _spec_files(), ids=lambda p: p.name)
def test_every_spec_is_named_in_the_index(spec: pathlib.Path) -> None:
    """🔴 A spec the index does not name is invisible to every agent.

    ⚠️ **Name the file in FULL.** A shorthand like `(+ _implementation)` reads
    as complete to a human and as a gap to this test — which is the one reader
    that cannot ask what was meant. `saas_multitenancy_implementation.md` was
    exactly that case, and expanding the row was part of closing H-57.
    """
    assert spec.name in _index_text(), (
        f"{spec.name} is not named in project-docs/INDEX.md.\n"
        "CLAUDE.md §5: a spec enters the index in the PR that creates it.\n"
        "Add a row under ACTIVE, DEFERRED or HISTORICAL — whichever is true — "
        "and name the file in full rather than by a suffix shorthand."
    )


def test_the_index_names_no_spec_that_is_gone() -> None:
    """The other direction. A row pointing at a deleted file sends an agent to
    a path that does not exist, and it reads as authoritative on the way.

    Only `specs/`-prefixed mentions are checked. The index legitimately names
    files elsewhere (`work_plan.md`, `../FOUNDATION_BUILDOUT_CHECKLIST.md`,
    `docs/multiplayer/…`), and this test owns one folder.
    """
    import re

    named = set(re.findall(r"`specs/([A-Za-z0-9_./-]+\.md)`", _index_text()))
    on_disk = {p.name for p in _spec_files()}
    dangling = sorted(n for n in named if "/" not in n and n not in on_disk)

    assert not dangling, (
        f"project-docs/INDEX.md names {dangling}, which no longer exist in "
        "project-docs/specs/. Delete the row or fix the filename."
    )
