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
* C2. ``G`` ``_LOCK_OPEN_TASK_RUNS_SQL``: drop the own-run arm
  (``IS DISTINCT FROM``). ``test_c2_the_closing_agent_keeps_its_own_run``,
  ``test_c9_the_own_parallel_write_gives_the_closing_agent_no_409`` and
  ``test_the_lock_refuses_at_once_and_names_the_org``.
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

Fix round 1 (the Opus review, P1): a put-back of the task inside the undo
window reopens the run that the close ended. The window is 15 minutes, or the
lease when the lease is shorter (``agent_runs.task_close_undo_s``).

* U1. ``G`` ``_REOPEN_CANDIDATE_SQL``: drop the marker arm
  (``task_close'->>'from' = ANY(:open)``).
  ``test_u7_a_close_with_no_marker_never_reopens`` (a run with no marker
  becomes the candidate, and the move has no event).
* U2. ``G`` ``reopen_run_of_reopened_task``: no ``newer`` test.
  ``test_u4_a_newer_run_blocks_the_reopen``.
* U3. ``G`` ``reopen_run_of_reopened_task``: no holder test.
  ``test_u4b_an_open_run_blocks_the_reopen`` (the index then refuses, and the
  line names a race, not a holder).
* U4. ``G`` ``reopen_run_of_reopened_task``: no window test.
  ``test_u3_a_put_back_after_the_window_does_not_reopen``.
* U5. ``C`` ``_runs_follow_the_task``: no reopen call.
  ``test_u1_an_undo_of_done_reopens_the_run``.
* U6. ``T`` ``unarchive_task``: no ``_reopen_after_unarchive``.
  ``test_u2_an_unarchive_reopens_the_run``.
* U7. ``G`` ``reopen_run_of_reopened_task``: the clock for ``ts=seen``.
  ``test_u1_an_undo_of_done_reopens_the_run`` (the lease must be the one
  the run had).
* U8. ``G`` ``reopen_run_of_reopened_task``: do not spend the close line.
  ``test_u1_an_undo_of_done_reopens_the_run`` (the later finish reads
  ``server_closed``).
* U9. ``G`` ``reopen_run_of_reopened_task``: the refusal line without
  ``server_close``. ``test_u3_...`` (the late summary gets 409).
* U10. ``G`` ``reopen_run_of_reopened_task``: no wait restore.
  ``test_u6_the_wait_comes_back``.

Fix round 2 (the second Opus review):

* V1. ``G`` ``finish_agent_run``: no ``_spend_marker_on_finish``.
  ``test_u9_a_finish_with_no_summary_spends_the_marker``.
* V2. ``G`` ``reopen_run_of_reopened_task``: no ``mark_rows_unread``.
  ``test_u5_a_question_comes_back_with_the_run`` (the bell row stays read).
* V3. ``G`` ``close_closed_task_run``: no ``bells`` in the marker.
  ``test_u5_...``.
* V4. ``G`` ``reopen_run_of_reopened_task``: a put-back that undoes another
  close spends nothing. ``test_u10_a_put_back_of_another_close_spends_the_marker``.
* V5. ``T`` ``_reopen_after_unarchive``: return early in a closing lane.
  ``test_u11_an_unarchive_in_the_done_lane_brings_the_own_run_back``.
* V6. ``G`` ``reopen_run_of_reopened_task``: no wait for a task that stays
  archived. ``test_u12_a_status_put_back_of_an_archived_task_waits``.
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
        """C3 and C2. The close never waits on a run lock, so it cannot close
        a cycle with an agent write (run, then task row). It never reads the
        run of the closing agent (member and stamp)."""
        sql = " ".join(agent_runs._LOCK_OPEN_TASK_RUNS_SQL.split())
        assert sql.endswith("FOR NO KEY UPDATE NOWAIT")
        assert "task_id IS NOT NULL" in sql
        assert f"organization_id = {filters.BOUND_ORG_SQL}" in sql
        assert ("(member_email, client_via) IS DISTINCT FROM"
                " (CAST(:me AS text), CAST(:via AS text))") in sql

    def test_the_closer_binds_the_member_and_the_stamp(self) -> None:
        """C2. An agent binds its member and its stamp. A person binds a NULL
        stamp, so the row compare keeps each run of the task."""
        org, conn = str(uuid.uuid4()), str(uuid.uuid4())
        assert agent_runs._closer_binds(_agent(ALICE, org, conn, VIA)) == {
            "me": ALICE, "via": VIA}
        assert agent_runs._closer_binds(_person(ALICE, org)) == {"me": ALICE, "via": None}

    @pytest.mark.parametrize("status", ["created", "in_progress", "awaiting", "stopping"])
    def test_a_reopen_goes_back_to_the_status_that_the_close_ended(self, status: str) -> None:
        assert agent_runs.transition("completed", f"task_reopen:{status}") == status
        with pytest.raises(HTTPException):
            agent_runs.transition("released", f"task_reopen:{status}")

    def test_the_undo_window_is_fifteen_minutes_or_the_lease(self, monkeypatch) -> None:
        monkeypatch.delenv(filters.AGENT_RUN_LEASE_ENV, raising=False)
        assert agent_runs.task_close_undo_s() == 900
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "120")
        assert agent_runs.task_close_undo_s() == 120

    def test_the_candidate_matches_by_the_marker_and_locks_at_once(self) -> None:
        sql = " ".join(agent_runs._REOPEN_CANDIDATE_SQL.split())
        assert "a.content->'task_close'->>'from' = ANY(:open)" in sql
        assert "NOT x.ephemeral ORDER BY x.seq DESC LIMIT 1" in sql
        assert "task_id IS NOT NULL" in sql
        assert f"r.organization_id = {filters.BOUND_ORG_SQL}" in sql
        assert sql.endswith("FOR NO KEY UPDATE OF r NOWAIT")
        # The match is by the marker, never by the words of the line.
        assert "Closed:" not in sql and "body" not in sql

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
        """Done, then cancelled (no crossing), then an archive (no open run):
        one server-close line in all. Fix round 1: a put-back reopens the
        run (``TestAPutBackReopensTheRun``), so this test closes no more."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            for lane in (t.done, t.cancelled):
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

    async def test_c9_the_own_parallel_write_gives_the_closing_agent_no_409(self, w):
        """Fix round 1. A write of the closing agent holds its own run. The
        close never reads that run, so the agent's own done goes through."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, t.id)).json()["agent_run_id"]
            with w.p.admin_engine.connect() as c:
                held = c.begin()
                c.execute(text("SELECT id FROM pm_agent_runs WHERE id = CAST(:i AS uuid) "
                               "FOR UPDATE"), {"i": run})
                try:
                    moved = await asyncio.wait_for(
                        _move(call, w.alice, t.id, t.done), timeout=15)
                finally:
                    held.rollback()
        assert moved.status_code == 200, moved.text
        assert _status_of(w, t.id) == t.done
        assert _run(w, run).status == "in_progress"

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


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1: a put-back of the task reopens the run (R8)
# ═══════════════════════════════════════════════════════════════════════════

#: A run of the task, planted by the RLS-bypassing admin with a chosen status
#: and age: a run that another session started in between.
_PLANT_AT_SQL = (
    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
    " status, started_by, started_at, last_activity_at, last_agent_activity_at,"
    " created_at, ended_at)"
    " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, :via, :status, 'agent',"
    " now() - make_interval(secs => :age), now(), now(),"
    " now() - make_interval(secs => :age),"
    " CASE WHEN :status = 'completed' THEN now() END) RETURNING id"
)


def _bodies(ns: Any, run_id: str) -> list[str]:
    return [r.body for r in _rows(
        ns.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities"
              " WHERE run_id = CAST(:i AS uuid) AND NOT ephemeral ORDER BY seq", i=run_id)]


def _seen(ns: Any, run_id: str) -> Any:
    return _rows(ns.p, "SELECT last_activity_at, waiting_on, waiting_until FROM pm_agent_runs"
                       " WHERE id = CAST(:i AS uuid)", i=run_id)[0]


def _age_close(ns: Any, run_id: str, seconds: int) -> None:
    """Make the close line of *run_id* older by *seconds*."""
    with ns.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_run_activities SET created_at = now() - make_interval(secs => :s)"
            " WHERE run_id = CAST(:i AS uuid) AND content ? 'task_close'"),
            {"s": seconds, "i": run_id})


def _server_closed(ns: Any, run_id: str) -> bool:
    from gateway.routes.projects import analytics_agent_runs as ar

    with ns.p.admin_engine.connect() as c:
        return bool(c.execute(text(
            f"SELECT {ar.SERVER_CLOSED_SQL} FROM pm_agent_runs r WHERE r.id = CAST(:i AS uuid)"),
            {"i": run_id}).scalar())


async def _claimed(call: Any, ns: Any, task: Any) -> str:
    answer = await _claim(call, ns.alice, task.id)
    assert answer.status_code == 200, answer.text
    return str(answer.json()["agent_run_id"])


async def _beat(call: Any, ns: Any, run: str) -> Any:
    return await call(ns.alice, "POST", f"/projects/agent-runs/{run}/activities",
                      {"type": "thought", "body": "back at it", "idempotency_key": _key()})


@_DB_GATE
class TestAPutBackReopensTheRun:

    async def test_u1_an_undo_of_done_reopens_the_run(self, w):
        """Eve drags the task to Done, then clicks Undo (the inverse PATCH).
        The run comes back with the lease it had, the task is held again,
        and the agent's next write works. Its later finish is a success."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            before = _seen(w, run)
            closed = await _move(call, w.eve_stranger, t.id, t.done)
            assert closed.status_code == 200, closed.text
            assert _run(w, run).status == "completed"
            undo = await _move(call, w.eve_stranger, t.id, doing)
            assert undo.status_code == 200, undo.text
            assert _run(w, run).status == "in_progress"
            assert _run(w, run).ended_at is None
            assert _seen(w, run).last_activity_at == before.last_activity_at
            assert _bodies(w, run)[-2:] == ["Closed: the task is done.",
                                            agent_runs.REOPENED_BODY]
            # The marker is spent: no line of the run is a server close now.
            assert _close_lines(w, run) == []
            beat = await _beat(call, w, run)
            assert beat.status_code == 201, beat.text
            held = await _claim(call, w.bob, t.id)
            assert held.status_code == 409, held.text
            assert held.json()["detail"]["error"] == "task_held"
            fin = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                             {"summary": "Done.", "idempotency_key": _key()})
        assert fin.status_code == 200, fin.text
        assert _run(w, run).status == "completed"
        assert _server_closed(w, run) is False

    async def test_u2_an_unarchive_reopens_the_run(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
            assert shelved.status_code == 200, shelved.text
            assert _run(w, run).status == "completed"
            back = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/unarchive", None)
            assert back.status_code == 200, back.text
            assert _run(w, run).status == "in_progress"
            assert _bodies(w, run)[-2:] == ["Closed: the task is archived.",
                                            agent_runs.REOPENED_BODY]
            beat = await _beat(call, w, run)
        assert beat.status_code == 201, beat.text

    async def test_u3_a_put_back_after_the_window_does_not_reopen(self, w):
        """Eve closes. Later, past the undo window, Carol moves the task back
        for her own reason. The run stays closed, with one line that says
        why, and its agent may still leave one late summary."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            _age_close(w, run, agent_runs.task_close_undo_s() + 60)
            back = await _move(call, w.carol_admin, t.id, doing)
            assert back.status_code == 200, back.text
            assert _run(w, run).status == "completed"
            assert _bodies(w, run)[-1] == agent_runs.REOPEN_REFUSED_BODIES["window"]
            late = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                              {"summary": "Wrapped up.", "idempotency_key": _key()})
            assert late.status_code == 200, late.text
            for lane in (t.done, doing):
                assert (await _move(call, w.carol_admin, t.id, lane)).status_code == 200
        assert _run(w, run).status == "completed"
        refusals = [b for b in _bodies(w, run) if b.startswith("Not reopened")]
        assert refusals == [agent_runs.REOPEN_REFUSED_BODIES["window"]]

    async def test_u4_a_newer_run_blocks_the_reopen(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            with w.p.admin_engine.begin() as c:
                c.execute(text(_PLANT_AT_SQL), {"o": w.org, "t": t.id, "m": DAVE, "via": VIA,
                                                "status": "completed", "age": 0})
            undo = await _move(call, w.eve_stranger, t.id, doing)
        assert undo.status_code == 200, undo.text
        assert _run(w, run).status == "completed"
        assert _bodies(w, run)[-1] == agent_runs.REOPEN_REFUSED_BODIES["newer_run"]

    async def test_u4b_an_open_run_blocks_the_reopen(self, w):
        """Another run is open on the task (an older run that a prompt opened
        again, say). The put-back leaves the closed run closed and names the
        holder. Without the holder test the unique index refuses instead."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            with w.p.admin_engine.begin() as c:
                other = str(c.execute(text(_PLANT_AT_SQL), {
                    "o": w.org, "t": t.id, "m": DAVE, "via": VIA, "status": "in_progress",
                    "age": 86400}).scalar())
            undo = await _move(call, w.eve_stranger, t.id, doing)
        assert undo.status_code == 200, undo.text
        assert _run(w, run).status == "completed"
        assert _run(w, other).status == "in_progress"
        assert _bodies(w, run)[-1] == agent_runs.REOPEN_REFUSED_BODIES["held"]

    async def test_u5_a_question_comes_back_with_the_run(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            asked = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities", {
                "type": "elicitation", "body": "Which one?", "idempotency_key": _key(),
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]})
            assert asked.status_code == 201, asked.text
            question = asked.json()["activity"]["id"]
            bells = _question_bells(w, run)
            assert bells and all(b.read_at is None for b in bells), bells
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            assert all(b.read_at is not None for b in _question_bells(w, run))
            assert (await _move(call, w.eve_stranger, t.id, doing)).status_code == 200
            listed = await call(w.alice_person, "GET", f"/projects/tasks/{t.id}/agent-runs", None)
        assert _run(w, run).status == "awaiting"
        [row] = listed.json()["rows"]
        assert row["open_elicitation"] is not None
        assert row["open_elicitation"]["id"] == question
        # Fix round 2: the question waits again, so its bell rows that the
        # close marked read are unread again (My Day "Needs you" reads them).
        after = _question_bells(w, run)
        assert [b.id for b in after] == [b.id for b in bells]
        assert all(b.read_at is None for b in after), after

    async def test_u6_the_wait_comes_back(self, w):
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            waits = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities", {
                "type": "thought", "body": "Waiting on CI", "idempotency_key": _key(),
                "waiting": {"on": "ci", "until_minutes": 30}})
            assert waits.status_code == 201, waits.text
            before = _seen(w, run)
            assert before.waiting_on == "ci"
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            assert _seen(w, run).waiting_on is None
            assert (await _move(call, w.eve_stranger, t.id, doing)).status_code == 200
        after = _seen(w, run)
        assert _run(w, run).status == "in_progress"
        assert (after.waiting_on, after.waiting_until) == (before.waiting_on, before.waiting_until)

    @pytest.mark.parametrize("door", ["status", "archive"])
    async def test_u7_a_close_with_no_marker_never_reopens(self, w, door):
        """A close line with no marker (the sweep's close) is no candidate."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            if door == "status":
                assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            else:
                shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive",
                                     None)
                assert shelved.status_code == 200, shelved.text
            with w.p.admin_engine.begin() as c:
                c.execute(text("UPDATE pm_agent_run_activities SET content = content - 'task_close'"
                               " WHERE run_id = CAST(:i AS uuid)"), {"i": run})
            lines = len(_bodies(w, run))
            if door == "status":
                back = await _move(call, w.eve_stranger, t.id, doing)
            else:
                back = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/unarchive",
                                  None)
        assert back.status_code == 200, back.text
        assert _run(w, run).status == "completed"
        assert len(_bodies(w, run)) == lines

    async def test_u8_with_the_flag_off_no_run_reopens(self, w, monkeypatch):
        from acb_common import get_settings

        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
            back = await _move(call, w.eve_stranger, t.id, doing)
        assert back.status_code == 200, back.text
        assert _run(w, run).status == "completed"


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def _question_bells(ns: Any, run_id: str) -> list[Any]:
    """The ``agent_question`` bell rows of the questions of *run_id*."""
    return _rows(ns.p, "SELECT n.id, n.read_at FROM pm_notifications n"
                       " WHERE n.kind = 'agent_question' AND n.activity_id IN"
                       " (SELECT a.pm_activity_id FROM pm_agent_run_activities a"
                       "   WHERE a.run_id = CAST(:i AS uuid) AND a.type = 'elicitation')"
                       " ORDER BY n.id", i=run_id)


class TestTheSecondReview:
    def test_a_put_back_undoes_only_its_own_kind_of_close(self) -> None:
        sid = str(uuid.uuid4())
        assert agent_runs._undoes({"status_id": sid}, "done", sid) is True
        assert agent_runs._undoes({"status_id": sid}, "done", str(uuid.uuid4())) is False
        assert agent_runs._undoes({"status_id": None}, "archived", sid) is False
        assert agent_runs._undoes({"status_id": None}, "archived", None) is True
        assert agent_runs._undoes({"status_id": sid}, "done", None) is False

    def test_the_way_back_of_a_bell_is_narrow(self) -> None:
        import inspect

        from gateway.routes.projects import notifications

        src = " ".join(inspect.getsource(notifications.mark_rows_unread).split())
        assert "kind = 'agent_question'" in src and "read_at IS NOT NULL" in src
        assert "id = ANY(CAST(:ids AS uuid[]))" in src


@_DB_GATE
class TestTheSecondReviewR8:

    async def test_u9_a_finish_with_no_summary_spends_the_marker(self, w):
        """Eve closes. The agent finishes with no summary and goes away. A
        put-back inside the window must not reopen a run that its agent
        finished, or the task would read held for a whole lease."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            fin = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                             {"idempotency_key": _key()})
            assert fin.status_code == 200, fin.text
            again = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish",
                               {"idempotency_key": _key()})
            assert again.status_code == 200, again.text
            assert _bodies(w, run)[-1] == agent_runs.FINISHED_AFTER_CLOSE_BODY
            lines = len(_bodies(w, run))
            back = await _move(call, w.eve_stranger, t.id, doing)
            assert back.status_code == 200, back.text
            taken = await _claim(call, w.bob, t.id)
        assert _run(w, run).status == "completed"
        assert len(_bodies(w, run)) == lines
        assert taken.status_code == 200, taken.text
        assert _server_closed(w, run) is True

    async def test_u10_a_put_back_of_another_close_spends_the_marker(self, w):
        """Done, then cancelled (no crossing), then to do: that put-back
        leaves Cancelled, and the close was of Done, so it does not undo it.
        The task is open now, so the marker is spent with one line. A later
        Done and to do inside the window reopens nothing."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            for lane in (t.done, t.cancelled, t.todo):
                assert (await _move(call, w.eve_stranger, t.id, lane)).status_code == 200
            assert _run(w, run).status == "completed"
            assert _bodies(w, run)[-1] == agent_runs.REOPEN_REFUSED_BODIES["other_close"]
            for lane in (t.done, t.todo):
                assert (await _move(call, w.eve_stranger, t.id, lane)).status_code == 200
        assert _run(w, run).status == "completed"
        refusals = [b for b in _bodies(w, run) if b.startswith("Not reopened")]
        assert refusals == [agent_runs.REOPEN_REFUSED_BODIES["other_close"]]

    async def test_u11_an_unarchive_in_the_done_lane_brings_the_own_run_back(self, w):
        """Q1: the agent moves its own task to done and keeps its run. Eve
        archives (the run ends), then unarchives inside the window. The task
        stays in Done, and the run comes back as it was."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            assert (await _move(call, w.alice, t.id, t.done)).status_code == 200
            assert _run(w, run).status == "in_progress"
            shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
            assert shelved.status_code == 200, shelved.text
            assert _run(w, run).status == "completed"
            back = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/unarchive", None)
            assert back.status_code == 200, back.text
            beat = await _beat(call, w, run)
        assert _status_of(w, t.id) == t.done
        assert _run(w, run).status == "in_progress"
        assert _bodies(w, run)[-1] == agent_runs.REOPENED_BODY
        assert beat.status_code == 201, beat.text

    async def test_u12_a_status_put_back_of_an_archived_task_waits(self, w):
        """Eve closes (Done), then archives the closed task. A status
        put-back while the task stays archived leaves the task closed, so it
        reopens nothing and spends nothing. The unarchive then brings the
        task back open, and it does not undo a status close, so it spends the
        marker with one line."""
        t = _closable(w.p, w.org, ALICE)
        async with _session(w.p) as call:
            run = await _claimed(call, w, t)
            doing = _status_of(w, t.id)
            assert (await _move(call, w.eve_stranger, t.id, t.done)).status_code == 200
            shelved = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/archive", None)
            assert shelved.status_code == 200, shelved.text
            lines = len(_bodies(w, run))
            moved = await _move(call, w.eve_stranger, t.id, doing)
            assert moved.status_code == 200, moved.text
            assert _run(w, run).status == "completed"
            assert len(_bodies(w, run)) == lines
            back = await call(w.eve_stranger, "POST", f"/projects/tasks/{t.id}/unarchive", None)
            assert back.status_code == 200, back.text
        assert _run(w, run).status == "completed"
        assert _bodies(w, run)[-1] == agent_runs.REOPEN_REFUSED_BODIES["other_close"]
