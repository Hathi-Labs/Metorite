"""WS-MCP SW-107b — the index that serves the SET NULL of the dispatcher (R8).

Spec: ``project-docs/specs/agent_coordination.md`` SW-107 ("Owner question:
the cost of the delete") and the owner's answer of 2026-11-23.

``pm_agent_runs.dispatched_by_run_id`` (SW-107) is a key to the same table
with ``ON DELETE SET NULL``. So each delete of a run makes Postgres run
``UPDATE ONLY pm_agent_runs SET dispatched_by_run_id = NULL WHERE
dispatched_by_run_id = $1``. The partial index ``pm_agent_runs_dispatched_by``
serves that lookup, so a run delete, and the cascade of an organization
delete, scans no table.

This file runs on a REAL Postgres (R8): the ladder, the phase-4 promotion and
the non-privileged role of ``test_h3_rls_promotion_rehearsal.py``. It seeds
some thousands of runs in two organizations, so the planner makes a real
choice. It never forces the plan with ``enable_seqscan = off``. The structural
fence that the index is the one exemption is ``test_agent_label_fence.py``.

Run it on a private database::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_run_dispatch_index_r8.py -v -rs

Mutations (R7). ``M`` is the migration
``infra/postgres/<n>_agent_run_dispatched_by_index.sql``.

* X1. ``M``: drop the ``CREATE INDEX``. ``test_the_index_is_a_valid_partial_index``,
  ``test_the_set_null_lookup_uses_the_index`` and
  ``test_a_run_delete_scans_no_table``.
* X2. ``M``: drop ``WHERE dispatched_by_run_id IS NOT NULL``.
  ``test_the_index_is_a_valid_partial_index``.
* X3. ``M``: index another column. ``test_the_index_is_a_valid_partial_index``
  and ``test_the_set_null_lookup_uses_the_index``.
* X4. 472: ``ON DELETE CASCADE`` in place of ``SET NULL``.
  ``test_a_run_delete_sets_null_and_keeps_the_lane``.
* X5. ``M``: a statement that fails on a second run (no ``IF NOT EXISTS``).
  ``test_the_file_runs_twice``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_agent_run_exclusive import _seed_task
from tests.unit.test_agent_runs_r8 import _rows
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _ROOT,
    app_engine,
    promoted,
)

OWNER = "sw107b-owner@dispatch.test"
INDEX = "pm_agent_runs_dispatched_by"
#: The SET NULL lookup of the key, in the words that Postgres's RI trigger uses.
SET_NULL_SQL = ("UPDATE ONLY pm_agent_runs SET dispatched_by_run_id = NULL"
                " WHERE dispatched_by_run_id = $1")
#: Runs for each of the two organizations. Some thousands, as production holds.
RUNS_PER_ORG = 2000
#: One run in this many has a dispatcher.
DISPATCHED_EVERY = 10


def _migration() -> Any:
    """The SW-107b file, found by CONTENT, never by number (R1)."""
    found = [p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
             if f"INDEX IF NOT EXISTS {INDEX}" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0]


def _insert_runs(c: Any, org: str, task: str, n: int, boss: str | None = None,
                 every: int = 1) -> None:
    """*n* ended runs of *org* on *task*. Each run *every* has *boss* as its
    dispatcher. Ended runs hold no lease, so the unique open-run indexes
    pass."""
    c.execute(text(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
        " status, started_by, dispatched_by_run_id)"
        " SELECT CAST(:o AS uuid), CAST(:t AS uuid), :m, 'mcp:claude-code', 'completed',"
        "  :m, CASE WHEN g % :e = 0 THEN CAST(:b AS uuid) END"
        " FROM generate_series(1, :n) AS g"),
        {"o": org, "t": task, "m": OWNER, "n": n, "b": boss, "e": every})


def _one_run(c: Any, org: str, task: str, boss: str | None = None) -> str:
    return str(c.execute(text(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
        " status, started_by, dispatched_by_run_id)"
        " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, 'mcp:claude-code', 'completed',"
        "  :m, CAST(:b AS uuid)) RETURNING id"),
        {"o": org, "t": task, "m": OWNER, "b": boss}).scalar())


def _dispatcher(p: Any, run: str) -> str | None:
    [row] = _rows(p, "SELECT dispatched_by_run_id FROM pm_agent_runs"
                     " WHERE id = CAST(:i AS uuid)", i=run)
    return None if row.dispatched_by_run_id is None else str(row.dispatched_by_run_id)


def _nodes(plan: dict) -> list[dict]:
    """Each node of an ``EXPLAIN (FORMAT JSON)`` plan, depth first."""
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


_numbers = iter(range(140_000, 160_000))


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """Two organizations with some thousands of runs each, then ANALYZE."""
    p = promoted
    ns = SimpleNamespace(p=p, org=p.org_a, org_b=p.org_b)
    ns.task = _seed_task(p, p.org_a, OWNER, n=next(_numbers))
    ns.task_b = _seed_task(p, p.org_b, OWNER, n=next(_numbers))
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        ns.boss = _one_run(c, p.org_a, ns.task)
        ns.boss_b = _one_run(c, p.org_b, ns.task_b)
        _insert_runs(c, p.org_a, ns.task, RUNS_PER_ORG, ns.boss, DISPATCHED_EVERY)
        _insert_runs(c, p.org_b, ns.task_b, RUNS_PER_ORG, ns.boss_b, DISPATCHED_EVERY)
    with p.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE pm_agent_runs"))
    yield ns
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))


@_DB_GATE
class TestTheIndexOnARealDatabase:

    def test_the_index_is_a_valid_partial_index(self, world):
        """The definition, word for word as Postgres gives it back. A plain
        btree on one column, not unique, valid and ready, with the predicate
        ``dispatched_by_run_id IS NOT NULL``."""
        [row] = _rows(world.p, (
            "SELECT i.indisvalid, i.indisready, i.indisunique,"
            " pg_get_expr(i.indpred, i.indrelid) AS predicate,"
            " pg_get_indexdef(i.indexrelid) AS definition"
            " FROM pg_index i WHERE i.indexrelid = to_regclass(:n)"), n=INDEX)
        assert (row.indisvalid, row.indisready, row.indisunique) == (True, True, False)
        assert row.predicate == "(dispatched_by_run_id IS NOT NULL)"
        assert row.definition == (
            "CREATE INDEX pm_agent_runs_dispatched_by ON public.pm_agent_runs"
            " USING btree (dispatched_by_run_id) WHERE (dispatched_by_run_id IS NOT NULL)")
        # The one index of the table that names the column.
        named = [r.indexname for r in _rows(world.p, (
            "SELECT indexname FROM pg_indexes WHERE tablename = 'pm_agent_runs'"
            " AND indexdef LIKE '%dispatched_by_run_id%'"))]
        assert named == [INDEX], named

    @pytest.mark.parametrize("generic", [False, True], ids=["custom", "generic"])
    def test_the_set_null_lookup_uses_the_index(self, world, generic):
        """The planner picks the index for the RI lookup on its own, with
        real statistics. A custom plan has the id as a constant. A generic
        plan has ``$1``, as a cached RI plan may."""
        with world.p.admin_engine.connect() as c:
            if generic:
                sql = f"EXPLAIN (FORMAT JSON, GENERIC_PLAN) {SET_NULL_SQL}"
            else:
                sql = f"EXPLAIN (FORMAT JSON) {SET_NULL_SQL.replace('$1', repr(world.boss))}"
            raw = c.connection.dbapi_connection.cursor()
            raw.execute(sql)
            [[plan]] = raw.fetchall()
            c.rollback()
        nodes = _nodes(plan[0]["Plan"])
        kinds = [(n["Node Type"], n.get("Index Name")) for n in nodes]
        assert not [k for k in kinds if k[0] == "Seq Scan"], kinds
        assert [k for k in kinds if k[1] == INDEX], kinds

    def test_a_run_delete_scans_no_table(self, world, app_engine):  # noqa: F811
        """Under the non-privileged role and the bound tenant, as a task
        delete runs it. The counters of the transaction show the index scan
        of the SET NULL and no scan of the table."""
        with world.p.admin_engine.begin() as c:
            boss = _one_run(c, world.org, world.task)
            lanes = [_one_run(c, world.org, world.task, boss) for _ in range(3)]
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
            gone = c.execute(text("DELETE FROM pm_agent_runs WHERE id = CAST(:i AS uuid)"),
                             {"i": boss}).rowcount
            [stats] = c.execute(text(
                "SELECT (SELECT seq_scan FROM pg_stat_xact_user_tables"
                "        WHERE relid = to_regclass('pm_agent_runs')) AS seq_scan,"
                "       pg_stat_get_xact_numscans(to_regclass(:n)) AS idx_scan"),
                {"n": INDEX})
        assert gone == 1
        assert stats.seq_scan == 0, stats
        assert stats.idx_scan and stats.idx_scan >= 1, stats
        assert [_dispatcher(world.p, lane) for lane in lanes] == [None, None, None]

    def test_a_run_delete_sets_null_and_keeps_the_lane(self, world, app_engine):  # noqa: F811
        """SW-107 unchanged: the lane stays, and only its line goes. The
        runs of the other organization keep their dispatcher."""
        with world.p.admin_engine.begin() as c:
            boss = _one_run(c, world.org, world.task)
            lane = _one_run(c, world.org, world.task, boss)
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
            c.execute(text("DELETE FROM pm_agent_runs WHERE id = CAST(:i AS uuid)"),
                      {"i": boss})
        assert _dispatcher(world.p, lane) is None
        [kept] = _rows(world.p, "SELECT count(*) AS n FROM pm_agent_runs"
                                " WHERE dispatched_by_run_id = CAST(:b AS uuid)",
                       b=world.boss_b)
        assert kept.n == RUNS_PER_ORG // DISPATCHED_EVERY

    def test_an_organization_delete_removes_each_run_of_the_organization(self, world):
        """The offboarding statement of ``acb_auth/offboard.py``. Each run of
        the organization goes with it, 201 of them, and the delete finishes.
        Since migration 491 (PR 2737) a run can name a dispatcher of its own
        organization only, so a run of another organization cannot name one of
        these runs and the delete has no row of another organization to set to
        NULL. The first build of this test planted such a row. The insert is
        refused now, by ``trg_pm_agent_runs_same_org_dispatched_by_run_id``."""
        org_c = str(uuid.uuid4())
        with world.p.admin_engine.begin() as c:
            c.execute(text("INSERT INTO organization (id, slug, display_name)"
                           " VALUES (CAST(:o AS uuid), :s, 'SW-107b gone')"),
                      {"o": org_c, "s": f"sw107b-{org_c[:8]}"})
        task_c = _seed_task(world.p, org_c, OWNER, n=next(_numbers))
        with world.p.admin_engine.begin() as c:
            boss_c = _one_run(c, org_c, task_c)
            _insert_runs(c, org_c, task_c, 200, boss_c, 2)
        [before] = _rows(world.p, "SELECT count(*) AS n FROM pm_agent_runs"
                                  " WHERE organization_id = CAST(:o AS uuid)", o=org_c)
        assert before.n == 201
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                      {"o": org_c})
        [left] = _rows(world.p, "SELECT count(*) AS n FROM pm_agent_runs"
                                " WHERE organization_id = CAST(:o AS uuid)", o=org_c)
        assert left.n == 0

    def test_the_file_runs_twice(self, world):
        """Idempotent (R6): the runner tries a whole file again on a lock
        timeout. Two more runs, no error, one index, the same definition."""
        before = _rows(world.p, "SELECT pg_get_indexdef(to_regclass(:n)) AS d", n=INDEX)
        for _ in range(2):
            with world.p.admin_engine.begin() as c:
                _exec_file(c, str(_migration()))
        after = _rows(world.p, "SELECT pg_get_indexdef(to_regclass(:n)) AS d", n=INDEX)
        assert before == after and before[0].d
        [count] = _rows(world.p, "SELECT count(*) AS n FROM pg_indexes"
                                 " WHERE indexname = :n", n=INDEX)
        assert count.n == 1
