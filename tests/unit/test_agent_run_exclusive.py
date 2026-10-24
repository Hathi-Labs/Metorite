"""WS-55 EA-14a — one AI for each task: the exclusive claim with a lease.

Spec: ``project-docs/specs/external_agent_access.md`` §12.3.
Owner directive, 2026-10-20: if a task has an open AI run, no other AI session
may claim it or start work on it.

Three halves.

**Hermetic.** The status table (``release`` and ``abandon``), the lease rule,
the ``unheld`` clause, the files of the migration, and the edge: the fixed text
of ``task_held``, ``release_task``, and ``held_by`` in the rows.

**R8** (a real Postgres, the real ``/projects`` router, the non-privileged
role of the phase-4 rehearsal). The gate, the lease, the release and the
migration run against SQL, because a fake agrees with any statement. Run them
on a PRIVATE database (``scripts/dev_db.sh``, never the shared :5432 one)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_agent_run_exclusive.py -v -rs

Mutations. Each one is a one-line edit of the gateway that must turn a named
test red. ``G`` is ``gateway/routes/projects/agent_runs.py``, ``F`` is
``filters.py`` beside it, and ``E`` is ``agent_edge/tools.py``.

* X1. ``G`` ``_gate_task``: return ``None`` when the holder is another member
  (skip the ``task_held`` raise). ``test_x1_two_members_claim_one_task``,
  ``test_x3_two_clients_of_one_member``, ``test_x4_a_member_start_is_refused``.
* X2. ``G`` ``_gate_task``: drop the own-run read at the top.
  ``test_x2_the_same_member_and_client_get_their_own_run_back``.
* X3. ``F`` ``lease_expired``: return ``True`` always, so every holder reads
  stale. ``test_x6_a_heartbeat_keeps_the_task``,
  ``test_x1_two_members_claim_one_task`` and the hermetic lease tests go red.
* X4. ``G`` ``_take_over``: no ``last_activity_at = :seen`` guard.
  ``test_x7_a_takeover_never_hits_a_run_that_just_moved``.
* X5. ``F`` ``agent_run_hold_s``: no factor for ``awaiting``.
  ``test_x8_a_run_that_waits_on_a_question_is_not_taken_early``.
* X6. ``G`` ``release_agent_run``: skip the ``allowed`` check.
  ``test_x10_a_stranger_may_not_release`` and
  ``test_x11_an_agent_of_another_client_may_not_release``.
* X7. ``G`` ``release_agent_run``: answer 404 for the stranger who sees the
  task. ``test_x10_...`` (403 is the contract, as ``finish`` has it).
* X8. ``G`` ``_clear_for_reopen``: delete the call in ``_write_activity``.
  ``test_x13_a_member_prompt_cannot_reopen_a_run_on_a_held_task`` (a 500 from
  the unique index, not a 409).
* X9. The migration: delete the duplicate ``UPDATE``.
  ``test_x15_the_migration_closes_duplicates_and_builds_the_index``.
* X10. The migration: delete ``CREATE UNIQUE INDEX``.
  ``test_x14_two_claims_at_once_give_one_run`` (both claims commit).
* X11. ``E`` ``_write``: drop the ``task_held`` branch.
  ``test_the_edge_maps_task_held_to_one_fixed_text``.
* X12. ``F`` ``unheld_clause``: drop the lease arm.
  ``test_x12_held_by_and_unheld_in_the_task_reads``.

SW-13c (one member, two machines, one stamp; spec §12.3):

* Z1. ``G`` ``_OPEN_RUN_SQL``: drop the ``connection_id`` arm.
  ``test_z1_a_live_run_of_another_connection_holds_the_task`` (R8) and
  ``test_the_gate_binds_the_agents_connection_in_the_own_run_read``.
* Z2. ``G`` ``_free``: ignore ``connection_gone``.
  ``test_z3_a_revoked_or_expired_holder_is_taken_over_at_once`` (R8),
  ``test_z5_the_reads_agree_with_the_gate_for_a_revoked_holder`` (R8) and
  ``test_a_holder_of_a_dead_connection_is_free_and_a_live_one_holds``.
* Z3. ``G`` ``start_agent_run``: bind the member's named connection as
  ``own_connection``. ``test_z4_a_person_start_keeps_the_old_rule`` (R8).
* Z4. ``F`` ``unheld_clause``: drop the ``NOT connection_gone`` arm.
  ``test_z5_the_reads_agree_with_the_gate_for_a_revoked_holder`` (R8) and
  ``test_one_text_of_a_dead_connection_for_the_gate_and_the_reads``.
* Z5. ``F`` ``held_by_of``: ``lease_expired`` for ``holder_free``.
  ``test_z5_...`` (R8) and ``test_held_by_reads_the_rule_of_the_gate``.
* Z6. ``F`` ``holder_free``: drop the ``connection_gone`` arm.
  ``test_the_holder_rule_frees_a_dead_connection``.
* Z7. ``E``: "from this client" in the ``start_agent_run`` text.
  ``test_the_tools_say_own_run_means_this_connection``.

SW-13c fix round 1:

* Z8. ``G`` ``_same_client``: return ``False``.
  ``test_an_own_run_of_another_connection_carries_its_id_and_the_hint`` and
  ``test_z6_a_new_sign_in_releases_its_own_old_run_and_claims`` (R8).
* Z9. ``E`` ``task_held_text``: ignore ``same_client``.
  ``test_the_edge_adds_the_hint_only_for_the_same_client``.
* Z10. ``G`` ``_OPEN_RUN_SQL``: drop the ``NOT connection_gone`` arm.
  ``test_a_dead_run_is_nobodys_own_and_the_org_arm_holds`` and
  ``test_z7_a_person_start_never_writes_into_a_dead_run`` (R8).
* Z11. ``F`` ``connection_gone_sql``: drop the ``organization_id`` arm.
  ``test_a_dead_run_is_nobodys_own_and_the_org_arm_holds``.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import agent_runs, filters
from sqlalchemy import text

from tests.unit.test_agent_runs_r8 import (  # noqa: F401
    CLIENT_ID,
    ORIGIN,
    VIA,
    _app,
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

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"

#: A second client of the same member. The stamp has the shape of EA-13a.
VIA_CURSOR = "mcp:cursor.com:cursor"
ALICE, BOB, CAROL = "ea14-alice@exclusive.test", "ea14-bob@exclusive.test", "ea14-carol@exclusive.test"
DAVE, EVE, MALLORY = "ea14-dave@exclusive.test", "ea14-eve@exclusive.test", "ea14-mallory@exclusive.test"


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic
# ═══════════════════════════════════════════════════════════════════════════


class TestTheStatusTable:
    @pytest.mark.parametrize("status", ["created", "in_progress", "awaiting", "stopping"])
    def test_an_open_run_can_be_released_or_abandoned(self, status: str) -> None:
        assert agent_runs.transition(status, "release") == "released"
        assert agent_runs.transition(status, "abandon") == "abandoned"

    @pytest.mark.parametrize("status", ["completed", "stopped", "failed", "abandoned", "released"])
    @pytest.mark.parametrize("event", ["release", "abandon", "agent_activity", "member_prompt"])
    def test_a_closed_run_takes_neither_move(self, status: str, event: str) -> None:
        if event == "member_prompt" and status == "completed":
            pytest.skip("a prompt reopens a completed run (the gate then checks the task)")
        with pytest.raises(HTTPException) as refused:
            agent_runs.transition(status, event)
        assert refused.value.status_code == 409

    def test_the_new_statuses_are_terminal_and_not_open(self) -> None:
        for status in ("abandoned", "released"):
            assert status in agent_runs.TERMINAL_STATUSES
            assert status not in agent_runs.OPEN_STATUSES
            assert status in agent_runs.RUN_STATUSES

    def test_an_agent_on_an_abandoned_run_is_told_to_stop(self) -> None:
        with pytest.raises(HTTPException) as refused:
            agent_runs.transition("abandoned", "agent_activity")
        assert "took this task over" in str(refused.value.detail)


class TestTheLeaseRule:
    def test_the_default_is_two_hours(self, monkeypatch) -> None:
        monkeypatch.delenv(filters.AGENT_RUN_LEASE_ENV, raising=False)
        assert filters.agent_run_lease_s() == 7200

    def test_the_environment_overrides_it_and_a_bad_value_reads_as_the_default(
            self, monkeypatch) -> None:
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "60")
        assert filters.agent_run_lease_s() == 60
        for bad in ("0", "-5", "soon", ""):
            monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, bad)
            assert filters.agent_run_lease_s() == 7200

    def test_a_run_is_stale_after_the_lease_and_a_question_after_three(self, monkeypatch) -> None:
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "100")
        assert not filters.lease_expired("in_progress", 100)
        assert filters.lease_expired("in_progress", 101)
        assert not filters.lease_expired("awaiting", 300)
        assert filters.lease_expired("awaiting", 301)

    def test_the_unheld_clause_reads_the_lease_when_it_is_built(self, monkeypatch) -> None:
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "100")
        sql = filters.unheld_clause()
        assert "interval '1 second'" in sql
        assert "THEN 300 ELSE 100 END" in sql
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "200")
        assert "THEN 600 ELSE 200 END" in filters.unheld_clause()


class TestOneConnectionOneOwner:
    """SW-13c, hermetic. The gate counts a run as the caller's own only when
    it carries the agent's connection, and a holder whose connection is
    revoked or expired is free. Spec §12.3."""

    def test_the_holder_rule_frees_a_dead_connection(self) -> None:
        """Z6."""
        assert filters.holder_free("in_progress", 10, True)
        assert filters.holder_free("awaiting", 10, True)
        assert not filters.holder_free("in_progress", 10, False)
        assert filters.holder_free("in_progress", 10**9, False)  # the lease still rules

    def test_one_text_of_a_dead_connection_for_the_gate_and_the_reads(self) -> None:
        """Z4. The gate, ``unheld`` and ``held_by`` read ONE fragment."""
        assert filters.connection_gone_sql("h") in filters.unheld_clause()
        assert "AND NOT " + filters.connection_gone_sql("h") in filters.unheld_clause()
        assert (filters.connection_gone_sql("r") + " AS connection_gone"
                in filters.agent_state_sql("true"))
        assert filters.connection_gone_sql("r") + " AS connection_gone" in agent_runs._HOLDER_SQL
        gone = filters.connection_gone_sql("x")
        assert "x.connection_id" in gone and "revoked_at IS NOT NULL" in gone
        assert "expires_at <= now()" in gone

    def test_held_by_reads_the_rule_of_the_gate(self) -> None:
        """Z5."""
        hit = SimpleNamespace(member_email=ALICE, member_name="Alice", client_via=VIA,
                              held_since=None, held_last=None, run_status="in_progress",
                              idle_s=5.0, connection_gone=True)
        assert filters.held_by_of(hit)["stale"] is True
        hit.connection_gone = False
        assert filters.held_by_of(hit)["stale"] is False

    def test_the_connection_of_a_caller_comes_from_the_agent_scope(self) -> None:
        conn = str(uuid.uuid4())
        assert agent_runs._agent_connection(_agent(ALICE, str(uuid.uuid4()), conn, VIA)) == conn
        assert agent_runs._agent_connection(_person(ALICE, str(uuid.uuid4()))) is None

    def test_the_own_run_read_has_one_connection_arm(self) -> None:
        assert "CAST(:cid AS uuid) IS NULL OR r.connection_id = CAST(:cid AS uuid)" in (
            agent_runs._OPEN_RUN_SQL)
        assert agent_runs._open_run_args("t", "m", VIA, None)["cid"] is None
        assert agent_runs._open_run_args("t", "m", VIA, "c")["cid"] == "c"

    async def test_the_gate_binds_the_agents_connection_in_the_own_run_read(self) -> None:
        """Z1, hermetic half. The fake answers the own-run read only when it
        carries the bound connection, as the SQL does. Machine B is not
        machine A, so it reads the holder and gets ``task_held``."""
        conn_a, conn_b = str(uuid.uuid4()), str(uuid.uuid4())
        run = _FakeRun(conn_a)
        db = _FakeGateDb(run)
        assert await agent_runs._gate_task(db, run.task_id, ALICE, via=VIA,
                                           connection=conn_a) is run
        with pytest.raises(HTTPException) as held:
            await agent_runs._gate_task(db, run.task_id, ALICE, via=VIA, connection=conn_b)
        assert held.value.status_code == 409
        assert held.value.detail["error"] == "task_held"
        # Fix round 1: machine B learns that the holder is its own client's run.
        assert held.value.detail["same_client"] is True
        assert held.value.detail["run_id"] == run.id
        # A person binds no connection and keeps the old rule.
        assert await agent_runs._gate_task(db, run.task_id, ALICE, via=VIA,
                                           connection=None) is run

    async def test_a_holder_of_a_dead_connection_is_free_and_a_live_one_holds(
            self, monkeypatch) -> None:
        """Z2, hermetic half."""
        taken: list[Any] = []

        async def take_over(db: Any, holder: Any, *, me: str, via: str | None) -> bool:
            taken.append(holder)
            return True

        monkeypatch.setattr(agent_runs, "_take_over", take_over)
        run = _FakeRun(str(uuid.uuid4()), gone=True)
        db = _FakeGateDb(run)
        assert await agent_runs._gate_task(
            db, run.task_id, ALICE, via=VIA, connection=str(uuid.uuid4())) is None
        assert taken == [run]
        run.connection_gone = False
        with pytest.raises(HTTPException):
            await agent_runs._gate_task(
                db, run.task_id, ALICE, via=VIA, connection=str(uuid.uuid4()))


class TestTheOwnRunOfAnotherConnection:
    """SW-13c fix round 1 (P2-a, P2-b, P3), hermetic."""

    def test_an_own_run_of_another_connection_carries_its_id_and_the_hint(self) -> None:
        """Z8. Only the caller's own member and stamp on ANOTHER connection
        gets ``same_client``, the run id and the hint."""
        conn_a, conn_b = str(uuid.uuid4()), str(uuid.uuid4())
        run = _FakeRun(conn_a)
        detail = agent_runs._task_held(run, me=ALICE, via=VIA, connection=conn_b).detail
        assert detail["same_client"] is True
        assert detail["run_id"] == run.id
        assert detail["hint"] == agent_runs.SAME_CLIENT_HINT
        assert agent_runs.SAME_CLIENT_HINT in detail["message"]
        assert "release_task" in agent_runs.SAME_CLIENT_HINT
        for me, via, connection in ((BOB, VIA, conn_b), (ALICE, VIA_CURSOR, conn_b),
                                    (ALICE, VIA, None), (ALICE, VIA, conn_a)):
            other = agent_runs._task_held(run, me=me, via=via, connection=connection).detail
            assert other["same_client"] is False, (me, via, connection)
            assert "run_id" not in other and "hint" not in other
        assert agent_runs._task_held(run).detail["same_client"] is False

    def test_the_edge_adds_the_hint_only_for_the_same_client(self) -> None:
        """Z9. The edge keeps its fixed text, and adds the hint with the run
        id only in the shape of an id."""
        run_id = str(uuid.uuid4())
        own = tools.task_held_text({"error": "task_held", "same_client": True,
                                    "run_id": run_id, "message": "SECRET"})
        assert own.startswith(tools.TEXT_TASK_HELD)
        assert f"call release_task with run_id {run_id}" in own
        assert "SECRET" not in own
        for detail in ({"error": "task_held"},
                       {"error": "task_held", "same_client": False, "run_id": run_id},
                       {"error": "task_held", "same_client": True, "run_id": "not an id"},
                       {"error": "task_held", "same_client": "yes", "run_id": run_id}):
            assert tools.task_held_text(detail) == tools.TEXT_TASK_HELD, detail

    def test_a_dead_run_is_nobodys_own_and_the_org_arm_holds(self) -> None:
        """Z10 and Z11."""
        assert "AND NOT " + filters.connection_gone_sql("r") in agent_runs._OPEN_RUN_SQL
        assert "gc.organization_id = x.organization_id" in filters.connection_gone_sql("x")


class _FakeRun(SimpleNamespace):
    """One open run of Alice under :data:`VIA`, fresh, on *connection*."""

    def __init__(self, connection: str, *, gone: bool = False) -> None:
        super().__init__(
            id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), member_email=ALICE,
            member_name="Alice Archer", client_via=VIA, connection_id=connection,
            status="in_progress", idle_s=5.0, connection_gone=gone,
            started_at=None, created_at=None, last_activity_at=None)


class _FakeGateDb:
    """Answers the two reads of ``_gate_task`` as the SQL would: the own-run
    read by member, stamp and (when bound) connection, the holder read with
    the one open run."""

    def __init__(self, run: _FakeRun) -> None:
        self.run = run

    async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        sql, run = str(statement), self.run
        if sql == agent_runs._OPEN_RUN_SQL:
            mine = (params["me"] == run.member_email and params["via"] == run.client_via
                    and params["cid"] in (None, run.connection_id))
            return SimpleNamespace(fetchone=lambda: run if mine else None)
        if sql == agent_runs._HOLDER_SQL:
            return SimpleNamespace(fetchone=lambda: run)
        raise AssertionError(sql)


class TestTheMigration:
    def _file(self) -> Path:
        found = [p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if "pm_agent_runs_one_open_per_task" in p.read_text(encoding="utf-8")
                 and "CREATE UNIQUE INDEX" in p.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        return found[0]

    def test_it_widens_the_check_closes_duplicates_and_then_builds_the_index(self) -> None:
        sql = self._file().read_text(encoding="utf-8")
        check = sql.index("ADD CONSTRAINT pm_agent_runs_status_check")
        update = sql.index("UPDATE pm_agent_runs")
        index = sql.index("CREATE UNIQUE INDEX IF NOT EXISTS pm_agent_runs_one_open_per_task")
        assert check < update < index
        assert "'abandoned'" in sql and "'released'" in sql
        # R6: the old per-member index stays.
        assert "DROP INDEX" not in sql

    def test_the_number_is_free_and_unique(self) -> None:
        number = self._file().name.split("_", 1)[0]
        same = [p.name for p in _MIGRATIONS.glob(f"{number}_*.sql")]
        assert len(same) == 1, same

    def test_the_check_lists_the_statuses_of_the_gateway(self) -> None:
        sql = self._file().read_text(encoding="utf-8")
        block = sql[sql.index("CHECK (status IN"):]
        block = block[: block.index(");")]
        listed = set(re.findall(r"'([a-z_]+)'", block))
        assert listed == set(agent_runs.RUN_STATUSES)


# ── The edge ────────────────────────────────────────────────────────────────

from agent_edge import tools  # noqa: E402

from tests.unit.test_mcp_edge_claim import (  # noqa: E402,F401
    CLAIM_PATH,
    _claim_args,
    edge,
    redis_fake,
)
from tests.unit.test_mcp_edge_writes import (  # noqa: E402
    _text,
    _tool,
)

RUN_ID = "11111111-2222-4333-8444-555555555555"
RELEASE_PATH = f"/projects/agent-runs/{RUN_ID}/release"

_HELD = {
    "member": "alice@example.test", "member_name": "Alice A", "client_via": VIA,
    "client_name": "Claude Code", "since": "2026-10-20T10:00:00+00:00",
    "last_activity_at": "2026-10-20T10:30:00+00:00", "stale": False,
}


class TestTheEdge:
    async def test_the_edge_maps_task_held_to_one_fixed_text(self, edge):  # noqa: F811
        """X11. The 409 of the claim and of the start gives ONE text. The
        message and the holder of the gateway never reach the agent."""
        body = {"detail": {"error": "task_held", "message": "SECRET Alice", "held_by": {}}}
        edge.gateway.answers[("POST", CLAIM_PATH)] = (409, body)
        result = await _tool("claim_task", _claim_args())
        assert result["isError"] is True
        assert _text(result) == tools.TEXT_TASK_HELD
        assert "SECRET" not in json.dumps(result)
        assert "pick another task" in tools.TEXT_TASK_HELD.lower()
        assert "do not claim or start this task again" in tools.TEXT_TASK_HELD.lower()

    async def test_a_same_client_409_gets_the_hint_through_the_edge(self, edge):  # noqa: F811
        """SW-13c fix round 1, Z9 through the real edge app."""
        run_id = str(uuid.uuid4())
        body = {"detail": {"error": "task_held", "message": "SECRET", "same_client": True,
                           "run_id": run_id, "held_by": {}}}
        edge.gateway.answers[("POST", CLAIM_PATH)] = (409, body)
        result = await _tool("claim_task", _claim_args())
        assert _text(result) == tools.TEXT_TASK_HELD + (
            tools.TEXT_TASK_HELD_SAME_CLIENT.format(run_id=run_id))
        assert "SECRET" not in json.dumps(result)

    async def test_a_409_that_is_not_task_held_keeps_its_own_text(self, edge):  # noqa: F811
        edge.gateway.answers[("POST", CLAIM_PATH)] = (409, {"detail": "something else"})
        result = await _tool("claim_task", _claim_args())
        assert _text(result) != tools.TEXT_TASK_HELD

    async def test_release_task_posts_the_run_and_reports_released(self, edge, monkeypatch):  # noqa: F811
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        run = {"id": RUN_ID, "task_id": str(uuid.uuid4()), "status": "released",
               "state": "released", "client_via": VIA, "member_email": "a@b.test"}
        edge.gateway.answers[("POST", RELEASE_PATH)] = (200, {"run": run, "released": True})
        result = await _tool("release_task", {"run_id": RUN_ID})
        assert result["isError"] is False, result
        assert result["structuredContent"]["released"] is True
        assert result["structuredContent"]["run"]["status"] == "released"

    async def test_release_task_has_a_text_for_a_run_of_another_client(self, edge, monkeypatch):  # noqa: F811
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        edge.gateway.answers[("POST", RELEASE_PATH)] = (
            403, {"detail": "You may not release this agent run."})
        result = await _tool("release_task", {"run_id": RUN_ID})
        assert _text(result) == tools.TEXT_RELEASE_FORBIDDEN

    async def test_release_task_is_hidden_while_the_runs_flag_is_off(self, edge, monkeypatch):  # noqa: F811
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        assert not tools.offered(tools.RELEASE_TASK)

    def test_the_task_rows_carry_held_by_in_the_shape_of_the_schema(self) -> None:
        held = tools._held_by({**_HELD, "client_via": VIA})
        assert held is not None
        assert held["client_name"] == "Claude Code" and held["stale"] is False
        assert held["member_name"] == {"untrusted_text": "Alice A"}
        assert tools._held_by(None) is None
        assert tools._held_by({**_HELD, "client_via": "not a stamp"})["client_via"] is None
        row = tools._list_task_row(
            {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()), "held_by": _HELD}, {})
        assert row["held_by"]["since"] == _HELD["since"]
        assert set(row) == set(tools.LIST_TASK_KEYS)
        free = tools._list_task_row({"id": str(uuid.uuid4()), "project_id": "p"}, {})
        assert free["held_by"] is None

    def test_the_tools_say_own_run_means_this_connection(self) -> None:
        """SW-13c, Z7. The own run is of this connection, not of this client."""
        for tool in (tools.START_AGENT_RUN, tools.ASK_ON_TASK):
            assert "from this connection" in tool.description, tool.name
            assert "from this client" not in tool.description, tool.name
        assert "another machine" in tools.START_AGENT_RUN.description
        assert "own open run from this connection" in tools.CLAIM_TASK.description

    def test_list_tasks_takes_unheld_and_sends_it_as_a_query_flag(self) -> None:
        assert "unheld" in tools._LIST_TASKS_INPUT["properties"]
        assert tools._LIST_TASKS_PARAMS["unheld"] == "unheld"

    def test_the_instructions_tell_the_agent_the_five_rules(self, monkeypatch) -> None:
        from acb_common import get_settings
        from agent_edge import mcp, prompts

        text_ = mcp._RUN_INSTRUCTIONS
        for phrase in ("held_by", "unheld", "claim_task first", "heartbeat", "release_task",
                       "Never work on a held task"):
            assert phrase in text_, phrase
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        steps = prompts._task_text("42", runs=True)
        assert "held_by.stale" in steps and "release_task" in steps
        assert "Do not work on a held task" in steps


# ═══════════════════════════════════════════════════════════════════════════
# R8
# ═══════════════════════════════════════════════════════════════════════════


def _agent(email: str, org: str, connection: str, via: str) -> Any:
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects", "data:org:read"]), organization_id=org,
        agent=AgentScope(
            connection_id=connection, scopes=("projects:read", "projects:write"),
            include_personal=False, client_origin=ORIGIN, actor_via=via),
    )


def _person(email: str, org: str, *, admin: bool = False, sees_all: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects"]
    if sees_all:
        perms.append("data:org:read")
    if admin:
        perms.append("admin:members:read")
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms), organization_id=org)


def _member_row(c: Any, org: str, email: str, name: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :n, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "n": name, "o": org})


def _seed_task(p: Any, org: str, owner: str, *, n: int, assignee: str | None = None,
               grant: str | None = None) -> str:
    """A project with a To do lane AND an In progress lane (a claim moves the
    task there), and one task."""
    project, todo, doing, task = (str(uuid.uuid4()) for _ in range(4))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'exclusive', :e, true)"), {"id": project, "o": org, "e": owner})
        if grant is not None:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": project, "s": grant, "e": owner})
        for sid, name, cat, pos in ((todo, "To do", "todo", 10), (doing, "Doing", "in_progress", 20)):
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
                "position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": sid, "o": org, "p": project, "n": name, "c": cat, "pos": pos})
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, :n, :s, 'Fix the extruder', :e)"),
            {"id": task, "o": org, "p": project, "n": n, "s": todo, "e": owner})
        if assignee is not None:
            _member_row(c, org, assignee, assignee)
            c.execute(text(
                "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
                "assigned_by) VALUES (:o, :t, :a, :e)"),
                {"o": org, "t": task, "a": assignee, "e": owner})
    return task


def _connection(p: Any, org: str, email: str, client_id: str, name: str) -> str:
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, :client, :name, :origin,"
            " ARRAY['projects:read', 'projects:write'], CAST('{}' AS uuid[]), false,"
            " now() + interval '1 day') RETURNING id"),
            {"o": org, "m": email, "client": client_id, "name": name, "origin": ORIGIN},
        ).scalar())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    org = promoted.org_a
    ns = SimpleNamespace(p=promoted, org=org)
    with promoted.admin_engine.begin() as c:
        for email, name in ((ALICE, "Alice Archer"), (BOB, "Bob Baker"), (CAROL, "Carol Chen"),
                            (DAVE, "Dave Doe"), (EVE, "Eve Evans"), (MALLORY, "Mallory M")):
            _member_row(c, org, email, name)
    ns.conn_alice = _connection(promoted, org, ALICE, CLIENT_ID, "Claude Code")
    ns.conn_alice_cursor = _connection(
        promoted, org, ALICE, "https://cursor.com/oauth/cursor-client-metadata", "Cursor")
    ns.conn_bob = _connection(promoted, org, BOB, CLIENT_ID, "Claude Code")
    ns.alice = _agent(ALICE, org, ns.conn_alice, VIA)
    ns.alice_cursor = _agent(ALICE, org, ns.conn_alice_cursor, VIA_CURSOR)
    ns.bob = _agent(BOB, org, ns.conn_bob, VIA)
    ns.alice_person = _person(ALICE, org)
    ns.carol_admin = _person(CAROL, org, admin=True)
    ns.dave_assignee = _person(DAVE, org)
    ns.eve_stranger = _person(EVE, org)
    ns.mallory_blind = _person(MALLORY, org, sees_all=False)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_notifications"))
    return world


_counter = iter(range(1000, 100000))


def _fresh(w: Any, **kw: Any) -> str:
    return _seed_task(w.p, w.org, ALICE, n=next(_counter), **kw)


async def _claim(call: Any, user: Any, task: str) -> httpx.Response:
    return await call(user, "POST", f"/projects/tasks/{task}/claim", None)


async def _start(call: Any, user: Any, task: str) -> httpx.Response:
    return await call(user, "POST", f"/projects/tasks/{task}/agent-runs", {})


def _open_runs(w: Any, task: str) -> list[Any]:
    return _rows(w.p, "SELECT id, member_email, client_via, status FROM pm_agent_runs "
                      "WHERE task_id = CAST(:t AS uuid) AND status IN "
                      "('created','in_progress','awaiting','stopping')", t=task)


def _age(w: Any, task: str, seconds: int) -> None:
    """Make the open run of *task* idle for *seconds* more seconds."""
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_agent_runs SET last_activity_at = now() - make_interval(secs => :s), "
            "last_agent_activity_at = now() - make_interval(secs => :s) "
            "WHERE task_id = CAST(:t AS uuid) AND status IN "
            "('created','in_progress','awaiting','stopping')"), {"s": seconds, "t": task})


def _detail(answer: httpx.Response) -> dict:
    detail = answer.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail


@_DB_GATE
class TestOneAiForEachTask:

    async def test_the_index_and_the_statuses_exist_in_the_ladder(self, w):
        assert _count(w.p, "SELECT count(*) FROM pg_indexes WHERE indexname IN "
                           "('pm_agent_runs_one_open_per_task', 'pm_agent_runs_one_open')") == 2

    # ── The gate ────────────────────────────────────────────────────────

    async def test_x1_two_members_claim_one_task(self, w):
        """Alice's AI claims. Bob's AI is refused with ``task_held``, the
        message names the holder and the client, and nothing of Bob's stays:
        no assignee row and no run."""
        task = _fresh(w)
        async with _session(w.p) as call:
            first = await _claim(call, w.alice, task)
            assert first.status_code == 200, first.text
            second = await _claim(call, w.bob, task)
        assert second.status_code == 409, second.text
        detail = _detail(second)
        assert detail["error"] == "task_held"
        assert "Alice Archer" in detail["message"] and "Claude Code" in detail["message"]
        assert "Pick another task" in detail["message"]
        assert detail["held_by"]["member"] == ALICE
        assert detail["held_by"]["client_via"] == VIA
        assert [r.member_email for r in _open_runs(w, task)] == [ALICE]
        assert _count(w.p, "SELECT count(*) FROM pm_task_assignees WHERE task_id = "
                           "CAST(:t AS uuid) AND assignee = :a", t=task, a=BOB) == 0

    async def test_x2_the_same_member_and_client_get_their_own_run_back(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            a = await _claim(call, w.alice, task)
            b = await _claim(call, w.alice, task)
            started = await _start(call, w.alice, task)
        assert a.status_code == b.status_code == 200
        assert a.json()["agent_run_id"] == b.json()["agent_run_id"]
        assert started.status_code == 200 and started.json()["created"] is False
        assert started.json()["run"]["id"] == a.json()["agent_run_id"]
        assert len(_open_runs(w, task)) == 1

    async def test_x3_two_clients_of_one_member(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            assert (await _claim(call, w.alice, task)).status_code == 200
            claim = await _claim(call, w.alice_cursor, task)
            start = await _start(call, w.alice_cursor, task)
        for answer in (claim, start):
            assert answer.status_code == 409, answer.text
            assert _detail(answer)["error"] == "task_held"
        assert [r.client_via for r in _open_runs(w, task)] == [VIA]

    async def test_x4_a_member_start_is_refused(self, w):
        """A member's own start (``created``, EA-12d) goes through the gate."""
        task = _fresh(w)
        async with _session(w.p) as call:
            assert (await _claim(call, w.alice, task)).status_code == 200
            answer = await call(w.carol_admin, "POST", f"/projects/tasks/{task}/agent-runs",
                                {"connection_id": w.conn_bob, "body": "Please take it"})
        # Carol names Bob's connection: it is not hers, so the start is 422 and
        # nothing opens. The gate test of the member path follows with Bob.
        assert answer.status_code == 422
        bob_person = _person(BOB, w.org)
        async with _session(w.p) as call:
            answer = await call(bob_person, "POST", f"/projects/tasks/{task}/agent-runs",
                                {"connection_id": w.conn_bob, "body": "Please take it"})
        assert answer.status_code == 409, answer.text
        assert _detail(answer)["error"] == "task_held"
        assert len(_open_runs(w, task)) == 1

    # ── The lease ───────────────────────────────────────────────────────

    async def test_x5_a_stale_holder_loses_the_task(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            old = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            _age(w, task, 3 * 3600)
            taken = await _claim(call, w.bob, task)
            assert taken.status_code == 200, taken.text
            new = taken.json()["agent_run_id"]
            # The old agent, still alive, learns that it lost the task.
            late = await call(w.alice, "POST", f"/projects/agent-runs/{old}/activities",
                              {"type": "thought", "body": "still here", "idempotency_key": _key()})
        assert new != old
        assert late.status_code == 409 and "took this task over" in late.text
        [old_row] = _rows(w.p, "SELECT status, ended_at FROM pm_agent_runs WHERE id = CAST(:i AS uuid)", i=old)
        assert old_row.status == "abandoned" and old_row.ended_at is not None
        [line] = _rows(w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities "
                            "WHERE run_id = CAST(:i AS uuid)", i=old)
        assert line.body == "Taken over by Bob Baker/Claude Code"
        assert [str(r.id) for r in _open_runs(w, task)] == [new]

    async def test_x6_a_heartbeat_keeps_the_task(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            _age(w, task, 3 * 3600)
            beat = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities",
                              {"type": "thought", "body": "alive", "idempotency_key": _key()})
            assert beat.status_code == 201, beat.text
            refused = await _claim(call, w.bob, task)
        assert refused.status_code == 409 and _detail(refused)["error"] == "task_held"

    async def test_x7_a_takeover_never_hits_a_run_that_just_moved(self, w):
        """The guard on ``last_activity_at``. A takeover that read the run as
        stale, and then lost the race to a heartbeat, must miss."""
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
        _age(w, task, 3 * 3600)
        from acb_common.db import bind_tenant, clear_tenant, release_tenant
        from gateway.routes.projects.core import _tenant_session

        from tests.unit._tenant_ladder import tenant_engine_scope

        scope = clear_tenant()
        try:
            async with tenant_engine_scope(
                    w.p.app_url.render_as_string(hide_password=False)):
                bind_tenant(w.org)
                async with _tenant_session() as db:
                    holder = (await db.execute(
                        text(agent_runs._HOLDER_SQL),
                        {"tid": task, "open": list(filters.AGENT_RUN_OPEN_STATUSES)},
                    )).fetchone()
                    assert filters.lease_expired(holder.status, float(holder.idle_s))
                    # A heartbeat lands between the read and the takeover.
                    with w.p.admin_engine.begin() as c:
                        c.execute(text("UPDATE pm_agent_runs SET last_activity_at = now() "
                                       "WHERE id = CAST(:i AS uuid)"), {"i": run})
                    took = await agent_runs._take_over(db, holder, me=BOB, via=VIA)
        finally:
            release_tenant(scope)
        assert took is False
        assert [str(r.id) for r in _open_runs(w, task)] == [run]

    async def test_x8_a_run_that_waits_on_a_question_is_not_taken_early(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            asked = await call(w.alice, "POST", f"/projects/agent-runs/{run}/activities", {
                "type": "elicitation", "body": "Which one?", "idempotency_key": _key(),
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]})
            assert asked.status_code == 201 and asked.json()["run"]["status"] == "awaiting"
            _age(w, task, 3 * 3600)  # past one lease, inside three
            held = await _claim(call, w.bob, task)
            assert held.status_code == 409 and _detail(held)["error"] == "task_held"
            _age(w, task, 7 * 3600)  # past three leases
            taken = await _claim(call, w.bob, task)
        assert taken.status_code == 200, taken.text
        [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)", i=run)
        assert row.status == "abandoned"

    async def test_x9_the_lease_comes_from_the_environment(self, w, monkeypatch):
        monkeypatch.setenv(filters.AGENT_RUN_LEASE_ENV, "60")
        task = _fresh(w)
        async with _session(w.p) as call:
            await _claim(call, w.alice, task)
            _age(w, task, 120)
            taken = await _claim(call, w.bob, task)
        assert taken.status_code == 200, taken.text

    # ── The release ─────────────────────────────────────────────────────

    async def test_x9b_the_holder_releases_and_the_task_is_free(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            before = _rows(w.p, "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=task)[0]
            released = await call(w.alice, "POST", f"/projects/agent-runs/{run}/release", {})
            assert released.status_code == 200, released.text
            assert released.json()["run"]["status"] == "released"
            again = await call(w.alice, "POST", f"/projects/agent-runs/{run}/release", {})
            assert again.status_code == 200
            taken = await _claim(call, w.bob, task)
        assert taken.status_code == 200, taken.text
        after = _rows(w.p, "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=task)[0]
        # The first claim moved the task to In progress. A release keeps it there.
        assert before.status_id == after.status_id
        assert _count(w.p, "SELECT count(*) FROM pm_task_assignees WHERE task_id = "
                           "CAST(:t AS uuid) AND assignee = :a", t=task, a=ALICE) == 1

    @pytest.mark.parametrize("who", ["alice_person", "carol_admin", "dave_assignee"])
    async def test_x9c_the_member_an_admin_and_an_assignee_release(self, w, who):
        task = _fresh(w, assignee=DAVE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            released = await call(getattr(w, who), "POST", f"/projects/agent-runs/{run}/release", {})
        assert released.status_code == 200, released.text
        assert _open_runs(w, task) == []
        [line] = _rows(w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities "
                            "WHERE run_id = CAST(:i AS uuid)", i=run)
        assert line.body.startswith("Released by ")

    async def test_x10_a_stranger_may_not_release(self, w):
        """A caller who sees the task and has no right: 403 (as ``finish``).
        A caller who cannot see the task: 404, so no id leaks."""
        task = _fresh(w, grant=ALICE)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            seen = await call(w.eve_stranger, "POST", f"/projects/agent-runs/{run}/release", {})
            blind = await call(w.mallory_blind, "POST", f"/projects/agent-runs/{run}/release", {})
        assert seen.status_code == 403, seen.text
        assert blind.status_code == 404, blind.text
        assert len(_open_runs(w, task)) == 1

    async def test_x11_an_agent_of_another_client_may_not_release(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            other_client = await call(w.alice_cursor, "POST", f"/projects/agent-runs/{run}/release", {})
            other_member = await call(w.bob, "POST", f"/projects/agent-runs/{run}/release", {})
        assert other_client.status_code == 403 and other_member.status_code == 403
        assert len(_open_runs(w, task)) == 1

    async def test_x11b_a_completed_run_cannot_be_released(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            run = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            fin = await call(w.alice, "POST", f"/projects/agent-runs/{run}/finish", {})
            assert fin.status_code == 200, fin.text
            released = await call(w.alice, "POST", f"/projects/agent-runs/{run}/release", {})
        assert released.status_code == 409

    # ── The reads ───────────────────────────────────────────────────────

    async def test_x12_held_by_and_unheld_in_the_task_reads(self, w):
        held, stale, free = _fresh(w), _fresh(w), _fresh(w)
        async with _session(w.p) as call:
            await _claim(call, w.alice, held)
            await _claim(call, w.alice, stale)
            _age(w, stale, 3 * 3600)
            one = (await call(w.eve_stranger, "GET", f"/projects/tasks/{held}")).json()
            old = (await call(w.eve_stranger, "GET", f"/projects/tasks/{stale}")).json()
            none = (await call(w.eve_stranger, "GET", f"/projects/tasks/{free}")).json()
            listing = (await call(w.eve_stranger, "GET", "/projects/tasks?unheld=true&page_size=100")).json()
            everything = (await call(w.eve_stranger, "GET", "/projects/tasks?page_size=100")).json()
        assert one["held_by"]["member"] == ALICE
        assert one["held_by"]["member_name"] == "Alice Archer"
        assert one["held_by"]["client_name"] == "Claude Code"
        assert one["held_by"]["stale"] is False and one["held_by"]["since"]
        assert old["held_by"]["stale"] is True
        assert none["held_by"] is None
        ids = {r["id"] for r in listing["rows"]}
        assert free in ids and stale in ids and held not in ids
        assert {held, stale, free} <= {r["id"] for r in everything["rows"]}

    # ── SW-13c: one member, two machines, one stamp ──────────────────────

    async def test_z1_a_live_run_of_another_connection_holds_the_task(self, w):
        """(a) Machine A's live run holds the task. Machine B of the same
        member, with the same client id and so the same stamp, is refused
        with ``task_held`` at the claim AND at the start. Mutation Z1."""
        task = _fresh(w)
        pc = _agent(ALICE, w.org, _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code"), VIA)
        async with _session(w.p) as call:
            held = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            claim = await _claim(call, pc, task)
            start = await _start(call, pc, task)
        for answer in (claim, start):
            assert answer.status_code == 409, answer.text
            detail = _detail(answer)
            assert detail["error"] == "task_held"
            assert detail["held_by"]["member"] == ALICE and detail["held_by"]["client_via"] == VIA
        assert [str(r.id) for r in _open_runs(w, task)] == [held]

    async def test_z2_the_same_connection_gets_its_own_run_back(self, w):
        """(b) The control of (a): machine A gets its own run back, while a
        second connection of the same stamp exists."""
        task = _fresh(w)
        _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code")
        async with _session(w.p) as call:
            first = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            again = await _claim(call, w.alice, task)
            started = await _start(call, w.alice, task)
        assert again.status_code == 200 and again.json()["agent_run_id"] == first
        assert started.status_code == 200 and started.json()["created"] is False
        assert started.json()["run"]["id"] == first

    @pytest.mark.parametrize("how", ["revoked", "expired"])
    async def test_z3_a_revoked_or_expired_holder_is_taken_over_at_once(self, w, how):
        """(c) A member signs in again on the same machine: the old
        connection is revoked (or it expired). Its run can never send a
        heartbeat, so a claim from the new connection takes the task over at
        once, with the line. Mutation Z2."""
        task = _fresh(w)
        old_conn = _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code")
        old = _agent(ALICE, w.org, old_conn, VIA)
        async with _session(w.p) as call:
            old_run = (await _claim(call, old, task)).json()["agent_run_id"]
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE agent_connection SET revoked_at = now() WHERE id = CAST(:i AS uuid)"
                if how == "revoked" else
                "UPDATE agent_connection SET expires_at = now() - interval '1 minute' "
                "WHERE id = CAST(:i AS uuid)"), {"i": old_conn})
        async with _session(w.p) as call:
            taken = await _claim(call, w.alice, task)
        assert taken.status_code == 200, taken.text
        new_run = taken.json()["agent_run_id"]
        assert new_run != old_run
        [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                      i=old_run)
        assert row.status == "abandoned"
        [line] = _rows(w.p, "SELECT content->>'body' AS body FROM pm_agent_run_activities "
                            "WHERE run_id = CAST(:i AS uuid)", i=old_run)
        assert line.body == "Taken over by Alice Archer/Claude Code"
        assert [str(r.id) for r in _open_runs(w, task)] == [new_run]

    async def test_z4_a_person_start_keeps_the_old_rule(self, w):
        """(d) A person ("Ask an agent") binds no connection in the gate. The
        live run of the same member and stamp on another connection is still
        the person's own run, as before SW-13c: the start answers it with
        ``created: false`` and no 409. Mutation Z3."""
        task = _fresh(w)
        other = _agent(ALICE, w.org, _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code"),
                       VIA)
        async with _session(w.p) as call:
            run = (await _claim(call, other, task)).json()["agent_run_id"]
            answer = await call(w.alice_person, "POST", f"/projects/tasks/{task}/agent-runs",
                                {"connection_id": w.conn_alice, "body": "Go on, please",
                                 "idempotency_key": _key()})
        assert answer.status_code == 200, answer.text
        assert answer.json()["created"] is False and answer.json()["run"]["id"] == run
        assert [str(r.id) for r in _open_runs(w, task)] == [run]

    async def test_z5_the_reads_agree_with_the_gate_for_a_revoked_holder(self, w):
        """(e) One rule in three places. While the holder's connection lives,
        ``held_by.stale`` is false, ``unheld`` leaves the task out and the
        gate refuses. After the revoke, all three say free. Mutations Z2, Z4
        and Z5."""
        task = _fresh(w)
        conn = _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code")
        holder = _agent(ALICE, w.org, conn, VIA)

        async def reads(call: Any) -> tuple[bool, bool]:
            one = (await call(w.eve_stranger, "GET", f"/projects/tasks/{task}")).json()
            listing = (await call(w.eve_stranger, "GET",
                                  "/projects/tasks?unheld=true&page_size=100")).json()
            return one["held_by"]["stale"], task in {r["id"] for r in listing["rows"]}

        async with _session(w.p) as call:
            assert (await _claim(call, holder, task)).status_code == 200
            assert await reads(call) == (False, False)
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now() "
                           "WHERE id = CAST(:i AS uuid)"), {"i": conn})
        async with _session(w.p) as call:
            assert await reads(call) == (True, True)
            taken = await _claim(call, w.bob, task)
        assert taken.status_code == 200, taken.text

    async def test_z6_a_new_sign_in_releases_its_own_old_run_and_claims(self, w):
        """Fix round 1 (P2-a). A new sign-in on the same machine leaves the
        old connection LIVE, so its run holds the task. The 409 says that the
        holder is the caller's own client, with the run id, and the release
        of that run by the new connection frees the task. Mutation Z8."""
        task = _fresh(w)
        old = _agent(ALICE, w.org, _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code"), VIA)
        new = _agent(ALICE, w.org, _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code"), VIA)
        async with _session(w.p) as call:
            old_run = (await _claim(call, old, task)).json()["agent_run_id"]
            refused = await _claim(call, new, task)
            assert refused.status_code == 409, refused.text
            detail = _detail(refused)
            assert detail["same_client"] is True and detail["run_id"] == old_run
            assert "release_task" in detail["message"]
            released = await call(new, "POST", f"/projects/agent-runs/{old_run}/release", {})
            assert released.status_code == 200, released.text
            claimed = await _claim(call, new, task)
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["agent_run_id"] != old_run
        # Another member's 409 never carries the run id.
        async with _session(w.p) as call:
            stranger = await _claim(call, w.bob, task)
        assert stranger.status_code == 409
        assert _detail(stranger)["same_client"] is False
        assert "run_id" not in _detail(stranger)

    async def test_z7_a_person_start_never_writes_into_a_dead_run(self, w):
        """Fix round 1 (P2-b). Alice's run of a revoked connection is not her
        own any more. Her "Ask an agent" start takes it over and opens a new
        run on the live connection that she names. Mutation Z10."""
        task = _fresh(w)
        dead_conn = _connection(w.p, w.org, ALICE, CLIENT_ID, "Claude Code")
        async with _session(w.p) as call:
            dead_run = (await _claim(call, _agent(ALICE, w.org, dead_conn, VIA), task)
                        ).json()["agent_run_id"]
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now() "
                           "WHERE id = CAST(:i AS uuid)"), {"i": dead_conn})
        async with _session(w.p) as call:
            made = await call(w.alice_person, "POST", f"/projects/tasks/{task}/agent-runs",
                              {"connection_id": w.conn_alice, "body": "Start again",
                               "idempotency_key": _key()})
        assert made.status_code == 201, made.text
        assert made.json()["created"] is True
        new_run = made.json()["run"]
        assert new_run["id"] != dead_run and new_run["connection_id"] == w.conn_alice
        [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)",
                      i=dead_run)
        assert row.status == "abandoned"
        assert [str(r.id) for r in _open_runs(w, task)] == [new_run["id"]]

    # ── The reopen of a completed run ───────────────────────────────────

    async def test_x13_a_member_prompt_cannot_reopen_a_run_on_a_held_task(self, w):
        task = _fresh(w)
        async with _session(w.p) as call:
            first = (await _claim(call, w.alice, task)).json()["agent_run_id"]
            assert (await call(w.alice, "POST", f"/projects/agent-runs/{first}/finish", {})).status_code == 200
            assert (await _claim(call, w.bob, task)).status_code == 200
            prompt = await call(w.alice_person, "POST", f"/projects/agent-runs/{first}/activities",
                                {"type": "prompt", "body": "One more thing", "idempotency_key": _key()})
        assert prompt.status_code == 409, prompt.text
        assert _detail(prompt)["error"] == "task_held"
        [row] = _rows(w.p, "SELECT status FROM pm_agent_runs WHERE id = CAST(:i AS uuid)", i=first)
        assert row.status == "completed"

    async def test_x13b_two_prompts_at_once_reopen_one_run(self, w):
        """H-409 item 1. Two members prompt their two completed runs of one
        task at the same time. Both pass ``_clear_for_reopen``, and the second
        reopen UPDATE breaks the unique index. Mutation: drop the savepoint and
        the ``IntegrityError`` catch in ``_move_run``, and the loser is a 500."""
        bob_person = _person(BOB, w.org)
        for _ in range(5):
            task = _fresh(w)
            async with _session(w.p) as call:
                mine = (await _claim(call, w.alice, task)).json()["agent_run_id"]
                assert (await call(w.alice, "POST", f"/projects/agent-runs/{mine}/finish", {})).status_code == 200
                theirs = (await _claim(call, w.bob, task)).json()["agent_run_id"]
                assert (await call(w.bob, "POST", f"/projects/agent-runs/{theirs}/finish", {})).status_code == 200
                answers = await asyncio.gather(
                    call(w.alice_person, "POST", f"/projects/agent-runs/{mine}/activities",
                         {"type": "prompt", "body": "One more thing", "idempotency_key": _key()}),
                    call(bob_person, "POST", f"/projects/agent-runs/{theirs}/activities",
                         {"type": "prompt", "body": "And this", "idempotency_key": _key()}),
                )
            codes = sorted(a.status_code for a in answers)
            assert codes == [201, 409], [a.text for a in answers]
            [loser] = [a for a in answers if a.status_code == 409]
            assert _detail(loser)["error"] == "task_held"
            assert len(_open_runs(w, task)) == 1

    # ── The race ────────────────────────────────────────────────────────

    async def test_x14_two_claims_at_once_give_one_run(self, w):
        for _ in range(5):
            task = _fresh(w)
            async with _session(w.p) as call:
                answers = await asyncio.gather(_claim(call, w.alice, task), _claim(call, w.bob, task))
            codes = sorted(a.status_code for a in answers)
            assert codes == [200, 409], [a.text for a in answers]
            [loser] = [a for a in answers if a.status_code == 409]
            assert _detail(loser)["error"] == "task_held"
            assert len(_open_runs(w, task)) == 1

    # ── The migration ───────────────────────────────────────────────────

    async def test_x15_the_migration_closes_duplicates_and_builds_the_index(self, w):
        """Old code could open two runs on one task. The migration keeps the
        newest and moves the rest to ``abandoned``, so the index builds."""
        path = next(p for p in _MIGRATIONS.glob("[0-9]*_*.sql")
                    if "pm_agent_runs_one_open_per_task" in p.read_text(encoding="utf-8")
                    and "CREATE UNIQUE INDEX" in p.read_text(encoding="utf-8"))
        task = _fresh(w)
        other = _fresh(w)
        with w.p.admin_engine.begin() as c:
            c.execute(text("DROP INDEX pm_agent_runs_one_open_per_task"))
            for member, via, conn, at, t in (
                (ALICE, VIA, w.conn_alice, "now() - interval '3 hours'", task),
                (BOB, VIA, w.conn_bob, "now() - interval '2 hours'", task),
                (ALICE, VIA_CURSOR, w.conn_alice_cursor, "now() - interval '1 hour'", task),
                (BOB, VIA, w.conn_bob, "now()", other),
            ):
                c.execute(text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, "
                    "connection_id, client_via, status, started_by, created_at) VALUES "
                    f"(CAST(:o AS uuid), CAST(:t AS uuid), :m, CAST(:c AS uuid), :v, "
                    f"'in_progress', :v, {at})"),
                    {"o": w.org, "t": t, "m": member, "c": conn, "v": via})
            # A closed run of the same task never counts as a duplicate.
            c.execute(text(
                "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, "
                "connection_id, client_via, status, started_by) VALUES "
                "(CAST(:o AS uuid), CAST(:t AS uuid), :m, CAST(:c AS uuid), :v, "
                "'completed', :v)"),
                {"o": w.org, "t": task, "m": CAROL, "c": w.conn_bob, "v": VIA})
        sql = path.read_text(encoding="utf-8")
        engine = w.p.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
        with engine.connect() as c:
            c.connection.dbapi_connection.cursor().execute(sql)
            # A second run changes nothing (idempotent).
            c.connection.dbapi_connection.cursor().execute(sql)
        states = {r.client_via + ":" + r.member_email: r.status for r in _rows(
            w.p, "SELECT client_via, member_email, status FROM pm_agent_runs "
                 "WHERE task_id = CAST(:t AS uuid) AND status <> 'completed'", t=task)}
        assert states == {
            f"{VIA}:{ALICE}": "abandoned", f"{VIA}:{BOB}": "abandoned",
            f"{VIA_CURSOR}:{ALICE}": "in_progress",
        }
        assert [r.status for r in _rows(
            w.p, "SELECT status FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)", t=other)
        ] == ["in_progress"]
        # Each abandoned run carries ONE server line that says why, though the
        # file ran twice. The run that stays carries none.
        lines = {r.member_email + ":" + r.client_via: r.n for r in _rows(
            w.p, "SELECT r.member_email, r.client_via, count(a.id) AS n "
                 "FROM pm_agent_runs r LEFT JOIN pm_agent_run_activities a "
                 "  ON a.run_id = r.id AND a.type = 'response' "
                 " AND (a.content->>'system')::boolean "
                 "WHERE r.task_id = CAST(:t AS uuid) AND r.status <> 'completed' "
                 "GROUP BY r.member_email, r.client_via", t=task)}
        assert lines == {f"{ALICE}:{VIA}": 1, f"{BOB}:{VIA}": 1, f"{ALICE}:{VIA_CURSOR}": 0}
        assert _count(w.p, "SELECT count(*) FROM pg_indexes WHERE indexname = "
                           "'pm_agent_runs_one_open_per_task'") == 1
        # The index now refuses a second open run on the task.
        with pytest.raises(Exception, match="pm_agent_runs_one_open_per_task"), (
            w.p.admin_engine.begin()
        ) as c:
            c.execute(text(
                "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, "
                "client_via, status, started_by) VALUES (CAST(:o AS uuid), "
                "CAST(:t AS uuid), :m, :v, 'created', :m)"),
                {"o": w.org, "t": task, "m": CAROL, "v": VIA})
