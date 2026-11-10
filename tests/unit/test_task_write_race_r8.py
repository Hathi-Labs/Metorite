"""Task 2427 (R8): a bulk edit that loses a deadlock answers 409, never 500.

A real database. The holder keeps the row of the HIGH task locked, and the
request locks the LOW task first and waits on the HIGH one. The holder then
asks for the LOW row. Postgres finds the deadlock and stops the request,
because the holder's probe waits 30 s. The route answers 409 and writes
nothing, and the same request sent again goes through.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import core
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_sprint_store_r8 import (  # noqa: F401
    _WAIT,
    _count,
    _node,
    _one,
    _session,
    _settle,
    _task,
    _until_lock_waits,
    w,
    world,
)

pytestmark = _DB_GATE


async def _race(w, route_call, check):
    """Run *route_call* against a held HIGH row, close the loop of the
    deadlock, and check the 409. Then send the same request again."""
    low, high = sorted(_task(w.p, w.p.org_a, w.alice, w.proj) for _ in range(2))
    async with _session(w.p) as call:
        holder = w.p.admin_engine.connect()
        running = None
        try:
            hold = holder.begin()
            holder.execute(text("SET LOCAL deadlock_timeout = '30s'"))
            holder.execute(text("UPDATE pm_tasks SET updated_at = now()"
                                " WHERE id = CAST(:t AS uuid)"), {"t": high})
            running = asyncio.ensure_future(route_call(call, [low, high]))
            await _until_lock_waits(w.p, "pm_tasks", stop=running)
            await asyncio.wait_for(asyncio.to_thread(
                holder.execute,
                text("UPDATE pm_tasks SET updated_at = now() WHERE id = CAST(:t AS uuid)"),
                {"t": low}), _WAIT)
            hold.commit()
            response = await asyncio.wait_for(running, _WAIT)
        finally:
            holder.close()
            await _settle(running)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == core.TASK_RACE_DETAIL
        check(low, high)
        again = await route_call(call, [low, high])
        assert again.status_code == 200, again.text


async def test_a_bulk_edit_that_loses_a_deadlock_answers_409_and_writes_nothing(w):
    def route(call, ids):
        return call(w.u_alice, "POST", "/projects/tasks/bulk",
                    {"task_ids": ids, "patch": {"title": "Renamed by bulk"}})

    def check(low, high):
        assert _count(w.p, "SELECT count(*) FROM pm_tasks"
                           " WHERE title = 'Renamed by bulk'") == 0

    await _race(w, route, check)


async def test_a_move_that_loses_a_deadlock_answers_409_and_writes_nothing(w):
    target = _node(w.p, w.p.org_a, w.alice, parent=w.space, grants=(w.alice,))

    def route(call, ids):
        return call(w.u_alice, "POST", "/projects/tasks/move",
                    {"task_ids": ids, "destination_project_id": target["id"],
                     "accept_drops": True})

    def check(low, high):
        assert _count(w.p, "SELECT count(*) FROM pm_tasks WHERE project_id = CAST(:proj AS uuid)"
                           " AND id IN (CAST(:a AS uuid), CAST(:b AS uuid))",
                      proj=target["id"], a=low, b=high) == 0

    await _race(w, route, check)
