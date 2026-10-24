"""WS-64 OP-10 — a folder becomes a project (D-OP-16).

Spec: ``project-docs/specs/one_projects_app.md`` D-OP-16 and §9 OP-10.

``POST /projects/nodes/{id}/convert`` with ``{"to": "project"}`` changes the
kind of a folder in one transaction. The projects inside it become
subprojects, because a level is derived and nothing stored moves. The
grammar decides through ``core.assert_folder_converts``, which calls
``assert_node_grammar``. The authority is ``authority.may_convert_node``.

Two halves.

**Database-free (always runs).** The grammar cases of a conversion, and
the words of each refusal.

**R8 (a real database, as a NON-privileged role).** The world of
``test_projects_write_authority.py``: ``Team`` is a space that ``LEAD``
leads. The real gateway app answers each request.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_folder_convert.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* C1. ``G/core.py`` ``assert_folder_converts``: ``depth = 1``, so the
  children do not count. ``test_a_child_with_subprojects_is_refused_and_named``
  and ``test_3_a_child_with_a_subproject_is_refused_and_nothing_changes``.
* C2. ``G/tree.py`` ``convert_node``: no ``assert_may_convert_node``.
  ``test_2_a_viewer_gets_403_with_the_sentence``.
* C3. ``G/authority.py`` ``may_convert_node``: no personal-tree arm.
  ``test_5_the_owner_converts_a_folder_in_their_own_tree``.
* C4. ``G/tree.py`` ``convert_node``: no ``record_activity``.
  ``test_1_a_lead_converts_a_folder_with_three_projects``.
* C5. ``G/authority.py`` ``attach_can_convert``: a project row reads true.
  ``test_4_the_reads_carry_can_convert``.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from gateway.routes.projects.core import assert_folder_converts
from sqlalchemy import text

from tests.unit import test_projects_write_authority as wa
from tests.unit.test_projects_write_authority import ADMIN, CREATOR, LEAD, VIEWER

_DB_GATE = wa._DB_GATE

#: The fixtures of the OP-8 world, on a catalog of this module's own.
world = wa.world
redis_fake = wa.redis_fake
live_app = wa.live_app
live = wa.live


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


def _refused(**kwargs: Any) -> str:
    with pytest.raises(HTTPException) as err:
        assert_folder_converts(**kwargs)
    assert err.value.status_code == 422
    return str(err.value.detail)


def test_a_folder_of_projects_under_a_space_converts() -> None:
    """space → folder → three projects: the projects become subprojects."""
    assert_folder_converts(
        parent_kind="project", parent_generation=1,
        children=[("Alpha", 1), ("Beta", 1), ("Gamma", 1)],
    )
    # An empty folder converts anywhere a project fits.
    assert_folder_converts(parent_kind="project", parent_generation=1, children=[])
    assert_folder_converts(parent_kind="project", parent_generation=2, children=[])


def test_a_child_with_subprojects_is_refused_and_named() -> None:
    detail = _refused(
        parent_kind="project", parent_generation=1,
        children=[("Alpha", 1), ("Deep", 2), ("Also deep", 2)],
    )
    assert "'Also deep', 'Deep'" in detail
    assert "Alpha" not in detail
    assert "Move their sub-projects out first, or move the project out of the folder." in detail


def test_a_folder_inside_a_project_converts_only_when_empty() -> None:
    detail = _refused(
        parent_kind="project", parent_generation=2, children=[("Leaf", 1)],
    )
    assert "'Leaf'" in detail and "inside a project" in detail
    assert_folder_converts(parent_kind="project", parent_generation=2, children=[])


def test_the_grammar_decides_through_the_one_seam() -> None:
    """The helper calls ``assert_node_grammar``. It holds no copy of the cap."""
    from gateway.routes.projects import core, tree

    src = inspect.getsource(core.assert_folder_converts)
    assert "assert_node_grammar(" in src
    route = inspect.getsource(tree.convert_node)
    assert "assert_folder_converts(" in route
    assert "assert_may_convert_node(db, user, node)" in route


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


def _node(w: Any, name: str, parent: str, kind: str = "project",
          maker: str = CREATOR, owner: str | None = None) -> str:
    return str(w.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, parent_project_id, kind, personal_owner) VALUES (:n, 'active',"
        " 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid), :k, :po)"
        " RETURNING id",
        n=name, m=maker, o=w.org, p=parent, k=kind, po=owner))


def _row(w: Any, node: str) -> Any:
    with w.admin.begin() as c:
        return c.execute(text(
            "SELECT kind, status, owns_statuses, updated_at FROM pm_projects"
            " WHERE id = CAST(:i AS uuid)"), {"i": node}).one()


def _task_state(w: Any, tasks: list[str]) -> dict[str, tuple[str, str, str]]:
    with w.admin.begin() as c:
        rows = c.execute(text(
            "SELECT id, project_id, root_project_id, status_id FROM pm_tasks"
            " WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": tasks}).fetchall()
    return {str(r.id): (str(r.project_id), str(r.root_project_id), str(r.status_id))
            for r in rows}


async def _convert(w: Any, who: str, node: str) -> Any:
    return await wa._as(w, who, "POST", f"/projects/nodes/{node}/convert", {"to": "project"})


async def _level(w: Any, who: str, node: str) -> str:
    r = await wa._as(w, who, "GET", f"/projects/nodes/{node}/summary")
    assert r.status_code == 200, r.text
    return r.json()["level"]


def _find(rows: list[dict[str, Any]], node: str) -> dict[str, Any] | None:
    for row in rows:
        if row["id"] == node:
            return row
        found = _find(row.get("children") or [], node)
        if found is not None:
            return found
    return None


class TestTheConversion:
    pytestmark = _DB_GATE

    async def test_1_a_lead_converts_a_folder_with_three_projects(self, live):
        folder = _node(live, "op10 folder", live.team, kind="folder")
        kids = [_node(live, f"op10 kid {i}", folder) for i in range(3)]
        tasks = [wa._task(live, kid, CREATOR, f"op10 task {i}") for i, kid in enumerate(kids)]
        before = _task_state(live, tasks)
        statuses_before = (await wa._as(
            live, LEAD, "GET", f"/projects/nodes/{kids[0]}/statuses")).json()["rows"]
        assert [await _level(live, LEAD, k) for k in kids] == ["project"] * 3

        r = await _convert(live, LEAD, folder)
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "project" and r.json()["status"] == "active"
        assert _row(live, folder).kind == "project"

        # The children read as subprojects, and their tasks did not move.
        assert await _level(live, LEAD, folder) == "project"
        assert [await _level(live, LEAD, k) for k in kids] == ["subproject"] * 3
        tree = (await wa._as(live, LEAD, "GET", "/projects/tree")).json()
        node = _find(tree["rows"], folder)
        assert node is not None and node["kind"] == "project"
        assert sorted(c["id"] for c in node["children"]) == sorted(kids)
        assert _task_state(live, tasks) == before
        statuses_after = (await wa._as(
            live, LEAD, "GET", f"/projects/nodes/{kids[0]}/statuses")).json()["rows"]
        assert [s["id"] for s in statuses_after] == [s["id"] for s in statuses_before]

        # The new project has a status set, and a task can be made on it.
        own = (await wa._as(live, LEAD, "GET", f"/projects/nodes/{folder}/statuses")).json()
        assert own["rows"], own
        made = await wa._as(live, LEAD, "POST", "/projects/tasks",
                            {"project_id": folder, "title": "op10 on the new project"})
        assert made.status_code in (200, 201), made.text
        assert made.json()["project_id"] == folder

        # The timeline says what happened.
        with live.admin.begin() as c:
            bodies = [r.body for r in c.execute(text(
                "SELECT body FROM pm_activities WHERE project_id = CAST(:p AS uuid)"),
                {"p": folder})]
        assert "Converted from a folder to a project" in bodies

        # A second call is refused: it is a project now.
        again = await _convert(live, LEAD, folder)
        assert again.status_code == 422 and "already a project" in again.json()["detail"]

    async def test_2_a_viewer_gets_403_with_the_sentence(self, live):
        folder = _node(live, "op10 viewer folder", live.team, kind="folder")
        _node(live, "op10 viewer kid", folder)
        stamp = _row(live, folder).updated_at
        r = await _convert(live, VIEWER, folder)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == wa._authority().CONVERT_REFUSED
        row = _row(live, folder)
        assert row.kind == "folder" and row.updated_at == stamp
        # The creator of the folder is not its lead either.
        assert (await _convert(live, CREATOR, folder)).status_code == 403

    async def test_3_a_child_with_a_subproject_is_refused_and_nothing_changes(self, live):
        folder = _node(live, "op10 deep folder", live.team, kind="folder")
        _node(live, "op10 shallow", folder)
        deep = _node(live, "op10 deep", folder)
        _node(live, "op10 deeper", deep)
        r = await _convert(live, ADMIN, folder)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert "'op10 deep'" in detail and "op10 shallow" not in detail
        assert "Move their sub-projects out first" in detail
        assert _row(live, folder).kind == "folder"
        assert await _level(live, ADMIN, deep) == "project"

    async def test_4_the_reads_carry_can_convert(self, live):
        folder = _node(live, "op10 flag folder", live.team, kind="folder")
        for who, want in ((LEAD, True), (ADMIN, True), (VIEWER, False)):
            got = (await wa._as(live, who, "GET", f"/projects/nodes/{folder}")).json()
            assert got["can_convert"] is want, (who, got)
            tree = (await wa._as(live, who, "GET", "/projects/tree")).json()
            assert _find(tree["rows"], folder)["can_convert"] is want, who
            assert _find(tree["rows"], live.team)["can_convert"] is False, who
        got = (await wa._as(live, VIEWER, "GET", f"/projects/nodes/{folder}")).json()
        assert got["convert_refusal"] == wa._authority().CONVERT_REFUSED

    async def test_5_the_owner_converts_a_folder_in_their_own_tree(self, live):
        """D-OP-2 and D-OP-15: a personal tree follows the same rule for its owner."""
        owner = VIEWER
        root = str(live.seeder.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses, personal_owner) VALUES ('op10 mine', 'active',"
            " 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata', true, :m) RETURNING id",
            m=owner, o=live.org))
        live.seeder.status(root, "To do", "todo", 0)
        wa.d._grant(live.seeder, root, owner)
        folder = _node(live, "op10 my folder", root, kind="folder", maker=owner, owner=owner)
        wa.d._grant(live.seeder, folder, owner)
        r = await _convert(live, owner, folder)
        assert r.status_code == 200, r.text
        assert _row(live, folder).kind == "project"
