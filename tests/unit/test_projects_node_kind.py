"""Migration 193 — folders in the projects tree, and the grammar that caps it.

Owner directive 2026-08-31: *space → [folder] → project → [folder] →
subproject*, and stop. Projects count toward depth (space=1, project=2,
subproject=3). Folders are transparent to depth, never nest, never hold
tasks, and are never a root.

Two halves, matching the run-state suite's split:

* **The mirror** — ``NODE_KINDS`` is a hand-written copy of the CHECK in the
  migration, so a test reads the SQL file (the migration-150 lesson: a
  mirrored constraint without such a test is a comment claiming to be an
  invariant). Found by CONTENT, never by number (R1).
* **The grammar** — ``assert_node_grammar`` is a pure function, exercised
  here refusal by refusal. The SQL that feeds it its numbers
  (``_project_generation``, ``_subtree_project_depth``) is a recursive walk;
  a hermetic fake would agree with any SQL (R8), so those helpers are
  covered by the create/move round-trips in the R8 suites, not here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects.core import (
    MAX_PROJECT_GENERATIONS,
    NODE_KINDS,
    assert_node_grammar,
    node_kind,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _kind_migration() -> Path:
    """The migration that adds ``pm_projects.kind``, whatever its number."""
    adds = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        # The constraint name, not the column add: two OTHER tables also
        # carry a `kind` column (38_, 129_), and this suite is about the
        # projects tree only.
        and "pm_projects_kind_check" in path.read_text(encoding="utf-8")
    ]
    assert len(adds) == 1, f"expected exactly one migration to add kind, got {adds}"
    return adds[0]


# ── The mirror ──────────────────────────────────────────────────────────────

def test_node_kinds_mirrors_the_check_in_the_migration():
    sql = _kind_migration().read_text(encoding="utf-8")
    match = re.search(
        r"ADD CONSTRAINT pm_projects_kind_check\s*"
        r"CHECK \(kind IN \((.*?)\)\)",
        sql, re.S,
    )
    assert match, "the kind CHECK is not in the migration"
    assert sorted(NODE_KINDS) == sorted(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_column_is_r6_shaped():
    """Nullable with a default — never NOT NULL in the expanding release."""
    sql = _kind_migration().read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT 'project'" in sql
    assert "NOT NULL" not in sql.replace("-- ", "")


def test_null_kind_reads_as_project():
    assert node_kind(None) == "project"
    assert node_kind("") == "project"
    assert node_kind("folder") == "folder"


# ── The grammar, refusal by refusal ─────────────────────────────────────────
#
# parent_generation: 0 = creating a root, 1 = under a space, 2 = under a
# project (or a folder inside one — folders report their project ancestor's
# count), 3 = under a subproject.

def _refused(**kwargs) -> str:
    with pytest.raises(HTTPException) as err:
        assert_node_grammar(**kwargs)
    assert err.value.status_code == 422
    return str(err.value.detail)


def test_the_happy_paths_all_pass():
    # A space; a project under a space; a subproject under a project.
    for gen in range(MAX_PROJECT_GENERATIONS):
        assert_node_grammar(
            kind="project", parent_kind="project" if gen else None,
            parent_generation=gen, subtree_depth=1,
        )
    # A folder under a space, and one under a project.
    for gen in (1, 2):
        assert_node_grammar(
            kind="folder", parent_kind="project",
            parent_generation=gen, subtree_depth=0,
        )
    # A project created inside a folder — the folder is transparent.
    assert_node_grammar(
        kind="project", parent_kind="folder",
        parent_generation=1, subtree_depth=1,
    )


def test_a_subproject_is_the_floor():
    detail = _refused(
        kind="project", parent_kind="project",
        parent_generation=3, subtree_depth=1,
    )
    assert "lowest level" in detail


def test_a_folder_cannot_be_a_root():
    detail = _refused(
        kind="folder", parent_kind=None, parent_generation=0, subtree_depth=0,
    )
    assert "space" in detail


def test_a_folder_cannot_hold_a_folder():
    detail = _refused(
        kind="folder", parent_kind="folder",
        parent_generation=1, subtree_depth=0,
    )
    assert "another folder" in detail


def test_an_empty_folder_under_a_subproject_is_still_refused():
    """The folder reserves a generation for the children it exists to hold."""
    detail = _refused(
        kind="folder", parent_kind="project",
        parent_generation=3, subtree_depth=0,
    )
    assert "floor" in detail


def test_a_move_carries_the_subtree_shape():
    # A project WITH subprojects (depth 2) cannot land under a project…
    _refused(
        kind="project", parent_kind="project",
        parent_generation=2, subtree_depth=2,
    )
    # …but lands fine under a space, and at the root.
    assert_node_grammar(
        kind="project", parent_kind="project",
        parent_generation=1, subtree_depth=2,
    )
    assert_node_grammar(
        kind="project", parent_kind=None,
        parent_generation=0, subtree_depth=2,
    )
    # A folder carrying project→subproject chains (depth 2) fits under a
    # space and nowhere deeper.
    assert_node_grammar(
        kind="folder", parent_kind="project",
        parent_generation=1, subtree_depth=2,
    )
    _refused(
        kind="folder", parent_kind="project",
        parent_generation=2, subtree_depth=2,
    )
