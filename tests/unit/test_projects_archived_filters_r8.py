"""WS-64 — an archived project must not show in the main lists (R8).

The sidebar tree returns archived rows on purpose, and it splits them out.
Every OTHER read must leave them out. Three reads are fenced here, against a
real Postgres:

* the node roll-up (``NODE_DESCENDANTS_SQL`` and ``node_counts_sql``),
* the portfolio roll-up (the route drops archived projects from its forest),
* ``MY_TASKS_FROM``, behind Inbox, Today, Next and the calendar.

Hermetic tests for the route guards sit in ``test_projects_archived_guards.py``.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from gateway.routes.projects.core import CLOSING_CATEGORIES
from gateway.routes.projects.personal import MY_TASKS_FROM
from gateway.routes.projects.tree import (
    NODE_DESCENDANTS_ARCHIVED_SQL,
    NODE_DESCENDANTS_SQL,
    fold_node_summary,
    node_counts_sql,
)

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402

pytestmark = pytest.mark.skipif(
    not _TENANT_URL, reason="TENANT_LADDER_DATABASE_URL unset - R8 needs Postgres",
)

WHO = "archived-r8@example.test"


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _project(c, org, name, parent=None, archived=False, personal=None):
    pid = str(c.execute(
        text(
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, parent_project_id, owns_statuses,"
            " archived_at, personal_owner)"
            " VALUES (:n,'active','manual',:w, CAST(:o AS uuid),'Asia/Kolkata',"
            " CAST(:par AS uuid), true,"
            " CASE WHEN :arch THEN now() END, :po) RETURNING id"
        ),
        {"n": f"{name}-{uuid.uuid4().hex[:6]}", "w": WHO, "o": org,
         "par": parent, "arch": archived, "po": personal},
    ).scalar_one())
    status = str(c.execute(
        text(
            "INSERT INTO pm_task_statuses (project_id,name,color,position,"
            " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
            " RETURNING id"
        ),
        {"p": pid},
    ).scalar_one())
    return pid, status


def _task(c, org, root, pid, status):
    c.execute(
        text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id,"
            " status_id, created_by, organization_id, task_number)"
            " SELECT 't', CAST(:p AS uuid), CAST(:r AS uuid),"
            " CAST(:s AS uuid), :w, CAST(:o AS uuid),"
            " COALESCE(MAX(task_number),0)+1 FROM pm_tasks"
            " WHERE root_project_id = CAST(:r AS uuid)"
        ),
        {"p": pid, "r": root, "s": status, "w": WHO, "o": org},
    )


@pytest.fixture
def world(db):
    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        root, _ = _project(c, org, "ar-root")
        live, live_s = _project(c, org, "ar-live", parent=root)
        gone, gone_s = _project(c, org, "ar-gone", parent=root, archived=True)
        under, under_s = _project(c, org, "ar-under", parent=gone, archived=True)
        mine, mine_s = _project(c, org, "ar-mine", personal=WHO)
        mine_gone, mine_gone_s = _project(
            c, org, "ar-mine-gone", parent=mine, personal=WHO, archived=True,
        )
        for pid, st, r in (
            (live, live_s, root), (gone, gone_s, root), (under, under_s, root),
            (mine, mine_s, mine), (mine_gone, mine_gone_s, mine),
        ):
            _task(c, org, r, pid, st)
        made.update(root=root, live=live, gone=gone, under=under,
                    mine=mine, mine_gone=mine_gone)
    yield made
    ids = [made[k] for k in ("root", "live", "gone", "under", "mine", "mine_gone")]
    with db.begin() as c:
        for sql in (
            "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM pm_tasks"
            " WHERE project_id = ANY(CAST(:p AS uuid[])))",
            "DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))",
            "DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))",
        ):
            c.execute(text(sql), {"p": ids})
        for key in ("under", "gone", "live", "root", "mine_gone", "mine"):
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": made[key]},
            )


def test_node_summary_lists_and_counts_only_active_children(db, world):
    with db.begin() as c:
        descendants = c.execute(
            text(NODE_DESCENDANTS_SQL), {"pid": world["root"]},
        ).fetchall()
        ids = [world["root"]] + [str(r.id) for r in descendants]
        rows = c.execute(
            text(node_counts_sql("TRUE")),
            {"ids": ids, "closed": sorted(CLOSING_CATEGORIES)},
        ).fetchall()
    got = fold_node_summary(world["root"], rows, descendants)
    assert [c["id"] for c in got["children"]] == [world["live"]]
    # One task in the live child. The archived child and ITS child add none.
    assert got["tasks"] == 1


def _summary(db, node, archived_node: bool):
    sql = NODE_DESCENDANTS_ARCHIVED_SQL if archived_node else NODE_DESCENDANTS_SQL
    with db.begin() as c:
        descendants = c.execute(text(sql), {"pid": node}).fetchall()
        ids = [node] + [str(r.id) for r in descendants]
        rows = c.execute(
            text(node_counts_sql("TRUE")),
            {"ids": ids, "closed": sorted(CLOSING_CATEGORIES)},
        ).fetchall()
    return fold_node_summary(node, rows, descendants)


def test_the_dashboard_of_an_archived_space_still_lists_its_projects(db):
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        space, _ = _project(c, org, "arsp", archived=True)
        a, a_s = _project(c, org, "arsp-a", parent=space, archived=True)
        b, b_s = _project(c, org, "arsp-b", parent=space, archived=True)
        _task(c, org, space, a, a_s)
        _task(c, org, space, b, b_s)
        _task(c, org, space, b, b_s)
    try:
        got = _summary(db, space, archived_node=True)
        assert sorted(c["id"] for c in got["children"]) == sorted([a, b])
        assert got["tasks"] == 3
        # The active walk would hide them, which is the bug this fences.
        assert _summary(db, space, archived_node=False)["children"] == []
    finally:
        ids = [a, b, space]
        with db.begin() as c:
            p = {"p": ids}
            c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"), p)
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"), p)
            for pid in ids:
                c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": pid})


def test_the_portfolio_route_drops_archived_projects_from_its_forest():
    """The route filters the rows it reads, so a task of an archived project
    has no space and is skipped. Pinned by source, because the route needs a
    full request to run."""
    import gateway.routes.projects.tree as tree_mod

    src = Path(tree_mod.__file__).read_text(encoding="utf-8")
    at = src.index("async def get_portfolio_summary(")
    assert 'getattr(r, "archived_at", None) is None' in src[at:at + 4000]


def test_my_work_leaves_out_tasks_of_an_archived_project(db, world):
    org = world["org"]

    def projects(archived: bool) -> set[str]:
        with db.begin() as c:
            rows = c.execute(
                text("SELECT t.project_id " + MY_TASKS_FROM),
                {"who": WHO, "vis_org": org, "vis_email": WHO,
                 "vis_groups": [], "archived": archived},
            ).fetchall()
        return {str(r.project_id) for r in rows}

    live = projects(False)
    assert world["mine"] in live
    assert world["mine_gone"] not in live
    # The archive view asks for archived rows, and still sees them.
    assert world["mine_gone"] in projects(True)


def test_reports_analytics_leave_out_an_archived_projects_overdue_work(db, world):
    """``load_open_where`` carries ``reportable_with_ancestors_clause``, and that
    walk drops an archived project. Overdue-by-project and workload both run on
    it, so the archived project has no row and no count (WS-64 verifier, 3)."""
    from gateway.routes.projects.analytics import (
        load_open_where,
        load_params,
        load_sql,
        overdue_by_project_sql,
    )
    from gateway.routes.projects.core import Visibility

    org = world["org"]
    vis = Visibility(
        unrestricted=True, email=WHO, groups=(), organization_id=org,
    )
    both = [world["live"], world["gone"]]
    with db.begin() as c:
        c.execute(
            text(
                "UPDATE pm_tasks SET due_at = now() - interval '2 days'"
                " WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"p": both},
        )
        c.execute(
            text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                " SELECT id, :w, :w FROM pm_tasks"
                " WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"w": WHO, "p": both},
        )
    where = (
        load_open_where("TRUE", vis)
        + " AND t.project_id = ANY(CAST(:ids AS uuid[]))"
    )
    params = {**load_params(vis, None), "ids": both}
    with db.begin() as c:
        overdue = c.execute(text(overdue_by_project_sql(where)), params).fetchall()
        load = c.execute(text(load_sql(where)), params).fetchall()
    assert {str(r.project_id) for r in overdue} == {world["live"]}
    assert sum(r.overdue for r in load) == 1
