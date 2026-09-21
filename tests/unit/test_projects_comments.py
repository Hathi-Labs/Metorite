"""Threaded comments — the depth cap, and the two lists the panel reads.

Spec: ``project-docs/specs/project_management_app.md`` §3.8 (the activity
spine). Migration ``208_projects_comment_replies.sql``.

Owner request, 2026-09-21, in two parts:

    "I think we should separate out activity and comments because the
    comments are getting muddled up with the activity."

    "Also enable threaded comments in the sense that we should be able to
    reply to certain comments. Limit the depth. I think just one layer of
    reply should be fine."

⚠️ **Most of the threading rule cannot be a database constraint, and this
file is where it lives instead.** A CHECK sees one row. Every interesting
question here is about the OTHER row — is it a comment, is it on this task,
is it already a reply — so migration 208 enforces only the pair it can see
(no self-parent, the parent exists) and ``_parent_comment`` enforces the
rest. R7: this file is that rule's fence.

⚠️ **What these tests are not.** They drive ``_parent_comment`` and read the
module as text. They do not run SQL. The ``kind`` filter builds its WHERE by
concatenation, and a hermetic fake agrees with whatever string it is handed
(R8) — ``tests/live/live_comment_threads.py`` is the half that runs against a
real Postgres, and the clause table below is checked for the SHAPE that makes
the concatenation safe rather than for the rows it returns.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from gateway.routes.projects import activities

REPO = Path(__file__).resolve().parents[2]
SOURCE = (
    REPO / "apps/services/gateway/gateway/routes/projects/activities.py"
).read_text(encoding="utf-8")
MIGRATION = (
    REPO / "infra/postgres/208_projects_comment_replies.sql"
).read_text(encoding="utf-8")

TASK = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_TASK = "aaaaaaaa-0000-0000-0000-000000000002"
ROOT = "bbbbbbbb-0000-0000-0000-000000000001"
REPLY = "bbbbbbbb-0000-0000-0000-000000000002"


def run(coro):
    return asyncio.run(coro)


class _Result:
    def __init__(self, row: Any):
        self._row = row

    def fetchone(self):
        return self._row


class _Db:
    """Answers the one SELECT ``_parent_comment`` makes."""

    def __init__(self, row: Any):
        self.row = row
        self.seen: list[str] = []

    async def execute(self, statement, params=None):
        self.seen.append(str(statement))
        return _Result(self.row)


def comment(**over: Any) -> SimpleNamespace:
    # Merged, not splatted after the defaults: `comment(parent_id=ROOT)` is
    # the whole point of the helper, and passing it twice is a TypeError.
    fields: dict[str, Any] = {
        "id": ROOT, "type": "comment", "task_id": TASK, "parent_id": None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


# ── The depth cap ────────────────────────────────────────────────────────


def _parent(row: Any, task_id: str = TASK, parent_id: str = ROOT):
    return activities._parent_comment(_Db(row), parent_id, task_id)


def test_a_top_level_comment_accepts_a_reply():
    """The case the feature exists for."""
    assert str(run(_parent(comment())).id) == ROOT


def test_a_reply_cannot_be_replied_to():
    """⚠️ THE cap, and the reason it is a refusal rather than a re-parent.

    Silently attaching the new comment to the grandparent would be the
    friendlier-looking choice. It is worse: the author picked what they were
    answering, the product would quietly answer something else, and nothing
    on the surface would ever tell them. An error they can read beats a lie
    they cannot see.
    """
    with pytest.raises(HTTPException) as caught:
        run(_parent(comment(id=REPLY, parent_id=ROOT), parent_id=REPLY))
    assert caught.value.status_code == 422
    assert "one level" in str(caught.value.detail).lower()


def test_you_cannot_reply_to_a_system_event():
    """A `status_change` is not something a person wrote.

    ⚠️ This is the rule migration 208 most obviously "should" carry and
    cannot: the type lives on the row being pointed AT, and a CHECK on the
    replying row never sees it.
    """
    with pytest.raises(HTTPException) as caught:
        run(_parent(comment(type="status_change")))
    assert caught.value.status_code == 422
    assert "only reply to a comment" in str(caught.value.detail)


def test_a_reply_cannot_cross_tasks():
    """Attached across tasks, a reply is invisible in one and orphaned in the
    other — the thread it belongs to never renders it."""
    with pytest.raises(HTTPException) as caught:
        run(_parent(comment(task_id=OTHER_TASK)))
    assert caught.value.status_code == 422
    assert "same task" in str(caught.value.detail)


def test_a_missing_parent_is_a_404_and_says_nothing_else():
    """A deleted parent and a wrong id are ONE answer.

    `_load_own_comment` takes the same line for the same reason: a 404 that
    distinguished "gone" from "never existed" would confirm the existence of
    rows the caller may not read.
    """
    with pytest.raises(HTTPException) as caught:
        run(_parent(None))
    assert caught.value.status_code == 404


def test_the_parent_lookup_withholds_deleted_rows_in_sql():
    """Not in Python, after the fact — a soft-deleted comment must never be
    a reply target, and the filter belongs in the query that finds it."""
    db = _Db(comment())
    run(activities._parent_comment(db, ROOT, TASK))
    assert "deleted_at IS NULL" in db.seen[0]


def test_the_cap_is_written_down_as_a_number():
    """Two places enforce one rule — this module, and the panel that offers
    Reply on a root and not on a reply. A named constant is what lets the
    two point at each other."""
    assert activities.MAX_COMMENT_DEPTH == 1


# ── Order of operations ──────────────────────────────────────────────────


def test_visibility_is_resolved_before_the_parent_is_looked_up():
    """⚠️ A leak that would be invisible in any functional test.

    Asked first, the parent lookup answers 404 or 422 for a task the caller
    may not see — and the difference between those two says whether a
    comment with that id exists. So the source order is the security
    property, and this reads it.
    """
    body = SOURCE[SOURCE.index("async def add_comment"):]
    body = body[: body.index("async def _load_own_comment")]
    assert body.index("load_visible_task") < body.index("_parent_comment")


def test_an_edit_cannot_re_parent_a_comment():
    """Moving somebody's reply under a different question changes what it
    appears to say. `CommentIn` carries `parent_id` because POST needs it;
    `edit_comment` must not spend it."""
    body = SOURCE[SOURCE.index("async def edit_comment"):]
    body = body[: body.index("async def delete_comment")]
    assert "parent_id" not in body


# ── The two lists ────────────────────────────────────────────────────────


def test_the_kind_filter_offers_exactly_three_reads():
    assert set(activities._KIND_CLAUSES) == {"all", "comments", "events"}


def test_all_stays_the_default_and_narrows_nothing():
    """Every caller written before 2026-09-21 omits `kind`, and none of them
    is asking for a narrowed stream."""
    assert activities._KIND_CLAUSES["all"] == ""
    assert "kind: str = \"all\"" in SOURCE


def test_the_two_halves_partition_the_stream():
    """Comments and events must be complementary, not merely different.

    An entry that fell into neither list would vanish from the product
    without ever erroring — the failure mode of splitting one stream in two.
    """
    assert activities._KIND_CLAUSES["comments"].strip() == "AND type = 'comment'"
    assert activities._KIND_CLAUSES["events"].strip() == "AND type <> 'comment'"


def test_each_clause_starts_with_a_space_and_the_conjunction():
    """The clause is CONCATENATED into a WHERE that already has a condition.

    A clause missing its leading space silently welds onto `NULL`, and one
    missing `AND` is a syntax error at runtime rather than here. Both are
    invisible to a hermetic fake, which is why the shape is asserted.
    """
    for kind, clause in activities._KIND_CLAUSES.items():
        if kind == "all":
            continue
        assert clause.startswith(" AND "), kind


def test_an_unknown_kind_is_refused_rather_than_ignored():
    """A typo must not silently return the whole stream — a client that
    thinks it asked for comments and got events would render events as
    comments."""
    assert "kind must be one of" in SOURCE


def test_the_count_is_narrowed_with_the_rows():
    """`total` drives the panel's "show N older" button. Counting the whole
    table while returning one kind makes that button promise rows that do
    not exist."""
    body = SOURCE[SOURCE.index("async def get_timeline"):]
    body = body[: body.index("@router.post")]
    assert body.count("+ clause") >= 1
    assert body.count("clause") >= 4


# ── The migration ────────────────────────────────────────────────────────


def test_the_migration_is_idempotent():
    assert "ADD COLUMN IF NOT EXISTS parent_id" in MIGRATION
    assert "CREATE INDEX IF NOT EXISTS idx_pm_activities_parent_id" in MIGRATION
    # The CHECK has no IF NOT EXISTS in Postgres, so it needs the DO block.
    assert "duplicate_object" in MIGRATION


def test_the_column_is_nullable_with_no_default_r6():
    """Expand/contract. Old code that never selects the column keeps working,
    which is what makes this safe to apply before the gateway restarts."""
    added = re.search(
        r"ADD COLUMN IF NOT EXISTS parent_id[^;]*;", MIGRATION, re.S
    )
    assert added is not None
    assert "NOT NULL" not in added.group(0)
    assert "DEFAULT" not in added.group(0)


def test_deleting_a_comment_does_not_destroy_other_peoples_replies():
    """⚠️ The ruling worth keeping. `ON DELETE CASCADE` reads as the tidy
    choice and is wrong twice — a comment delete here is a SOFT delete, which
    no foreign key ever sees, and making it fire would let one person's
    tidy-up silently remove somebody else's words."""
    # The STATEMENT, not the file — the header argues the ruling at length
    # and says "ON DELETE CASCADE" while rejecting it.
    added = re.search(r"ALTER TABLE pm_activities[^;]*parent_id[^;]*;", MIGRATION, re.S)
    assert added is not None
    assert "ON DELETE SET NULL" in added.group(0)
    assert "ON DELETE CASCADE" not in added.group(0)


def test_a_row_cannot_be_its_own_parent():
    assert "parent_id <> id" in MIGRATION
