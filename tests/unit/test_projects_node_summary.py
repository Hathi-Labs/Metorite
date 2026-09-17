"""The node roll-up behind every dashboard — and the tasks it used to lose.

Spec: ``project-docs/specs/project_management_app.md`` §5.1, §9.12.7.

⚠️ **Until this file existed, NOTHING tested ``GET /nodes/{id}/summary``** —
not on the server, not in the client. It is the single endpoint behind every
space, folder, project and subproject dashboard. That is how the hole below
lived from 2026-08-31 to 2026-09-17.

**The hole, in the owner's own words:** *"If a project has subprojects, and
the project itself has tasks in addition to subprojects."* The subtree walk
starts BELOW the node, so the node never appears among its own descendants.
The child rows are built from descendants alone. The node's own tasks were
folded into ``per_branch[project_id]`` and then dropped.

What a reader saw: a KPI strip counting 25 tasks, over child rows adding to
13, with no row carrying the other 12. Every single number on the page was
correct, and the page as a whole did not add up. That is worse than a wrong
number, because nothing looks wrong — the reader either does not check, or
checks and cannot find the error.

The invariant this file pins::

    own["tasks"] + sum(child["tasks"] for child in children) == tasks
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from gateway.routes.projects.core import CLOSING_CATEGORIES
from gateway.routes.projects.tree import (
    NODE_DESCENDANTS_SQL,
    fold_node_summary,
    node_counts_sql,
)

REPO = Path(__file__).resolve().parents[2]
SOURCE = (
    REPO / "apps/services/gateway/gateway/routes/projects/tree.py"
).read_text(encoding="utf-8")


# ── The shape, with no database ─────────────────────────────────────────────


class _Row:
    """A stand-in for one grouped count or one descendant."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _count(project_id, category, n, overdue=0):
    return _Row(project_id=project_id, category=category, n=n, overdue=overdue)


def _node(node_id, parent, branch, name="n", kind="project"):
    return _Row(
        id=node_id, parent_project_id=parent, branch_id=branch, name=name,
        kind=kind, status="active", archived_at=None,
    )


class TestTheNodesOwnWorkIsReturned:
    """⚠️ The ruling this module exists to carry."""

    def test_a_parent_with_its_own_tasks_reports_them(self):
        folded = fold_node_summary(
            "P",
            [_count("P", "todo", 12), _count("A", "todo", 8)],
            [_node("A", "P", "A")],
        )
        assert folded["own"]["tasks"] == 12
        assert folded["tasks"] == 20

    def test_the_rows_add_up_to_the_total(self):
        """The invariant. A dashboard that fails it lies while every number
        on it is individually right."""
        folded = fold_node_summary(
            "P",
            [
                _count("P", "todo", 12, overdue=2),
                _count("A", "todo", 8),
                _count("B", "done", 5),
            ],
            [_node("A", "P", "A"), _node("B", "P", "B")],
        )
        rows = folded["own"]["tasks"] + sum(
            c["tasks"] for c in folded["children"]
        )
        assert rows == folded["tasks"] == 25

    def test_a_grandchilds_work_lands_on_the_child_not_on_own(self):
        """`branch_id` exists for this. Work two levels down belongs to the
        row a reader can actually click, and never to the parent's own line."""
        folded = fold_node_summary(
            "P",
            [_count("P", "todo", 3), _count("G", "todo", 7)],
            # G sits under A, which sits under P. Its branch is A.
            [_node("A", "P", "A"), _node("G", "A", "A")],
        )
        assert folded["own"]["tasks"] == 3
        assert [c["tasks"] for c in folded["children"]] == [7]

    def test_own_is_present_even_when_the_node_owns_nothing(self):
        # ⚠️ An ABSENT key means "the server did not say", and no client can
        # tell that from "this node owns no work". `NodeDashboard`'s Stat
        # tile already had to grow a dash for exactly that confusion.
        folded = fold_node_summary(
            "P", [_count("A", "todo", 8)], [_node("A", "P", "A")],
        )
        assert folded["own"] == {"tasks": 0, "overdue": 0, "by_category": {}}

    def test_own_is_present_when_there_are_no_children_at_all(self):
        folded = fold_node_summary("P", [_count("P", "todo", 4)], [])
        assert folded["own"]["tasks"] == 4
        assert folded["children"] == []

    def test_own_carries_its_own_overdue_and_lanes(self):
        folded = fold_node_summary(
            "P",
            [_count("P", "todo", 3, overdue=2), _count("P", "done", 1)],
            [_node("A", "P", "A")],
        )
        assert folded["own"]["overdue"] == 2
        assert folded["own"]["by_category"] == {"todo": 3, "done": 1}

    def test_overdue_totals_include_the_nodes_own_late_work(self):
        folded = fold_node_summary(
            "P",
            [_count("P", "todo", 3, overdue=2), _count("A", "todo", 4, overdue=1)],
            [_node("A", "P", "A")],
        )
        assert folded["overdue"] == 3


class TestTheRouteUsesTheSeam:
    def test_the_route_returns_the_fold_rather_than_rebuilding_it(self):
        # A second fold in the route is how the two start disagreeing, and
        # only one of them would have a test.
        assert "fold_node_summary(project_id, rows, descendants)" in SOURCE
        assert "**folded," in SOURCE
        # ⚠️ EVERY mention of the fold's working state sits inside
        # `fold_node_summary`, above the route. A copy inside the route would
        # be a second counting rule, and only one of the two has a test.
        # Asserted by POSITION rather than by count — a count rots the next
        # time somebody edits a comment, which teaches people to delete it.
        fold_at = SOURCE.index("def fold_node_summary(")
        route_at = SOURCE.index('@router.get("/nodes/{project_id}/summary")')
        assert fold_at < route_at
        assert SOURCE.index("per_branch") > fold_at
        assert SOURCE.rindex("per_branch") < route_at

    def test_the_sql_is_named_so_a_test_can_run_it(self):
        assert "NODE_DESCENDANTS_SQL" in SOURCE
        assert "node_counts_sql(task_visibility_clause(vis" in SOURCE


# ── R8: the same SQL, against a real Postgres ───────────────────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

from tests.unit._tenant_ladder import apply_ladder  # noqa: E402


@pytest.fixture(scope="module")
def db():
    if not _TENANT_URL:
        pytest.skip(
            "TENANT_LADDER_DATABASE_URL unset — R8 needs a REAL Postgres. The"
            " recursive walk and the grouped count are exactly where a wrong"
            " column name hides, and a hermetic fake agrees with any SQL."
        )
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def tree(db):
    """A project with TWO subprojects, one of which has a subproject of its own.

    The shape the owner asked about, plus one more level — a grandchild is
    what proves `branch_id` files work onto the row a reader can click.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        for key, parent in (
            ("root", None), ("subA", "root"), ("subB", "root"),
            ("grand", "subA"),
        ):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id,"
                    " owns_statuses)"
                    " VALUES (:n,'active','manual','sum@example.test',"
                    " CAST(:o AS uuid),'Asia/Kolkata',"
                    " CAST(:par AS uuid), true) RETURNING id"
                ),
                {
                    "n": f"sum-{key}-{uuid.uuid4().hex[:6]}",
                    "o": org,
                    "par": made[parent] if parent else None,
                },
            ).scalar_one())
            made[key] = pid
            for name, cat, pos in (
                ("To do", "todo", 0),
                ("In progress", "in_progress", 1),
                ("Done", "done", 2),
                ("Cancelled", "cancelled", 3),
            ):
                made[f"{key}_{cat}"] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id,name,color,"
                        " position,category) VALUES (CAST(:p AS uuid),:n,"
                        " 'gray',:pos,:cat) RETURNING id"
                    ),
                    {"p": pid, "n": name, "pos": pos, "cat": cat},
                ).scalar_one())
    yield made
    ids = [made[k] for k in ("root", "subA", "subB", "grand")]
    with db.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM"
                " pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[])))"
            ),
            {"p": ids},
        )
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
            {"p": ids},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses"
                " WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"p": ids},
        )
        # Deepest first — each is the next one's foreign key.
        for key in ("grand", "subB", "subA", "root"):
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": made[key]},
            )


def _tasks(conn, tree, node, *, cat, n, due=None):
    """`n` tasks in `node`, in lane `cat`, optionally all due at `due`."""
    for i in range(n):
        conn.execute(
            text(
                "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                " status_id, created_by, organization_id, due_at, task_number)"
                " SELECT :t, CAST(:p AS uuid), CAST(:r AS uuid),"
                " CAST(:s AS uuid), 'sum@example.test', CAST(:o AS uuid),"
                " CAST(:d AS timestamptz),"
                " COALESCE(MAX(task_number),0)+1 FROM pm_tasks"
                " WHERE root_project_id = CAST(:r AS uuid)"
            ),
            {
                "t": f"{node}-{cat}-{i}",
                "p": tree[node],
                "r": tree["root"],
                "s": tree[f"{node}_{cat}"],
                "o": tree["org"],
                "d": due,
            },
        )


def _summarise(db, tree, node):
    """Run the endpoint's OWN sql and fold, with visibility wide open.

    The grant clause is `TRUE` here on purpose. This module is about the
    ARITHMETIC of the roll-up. `test_projects_grants.py` owns the question of
    who may see what, and re-testing it here would leave two places to change
    when the clause moves.
    """
    with db.begin() as c:
        descendants = c.execute(
            text(NODE_DESCENDANTS_SQL), {"pid": tree[node]},
        ).fetchall()
        ids = [tree[node]] + [str(r.id) for r in descendants]
        rows = c.execute(
            text(node_counts_sql("TRUE")),
            {"ids": ids, "closed": sorted(CLOSING_CATEGORIES)},
        ).fetchall()
    return fold_node_summary(tree[node], rows, descendants)


class TestAgainstPostgres:
    def test_a_parent_with_subprojects_AND_its_own_tasks_adds_up(self, db, tree):
        """⚠️ The owner's question, against a real database.

        Twelve tasks on the project itself, eight and five below it. Before
        `own` existed the strip said 25 and the rows said 13.
        """
        with db.begin() as c:
            _tasks(c, tree, "root", cat="todo", n=12)
            _tasks(c, tree, "subA", cat="todo", n=8)
            _tasks(c, tree, "subB", cat="done", n=5)

        got = _summarise(db, tree, "root")

        assert got["tasks"] == 25
        assert got["own"]["tasks"] == 12
        assert sorted(c["tasks"] for c in got["children"]) == [5, 8]
        assert got["own"]["tasks"] + sum(
            c["tasks"] for c in got["children"]
        ) == got["tasks"]

    def test_a_grandchilds_tasks_file_under_the_child_a_reader_can_click(
        self, db, tree,
    ):
        with db.begin() as c:
            _tasks(c, tree, "root", cat="todo", n=2)
            _tasks(c, tree, "grand", cat="in_progress", n=6)

        got = _summarise(db, tree, "root")
        by_id = {c["id"]: c for c in got["children"]}

        assert got["own"]["tasks"] == 2
        # subA owns no task of its own — all six sit two levels down.
        assert by_id[tree["subA"]]["tasks"] == 6
        assert by_id[tree["subB"]]["tasks"] == 0
        assert got["tasks"] == 8

    def test_a_leaf_subproject_reports_its_work_as_its_own(self, db, tree):
        """A subproject has no children, so everything it holds is `own`. The
        dashboard uses that to decide it needs no Direct-work row at all."""
        with db.begin() as c:
            _tasks(c, tree, "subB", cat="todo", n=4)

        got = _summarise(db, tree, "subB")

        assert got["children"] == []
        assert got["own"]["tasks"] == 4
        assert got["tasks"] == 4

    def test_the_nodes_own_overdue_work_reaches_the_total(self, db, tree):
        """⚠️ Overdue was already summed over the whole subtree and was
        already right. The child ROWS were not, so a parent's own late task
        raised the Overdue tile while no row below it showed anything late."""
        with db.begin() as c:
            _tasks(c, tree, "root", cat="todo", n=3, due="2020-01-01T00:00:00Z")
            _tasks(c, tree, "subA", cat="todo", n=1, due="2020-01-01T00:00:00Z")

        got = _summarise(db, tree, "root")

        assert got["overdue"] == 4
        assert got["own"]["overdue"] == 3
        assert sum(c["overdue"] for c in got["children"]) == 1

    def test_a_done_task_past_its_due_date_is_not_late(self, db, tree):
        # The same CLOSING_CATEGORIES rule the rest of Projects uses. Pinned
        # here because `own` is a new consumer of that filter.
        with db.begin() as c:
            _tasks(c, tree, "root", cat="done", n=2, due="2020-01-01T00:00:00Z")

        got = _summarise(db, tree, "root")

        assert got["tasks"] == 2
        assert got["overdue"] == 0
        assert got["own"]["overdue"] == 0
