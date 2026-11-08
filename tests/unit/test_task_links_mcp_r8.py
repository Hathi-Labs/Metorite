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
* L9. ``G/relations.py``: ``delete_task_link`` writes no activity. ``test_an_agent_removal_writes_one_row_on_each_task`` and ``test_a_person_removal_writes_the_record_too``.
* L10. ``G/tasks.py``: ``delete_link`` passes no ``far_end_vis``. ``test_a_pinned_agent_cannot_remove_a_link_that_leaves_its_pin``.
* L11. ``G/tasks.py``: ``delete_link`` passes ``far_end_vis`` for a person too. ``test_a_person_may_still_remove_a_link_to_a_hidden_task``.
* L12. ``G/relations.py``: ``insert_task_link`` loses ``if not row.inserted``. ``test_a_second_identical_add_writes_nothing``.
* L13. ``E/tools.py``: ``_meta_link`` loses ``link_action``. ``test_the_timeline_keeps_the_mark_of_a_removal``.
* L14. ``G/activities.py``: ``get_timeline`` skips ``drop_unseen_link_targets``. ``test_a_pinned_agent_reads_no_outside_id``.
* L15. ``G/relations.py``: ``drop_unseen_link_targets`` drops each target. ``test_a_reader_who_sees_the_far_end_keeps_the_target``.
* L16. ``G/relations.py``: ``assert_no_block_cycle`` compares the ids as they came. ``test_an_upper_case_id_cannot_close_a_loop``.
* L17. ``E/tools.py``: the two link tools send the ids as they came. ``test_the_edge_sends_the_ids_in_lower_case``.
* L18. ``G/relations.py``: ``assert_no_block_cycle`` takes no lock. ``test_two_adds_in_opposite_directions_store_no_loop`` and, with no database, ``test_projects_relations.py::test_the_lock_comes_before_the_first_read_of_the_graph``.
* L19. ``E/tools.py``: ``_remove_task_link`` skips the kind check. ``test_remove_refuses_a_link_that_is_not_blocks``.
"""
from __future__ import annotations

import asyncio
import contextlib
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
        assert "my_work does not offer the target task until task_id is done" in text_
        assert "claim_task on the target task is not refused" in text_

    async def test_the_edge_sends_the_ids_in_lower_case(self, monkeypatch):
        sent: list[Any] = []

        async def read(_ctx, _route, _not_found, **_kw):
            return {"links": [{"id": ID_B, "link_id": ID_A, "link_type": "blocks",
                               "direction": "outgoing"}]}

        async def write(_ctx, method, _route, _not_found, **kw):
            sent.append((method, kw["path_params"], kw["json_body"]))
            return 200, {"id": ID_A}

        monkeypatch.setattr(tools, "_get", read)
        monkeypatch.setattr(tools, "_write", write)
        made = await tools._add_task_link(None, {
            "task_id": ID_A.upper(), "target_task_id": ID_B.upper(), "link_type": "blocks"})
        gone = await tools._remove_task_link(None, {
            "task_id": ID_A.upper(), "link_id": ID_A.upper()})
        assert (made["task_id"], made["target_task_id"]) == (ID_A, ID_B)
        assert (gone["task_id"], gone["link_id"]) == (ID_A, ID_A)
        assert sent == [
            ("POST", {"task_id": ID_A}, {"target_task_id": ID_B, "link_type": "blocks"}),
            ("DELETE", {"task_id": ID_A, "link_id": ID_A}, {}),
        ]

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

    def test_the_timeline_keeps_the_mark_of_a_removal(self):
        kept = tools.timeline_meta("link", {
            "target": ID_B, "type": "blocks", "direction": "incoming",
            "link_action": "removed", "intake": "x"})
        assert kept == {"target": ID_B, "type": "blocks", "direction": "incoming",
                        "link_action": "removed"}

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


def _link_rows(live, task: str) -> list[Any]:
    """The ``link`` rows of the timeline of *task*, oldest first."""
    with live.admin.begin() as c:
        return list(c.execute(text(
            "SELECT created_by, body, meta FROM pm_activities"
            " WHERE task_id = CAST(:t AS uuid) AND type = 'link' ORDER BY seq"),
            {"t": task}))


def _stamps(live, *tasks: str) -> list[Any]:
    return [t._row(live, task).updated_at for task in tasks]


def _pinned(live) -> None:
    """A write connection that is pinned to the team project."""
    if "pinned" not in live.c:
        live.c["pinned"], live.tok["pinned"] = live.seeder.connection(
            scopes=w.WRITE_SCOPES, pins=(live.team,))


class TestTheRecordOfALink:
    """What a link write leaves on the timeline, and the reach of a removal."""

    pytestmark = _DB_GATE

    async def test_an_agent_removal_writes_one_row_on_each_task(self, live):
        a = live.task(live.team, live.s.todo, "sw16a record A")
        b = live.task(live.team, live.s.todo, "sw16a record B")
        link_id = (await _link(live, a, b))["structuredContent"]["link_id"]
        seen_a, seen_b = len(_link_rows(live, a)), len(_link_rows(live, b))
        removed = await _mcp(live, "write", "remove_task_link", {
            "task_id": a, "link_id": link_id})
        assert removed["isError"] is False, removed
        new_a, new_b = _link_rows(live, a)[seen_a:], _link_rows(live, b)[seen_b:]
        assert len(new_a) == 1 and len(new_b) == 1, (new_a, new_b)
        for row, other, side in ((new_a[0], b, "outgoing"), (new_b[0], a, "incoming")):
            assert row.created_by == w.MEMBER
            assert row.body == "Removed a link"
            assert row.meta == {"target": other, "type": "blocks", "direction": side,
                                "link_action": "removed", "via": t.R8_VIA}
        # An agent reads that record, and it does not read as an add.
        read = await _mcp(live, "write", "get_task_timeline", {"task_id": b, "kind": "events"})
        newest = read["structuredContent"]["activities"][0]
        assert newest["type"] == "link" and newest["created_by"] == w.MEMBER
        assert newest["meta"]["link_action"] == "removed"
        assert newest["meta"]["via"] == t.R8_VIA

    async def test_a_person_removal_writes_the_record_too(self, live):
        a = live.task(live.team, live.s.todo, "sw16a person A")
        b = live.task(live.team, live.s.todo, "sw16a person B")
        link_id = (await _link(live, a, b))["structuredContent"]["link_id"]
        seen_a, seen_b = len(_link_rows(live, a)), len(_link_rows(live, b))
        sent = await t._send(live, "DELETE", f"/projects/tasks/{b}/links/{link_id}",
                             signed=None)
        assert sent.status_code == 200, sent.text
        new_a, new_b = _link_rows(live, a)[seen_a:], _link_rows(live, b)[seen_b:]
        assert len(new_a) == 1 and len(new_b) == 1, (new_a, new_b)
        for row, other, side in ((new_a[0], b, "outgoing"), (new_b[0], a, "incoming")):
            assert row.created_by == w.MEMBER
            assert row.body == "Removed a link"
            assert row.meta == {"target": other, "type": "blocks", "direction": side,
                                "link_action": "removed"}

    async def test_a_pinned_agent_cannot_remove_a_link_that_leaves_its_pin(self, live):
        _pinned(live)
        inside = live.task(live.team, live.s.todo, "sw16a pin inside")
        outside = live.task(live.other, live.s.other, "sw16a pin outside")
        link_id = (await _link(live, inside, outside))["structuredContent"]["link_id"]
        stamps = _stamps(live, inside, outside)
        rows = len(_link_rows(live, inside)), len(_link_rows(live, outside))
        missing_id = str(uuid.uuid4())
        refused = await _mcp(live, "pinned", "remove_task_link", {
            "task_id": inside, "link_id": link_id})
        missing = await _mcp(live, "pinned", "remove_task_link", {
            "task_id": inside, "link_id": missing_id})
        assert _text(refused) == _text(missing) == tools.TEXT_LINK_NOT_FOUND
        # The gateway gives the answer of a missing link, word for word.
        path = "/projects/tasks/" + inside + "/links/"
        direct = await t._send(live, "DELETE", path + link_id, signed="pinned")
        gone = await t._send(live, "DELETE", path + missing_id, signed="pinned")
        assert direct.status_code == gone.status_code == 404
        assert direct.json() == gone.json()
        # Nothing was written: the row stays, and neither task moved.
        assert len(_links(live, inside)) == 1
        assert _stamps(live, inside, outside) == stamps
        assert (len(_link_rows(live, inside)), len(_link_rows(live, outside))) == rows
        # A link with both ends inside the pin still goes.
        other_inside = live.task(live.team, live.s.todo, "sw16a pin inside 2")
        ok_id = (await _link(live, inside, other_inside))["structuredContent"]["link_id"]
        ok = await _mcp(live, "pinned", "remove_task_link", {
            "task_id": other_inside, "link_id": ok_id})
        assert ok["isError"] is False, ok

    async def test_a_person_may_still_remove_a_link_to_a_hidden_task(self, live):
        seeder = live.seeder
        hidden_project = seeder.project("sw16a far end", in_space=True)
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        hidden_status = seeder.status(hidden_project, "To do", "todo", 0)
        hidden = seeder.task(hidden_project, hidden_status, "sw16a far task")
        mine = live.task(live.team, live.s.todo, "sw16a far mine")
        link_id = str(seeder.one(
            "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type, created_by)"
            " VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 'blocks', :m) RETURNING id",
            a=mine, b=hidden, m=w.MEMBER))
        # The member's agent does not see that end, so the link is not there for it.
        refused = await _mcp(live, "write", "remove_task_link", {
            "task_id": mine, "link_id": link_id})
        assert _text(refused) == tools.TEXT_LINK_NOT_FOUND
        assert len(_links(live, mine)) == 1
        # The member still removes it from the end they see, as before SW-16a.
        sent = await t._send(live, "DELETE", f"/projects/tasks/{mine}/links/{link_id}",
                             signed=None)
        assert sent.status_code == 200, sent.text
        assert _links(live, mine) == []

    async def test_a_second_identical_add_writes_nothing(self, live):
        a = live.task(live.team, live.s.todo, "sw16a again A")
        b = live.task(live.team, live.s.todo, "sw16a again B")
        first = await _link(live, a, b)
        assert first["structuredContent"]["created"] is True
        assert len(_link_rows(live, a)) == 1, "the first add writes its one row"
        stamps = _stamps(live, a, b)
        rows = len(_link_rows(live, a)), len(_link_rows(live, b))
        second = await _link(live, a, b)
        assert second["isError"] is False, second
        assert second["structuredContent"]["created"] is False
        assert (len(_link_rows(live, a)), len(_link_rows(live, b))) == rows
        assert _stamps(live, a, b) == stamps


async def _person_link(live, source: str, target: str, kind: str = "blocks") -> str:
    """The member adds a link in the app. Returns its id."""
    sent = await t._send(live, "POST", f"/projects/tasks/{source}/links",
                         body={"target_task_id": target, "link_type": kind}, signed=None)
    assert sent.status_code == 201, sent.text
    return str(sent.json()["id"])


async def _link_events(live, name: str, task: str) -> list[dict[str, Any]]:
    """The ``link`` rows that connection *name* reads, newest first."""
    read = await _mcp(live, name, "get_task_timeline", {"task_id": task, "kind": "events"})
    return [r for r in read["structuredContent"]["activities"] if r["type"] == "link"]


class TestTheReachAndTheLoopOfALink:
    """The far end of a timeline row, the loop check and the kind of a removal."""

    pytestmark = _DB_GATE

    async def test_a_pinned_agent_reads_no_outside_id(self, live):
        _pinned(live)
        inside = live.task(live.team, live.s.todo, "sw16a far inside")
        outside = live.task(live.other, live.s.other, "sw16a far outside")
        link_id = await _person_link(live, inside, outside)
        # After the add. The row of the add is on the source task.
        added = await _link_events(live, "pinned", inside)
        assert added[0]["meta"] == {"type": "blocks"}, added
        assert outside not in str(added)
        # After a person removes the link.
        sent = await t._send(live, "DELETE", f"/projects/tasks/{inside}/links/{link_id}",
                             signed=None)
        assert sent.status_code == 200, sent.text
        removed = await _link_events(live, "pinned", inside)
        assert removed[0]["meta"] == {
            "type": "blocks", "direction": "outgoing", "link_action": "removed"}, removed
        assert outside not in str(removed)
        # The route of the app gives the agent no more than the tool does.
        direct = await t._send(live, "GET", f"/projects/tasks/{inside}/timeline",
                               signed="pinned")
        assert direct.status_code == 200, direct.text
        assert outside not in direct.text
        # The stored row keeps the target, for a reader who can see both ends.
        assert _link_rows(live, inside)[-1].meta["target"] == outside

    async def test_a_reader_who_sees_the_far_end_keeps_the_target(self, live):
        _pinned(live)
        a = live.task(live.team, live.s.todo, "sw16a near")
        b = live.task(live.team, live.s.todo, "sw16a far")
        await _person_link(live, a, b)
        for name in ("write", "pinned"):
            rows = await _link_events(live, name, a)
            assert rows[0]["meta"] == {"target": b, "type": "blocks"}, (name, rows)
        person = await t._send(live, "GET", f"/projects/tasks/{a}/timeline", signed=None)
        assert person.status_code == 200, person.text
        kept = [r for r in person.json()["rows"] if r["type"] == "link"]
        assert kept[0]["meta"] == {"target": b, "type": "blocks"}, kept

    async def test_an_upper_case_id_cannot_close_a_loop(self, live):
        a = live.task(live.team, live.s.todo, "sw16a case A")
        b = live.task(live.team, live.s.todo, "sw16a case B")
        assert (await _link(live, a, b))["isError"] is False
        assert _text(await _link(live, b.upper(), a)) == tools.TEXT_LINK_CYCLE
        assert _text(await _link(live, b, a.upper())) == tools.TEXT_LINK_CYCLE
        # A person reaches the same check through the route.
        for source, target in ((b.upper(), a), (b, a.upper())):
            sent = await t._send(live, "POST", f"/projects/tasks/{source}/links",
                                 body={"target_task_id": target, "link_type": "blocks"},
                                 signed=None)
            assert sent.status_code == 422, sent.text
            assert sent.json()["detail"] == tools.LINK_CYCLE_DETAIL
        selfed = await t._send(live, "POST", f"/projects/tasks/{a.upper()}/links",
                               body={"target_task_id": a, "link_type": "relates_to"},
                               signed=None)
        assert selfed.status_code == 422, selfed.text
        assert selfed.json()["detail"] == tools.LINK_SELF_DETAIL
        assert len(_links(live, a)) == 1

    async def test_two_adds_in_opposite_directions_store_no_loop(self, live, monkeypatch):
        from gateway.routes.projects import tasks as pm_tasks

        a = live.task(live.team, live.s.todo, "sw16a race A")
        b = live.task(live.team, live.s.todo, "sw16a race B")
        real = pm_tasks.insert_task_link
        both_here = asyncio.Event()
        arrived = 0

        # Each add waits after its check for the other one. With the lock the
        # second add cannot pass the check, so the first add waits out the
        # limit and commits. Without the lock both adds are past the check.
        async def paused(db, *args, **kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                both_here.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_here.wait(), 2)
            return await real(db, *args, **kwargs)

        monkeypatch.setattr(pm_tasks, "insert_task_link", paused)

        async def both() -> list[dict[str, Any]]:
            return list(await asyncio.gather(*(
                t._mcp_unscoped(live, "write", "add_task_link", {
                    "task_id": source, "target_task_id": target, "link_type": "blocks"})
                for source, target in ((a, b), (b, a)))))

        results = await t._in_scope(live, both)
        made = [r for r in results if r["isError"] is False]
        refused = [r for r in results if r["isError"] is not False]
        assert len(made) == 1 and len(refused) == 1, results
        assert _text(refused[0]) == tools.TEXT_LINK_CYCLE
        assert len(_links(live, a)) == 1

    async def test_remove_refuses_a_link_that_is_not_blocks(self, live):
        a = live.task(live.team, live.s.todo, "sw16a kind A")
        b = live.task(live.team, live.s.todo, "sw16a kind B")
        link_id = await _person_link(live, a, b, "relates_to")
        stamps = _stamps(live, a, b)
        rows = len(_link_rows(live, a)), len(_link_rows(live, b))
        refused = await _mcp(live, "write", "remove_task_link", {
            "task_id": a, "link_id": link_id})
        assert _text(refused) == tools.TEXT_LINK_NOT_BLOCKS
        assert "A person removes other kinds of link in the app." in _text(refused)
        # Nothing was written.
        assert len(_links(live, a)) == 1
        assert _stamps(live, a, b) == stamps
        assert (len(_link_rows(live, a)), len(_link_rows(live, b))) == rows
        # A person still removes it.
        sent = await t._send(live, "DELETE", f"/projects/tasks/{a}/links/{link_id}", signed=None)
        assert sent.status_code == 200, sent.text
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
