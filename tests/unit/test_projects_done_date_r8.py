"""UNDO-1 round 2, fix B: Undo of a Reopen restores the exact done date (R8).

``PATCH /projects/tasks/{id}`` takes an optional ``completed_at``. The server
honours it ONLY when the same write moves the task INTO a closing category.
It refuses a date in the future or before ``created_at`` with a 422. These
tests run the real router on a real Postgres as the non-privileged role.

Run (a private ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/undo1_r8
    uv run pytest tests/unit/test_projects_done_date_r8.py -v -rs

**The mutation (R7).** Make ``apply_status_transition`` ignore ``completed_at``
(always ``now()``) and ``test_d1_reopen_then_undo_restores_the_done_date``
goes red.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

A_EMAIL = "undo1-a@done-date-r8.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    b.member(promoted, promoted.org_a, A_EMAIL)
    return ns


@pytest.fixture
def w(world):
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


def _stored(w: Any, tid: str) -> str | None:
    with w.p.admin_engine.begin() as c:
        got = c.execute(text(
            "SELECT completed_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": tid}).scalar()
    return got.isoformat() if got is not None else None


def _done_task(w: Any) -> tuple[str, str]:
    """A done task whose done date is three days old. Returns (id, that date)."""
    tid = b.task(w.p, w.p.org_a, A_EMAIL, w.root, category="done")
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET created_at = now() - interval '10 days', "
            "completed_at = now() - interval '3 days' "
            "WHERE id = CAST(:t AS uuid)"), {"t": tid})
    old = _stored(w, tid)
    assert old is not None
    return tid, old


async def _patch(w: Any, tid: str, body: dict[str, Any]) -> Any:
    async with b._session(w.p) as call:
        return await call(w.a, "PATCH", f"/projects/tasks/{tid}", body)


async def _reopen(w: Any, tid: str) -> None:
    got = await _patch(w, tid, {"status_id": w.root["lanes"]["todo"]})
    assert got.status_code == 200, got.text
    assert _stored(w, tid) is None


class TestTheDoneDate:
    async def test_d1_reopen_then_undo_restores_the_done_date(self, w):
        tid, old = _done_task(w)
        await _reopen(w, tid)
        got = await _patch(w, tid, {
            "status_id": w.root["lanes"]["done"], "completed_at": old})
        assert got.status_code == 200, got.text
        assert _stored(w, tid) == old

    async def test_d2_without_the_date_the_stamp_is_now(self, w):
        tid, old = _done_task(w)
        await _reopen(w, tid)
        got = await _patch(w, tid, {"status_id": w.root["lanes"]["done"]})
        assert got.status_code == 200, got.text
        stamp = _stored(w, tid)
        assert stamp is not None and stamp != old
        assert datetime.fromisoformat(stamp) > datetime.fromisoformat(old)

    async def test_d3_a_future_date_answers_422_and_writes_nothing(self, w):
        tid, _ = _done_task(w)
        await _reopen(w, tid)
        got = await _patch(w, tid, {
            "status_id": w.root["lanes"]["done"],
            "completed_at": "2999-01-01T00:00:00+00:00"})
        assert got.status_code == 422
        assert "future" in got.text
        assert _stored(w, tid) is None

    async def test_d4_a_date_before_the_task_existed_answers_422(self, w):
        tid, _ = _done_task(w)
        await _reopen(w, tid)
        got = await _patch(w, tid, {
            "status_id": w.root["lanes"]["done"],
            "completed_at": "2001-01-01T00:00:00+00:00"})
        assert got.status_code == 422
        assert "before the task was created" in got.text
        assert _stored(w, tid) is None

    async def test_d5_the_date_is_ignored_when_the_write_does_not_close_the_task(self, w):
        tid = b.task(w.p, w.p.org_a, A_EMAIL, w.root, category="todo")
        got = await _patch(w, tid, {
            "title": "Renamed", "completed_at": "2020-01-01T00:00:00+00:00"})
        assert got.status_code == 200, got.text
        assert _stored(w, tid) is None
