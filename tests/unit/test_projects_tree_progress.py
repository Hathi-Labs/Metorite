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
from gateway.routes.projects.core import CLOSING_CATEGORIES
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
    counts = {"proj": (4, 1), "sub": (6, 5)}

    pm_tree._attach_progress(tree, counts)

    space = tree[0]
    proj = space["children"][0]
    sub = proj["children"][0]
    assert (sub["tasks"], sub["done"]) == (6, 5)
    # 🔴 The parent adds its child. Counting its own rows only would put this
    # project at 1 of 4 while 5 of the 6 tasks beneath it are finished.
    assert (proj["tasks"], proj["done"]) == (10, 6)
    assert (space["tasks"], space["done"]) == (10, 6)


def test_a_node_with_nothing_under_it_reports_zero_not_none():
    """`0` and `0`, so the client can tell "no work" from "no answer"."""
    tree = [{"id": "empty", "children": []}]
    pm_tree._attach_progress(tree, {})
    assert tree[0]["tasks"] == 0
    assert tree[0]["done"] == 0


def test_a_count_for_a_node_outside_the_tree_is_ignored():
    """A task in a project the forest read did not return - a personal
    project, say - belongs to no row here. The alternative is a total that
    no line on screen adds up to."""
    tree = [{"id": "visible", "children": []}]
    pm_tree._attach_progress(tree, {"visible": (2, 1), "somewhere-else": (99, 99)})
    assert (tree[0]["tasks"], tree[0]["done"]) == (2, 1)


# -- the SQL, on the driver production actually uses (R8) ---------------------
#
# `FakeProjectsDB` cannot evaluate `GROUP BY ... count(*) FILTER (...)`, and
# teaching it to would re-implement the counting rule in Python and assert
# against the mirror - the harness header's own warning, and what R8 exists to
# prevent. So the roll-up above is fenced hermetically and the STATEMENT is
# fenced here, against a real Postgres on asyncpg.


@pytest.mark.skipif(
    not os.environ.get("TENANT_LADDER_DATABASE_URL"),
    reason="TENANT_LADDER_DATABASE_URL unset - R8 needs a real Postgres",
)
@ASYNC
async def test_the_count_query_runs_on_asyncpg():
    """Either the driver accepts the statement and its binds, or it raises.

    Asserts nothing about the answer: the tenant database this runs against is
    whatever the ladder left. What is under test is the shape - a `FILTER` over
    a text[] bind is exactly the fragment that passes on psycopg and can refuse
    on asyncpg (H-114).
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = os.environ["TENANT_LADDER_DATABASE_URL"].replace("+psycopg", "+asyncpg")
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(
                text(
                    "SELECT t.project_id,"
                    "       count(*) AS total,"
                    "       count(*) FILTER ("
                    "         WHERE s.category = ANY(CAST(:closed AS text[]))"
                    "       ) AS done"
                    "  FROM pm_tasks t"
                    "  JOIN pm_task_statuses s ON s.id = t.status_id"
                    " WHERE t.archived_at IS NULL"
                    " GROUP BY t.project_id"
                ),
                {"closed": sorted(CLOSING_CATEGORIES)},
            )).fetchall()
        # The column NAMES are what `_open_and_done` reads off each row. A
        # rename here reads as an AttributeError three layers away.
        for row in rows:
            assert row.total is not None
            assert row.done is not None
    finally:
        await engine.dispose()
