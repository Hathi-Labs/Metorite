"""Areas — a member's own categories, and the line around them (WS-39 S6b).

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 S6b · D73 · migration 191.

An **Area** is a child of the personal root carrying ``personal_owner``. Three
claims are worth a test, and they are not the CRUD:

1. **An Area is private at depth.** Migration 191 made ``personal_owner`` mean
   "private to this person anywhere in the tree", and ``tree.py`` excludes
   every row that carries it. A create path that forgot the column would put a
   member's categories on the company board.
2. **Somebody else's Area is NOT FOUND, not refused.** Authorization here is
   the lookup: the query has no way to reach a row that is not mine, so there
   is no check to forget.
3. **The line between the company tree and a private one does not get
   crossed** — in either direction. That guard did not exist before this
   slice, because until Areas shipped a personal tree was one node deep and
   neither refusal was reachable.

Hermetic: no Postgres, no network. The SQL itself is proven in
``tests/live/live_ws39_personal_tree.sql``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal)

ALICE = member_user("alice@fracktal.in")
BOB = member_user("bob@fracktal.in")


def run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _row(db, table: str, row_id: str):
    """One row by id, or None. `FakeProjectsDB` exposes whole tables."""
    for row in db.rows(table):
        if str(row.get("id")) == str(row_id):
            return row
    return None


def _personal_root(db: FakeProjectsDB, email: str):
    root = db.seed_project(
        name="My tasks", subject=email, personal_owner=email, owns_statuses=True,
    )
    db.seed_status(root.id, name="Inbox", category="backlog")
    return root


def _area(db: FakeProjectsDB, root, email: str, name: str):
    return db.seed_project(
        name=name, parent=str(root.id), subject=email,
        personal_owner=email, owns_statuses=False,
    )


# -- The name --------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_a_blank_name_is_refused(raw):
    with pytest.raises(HTTPException) as exc:
        pm_personal._clean_area_name(raw)
    assert exc.value.status_code == 422


def test_a_name_is_trimmed_not_rejected_for_whitespace():
    assert pm_personal._clean_area_name("  Home  ") == "Home"


def test_a_name_longer_than_the_cap_is_refused():
    with pytest.raises(HTTPException) as exc:
        pm_personal._clean_area_name("x" * (pm_personal.AREA_NAME_MAX + 1))
    assert exc.value.status_code == 422
    # The limit is NAMED in the message. A refusal that does not say the number
    # makes the member guess how much to cut.
    assert str(pm_personal.AREA_NAME_MAX) in exc.value.detail


# -- Privacy at depth ------------------------------------------------------


def test_a_new_area_carries_personal_owner(db):
    _personal_root(db, "alice@fracktal.in")
    created = run(pm_personal.create_my_area(
        pm_personal.AreaIn(name="Home"), ALICE,
    ))
    row = _row(db, "pm_projects", created["id"])
    assert (row["personal_owner"] or "").lower() == "alice@fracktal.in"
    # And it is a CHILD. A root would collide with the member's real root on
    # migration 191's partial unique index.
    assert row["parent_project_id"] is not None


def test_a_new_area_does_not_own_statuses(db):
    # It inherits the root's lanes, so a task moved between two of my Areas
    # keeps its status instead of pointing at a lane that does not exist.
    _personal_root(db, "alice@fracktal.in")
    created = run(pm_personal.create_my_area(
        pm_personal.AreaIn(name="Home"), ALICE,
    ))
    assert _row(db, "pm_projects", created["id"])["owns_statuses"] is False


def test_two_areas_of_the_same_name_are_refused(db):
    root = _personal_root(db, "alice@fracktal.in")
    _area(db, root, "alice@fracktal.in", "Home")
    with pytest.raises(HTTPException) as exc:
        run(pm_personal.create_my_area(pm_personal.AreaIn(name="home"), ALICE))
    assert exc.value.status_code == 409


# -- Somebody else's Area is not found -------------------------------------


def test_i_cannot_rename_somebody_elses_area(db):
    bob_root = _personal_root(db, "bob@fracktal.in")
    bobs = _area(db, bob_root, "bob@fracktal.in", "Bob's shed")
    _personal_root(db, "alice@fracktal.in")
    with pytest.raises(HTTPException) as exc:
        run(pm_personal.rename_my_area(
            str(bobs.id), pm_personal.AreaIn(name="Mine now"), ALICE,
        ))
    # 404, not 403. The query had no way to reach the row, so there is no
    # "refused" state to leak the row's existence through.
    assert exc.value.status_code == 404


def test_i_cannot_delete_somebody_elses_area(db):
    bob_root = _personal_root(db, "bob@fracktal.in")
    bobs = _area(db, bob_root, "bob@fracktal.in", "Bob's shed")
    _personal_root(db, "alice@fracktal.in")
    with pytest.raises(HTTPException) as exc:
        run(pm_personal.remove_my_area(str(bobs.id), ALICE))
    assert exc.value.status_code == 404


def test_the_lookup_can_never_return_my_own_ROOT(db):
    # The root carries `personal_owner` too, so a lookup keyed on that alone
    # would let a member rename or delete the project their captures land in.
    root = _personal_root(db, "alice@fracktal.in")
    with pytest.raises(HTTPException) as exc:
        run(pm_personal.remove_my_area(str(root.id), ALICE))
    assert exc.value.status_code == 404


# -- Delete tells the truth about what it did ------------------------------


def test_an_empty_area_is_deleted(db):
    root = _personal_root(db, "alice@fracktal.in")
    area = _area(db, root, "alice@fracktal.in", "Scratch")
    out = run(pm_personal.remove_my_area(str(area.id), ALICE))
    assert out["outcome"] == "deleted"
    assert out["tasks"] == 0
    assert _row(db, "pm_projects", str(area.id)) is None


def test_an_area_holding_work_is_ARCHIVED_not_deleted(db):
    root = _personal_root(db, "alice@fracktal.in")
    area = _area(db, root, "alice@fracktal.in", "Home")
    status = db.seed_status(str(root.id), name="Inbox", category="backlog")
    db.seed(
        "pm_tasks", project_id=str(area.id), root_project_id=str(root.id),
        status_id=str(status.id), title="Fix the tap", created_by="alice@fracktal.in",
    )
    out = run(pm_personal.remove_my_area(str(area.id), ALICE))
    assert out["outcome"] == "archived"
    assert out["tasks"] == 1
    # The row survives, and so does the work in it.
    assert _row(db, "pm_projects", str(area.id))["archived_at"] is not None


# -- The line around a personal tree ---------------------------------------


class _OneRead:
    """The single SELECT the guard makes, and nothing else."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _sql, _params=None):
        rows = self._rows

        class R:
            @staticmethod
            def fetchall():
                return rows

        return R()


def _node(node_id: str, owner: str | None):
    return SimpleNamespace(id=node_id, personal_owner=owner)


def _guard(moved_owner, parent_owner, parent_id="parent"):
    rows = [_node("moved", moved_owner)]
    if parent_id is not None:
        rows.append(_node(parent_id, parent_owner))
    return pm_core.assert_project_move_keeps_privacy(
        _OneRead(rows), "moved", parent_id,
    )


def test_team_under_team_is_allowed():
    run(_guard(None, None))


def test_my_area_under_my_other_area_is_allowed():
    run(_guard("alice@fracktal.in", "alice@fracktal.in"))


def test_a_personal_area_cannot_be_moved_under_a_team_node():
    with pytest.raises(HTTPException) as exc:
        run(_guard("alice@fracktal.in", None))
    assert exc.value.status_code == 422
    assert "personal area" in exc.value.detail


def test_a_personal_area_cannot_be_promoted_to_a_top_LEVEL_node():
    # `parent_project_id = NULL` is a move out into the open, which is the
    # same crossing as a move under a team space.
    with pytest.raises(HTTPException) as exc:
        run(_guard("alice@fracktal.in", None, parent_id=None))
    assert exc.value.status_code == 422


def test_a_team_project_cannot_be_moved_into_a_personal_space():
    with pytest.raises(HTTPException) as exc:
        run(_guard(None, "bob@fracktal.in"))
    assert exc.value.status_code == 422
    assert "personal space" in exc.value.detail


def test_my_area_cannot_be_moved_into_somebody_elses():
    with pytest.raises(HTTPException) as exc:
        run(_guard("alice@fracktal.in", "bob@fracktal.in"))
    assert exc.value.status_code == 422


def test_the_guard_is_wired_into_the_node_move():
    # The function existing is not the fence. `move_node` calling it is.
    import inspect

    source = inspect.getsource(pm_tree.move_node)
    assert "assert_project_move_keeps_privacy" in source
