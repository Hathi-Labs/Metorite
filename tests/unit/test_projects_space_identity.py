"""Migration 194 — the four levels, and what each one is allowed to be.

Owner directive 2026-08-31:

* a **space** is not a project. It shows a roll-up of everything beneath it
  and none of a project's views;
* a space carries its own **icon** and **hue**, set in Space Settings;
* **only a project or a subproject** can be started, paused or stopped.

Split the same way as ``test_projects_node_kind.py``: the constants that
mirror the migration are checked against the SQL file (found by CONTENT,
never by number — R1), and the level rules are exercised as pure functions.
The roll-up SQL itself is two recursive walks and a grouped count, which a
hermetic fake would agree with whatever it was handed (R8) — it is proven
against a real database by the create/move round-trips in the R8 suites.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects.core import (
    ICON_SLOT_RANGE,
    NODE_LEVELS,
    RUN_STATE_LEVELS,
    assert_run_state_allowed,
    node_level,
    validate_icon_slot,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _identity_migration() -> Path:
    """The migration ADDING the columns, whatever it ends up numbered."""
    adds = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "ADD COLUMN IF NOT EXISTS icon_slot" in path.read_text(encoding="utf-8")
    ]
    assert len(adds) == 1, f"expected exactly one migration to add it, got {adds}"
    return adds[0]


def _check_migration() -> Path:
    """The migration whose CHECK is CURRENT — the last to (re)define it.

    Migration 195 widened 194's range (drop, then re-add), so the range of
    record is the highest-numbered file naming the constraint. Applying the
    files in order lands exactly there.
    """
    defines = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "ADD CONSTRAINT pm_projects_icon_slot_check"
        in path.read_text(encoding="utf-8")
    ]
    assert defines, "no migration defines pm_projects_icon_slot_check"
    return defines[-1]


# ── The mirror ──────────────────────────────────────────────────────────────

def test_the_columns_are_r6_shaped():
    """Nullable, no default, no NOT NULL in the expanding release."""
    sql = _identity_migration().read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS icon TEXT;" in sql
    assert "ADD COLUMN IF NOT EXISTS icon_slot SMALLINT;" in sql
    # Comment prose legitimately says "NOT NULL"; the DDL must not.
    ddl = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    assert "NOT NULL" not in ddl


def test_the_slot_range_matches_the_twelve_slot_ramp():
    """The CHECK is 1..12 — the ramp `src/lib/categorical.ts` declares.

    A thirteenth slot accepted here would store a value whose class
    (`bg-cat-13`) has no custom property behind it. That declaration
    resolves to nothing and takes the whole rule with it, so the icon would
    silently lose its colour rather than fail. (1..8 until migration 195
    widened it — slots 9..12 are choice-only, the hash stays modulo 8.)
    """
    sql = _check_migration().read_text(encoding="utf-8")
    match = re.search(
        r"CHECK \(icon_slot IS NULL OR "
        r"\(icon_slot >= (\d+) AND icon_slot <= (\d+)\)\)",
        sql,
    )
    assert match, "the icon_slot range CHECK is not in the migration"
    assert (int(match.group(1)), int(match.group(2))) == (1, 12)


def test_a_colour_is_never_stored():
    """A NAME and a SLOT. A hex value here is unreachable by any re-theme.

    Scoped to the DDL, like the R6 check above: the migration's own header
    quotes `#7c3aed` as the thing it refuses, and a fence that failed on
    its own rationale would be a fence against documentation.
    """
    sql = _identity_migration().read_text(encoding="utf-8")
    ddl = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    assert not re.search(r"#[0-9a-fA-F]{6}\b", ddl)


def test_the_future_team_binding_is_recorded_not_invented():
    """The migration names where the team/Center note lives (owner ask)."""
    sql = _identity_migration().read_text(encoding="utf-8")
    assert "pm_project_grants" in sql
    assert "project_management_app.md" in sql


# ── The levels ──────────────────────────────────────────────────────────────

def test_node_level_maps_kind_and_generation():
    assert node_level("project", 1) == "space"
    assert node_level("project", 2) == "project"
    assert node_level("project", 3) == "subproject"
    # A folder is a folder wherever it sits.
    for generation in (1, 2, 3):
        assert node_level("folder", generation) == "folder"


def test_a_null_kind_still_levels_correctly():
    """R6 — an untouched row carries no kind and is still a real level."""
    assert node_level(None, 1) == "space"  # type: ignore[arg-type]
    assert node_level(None, 2) == "project"  # type: ignore[arg-type]


def test_every_level_is_in_the_vocabulary():
    for generation in (1, 2, 3):
        assert node_level("project", generation) in NODE_LEVELS
    assert node_level("folder", 1) in NODE_LEVELS


def test_only_a_project_or_subproject_owns_a_run_state():
    assert RUN_STATE_LEVELS == {"project", "subproject"}
    assert_run_state_allowed("project")
    assert_run_state_allowed("subproject")


@pytest.mark.parametrize("level", ["space", "folder"])
def test_a_space_and_a_folder_refuse_a_run_state(level):
    """Refused, never ignored: a silently dropped field answers 200."""
    with pytest.raises(HTTPException) as err:
        assert_run_state_allowed(level)
    assert err.value.status_code == 422
    assert level in str(err.value.detail)


def test_a_slot_outside_the_ramp_is_refused_at_the_door():
    """422, not the 500 the CHECK constraint alone produced.

    Measured 2026-08-31: `icon_slot: 9` reached Postgres, came back as an
    IntegrityError and was answered as a server fault — an input error the
    caller could do nothing with.
    """
    for bad in (0, 13, -1, 99, "3", 2.5, True):
        with pytest.raises(HTTPException) as err:
            validate_icon_slot(bad)
        assert err.value.status_code == 422
    # NULL is "not chosen", and every good slot passes.
    validate_icon_slot(None)
    for good in range(1, 13):
        validate_icon_slot(good)


def test_the_validator_and_the_migration_agree_on_the_range():
    """A hand-written range beside a CHECK needs a test that reads the SQL."""
    sql = _check_migration().read_text(encoding="utf-8")
    match = re.search(
        r"\(icon_slot >= (\d+) AND icon_slot <= (\d+)\)", sql,
    )
    assert match
    assert ICON_SLOT_RANGE == (int(match.group(1)), int(match.group(2)))


def test_the_refusal_says_what_the_level_actually_does():
    """A folder groups; it does not summarise. Wrong words teach wrong models."""
    with pytest.raises(HTTPException) as space:
        assert_run_state_allowed("space")
    assert "summarises" in str(space.value.detail)
    with pytest.raises(HTTPException) as folder:
        assert_run_state_allowed("folder")
    assert "groups" in str(folder.value.detail)
