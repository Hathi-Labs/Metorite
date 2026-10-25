"""WS-55 SW-10a — stranded agent runs: nudge once, then hand it to a person.

Spec: ``project-docs/specs/external_agent_access.md`` §12.6 (Metorite task
#341).

Two halves. The hermetic tests need no database: the flag, the SQL rules as
text, the migration as text, and the edge's ``my_work`` and run projection
through the fake gateway of ``test_mcp_edge_context.py``. The R8 tests run the
real sweep (``agent_run_sweep.run_org``) and the real ``/projects`` routes on
the phase-4-promoted catalog as the NON-privileged app role, so FORCE RLS
holds. Time moves by a backdate of the timestamps, as
``test_agent_run_exclusive.py`` moves it.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_sw10 \\
        uv run pytest tests/unit/test_agent_run_sweep_r8.py -v -rs

**The mutations (R7).** Each one turns the named test red. ``F`` is
``gateway/routes/projects/filters.py``, ``R`` is ``agent_runs.py``, ``S`` is
``agent_run_sweep.py`` and ``E`` is ``agent_edge/tools.py``.

* W1. ``F`` ``AGENT_RUN_STATE_SQL``: test ``answered`` before ``stale`` again.
  ``test_s1_an_answer_that_no_agent_reads_turns_stale`` and
  ``test_stale_comes_before_answered_in_the_state``.
* W2. ``R`` ``approve_task_archive``: no ``close_archived_run``.
  ``test_s2_an_approved_archive_closes_its_run``.
* W3. ``S`` ``run_org``: no step of the old archive shape.
  ``test_s2_the_sweep_closes_a_run_of_the_old_archive_shape``.
* W4. ``S`` ``_first_miss``: no ``notify``.
  ``test_s3_the_first_miss_writes_one_fingerprint_and_one_bell``.
* W5. ``R`` ``_NEEDS_AGENT_REASON_SQL``: no ``stranded`` arm.
  ``test_s3_the_first_miss_writes_one_fingerprint_and_one_bell``.
* W6. ``S`` ``_stranded_sql``: the join of the open strand never matches.
  ``test_s5_the_second_miss_opens_one_recovery_record_and_a_third_adds_nothing``.
* W7. The migration: the fingerprint index is not ``UNIQUE``.
  ``test_s4_two_processes_send_one_bell``.
* W8. ``S`` ``_SECOND_MISS_SQL``: no guard ``recovered_at IS NULL``.
  ``test_s5_two_processes_open_one_recovery_record``.
* W9. ``S`` ``_one_run``: no check of ``window_passed``.
  ``test_s4_a_second_tick_in_the_window_sends_no_second_bell``.
* W10. ``S`` ``_CLEAR_SQL``: clear nothing.
  ``test_s6_new_agent_activity_resets_the_fingerprint``.
* W11. ``F`` ``strand_fits_sql``: no ``seen_activity_at`` test.
  ``test_s6_new_agent_activity_resets_the_fingerprint``.
* W12. ``F`` ``stranded_clause``: no lease arm (a quiet run is stranded at
  five minutes). ``test_s7_a_live_lease_an_awaiting_run_and_a_queued_run_are_never_swept``.
* W13. ``F`` ``stranded_clause``: any open run quiet for five minutes, with
  no test of the status or the state.
  ``test_s7_a_live_lease_an_awaiting_run_and_a_queued_run_are_never_swept``.
* W14. ``S`` ``run_tick``: one ``tenant_session`` of the first org for every
  org. ``test_s8_each_pass_binds_its_own_org``.
* W15. ``S`` ``start_agent_run_sweep``: no check of ``sweep_enabled``.
  ``test_the_flag_off_starts_no_task``.
* W16. ``S`` ``run_tick``: no check of the flags.
  ``test_the_flag_off_tick_reads_nothing``.
* W17. ``E`` ``_waiting_reason``: read ``state`` only.
  ``test_an_answer_older_than_five_minutes_stays_run_answered``.

The fix round (2026-10-25):

* W18. ``S`` ``_stranded_sql``: no ``AND (s.id IS NULL OR s.recovered_at IS
  NULL)``. ``test_s9_recovered_runs_never_starve_a_new_strand``.
* W19. ``F`` ``stranded_clause``: no ``NOT task_closed_sql``.
  ``test_s10_a_quiet_run_of_a_closed_task_closes_and_gets_no_bell``.
* W20. ``S`` ``run_org``: no step of the closed tasks.
  ``test_s10_a_quiet_run_of_a_closed_task_closes_and_gets_no_bell``.
* W21. ``R`` ``_CLOSED_TASK_RUNS_SQL``: no test of the stale state.
  ``test_s10_a_closed_task_with_fresh_activity_is_left_alone``.
* W22. ``R`` ``finish_agent_run``: no late summary.
  ``test_s11_a_late_summary_after_a_server_close_is_kept``.
* W23. ``R`` ``_NEEDS_AGENT_REASON_SQL``: ``stranded`` before ``answered``.
  ``test_s12_an_answer_wins_over_a_strand``.
* W24. ``S`` ``_each``: no try around a step.
  ``test_s13_a_closer_that_fails_stops_nothing_else``.
* W25. ``S`` ``run_tick``: no try around an org.
  ``test_one_org_that_fails_leaves_the_others_swept``.
* W26. ``S`` ``_loop``: no try around a tick.
  ``test_a_tick_that_throws_does_not_end_the_loop``.
* W27. ``routes/admin/members.py``: no row of ``pm_agent_run_strands``.
  ``test_crm_answer_first_fix1.py::test_every_table_keyed_by_a_member_email_is_in_the_purge``.

The last round (2026-10-25):

* W28. ``R`` ``_closed_task_runs_sql``: the stale band instead of
  ``holder_free_sql``. ``test_s10_a_run_quiet_for_ten_minutes_on_a_done_task_keeps_it``.
* W29. ``R`` ``close_closed_task_run``: no ``server_close=why``.
  ``test_s11_a_late_summary_after_the_sweep_closed_the_run_is_kept``.
* W30. ``R`` ``_closed_task_runs_sql``: the lease measured on
  ``last_agent_activity_at``. ``test_s10_a_fresh_answer_on_a_closed_task_keeps_the_run``.

Two guards hold twice on purpose: the read of the open strand and the
unique index (W6, W7), and ``_one_run`` and the guarded ``UPDATE`` (W8). The
arbiter tests call one step twice, as two processes would, so each layer has
its own red test.
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from agent_edge import gateway_client, tools
from gateway import agent_run_sweep as sweep
from gateway.routes.projects import agent_runs, filters
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_agent_run_exclusive import (
    _agent,
    _connection,
    _member_row,
    _person,
    _seed_task,
)
from tests.unit.test_agent_runs_r8 import (
    CLIENT_ID,
    VIA,
    _count,
    _key,
    _rows,
    _session,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_edge_context import _Board, _run, _task_row, _work
from tests.unit.test_mcp_edge_writes import edge  # noqa: F401 — a fixture

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"
MAIN = _REPO / "apps" / "services" / "gateway" / "gateway" / "main.py"

ALICE = "sw10-alice@sweep.test"
BOB = "sw10-bob@sweep.test"
CAROL = "sw10-carol@sweep.test"
LEASE = filters.AGENT_RUN_LEASE_DEFAULT_S


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the flag
# ═══════════════════════════════════════════════════════════════════════════


class TestTheFlag:

    def test_the_flag_defaults_off(self) -> None:
        assert type(get_settings()).model_fields["agent_run_sweep_enabled"].default is False

    def test_the_flag_off_starts_no_task(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "agent_run_sweep_enabled", False)

        async def go() -> bool:
            started = await sweep.start_agent_run_sweep()
            assert not sweep.loop_running()
            return started

        assert asyncio.run(go()) is False

    @pytest.mark.parametrize(("sweep_on", "runs_on"), [(False, True), (True, False)])
    def test_the_flag_off_tick_reads_nothing(self, monkeypatch, sweep_on, runs_on) -> None:
        """With either flag off, a tick lists no org and opens no session."""
        monkeypatch.setattr(get_settings(), "agent_run_sweep_enabled", sweep_on)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", runs_on)

        async def no_read() -> list[str]:
            raise AssertionError("the tick read the organizations")

        def no_session(org: str) -> Any:
            raise AssertionError("the tick opened a session")

        monkeypatch.setattr(sweep, "_organization_ids", no_read)
        monkeypatch.setattr(sweep, "tenant_session", no_session)
        result = asyncio.run(sweep.run_tick())
        assert result == sweep.TickResult()

    def test_stop_is_safe_when_never_started(self) -> None:
        try:
            asyncio.run(sweep.stop_agent_run_sweep())
            assert not sweep.loop_running()
        finally:
            sweep._stopping.clear()  # a later tick in this process must run

    def test_the_start_function_reads_the_flag(self) -> None:
        tree = ast.parse(Path(sweep.__file__).read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "start_agent_run_sweep")
        called = {c.func.id for c in ast.walk(start)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "sweep_enabled" in called

    def test_main_starts_and_stops_it_and_never_reads_the_flag(self) -> None:
        src = MAIN.read_text(encoding="utf-8")
        assert "start_agent_run_sweep" in src and "stop_agent_run_sweep" in src
        assert "agent_run_sweep_enabled" not in src


class TestTheLoopSurvives:
    """Fix round, item 6. One org that fails does not stop the others, and
    a tick that throws does not end the loop."""

    def test_one_org_that_fails_leaves_the_others_swept(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "agent_run_sweep_enabled", True)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        sweep._stopping.clear()
        swept: list[str] = []

        async def orgs() -> list[str]:
            return ["org-a", "org-b", "org-c"]

        async def run_org(org: str, result: Any) -> None:
            if org == "org-b":
                raise RuntimeError("a bad org")
            swept.append(org)
            result.nudged += 1

        monkeypatch.setattr(sweep, "_organization_ids", orgs)
        monkeypatch.setattr(sweep, "run_org", run_org)
        result = asyncio.run(sweep.run_tick())
        assert swept == ["org-a", "org-c"]
        assert (result.orgs, result.nudged, result.failed) == (3, 2, 1)

    def test_a_tick_that_throws_does_not_end_the_loop(self, monkeypatch) -> None:
        calls: list[int] = []

        async def tick() -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("a bad tick")
            sweep._stopping.set()
            return sweep.TickResult()

        monkeypatch.setattr(sweep, "run_tick", tick)
        monkeypatch.setattr(sweep, "SWEEP_EVERY_SECS", 0.01)
        sweep._stopping.clear()
        try:
            asyncio.run(asyncio.wait_for(sweep._loop(), timeout=5))
        finally:
            sweep._stopping.clear()
        assert len(calls) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the rules as text, and the migration
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRules:

    @pytest.mark.parametrize("status", agent_runs.RUN_STATUSES)
    def test_the_archive_close_is_one_move(self, status: str) -> None:
        if status in ("in_progress", "stale"):
            assert agent_runs.transition(status, "archive_close") == "completed"
            return
        with pytest.raises(Exception) as refused:
            agent_runs.transition(status, "archive_close")
        assert getattr(refused.value, "status_code", None) == 409

    def test_stale_comes_before_answered_in_the_state(self) -> None:
        sql = filters.AGENT_RUN_STATE_SQL
        assert sql.index("THEN 'stale'") < sql.index("THEN 'answered'")
        assert filters.AGENT_RUN_ANSWER_WAITS_SQL in sql

    def test_the_stranded_clause_reads_the_lease_when_it_is_built(self, monkeypatch) -> None:
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "100")
        assert "THEN 300 ELSE 100 END) * interval '1 second'" in filters.stranded_clause()
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "200")
        assert "THEN 600 ELSE 200 END) * interval '1 second'" in filters.stranded_clause()

    def test_the_gate_the_strand_and_the_close_read_one_lease_text(self) -> None:
        """Last round, item 1. ``lease_held_sql`` is the one lease text: the
        ``unheld`` filter of the gate holds it, and ``holder_free_sql``, the
        SQL twin of ``holder_free``, holds it for the stranded rule and the
        close of a run of a closed task."""
        assert filters.lease_held_sql("h") in filters.unheld_clause()
        assert filters.lease_held_sql("r") in filters.holder_free_sql("r")
        assert filters.holder_free_sql("r") in filters.stranded_clause()
        assert filters.holder_free_sql("r") in agent_runs._closed_task_runs_sql()

    def test_the_strand_reasons_are_the_check_of_the_migration(self) -> None:
        sql = _migration().read_text(encoding="utf-8")
        assert "CHECK (reason IN ('agent_quiet', 'answer_unread'))" in sql
        assert filters.STRAND_REASONS == ("agent_quiet", "answer_unread")
        assert set(tools.STRANDED_NOTES) == set(filters.STRAND_REASONS)

    def test_the_migration_is_tenant_scoped_and_holds_the_fingerprint(self) -> None:
        sql = _migration().read_text(encoding="utf-8")
        assert "REFERENCES organization (id) ON DELETE CASCADE\n" in sql
        assert "ALTER TABLE pm_agent_run_strands FORCE  ROW LEVEL SECURITY" in sql
        assert ("CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_run_strands_fingerprint\n"
                "    ON pm_agent_run_strands (organization_id, run_id, reason)\n"
                "    WHERE cleared_at IS NULL") in sql
        assert "pm_agent_run_strands_open_recovery" in sql
        assert "DROP TABLE" not in sql and "RENAME" not in sql


def _migration() -> Path:
    """The migration that creates ``pm_agent_run_strands``, by content (R1)."""
    found = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
             if "CREATE TABLE IF NOT EXISTS pm_agent_run_strands"
             in p.read_text(encoding="utf-8")]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the edge
# ═══════════════════════════════════════════════════════════════════════════


class _ReasonBoard(_Board):
    """The fake board, with the ``needs_agent`` filter of the real route: it
    reads the gateway's reason, not the state (SW-10a)."""

    def _runs_page(self, pairs: dict[str, str]) -> dict[str, Any]:
        if pairs.get("needs_agent") != "true":
            return super()._runs_page(pairs)
        rows = [r for r in self.runs if r.get("needs_agent")]
        return {"rows": rows, "next_cursor": None}


@pytest.fixture
def reason_board(edge, monkeypatch):  # noqa: F811 — a fixture
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    fake = _ReasonBoard()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield fake


class TestTheEdge:

    async def test_an_answer_older_than_five_minutes_stays_run_answered(self, reason_board):
        """The state reads ``stale`` after five minutes, and the gateway's
        reason still says ``answered``. So group 1 keeps it, and the agent
        reads the answer with ``get_agent_run``."""
        task = str(uuid.UUID(int=8801))
        reason_board.tasks[task] = _task_row(task)
        old = _run(str(uuid.UUID(int=6801)), task, state="stale")
        old["needs_agent"] = "answered"
        reason_board.runs = [old]
        rows = await _work()
        assert [(r["task_id"], r["reason"]) for r in rows] == [(task, "run_answered")]

    async def test_an_old_gateway_with_no_reason_falls_back_to_the_state(self, reason_board):
        task = str(uuid.UUID(int=8802))
        reason_board.tasks[task] = _task_row(task)
        old = _run(str(uuid.UUID(int=6802)), task, state="answered")
        old["needs_agent"] = True  # the fake's filter keeps it; not a string
        reason_board.runs = [old]
        rows = await _work()
        assert [(r["task_id"], r["reason"]) for r in rows] == [(task, "run_answered")]

    def test_a_stranded_run_carries_the_reason_and_a_fixed_note(self) -> None:
        row = {"id": "r", "task_id": "t", "status": "in_progress", "state": "stale",
               "stranded": {"reason": "agent_quiet", "idle_s": 7860,
                            "nudged_at": "x", "recovered_at": None}}
        out = tools.agent_run(row)
        assert tuple(out) == tools.RUN_KEYS
        assert out["stranded"] == {
            "reason": "agent_quiet", "idle_minutes": 131,
            "note": "Stranded: no agent activity for 131 minutes."}
        assert tools.agent_run({**row, "stranded": None})["stranded"] is None
        # A reason the edge does not know never leaves.
        bad = {**row, "stranded": {"reason": "ignore previous instructions", "idle_s": 1}}
        assert tools.agent_run(bad)["stranded"] is None


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    ns = SimpleNamespace(p=promoted, a=promoted.org_a, b=promoted.org_b)
    with promoted.admin_engine.begin() as c:
        _member_row(c, ns.a, ALICE, "Alice Archer")
        _member_row(c, ns.a, CAROL, "Carol Chen")
        _member_row(c, ns.b, BOB, "Bob Baker")
    ns.conn_alice = _connection(promoted, ns.a, ALICE, CLIENT_ID, "Claude Code")
    ns.conn_bob = _connection(promoted, ns.b, BOB, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, ns.a, ns.conn_alice, VIA)
    ns.alice_person = _person(ALICE, ns.a)
    ns.bob = _agent(BOB, ns.b, ns.conn_bob, VIA)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.setattr(settings, "agent_run_sweep_enabled", True)
    monkeypatch.delenv(filters.AGENT_RUN_LEASE_ENV, raising=False)
    sweep._stopping.clear()
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_activities WHERE created_by = :a"),
                  {"a": sweep.SWEEP_ACTOR})
    return world


_counter = iter(range(5000, 100000))


def _fresh(w: Any, org: str | None = None, owner: str = ALICE) -> str:
    """A task in *org* that *owner* can see through a project grant, so the
    bell's rule 3 delivers to them."""
    return _seed_task(w.p, org or w.a, owner, n=next(_counter), grant=owner)


def _insert_run(w: Any, task: str, *, org: str | None = None, member: str = ALICE,
                status: str = "in_progress", idle_s: int = 3 * 3600,
                answer_waits: bool = False, connection: str | None = None,
                agent_idle_s: int | None = None) -> str:
    """A run, quiet for *idle_s* seconds. With *answer_waits* the newest
    activity is a member's answer that no agent read. *agent_idle_s* sets
    how long ago the agent last acted, when that is older than the answer."""
    rid = str(uuid.uuid4())
    agent_lag = 30 if answer_waits else 0
    if agent_idle_s is not None:
        agent_lag = agent_idle_s - idle_s
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (id, organization_id, task_id, member_email,"
            " connection_id, client_via, status, started_by, started_at,"
            " last_activity_at, last_agent_activity_at)"
            " VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:t AS uuid), :m,"
            " CAST(:cid AS uuid), :via, :s, :via, now() - make_interval(secs => :i + 60),"
            " now() - make_interval(secs => :i),"
            " now() - make_interval(secs => :i + :lag))"),
            {"id": rid, "o": org or w.a, "t": task, "m": member, "cid": connection,
             "via": VIA, "s": status, "i": idle_s, "lag": agent_lag})
    return rid


async def _sweep_org(w: Any, org: str | None = None) -> sweep.TickResult:
    result = sweep.TickResult()
    async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
        await sweep.run_org(org or w.a, result)
    return result


async def _stranded_rows(w: Any, org: str | None = None) -> list[Any]:
    """The rows that one tick reads in step 3, as one process reads them."""
    async with (tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)),
                sweep.tenant_session(org or w.a) as db):
        return list((await db.execute(text(sweep._stranded_sql()),
                                      {"n": 50, "window": LEASE})).fetchall())


async def _in_session(w: Any, step: Any, row: Any, org: str | None = None) -> Any:
    """One step of the sweep in its own committed transaction: one process."""
    async with (tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)),
                sweep.tenant_session(org or w.a) as db):
        return await step(db, row)


def _strands(w: Any, run: str) -> list[Any]:
    return _rows(w.p, "SELECT * FROM pm_agent_run_strands WHERE run_id = CAST(:r AS uuid)"
                      " ORDER BY nudged_at", r=run)


def _nudges(w: Any, task: str) -> list[Any]:
    return _rows(w.p, "SELECT recipient, kind, actor, excerpt FROM pm_notifications"
                      " WHERE task_id = CAST(:t AS uuid)", t=task)


def _run_row(w: Any, run: str) -> Any:
    [row] = _rows(w.p, "SELECT * FROM pm_agent_runs WHERE id = CAST(:r AS uuid)", r=run)
    return row


def _task_state(w: Any, task: str) -> tuple[Any, ...]:
    """What the sweep must never change on the task: the status, the
    archive, and the assignees."""
    [row] = _rows(w.p, "SELECT status_id, archived_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                  t=task)
    people = sorted(r.assignee for r in _rows(
        w.p, "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)", t=task))
    return (row.status_id, row.archived_at, tuple(people))


def _system_rows(w: Any, task: str) -> list[Any]:
    return _rows(w.p, "SELECT body, meta, created_by FROM pm_activities WHERE"
                      " task_id = CAST(:t AS uuid) AND type = 'system' AND created_by = :a",
                 t=task, a=sweep.SWEEP_ACTOR)


def _run_lines(w: Any, run: str) -> list[str]:
    return [r.body for r in _rows(
        w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities"
             " WHERE run_id = CAST(:r AS uuid) AND content->>'system' = 'true' ORDER BY seq",
        r=run)]


async def _needs(w: Any, user: Any) -> list[dict[str, Any]]:
    async with _session(w.p) as call:
        got = await call(user, "GET", "/projects/agent-runs?needs_agent=true")
    assert got.status_code == 200, got.text
    return got.json()["rows"]


def _close_task(w: Any, task: str, how: str) -> None:
    """Close *task* as a person would: archive it, or move it to a lane of
    the category *how* (``done`` or ``cancelled``)."""
    with w.p.admin_engine.begin() as c:
        if how == "archived":
            c.execute(text("UPDATE pm_tasks SET archived_at = now() WHERE id = CAST(:t AS uuid)"),
                      {"t": task})
            return
        project, org = c.execute(text(
            "SELECT project_id, organization_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task}).one()
        lane = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category,"
            " position) VALUES (CAST(:id AS uuid), :o, :p, :n, :c, 90)"),
            {"id": lane, "o": org, "p": project, "n": f"{how} {lane[:8]}", "c": how})
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) WHERE id = CAST(:t AS uuid)"),
                  {"s": lane, "t": task})


def _age_strand(w: Any, run: str, seconds: int) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_run_strands SET nudged_at = nudged_at"
                       " - make_interval(secs => :s) WHERE run_id = CAST(:r AS uuid)"),
                  {"s": seconds, "r": run})


def _age_run(w: Any, run: str, seconds: int) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_runs SET"
                       " last_activity_at = now() - make_interval(secs => :s),"
                       " last_agent_activity_at = now() - make_interval(secs => :s)"
                       " WHERE id = CAST(:r AS uuid)"), {"s": seconds, "r": run})


@_DB_GATE
class TestTheAnsweredAge:

    async def test_s1_an_answer_that_no_agent_reads_turns_stale(self, w):
        """Part 1, item 1. Through the real routes: the answer reads
        ``answered``, and six minutes later ``stale``. ``needs_agent`` keeps
        the run with the reason ``answered``, and the pill turns idle."""
        task = _fresh(w)
        async with _session(w.p) as call:
            started = await call(w.alice, "POST", f"/projects/tasks/{task}/agent-runs", {})
            rid = started.json()["run"]["id"]
            asked = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/activities",
                               {"type": "elicitation", "body": "Which?",
                                "idempotency_key": _key()})
            assert asked.status_code == 201, asked.text
            answered = await call(w.alice_person, "POST", f"/projects/agent-runs/{rid}/activities",
                                  {"type": "prompt", "body": "Postgres",
                                   "idempotency_key": _key()})
            assert answered.status_code == 201, answered.text
            fresh = await call(w.alice_person, "GET", f"/projects/agent-runs/{rid}")
            assert fresh.json()["run"]["state"] == "answered"
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_agent_runs SET last_activity_at = last_activity_at"
                    " - interval '6 minutes', last_agent_activity_at ="
                    " last_agent_activity_at - interval '6 minutes'"
                    " WHERE id = CAST(:r AS uuid)"), {"r": rid})
            old = await call(w.alice_person, "GET", f"/projects/agent-runs/{rid}")
            assert old.json()["run"]["state"] == "stale"
            assert old.json()["run"]["needs_agent"] == "answered"
            pill = await call(w.alice_person, "GET", f"/projects/tasks/{task}")
            assert pill.json()["agent_state"] == "stale"
        assert [(r["id"], r["needs_agent"]) for r in await _needs(w, w.alice)] == [
            (rid, "answered")]


@_DB_GATE
class TestTheArchiveClose:

    async def test_s2_an_approved_archive_closes_its_run(self, w):
        """Part 1, item 2. The approve archives the task and closes the run in
        one transaction, with one system line. A decline keeps it open."""
        for decision, want in (("approve", "completed"), ("decline", "in_progress")):
            task = _fresh(w)
            async with _session(w.p) as call:
                asked = await call(w.alice, "POST", f"/projects/tasks/{task}/archive-request",
                                   {"idempotency_key": _key()})
                assert asked.status_code == 201, asked.text
                rid = asked.json()["run"]["id"]
                answer = await call(w.alice_person, "POST",
                                    f"/projects/agent-runs/{rid}/archive-approval",
                                    {"decision": decision, "idempotency_key": _key()})
                assert answer.status_code == 200, answer.text
                assert answer.json()["run"]["status"] == want
                # The agent finds the run closed. A finish with no summary
                # returns the run, as the routine expects.
                if decision == "approve":
                    late = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/activities",
                                      {"type": "thought", "body": "still here",
                                       "idempotency_key": _key()})
                    assert late.status_code == 409, late.text
                    done = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish", {})
                    assert done.status_code == 200 and done.json()["run"]["status"] == want
            row = _run_row(w, rid)
            assert row.status == want
            lines = _run_lines(w, rid)
            if decision == "approve":
                assert lines == [agent_runs.ARCHIVE_CLOSED_BODY]
                assert row.ended_at is not None and row.session_key is None
            else:
                assert lines == []

    async def test_s2_the_sweep_closes_a_run_of_the_old_archive_shape(self, w):
        """A run approved before the deploy: in_progress, its newest prompt
        approved the archive, and the task is archived. The sweep closes it.
        The same shape on a task that is NOT archived stays open."""
        archived, kept = _fresh(w), _fresh(w)
        runs = {}
        for task in (archived, kept):
            rid = _insert_run(w, task, idle_s=600, answer_waits=True)
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id,"
                    " type, content, actor) VALUES (CAST(:o AS uuid), CAST(:r AS uuid),"
                    " CAST(:t AS uuid), 'prompt',"
                    " CAST('{\"selected\": [\"approve\"], \"archived\": true}' AS jsonb), :m)"),
                    {"o": w.a, "r": rid, "t": task, "m": ALICE})
            runs[task] = rid
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET archived_at = now() WHERE id = CAST(:t AS uuid)"),
                      {"t": archived})
        result = await _sweep_org(w)
        assert result.closed == 1
        assert _run_row(w, runs[archived]).status == "completed"
        assert _run_lines(w, runs[archived]) == [agent_runs.ARCHIVE_CLOSED_BODY]
        assert _run_row(w, runs[kept]).status == "in_progress"
        assert _nudges(w, archived) == []


@_DB_GATE
class TestTheMisses:

    async def test_s3_the_first_miss_writes_one_fingerprint_and_one_bell(self, w):
        task = _fresh(w)
        before = _task_state(w, task)
        rid = _insert_run(w, task)
        result = await _sweep_org(w)
        assert result.nudged == 1 and result.recovered == 0
        [strand] = _strands(w, rid)
        run = _run_row(w, rid)
        assert strand.reason == "agent_quiet" and strand.member_email == ALICE
        assert strand.seen_activity_at == run.last_activity_at
        assert strand.recovered_at is None and strand.cleared_at is None
        [bell] = _nudges(w, task)
        assert (bell.recipient, bell.kind, bell.actor) == (ALICE, "nudge", VIA)
        assert "Claude Code sent nothing on this task for 180 minutes" in bell.excerpt
        # needs_agent returns it to the member's next session, with the reason.
        [listed] = await _needs(w, w.alice)
        assert listed["id"] == rid and listed["needs_agent"] == "stranded"
        assert listed["stranded"]["reason"] == "agent_quiet"
        assert listed["stranded"]["recovered_at"] is None
        # Never: no move of the run, no change of the task, no timeline row.
        assert run.status == "in_progress" and run.ended_at is None
        assert _task_state(w, task) == before
        assert _system_rows(w, task) == [] and _run_lines(w, rid) == []

    async def test_s3_an_unread_answer_has_its_own_reason(self, w):
        task = _fresh(w)
        rid = _insert_run(w, task, answer_waits=True)
        await _sweep_org(w)
        [strand] = _strands(w, rid)
        assert strand.reason == "answer_unread"
        [bell] = _nudges(w, task)
        assert "did not read your answer for 180 minutes" in bell.excerpt

    async def test_s4_two_processes_send_one_bell(self, w):
        """The arbiter of the fingerprint is the partial unique index, not the
        read. Two processes that both read "no strand yet" both try the first
        miss: one insert wins, and the other sends no bell."""
        task = _fresh(w)
        rid = _insert_run(w, task)
        [row] = await _stranded_rows(w)
        assert row.strand_id is None
        sent = [await _in_session(w, sweep._first_miss, row) for _ in range(2)]
        assert sent == [True, False]
        assert len(_nudges(w, task)) == 1 and len(_strands(w, rid)) == 1

    async def test_s4_a_second_tick_in_the_window_sends_no_second_bell(self, w):
        task = _fresh(w)
        rid = _insert_run(w, task)
        await _sweep_org(w)
        second = await _sweep_org(w)
        # Within the window, even near its end.
        _age_strand(w, rid, LEASE - 120)
        third = await _sweep_org(w)
        assert second.nudged == third.nudged == 0
        assert second.recovered == third.recovered == 0
        assert len(_strands(w, rid)) == 1 and len(_nudges(w, task)) == 1
        assert _strands(w, rid)[0].recovered_at is None

    async def test_s5_the_second_miss_opens_one_recovery_record_and_a_third_adds_nothing(
            self, w):
        task = _fresh(w)
        before = _task_state(w, task)
        rid = _insert_run(w, task)
        await _sweep_org(w)
        _age_strand(w, rid, LEASE + 60)
        second = await _sweep_org(w)
        assert second.recovered == 1 and second.nudged == 0
        [strand] = _strands(w, rid)
        assert strand.recovered_at is not None and strand.cleared_at is None
        evidence = strand.evidence
        assert evidence["run_id"] == rid and evidence["task_id"] == task
        assert evidence["reason"] == "agent_quiet" and evidence["client_via"] == VIA
        assert evidence["idle_minutes"] == 180 and evidence["last_activity_at"]
        assert _run_lines(w, rid) == [
            "No agent activity for 180 minutes, after one nudge. "
            "A recovery record is open for Alice Archer."]
        [row] = _system_rows(w, task)
        assert row.meta["agent_run_id"] == rid
        assert row.meta["agent_run_strand_id"] == str(strand.id)
        assert "A recovery record is open for Alice Archer." in row.body
        assert len(_nudges(w, task)) == 1  # no second bell
        third = await _sweep_org(w)
        assert (third.nudged, third.recovered) == (0, 0)
        assert len(_strands(w, rid)) == 1 and len(_system_rows(w, task)) == 1
        assert len(_run_lines(w, rid)) == 1 and len(_nudges(w, task)) == 1
        # Never: the run stays open and the task as it was.
        assert _run_row(w, rid).status == "in_progress"
        assert _task_state(w, task) == before
        [listed] = await _needs(w, w.alice)
        assert listed["stranded"]["recovered_at"] is not None

    async def test_s5_two_processes_open_one_recovery_record(self, w):
        """The second miss is an UPDATE guarded on ``recovered_at IS NULL``.
        Two processes that both read the open fingerprint write ONE record
        and ONE pair of lines."""
        task = _fresh(w)
        rid = _insert_run(w, task)
        await _sweep_org(w)
        _age_strand(w, rid, LEASE + 60)
        [row] = await _stranded_rows(w)
        assert row.strand_id is not None and row.recovered_at is None and row.window_passed
        opened = [await _in_session(w, sweep._second_miss, row) for _ in range(2)]
        assert opened == [True, False]
        assert len(_run_lines(w, rid)) == 1 and len(_system_rows(w, task)) == 1

    async def test_s6_new_agent_activity_resets_the_fingerprint(self, w):
        task = _fresh(w)
        rid = _insert_run(w, task, connection=w.conn_alice)
        await _sweep_org(w)
        async with _session(w.p) as call:
            back = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/activities",
                              {"type": "thought", "body": "Back", "idempotency_key": _key()})
        assert back.status_code == 201, back.text
        # The derived rule stops at once, before any tick.
        assert await _needs(w, w.alice) == []
        cleared = await _sweep_org(w)
        assert cleared.cleared == 1 and cleared.nudged == 0
        [old] = _strands(w, rid)
        assert old.cleared_reason == "new_activity" and old.cleared_at is not None
        # A later strand starts at the first miss: a new fingerprint, one bell.
        _age_run(w, rid, 3 * 3600)
        again = await _sweep_org(w)
        assert again.nudged == 1
        rows = _strands(w, rid)
        assert len(rows) == 2 and rows[1].cleared_at is None
        assert len(_nudges(w, task)) == 2

    async def test_s6_a_member_reply_clears_it_and_a_release_clears_it(self, w):
        replied, released = _fresh(w), _fresh(w)
        r1 = _insert_run(w, replied)
        r2 = _insert_run(w, released)
        await _sweep_org(w)
        async with _session(w.p) as call:
            reply = await call(w.alice_person, "POST", f"/projects/agent-runs/{r1}/activities",
                               {"type": "prompt", "body": "Are you there?",
                                "idempotency_key": _key()})
            assert reply.status_code == 201, reply.text
            gone = await call(w.alice_person, "POST", f"/projects/agent-runs/{r2}/release", {})
            assert gone.status_code == 200, gone.text
        result = await _sweep_org(w)
        assert result.cleared == 2
        assert _strands(w, r1)[0].cleared_reason == "new_activity"
        assert _strands(w, r2)[0].cleared_reason == "run_moved"
        # The reply restarted the lease: a live path, so no new strand yet.
        assert result.nudged == 0

    async def test_s7_a_live_lease_an_awaiting_run_and_a_queued_run_are_never_swept(self, w):
        live = _insert_run(w, _fresh(w), idle_s=600)  # stale, the lease holds
        awaiting = _insert_run(w, _fresh(w), status="awaiting", idle_s=10 * 3600)
        queued = _insert_run(w, _fresh(w), status="created", idle_s=10 * 3600)
        fresh_answer = _insert_run(w, _fresh(w), idle_s=3600, answer_waits=True)
        # The one text of the rule reads none of them. The first-miss insert
        # checks `in_progress` again, so this read is its own fence.
        assert await _stranded_rows(w) == []
        result = await _sweep_org(w)
        assert (result.nudged, result.recovered) == (0, 0)
        for rid in (live, awaiting, queued, fresh_answer):
            assert _strands(w, rid) == []
        assert _count(w.p, "SELECT count(*) FROM pm_notifications") == 0

    async def test_s7_a_dead_connection_is_no_live_path(self, w):
        """The positive control of the live path: inside the lease, a run
        whose connection the member revoked has no way back."""
        dead = _connection(w.p, w.a, ALICE, CLIENT_ID, "Claude Code")
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now()"
                           " WHERE id = CAST(:i AS uuid)"), {"i": dead})
        rid = _insert_run(w, _fresh(w), idle_s=600, connection=dead)
        assert [str(r.id) for r in await _stranded_rows(w)] == [rid]
        result = await _sweep_org(w)
        assert result.nudged == 1
        assert _strands(w, rid)[0].reason == "agent_quiet"


@_DB_GATE
class TestTheFixRound:
    """The review and the verifier of 2026-10-25 (fix round, items 2 to 6)."""

    async def test_s9_recovered_runs_never_starve_a_new_strand(self, w, monkeypatch):
        """Item 2. A recovered run stays in_progress and stranded. With the
        window of one tick at 2, two recovered runs must not hide a new one."""
        monkeypatch.setattr(sweep, "MAX_PER_ORG", 2)
        old = [_insert_run(w, _fresh(w), idle_s=5 * 3600) for _ in range(2)]
        await _sweep_org(w)
        for rid in old:
            _age_strand(w, rid, LEASE + 60)
        recovered = await _sweep_org(w)
        assert recovered.recovered == 2
        new_task = _fresh(w)
        new = _insert_run(w, new_task, idle_s=3 * 3600)
        result = await _sweep_org(w)
        assert result.nudged == 1
        assert len(_strands(w, new)) == 1 and len(_nudges(w, new_task)) == 1

    @pytest.mark.parametrize("how", ["done", "cancelled", "archived"])
    async def test_s10_a_quiet_run_of_a_closed_task_closes_and_gets_no_bell(self, w, how):
        """Item 3. The sweep closes the run with one system line, never
        touches the task, and sends no bell, even past the lease."""
        task = _fresh(w)
        rid = _insert_run(w, task, idle_s=3 * 3600)
        _close_task(w, task, how)
        before = _task_state(w, task)
        assert await _stranded_rows(w) == []
        result = await _sweep_org(w)
        assert result.closed == 1 and result.nudged == 0
        row = _run_row(w, rid)
        assert row.status == "completed" and row.ended_at is not None
        assert _run_lines(w, rid) == [agent_runs.CLOSED_TASK_BODIES[how]]
        assert _task_state(w, task) == before
        assert _nudges(w, task) == [] and _strands(w, rid) == []

    async def test_s10_a_run_quiet_for_ten_minutes_on_a_done_task_keeps_it(self, w):
        """Last round, item 1. The bar is the LEASE, not the stale band. An
        agent that moved its own task to done and then waits in a long call
        keeps its run: ten quiet minutes close nothing, and no bell goes."""
        task = _fresh(w)
        rid = _insert_run(w, task, idle_s=600)
        _close_task(w, task, "done")
        result = await _sweep_org(w)
        assert (result.closed, result.nudged) == (0, 0)
        assert _run_row(w, rid).status == "in_progress"
        assert _run_lines(w, rid) == [] and _nudges(w, task) == []

    async def test_s10_a_fresh_answer_on_a_closed_task_keeps_the_run(self, w):
        """Last round, item 3. The agent last acted three hours ago, and a
        member answered 30 minutes ago. The answer renews the lease, so the
        run stays open on its done task."""
        task = _fresh(w)
        rid = _insert_run(w, task, idle_s=1800, answer_waits=True, agent_idle_s=3 * 3600)
        _close_task(w, task, "done")
        result = await _sweep_org(w)
        assert result.closed == 0
        assert _run_row(w, rid).status == "in_progress" and _run_lines(w, rid) == []

    async def test_s10_an_open_strand_of_a_closed_task_clears(self, w):
        """Item 3. A run nudged before its task closed: the next tick closes
        the run and clears the strand, and no recovery record opens."""
        task = _fresh(w)
        rid = _insert_run(w, task)
        await _sweep_org(w)
        _close_task(w, task, "cancelled")
        _age_strand(w, rid, LEASE + 60)
        result = await _sweep_org(w)
        assert result.closed == 1 and result.recovered == 0 and result.cleared == 1
        [strand] = _strands(w, rid)
        assert strand.cleared_reason == "run_moved" and strand.recovered_at is None
        assert len(_nudges(w, task)) == 1 and _system_rows(w, task) == []

    async def test_s11_a_late_summary_after_a_server_close_is_kept(self, w):
        """Item 4. The member approved the archive, so the run closed. The
        agent then finishes with a summary: 200, and the summary is kept as a
        response. A second summary is refused, as a finish on any completed
        run is."""
        task = _fresh(w)
        async with _session(w.p) as call:
            asked = await call(w.alice, "POST", f"/projects/tasks/{task}/archive-request",
                               {"idempotency_key": _key()})
            rid = asked.json()["run"]["id"]
            ok = await call(w.alice_person, "POST", f"/projects/agent-runs/{rid}/archive-approval",
                            {"decision": "approve", "idempotency_key": _key()})
            assert ok.json()["run"]["status"] == "completed"
            ended = _run_row(w, rid).ended_at
            done = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                              {"summary": "Archived it, nothing left.", "idempotency_key": _key()})
            assert done.status_code == 200, done.text
            assert done.json()["run"]["status"] == "completed"
            assert done.json()["activity"]["content"]["body"] == "Archived it, nothing left."
            again = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                               {"summary": "Again.", "idempotency_key": _key()})
            assert again.status_code == 409, again.text
        row = _run_row(w, rid)
        assert row.status == "completed" and row.ended_at == ended
        bodies = [r.body for r in _rows(
            w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities"
                 " WHERE run_id = CAST(:r AS uuid) AND type = 'response' ORDER BY seq", r=rid)]
        assert bodies == [agent_runs.ARCHIVE_CLOSED_BODY, "Archived it, nothing left."]

    async def test_s11_a_late_summary_after_the_sweep_closed_the_run_is_kept(self, w):
        """Last round, item 2. The SWEEP closed the run because its task is
        done. The agent then finishes with a summary: 200, the summary is a
        response, and a second summary gets 409."""
        task = _fresh(w)
        rid = _insert_run(w, task, connection=w.conn_alice)
        _close_task(w, task, "done")
        assert (await _sweep_org(w)).closed == 1
        async with _session(w.p) as call:
            done = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                              {"summary": "Shipped before the task closed.",
                               "idempotency_key": _key()})
            assert done.status_code == 200, done.text
            assert done.json()["run"]["status"] == "completed"
            again = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                               {"summary": "Again.", "idempotency_key": _key()})
            assert again.status_code == 409, again.text
        bodies = [r.body for r in _rows(
            w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities"
                 " WHERE run_id = CAST(:r AS uuid) AND type = 'response' ORDER BY seq", r=rid)]
        assert bodies == [agent_runs.CLOSED_TASK_BODIES["done"], "Shipped before the task closed."]

    async def test_s11_a_summary_on_a_run_the_agent_finished_is_still_refused(self, w):
        """Item 4, the other side. Only a SERVER close takes a late summary."""
        task = _fresh(w)
        async with _session(w.p) as call:
            started = await call(w.alice, "POST", f"/projects/tasks/{task}/agent-runs", {})
            rid = started.json()["run"]["id"]
            first = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                               {"summary": "Done.", "idempotency_key": _key()})
            assert first.status_code == 200, first.text
            again = await call(w.alice, "POST", f"/projects/agent-runs/{rid}/finish",
                               {"summary": "More.", "idempotency_key": _key()})
        assert again.status_code == 409, again.text

    async def test_s12_an_answer_wins_over_a_strand(self, w):
        """Item 5. A run whose answer waits past the lease has a strand of
        reason ``answer_unread``. Its ``needs_agent`` reason is ``answered``,
        so ``my_work`` offers it as ``run_answered``, not as a quiet run."""
        task = _fresh(w)
        rid = _insert_run(w, task, answer_waits=True)
        await _sweep_org(w)
        [listed] = await _needs(w, w.alice)
        assert listed["id"] == rid and listed["needs_agent"] == "answered"
        assert listed["stranded"]["reason"] == "answer_unread"

    async def test_s13_a_closer_that_fails_stops_nothing_else(self, w, monkeypatch):
        """Item 6. Each closed run has its own savepoint: one failure leaves
        the other closes, and the nudges of the org still go."""
        archived = [_fresh(w), _fresh(w)]
        runs = []
        for task in archived:
            rid = _insert_run(w, task, idle_s=600, answer_waits=True)
            with w.p.admin_engine.begin() as c:
                c.execute(text(
                    "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id,"
                    " type, content, actor) VALUES (CAST(:o AS uuid), CAST(:r AS uuid),"
                    " CAST(:t AS uuid), 'prompt',"
                    " CAST('{\"selected\": [\"approve\"], \"archived\": true}' AS jsonb), :m)"),
                    {"o": w.a, "r": rid, "t": task, "m": ALICE})
            runs.append(rid)
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET archived_at = now()"
                           " WHERE id = ANY(CAST(:t AS uuid[]))"), {"t": archived})
        quiet_task = _fresh(w)
        quiet = _insert_run(w, quiet_task)
        real = agent_runs.close_archived_run

        async def flaky(db: Any, run: Any) -> None:
            if str(run.id) == runs[0]:
                raise RuntimeError("a bad run")
            await real(db, run)

        monkeypatch.setattr(agent_runs, "close_archived_run", flaky)
        result = await _sweep_org(w)
        # The failed close rolled back alone, and the nudge of the org still
        # goes. Step 2 leaves that run open: its lease holds (ten minutes).
        assert result.failed == 1 and result.closed == 1 and result.nudged == 1
        assert _run_lines(w, runs[1]) == [agent_runs.ARCHIVE_CLOSED_BODY]
        assert _run_row(w, runs[0]).status == "in_progress"
        assert len(_nudges(w, quiet_task)) == 1 and len(_strands(w, quiet)) == 1


@_DB_GATE
class TestTheTenancy:

    async def test_s8_the_pass_of_org_a_leaves_org_b_untouched(self, w):
        task_a = _fresh(w, w.a, ALICE)
        task_b = _fresh(w, w.b, BOB)
        run_a = _insert_run(w, task_a, org=w.a, member=ALICE)
        run_b = _insert_run(w, task_b, org=w.b, member=BOB)
        result = await _sweep_org(w, w.a)
        assert result.nudged == 1
        assert len(_strands(w, run_a)) == 1
        assert _strands(w, run_b) == [] and _nudges(w, task_b) == []
        await _sweep_org(w, w.b)
        [strand_b] = _strands(w, run_b)
        assert str(strand_b.organization_id) == w.b
        assert str(_strands(w, run_a)[0].organization_id) == w.a
        [bell_b] = _nudges(w, task_b)
        assert bell_b.recipient == BOB

    async def test_s8_each_pass_binds_its_own_org(self, w, monkeypatch):
        """``run_tick`` opens one session for each org, bound to that org,
        and each strand lands in the org of its run."""
        task_a = _fresh(w, w.a, ALICE)
        task_b = _fresh(w, w.b, BOB)
        run_a = _insert_run(w, task_a, org=w.a, member=ALICE)
        run_b = _insert_run(w, task_b, org=w.b, member=BOB)
        real = sweep.tenant_session
        bound: list[str] = []

        def spy(org: str) -> Any:
            bound.append(org)
            return real(org)

        async def orgs() -> list[str]:
            return [w.a, w.b]

        monkeypatch.setattr(sweep, "tenant_session", spy)
        monkeypatch.setattr(sweep, "_organization_ids", orgs)
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            result = await sweep.run_tick()
        assert bound == [w.a, w.b]
        assert result.orgs == 2 and result.nudged == 2 and result.failed == 0
        assert str(_strands(w, run_a)[0].organization_id) == w.a
        assert str(_strands(w, run_b)[0].organization_id) == w.b
