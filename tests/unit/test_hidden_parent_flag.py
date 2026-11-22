"""Task 3852, slice 1 — the consumers read ``parent_hidden``, database-free.

``project_management_app.md`` §14.9 rule 4: a hidden space is never named.
Today the gateway sends the raw ``parent_project_id`` of a node whose parent
the member cannot see. Slice 2 (task 3885) sends ``null`` there. Each
consumer below read "a parent id on a top row" as "my parent is hidden", so
each now reads the flag that the gateway sends on every node.

The node of slice 2 is the shape under test here:
``{"parent_project_id": None, "parent_hidden": True}``. Each test fails on
the code before this change (the red-before of the report). An old gateway
sends no flag, and the parent id still decides, so each old test stays.

The R8 half (the flag and the move guard on a real database) is
``test_hidden_parent_flag_r8.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import guarded as G
from skill_projects import reads as R
from skill_projects import writes as W

from tests.unit import test_mcp_edge_projects as ep
from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_mcp_edge_projects import edge, redis_fake  # noqa: F401 — the fixtures

CHILD = "3f8fad5b-d9cb-469f-a165-70867728950e"
SIBLING = "4f8fad5b-d9cb-469f-a165-70867728950e"
SPACE = "5f8fad5b-d9cb-469f-a165-70867728950e"
HIDDEN_FOLDER = ep._uuid(7001)
UNDER_HIDDEN = ep._uuid(7002)

#: The shape of slice 2: no id for the parent, and the flag.
SLICE_TWO = {"parent_project_id": None, "parent_hidden": True}


# ═══════════════════════════════════════════════════════════════════════════
# The MCP edge: the two sites
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEdge:
    async def test_a_folder_under_a_hidden_parent_lets_the_gateway_decide(self, edge):  # noqa: F811
        """``_check_parent_level``. Before: no id above the folder raised
        TEXT_BUSY, so the agent could make no project in the folder."""
        edge.gateway.nodes[HIDDEN_FOLDER] = ep._node(HIDDEN_FOLDER, "folder", None,
                                                     parent_hidden=True)
        result = await ep._tool("create_project", {"name": "x", "level": "project",
                                                   "parent_id": HIDDEN_FOLDER})
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests][:2] == [
            ("GET", f"/projects/nodes/{HIDDEN_FOLDER}"), ("POST", "/projects/nodes")]

    async def test_a_folder_with_no_parent_and_no_flag_is_still_busy(self, edge):  # noqa: F811
        """A folder never sits at the top. With no flag, the edge does not guess."""
        edge.gateway.nodes[HIDDEN_FOLDER] = ep._node(HIDDEN_FOLDER, "folder", None)
        result = await ep._tool("create_project", {"name": "x", "level": "project",
                                                   "parent_id": HIDDEN_FOLDER})
        assert ep._text(result) == ep.tools.TEXT_BUSY
        assert ep._writes(edge.gateway) == []

    async def test_a_project_under_a_hidden_parent_is_not_a_space(self, edge):  # noqa: F811
        """``_check_destination``. Before: a null parent id read as a space,
        and the move was refused with TEXT_MOVE_INTO_SPACE."""
        edge.gateway.nodes[UNDER_HIDDEN] = ep._node(UNDER_HIDDEN, "project", None,
                                                    parent_hidden=True)
        result = await ep._tool("preview_move_tasks", ep._move_args(
            destination_project_id=UNDER_HIDDEN))
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests] == [
            ("GET", f"/projects/nodes/{UNDER_HIDDEN}"), ("POST", "/projects/tasks/move/preview")]

    async def test_a_true_root_is_still_a_space(self, edge):  # noqa: F811
        edge.gateway.nodes[UNDER_HIDDEN] = ep._node(UNDER_HIDDEN, "project", None,
                                                    parent_hidden=False)
        result = await ep._tool("preview_move_tasks", ep._move_args(
            destination_project_id=UNDER_HIDDEN))
        assert ep._text(result) == ep.tools.TEXT_MOVE_INTO_SPACE
        assert ep._writes(edge.gateway) == []


# ═══════════════════════════════════════════════════════════════════════════
# The chat: move_project, the null hazard
# ═══════════════════════════════════════════════════════════════════════════


def _gateway(node: dict[str, Any], tree: dict[str, Any]):
    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/nodes/{CHILD}":
            return node
        if path == "/projects/tree":
            return tree
        if method == "POST":
            return {"id": CHILD, **(call["json"] or {})}
        return {"rows": [], "total": 0}

    return answer


def _hidden_tree() -> dict[str, Any]:
    """Slice 2's tree: the child draws at the top with no parent id."""
    return {"rows": [
        {"id": SPACE, "name": "Sales", "parent_project_id": None, "parent_hidden": False,
         "position": 100.0, "children": []},
        {"id": CHILD, "name": "Granted", **SLICE_TWO, "position": 200.0, "children": []},
    ]}


class TestTheChatMove:
    async def test_a_reorder_under_a_hidden_parent_posts_no_null_move(self, monkeypatch):
        """``_move_target``. Before: the current parent was null, so the
        reorder planned at the top and posted ``parent_project_id: null``,
        which ``move_node`` reads as a real re-parent to the top level."""
        asked = approve(monkeypatch)
        node = {"id": CHILD, "name": "Granted", **SLICE_TWO}
        calls = fake_gateway(monkeypatch, _gateway(node, _hidden_tree()))
        out = await skill_projects.move_project(CHILD, place="first")
        assert out == G.HIDDEN_PARENT_REFUSAL
        assert asked == [] and writes(calls) == []

    async def test_to_top_level_under_a_hidden_parent_is_refused_before_the_card(
            self, monkeypatch):
        """Before: a null parent id read as "already a space"."""
        asked = approve(monkeypatch)
        node = {"id": CHILD, "name": "Granted", **SLICE_TWO}
        calls = fake_gateway(monkeypatch, _gateway(node, _hidden_tree()))
        out = await skill_projects.move_project(CHILD, to_top_level=True)
        assert out == G.HIDDEN_PARENT_REFUSAL
        assert asked == [] and writes(calls) == []

    async def test_a_true_root_reorder_never_ranks_against_the_hidden_one(self, monkeypatch):
        """``_plan_place``. Before: the hidden one was a true-root sibling, so
        a spread posted ``parent_project_id: null`` for it."""
        approve(monkeypatch)
        tree = {"rows": [
            {"id": SPACE, "name": "Sales", "parent_project_id": None, "position": None,
             "children": []},
            {"id": SIBLING, "name": "Ops", "parent_project_id": None, "position": None,
             "children": []},
            {"id": CHILD, "name": "Granted", **SLICE_TWO, "position": None, "children": []},
        ]}

        def answer(call: dict) -> Any:
            if call["path"] == f"/projects/nodes/{SIBLING}":
                return {"id": SIBLING, "name": "Ops", "parent_project_id": None,
                        "parent_hidden": False}
            if call["path"] == "/projects/tree":
                return tree
            return {"id": SIBLING}

        calls = fake_gateway(monkeypatch, answer)
        await skill_projects.move_project(SIBLING, place="first")
        moved = [c["path"] for c in writes(calls)]
        assert f"/projects/nodes/{CHILD}/move" not in moved
        assert moved == [f"/projects/nodes/{SPACE}/move", f"/projects/nodes/{SIBLING}/move"]

    async def test_a_true_root_moves_to_a_parent_as_before(self, monkeypatch):
        """No regression: a node with a visible parent and no flag moves."""
        approve(monkeypatch)
        node = {"id": CHILD, "name": "Granted", "parent_project_id": SPACE,
                "parent_hidden": False}
        calls = fake_gateway(monkeypatch, _gateway(node, {"rows": [
            {"id": SPACE, "name": "Sales", "parent_project_id": None, "position": None,
             "children": [{"id": CHILD, "name": "Granted", "parent_project_id": SPACE,
                           "position": None, "children": []}]}]}))
        await skill_projects.move_project(CHILD, to_top_level=True)
        assert [c["json"] for c in writes(calls)] == [{"parent_project_id": None}]


# ═══════════════════════════════════════════════════════════════════════════
# The chat: the other readers of a root
# ═══════════════════════════════════════════════════════════════════════════


class TestTheChatRootReaders:
    def test_is_root(self):
        assert R.is_root({"parent_project_id": None}) is True
        assert R.is_root({"parent_project_id": None, "parent_hidden": False}) is True
        assert R.is_root(SLICE_TWO) is False
        assert R.is_root({"parent_project_id": SPACE}) is False

    def test_a_top_row_under_a_hidden_parent_is_not_a_team_space(self):
        """``writes.is_team_space``. Before: True, so a task was refused."""
        assert W.is_team_space({"kind": "project", **SLICE_TWO}) is False
        assert W.is_team_space({"kind": "project", "parent_project_id": None}) is True

    def test_my_tasks_reads_the_flag(self):
        """``skill_my_tasks.core._is_team_space``. Before: True."""
        pytest.importorskip("skill_my_tasks", reason="skill-my-tasks not installed")
        from skill_my_tasks.core import _is_team_space

        assert _is_team_space({"kind": "project", **SLICE_TWO}) is False
        assert _is_team_space({"kind": "project", "parent_project_id": None}) is True

    def test_the_level_rules_bind_a_node_under_a_hidden_parent(self):
        """``writes._level_refusal``. Before: "" (a root), so the icon card showed."""
        assert W._level_refusal({"name": "Granted", **SLICE_TWO}, {"icon": "rocket"})
        assert W._level_refusal({"name": "Sales", "parent_project_id": None},
                                {"icon": "rocket"}) == ""

    async def test_the_walk_to_the_space_names_no_space(self, monkeypatch):
        """``writes._root_of``. Before: it returned the node itself as the root."""
        fake_gateway(monkeypatch, lambda call: {})
        space = await W._space_of(CHILD, {"id": CHILD, "name": "Granted", **SLICE_TWO})
        assert space.name is None and space.words == W.HIDDEN_SPACE

    async def test_the_last_grant_clause_is_for_a_true_root(self):
        """``guarded._revoke_warnings``. Before: a null parent read as a top project."""
        row = {"id": "g1", "yours": False}
        hidden = await G._revoke_warnings({"id": CHILD, **SLICE_TWO}, row, {"rows": [row]})
        assert G.LAST_GRANT_CLAUSE not in hidden
        root = await G._revoke_warnings({"id": SPACE, "parent_project_id": None}, row,
                                        {"rows": [row]})
        assert G.LAST_GRANT_CLAUSE in root

    async def test_the_tree_calls_a_top_row_under_a_hidden_parent_a_project(self, monkeypatch):
        """``reads.projects_tree``. Before: it read as a space."""
        fake_gateway(monkeypatch, lambda call: {"total": 1, "rows": [
            {"id": CHILD, "name": "Granted", "kind": "project", **SLICE_TWO,
             "children": []}]})
        out = await skill_projects.projects_tree()
        assert "[project]" in out and "[space]" not in out
