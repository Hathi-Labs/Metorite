"""The subtree roll-up `GET /projects/tree` carries for the completion wheel.

Owner directive 2026-09-23: *"replace the green-circle dot with a completion
wheel in the projects app... The progress wheel should be adaptive."*

The tree returned `status` per row and no counts at all, so the sidebar had
nothing to draw a wheel from. It now stamps `tasks` and `done` on every node.

🔴 **The rule most likely to be got wrong is the SUBTREE one.** A project's
wheel has to mean "how much of this project is finished", and for a project
with subprojects the work lives in the children. A roll-up that counted a
node's own rows only would sit a parent at 0% while every child under it was
complete — the one reading nobody would expect, and one that looks like data
rather than a bug.

⚠️ These numbers must agree with `nodes/{id}/summary`, because the sidebar and
the dashboard sit on the same screen. Same join, same `CLOSING_CATEGORIES`,
same visibility clause. Measured against a real gateway on 2026-09-23:
Engineering 8/1, Metorite Platform 8/1, Hardware 0/0 — tree and summary alike.
"""

from __future__ import annotations

import os

import pytest
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    Visibility,
)
from sqlalchemy import text

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks)
USER = projects_user()

ASYNC = pytest.mark.asyncio


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _find(rows: list[dict], name: str) -> dict:
    for row in rows:
        if row["name"] == name:
            return row
        hit = _find(row.get("children") or [], name)
        if hit:
            return hit
    return {}


# -- the pure roll-up, without a database in the way ------------------------

def test_a_parent_counts_its_whole_subtree():
    """The rule the wheel rests on."""
    tree = [{
        "id": "space", "children": [
            {"id": "proj", "children": [
                {"id": "sub", "children": []},
            ]},
        ],
    }]
    counts = {"proj": (4, 1, 0), "sub": (6, 5, 0)}

    pm_tree._attach_progress(tree, counts)

    space = tree[0]
    proj = space["children"][0]
    sub = proj["children"][0]
    assert (sub["tasks"], sub["done"]) == (6, 5)
    # 🔴 The parent adds its child. Counting its own rows only would put this
    # project at 1 of 4 while 5 of the 6 tasks beneath it are finished.
    assert (proj["tasks"], proj["done"]) == (10, 6)
    assert (space["tasks"], space["done"]) == (10, 6)


def test_cancelled_work_rolls_up_too_and_stays_out_of_done():
    """🔴 The P1: an abandoned task is not a delivered one.

    The client subtracts `cancelled` from the DENOMINATOR, so the roll-up has
    to carry it up the tree beside the other two. Folding it into `done` — the
    first version of this — filled the ring for a project whose work was all
    abandoned, beside a dashboard reading 0%.
    """
    tree = [{"id": "space", "children": [{"id": "proj", "children": []}]}]
    counts = {"proj": (10, 4, 4), "space": (2, 0, 2)}

    pm_tree._attach_progress(tree, counts)

    proj = tree[0]["children"][0]
    assert (proj["tasks"], proj["done"], proj["cancelled"]) == (10, 4, 4)
    # The space adds its own two abandoned rows to the child's four.
    assert (tree[0]["tasks"], tree[0]["done"], tree[0]["cancelled"]) == (12, 4, 6)


def test_a_node_with_nothing_under_it_reports_zero_not_none():
    """`0`, `0` and `0`, so the client tells "no work" from "no answer"."""
    tree = [{"id": "empty", "children": []}]
    pm_tree._attach_progress(tree, {})
    assert tree[0]["tasks"] == 0
    assert tree[0]["done"] == 0
    assert tree[0]["cancelled"] == 0


def test_a_count_for_a_node_outside_the_tree_is_ignored():
    """A task in a project the forest read did not return - a personal
    project, say - belongs to no row here. The alternative is a total that
    no line on screen adds up to."""
    tree = [{"id": "visible", "children": []}]
    pm_tree._attach_progress(
        tree, {"visible": (2, 1, 0), "somewhere-else": (99, 99, 9)},
    )
    assert (tree[0]["tasks"], tree[0]["done"]) == (2, 1)


# -- the SQL, on the driver production actually uses (R8) ---------------------
#
# `FakeProjectsDB` cannot evaluate `GROUP BY ... count(*) FILTER (...)`, and
# teaching it to would re-implement the counting rule in Python and assert
# against the mirror - the harness header's own warning, and what R8 exists to
# prevent. So the roll-up above is fenced hermetically and the STATEMENT is
# fenced here, against a real Postgres on asyncpg.


#: The two callers the clause has arms for. A restricted caller is the one
#: whose clause carries the grant-closure subquery, so it is the one that can
#: refuse on asyncpg — and it was the one the old literal never exercised.
_VIS_SHAPES = [
    pytest.param(True, id="unrestricted"),
    pytest.param(False, id="restricted"),
]


@pytest.mark.skipif(
    not os.environ.get("TENANT_LADDER_DATABASE_URL"),
    reason="TENANT_LADDER_DATABASE_URL unset - R8 needs a real Postgres",
)
@pytest.mark.parametrize("unrestricted", _VIS_SHAPES)
@ASYNC
async def test_the_count_query_runs_on_asyncpg(unrestricted: bool):
    """Either the driver accepts the statement and its binds, or it raises.

    Asserts nothing about the answer: the tenant database this runs against is
    whatever the ladder left. What is under test is the shape - a `FILTER` over
    a text[] bind is exactly the fragment that passes on psycopg and can refuse
    on asyncpg (H-114).

    ⚠️ **Runs the statement production BUILDS, not a copy of it.** This test
    used to carry its own hand-written SQL, and that copy had lost
    `AND (<visibility clause>)` along with every `vis_*` bind. It proved a
    simplified query runs while the real one reached no database in any suite
    - and the visibility clause is the half with the subquery and the array
    binds, so it is the half most likely to refuse. `progress_statement()`
    exists to close that gap.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    vis = Visibility(
        unrestricted=unrestricted,
        email="rollup-fence@example.com",
        groups=("engineering",),
        organization_id="00000000-0000-0000-0000-000000000001",
    )
    sql, params = pm_tree.progress_statement(vis)
    # The fence is worth nothing if the clause silently dropped out again.
    assert "vis_org" in sql
    assert params["done"] == [COMPLETED_CATEGORY]
    assert "cancelled" not in params["done"]

    url = os.environ["TENANT_LADDER_DATABASE_URL"].replace("+psycopg", "+asyncpg")
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(text(sql), params)).fetchall()
        # The column NAMES are what `_open_and_done` reads off each row. A
        # rename here reads as an AttributeError three layers away.
        for row in rows:
            assert row.total is not None
            assert row.done is not None
            assert row.cancelled is not None
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("TENANT_LADDER_DATABASE_URL"),
    reason="TENANT_LADDER_DATABASE_URL unset - R8 needs a real Postgres",
)
@ASYNC
async def test_a_cancelled_task_is_counted_apart_from_a_done_one():
    """🔴 The P1, against a real database rather than against the fake.

    The ring counted every CLOSING category as delivered, so an abandoned task
    filled it. `NodeDashboard` prints `done / (tasks - cancelled)` on the same
    screen, so the two disagreed by a thumb's width. This asserts the two
    filters cannot both match one row.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    vis = Visibility(
        unrestricted=True,
        email="rollup-fence@example.com",
        groups=(),
        organization_id="00000000-0000-0000-0000-000000000001",
    )
    sql, params = pm_tree.progress_statement(vis)
    url = os.environ["TENANT_LADDER_DATABASE_URL"].replace("+psycopg", "+asyncpg")
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(text(sql), params)).fetchall()
        for row in rows:
            # Disjoint by construction: `done` and `abandoned` partition the
            # closing categories, so their sum can never exceed the total.
            assert row.done + row.cancelled <= row.total
    finally:
        await engine.dispose()
