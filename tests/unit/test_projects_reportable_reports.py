"""D-PM-32(b) against a real database: a stopped project leaves the reports.

⚠️ **This is the test the spec promised and did not have.**
`test_projects_reportable.py` asserts the PYTHON helpers — `is_reportable`
against `REPORTABLE_STATUSES`. That is a mirror: it would pass unchanged if
the SQL clause were never applied to a single endpoint, which is exactly the
state the first round of this work shipped in. `test_projects_move_sql_asyncpg`
proves the clause PARSES on the production driver and asserts nothing about
which rows come back.

So nothing tested the thing the owner actually asked for:

    "those particular tasks stop appearing or should not appear in any report
     or anything because the project is stopped"

This runs the real clause over real rows and asserts the ANSWER.

## What it deliberately does NOT assert

The historical reads — `/analytics/throughput`, `/analytics/finished` and the
weekly report's `past_where` — keep a stopped project's FINISHED work. That is
not an oversight, it is the same rule the tree already applies to archiving:
"an archive sweep in September must not empty July". Stopping a project must
not retroactively rewrite a week we have already sent. `test_the_history_
predicate_is_untouched` pins that, so a later reading of "any report" cannot
quietly widen the clause onto the past without a test turning red.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects.core import (
    REPORTABLE_STATUSES,
    reportable_project_clause,
    reportable_with_ancestors_clause,
)
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset. ⚠️ Without it D-PM-32(b) has no"
        " behavioural fence at all — only the Python mirror, which agrees"
        " with itself."
    ),
)

_PARAMS = {"reportable_states": sorted(REPORTABLE_STATUSES)}


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def tree(db):
    """Four projects covering every run state the decision names, plus the
    case the ancestor walk exists for: an ACTIVE child of a STOPPED parent.

    Function-scoped and torn down by hand, because every assertion here counts
    rows and one test's leftovers are another's wrong answer.
    """
    made: dict[str, str] = {}
    tag = uuid.uuid4().hex[:6]
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(key: str, status: str, parent: str | None = None) -> None:
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id,"
                    " owns_statuses)"
                    " VALUES (:n, :st, 'manual', 'rep@example.test',"
                    " CAST(:o AS uuid), 'Asia/Kolkata',"
                    " CAST(:par AS uuid), true) RETURNING id"
                ),
                {"n": f"{key}-{tag}", "st": status, "o": org, "par": parent},
            ).scalar_one())

        project("active", "active")
        project("paused", "on_hold")
        project("queued", "queued")
        project("stopped", "stopped")
        # ⚠️ ACTIVE itself. Its parent is stopped, and that is the whole
        # reason the walk is recursive rather than a single-row check.
        project("child_of_stopped", "active", made["stopped"])

        for key in ("active", "paused", "queued", "stopped",
                    "child_of_stopped"):
            made[f"lane:{key}"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color,"
                    " position, category) VALUES (CAST(:p AS uuid), 'To do',"
                    " 'gray', 0, 'todo') RETURNING id"
                ),
                {"p": made[key]},
            ).scalar_one())
            made[f"task:{key}"] = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number)"
                    " VALUES (:t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'rep@example.test',"
                    " CAST(:o AS uuid), 1) RETURNING id"
                ),
                {"t": f"task in {key}", "p": made[key],
                 "s": made[f"lane:{key}"], "o": org},
            ).scalar_one())

    yield made

    with db.begin() as c:
        ids = [made[k] for k in
               ("child_of_stopped", "active", "paused", "queued", "stopped")]
        for table in ("pm_tasks", "pm_task_statuses"):
            c.execute(
                text(f"DELETE FROM {table}"
                     f" WHERE project_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": ids},
            )
        # Children first: `parent_project_id` is a real reference.
        for pid in ids:
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": pid},
            )


def _reportable_titles(db, tree) -> set[str]:
    """Every task the clause lets through, by title."""
    with db.begin() as c:
        rows = c.execute(
            text(
                f"SELECT t.title FROM pm_tasks t"
                f" WHERE t.organization_id = CAST(:o AS uuid)"
                f"   AND t.project_id = ANY(CAST(:ids AS uuid[]))"
                f"   AND ({reportable_with_ancestors_clause('t')})"
            ),
            {
                "o": tree["org"],
                "ids": [tree[k] for k in
                        ("active", "paused", "queued", "stopped",
                         "child_of_stopped")],
                **_PARAMS,
            },
        ).fetchall()
    return {r.title for r in rows}


def test_a_stopped_projects_work_leaves_the_report(db, tree):
    """The owner's sentence, as an assertion."""
    assert "task in stopped" not in _reportable_titles(db, tree)


def test_a_paused_projects_work_STAYS(db, tree):
    """The other half, and the one that is easy to get wrong.

    Hiding a stalled project from the one surface that would reveal the stall
    is how a quarter of its work goes missing. Paused is not abandoned.
    """
    assert "task in paused" in _reportable_titles(db, tree)


def test_queued_and_active_work_stays_too(db, tree):
    got = _reportable_titles(db, tree)
    assert "task in active" in got
    assert "task in queued" in got


def test_an_ACTIVE_subproject_of_a_stopped_root_is_stopped_work(db, tree):
    """The ancestor walk, and the reason it is recursive.

    A single-row check on the task's own project passes this task — it is in
    an `active` project. The work is still not happening, because the root
    above it was stopped. Slice 1's guards each read only the immediate
    project and disagreed with the UI for exactly this shape.
    """
    assert "task in child_of_stopped" not in _reportable_titles(db, tree)


def test_the_two_predicates_disagree_on_paused_IN_SQL(db, tree):
    """D-PM-32 rests on the claim that one predicate cannot answer both.

    `test_projects_reportable.py` asserts this about the Python helpers. This
    asserts it about the SQL, which is the half that actually runs in a
    report. If someone ever collapses the two, this fails rather than a
    paused project quietly vanishing from every dashboard.
    """
    with db.begin() as c:
        reportable = c.execute(
            text(
                f"SELECT count(*) AS n FROM pm_projects p"
                f" WHERE p.id = CAST(:p AS uuid)"
                f"   AND ({reportable_project_clause('p')})"
            ),
            {"p": tree["paused"], **_PARAMS},
        ).scalar_one()
        runnable = c.execute(
            text(
                "SELECT count(*) AS n FROM pm_projects p"
                " WHERE p.id = CAST(:p AS uuid)"
                "   AND p.status = 'active' AND p.archived_at IS NULL"
            ),
            {"p": tree["paused"]},
        ).scalar_one()

    assert reportable == 1, "a paused project must still COUNT"
    assert runnable == 0, "a paused project must not be ACTED on"


def test_the_history_predicate_is_untouched(db, tree):
    """⚠️ A guard against over-applying the decision, not a feature.

    The historical reads keep a stopped project's finished work, because a
    report describes the past and stopping a project in September must not
    empty July. This pins the carve-out so that widening the clause onto
    `past_where` turns a test red instead of silently rewriting numbers we
    have already sent.
    """
    with db.begin() as c:
        got = c.execute(
            text(
                "SELECT count(*) AS n FROM pm_tasks t"
                " WHERE t.project_id = CAST(:p AS uuid)"
            ),
            {"p": tree["stopped"]},
        ).scalar_one()
    assert got == 1, (
        "the stopped project's row is still THERE — D-PM-32(b) filters what a "
        "report counts and never deletes or rewrites history"
    )


# ── The drift this decision actually failed on ──────────────────────────────


def test_every_open_work_predicate_carries_the_clause():
    """⚠️ The first round applied D-PM-32(b) to ONE of five reads.

    Not because the rule was misunderstood — because `open_where` is built
    from scratch in four places, each with a comment claiming it is "the same
    predicate `stuck` uses". They were copies, so adding a clause to one
    changed one. The spec said the scope was every report, the code covered
    `/analytics/stuck`, and every test passed.

    This is a source scan rather than a behavioural test on purpose: the
    defect is a MISSING call site, and no amount of exercising the four
    existing ones can see the fifth that somebody adds next.

    ⚠️ It reads `open_where` only. `past_where` and `scope_where` are the
    historical predicates and must NOT carry the clause — see
    `test_the_history_predicate_is_untouched`.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    base = root / "apps/services/gateway/gateway/routes/projects"
    missing: list[str] = []

    for name in ("analytics.py", "reports.py"):
        src = (base / name).read_text(encoding="utf-8")
        # Each assignment runs to the closing paren of its parenthesised
        # f-string chain, which is the first line that is exactly 8 spaces
        # and a `)`.
        for match in re.finditer(r"^        open_where = \(\n", src, re.M):
            tail = src[match.end():]
            body = tail[: tail.index("\n        )\n")]
            if "reportable_with_ancestors_clause" not in body:
                line = src[: match.start()].count("\n") + 1
                missing.append(f"{name}:{line}")

    assert not missing, (
        "an open-work predicate does not carry D-PM-32(b): "
        + ", ".join(missing)
        + ". A STOPPED project's tasks would appear in whatever this read "
        "serves. Add `reportable_with_ancestors_clause('t')` and the "
        "`reportable_states` parameter beside it — or, if this read "
        "describes the PAST, name it `past_where` like the others do."
    )


def test_the_scan_can_actually_find_something():
    """A scan that matches nothing passes forever.

    `test_every_open_work_predicate_carries_the_clause` is only worth having
    if its regex still finds the assignments after somebody reformats the
    module. This is the tripwire for that.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    base = root / "apps/services/gateway/gateway/routes/projects"
    found = sum(
        len(re.findall(r"^        open_where = \(\n",
                       (base / name).read_text(encoding="utf-8"), re.M))
        for name in ("analytics.py", "reports.py")
    )
    assert found >= 4, (
        f"the open_where scan matched {found} assignments, expected at least"
        " 4 (stuck, load, outlook, the weekly report) — the shape changed and"
        " the fence above has gone blind"
    )
