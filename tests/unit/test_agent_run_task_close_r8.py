"""H-409 (Metorite task 748) — harden EA-14a: a task close ends its run, the
release rule, and the org arm of the holder reads.

Spec: ``project-docs/specs/external_agent_access.md`` §12.3 ("H-409") and
§12.7 rule 4. The owner decided the release rule in Q5 of
``agent_coordination.md`` §8 (2026-11-06, task 1874): ending another member's
run is a write, so ``admin:members:manage`` gives it and ``admin:members:read``
does not.

Three items.

1. **A task close ends its open run.** A PATCH into ``done`` or
   ``cancelled``, and an archive, end the task's open run in the SAME
   transaction: the move ``task_close`` to ``completed``, with one
   server-close line. The run of the closing agent itself stays open, because
   the close is its own disposition. A run that an agent write holds at that
   moment refuses the lock at once (``NOWAIT``), and the route answers 409
   with nothing written.
2. **The release rule.** A person releases another member's run with
   ``admin:members:manage``. The run panel reads the same slug.
3. **The org arm.** ``unheld``, ``holder``, the gate's ``_HOLDER_SQL``, every
   ``_RUN_SELECT`` read and the pill of ``held_by`` name the org, as defence
   in depth beside FORCE RLS. The R8 test reads them through the RLS-bypassing
   admin, because the app role cannot see a row of another org at all.

Run on a PRIVATE database (``scripts/dev_db.sh``, then a database of your
own on the scratch server)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant_<you> \\
        uv run pytest tests/unit/test_agent_run_task_close_r8.py -v -rs

Mutations. ``G`` is ``routes/projects/agent_runs.py``, ``F`` is
``filters.py``, ``C`` is ``core.py`` and ``T`` is ``tasks.py``, all beside
each other. ``P`` is ``workbench/control_plane/src/app/projects/``.

* C1. ``C`` ``apply_status_transition``: never call
  ``end_runs_of_closed_task``. ``test_c1_a_person_moves_the_task_to_done``,
  ``test_c3_a_person_cancels_the_task`` and ``test_c5_a_question_closes_too``.
* C2. ``G`` ``end_runs_of_closed_task``: drop the ``_own_agent_run`` arm.
  ``test_c2_the_closing_agent_keeps_its_own_run`` and
  ``test_the_own_agent_run_is_the_member_and_the_stamp``.
* C3. ``G`` ``_LOCK_OPEN_TASK_RUNS_SQL``: drop ``NOWAIT``.
  ``test_c7_a_run_that_a_write_holds_refuses_the_close`` (a wait, then the
  timeout) and ``test_the_lock_refuses_at_once_and_names_the_org``.
* C4. ``T`` ``archive_task``: pass ``end_runs=False``.
  ``test_c4_a_person_archives_the_task``.
* C5. ``G`` ``_MOVES["task_close"]``: ``in_progress`` only.
  ``test_c5_a_question_closes_too`` (409) and
  ``test_task_close_moves_each_open_status_to_completed``.
* C6. ``G`` ``end_runs_of_closed_task``: ignore the flag.
  ``test_the_flag_off_runs_no_statement``.
* C7. ``T`` ``patch_task``: no ``closer=user``.
  ``test_c1_a_person_moves_the_task_to_done``.
* R1. ``G`` ``RELEASE_ADMIN_PERMISSION``: ``admin:members:read`` again.
  ``test_r1_a_manager_may_not_release_and_an_admin_may`` and
  ``test_the_release_slug_is_the_write_slug_in_both_places``.
* R2. ``P`` ``AgentRunPanel.tsx``: ``isAdmin: access.is_admin`` again.
  ``test_the_release_slug_is_the_write_slug_in_both_places``.
* O1. ``F`` ``unheld_clause``: drop ``h.organization_id = t.organization_id``.
  ``test_o1_the_reads_ignore_a_run_of_another_org`` and
  ``test_the_holder_reads_carry_the_org_arm``.
* O2. ``G`` ``_HOLDER_SQL``: drop the ``BOUND_ORG_SQL`` arm. ``test_o1_...``.
* O3. ``G`` ``_RUN_SELECT``: drop the ``bound_org`` join. ``test_o1_...``.
* O4. ``F`` ``agent_state_sql``: drop the ``BOUND_ORG_SQL`` arm. ``test_o1_...``.
* O5. ``F`` ``holder_sql``: drop the org arm. ``test_o1_...``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import agent_runs, filters
from sqlalchemy import text

from tests.unit.test_agent_run_exclusive import (  # noqa: F401
    ALICE,
    BOB,
    CAROL,
    DAVE,
    EVE,
    VIA,
    VIA_CURSOR,
    _agent,
    _claim,
    _member_row,
    _open_runs,
    _person,
    world,
)
from tests.unit.test_agent_runs_r8 import _count, _key, _rows, _session
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_PANEL = _REPO / "workbench/control_plane/src/app/projects/components/AgentRunPanel.tsx"
_RUNS_TS = _REPO / "workbench/control_plane/src/app/projects/lib/agentRuns.ts"

#: A manager: sees the whole org and holds the READ slug only.
MANAGER = "h409-manager@exclusive.test"
#: The member of a run of org B. ``app_user.email`` is unique over all orgs,
#: and a run names its member by text, so no member row is needed.
STRANGER_B = "h409-stranger@other-org.test"


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRules:
    @pytest.mark.parametrize("status", ["created", "in_progress", "awaiting", "stopping"])
    def test_task_close_moves_each_open_status_to_completed(self, status: str) -> None:
        """C5. A person may close a task whose run waits on a question, or
        that no agent picked up yet. The run then ends, from any open status."""
        assert agent_runs.transition(status, "task_close") == "completed"

    @pytest.mark.parametrize("status", ["completed", "stopped", "failed", "abandoned", "released"])
    def test_a_closed_run_takes_no_task_close(self, status: str) -> None:
        with pytest.raises(HTTPException) as refused:
            agent_runs.transition(status, "task_close")
        assert refused.value.status_code == 409

    def test_the_lock_refuses_at_once_and_names_the_org(self) -> None:
        """C3. The close never waits on a run lock, so it cannot close a
        cycle with an agent write (run, then task row)."""
        sql = " ".join(agent_runs._LOCK_OPEN_TASK_RUNS_SQL.split())
        assert sql.endswith("FOR UPDATE NOWAIT")
        assert "task_id IS NOT NULL" in sql
        assert f"organization_id = {filters.BOUND_ORG_SQL}" in sql

    def test_the_own_agent_run_is_the_member_and_the_stamp(self) -> None:
        """C2. Only the closing agent's own run (member and stamp) stays."""
        org, conn = str(uuid.uuid4()), str(uuid.uuid4())
        run = SimpleNamespace(member_email=ALICE, client_via=VIA)
        assert agent_runs._own_agent_run(run, _agent(ALICE, org, conn, VIA)) is True
        assert agent_runs._own_agent_run(run, _agent(ALICE, org, conn, VIA_CURSOR)) is False
        assert agent_runs._own_agent_run(run, _agent(BOB, org, conn, VIA)) is False
        assert agent_runs._own_agent_run(run, _person(ALICE, org)) is False

    async def test_the_flag_off_runs_no_statement(self, monkeypatch) -> None:
        """C6. While ``agent_runs_enabled`` is off, the close reads nothing."""
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)

        class _NoDb:
            async def execute(self, *_a: Any, **_k: Any) -> Any:
                raise AssertionError("no statement may run while the flag is off")

        got = await agent_runs.end_runs_of_closed_task(
            _NoDb(), str(uuid.uuid4()), "done", _person(ALICE, str(uuid.uuid4())))
        assert got == []

    def test_the_release_slug_is_the_write_slug_in_both_places(self) -> None:
        """R1 and R2. The gateway and the run panel read ONE slug, and it is
        the write slug of Q5."""
        assert agent_runs.RELEASE_ADMIN_PERMISSION == "admin:members:manage"
        runs_ts = _RUNS_TS.read_text(encoding="utf-8")
        assert ('export const RELEASE_ADMIN_PERMISSION = "admin:members:manage";'
                in runs_ts)
        panel = _PANEL.read_text(encoding="utf-8")
        assert "isAdmin: hasCapability(access, RELEASE_ADMIN_PERMISSION)" in panel
        assert "access.is_admin" not in panel

    def test_the_holder_reads_carry_the_org_arm(self) -> None:
        """O1 to O5, the source half. The R8 test below is the behaviour."""
        assert "h.organization_id = t.organization_id" in filters.unheld_clause()
        assert "h.organization_id = t.organization_id" in filters.holder_sql()
        bound = filters.BOUND_ORG_SQL
        assert f"r.organization_id = {bound}" in agent_runs._HOLDER_SQL
        assert f"JOIN (SELECT {bound} AS id) bound_org" in agent_runs._RUN_SELECT
        assert f"r.organization_id = {bound}" in filters._AGENT_STATE_SQL
        assert "n.organization_id = r.organization_id" in filters._AGENT_STATE_SQL
        # The bound org comes from the seam, never from a bind of the request.
        assert bound == "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════

_counter = iter(range(200000, 300000))


@pytest.fixture
def w(world, monkeypatch):  # noqa: F811 — the imported fixture
    """The world of ``test_agent_run_exclusive``, with the runs flag on and
    no run or bell left from an earlier test."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    monkeypatch.delenv(filters.AGENT_RUN_LEASE_ENV, raising=False)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_notifications"))
    return world


def _closable(p: Any, org: str, owner: str) -> SimpleNamespace:
    """A project with To do, Doing, Done and Cancelled lanes, and one task in
    To do. A claim moves the task to Doing."""
    project, task = str(uuid.uuid4()), str(uuid.uuid4())
    lanes = {name: str(uuid.uuid4()) for name in ("todo", "doing", "done", "cancelled")}
    cats = {"todo": "todo", "doing": "in_progress", "done": "done", "cancelled": "cancelled"}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'h409', :e, true)"), {"id": project, "o": org, "e": owner})
        for pos, (name, sid) in enumerate(lanes.items()):
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": sid, "o": org, "p": project, "n": name.title(), "c": cats[name],
                 "pos": 10 * (pos + 1)})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, :n, :s, 'Close the loop', :e)"),
            {"id": task, "o": org, "p": project, "n": next(_counter), "s": lanes["todo"],
             "e": owner})
    return SimpleNamespace(id=task, **lanes)


def _run(ns: Any, run_id: str) -> Any:
    return _rows(ns.p, "SELECT status, ended_at FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                 i=run_id)[0]


def _close_lines(ns: Any, run_id: str) -> list[Any]:
    return _rows(ns.p, "SELECT content->>'body' AS body, content->>'server_close' AS why "
                      "FROM pm_agent_run_activities WHERE run_id = CAST(:i AS uuid) "
                      "AND content ? 'server_close' ORDER BY seq", i=run_id)


def _status_of(ns: Any, task: str) -> str:
    return str(_rows(ns.p, "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                     t=task)[0].status_id)


async def _move(call: Any, user: Any, task: str, status: str) -> Any:
    return await call(user, "PATCH", f"/projects/tasks/{task}", {"status_id": status})


@_DB_GATE
class TestATaskCloseEndsItsRun:

    async def test_c1_a_person_moves_the_task_to_done(self, w):
        """The run ends in the transaction of the move, once, with the line
        of the sweep. The agent learns at its next write, and may still leave
        one late summary."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            moved = await _move(call, w.eve_stranger, t.id, t.done)
            assert moved.status_code == 200, moved.text
            late = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities",
                              {"type": "thought", "body": "still here",
                               "idempotency_key": _key()})
            summary = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                                 {"summary": "Shipped it.", "idempotency_key": _key()})
        assert _status_of(w, t.id) == t.done
        row = _run(w, run)
        assert row.status == "completed" and row.ended_at is not None
        assert [(x.body, x.why) for x in _close_lines(w, run)] == [
            ("Closed: the task is done.", "done")]
        assert _open_runs(w, t.id) == []
        assert late.status_code == 409, late.text
        assert summary.status_code == 200, summary.text
        assert summary.json()["run"]["status"] == "completed"

    async def test_c2_the_closing_agent_keeps_its_own_run(self, w):
        """The holder's own done is its disposition. Its run stays open, and
        its own finish completes it with no server-close line."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            moved = await _move(call, w.alice, t.id, t.done)
            assert moved.status_code == 200, moved.text
            assert _run(w, run).status == "in_progress"
            fin = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                             {"summary": "Done.", "idempotency_key": _key()})
        assert fin.status_code == 200, fin.text
        assert _run(w, run).status == "completed"
        assert _close_lines(w, run) == []

    async def test_c2b_another_agent_that_closes_ends_the_run(self, w):
        """Only the run of the closing agent is its own. Bob's agent closes
        a task that Alice's agent holds, and Alice's run ends."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            moved = await _move(call, w.bob, t.id, t.done)
        assert moved.status_code == 200, moved.text
        assert _run(w, run).status == "completed"
        assert [x.why for x in _close_lines(w, run)] == ["done"]

    async def test_c3_a_person_cancels_the_task(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            moved = await _move(call, w.carol_admin, t.id, t.cancelled)
        assert moved.status_code == 200, moved.text
        assert [(x.body, x.why) for x in _close_lines(w, run)] == [
            ("Closed: the task is cancelled.", "cancelled")]
        assert _run(w, run).status == "completed"

    async def test_c3b_a_refused_close_ends_no_run(self, w):
        """One transaction. An agent may not cancel (EA-6f), and the 422
        comes after the move. So the run that the move ended is open again."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            doing = _status_of(w, t.id)
            refused = await _move(call, w.bob, t.id, t.cancelled)
        assert refused.status_code == 422, refused.text
        assert _status_of(w, t.id) == doing
        assert _run(w, run).status == "in_progress"
        assert _close_lines(w, run) == []

    async def test_c4_a_person_archives_the_task(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
            again = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
        assert shelved.status_code == 200, shelved.text
        assert again.status_code == 200, again.text
        assert [(x.body, x.why) for x in _close_lines(w, run)] == [
            ("Closed: the task is archived.", "archived")]
        assert _run(w, run).status == "completed"

    async def test_c5_a_question_closes_too(self, w):
        """A run that waits on a question ends from ``awaiting``."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            asked = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities", {
                "type": "elicitation", "body": "Which one?", "idempotency_key": _key(),
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]})
            assert asked.json()["run"]["status"] == "awaiting", asked.text
            moved = await _move(call, w.eve_stranger, t.id, t.done)
        assert moved.status_code == 200, moved.text
        assert _run(w, run).status == "completed"
        assert [x.why for x in _close_lines(w, run)] == ["done"]

    async def test_c6_the_run_ends_exactly_once(self, w):
        """Done, then cancelled (no crossing), then a reopen and done again,
        then an archive: one server-close line in all."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            for lane in (t.done, t.cancelled, t.doing, t.done):
                answer = await _move(call, w.eve_stranger, t.id, lane)
                assert answer.status_code == 200, answer.text
            shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
            assert shelved.status_code == 200, shelved.text
        assert len(_close_lines(w, run)) == 1
        assert _count(w.p, "SELECT count(*) FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                      t=t.id) == 1

    async def test_c7_a_run_that_a_write_holds_refuses_the_close(self, w):
        """An agent write holds the run row (EA-13c: run, then task row). The
        close does not wait for it: 409, and nothing is written."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            doing = _status_of(w, t.id)
            with w.p.admin_engine.connect() as c:
                held = c.begin()
                c.execute(text("SELECT id FROM pm_agent_runs WHERE id = CAST(:i AS uuid) "
                               "FOR UPDATE"), {"i": run})
                try:
                    refused = await asyncio.wait_for(
                        _move(call, w.eve_stranger, t.id, t.done), timeout=15)
                finally:
                    held.rollback()
            assert refused.status_code == 409, refused.text
            assert "Try again" in refused.text
            assert _status_of(w, t.id) == doing
            assert _run(w, run).status == "in_progress"
            # The same close goes through once the write is over.
            moved = await _move(call, w.eve_stranger, t.id, t.done)
        assert moved.status_code == 200, moved.text
        assert _run(w, run).status == "completed"

    async def test_c8_with_the_flag_off_no_run_ends(self, w, monkeypatch):
        from acb_common import get_settings

        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
            moved = await _move(call, w.eve_stranger, t.id, t.done)
        assert moved.status_code == 200, moved.text
        assert _run(w, run).status == "in_progress"


@_DB_GATE
class TestTheReleaseRule:

    async def test_r1_a_manager_may_not_release_and_an_admin_may(self, w):
        """Q5. A manager holds ``admin:members:read`` only: 403, and the run
        stays. An admin with ``admin:members:manage`` releases it."""
        from acb_auth import UserContext, UserRole, build_access

        with w.p.admin_engine.begin() as c:
            _member_row(c, w.org, MANAGER, "Mona Manager")
        manager = UserContext(
            email=MANAGER, role=UserRole.EMPLOYEE, organization_id=w.org,
            access=build_access(["feature:projects", "data:org:read", "admin:members:read"]))
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            refused = await call(manager, "POST", f"/projects/agent-runs/{run}/release", {})
            assert refused.status_code == 403, refused.text
            assert _run(w, run).status == "in_progress"
            released = await call(w.carol_admin, "POST",
                                  f"/projects/agent-runs/{run}/release", {})
        assert released.status_code == 200, released.text
        assert _run(w, run).status == "released"


#: A run in org B that names a task of org A: the cross-org row that FORCE RLS
#: hides from the app role. Only the RLS-bypassing admin can plant or read it.
_PLANT_SQL = (
    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
    " status, started_by, started_at, last_activity_at, last_agent_activity_at)"
    " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, :via, 'in_progress', 'agent',"
    " now(), now(), now()) RETURNING id"
)


def _reads(c: Any, task: str, run: str) -> dict[str, Any]:
    """Each holder read of item 3 for *task*, in the transaction of *c*."""
    unheld = c.execute(text(
        f"SELECT count(*) FROM pm_tasks t WHERE t.id = CAST(:t AS uuid) AND {filters.unheld_clause()}"),
        {"t": task}).scalar()
    holder = c.execute(text(
        f"SELECT {filters.holder_sql()} FROM pm_tasks t WHERE t.id = CAST(:t AS uuid)"),
        {"t": task}).scalar()
    gate = c.execute(text(agent_runs._HOLDER_SQL),
                     {"tid": task, "open": list(filters.AGENT_RUN_OPEN_STATUSES)}).fetchall()
    load = c.execute(text(agent_runs._LOAD_TASK_RUN_SQL), {"rid": run}).fetchall()
    pill = c.execute(text(filters._AGENT_STATE_SQL), {"ids": [task]}).fetchall()
    return {"unheld": int(unheld), "holder": holder, "gate": len(gate), "load": len(load),
            "pill": len(pill)}


@_DB_GATE
class TestTheOrgArm:

    async def test_o1_the_reads_ignore_a_run_of_another_org(self, w):
        """Defence in depth. With RLS out of the way (the admin), a run of
        org B on a task of org A holds nothing in org A. A run of org A on the
        same task holds it (the control). With no tenant bound, the reads of a
        run fail closed."""
        org_b = w.p.org_b
        t = _closable(w.p, w.org, ALICE)
        with w.p.admin_engine.begin() as c:
            foreign = str(c.execute(text(_PLANT_SQL), {
                "o": org_b, "t": t.id, "m": STRANGER_B, "via": VIA}).scalar())
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            got = _reads(c, t.id, foreign)
        assert got == {"unheld": 1, "holder": None, "gate": 0, "load": 0, "pill": 0}, got

        # The control: a run of the task's own org is held, so each read works.
        with w.p.admin_engine.begin() as c:
            own = str(c.execute(text(_PLANT_SQL), {
                "o": w.org, "t": t.id, "m": DAVE, "via": VIA}).scalar())
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org})
            got = _reads(c, t.id, own)
        assert got == {"unheld": 0, "holder": DAVE, "gate": 1, "load": 1, "pill": 1}, got

        # No tenant bound: the reads that name the bound org find nothing.
        with w.p.admin_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            got = _reads(c, t.id, own)
        assert (got["gate"], got["load"], got["pill"]) == (0, 0, 0), got

    async def test_o2_held_by_reads_no_run_of_another_org(self, w):
        """The route half, under RLS: a reader of org A sees no ``held_by``
        from org B's run, and the task reads unheld."""
        t = _closable(w.p, w.org, ALICE)
        with w.p.admin_engine.begin() as c:
            c.execute(text(_PLANT_SQL), {"o": w.p.org_b, "t": t.id, "m": STRANGER_B,
                                         "via": VIA})
        async with _session(w.p) as call:
            one = (await call(w.eve_stranger, "GET", f"/projects/tasks/{t.id}")).json()
            listing = (await call(w.eve_stranger, "GET",
                                  "/projects/tasks?unheld=true&page_size=100")).json()
            claim = await _claim(call, w.alice, t.id)
        assert one["held_by"] is None
        assert t.id in {r["id"] for r in listing["rows"]}
        assert claim.status_code == 200, claim.text

