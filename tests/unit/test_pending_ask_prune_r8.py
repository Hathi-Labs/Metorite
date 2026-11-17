"""Task 3435 — the retention of ``chat_pending_ask``, on a real Postgres (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 1 (the
retention of a row). Code: ``orchestrator.pending_ask.prune`` and
``prune_hook`` (the hook of ``gateway.jobs.worker.prune_hooks``), and the
purge clause ``chat_pending_asks`` (``gateway/routes/admin/members.py``). The
hermetic twin is ``tests/unit/test_pending_ask_prune.py``.

The prune and the purge run on a phase-4 promoted scratch database, as the
NOBYPASSRLS app role, so FORCE RLS binds each statement. Two tests run the
same SQL as the superuser with a bound tenant. The superuser bypasses RLS,
so only the org term of the SQL keeps the other org's rows safe there.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_pending_ask_prune_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (task 3435).**

a. An expired row loses its stored call, and keeps its payload, its state
   and its answer until the grace ends.
b. A row past the grace goes, in each state.
c. A row that has not expired never changes, in each state: an open row and
   a parked row keep their stored call.
d. The prune of org B never changes a row of org A, as the app role and as
   a session that bypasses RLS.
e. The purge deletes the rows that asked the leaver in the bound org only,
   and its count equals its delete.
f. One prune takes at most ``PENDING_ASK_PRUNE_BATCH`` rows of each kind,
   and the next prune takes the rest.
g. A second prune is a no-op.
h. The daily prune job of the worker runs the hook.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pending_ask = pytest.importorskip("orchestrator.pending_ask")

from acb_common.db import tenant_session  # noqa: E402
from gateway.jobs import worker  # noqa: E402
from gateway.routes.admin.members import _PURGE_DELETES  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tests.unit._tenant_ladder import tenant_engine_scope  # noqa: E402
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
LEAVER = f"leaver.{TAG}@pa-prune.test"
STAYER = f"stayer.{TAG}@pa-prune.test"
#: The stored call of a row. A sentinel, so a read finds it by value.
CALL = {"v": 1, "kind": "confirmation", "tool": "update_lead",
        "arguments": {"email": f"lead.{TAG}@x.test", "phone": "+91 90000 00000"}}
#: The ages of a row, as an interval from now to its ``expires_at``.
LIVE = "1 day"
EXPIRED = "-1 hour"
PAST_GRACE = f"-{pending_ask.PENDING_ASK_KEEP_DAYS + 1} days"


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org A and org B of the promoted catalog, with no row of the table
    before and after each test. The catalog is this suite's own."""
    p = promoted

    def clear() -> None:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM chat_pending_ask WHERE organization_id = ANY("
                           "CAST(:orgs AS uuid[]))"), {"orgs": [p.org_a, p.org_b]})

    clear()
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    clear()


def _add(world: Any, org: str, *, expires: str, state: str = "open",
         actor: str = STAYER, stored: dict[str, Any] | None = CALL) -> str:
    """One row, as the superuser. Returns its request id."""
    rid = uuid.uuid4().hex
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_pending_ask (organization_id, request_id, thread_id, "
            "  actor_email, kind, question, payload, state, answer, answered_at, "
            "  expires_at, stored_call) "
            "VALUES (CAST(:o AS uuid), :rid, :tid, :actor, 'confirmation', 'Move it?', "
            "  CAST(:payload AS jsonb), :state, :answer, "
            "  CASE WHEN :state IN ('answered', 'closed') THEN now() END, "
            "  now() + CAST(:exp AS interval), CAST(:stored AS jsonb))"),
            {"o": org, "rid": rid, "tid": f"t-{TAG}", "actor": actor,
             "payload": json.dumps({"request_id": rid, "title": "Move it?"}),
             "state": state, "answer": "APPROVE" if state == "answered" else None,
             "exp": expires,
             "stored": json.dumps(stored) if stored is not None else None})
    return rid


def _rows(world: Any) -> dict[str, tuple[str, str, bool, bool]]:
    """Each row of both orgs, as the superuser:
    ``rid -> (org, state, has a stored call, has its payload)``."""
    with world.p.admin_engine.connect() as c:
        return {
            str(r[0]): (str(r[1]), str(r[2]), r[3] is not None, r[4] == "Move it?")
            for r in c.execute(text(
                "SELECT request_id, organization_id, state, stored_call, "
                "  payload->>'title' FROM chat_pending_ask "
                "WHERE organization_id = ANY(CAST(:orgs AS uuid[]))"),
                {"orgs": [world.a, world.b]}).all()
        }


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _prune(world: Any, org: str) -> tuple[int, int]:
    async def go() -> tuple[int, int]:
        async with tenant_session(org) as db:
            return await pending_ask.prune(db)
    return _run(world, go)


def _hook(world: Any, org: str) -> None:
    async def go() -> None:
        async with tenant_session(org) as db:
            await pending_ask.prune_hook(db, datetime.now(UTC).date())
    _run(world, go)


def _as_superuser(world: Any, org: str, sql: str, params: dict[str, Any]) -> Any:
    """Run *sql* as the superuser with *org* bound. RLS does not bind it."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        return c.execute(text(sql), params).first()


# ── a, b, c. The policy ───────────────────────────────────────────────────


def test_r8_a_an_expired_row_loses_its_call_and_keeps_its_payload(world) -> None:
    rids = {state: _add(world, world.a, expires=EXPIRED, state=state)
            for state in ("open", "parked")}
    assert _prune(world, world.a) == (0, 2)
    rows = _rows(world)
    for state, rid in rids.items():
        assert rows[rid] == (world.a, state, False, True), state


def test_r8_b_a_row_past_the_grace_goes_in_each_state(world) -> None:
    gone = [_add(world, world.a, expires=PAST_GRACE, state=state,
                 stored=CALL if state in ("open", "parked") else None)
            for state in ("open", "parked", "answered", "closed")]
    kept = _add(world, world.a, expires=EXPIRED, stored=None)
    assert _prune(world, world.a) == (4, 0)
    rows = _rows(world)
    assert not set(gone) & set(rows)
    assert rows[kept] == (world.a, "open", False, True)


def test_r8_c_a_row_that_has_not_expired_never_changes(world) -> None:
    waiting = {state: _add(world, world.a, expires=LIVE, state=state)
               for state in ("open", "parked")}
    done = {state: _add(world, world.a, expires=LIVE, state=state, stored=None)
            for state in ("answered", "closed")}
    before = _rows(world)
    _hook(world, world.a)
    assert _rows(world) == before
    for state, rid in waiting.items():
        assert before[rid] == (world.a, state, True, True), state
    for state, rid in done.items():
        assert before[rid] == (world.a, state, False, True), state


# ── d. The org ────────────────────────────────────────────────────────────


def test_r8_d_the_prune_of_org_b_never_changes_a_row_of_org_a(world) -> None:
    a_stale = _add(world, world.a, expires=EXPIRED)
    a_old = _add(world, world.a, expires=PAST_GRACE)
    b_stale = _add(world, world.b, expires=EXPIRED)
    b_old = _add(world, world.b, expires=PAST_GRACE)
    _hook(world, world.b)
    rows = _rows(world)
    assert rows[a_stale] == (world.a, "open", True, True)
    assert rows[a_old] == (world.a, "open", True, True)
    assert rows[b_stale] == (world.b, "open", False, True)
    assert b_old not in rows


def test_r8_d_a_session_that_bypasses_rls_prunes_the_bound_org_only(world) -> None:
    """The org term of the SQL, with no RLS to back it (R5)."""
    a_stale = _add(world, world.a, expires=EXPIRED)
    a_old = _add(world, world.a, expires=PAST_GRACE)
    b_old = _add(world, world.b, expires=PAST_GRACE)
    got = _as_superuser(world, world.b, pending_ask._PRUNE_SQL, {
        "keep": pending_ask.PENDING_ASK_KEEP_DAYS,
        "batch": pending_ask.PENDING_ASK_PRUNE_BATCH})
    assert tuple(got) == (1, 0)
    rows = _rows(world)
    assert rows[a_stale] == (world.a, "open", True, True)
    assert rows[a_old] == (world.a, "open", True, True)
    assert b_old not in rows


# ── e. The purge ──────────────────────────────────────────────────────────


def test_r8_e_the_purge_deletes_the_leaver_s_rows_in_the_bound_org_only(world) -> None:
    [rows] = [r for r in _PURGE_DELETES if r.key == "chat_pending_asks"]
    mine = [_add(world, world.a, expires=LIVE, state=state, actor=LEAVER)
            for state in ("open", "parked")]
    other = _add(world, world.a, expires=LIVE, actor=STAYER)
    elsewhere = _add(world, world.b, expires=LIVE, actor=LEAVER)

    async def purge() -> tuple[int, int]:
        async with tenant_session(world.a) as db:
            count = (await db.execute(text(rows.count_sql), {"email": LEAVER})).scalar_one()
            deleted = (await db.execute(text(rows.delete_sql), {"email": LEAVER})).rowcount
        return int(count), int(deleted)

    assert _run(world, purge) == (2, 2)
    left = _rows(world)
    assert not set(mine) & set(left)
    assert set(left) == {other, elsewhere}


def test_r8_e_the_purge_takes_a_row_in_another_case_and_counts_it(world) -> None:
    """The clause lowers the column, so a row in capitals goes too, and the
    count equals the delete."""
    [rows] = [r for r in _PURGE_DELETES if r.key == "chat_pending_asks"]
    for _ in range(3):
        _add(world, world.a, expires=LIVE, actor=LEAVER.upper())

    async def purge() -> tuple[int, int]:
        async with tenant_session(world.a) as db:
            count = (await db.execute(text(rows.count_sql), {"email": LEAVER})).scalar_one()
            deleted = (await db.execute(text(rows.delete_sql), {"email": LEAVER})).rowcount
        return int(count), int(deleted)

    assert _run(world, purge) == (3, 3)


def test_r8_e_a_purge_that_bypasses_rls_takes_the_bound_org_only(world) -> None:
    """The org term of the purge clause, with no RLS to back it (R5)."""
    [rows] = [r for r in _PURGE_DELETES if r.key == "chat_pending_asks"]
    mine = _add(world, world.a, expires=LIVE, actor=LEAVER)
    elsewhere = _add(world, world.b, expires=LIVE, actor=LEAVER)
    with world.p.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.a})
        assert c.execute(text(rows.delete_sql), {"email": LEAVER}).rowcount == 1
    left = _rows(world)
    assert mine not in left
    assert elsewhere in left


# ── f, g. The bound, and a second run ─────────────────────────────────────


def test_r8_f_one_prune_takes_a_bounded_batch(world, monkeypatch) -> None:
    monkeypatch.setattr(pending_ask, "PENDING_ASK_PRUNE_BATCH", 2)
    old = [_add(world, world.a, expires=PAST_GRACE) for _ in range(3)]
    stale = [_add(world, world.a, expires=EXPIRED) for _ in range(3)]
    # Run 1 deletes 2 old rows. The third old row is not in that batch, so it
    # loses its call with the oldest stale row: 2 cleared. Run 2 deletes the
    # third old row and clears the 2 stale rows that are left. Run 3 is quiet.
    assert _prune(world, world.a) == (2, 2)
    assert _prune(world, world.a) == (1, 2)
    assert _prune(world, world.a) == (0, 0)
    rows = _rows(world)
    assert not set(old) & set(rows)
    assert all(rows[rid] == (world.a, "open", False, True) for rid in stale)


def test_r8_g_a_second_prune_is_a_no_op(world) -> None:
    _add(world, world.a, expires=PAST_GRACE)
    _add(world, world.a, expires=EXPIRED)
    live = _add(world, world.a, expires=LIVE)
    assert _prune(world, world.a) == (1, 1)
    after = _rows(world)
    assert _prune(world, world.a) == (0, 0)
    assert _rows(world) == after
    assert after[live] == (world.a, "open", True, True)


# ── h. The daily prune job ────────────────────────────────────────────────


def test_r8_h_the_daily_prune_job_runs_the_hook(world, monkeypatch) -> None:
    assert pending_ask.prune_hook in worker.prune_hooks
    monkeypatch.setattr(worker, "prune_hooks", [pending_ask.prune_hook])
    old = _add(world, world.a, expires=PAST_GRACE)
    stale = _add(world, world.a, expires=EXPIRED)

    async def go() -> None:
        async with tenant_session(world.a) as db:
            await worker.prune_job(db, {})
    _run(world, go)
    rows = _rows(world)
    assert old not in rows
    assert rows[stale] == (world.a, "open", False, True)
