"""WS-55 MCP-G12, MCP-G14 and MCP-G10: the write tools say what they do.

Three gaps of the write tools, one file.

* **G12.** ``update_task`` takes null for ``due_at``, ``start_date`` and
  ``estimate_mins``. Null clears the field. A field that the call leaves out
  stays as it is.
* **G14.** ``create_task`` with the Epic type and a parent answers one sentence
  that names the rule, and not the generic bad-request text.
* **G10.** The refusal of the write cap holds ``retry_after`` in seconds, and
  the transport sends ``Retry-After``. The write tools carry ONE line that
  names the limit. A busy answer that holds ``Retry-After`` passes it on.

**Database-free.** The edge runs in process against the fake gateway of
``test_mcp_edge_task_writes.py``. The burst test puts the REAL
``deps._claim_write`` (branch 0's cap) in front of that fake, on the fake
Redis with a clock.

**R8.** The edge calls the REAL gateway app on a promoted catalog. The null
tests read the row and its version. The Epic test reads the real refusal.

Run::

    docker exec metorite-scratch-tenant createdb -U acb <private_db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db>
    PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest tests/unit/test_mcp_edge_write_clarity.py -v -rs

**The mutations (R7).** Each one turns the named test red.

* C1. ``tools.py``: ``due_at`` of ``_UPDATE_INPUT`` loses ``"null"``. ``test_null_clears_the_planning_fields``.
* C2. ``tools.py``: ``check_input`` drops a null of a null-typed input. ``test_null_clears_the_planning_fields``.
* C3. ``tools.py``: the create ``_write`` loses ``details``. ``test_an_epic_with_a_parent_names_the_rule``.
* C4. ``core.py``: the Epic detail changes. ``test_the_epic_detail_is_the_one_of_the_gateway``.
* C5. ``tools.py``: ``_body_of`` drops ``answer.retry_after`` on a 429. ``test_a_burst_past_the_limit_holds_retry_after_and_a_wait_clears_it``.
* C6. ``mcp.py``: no ``Retry-After`` header. The same test.
* C7. ``gateway_client.py``: ``_retry_after`` returns None. ``test_a_busy_answer_passes_on_its_retry_after``.
* C8. ``tools.py``: ``Tool.describe`` drops the limit line. ``test_every_write_tool_carries_the_limit_line_once``.
* C9. ``WRITE_CAP`` or ``WRITE_WINDOW_S`` changes. ``test_the_limit_text_is_pinned``.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_signing, deps
from agent_edge import gateway_client, mcp, tools
from fastapi import HTTPException

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w

_DB_GATE = w._DB_GATE
edge, redis_fake = t.edge, t.redis_fake
world, live_app, live = t.world, t.live_app, t.live
_mcp, _row = t._mcp, t._row
_tool, _text, _post, _call = w._tool, w._text, w._post, w._call
TASK, PROJECT, V1, V3 = w.TASK, w.PROJECT, w.V1, t.V3
_update_args, _create_args = t._update_args, t._create_args

ROOT = Path(__file__).resolve().parents[2]
EPIC_DETAIL = "An Epic cannot have a parent. Leave out type_id or parent_task_id."


# ═══════════════════════════════════════════════════════════════════════════
# G12: null clears
# ═══════════════════════════════════════════════════════════════════════════


class TestNullClearsAtTheEdge:
    """MCP-G12, database-free."""

    async def test_null_clears_the_planning_fields(self, edge):
        result = await _tool("update_task", _update_args(
            due_at=None, start_date=None, estimate_mins=None))
        assert result["isError"] is False, result
        [patch] = edge.gateway.writes()
        # A null is sent as a null, and it is not dropped.
        assert json.loads(patch.body) == {
            "due_at": None, "start_date": None, "estimate_mins": None}
        assert result["structuredContent"]["task"]["version"] == V3

    async def test_a_field_left_out_is_not_sent(self, edge):
        await _tool("update_task", _update_args(title="Only the title"))
        [patch] = edge.gateway.writes()
        assert json.loads(patch.body) == {"title": "Only the title"}

    async def test_one_null_does_not_touch_the_other_fields(self, edge):
        await _tool("update_task", _update_args(due_at=None))
        [patch] = edge.gateway.writes()
        assert json.loads(patch.body) == {"due_at": None}

    @pytest.mark.parametrize("args", [
        {"due_at": ""}, {"start_date": "soon"}, {"estimate_mins": -1},
        {"estimate_mins": "5"}])
    async def test_a_bad_value_is_still_refused(self, edge, args):
        result = await _tool("update_task", _update_args(**args))
        assert result["isError"] is True
        assert edge.gateway.writes() == []

    async def test_the_schema_and_the_description_name_null(self, edge):
        listed = {tool["name"]: tool for tool in tools.listed(
            frozenset({agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE}))}
        update = listed["update_task"]
        for name in ("due_at", "start_date", "estimate_mins"):
            assert "null" in update["inputSchema"]["properties"][name]["type"]
        assert "Send null for due_at, start_date or estimate_mins to clear it" in (
            update["description"])
        # create_task takes no null: a new task has nothing to clear.
        create = listed["create_task"]["inputSchema"]["properties"]
        assert "null" not in create["due_at"]["type"]


class TestNullClearsOnARealDatabase:
    """MCP-G12 against the real route: the column clears, the version moves."""

    pytestmark = _DB_GATE

    async def test_update_task_with_null_clears_the_fields_and_bumps_the_version(self, live):
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "has dates", "due_at": "2026-12-01",
            "start_date": "2026-11-20", "estimate_mins": 90})
        assert made["isError"] is False, made
        task = made["structuredContent"]["task"]
        before = _row(live, task["id"])
        assert before.due_at is not None and before.estimate_mins == 90
        assert str(before.start_date) == "2026-11-20"
        cleared = await _mcp(live, "write", "update_task", {
            "task_id": task["id"], "expected_version": task["version"],
            "due_at": None, "start_date": None, "estimate_mins": None})
        assert cleared["isError"] is False, cleared
        after = _row(live, task["id"])
        assert (after.due_at, after.start_date, after.estimate_mins) == (None, None, None)
        new = cleared["structuredContent"]["task"]
        assert new["version"] != task["version"]
        assert (new["due_at"], new["start_date"], new["estimate_mins"]) == (None, None, None)
        # The old version is stale now.
        stale = await _mcp(live, "write", "update_task", {
            "task_id": task["id"], "expected_version": task["version"], "title": "x"})
        assert _text(stale) == tools.TEXT_STALE

    async def test_a_field_left_out_keeps_its_value(self, live):
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "keeps dates", "due_at": "2026-12-02",
            "estimate_mins": 30})
        task = made["structuredContent"]["task"]
        changed = await _mcp(live, "write", "update_task", {
            "task_id": task["id"], "expected_version": task["version"],
            "title": "keeps dates 2"})
        assert changed["isError"] is False, changed
        row = _row(live, task["id"])
        assert row.due_at is not None and row.estimate_mins == 30


# ═══════════════════════════════════════════════════════════════════════════
# G14: an Epic cannot have a parent
# ═══════════════════════════════════════════════════════════════════════════


class TestAnEpicWithAParent:
    """MCP-G14."""

    async def test_an_epic_with_a_parent_names_the_rule(self, edge):
        edge.gateway.answers[("POST", "/projects/tasks")] = (
            422, {"detail": tools.EPIC_PARENT_DETAIL})
        result = await _tool("create_task", _create_args(parent_task_id=TASK, type_id=t.TYPE))
        assert _text(result) == EPIC_DETAIL
        assert _text(result) == tools.TEXT_EPIC_PARENT
        assert tools.TEXT_BAD_REQUEST not in json.dumps(result)

    async def test_another_422_stays_the_bad_request_text(self, edge):
        edge.gateway.answers[("POST", "/projects/tasks")] = (422, {"detail": "Other."})
        result = await _tool("create_task", _create_args(parent_task_id=TASK, type_id=t.TYPE))
        assert _text(result) == tools.TEXT_BAD_REQUEST

    async def test_the_refusal_frees_the_key(self, edge):
        edge.gateway.answers[("POST", "/projects/tasks")] = (
            422, {"detail": tools.EPIC_PARENT_DETAIL})
        args = _create_args(parent_task_id=TASK, type_id=t.TYPE, idempotency_key="epic-key-0001")
        assert _text(await _tool("create_task", args)) == EPIC_DETAIL
        assert _text(await _tool("create_task", args)) == EPIC_DETAIL

    def test_the_epic_detail_is_the_one_of_the_gateway(self):
        source = (ROOT / "apps/services/gateway/gateway/routes/projects/core.py").read_text(
            encoding="utf-8")
        assert f'detail="{tools.EPIC_PARENT_DETAIL}"' in source

    async def test_the_descriptions_state_the_rule(self, edge):
        listed = {tool["name"]: tool["description"] for tool in tools.listed(
            frozenset({agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE}))}
        assert "An Epic cannot have a parent" in listed["create_task"]
        assert "An Epic cannot have a parent" in listed["update_task"]

    async def test_a_move_of_an_epic_keeps_its_own_clear_text(self, edge):
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/move")] = (
            422, {"detail": tools.EPIC_PARENT_DETAIL})
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": t.OTHER_TASK})
        assert _text(result) == tools.TEXT_MOVE_EPIC


class TestAnEpicOnARealDatabase:
    """MCP-G14 against the real route."""

    pytestmark = _DB_GATE

    def _epic(self, live) -> str:
        return str(live.seeder.one(
            "INSERT INTO pm_task_types (project_id, name, organization_id, is_epic)"
            " VALUES (CAST(:p AS uuid), :n, CAST(:o AS uuid), true) RETURNING id",
            p=live.seeder.root(live.team), o=live.org, n=f"Epic {uuid.uuid4().hex[:8]}"))

    async def test_create_with_the_epic_type_and_a_parent_says_the_rule(self, live):
        epic = self._epic(live)
        parent = live.task(live.team, live.s.todo, "an epic parent")
        refused = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "epic under a task",
            "type_id": epic, "parent_task_id": parent})
        assert _text(refused) == EPIC_DETAIL
        assert t._count(live, "epic under a task") == 0
        # Either input alone works.
        top = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "epic at the top", "type_id": epic})
        assert top["isError"] is False, top
        sub = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "plain subtask", "parent_task_id": parent})
        assert sub["isError"] is False, sub

    async def test_a_move_of_an_epic_under_a_parent_says_the_rule(self, live):
        epic = self._epic(live)
        top = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "epic to move", "type_id": epic})
        task = top["structuredContent"]["task"]
        parent = live.task(live.team, live.s.todo, "would-be parent")
        moved = await _mcp(live, "write", "update_task", {
            "task_id": task["id"], "expected_version": task["version"],
            "parent_task_id": parent})
        assert _text(moved) == tools.TEXT_MOVE_EPIC


# ═══════════════════════════════════════════════════════════════════════════
# G10: the cap, retry_after and the limit line
# ═══════════════════════════════════════════════════════════════════════════


class _CapGateway:
    """The REAL write cap of branch 0 (``deps._claim_write``) in front of the
    fake gateway. A write past the cap gets the real 429 and its header."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send) -> None:
        if scope["method"] != "GET":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            connection = SimpleNamespace(
                scopes=frozenset({agent_oauth.PROJECTS_WRITE}),
                organization_id=w.ORG, connection_id=w.CONN)
            try:
                refused = await deps._claim_write(
                    connection, headers[agent_signing.HEADER_NONCE.lower()],
                    agent_oauth.PROJECTS_WRITE)
                error = None if refused is None else HTTPException(401, refused)
            except HTTPException as exc:
                error = exc
            if error is not None:
                raw = [(b"content-type", b"application/json"),
                       *[(k.lower().encode(), v.encode())
                         for k, v in (error.headers or {}).items()]]
                await send({"type": "http.response.start", "status": error.status_code,
                            "headers": raw})
                await send({"type": "http.response.body",
                            "body": json.dumps({"detail": error.detail}).encode()})
                return
        await self.inner(scope, receive, send)


class TestTheCapAnswersWithAWait:
    """MCP-G10 items 1 and 2."""

    async def test_a_burst_past_the_limit_holds_retry_after_and_a_wait_clears_it(
            self, edge, redis_fake, monkeypatch):
        monkeypatch.setattr(agent_oauth, "WRITE_CAP", 4)
        with gateway_client.use_transport(httpx.ASGITransport(app=_CapGateway(edge.gateway))):
            for n in range(4):
                ok = await _tool("update_task", _update_args(title=f"burst {n}"))
                assert ok["isError"] is False, ok
            over = await _post(_call("update_task", _update_args(title="burst 4")))
            assert over.status_code == 200
            result = over.json()["result"]
            assert result["isError"] is True
            assert result["content"][0]["text"] == tools.TEXT_CAP
            wait = result["structuredContent"]["retry_after"]
            assert wait == agent_oauth.WRITE_WINDOW_S == 600
            assert result["content"][1]["text"] == f"Try again in {wait} seconds."
            # The transport sends the header too.
            assert over.headers["retry-after"] == str(wait)
            # Waiting the named time frees the window, and the write lands.
            redis_fake.now += wait
            later = await _tool("update_task", _update_args(title="after the wait"))
            assert later["isError"] is False, later
        assert "retry_after" not in (later.get("structuredContent") or {})

    async def test_a_success_carries_no_retry_after_header(self, edge):
        r = await _post(_call("update_task", _update_args(title="fine")))
        assert "retry-after" not in r.headers

    async def test_a_cap_answer_without_a_header_names_no_wait(self, edge):
        edge.gateway.answers[("PATCH", f"/projects/tasks/{TASK}")] = (429, {"detail": "x"})
        result = await _tool("update_task", _update_args(title="x"))
        assert _text(result) == tools.TEXT_CAP
        assert "structuredContent" not in result

    @pytest.mark.parametrize("raw,expected", [
        ("17", 17), (" 5 ", 5), ("0", 1), ("999999", 3600),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("-3", None), ("", None), (None, None)])
    def test_the_header_is_read_as_whole_seconds(self, raw, expected):
        assert gateway_client._retry_after(raw) == expected

    async def test_a_busy_answer_passes_on_its_retry_after(self, edge):
        route = ("PATCH", f"/projects/tasks/{TASK}")
        edge.gateway.answers[route] = (503, {"detail": "secret"})
        edge.gateway.answer_headers[route] = {"Retry-After": "2"}
        r = await _post(_call("update_task", _update_args(title="x")))
        result = r.json()["result"]
        assert result["content"][0]["text"] == tools.TEXT_BUSY
        assert result["structuredContent"] == {"retry_after": 2}
        assert r.headers["retry-after"] == "2"
        assert "secret" not in json.dumps(result)

    async def test_a_busy_answer_with_no_header_stays_as_it_was(self, edge):
        edge.gateway.answers[("PATCH", f"/projects/tasks/{TASK}")] = (503, {})
        result = await _tool("update_task", _update_args(title="x"))
        assert _text(result) == tools.TEXT_BUSY
        assert "structuredContent" not in result


class TestTheLimitLine:
    """MCP-G10 item 2: ONE short line on the write tools, not in the server
    instructions."""

    def test_the_limit_text_is_pinned(self):
        assert (agent_oauth.WRITE_CAP, agent_oauth.WRITE_WINDOW_S) == (120, 600)
        assert tools.WRITE_LIMIT_LINE == (
            " Limit: a connection makes at most 120 changes in 10 minutes, counted"
            " from its first change. Past it, a refusal holds retry_after, the"
            " seconds to wait.")
        assert len(tools.WRITE_LIMIT_LINE) < 200

    async def test_every_write_tool_carries_the_limit_line_once(self, edge):
        listed = [tool.describe() for tool in tools.TOOLS.values()]
        writes = {name for name, tool in tools.TOOLS.items() if tool.method != "GET"}
        assert {"update_task", "create_task", "add_comment", "set_task_status"} <= writes
        seen = {tool["name"]: tool["description"] for tool in listed}
        for name in writes & seen.keys():
            assert seen[name].count(tools.WRITE_LIMIT_LINE) == 1, name
        for name in set(seen) - writes:
            assert "Limit: a connection makes" not in seen[name], name

    def test_the_server_instructions_do_not_carry_it(self):
        for name in ("_ROUTINE_INSTRUCTIONS", "_RUN_INSTRUCTIONS"):
            text = getattr(mcp, name, "")
            assert "Limit: a connection makes" not in text
            assert "retry_after" not in text
