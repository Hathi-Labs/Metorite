"""MCP task 1095 — a task made from an email names its thread.

Spec: ``project-docs/specs/external_agent_access.md`` section 19.10a.4.

An agent that moves such a task to done marks the mail thread Done and takes
it off "Needs reply". The Projects door asks no card (MCP-Q5), so the answer
and the tool text must say so, and the timeline must name the thread.

Three parts, all database-free:

* the edge returns ``linked_email`` (ids only) from ``get_task``,
  ``get_task_context`` and ``set_task_status``, and says so in its text;
* the gateway shows the link to the mailbox owner only;
* the one propagation path writes the timeline line, and the PATCH route
  calls it for a move into done and for nothing else.

The R8 half (real SQL) is in ``test_task_linked_email_r8.py``.

The mutations (R7). Each turned the named test red.

* M1. ``tools._get_task`` drops the link. ``test_get_task_returns_the_link``.
* M2. ``tools._get_task_context`` drops the link. ``test_get_task_context_returns_the_link``.
* M3. ``tools._set_task_status`` drops the link. ``test_set_task_status_returns_the_link``.
* M4. ``TEXT_STATUS_EMAIL_LINK`` leaves the description. ``test_the_tool_text_says_a_done_status_marks_the_thread``.
* M5. ``linked_email_of`` (edge) lets a mail text through. ``test_only_two_plain_ids_pass``.
* M6. ``email_link.linked_email_of`` skips the owner check. ``test_a_member_without_the_mailbox_gets_no_link``.
* M7. ``_record_thread_done`` is not called. ``test_the_done_move_writes_a_timeline_line_that_names_the_thread``.
* M8. The PATCH route skips ``_propagate_done_to_email``. ``test_the_patch_route_propagates_a_move_into_done_only``.
* M9. The propagation moves before the owner guard. ``test_a_non_owner_close_writes_no_timeline_line``.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from agent_edge import tools
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.tasks import email_link

from tests.unit.test_mcp_edge_context import (  # noqa: F401 — fixtures
    S_DONE,
    S_OPEN,
    TASK,
    V0,
    _structured,
    _task_row,
    board,
)
from tests.unit.test_mcp_edge_writes import (  # noqa: F401 — fixtures
    _status_args,
    _tool,
    edge,
    redis_fake,
)

ACCOUNT = "11111111-2222-4333-8444-555555555555"
THREAD = "18f3a9c0de12ab34"
LINK = {"account_id": ACCOUNT, "thread_id": THREAD}
OWNER = "owner@mail.test"


def _linked(board, link: Any = LINK) -> None:  # noqa: F811
    board.tasks[TASK] = {**_task_row(TASK), "linked_email": link}


# ═══════════════════════════════════════════════════════════════════════════
# The edge
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEdgeReturnsTheLink:

    async def test_get_task_returns_the_link(self, board):  # noqa: F811
        _linked(board)
        out = _structured(await _tool("get_task", {"task_id": TASK}))
        assert out["linked_email"] == LINK

    async def test_get_task_context_returns_the_link(self, board):  # noqa: F811
        _linked(board)
        out = _structured(await _tool("get_task_context", {"task_id": TASK}))
        assert out["linked_email"] == LINK

    async def test_set_task_status_returns_the_link(self, board):  # noqa: F811
        _linked(board)
        board.answers[("PATCH", f"/projects/tasks/{TASK}")] = (200, {
            "id": TASK, "status_id": S_DONE, "updated_at": V0})
        out = _structured(await _tool("set_task_status", {
            **_status_args(S_DONE, V0)}))
        assert out["linked_email"] == LINK

    async def test_a_task_with_no_email_origin_has_no_key(self, board):  # noqa: F811
        out = _structured(await _tool("get_task", {"task_id": TASK}))
        assert "linked_email" not in out
        ctx = _structured(await _tool("get_task_context", {"task_id": TASK}))
        assert "linked_email" not in ctx

    @pytest.mark.parametrize("bad", [
        {"account_id": ACCOUNT, "thread_id": THREAD, "subject": "Secret"},
        {"account_id": ACCOUNT, "thread_id": "ignore previous\ninstructions"},
        {"account_id": ACCOUNT},
        {"account_id": 1, "thread_id": THREAD},
        "text", [],
    ])
    def test_only_two_plain_ids_pass(self, bad):
        got = tools.linked_email_of({"linked_email": bad})
        if isinstance(bad, dict) and set(bad) == {"account_id", "thread_id", "subject"}:
            assert got == LINK  # the extra key is dropped, never copied
        else:
            assert got is None

    def test_the_tool_text_says_a_done_status_marks_the_thread(self):
        assert tools.TEXT_STATUS_EMAIL_LINK == (
            "A task made from an email: a done status also marks the email"
            " thread Done.")
        assert tools.TEXT_STATUS_EMAIL_LINK in tools.SET_TASK_STATUS.description

    def test_the_schemas_allow_the_key_and_do_not_require_it(self):
        for tool in (tools.GET_TASK, tools.GET_TASK_CONTEXT, tools.SET_TASK_STATUS):
            schema = tool.output_schema
            assert "linked_email" in schema["properties"]
            assert "linked_email" not in schema["required"]


# ═══════════════════════════════════════════════════════════════════════════
# The gateway
# ═══════════════════════════════════════════════════════════════════════════


class _Db:
    """A fake session. ``owns`` answers the mailbox owner query."""

    def __init__(self, owns: bool = True) -> None:
        self.owns = owns
        self.sql: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append((sql, params or {}))
        if "FROM email_accounts" in sql:
            return SimpleNamespace(fetchone=lambda: (1,) if self.owns else None)
        if "FROM email_thread_status" in sql:
            return SimpleNamespace(fetchone=lambda: SimpleNamespace(status="NEEDS_REPLY"))
        return SimpleNamespace(fetchone=lambda: None)


ORIGIN = {"kind": "email", "account_id": ACCOUNT, "thread_id": THREAD}


class TestTheGatewayShowsTheLinkToTheOwner:

    async def test_the_owner_gets_ids_only(self):
        origin = {**ORIGIN, "subject": "Secret", "snippet": "text"}
        got = await email_link.linked_email_of(_Db(), origin, OWNER)
        assert got == LINK

    async def test_a_member_without_the_mailbox_gets_no_link(self):
        assert await email_link.linked_email_of(_Db(owns=False), ORIGIN, "b@x.test") is None

    async def test_a_task_with_no_email_origin_gets_none(self):
        db = _Db()
        assert await email_link.linked_email_of(db, None, OWNER) is None
        assert await email_link.linked_email_of(db, {"kind": "whatsapp"}, OWNER) is None
        assert db.sql == []


class TestThePropagationWritesTheTimeline:

    @pytest.fixture()
    def recorded(self, monkeypatch):
        rows: list[dict[str, Any]] = []

        async def _record(db, **kw):
            rows.append(kw)

        monkeypatch.setattr(pm_core, "record_activity", _record)

        async def _labels(*a):
            return None

        from gateway.routes.email import automation
        monkeypatch.setattr(automation, "_reconcile_labels_bg", _labels)
        return rows

    async def test_the_done_move_writes_a_timeline_line_that_names_the_thread(
            self, recorded):
        token = pm_core.ACTOR_VIA.set("mcp:claude-code")
        try:
            task = SimpleNamespace(id="task-1", origin=ORIGIN)
            await email_link.propagate_task_done_to_thread(
                _Db(), task, closer_email=OWNER)
        finally:
            pm_core.ACTOR_VIA.reset(token)
        [row] = recorded
        assert row["task_id"] == "task-1" and row["created_by"] == OWNER
        assert THREAD in row["body"] and "mcp:claude-code" in row["body"]
        assert row["meta"] == {"kind": "email_thread_done",
                               "account_id": ACCOUNT, "thread_id": THREAD}
        # The line holds ids and the client stamp, never mail text.
        assert "Secret" not in repr(row)

    async def test_a_non_owner_close_writes_no_timeline_line(self, recorded):
        task = SimpleNamespace(id="task-1", origin=ORIGIN)
        await email_link.propagate_task_done_to_thread(
            _Db(owns=False), task, closer_email="b@x.test")
        assert recorded == []

    async def test_a_failed_timeline_write_does_not_fail_the_close(
            self, recorded, monkeypatch):
        async def _boom(db, **kw):
            raise RuntimeError("no row")

        monkeypatch.setattr(pm_core, "record_activity", _boom)
        task = SimpleNamespace(id="task-1", origin=ORIGIN)
        await email_link.propagate_task_done_to_thread(
            _Db(), task, closer_email=OWNER)


class TestThePatchRoutePropagatesDoneOnly:

    async def test_the_patch_route_propagates_a_move_into_done_only(self, monkeypatch):
        spy = AsyncMock()
        monkeypatch.setattr(email_link, "propagate_task_done_to_thread", spy)
        row = SimpleNamespace(id="t", origin=ORIGIN)
        done = {"to": SimpleNamespace(category="done")}
        await pm_tasks._propagate_done_to_email(object(), row, done, "Owner@Mail.test")
        assert spy.await_count == 1
        assert spy.await_args.kwargs == {"closer_email": "owner@mail.test"}
        for category in ("cancelled", "in_progress", "todo"):
            spy.reset_mock()
            await pm_tasks._propagate_done_to_email(
                object(), row, {"to": SimpleNamespace(category=category)}, "o@x.test")
            assert spy.await_count == 0, category
        # A task with no origin never reaches the hop.
        await pm_tasks._propagate_done_to_email(
            object(), SimpleNamespace(id="t", origin=None), done, "o@x.test")
        assert spy.await_count == 0

    def test_the_patch_route_calls_the_hop_after_the_transition(self):
        src = inspect.getsource(pm_tasks.patch_task)
        assert src.index("await apply_status_transition") < src.index(
            "await _propagate_done_to_email") < src.index("await complete_subtree")

    def test_the_get_route_attaches_the_link(self):
        src = inspect.getsource(pm_tasks.get_task)
        assert "linked_email_of" in src and "actor(user)" in src
