"""SW-16a and SP-6 — ``add_task_link``, ``remove_task_link`` and ``update_task`` ``type_id``.

Specs: ``project-docs/specs/agent_coordination.md`` SW-16a, and
``project-docs/specs/project_management_app.md`` §13.6 SP-6 (items 3 and 4).

Two halves.

**Database-free (always runs).** The tool records, the direction text, the
input limits and the refusal texts. No refusal sends a request.

**R8 (a real database, as a NON-privileged role).** The edge calls the REAL
gateway app. It proves: a link made with ``add_task_link`` shows in
``get_task`` with the ``link_id`` that ``remove_task_link`` takes; the same
link twice keeps one row; a loop, a link from a task to itself and a target
that the member cannot see each give the plain refusal; a hidden task answers
the text of a task that does not exist; ``update_task`` sets and clears the
type, and an Epic type on a task with a parent is refused with a reason.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_task_links_mcp_r8.py -v -rs

**The mutations (R7).** ``E`` is ``apps/services/agent_edge/agent_edge``,
``G`` is ``apps/services/gateway/gateway/routes/projects``.

* L1. ``E/tools.py``: ``_link`` drops ``link_id``. ``test_a_link_shows_in_get_task_and_goes_away``.
* L2. ``E/tools.py``: ``_add_task_link`` swaps the two ids. ``test_task_id_blocks_the_target``.
* L3. ``G/tasks.py``: ``create_link`` skips the load of the target. ``test_a_hidden_target_reads_as_a_task_that_does_not_exist``.
* L4. ``E/tools.py``: ``_LINK_DETAILS`` loses the cycle row. ``test_a_loop_is_refused_in_plain_words``.
* L5. ``G/relations.py``: ``insert_task_link`` loses ``ON CONFLICT``. ``test_the_same_link_twice_keeps_one_row``.
* L6. ``G/tasks.py``: ``AGENT_PATCH_FIELDS`` loses ``type_id``. ``test_the_type_is_set_and_cleared``.
* L7. ``G/tasks.py``: ``patch_task`` drops ``assert_epic_has_no_parent``. ``test_an_epic_type_on_a_task_with_a_parent_is_refused``.
* L8. ``E/tools.py``: ``_update_task`` drops ``_check_type``. ``test_a_type_of_another_project_is_refused``.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth
from agent_edge import tools
from sqlalchemy import text

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit.test_mcp_edge_assignees import world  # noqa: F401  (the fixture)

_DB_GATE = w._DB_GATE
live_app = t.live_app
live = t.live
redis_fake = t.redis_fake
_mcp = t._mcp
_text = w._text

V1 = w.V1
LINKS = "/projects/tasks/{task_id}/links"
LINK = "/projects/tasks/{task_id}/links/{link_id}"
ID_A = "00000000-0000-4000-8000-0000000000a1"
ID_B = "00000000-0000-4000-8000-0000000000b2"


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the records and the texts
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRecords:
    def _listed(self, name: str) -> dict[str, Any]:
        return tools.TOOLS[name].describe()

    def test_both_routes_are_write_pairs(self):
        assert ("POST", LINKS) in agent_oauth.MCP_WRITE_ROUTES
        assert ("DELETE", LINK) in agent_oauth.MCP_WRITE_ROUTES

    def test_the_description_states_the_direction(self):
        text_ = self._listed("add_task_link")["description"]
        assert "task_id BLOCKS target_task_id" in text_
        assert "cannot start until task_id is done" in text_

    def test_link_type_is_blocks_only(self):
        schema = self._listed("add_task_link")["inputSchema"]["properties"]["link_type"]
        assert schema["enum"] == ["blocks"]
        assert self._listed("add_task_link")["inputSchema"]["required"] == [
            "task_id", "target_task_id", "link_type"]

    def test_remove_takes_a_task_and_a_link(self):
        schema = self._listed("remove_task_link")["inputSchema"]
        assert sorted(schema["required"]) == ["link_id", "task_id"]

    def test_both_are_writes_with_the_limit_line(self):
        for name in ("add_task_link", "remove_task_link"):
            entry = self._listed(name)
            assert entry["annotations"]["readOnlyHint"] is False
            assert tools.WRITE_LIMIT_LINE.strip() in entry["description"]

    def test_get_task_links_carry_the_link_id(self):
        assert "link_id" in tools.LINK_KEYS
        row = tools._link({"id": ID_B, "link_id": ID_A, "link_type": "blocks",
                           "direction": "outgoing", "title": "x"})
        assert row["link_id"] == ID_A and row["id"] == ID_B

    def test_the_details_are_the_ones_of_the_gateway(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "apps/services/gateway/gateway/routes/projects/relations.py").read_text(
            encoding="utf-8")
        tasks = (root / "apps/services/gateway/gateway/routes/projects/tasks.py").read_text(
            encoding="utf-8")
        flat = " ".join(source.split())
        assert tools.LINK_SELF_BLOCK_DETAIL in flat
        assert tools.LINK_CHAIN_DETAIL in flat
        # The cycle text is split over two source lines. Compare it flat.
        assert " ".join(tools.LINK_CYCLE_DETAIL.split()) in flat.replace('" "', "")
        assert tools.LINK_SELF_DETAIL in tasks

    def test_the_texts_say_what_happened_and_what_next(self):
        assert "Nothing changed" in tools.TEXT_LINK_CYCLE
        assert "other way round" in tools.TEXT_LINK_CYCLE
        assert "different tasks" in tools.TEXT_LINK_SELF

    def test_update_task_lists_type_id(self):
        schema = self._listed("update_task")["inputSchema"]["properties"]["type_id"]
        assert schema["type"] == ["string", "null"]
        assert "type_id" in tools.UPDATE_BODY_KEYS


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app
# ═══════════════════════════════════════════════════════════════════════════


def _links(live, task: str) -> list[Any]:
    with live.admin.begin() as c:
        return list(c.execute(text(
            "SELECT id, source_task_id, target_task_id, link_type FROM pm_task_links"
            " WHERE source_task_id = CAST(:t AS uuid) OR target_task_id = CAST(:t AS uuid)"),
            {"t": task}))


async def _link(live, blocker: str, waiter: str) -> dict[str, Any]:
    return await _mcp(live, "write", "add_task_link", {
        "task_id": blocker, "target_task_id": waiter, "link_type": "blocks"})


class TestLinksOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_a_link_shows_in_get_task_and_goes_away(self, live):
        made1 = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a A1"})
        made2 = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a A2"})
        a1 = made1["structuredContent"]["task"]["id"]
        a2 = made2["structuredContent"]["task"]["id"]
        result = await _link(live, a1, a2)
        assert result["isError"] is False, result
        got = result["structuredContent"]
        assert (got["task_id"], got["target_task_id"], got["created"]) == (a1, a2, True)
        # get_task shows the link on both ends, with the id that removes it.
        read1 = (await _mcp(live, "write", "get_task", {"task_id": a1}))["structuredContent"]
        read2 = (await _mcp(live, "write", "get_task", {"task_id": a2}))["structuredContent"]
        link1 = [x for x in read1["links"] if x["link_type"] == "blocks"]
        link2 = [x for x in read2["links"] if x["link_type"] == "blocks"]
        assert [(x["direction"], x["id"], x["link_id"]) for x in link1] == [
            ("outgoing", a2, got["link_id"])]
        assert [(x["direction"], x["id"], x["link_id"]) for x in link2] == [
            ("incoming", a1, got["link_id"])]
        # The remove works from either end.
        removed = await _mcp(live, "write", "remove_task_link", {
            "task_id": a2, "link_id": got["link_id"]})
        assert removed["isError"] is False, removed
        assert removed["structuredContent"]["removed"] is True
        assert _links(live, a1) == []
        again = await _mcp(live, "write", "remove_task_link", {
            "task_id": a1, "link_id": got["link_id"]})
        assert _text(again) == tools.TEXT_LINK_NOT_FOUND

    async def test_task_id_blocks_the_target(self, live):
        blocker = live.task(live.team, live.s.todo, "sw16a blocker")
        waiter = live.task(live.team, live.s.todo, "sw16a waiter")
        assert (await _link(live, blocker, waiter))["isError"] is False
        rows = _links(live, blocker)
        assert [(str(r.source_task_id), str(r.target_task_id), r.link_type) for r in rows] == [
            (blocker, waiter, "blocks")]

    async def test_the_same_link_twice_keeps_one_row(self, live):
        a = live.task(live.team, live.s.todo, "sw16a twice A")
        b = live.task(live.team, live.s.todo, "sw16a twice B")
        first = await _link(live, a, b)
        second = await _link(live, a, b)
        assert second["isError"] is False, second
        assert first["structuredContent"]["created"] is True
        assert second["structuredContent"]["created"] is False
        assert first["structuredContent"]["link_id"] == second["structuredContent"]["link_id"]
        assert len(_links(live, a)) == 1

    async def test_a_loop_is_refused_in_plain_words(self, live):
        a = live.task(live.team, live.s.todo, "sw16a loop A")
        b = live.task(live.team, live.s.todo, "sw16a loop B")
        c = live.task(live.team, live.s.todo, "sw16a loop C")
        assert (await _link(live, a, b))["isError"] is False
        assert (await _link(live, b, c))["isError"] is False
        direct = await _link(live, b, a)
        assert _text(direct) == tools.TEXT_LINK_CYCLE
        around = await _link(live, c, a)
        assert _text(around) == tools.TEXT_LINK_CYCLE
        assert len(_links(live, a)) == 1 and len(_links(live, c)) == 1

    async def test_a_link_from_a_task_to_itself_is_refused(self, live):
        a = live.task(live.team, live.s.todo, "sw16a self")
        refused = await _link(live, a, a)
        assert _text(refused) == tools.TEXT_LINK_SELF
        assert _links(live, a) == []
        # The route refuses it too, with the text that the edge maps.
        sent = await t._send(live, "POST", f"/projects/tasks/{a}/links",
                             body={"target_task_id": a, "link_type": "blocks"})
        assert sent.status_code == 422
        assert sent.json()["detail"] == tools.LINK_SELF_DETAIL

    async def test_a_hidden_target_reads_as_a_task_that_does_not_exist(self, live):
        seeder = live.seeder
        hidden_project = seeder.project("sw16a hidden", in_space=True)
        # The member holds a grant on the project and on its space, and a grant
        # on the space reaches the project. Take both away.
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        hidden_status = seeder.status(hidden_project, "To do", "todo", 0)
        hidden = seeder.task(hidden_project, hidden_status, "sw16a hidden task")
        mine = live.task(live.team, live.s.todo, "sw16a visible")
        made_up = str(uuid.uuid4())
        # A hidden target and a made-up target give one text.
        hidden_target = await _link(live, mine, hidden)
        missing_target = await _link(live, mine, made_up)
        assert _text(hidden_target) == _text(missing_target) == tools.TEXT_TASK_NOT_FOUND
        # So do a hidden task_id and a made-up task_id.
        hidden_source = await _link(live, hidden, mine)
        missing_source = await _link(live, made_up, mine)
        assert _text(hidden_source) == _text(missing_source) == tools.TEXT_TASK_NOT_FOUND
        # Nothing was written, on either side.
        assert _links(live, mine) == [] and _links(live, hidden) == []

    async def test_a_read_only_connection_cannot_link(self, live):
        a = live.task(live.team, live.s.todo, "sw16a ro A")
        b = live.task(live.team, live.s.todo, "sw16a ro B")
        with pytest.raises(AssertionError, match="Insufficient scope"):
            await _mcp(live, "read", "add_task_link", {
                "task_id": a, "target_task_id": b, "link_type": "blocks"})
        with pytest.raises(AssertionError, match="Insufficient scope"):
            await _mcp(live, "read", "remove_task_link", {
                "task_id": a, "link_id": str(uuid.uuid4())})
        assert _links(live, a) == []


class TestTheTypeOnARealDatabase:
    pytestmark = _DB_GATE

    def _type(self, live, *, epic: bool = False, project: str | None = None) -> str:
        return str(live.seeder.one(
            "INSERT INTO pm_task_types (project_id, name, organization_id, is_epic)"
            " VALUES (CAST(:p AS uuid), :n, CAST(:o AS uuid), :e) RETURNING id",
            p=live.seeder.root(project or live.team), o=live.org, e=epic,
            n=f"T {uuid.uuid4().hex[:8]}"))

    async def test_the_type_is_set_and_cleared(self, live):
        kind = self._type(live)
        task = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a typed"})
        made = task["structuredContent"]["task"]
        set_ = await _mcp(live, "write", "update_task", {
            "task_id": made["id"], "expected_version": made["version"], "type_id": kind})
        assert set_["isError"] is False, set_
        assert set_["structuredContent"]["task"]["type_id"] == kind
        cleared = await _mcp(live, "write", "update_task", {
            "task_id": made["id"], "expected_version": set_["structuredContent"]["task"]["version"],
            "type_id": None})
        assert cleared["isError"] is False, cleared
        assert cleared["structuredContent"]["task"]["type_id"] is None

    async def test_an_epic_type_on_a_task_with_a_parent_is_refused(self, live):
        epic = self._type(live, epic=True)
        parent = live.task(live.team, live.s.todo, "sw16a parent")
        child = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a child", "parent_task_id": parent})
        made = child["structuredContent"]["task"]
        refused = await _mcp(live, "write", "update_task", {
            "task_id": made["id"], "expected_version": made["version"], "type_id": epic})
        assert _text(refused) == tools.TEXT_EPIC_TYPE_HAS_PARENT
        assert "parent_task_id" in _text(refused)
        assert t._row(live, made["id"]).type_id is None
        # A task at the top level takes the Epic type.
        top = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a top"})
        top_task = top["structuredContent"]["task"]
        ok = await _mcp(live, "write", "update_task", {
            "task_id": top_task["id"], "expected_version": top_task["version"],
            "type_id": epic})
        assert ok["isError"] is False, ok

    async def test_a_type_of_another_project_is_refused(self, live):
        foreign = self._type(live, project=live.other)
        task = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "sw16a foreign type"})
        made = task["structuredContent"]["task"]
        refused = await _mcp(live, "write", "update_task", {
            "task_id": made["id"], "expected_version": made["version"], "type_id": foreign})
        assert _text(refused) == tools.TEXT_BAD_REQUEST
        assert t._row(live, made["id"]).type_id is None
