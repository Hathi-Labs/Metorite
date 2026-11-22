"""Task 3852, slice 1: ``parent_hidden`` and the move guard, on a real database (R8).

``project_management_app.md`` §14.9 rule 4: a hidden space is never named.
A member who sees a child space but not its parent got the parent's id from
the tree. Slice 2 (task 3885) sends ``null`` there. This slice adds the flag
that the clients read first, and a guard on the one write that a null parent
id would turn into a real act.

Access is HIGH, and hermetic fakes agree with whatever SQL they get, so this
file runs the real routes on a promoted catalog, as a NON-privileged role
that FORCE ROW LEVEL SECURITY binds. The world is the one of
``test_projects_write_authority.py``. Each test makes its own rows.

* The member who sees the child and not its parent gets ``parent_hidden:
  true`` on the tree, the node list, the node read and a write echo. A member
  who sees both gets ``false``.
* ``move_node`` refuses that member a move to the top level (403), with words
  that name no parent. It allows a top-level move to a member who sees the
  parent, and it does not refuse a reorder under the same parent.
* The chat refuses before its card, through the real gateway.

Run (one private ladder database)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://...
    uv run pytest tests/unit/test_hidden_parent_flag_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red.

* M1. ``tree.attach_parent_hidden``: stamp ``False`` on every node.
  ``test_r8_the_member_who_cannot_see_the_parent_gets_the_flag``.
* M2. ``tree.attach_parent_hidden``: treat every parent outside *known* as
  hidden (drop the ``project_clause`` read).
  ``test_r8_a_member_who_sees_both_gets_false``.
* M3. ``tree.move_node``: drop the guard.
  ``test_r8_the_guard_refuses_a_move_to_the_top_level``.
* M4. ``tree.move_node``: refuse every null move of a node with a parent.
  ``test_r8_a_member_who_sees_the_parent_moves_it_to_the_top``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from sqlalchemy import text

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import approve
from tests.unit.test_projects_grant_tools_r8 import OUTSIDER, _private
from tests.unit.test_projects_write_authority import (  # noqa: F401 — the fixtures
    LEAD,
    _as,
    _chat,
    live,
    live_app,
    redis_fake,
    world,
)

pytestmark = w._DB_GATE


def _name() -> str:
    return f"h3852 {uuid.uuid4().hex[:6]}"


def _child(wld: Any, parent: str, *, lead: str | None = None) -> str:
    """A project under *parent*, granted to OUTSIDER and led by *lead*."""
    pid = str(wld.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, parent_project_id, lead) VALUES (:n, 'active', 'manual',"
        " :c, CAST(:o AS uuid), 'Asia/Kolkata', true, CAST(:p AS uuid), :l) RETURNING id",
        n=_name(), c=LEAD, o=wld.org, p=parent, l=lead))
    d._grant(wld.seeder, pid, OUTSIDER)
    return pid


def _row(wld: Any, pid: str) -> Any:
    with wld.admin.begin() as c:
        return c.execute(text(
            "SELECT parent_project_id, position FROM pm_projects WHERE id = CAST(:i AS uuid)"),
            {"i": pid}).first()


def _find(rows: list[dict[str, Any]], pid: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("id") == pid:
            return row
        found = _find(row.get("children") or [], pid)
        if found is not None:
            return found
    return None


def _words() -> str:
    from gateway.routes.projects.tree import HIDDEN_PARENT_MOVE_REFUSED

    return HIDDEN_PARENT_MOVE_REFUSED


async def _flags(wld: Any, who: str, pid: str) -> dict[str, Any]:
    """The flag on each read that returns the node."""
    tree = (await _as(wld, who, "GET", "/projects/tree")).json()
    listed = (await _as(wld, who, "GET", "/projects/nodes")).json()
    node = await _as(wld, who, "GET", f"/projects/nodes/{pid}")
    assert node.status_code == 200, node.text
    in_tree = _find(tree["rows"], pid)
    in_list = next(r for r in listed["rows"] if r["id"] == pid)
    return {"tree": in_tree["parent_hidden"], "nodes": in_list["parent_hidden"],
            "node": node.json()["parent_hidden"], "tree_row": in_tree,
            "top": any(r["id"] == pid for r in tree["rows"])}


async def test_r8_the_member_who_cannot_see_the_parent_gets_the_flag(live) -> None:  # noqa: F811
    parent = _private(live, _name())
    child = _child(live, parent)
    assert (await _as(live, OUTSIDER, "GET", f"/projects/nodes/{parent}")).status_code == 404
    got = await _flags(live, OUTSIDER, child)
    assert got["top"], "the child draws at the top for the outsider"
    assert (got["tree"], got["nodes"], got["node"]) == (True, True, True)
    # A write echo carries it too.
    patched = await _as(live, OUTSIDER, "PATCH", f"/projects/nodes/{child}",
                        {"description": "h3852"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["parent_hidden"] is True


async def test_r8_a_member_who_sees_both_gets_false(live) -> None:  # noqa: F811
    parent = _private(live, _name())
    child = _child(live, parent)
    got = await _flags(live, LEAD, child)
    assert not got["top"], "the child draws under its parent for the lead"
    assert (got["tree"], got["nodes"], got["node"]) == (False, False, False)
    assert (await _flags(live, LEAD, parent))["tree"] is False
    # A node made under a visible parent says false in its echo.
    made = await _as(live, OUTSIDER, "POST", "/projects/nodes",
                     {"name": _name(), "parent_project_id": child})
    assert made.status_code == 201, made.text
    assert made.json()["parent_hidden"] is False


async def test_r8_the_guard_refuses_a_move_to_the_top_level(live) -> None:  # noqa: F811
    """OUTSIDER leads the child, so only the guard can refuse this move."""
    parent = _private(live, _name())
    child = _child(live, parent, lead=OUTSIDER)
    r = await _as(live, OUTSIDER, "POST", f"/projects/nodes/{child}/move",
                  {"parent_project_id": None})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == _words()
    assert str(_row(live, child).parent_project_id) == parent, "the child stays in its parent"


async def test_r8_a_member_who_sees_the_parent_moves_it_to_the_top(live) -> None:  # noqa: F811
    parent = _private(live, _name())
    child = _child(live, parent, lead=OUTSIDER)
    r = await _as(live, LEAD, "POST", f"/projects/nodes/{child}/move",
                  {"parent_project_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["parent_hidden"] is False
    assert _row(live, child).parent_project_id is None


async def test_r8_a_reorder_under_the_same_parent_is_not_refused(live) -> None:  # noqa: F811
    parent = _private(live, _name())
    child = _child(live, parent, lead=OUTSIDER)
    r = await _as(live, LEAD, "POST", f"/projects/nodes/{child}/move",
                  {"parent_project_id": parent, "position": 7.5})
    assert r.status_code == 200, r.text
    assert r.json()["parent_hidden"] is False
    row = _row(live, child)
    assert str(row.parent_project_id) == parent and row.position == 7.5


async def test_r8_the_members_own_area_has_a_visible_parent(live) -> None:  # noqa: F811
    """The personal tree. Its parent is in the response, but not in the team
    rows that ``get_tree`` passes as *known*, so the tree asks
    ``project_clause``. A true answer here would make the chat refuse to
    reorder the member's own Areas."""
    made = await _as(live, OUTSIDER, "POST", "/projects/my/areas", {"name": _name()})
    assert made.status_code == 201, made.text
    area = made.json()["id"]
    tree = (await _as(live, OUTSIDER, "GET", "/projects/tree")).json()
    in_tree = _find(tree["personal"], area)
    assert in_tree is not None and in_tree["parent_hidden"] is False
    node = await _as(live, OUTSIDER, "GET", f"/projects/nodes/{area}")
    assert node.status_code == 200, node.text
    assert node.json()["parent_hidden"] is False


async def test_r8_a_team_node_under_another_members_personal_root(live) -> None:  # noqa: F811
    """Migration 297 can keep a TEAM node under a personal root. Its owner
    sees the root, and no one else does. So the flag is true for the lead of
    the team node, and the guard refuses that lead a move to the top level.
    The owner sees the parent, so the guard lets the owner's move go on to
    the checks that follow it."""
    # One personal root for each member (`uq_pm_projects_personal_root`), so
    # the route makes it or returns the one that exists.
    mine = await _as(live, OUTSIDER, "POST", "/projects/my/project")
    assert mine.status_code == 201, mine.text
    root = mine.json()["id"]
    team = _child(live, root, lead=LEAD)
    d._grant(live.seeder, team, LEAD)
    lead_view = (await _as(live, LEAD, "GET", f"/projects/nodes/{team}")).json()
    assert lead_view["parent_hidden"] is True
    owner_view = (await _as(live, OUTSIDER, "GET", f"/projects/nodes/{team}")).json()
    assert owner_view["parent_hidden"] is False
    r = await _as(live, LEAD, "POST", f"/projects/nodes/{team}/move",
                  {"parent_project_id": None})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == _words()
    assert str(_row(live, team).parent_project_id) == root


async def test_r8_the_chat_refuses_before_its_card(live, monkeypatch) -> None:  # noqa: F811
    """Through the real gateway: the node read carries the flag, and the
    chat posts no move."""
    from skill_projects import guarded as G

    parent = _private(live, _name())
    child = _child(live, parent, lead=OUTSIDER)
    asked = approve(monkeypatch)
    out = await _chat(live, monkeypatch, OUTSIDER,
                      lambda: G.move_project(child, to_top_level=True))
    assert out == G.HIDDEN_PARENT_REFUSAL
    assert asked == []
    assert str(_row(live, child).parent_project_id) == parent
