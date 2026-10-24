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


def _chat_gateway(monkeypatch, node: dict[str, Any], children: list[dict[str, Any]]):
    from tests.unit._projects_agent_fakes import fake_gateway

    def answer(call: dict) -> Any:
        if call["method"] == "GET" and call["path"] == "/projects/tree":
            return {"rows": [{"id": "space", "name": "Space", "children": [
                {**node, "children": children}]}]}
        if call["method"] == "GET":
            return node
        return {**node, "kind": "project"}

    return fake_gateway(monkeypatch, answer)


async def test_the_chat_card_says_the_children_become_subprojects(monkeypatch) -> None:
    from skill_projects import guarded

    from tests.unit._projects_agent_fakes import approve

    fid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    calls = _chat_gateway(monkeypatch, {"id": fid, "name": "Clients", "kind": "folder",
                                        "can_convert": True},
                          [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Globex"}])
    asked = approve(monkeypatch)
    out = await guarded.convert_folder_to_project(fid)
    [card] = asked
    assert "2 projects you can see inside it become its subprojects" in card["context"]
    assert "«Acme», «Globex»" in card["context"]
    posts = [c for c in calls if c["method"] == "POST"]
    assert [c["path"] for c in posts] == [f"/projects/nodes/{fid}/convert"]
    assert posts[0]["json"] == {"to": "project"}
    assert out.startswith("Converted «Clients» to a project."), out


async def test_the_chat_refuses_before_the_card_and_quotes_the_server(monkeypatch) -> None:
    from skill_projects import guarded

    from tests.unit._projects_agent_fakes import approve

    fid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    words = wa._authority().CONVERT_REFUSED
    calls = _chat_gateway(monkeypatch, {"id": fid, "name": "Clients", "kind": "folder",
                                        "can_convert": False, "convert_refusal": words}, [])
    asked = approve(monkeypatch)
    out = await guarded.convert_folder_to_project(fid)
    assert asked == [] and out.startswith("Refused:"), out
    assert f"Gateway said: «{words}»" in out
    # A project is refused before the card too.
    calls = _chat_gateway(monkeypatch, {"id": fid, "name": "Launch", "kind": "project"}, [])
    out = await guarded.convert_folder_to_project(fid)
    assert asked == [] and "already a project" in out
    assert not [c for c in calls if c["method"] == "POST"]


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


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1 — the tree read costs no walk, and a convert serializes
# ═══════════════════════════════════════════════════════════════════════════


async def test_a_page_of_folders_costs_one_query_and_no_walk() -> None:
    """``attach_can_convert(walk=False)`` reads the lead set once, and the row's
    own ``personal_owner`` answers the owner arm. No query grows with the rows."""
    from types import SimpleNamespace

    a = wa._authority()
    seen: list[str] = []

    class CountingDb:
        async def execute(self, sql: Any, *_a: Any, **_k: Any) -> Any:
            seen.append(str(sql))
            return SimpleNamespace(fetchall=lambda: [], scalar=lambda: None,
                                   fetchone=lambda: None)

    member = SimpleNamespace(email="me@x.test", has_permission=lambda _p: False)
    rows = [{"id": f"f{i}", "kind": "folder", "created_by": "other@x.test",
             "personal_owner": None} for i in range(6)]
    rows.append({"id": "mine", "kind": "folder", "created_by": "other@x.test",
                 "personal_owner": "Me@x.test"})
    await a.attach_can_convert(CountingDb(), member, rows, walk=False)
    assert len(seen) == 1, seen
    assert [r["can_convert"] for r in rows] == [False] * 6 + [True]


def test_a_team_child_kept_in_a_personal_tree_names_who_can_move_it() -> None:
    detail = _refused(
        parent_kind="project", parent_generation=1,
        children=[("Mine", 2), ("Team work", 2)], kept=frozenset({"Team work"}),
    )
    assert "Move 'Mine' out of the folder first." in detail
    assert ("'Team work' is team work. A lead of 'Team work' or a Projects admin "
            "can move it.") in detail
    assert "Move 'Team work'" not in detail and "'Mine', 'Team work'" in detail
    # Inside a project, the same split.
    detail = _refused(
        parent_kind="project", parent_generation=2,
        children=[("Team work", 1)], kept=frozenset({"Team work"}),
    )
    assert "Move 'Team work'" not in detail
    assert "A lead of 'Team work' or a Projects admin can move it." in detail


def test_the_card_caps_its_list_as_the_dialog_does() -> None:
    import pathlib
    import re

    from skill_projects import guarded

    ts = (pathlib.Path(__file__).resolve().parents[2] / "workbench" / "control_plane"
          / "src" / "app" / "projects" / "lib" / "projectConvert.ts").read_text(
              encoding="utf-8")
    [limit] = re.findall(r"export const CONVERT_LIST_LIMIT = (\d+);", ts)
    assert guarded.CONVERT_LIST_LIMIT == int(limit)


async def test_the_chat_card_says_and_n_more(monkeypatch) -> None:
    from skill_projects import guarded

    from tests.unit._projects_agent_fakes import approve

    fid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    n = guarded.CONVERT_LIST_LIMIT + 3
    _chat_gateway(monkeypatch, {"id": fid, "name": "Clients", "kind": "folder",
                                "can_convert": True},
                  [{"id": f"k{i}", "name": f"P{i}"} for i in range(n)])
    asked = approve(monkeypatch)
    await guarded.convert_folder_to_project(fid)
    [card] = asked
    last = guarded.CONVERT_LIST_LIMIT - 1
    assert f"«P{last}» and 3 more" in card["context"], card["context"]
    assert f"«P{last + 1}»" not in card["context"]
    assert f"{n} projects you can see" in card["context"]


class TestFixRound1:
    pytestmark = _DB_GATE

    async def test_the_tree_read_makes_no_walk_per_folder(self, live, monkeypatch):
        from gateway.routes.projects import authority

        for i in range(4):
            _node(live, f"op10 nwalk folder {i}", live.team, kind="folder")
        # CREATOR leads nothing and is no admin, so every team folder
        # falls to the owner arm. Test 5 already gave VIEWER a personal root.
        owner = CREATOR
        root = str(live.seeder.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses, personal_owner) VALUES ('op10 nwalk mine', 'active',"
            " 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata', true, :m) RETURNING id",
            m=owner, o=live.org))
        live.seeder.status(root, "To do", "todo", 0)
        wa.d._grant(live.seeder, root, owner)
        mine = _node(live, "op10 nwalk my folder", root, kind="folder",
                     maker=owner, owner=owner)
        wa.d._grant(live.seeder, mine, owner)

        walks: list[str] = []
        real = authority._tree_owner

        async def counted(db: Any, pid: str) -> Any:
            walks.append(pid)
            return await real(db, pid)

        monkeypatch.setattr(authority, "_tree_owner", counted)
        r = await wa._as(live, owner, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        assert walks == [], f"{len(walks)} walks on one tree read"
        tree = r.json()
        assert _find(tree["rows"], live.team)["can_convert"] is False
        assert _find(tree["personal"], mine)["can_convert"] is True

    async def _hold_folder(self, live, folder: str):
        """An open transaction that holds the folder as ``convert_node`` does."""
        conn = live.admin.connect()
        trans = conn.begin()
        conn.execute(text("SELECT kind FROM pm_projects WHERE id = CAST(:p AS uuid)"
                          " FOR UPDATE"), {"p": folder})
        return conn, trans

    async def _waits_on_a_lock(self, live, task) -> bool:
        import asyncio
        for _ in range(60):
            if task.done():
                return False
            with live.admin.connect() as c:
                waiting = c.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                    " AND datname = current_database()")).scalar()
            if waiting:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _race(self, live, act) -> Any:
        """Hold the folder, start *act*, see it wait, convert, then read its answer."""
        import asyncio

        folder = _node(live, "op10 race folder", live.team, kind="folder")
        child = _node(live, "op10 race child", folder)
        conn, trans = await self._hold_folder(live, folder)
        try:
            task = asyncio.ensure_future(act(child))
            blocked = await self._waits_on_a_lock(live, task)
            conn.execute(text("UPDATE pm_projects SET kind = 'project'"
                              " WHERE id = CAST(:p AS uuid)"), {"p": folder})
            trans.commit()
        finally:
            conn.close()
        r = await task
        assert blocked, f"the write did not wait for the convert: {r.status_code} {r.text}"
        return r, child

    async def test_a_create_under_a_child_waits_for_the_convert_and_is_refused(self, live):
        async def create(child: str) -> Any:
            return await wa._as(live, ADMIN, "POST", "/projects/nodes",
                                {"name": "op10 race sub", "parent_project_id": child})

        r, child = await self._race(live, create)
        assert r.status_code == 422, r.text
        with live.admin.begin() as c:
            assert c.execute(text(
                "SELECT count(*) FROM pm_projects WHERE parent_project_id = CAST(:p AS uuid)"),
                {"p": child}).scalar() == 0

    async def test_a_move_under_a_child_waits_for_the_convert_and_is_refused(self, live):
        mover = _node(live, "op10 race mover", live.team)

        async def move(child: str) -> Any:
            return await wa._as(live, ADMIN, "POST", f"/projects/nodes/{mover}/move",
                                {"parent_project_id": child})

        r, child = await self._race(live, move)
        assert r.status_code == 422, r.text
        with live.admin.begin() as c:
            assert str(c.execute(text(
                "SELECT parent_project_id FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": mover}).scalar()) == live.team

    async def test_the_422_says_who_can_move_a_kept_team_child(self, live):
        owner = LEAD
        root = str(live.seeder.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses, personal_owner) VALUES ('op10 kept mine', 'active',"
            " 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata', true, :m) RETURNING id",
            m=owner, o=live.org))
        live.seeder.status(root, "To do", "todo", 0)
        wa.d._grant(live.seeder, root, owner)
        folder = _node(live, "op10 kept folder", root, kind="folder",
                       maker=owner, owner=owner)
        wa.d._grant(live.seeder, folder, owner)
        # A team node that migration 297 kept unstamped under the private tree,
        # with a subproject, beside one of the owner's own.
        team = _node(live, "op10 kept team", folder)
        _node(live, "op10 kept team sub", team)
        own = _node(live, "op10 kept own", folder, maker=owner, owner=owner)
        _node(live, "op10 kept own sub", own, maker=owner, owner=owner)
        r = await _convert(live, owner, folder)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert "Move 'op10 kept own' out of the folder first." in detail, detail
        assert ("A lead of 'op10 kept team' or a Projects admin can move it."
                in detail), detail
        assert "Move 'op10 kept team'" not in detail
        assert _row(live, folder).kind == "folder"
