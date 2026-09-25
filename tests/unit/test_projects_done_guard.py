"""D79 — every status set keeps at least one Done status.

Spec: ``project-docs/work_plan.md`` §3 D79 · ``specs/my_tasks_cutover.md``
§4.9. Mark done moves a task into the FIRST Done status by position and never
asks (``personal.complete_for_member``). A set with no Done status would give
that one gesture nowhere to go, so every door that removes or never creates a
Done status refuses:

* deleting the last Done status (``admin.delete_status``);
* moving the last Done status out of the Done category
  (``admin.patch_status``);
* switching a node onto a set with no Done status
  (``admin.set_status_set``) and promoting a node to a space
  (``tree.move_node``) — both through ``core.require_done_status``;
* every seed that creates a set carries a Done status.

The hermetic half pins the refusals and the seeds. The half marked ``live``
runs the guard's SQL on a real Postgres (R8), because a fake agrees with any
SQL it is handed.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import FakeProjectsDB, bind_db, projects_user

MODULES = (pm_core, pm_tree, pm_admin)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


def _set(db: FakeProjectsDB, *lanes: tuple[str, str, int]) -> tuple:
    project = db.seed_project(name="Website relaunch")
    rows = [
        db.seed_status(project.id, name=name, category=category, position=position)
        for name, category, position in lanes
    ]
    return project, rows


def _categories(db: FakeProjectsDB, project_id: str) -> list[str]:
    return sorted(
        str(r["category"]) for r in db.rows("pm_task_statuses")
        if str(r["project_id"]) == str(project_id)
    )


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_the_last_done_status_cannot_be_deleted(db: FakeProjectsDB) -> None:
    """A Cancelled lane survives, so the older "last closing lane" guard
    passes. The Done guard still refuses, and names the status."""
    project, (_todo, done, _dropped) = _set(
        db, ("To do", "todo", 10), ("Done", "done", 40), ("Dropped", "cancelled", 50),
    )
    with pytest.raises(HTTPException) as caught:
        await pm_admin.delete_status(str(done.id), user=USER)
    assert caught.value.status_code == 409
    assert "'Done' is the last Done status here" in caught.value.detail
    assert "done" in _categories(db, project.id)


async def test_a_done_status_with_a_sibling_can_be_deleted(db: FakeProjectsDB) -> None:
    project, (_todo, shipped, _done) = _set(
        db, ("To do", "todo", 10), ("Shipped", "done", 35), ("Done", "done", 40),
    )
    out = await pm_admin.delete_status(str(shipped.id), user=USER)
    assert out["deleted"] == str(shipped.id)
    assert _categories(db, project.id) == ["done", "todo"]


# ── Category change ─────────────────────────────────────────────────────────


async def test_the_last_done_status_cannot_leave_the_done_category(
    db: FakeProjectsDB,
) -> None:
    _project, (_todo, done) = _set(db, ("To do", "todo", 10), ("Done", "done", 40))
    for elsewhere in ("in_progress", "cancelled"):
        with pytest.raises(HTTPException) as caught:
            await pm_admin.patch_status(
                str(done.id), pm_admin.StatusIn(category=elsewhere), user=USER,
            )
        assert caught.value.status_code == 409
        assert "last Done status" in caught.value.detail


async def test_the_last_done_status_may_still_be_renamed_and_moved(
    db: FakeProjectsDB,
) -> None:
    """Only a change of CATEGORY takes the Done status away. A rename, a new
    colour, a new position, or a category it already has, all stay allowed."""
    _project, (_todo, done) = _set(db, ("To do", "todo", 10), ("Done", "done", 40))
    out = await pm_admin.patch_status(
        str(done.id),
        pm_admin.StatusIn(name="Shipped", color="green", position=45, category="done"),
        user=USER,
    )
    assert out["name"] == "Shipped"
    assert out["category"] == "done"


async def test_a_done_status_with_a_sibling_may_change_category(
    db: FakeProjectsDB,
) -> None:
    _project, (_todo, review, _done) = _set(
        db, ("To do", "todo", 10), ("Review", "done", 30), ("Done", "done", 40),
    )
    out = await pm_admin.patch_status(
        str(review.id), pm_admin.StatusIn(category="in_progress"), user=USER,
    )
    assert out["category"] == "in_progress"


# ── The shared guard ────────────────────────────────────────────────────────


async def test_require_done_status_refuses_a_set_with_no_done_status(
    db: FakeProjectsDB,
) -> None:
    project, _rows = _set(db, ("To do", "todo", 10), ("Dropped", "cancelled", 50))
    with pytest.raises(HTTPException) as caught:
        await pm_core.require_done_status(db, str(project.id))
    assert caught.value.status_code == 409
    assert caught.value.detail == pm_core.LAST_DONE_REFUSAL


async def test_require_done_status_passes_a_set_with_one(db: FakeProjectsDB) -> None:
    project, _rows = _set(db, ("To do", "todo", 10), ("Done", "done", 40))
    await pm_core.require_done_status(db, str(project.id))


async def test_a_switch_onto_a_set_with_no_done_status_is_refused(
    db: FakeProjectsDB,
) -> None:
    """``set_status_set`` reuses a DORMANT set as it was left. One with no
    Done status would leave the node unable to complete a task."""
    root, _rows = _set(db, ("To do", "todo", 10), ("Done", "done", 40))
    child = db.seed_project(name="Mobile app", parent=root.id, subject=None)
    db.seed_status(child.id, name="Queued", category="todo", position=10)
    with pytest.raises(HTTPException) as caught:
        await pm_admin.set_status_set(
            str(child.id), pm_admin.StatusSetIn(mode="own"), user=USER,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == pm_core.LAST_DONE_REFUSAL


# ── Every seed carries a Done status ────────────────────────────────────────


def test_every_seed_that_creates_a_set_carries_a_done_status() -> None:
    """A new space (``tree._seed_root``) and a personal root
    (``personal.ensure_personal_project``) are the two paths that create a
    set from nothing. A copy, a switch and a promotion copy an existing set,
    and ``require_done_status`` checks what they leave."""
    seeds = {
        "space": [category for _n, _c, _p, category, _d in pm_tree._SEED_STATUSES],
        "personal": [category for _n, category in pm_personal.PERSONAL_SEED_STATUSES],
    }
    for where, categories in seeds.items():
        assert pm_core.COMPLETED_CATEGORY in categories, where


# ── R8: the guard's SQL on a real Postgres ──────────────────────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

live = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def ladder():
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
async def real_db(ladder):
    """One transaction that is rolled back, so nothing survives the test."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(
        _TENANT_URL.replace("+psycopg", "+asyncpg"), poolclass=NullPool,
    )
    async with eng.connect() as conn:
        outer = await conn.begin()
        try:
            yield conn
        finally:
            await outer.rollback()
    await eng.dispose()


async def _real_set(conn, *lanes: tuple[str, str, int, str]) -> tuple[str, dict]:
    from sqlalchemy import text

    org = (await conn.execute(text(
        "INSERT INTO organization (slug, display_name) "
        "VALUES (:s, 'D79 live') RETURNING id"),
        {"s": f"d79-{uuid.uuid4().hex[:8]}"},
    )).scalar_one()
    project = (await conn.execute(text(
        "INSERT INTO pm_projects (name, status, source, created_by, "
        "  organization_id, timezone, owns_statuses) "
        "VALUES ('Website relaunch', 'active', 'manual', 'd79@example.test', "
        "        :o, 'Asia/Kolkata', true) RETURNING id"),
        {"o": org},
    )).scalar_one()
    ids = {}
    for name, category, position, color in lanes:
        ids[name] = str((await conn.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, position, color) "
            "VALUES (:p, :n, :c, :pos, :col) RETURNING id"),
            {"p": project, "n": name, "c": category, "pos": position, "col": color},
        )).scalar_one())
    return str(project), ids


@live
async def test_live_the_guard_reads_the_real_set(real_db) -> None:
    project, _ids = await _real_set(
        real_db, ("To do", "todo", 10, "gray"), ("Dropped", "cancelled", 50, "gray"),
    )
    with pytest.raises(HTTPException) as caught:
        await pm_core.require_done_status(real_db, project)
    assert caught.value.status_code == 409

    kept, _ids = await _real_set(
        real_db, ("To do", "todo", 10, "gray"), ("Done", "done", 40, "green"),
    )
    await pm_core.require_done_status(real_db, kept)


@live
async def test_live_other_lanes_excludes_only_the_status_itself(real_db) -> None:
    from types import SimpleNamespace

    project, ids = await _real_set(
        real_db, ("To do", "todo", 10, "gray"), ("Done", "done", 40, "green"),
    )
    existing = SimpleNamespace(id=ids["Done"], project_id=project)
    others = await pm_admin._other_lanes(real_db, existing)
    assert [str(r.id) for r in others] == [ids["To do"]]
    assert not pm_admin._has_done(others)


@live
async def test_live_the_lanes_read_carries_each_colour(real_db) -> None:
    """``GET /my/tasks/{id}/lanes`` adds ``color`` (D79). The column read
    is the route's own statement."""
    from sqlalchemy import text

    project, _ids = await _real_set(
        real_db, ("To do", "todo", 10, "gray"), ("In review", "in_progress", 30, "violet"),
        ("Done", "done", 40, "green"),
    )
    rows = (await real_db.execute(
        text(pm_personal.MY_TASK_LANES_SQL), {"root": project},
    )).fetchall()
    assert [(r.name, r.color) for r in rows] == [
        ("To do", "gray"), ("In review", "violet"), ("Done", "green"),
    ]
