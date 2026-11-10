"""R8 — SP-7, the one-time move of the tasks tagged "Epic" to the Epic type.

Spec: ``project-docs/specs/project_management_app.md`` §13, slice SP-7 (Metorite
task 2290). Code: ``gateway/routes/projects/epic_tag_move.py``.

Against a REAL Postgres, as the non-privileged role. A top-level task with the
tag (any case) and a non-epic type gets the epic type of its root project, keeps
the tag, and gains one ``field_change`` activity by ``system:sp7-epic-tag``. A
task with a parent, or whose root has no epic type, is skipped and listed. A
second run moves nothing, a dry run writes nothing, and a second org is not
touched.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_epic_tag_move_r8.py -v -rs

**The mutations (R7).** ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* M1. ``G/epic_tag_move.py``: drop the ``parent_task_id`` skip.
  ``test_a_task_with_a_parent_is_skipped_and_listed``.
* M2. ``G/epic_tag_move.py``: drop the ``db.rollback()`` of a dry run.
  ``test_a_dry_run_writes_nothing``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import epic_tag_move
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import _app_dsn, _assert_non_priv

EMAIL = "sp7@epictag.test"


def _project(p: Any, org: str, *, epic: bool = True) -> dict[str, Any]:
    """A root project with a todo lane, a plain type and (optionally) an epic type."""
    pid, status = str(uuid.uuid4()), str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": EMAIL, "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'sp7', :e, true)"), {"id": pid, "o": org, "e": EMAIL})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": org, "p": pid})
        types = {}
        for name, is_epic in (("Task", False), ("Epic", True)):
            if is_epic and not epic:
                continue
            types[name] = str(c.execute(text(
                "INSERT INTO pm_task_types (project_id, name, is_epic, organization_id) "
                "VALUES (CAST(:p AS uuid), :n, :e, CAST(:o AS uuid)) RETURNING id"),
                {"p": pid, "n": name, "e": is_epic, "o": org}).scalar())
    return {"id": pid, "status": status, "types": types}


_NUMBER = [900]


def _task(p: Any, org: str, proj: dict[str, Any], title: str, *, tags: list[str],
          kind: str | None = "Task", parent: str | None = None) -> str:
    tid = str(uuid.uuid4())
    _NUMBER[0] += 1
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by, tags, type_id, parent_task_id) "
            "VALUES (:id, :o, :p, :p, :n, :s, :t, :e, CAST(:tags AS text[]), "
            "CAST(:ty AS uuid), CAST(:par AS uuid))"),
            {"id": tid, "o": org, "p": proj["id"], "n": _NUMBER[0], "s": proj["status"],
             "t": title, "e": EMAIL, "ty": proj["types"][kind] if kind else None,
             "par": parent, "tags": "{" + ",".join(f'"{t}"' for t in tags) + "}"})
    return tid


def _row(p: Any, tid: str) -> tuple[str | None, list[str]]:
    with p.admin_engine.connect() as c:
        r = c.execute(text("SELECT type_id, tags FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                      {"t": tid}).one()
    return (str(r[0]) if r[0] else None), list(r[1])


def _activities(p: Any, tid: str) -> list[tuple]:
    with p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT type, created_by, meta FROM pm_activities "
            "WHERE task_id = CAST(:t AS uuid)"), {"t": tid})]


async def _move(p: Any, org: str, **kw: Any) -> dict[str, Any]:
    from acb_common.db import clear_tenant, release_tenant

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            return await epic_tag_move.move_org(org, **kw)
    finally:
        release_tenant(scope)


@pytest.fixture
def board(promoted):  # noqa: F811 — a fixture
    yield promoted
    with promoted.admin_engine.begin() as c:
        for table in ("pm_activities", "pm_tasks", "pm_task_types", "pm_task_statuses",
                      "pm_projects"):
            c.execute(text(f"DELETE FROM {table}"))
        c.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": EMAIL})


@_DB_GATE
async def test_a_tagged_task_moves_to_the_epic_type_and_a_rerun_moves_nothing(board, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    proj = _project(board, board.org_a)
    epic = proj["types"]["Epic"]
    upper = _task(board, board.org_a, proj, "upper", tags=["Epic", "x"])
    lower = _task(board, board.org_a, proj, "lower", tags=["epic"])
    untyped = _task(board, board.org_a, proj, "untyped", tags=["Epic"], kind=None)
    untagged = _task(board, board.org_a, proj, "untagged", tags=["x"])
    already = _task(board, board.org_a, proj, "already", tags=["Epic"], kind="Epic")

    got = await _move(board, board.org_a)
    assert got["moved"] == 3 and got["found"] == 3 and got["skipped"] == []
    for tid, tags in ((upper, ["Epic", "x"]), (lower, ["epic"]), (untyped, ["Epic"])):
        assert _row(board, tid) == (epic, tags), "type moved and the tag stays"
        ((kind, by, meta),) = _activities(board, tid)
        assert (kind, by) == ("field_change", "system:sp7-epic-tag")
        meta = meta if isinstance(meta, dict) else json.loads(meta)
        assert meta["changes"][0]["field"] == "type_id"
    # An untagged task and an already-epic task are untouched, with no activity.
    assert _row(board, untagged)[0] == proj["types"]["Task"]
    assert _row(board, already)[0] == epic
    assert _activities(board, untagged) == [] and _activities(board, already) == []

    again = await _move(board, board.org_a)
    assert again["moved"] == 0 and again["found"] == 0
    assert len(_activities(board, upper)) == 1


@_DB_GATE
async def test_a_task_with_a_parent_is_skipped_and_listed(board, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    proj = _project(board, board.org_a)
    top = _task(board, board.org_a, proj, "top", tags=["x"])
    child = _task(board, board.org_a, proj, "child", tags=["Epic"], parent=top)

    got = await _move(board, board.org_a)
    assert got["moved"] == 0
    assert [(s["task_number"], s["reason"]) for s in got["skipped"]] == [
        (got["skipped"][0]["task_number"], "has a parent")]
    assert got["skipped"][0]["title"] == "child"
    assert _row(board, child)[0] == proj["types"]["Task"]
    assert _activities(board, child) == []


@_DB_GATE
async def test_a_task_whose_project_has_no_epic_type_is_skipped(board, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    proj = _project(board, board.org_a, epic=False)
    tid = _task(board, board.org_a, proj, "noepic", tags=["Epic"])

    got = await _move(board, board.org_a)
    assert got["moved"] == 0
    assert [s["reason"] for s in got["skipped"]] == ["its project has no epic type"]
    assert _row(board, tid)[0] == proj["types"]["Task"]
    assert _activities(board, tid) == []


@_DB_GATE
async def test_a_dry_run_writes_nothing(board, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    proj = _project(board, board.org_a)
    tid = _task(board, board.org_a, proj, "dry", tags=["Epic"])

    dry = await _move(board, board.org_a, dry_run=True)
    assert dry["moved"] == 1 and dry["moved_numbers"]
    assert _row(board, tid)[0] == proj["types"]["Task"]
    assert _activities(board, tid) == []

    real = await _move(board, board.org_a)
    assert real["moved"] == 1
    assert _row(board, tid)[0] == proj["types"]["Epic"]


@_DB_GATE
async def test_another_org_is_not_touched(board, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    proj_a = _project(board, board.org_a)
    proj_b = _project(board, board.org_b)
    a = _task(board, board.org_a, proj_a, "a", tags=["Epic"])
    b = _task(board, board.org_b, proj_b, "b", tags=["Epic"])

    got = await _move(board, board.org_a)
    assert got["moved"] == 1
    assert _row(board, a)[0] == proj_a["types"]["Epic"]
    assert _row(board, b)[0] == proj_b["types"]["Task"], "org A's run changed org B"
    assert _activities(board, b) == []
