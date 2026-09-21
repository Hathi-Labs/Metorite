"""Merging tasks — the rules that are arithmetic, and one mirror that bites.

Spec: ``project-docs/specs/project_management_app.md`` §11.18. Migration
``210_projects_task_merge.sql``.

⚠️ **This file deliberately does NOT test the merge.** The merge is twelve
foreign keys' worth of UPDATE and DELETE, and a hermetic fake agrees with
whichever SQL it is handed (R8). ``tests/live/live_task_merge.py`` is where
that lives, and it runs 31 checks against a real Postgres.

What IS here: the pure folding rules, which are decisions rather than SQL,
and the two mirrors that a database cannot check from the Python side.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from gateway.routes.projects import merge as pm_merge
from gateway.routes.projects.core import ACTIVITY_TYPES

REPO = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO / "infra/postgres/210_projects_task_merge.sql"
).read_text(encoding="utf-8")


def task(**over: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": "t-target", "task_number": 1, "title": "Target",
        "description": None, "importance": None, "estimate_mins": None,
        "start_date": None, "due_at": None, "tags": [], "custom_fields": {},
        "project_id": "p1", "merged_into_task_id": None, "archived_at": None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


# ── The scalar folding rules ─────────────────────────────────────────────


def test_priority_takes_the_higher():
    """Folding an urgent task into a normal one does not make the urgent
    work less urgent. Higher is more important (3 Urgent, 0 Low)."""
    got = pm_merge._fold_scalars(task(importance=1), [task(importance=3)])
    assert got["importance"] == 3


def test_no_priority_loses_to_any_priority():
    """`None` is "no priority", not zero. It must not win a max(), and it
    must not be treated as a value the target then has to keep."""
    # The source says Low, the target says nothing: Low is the answer.
    assert pm_merge._fold_scalars(task(), [task(importance=0)])["importance"] == 0
    # Neither says anything: nothing to write.
    assert "importance" not in pm_merge._fold_scalars(task(), [task()])
    # The target already holds the winner: also nothing to write, because a
    # merge that changes nothing must not post a `field_change`.
    assert "importance" not in pm_merge._fold_scalars(task(importance=0), [task()])


def test_estimates_sum():
    """Two tasks' work is still two tasks' work."""
    got = pm_merge._fold_scalars(task(estimate_mins=60), [task(estimate_mins=30)])
    assert got["estimate_mins"] == 90


def test_a_missing_estimate_contributes_nothing_rather_than_zero():
    got = pm_merge._fold_scalars(task(estimate_mins=60), [task()])
    assert "estimate_mins" not in got  # unchanged, so not written


def test_the_due_date_takes_the_EARLIEST():
    """🔴 The judgement worth arguing with, so it is written down.

    Latest would be the intuitive reading — the combined task is bigger, so
    surely it finishes later. It is the wrong one. A due date is a
    commitment somebody made to somebody else, and merging is an internal
    tidying act. If either half was due on Friday, Friday is still the
    promise; quietly moving it to the later of the two relaxes a deadline
    nobody agreed to relax.
    """
    from datetime import datetime, timezone

    early = datetime(2026, 10, 1, tzinfo=timezone.utc)
    late = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert pm_merge._fold_scalars(task(due_at=late), [task(due_at=early)])["due_at"] == early
    # And it does not move when the target already holds the earlier one.
    assert "due_at" not in pm_merge._fold_scalars(task(due_at=early), [task(due_at=late)])


# ── Tags ─────────────────────────────────────────────────────────────────


def test_tags_union_and_fold_on_case():
    """`pm_tags` is case-insensitive elsewhere, so `Alpha` and `alpha` are
    one tag here too — otherwise a merge is how a project grows a second
    spelling of a tag it already had."""
    got = pm_merge._union_tags(task(tags=["alpha"]), [task(tags=["Alpha", "beta"])])
    assert got == ["alpha", "beta"]


def test_tag_order_is_kept_rather_than_sorted():
    """A member's tag order on the task they kept is a thing they chose."""
    got = pm_merge._union_tags(task(tags=["zulu", "alpha"]), [task(tags=["mike"])])
    assert got == ["zulu", "alpha", "mike"]


def test_blank_tags_are_dropped():
    assert pm_merge._union_tags(task(tags=["  ", ""]), [task(tags=["a"])]) == ["a"]


# ── Description ──────────────────────────────────────────────────────────


def test_the_description_appends_under_a_rule_naming_the_source():
    got = pm_merge._merged_description(
        task(description="Mine."),
        [task(id="s", task_number=7, title="Theirs", description="Yours.")],
    )
    assert got is not None
    assert got.startswith("Mine.")
    assert "#7 Theirs" in got
    assert got.endswith("Yours.")


def test_nothing_anywhere_leaves_the_column_alone():
    """`None`, not `""`. Writing an empty string turns "never described" into
    "described as nothing", which reads differently in every list."""
    assert pm_merge._merged_description(task(), [task()]) is None


def test_a_source_with_no_prose_adds_no_heading():
    got = pm_merge._merged_description(task(description="Mine."), [task()])
    assert got == "Mine."


# ── Custom fields ────────────────────────────────────────────────────────


def test_a_custom_answer_on_the_target_is_never_overwritten():
    """The target is the task being kept. A merge must not silently change
    an answer its owner gave."""
    got = pm_merge._fold_scalars(
        task(custom_fields={"team": "firmware"}),
        [task(custom_fields={"team": "radio"})],
    )
    assert "custom_fields" not in got


def test_but_a_blank_one_is_filled_in():
    got = pm_merge._fold_scalars(
        task(custom_fields={"team": None}),
        [task(custom_fields={"team": "radio", "risk": "high"})],
    )
    assert got["custom_fields"] == {"team": "radio", "risk": "high"}


def test_a_merge_that_changes_nothing_writes_nothing():
    """So it posts no `field_change` and bumps no delta cursor."""
    assert pm_merge._fold_scalars(task(), [task()]) == {}


# ── The refusals that are not SQL ────────────────────────────────────────


class _Result:
    def __init__(self, row: Any):
        self._row = row

    def fetchone(self):
        return self._row


class _Db:
    """Answers the one SELECT `load_visible_task` makes."""

    def __init__(self, row: Any):
        self.row = row

    async def execute(self, statement, params=None):
        return _Result(self.row)


def _mergeable(source: Any, target: Any):
    import asyncio

    from gateway.routes.projects.core import Visibility

    vis = Visibility(unrestricted=True, email="me@x.com", groups=(),
                     organization_id="org-1")
    return asyncio.run(
        pm_merge._load_mergeable(_Db(source), vis, str(source.id), target)
    )


def test_a_cross_project_merge_is_refused_by_name():
    """The owner's ruling, 2026-09-21. Projects own their statuses, custom
    fields and task types, so this is a Move plus a merge — and the message
    has to say so, or it reads as a bug rather than a rule."""
    with pytest.raises(HTTPException) as caught:
        _mergeable(task(id="s", task_number=9, project_id="p2"), task(project_id="p1"))
    assert caught.value.status_code == 422
    assert "different project" in str(caught.value.detail)
    assert "Move" in str(caught.value.detail)


def test_a_task_already_merged_cannot_be_merged_again():
    with pytest.raises(HTTPException) as caught:
        _mergeable(task(id="s", task_number=9, merged_into_task_id="x"), task())
    assert caught.value.status_code == 422
    assert "already been merged" in str(caught.value.detail)


def test_a_same_project_source_is_accepted():
    """The case the feature exists for, so the refusals above mean something."""
    got = _mergeable(task(id="s", task_number=9), task())
    assert str(got.id) == "s"


def test_the_source_is_loaded_through_the_visibility_seam():
    """⚠️ A source the caller cannot see must not be merged into one they
    can — that would move another team's comments onto their task. The check
    is that `_load_mergeable` goes through `load_visible_task` rather than
    reading the row directly."""
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("async def _load_mergeable"):]
    body = body[: body.index("def _merged_description")]
    assert "load_visible_task" in body


def test_the_target_is_checked_before_any_source_is_touched():
    """Merging into a stub is refused up front. Discovering it half way
    would leave some sources moved and some not."""
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("async def merge_tasks"):]
    assert body.index("merged_into_task_id") < body.index("_load_mergeable")


# ── The two mirrors ──────────────────────────────────────────────────────


def test_the_activity_vocabulary_matches_the_migration():
    """🔴 This mirror bit during the build, and the migration's own header
    names the trap: `record_activity` refuses a type its CHECK does not
    list, and the CHECK was widened while the Python tuple was not. The
    merge then 422'd at the last statement, after every row had moved.

    Two lists of the same words in two languages is a mirror, and mirrors go
    stale. This is the only thing that makes it fail here rather than in
    somebody's merge.
    """
    written = re.search(
        r"ADD CONSTRAINT pm_activities_type_check\s*CHECK \(type IN \(([^)]*)\)\)",
        MIGRATION,
        re.S,
    )
    assert written is not None, "the migration no longer widens the CHECK here"
    in_sql = set(re.findall(r"'([a-z_]+)'", written.group(1)))
    assert in_sql == set(ACTIVITY_TYPES), (
        f"only in SQL: {in_sql - set(ACTIVITY_TYPES)}; "
        f"only in Python: {set(ACTIVITY_TYPES) - in_sql}"
    )


def test_merge_is_a_legal_activity_type():
    assert "merge" in ACTIVITY_TYPES


# ── The migration ────────────────────────────────────────────────────────


def test_a_merged_task_is_always_archived():
    """🔴 THE invariant, and the owner's objection made concrete.

    The first design hid merged tasks with a rule of their own. The owner:
    *"what happens when we want to delete or archive a task? There is no way
    to do that because it'll be unseen in the UI."* A row nothing lists is a
    row nobody can manage.

    So a merged task is an ARCHIVED task, and this CHECK is what stops a
    future endpoint setting the pointer without the shelf.
    """
    assert "pm_tasks_merged_is_archived" in MIGRATION
    assert "merged_into_task_id IS NULL OR archived_at IS NOT NULL" in MIGRATION


def test_deleting_the_target_does_not_delete_the_stub():
    added = re.search(
        r"ADD COLUMN IF NOT EXISTS merged_into_task_id[^;]*;", MIGRATION, re.S
    )
    assert added is not None
    assert "ON DELETE SET NULL" in added.group(0)
    assert "ON DELETE CASCADE" not in added.group(0)


def test_the_migration_is_idempotent():
    assert "ADD COLUMN IF NOT EXISTS merged_into_task_id" in MIGRATION
    assert "CREATE INDEX IF NOT EXISTS idx_pm_tasks_merged_into" in MIGRATION
    assert MIGRATION.count("duplicate_object") == 2
    assert "DROP CONSTRAINT IF EXISTS pm_activities_type_check" in MIGRATION


def test_the_columns_are_nullable_with_no_default_r6():
    for column in ("merged_into_task_id", "merged_at", "merged_by"):
        added = re.search(
            rf"ADD COLUMN IF NOT EXISTS {column}[^;]*;", MIGRATION, re.S
        )
        assert added is not None, column
        assert "NOT NULL" not in added.group(0), column
        assert "DEFAULT" not in added.group(0), column


# ── What adversarial review found, kept as rules ─────────────────────────


def test_no_satellite_is_moved_blind():
    """🔴 Every satellite move is conditional, and none deletes.

    The first draft split the tables in two: four moved with a blind UPDATE
    under the claim they had "no key that could collide", and three
    de-duplicated by DELETING the source's row. Both halves were wrong
    against a real database — `pm_intake.task_id` is UNIQUE so a merge of two
    captured tasks answered 500, and `pm_task_personal` holds a member's
    Calendar block and tracked actuals, which the de-dupe destroyed.

    One rule now: move a row only where the target has none for the same key.
    This reads the source, because "does it still delete" is the question,
    and a functional test of a merge that happens to avoid a collision would
    pass either way.
    """
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("async def _move_satellites"):]
    body = body[: body.index("async def _move_links")]
    assert "NOT EXISTS" in body, "the move is unconditional again"
    # ⚠️ `DELETE FROM`, not `DELETE`. The docstring above the function
    # explains at length what it no longer deletes, and a looser check reads
    # its own documentation and fails on the explanation. That exact shape
    # has now cost this repo three tests in one day.
    assert "DELETE FROM" not in body.upper(), "a satellite move must not delete"


def test_the_intake_row_has_no_second_half_to_its_key():
    """`pm_intake.task_id` is UNIQUE on its own, so `None` is the right
    entry — not a column name that would make the clash test always false."""
    assert ("pm_intake", None) in pm_merge._MOVE


def test_the_block_cycle_guard_is_the_EXISTING_one():
    """⚠️ Not a second implementation. `assert_no_block_cycle` is bounded,
    tested, and refuses the same state the link endpoint refuses — a merge
    writing a cycle the rest of the app forbids is the defect review found."""
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    assert "from gateway.routes.projects.relations import assert_no_block_cycle" in source
    body = source[source.index("async def _move_links"):]
    body = body[: body.index("async def _descends_from")]
    assert "assert_no_block_cycle" in body


def test_the_ancestor_test_walks_rather_than_peeking_at_the_parent():
    """🔴 The first draft asked only "is its parent the source", which misses
    the grandchild — and merging into one's own grandchild made two tasks
    each other's parent."""
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("async def _descends_from"):]
    body = body[: body.index("async def _reparent_children")]
    assert "MAX_DEPTH" in body, "an unbounded walk is a denial-of-service surface"
    assert "range(" in body


def test_stubs_are_re_pointed_so_no_chain_can_form():
    """🔴 Merge A into B, then B into C. Without this A aims at a stub and
    its old link opens an empty task — the one promise merging makes."""
    source = (
        REPO / "apps/services/gateway/gateway/routes/projects/merge.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("async def merge_tasks"):]
    assert "WHERE merged_into_task_id = CAST(:src AS uuid)" in body
