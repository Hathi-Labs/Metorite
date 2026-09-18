"""H-64 — the board wrote an order that nothing read.

⚠️ **A drag inside a column was a SILENT NO-OP, and had been since WS-27.**
`handleDrop` wrote `pm_view_task_positions` through
`PUT /projects/views/{id}/positions`, and that half worked.
`GET /projects/tasks` selected ``t.*`` and joined nothing, so every row
arrived with `view_position` undefined. `board.sortForView` then put every
task in its `created_at` branch, the card animated back to where it started,
and the user saw a gesture fail with no error anywhere.

Two further costs, both invisible:

* `planDrop` saw no positioned neighbour and MATERIALISED the whole group on
  every drop — up to `MAX_POSITIONS` rows written per drag, none ever read.
* A same-column drop also made `buildCellDropPatch` answer `null`, so there
  was no field change either. The request went out and nothing came back
  different.

**What this file pins.** The join returns a position where one exists, `NULL`
where none does, and never drops a row. And `view_position` survives
`row_to_dict` — a model FILTERS, so a column added to the SELECT and not to
`TaskModel` is dropped from the response without a word.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects.core import TaskModel
from gateway.routes.projects.tasks import VIEW_POSITION_JOIN
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason="TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres",
)


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def board(db):
    """A project, a board view, and three tasks."""
    made: dict[str, str] = {}
    with db.begin() as c:
        made["org"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["project"] = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, owns_statuses)"
                " VALUES (:n,'active','manual','ord@example.test',"
                " CAST(:o AS uuid),'Asia/Kolkata',true) RETURNING id"
            ),
            {"n": f"ord-{uuid.uuid4().hex[:6]}", "o": made["org"]},
        ).scalar_one())
        made["todo"] = str(c.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id,name,color,position,"
                " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
                " RETURNING id"
            ),
            {"p": made["project"]},
        ).scalar_one())
        made["view"] = str(c.execute(
            text(
                "INSERT INTO pm_views (project_id, name, view_type, config,"
                " created_by, organization_id) VALUES (CAST(:p AS uuid),"
                " 'Board', 'board', '{}'::jsonb, 'ord@example.test',"
                " CAST(:o AS uuid)) RETURNING id"
            ),
            {"p": made["project"], "o": made["org"]},
        ).scalar_one())
        for key in ("a", "b", "c"):
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'ord@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1 FROM pm_tasks"
                    " WHERE root_project_id = CAST(:p AS uuid) RETURNING id"
                ),
                {
                    "t": key, "p": made["project"], "s": made["todo"],
                    "o": made["org"],
                },
            ).scalar_one())
    yield made
    with db.begin() as c:
        c.execute(
            text(
                "DELETE FROM pm_view_task_positions WHERE view_id ="
                " CAST(:v AS uuid)"
            ),
            {"v": made["view"]},
        )
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )
        c.execute(
            text("DELETE FROM pm_views WHERE id = CAST(:v AS uuid)"),
            {"v": made["view"]},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            ),
            {"p": made["project"]},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": made["project"]},
        )


def _place(conn, board, key, position):
    conn.execute(
        text(
            "INSERT INTO pm_view_task_positions (view_id, task_id, position,"
            " organization_id) VALUES (CAST(:v AS uuid), CAST(:t AS uuid),"
            " :pos, CAST(:o AS uuid))"
            " ON CONFLICT (view_id, task_id) DO UPDATE SET position = :pos"
        ),
        {
            "v": board["view"], "t": board[key], "pos": position,
            "o": board["org"],
        },
    )


def _read(db, board, *, view_id=None):
    """The route's OWN join fragment, run against the real database."""
    join = VIEW_POSITION_JOIN if view_id else ""
    sql = (
        f"SELECT t.id, t.title"
        f"{', vp.position AS view_position' if view_id else ''}"
        f"  FROM pm_tasks t{join}"
        f" WHERE t.project_id = CAST(:pid AS uuid)"
        f" ORDER BY t.task_number"
    )
    params = {"pid": board["project"]}
    if view_id:
        params["view_id"] = view_id
    with db.connect() as c:
        return c.execute(text(sql), params).fetchall()


class TestThePositionsAreReadBack:
    def test_a_placed_task_carries_its_position(self, db, board):
        """⚠️ The read half that never existed. Without it every drag was a
        no-op."""
        with db.begin() as c:
            _place(c, board, "b", 100.0)
        rows = {r.title: r.view_position for r in _read(db, board, view_id=board["view"])}
        assert rows["b"] == 100.0

    def test_an_unplaced_task_is_NULL_and_is_not_dropped(self, db, board):
        """⚠️ LEFT, never inner. A task nobody has dragged has no row here,
        and an inner join would drop every such card — on a fresh board, all
        of them."""
        with db.begin() as c:
            _place(c, board, "b", 100.0)
        rows = _read(db, board, view_id=board["view"])
        assert len(rows) == 3
        by_title = {r.title: r.view_position for r in rows}
        assert by_title["a"] is None
        assert by_title["c"] is None

    def test_a_board_nobody_has_touched_returns_three_NULLs(self, db, board):
        rows = _read(db, board, view_id=board["view"])
        assert len(rows) == 3
        assert all(r.view_position is None for r in rows)

    def test_another_view_positions_do_not_leak_in(self, db, board):
        """The join is keyed on the view. Two saved views hold two different
        hand-arranged orders of the same tasks, and neither may answer for
        the other."""
        with db.begin() as c:
            other = str(c.execute(
                text(
                    "INSERT INTO pm_views (project_id, name, view_type, config,"
                    " created_by, organization_id) VALUES (CAST(:p AS uuid),"
                    " 'Other', 'board', '{}'::jsonb, 'ord@example.test',"
                    " CAST(:o AS uuid)) RETURNING id"
                ),
                {"p": board["project"], "o": board["org"]},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_view_task_positions (view_id, task_id,"
                    " position, organization_id) VALUES (CAST(:v AS uuid),"
                    " CAST(:t AS uuid), 999, CAST(:o AS uuid))"
                ),
                {"v": other, "t": board["a"], "o": board["org"]},
            )
        rows = {r.title: r.view_position for r in _read(db, board, view_id=board["view"])}
        assert rows["a"] is None
        with db.begin() as c:
            c.execute(
                text("DELETE FROM pm_view_task_positions WHERE view_id = CAST(:v AS uuid)"),
                {"v": other},
            )
            c.execute(text("DELETE FROM pm_views WHERE id = CAST(:v AS uuid)"), {"v": other})

    def test_asking_for_no_view_selects_no_position_column(self, db, board):
        """The parameter is optional, and every surface that does not order by
        hand must not pay for the join."""
        rows = _read(db, board)
        assert len(rows) == 3
        assert not hasattr(rows[0], "view_position")


class TestTheModelCarriesIt:
    def test_view_position_survives_row_to_dict(self, db, board):
        """⚠️ `row_to_dict` FILTERS through `TaskModel`. A column added to the
        SELECT and not to the model is dropped from the response without a
        word — the same drift `ReportModel` grew a test for."""
        from gateway.routes.projects.core import row_to_dict

        with db.begin() as c:
            _place(c, board, "b", 250.5)
            row = c.execute(
                text(
                    f"SELECT t.*, vp.position AS view_position"
                    f"  FROM pm_tasks t{VIEW_POSITION_JOIN}"
                    f" WHERE t.id = CAST(:i AS uuid)"
                ),
                {"i": board["b"], "view_id": board["view"]},
            ).one()
        assert row_to_dict(row, TaskModel)["view_position"] == 250.5

    def test_it_is_declared_on_the_model_at_all(self):
        assert "view_position" in TaskModel.model_fields

    def test_it_is_NOT_on_the_input_model(self):
        """⚠️ It landed on `TaskIn` for one commit, and this is what caught it.

        `view_position` is a JOIN PRODUCT, not a `pm_tasks` column. On the
        input model it would let a caller write a field the table does not
        have. Order is written through `PUT /views/{id}/positions`, and only
        through that.
        """
        from gateway.routes.projects.core import TaskIn

        assert "view_position" not in TaskIn.model_fields
