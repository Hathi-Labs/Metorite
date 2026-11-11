"""MCP task 1095 (R8) — the link and the timeline line, against real SQL.

Spec: ``project-docs/specs/external_agent_access.md`` section 19.10a.4.

As the non-privileged role ``acb_app_h3rls``, in one organization with two
members. Only the owner check hides the mailbox (the same setup as
``test_email_chat_context_owner.py``).

R7 fences named here:

* ``linked-email-owner-only``: the owner reads ``{account_id, thread_id}``,
  another member of the organization reads nothing.
* ``linked-email-timeline``: a done move through the PATCH hop writes one
  ``pm_activities`` row that names the thread, and marks the thread DONE.

Mutations. M1: ``linked_email_of`` skips the owner check, so
``test_only_the_owner_reads_the_link`` goes red. M2: ``_record_thread_done`` is
not called, so ``test_the_done_move_writes_the_timeline_line`` goes red.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_task_linked_email_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import db as common_db
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.tasks import email_link
from sqlalchemy import text

from tests.unit import _agent_board_r8 as board
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_chat_context_owner import (
    _assert_non_priv,
    _purge,
    _seed_mailbox_of_a,
    _thread_status,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def _link_task(admin, task_id: str, account: str, thread: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET origin = CAST(:o AS jsonb) WHERE id = CAST(:t AS uuid)"),
            {"t": task_id, "o": json.dumps({
                "kind": "email", "account_id": account, "thread_id": thread})})


def _lines(admin, task_id: str) -> list:
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT type, body, meta, created_by FROM pm_activities "
            "WHERE task_id = CAST(:t AS uuid) ORDER BY created_at"), {"t": task_id}))


@_DB_GATE
class TestTheLinkedEmail:

    @pytest.fixture()
    def world(self, promoted, app_engine, monkeypatch):  # noqa: F811
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"a-{uuid.uuid4().hex[:8]}@em-1095.test"
        other = f"b-{uuid.uuid4().hex[:8]}@em-1095.test"
        acc, _, thread = _seed_mailbox_of_a(p.admin_engine, org=p.org_b, owner=owner)
        proj = board.project(p, p.org_b, owner)
        tid = board.task(p, p.org_b, owner, proj)
        _link_task(p.admin_engine, tid, acc, thread)

        async def _labels(*a):
            return None

        from gateway.routes.email import automation
        monkeypatch.setattr(automation, "_reconcile_labels_bg", _labels)
        yield p, owner, other, acc, thread, tid, proj
        _purge(p.admin_engine, [acc])

    async def test_only_the_owner_reads_the_link(self, world):
        p, owner, other, acc, thread, tid, _proj = world
        dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), common_db.tenant_session(p.org_b) as db:
            row = (await db.execute(text(
                "SELECT origin FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                {"t": tid})).fetchone()
            got = await email_link.linked_email_of(db, row.origin, owner.upper())
            none = await email_link.linked_email_of(db, row.origin, other)
        assert got == {"account_id": acc, "thread_id": thread}
        assert none is None

    async def test_the_done_move_writes_the_timeline_line(self, world):
        p, owner, _other, acc, thread, tid, _ = world
        dsn = p.app_url.render_as_string(hide_password=False)
        token = pm_core.ACTOR_VIA.set("mcp:claude-code")
        try:
            async with tenant_engine_scope(dsn), common_db.tenant_session(p.org_b) as db:
                task = (await db.execute(text(
                    "SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                    {"t": tid})).fetchone()
                done = type("S", (), {"category": "done"})()
                await pm_tasks._propagate_done_to_email(
                    db, task, {"to": done}, owner)
        finally:
            pm_core.ACTOR_VIA.reset(token)
        assert _thread_status(p.admin_engine, acc, thread) == "DONE"
        [line] = [r for r in _lines(p.admin_engine, tid) if r.type == "system"]
        assert thread in line.body and "mcp:claude-code" in line.body
        assert line.meta["thread_id"] == thread and line.meta["account_id"] == acc
        assert line.meta["via"] == "mcp:claude-code"
        assert line.created_by == owner
        assert "hello" not in line.body  # the subject of the mail never lands

    async def test_a_non_owner_done_move_leaves_thread_and_timeline_alone(self, world):
        p, _owner, other, acc, thread, tid, _ = world
        dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), common_db.tenant_session(p.org_b) as db:
            task = (await db.execute(text(
                "SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                {"t": tid})).fetchone()
            done = type("S", (), {"category": "done"})()
            await pm_tasks._propagate_done_to_email(db, task, {"to": done}, other)
        assert _thread_status(p.admin_engine, acc, thread) == "NEEDS_REPLY"
        assert [r for r in _lines(p.admin_engine, tid) if r.type == "system"] == []
