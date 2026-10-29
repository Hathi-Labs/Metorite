"""WS-BRIEF BR-1a — the shared durable job queue on a real database (R8).

Reference: ``docs/job_queue.md``. Code: ``gateway/jobs/queue.py`` and
``gateway/jobs/worker.py``.

The queue runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations. Each tick takes a frozen
``now``, so no test sleeps for a backoff or a lease.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_job_queue.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (BR-1a).**

a. A job queued before a restart runs once after it.
b. A failing job retries with a backoff to ``max_attempts``, then turns
   ``failed`` with ``last_error`` kept. A job that passes on try N is ``done``.
c. A running job whose lease passed (a killed worker) is claimed again, and
   runs.
d. Two workers at the same time never run one job twice.
e. The worker of org A cannot claim, settle or read a job of org B.
f. A payload with NaN or Infinity, or over the cap, is refused at enqueue.
g. Every test takes a frozen clock.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``worker.run_one``: open the run session with no tenant binding (a
  plain session from ``get_session_factory()``).
  ``test_r8_org_a_cannot_claim_settle_or_read_a_job_of_org_b`` goes red.
* M2. ``queue._CLAIM_SQL``: drop ``FOR UPDATE SKIP LOCKED`` and the token
  match of ``queue._HOLD_SQL``.
  ``test_r8_two_workers_never_run_one_job_twice`` goes red, within its 30 s
  limit.
* M3. ``worker._call``: await the handler with no timeout scope.
  ``test_r8_a_hung_handler_times_out_and_the_next_org_still_runs`` goes red
  within its 20 s limit.
* M4. ``worker._settle_failure``: drop the ``jobs.failed`` error line.
  ``test_r8_a_job_that_fails_for_good_logs_jobs_failed_once`` goes red.

**Acceptance (BR-1b-a).** The detached mode, ``jobs.prune`` and the timeout
label. The tests are at the end of this file, under "BR-1b-a".

1. A detached handler runs with no session open and no pooled connection
   checked out. A new session then holds, writes and completes.
2. A detached handler that lost its lease settles nothing.
3. A handler that reads its intent mark on a retry does not call the
   provider again.
4. ``jobs.prune`` deletes only the old terminal rows of the bound org, as
   the app role under RLS.
5. ``run_org`` seeds one prune job for each org and UTC day. The cache in
   memory answers a later tick of the day with no statement.
6. A handler that hangs in the database fails with ``HandlerTimeout``, and
   the next org still runs.
7. A detached handler that hangs fails with ``HandlerTimeout``. One that
   returns without a settle fails with ``Unsettled``.

* M5. ``worker._run_detached``: open ``tenant_session(org)`` around the
  ``_call``. ``test_r8_a_detached_handler_runs_with_no_session_open`` goes red.
* M6. ``queue._COMPLETE_SQL``: drop ``AND locked_by = ...``.
  ``test_r8_a_detached_handler_that_lost_its_lease_settles_nothing`` goes red.
* M7. The intent handler of the test: skip the read of the mark.
  ``test_r8_an_intent_mark_stops_a_second_provider_call`` goes red.
* M8. ``queue._PRUNE_SQL``: drop the age cut-off, or the state filter.
  ``test_r8_prune_deletes_only_old_terminal_rows_of_the_bound_org`` goes red.
* M9. ``worker.run_one``: settle the outer failure with ``exc``, not
  ``attempt.timeout or exc``.
  ``test_r8_a_handler_that_hangs_in_the_database_fails_as_a_timeout`` goes red.

**Review round 1 of BR-1b-a.** The tests are at the end of this file.

* M10. ``worker.run_one``: settle with the *now* of the claim, not the
  clock. ``test_r8_a_slow_try_takes_its_backoff_from_its_end`` (both modes)
  and ``test_r8_the_worker_reads_the_clock_for_each_job`` go red.
* M11. ``queue.give_up``: drop the ``jobs.failed`` line.
  ``test_r8_an_intent_mark_stops_a_second_provider_call`` and
  ``test_r8_a_detached_kind_that_is_not_idempotent_gets_one_try`` go red.
* M12. ``worker._run_detached``: drop the guard of a kind that is not
  idempotent. ``test_r8_a_detached_kind_that_is_not_idempotent_gets_one_try``
  goes red.
* M13. ``queue._PRUNE_SQL``: drop the bound-tenant belt.
  ``test_r8_a_prune_on_a_session_that_bypasses_rls_takes_the_bound_org_only``
  goes red.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

pytest.importorskip("sqlalchemy")

from acb_common.db import current_tenant, tenant_session
from gateway.jobs import queue, worker
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS jobs (" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
#: The kinds of this suite. A kind holds the tag, so teardown finds its rows.
OK = f"test.br1a{TAG}.ok"
BAD = f"test.br1a{TAG}.bad"
FLAKY = f"test.br1a{TAG}.flaky"
SLOW = f"test.br1a{TAG}.slow"
CHILD = f"test.br1a{TAG}.child"
NOBODY = f"test.br1a{TAG}.nobody"
HUNG = f"test.br1a{TAG}.hung"
DETACHED = f"test.br1a{TAG}.detached"
OLD = f"test.br1a{TAG}.old"
#: The domain table of the intent-mark test. The test makes it and drops it.
INTENT_TABLE = f"br1b_intent_{TAG}"

#: The named error of an uncertain send (review P1-2).
UNCERTAIN = "UncertainSend: the provider call possibly ran, so no second call"

#: The frozen clock: Tuesday 10 March 2099, 09:00 UTC.
T = datetime(2099, 3, 10, 9, 0, tzinfo=UTC)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


# ── The world ──────────────────────────────────────────────────────────────


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Two orgs as the worker's discovery read, the switch on, and an empty
    registry. Teardown deletes the rows of this suite's kinds."""
    p = promoted
    ns = SimpleNamespace(p=p, runs=Counter(), seen=[])

    async def orgs() -> list[str]:
        return [p.org_a, p.org_b]

    monkeypatch.setattr(worker, "_orgs", orgs)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=True))
    monkeypatch.setattr(worker, "_HANDLERS", {})
    monkeypatch.setattr(worker, "_PRUNE_SEEDED", {})
    monkeypatch.setattr(worker, "prune_hooks", [])
    worker._stopping.clear()
    yield ns
    worker._stopping.clear()
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM jobs WHERE kind LIKE :k"), {"k": f"test.br1a{TAG}.%"})
        c.execute(text("DELETE FROM jobs WHERE kind = :k AND organization_id IN "
                       "(CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"k": worker.PRUNE_KIND, "a": p.org_a, "b": p.org_b})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _enqueue(org: str, kind: str, payload: dict | None = None, **kw: Any) -> str:
    async with tenant_session(org) as db:
        out = await queue.enqueue(db, kind, payload, **{"now": T, **kw})
    return out.id


def _row(world: Any, job_id: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT * FROM jobs WHERE id = CAST(:i AS uuid)"),
                         {"i": job_id}).fetchone()


def _count(world: Any, kind: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM jobs WHERE kind = :k"),
                             {"k": kind}).scalar_one())


def _events(caps: list[dict], name: str) -> list[dict]:
    return [c for c in caps if c.get("event") == name]


def _register_ok(world: Any) -> None:
    async def ok(db: Any, payload: dict[str, Any]) -> None:
        bound = (await db.execute(text(
            "SELECT current_setting('app.tenant_id', true)"))).scalar_one()
        world.runs[payload["n"]] += 1
        world.seen.append((payload["n"], bound, current_tenant()))

    worker.register(OK, ok)


# ── The schema and the wiring ──────────────────────────────────────────────


def test_the_migration_holds_the_states_and_the_rls_of_the_queue() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    for state in queue.STATES:
        assert f"'{state}'" in sql
    assert "CONSTRAINT jobs_state CHECK (state IN ('queued', 'running', 'done', 'failed'))" in sql
    assert "ALTER TABLE jobs FORCE  ROW LEVEL SECURITY" in sql
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "gtd_" not in sql
    assert "FOR UPDATE SKIP LOCKED" in queue._CLAIM_SQL
    # No statement takes an org: RLS binds each one. The prune DELETE also
    # names the bound tenant, as a belt for a session that bypasses RLS.
    for name in dir(queue):
        if name.endswith("_SQL") and name != "_PRUNE_SQL":
            assert "organization_id =" not in getattr(queue, name), name
    assert f"organization_id = {queue._BOUND_ORG} " in queue._PRUNE_SQL
    assert ":org" not in queue._PRUNE_SQL
    assert "organization_id" not in queue._INSERT_SQL.split("VALUES")[0]


def test_main_starts_and_stops_the_worker_with_no_condition() -> None:
    """The flag gate lives inside the start function (gateway AGENTS.md
    item 1), so main.py starts and stops the worker unconditionally."""
    main = (_ROOT / "apps" / "services" / "gateway" / "gateway" / "main.py").read_text(
        encoding="utf-8")
    assert "from gateway.jobs.worker import start_job_worker" in main
    assert "await start_job_worker()" in main
    assert "await stop_job_worker()" in main
    assert "if worker_enabled" not in main and "settings.job_queue" not in main


def test_the_worker_is_dark_by_default(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["job_queue"].default is False
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=False))
    assert worker.worker_enabled() is False

    async def go() -> tuple[bool, dict]:
        started = await worker.start_job_worker()
        return started, await worker.run_tick(T)

    assert asyncio.run(go()) == (False, {})
    assert worker._task is None


# ── a. Restart ─────────────────────────────────────────────────────────────


def test_r8_a_job_queued_before_a_restart_runs_once_after_it(
    world, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_email_keep_separate import _assert_non_priv

    _assert_non_priv(app_engine)
    _register_ok(world)
    org = world.p.org_b
    job = _go(world, lambda: _enqueue(org, OK, {"n": 1}, run_at=T + timedelta(minutes=10)))

    # The first worker starts, ticks once on the real clock and stops. The
    # job is for 2099, so it does not run.
    ticked = asyncio.Event()
    real_tick = worker.run_tick

    async def tick(now: datetime | None = None) -> dict:
        try:
            return await real_tick(now)
        finally:
            ticked.set()

    async def first_life() -> bool:
        worker.run_tick = tick  # type: ignore[assignment]
        try:
            started = await worker.start_job_worker()
            await asyncio.wait_for(ticked.wait(), 10)
            await worker.stop_job_worker()
            return started
        finally:
            worker.run_tick = real_tick  # type: ignore[assignment]

    assert _go(world, first_life) is True
    assert worker._task is None
    assert _row(world, job).state == "queued" and world.runs[1] == 0

    # The second worker: a new event loop and a new engine, as after a restart.
    worker._stopping.clear()
    early, due, again = _go(world, lambda: _ticks(T + timedelta(minutes=9),
                                                  T + timedelta(minutes=10),
                                                  T + timedelta(hours=1)))
    assert early == {} and due == {org: Counter(done=1)} and again == {}
    row = _row(world, job)
    assert row.state == "done" and row.attempts == 1 and row.finished_at == T + timedelta(
        minutes=10)
    assert world.runs[1] == 1


async def _ticks(*instants: datetime) -> list[dict]:
    return [await worker.run_tick(at) for at in instants]


# ── b. Retry with a backoff ────────────────────────────────────────────────


def test_r8_a_failing_job_retries_with_backoff_then_fails_with_its_error(world) -> None:
    calls: list[int] = []

    async def bad(db: Any, payload: dict[str, Any]) -> None:
        calls.append(1)
        raise RuntimeError(f"provider said no {len(calls)}")

    tries = Counter()

    async def flaky(db: Any, payload: dict[str, Any]) -> None:
        tries["n"] += 1
        if tries["n"] < 3:
            raise TimeoutError("slow")

    worker.register(BAD, bad)
    worker.register(FLAKY, flaky)
    org = world.p.org_a

    async def go() -> list[dict]:
        bad_id = await _enqueue(org, BAD, {}, max_attempts=3)
        flaky_id = await _enqueue(org, FLAKY, {}, max_attempts=5)
        world.ids = (bad_id, flaky_id)
        s = timedelta(seconds=1)
        return await _ticks(T, T + 29 * s, T + 30 * s, T + 89 * s, T + 90 * s,
                            T + timedelta(days=1))

    t0, t29, t30, t89, t90, later = _go(world, go)
    bad_id, flaky_id = world.ids
    assert t0 == {org: Counter(retry=2)}
    assert t29 == {}  # the backoff of try 1 is 30 s
    assert t30 == {org: Counter(retry=2)}
    assert t89 == {}  # the backoff of try 2 is 60 s
    assert t90 == {org: Counter(failed=1, done=1)}  # FLAKY passes on try 3
    assert later == {}
    row = _row(world, bad_id)
    assert row.state == "failed" and row.attempts == row.max_attempts == 3
    assert row.last_error == "RuntimeError: provider said no 3"
    assert row.finished_at == T + timedelta(seconds=90) and row.lease_until is None
    assert len(calls) == 3
    ok = _row(world, flaky_id)
    assert ok.state == "done" and ok.attempts == 3 and tries["n"] == 3
    assert ok.last_error == "TimeoutError: slow"


def test_r8_a_failed_attempt_rolls_back_the_writes_of_its_handler(world) -> None:
    """The handler and the settle share one transaction: the writes of a
    failed try go, and the writes of a passed try stay with ``done``."""
    async def parent(db: Any, payload: dict[str, Any]) -> None:
        await queue.enqueue(db, CHILD, {"try": payload["fail"]}, now=T)
        if payload["fail"]:
            raise ValueError("no")

    worker.register(OK, parent)
    org = world.p.org_b

    async def go() -> list[dict]:
        await _enqueue(org, OK, {"fail": True}, max_attempts=1)
        await _enqueue(org, OK, {"fail": False}, max_attempts=1)
        return await _ticks(T)

    assert _go(world, go) == [{org: Counter(failed=1, done=1)}]
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT payload, organization_id FROM jobs WHERE kind = :k"),
                         {"k": CHILD}).fetchall()
    assert [(r.payload, str(r.organization_id)) for r in rows] == [({"try": False}, org)]


# ── c. A killed worker ─────────────────────────────────────────────────────


def test_r8_a_running_job_with_a_lost_lease_is_claimed_again_and_runs(world) -> None:
    _register_ok(world)
    org = world.p.org_b

    async def go() -> tuple:
        job = await _enqueue(org, OK, {"n": 7})
        # Worker 1 claims and dies: it never runs the handler.
        async with tenant_session(org) as db:
            dead = await queue.claim(db, [OK], now=T)
        assert dead is not None and dead.attempts == 1
        before = await worker.run_org(org, T + queue.LEASE - timedelta(seconds=1))
        after = await worker.run_org(org, T + queue.LEASE)
        # The dead worker comes back late. Its token is gone.
        async with tenant_session(org) as db:
            late = await queue.complete(db, dead, now=T + queue.LEASE)
            late_fail = await queue.fail(db, dead, "late", now=T + queue.LEASE)
        return job, before, after, late, late_fail

    with structlog.testing.capture_logs() as caps:
        job, before, after, late, late_fail = _go(world, go)
    assert before == Counter() and after == Counter(done=1)
    expired = _events(caps, "jobs.lease_expired")
    assert len(expired) == 1 and expired[0]["log_level"] == "warning"
    assert expired[0]["job_id"] == job and expired[0]["outcome"] == "claimed_again"
    assert not _events(caps, "jobs.failed")
    assert late == 0 and late_fail is None
    row = _row(world, job)
    assert row.state == "done" and row.attempts == 2 and world.runs[7] == 1


def test_r8_a_lost_lease_at_the_last_attempt_fails_the_job(world) -> None:
    _register_ok(world)
    org = world.p.org_a

    async def go() -> tuple:
        job = await _enqueue(org, OK, {"n": 8}, max_attempts=1)
        async with tenant_session(org) as db:
            assert await queue.claim(db, [OK], now=T) is not None
        return job, await worker.run_org(org, T + queue.LEASE)

    with structlog.testing.capture_logs() as caps:
        job, counts = _go(world, go)
    assert counts == Counter()
    row = _row(world, job)
    assert row.state == "failed" and row.last_error == "lease expired" and world.runs[8] == 0
    expired = _events(caps, "jobs.lease_expired")
    assert len(expired) == 1 and expired[0]["log_level"] == "error"
    assert expired[0]["job_id"] == job and expired[0]["outcome"] == "failed"


def test_r8_a_live_worker_keeps_its_row_after_the_lease_time(world) -> None:
    """The run holds the row lock, so a second worker skips the row while the
    first runs it, also after ``lease_until``."""
    org = world.p.org_b
    inner: list[Counter] = []

    async def slow(db: Any, payload: dict[str, Any]) -> None:
        world.runs["slow"] += 1
        inner.append(await worker.run_org(org, T + queue.LEASE * 3))

    worker.register(SLOW, slow)

    async def go() -> Counter:
        await _enqueue(org, SLOW, {})
        return await worker.run_org(org, T)

    assert _go(world, go) == Counter(done=1)
    assert inner == [Counter()] and world.runs["slow"] == 1


# ── d. Two workers ─────────────────────────────────────────────────────────


class _Pairing:
    """Two workers meet here inside a run. Each waits a short time for the
    other, so two runs that overlap do overlap."""

    def __init__(self) -> None:
        self.waiting: asyncio.Future[None] | None = None
        self.met = 0

    async def meet(self) -> None:
        if self.waiting is not None and not self.waiting.done():
            self.waiting.set_result(None)
            self.waiting, self.met = None, self.met + 1
            return
        mine: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiting = mine
        try:
            await asyncio.wait_for(asyncio.shield(mine), 0.5)
        except TimeoutError:
            if self.waiting is mine:
                self.waiting = None


def test_r8_two_workers_never_run_one_job_twice(world) -> None:
    org = world.p.org_a
    pairing = _Pairing()

    async def paired(db: Any, payload: dict[str, Any]) -> None:
        world.runs[payload["n"]] += 1
        await pairing.meet()

    worker.register(OK, paired)

    async def go() -> list[Counter]:
        for n in range(8):
            await _enqueue(org, OK, {"n": n})
        # A limit, so a lost SKIP LOCKED fails in seconds and does not wait
        # for the 600 s idle timeout of the database.
        return list(await asyncio.wait_for(
            asyncio.gather(worker.run_org(org, T), worker.run_org(org, T)), 30))

    first, second = _go(world, go)
    assert pairing.met >= 1  # non-vacuity: two runs held two jobs at once
    assert first["done"] > 0 and second["done"] > 0
    assert first["done"] + second["done"] == 8
    assert world.runs == Counter({n: 1 for n in range(8)})


# ── e. Two orgs ────────────────────────────────────────────────────────────


def test_r8_org_a_cannot_claim_settle_or_read_a_job_of_org_b(world) -> None:
    _register_ok(world)
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        theirs = await _enqueue(b, OK, {"n": 2})
        out: dict[str, Any] = {"theirs": theirs}
        # Bound to org A, with only org B's job queued.
        out["a_first"] = await worker.run_org(a, T)
        async with tenant_session(a) as db:
            out["a_claim"] = await queue.claim(db, [OK], now=T)
            out["a_read"] = await queue.get_job(db, theirs)
        # Org B claims its own job. Org A then tries to settle it.
        async with tenant_session(b) as db:
            claim = await queue.claim(db, [OK], now=T)
        out["b_claim"] = claim
        async with tenant_session(a) as db:
            out["a_hold"] = await queue.hold(db, claim)
            out["a_complete"] = await queue.complete(db, claim, now=T)
            out["a_fail"] = await queue.fail(db, claim, "x", now=T)
        # The positive control: org A's worker runs org A's own job.
        ours = await _enqueue(a, OK, {"n": 3})
        out["ours"] = ours
        out["a_own"] = await worker.run_org(a, T)
        async with tenant_session(b) as db:
            out["b_complete"] = await queue.complete(db, claim, now=T)
        return out

    out = _go(world, go)
    assert out["a_first"] == Counter() and out["a_claim"] is None and out["a_read"] is None
    assert out["b_claim"] is not None and out["b_claim"].organization_id == b
    assert out["a_hold"] is False and out["a_complete"] == 0 and out["a_fail"] is None
    assert out["a_own"] == Counter(done=1)
    assert out["b_complete"] == 1
    assert _row(world, out["theirs"]).state == "done"
    assert str(_row(world, out["ours"]).organization_id) == a
    assert world.runs == Counter({3: 1})
    # The handler ran inside org A's own binding.
    assert world.seen == [(3, a, a)]


def test_r8_each_org_runs_its_own_jobs_in_its_own_tenant(world) -> None:
    _register_ok(world)
    a, b = world.p.org_a, world.p.org_b

    async def go() -> list[dict]:
        await _enqueue(a, OK, {"n": 10})
        await _enqueue(b, OK, {"n": 20})
        return await _ticks(T)

    assert _go(world, go) == [{a: Counter(done=1), b: Counter(done=1)}]
    assert sorted(world.seen) == [(10, a, a), (20, b, b)]


# ── f. The payload ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    {"x": math.nan},
    {"x": math.inf},
    {"x": -math.inf},
    {"deep": [{"y": [1, 2, float("nan")]}]},
    {"big": "a" * (queue.MAX_PAYLOAD_BYTES + 1)},
    {"nul": "a\x00b"},
    ["not", "an", "object"],
    "text",
    {"x": object()},
])
def test_r8_a_bad_payload_is_refused_at_enqueue_and_writes_nothing(world, payload) -> None:
    async def go() -> None:
        async with tenant_session(world.p.org_a) as db:
            await queue.enqueue(db, OK, payload, now=T)

    with pytest.raises(queue.JobError):
        _go(world, go)
    assert _count(world, OK) == 0


def test_r8_a_payload_at_the_cap_is_taken(world) -> None:
    overhead = len('{"big":""}')
    payload = {"big": "a" * (queue.MAX_PAYLOAD_BYTES - overhead)}
    job = _go(world, lambda: _enqueue(world.p.org_a, OK, payload))
    assert _row(world, job).payload == payload


@pytest.mark.parametrize("options", [
    {"max_attempts": 0}, {"max_attempts": 26}, {"max_attempts": True},
    {"dedupe_key": ""}, {"dedupe_key": "k" * 201},
    {"run_at": datetime(2099, 3, 10, 9, 0)},
])
def test_r8_bad_options_are_refused_at_enqueue(world, options) -> None:
    with pytest.raises(queue.JobError):
        _go(world, lambda: _enqueue(world.p.org_a, OK, {}, **options))
    assert _count(world, OK) == 0


@pytest.mark.parametrize("kind", ["", "Upper", "1st", "has space", "a" * 65, None])
def test_a_bad_kind_is_refused(kind) -> None:
    with pytest.raises(queue.JobError):
        queue.check_kind(kind)
    with pytest.raises(queue.JobError):
        worker.register(kind, lambda db, p: None)  # type: ignore[arg-type]


# ── The dedupe key and the registry ────────────────────────────────────────


def test_r8_a_dedupe_key_keeps_one_live_job_for_each_org(world) -> None:
    _register_ok(world)
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with tenant_session(a) as db:
            out["one"] = await queue.enqueue(db, OK, {"n": 1}, dedupe_key="k", now=T)
            out["two"] = await queue.enqueue(db, OK, {"n": 1}, dedupe_key="k", now=T)
        async with tenant_session(b) as db:
            out["other_org"] = await queue.enqueue(db, OK, {"n": 1}, dedupe_key="k", now=T)
        out["ran"] = await worker.run_org(a, T)
        async with tenant_session(a) as db:
            out["after"] = await queue.enqueue(db, OK, {"n": 1}, dedupe_key="k", now=T)
        return out

    out = _go(world, go)
    assert out["one"].created and not out["two"].created and out["two"].id == out["one"].id
    assert out["other_org"].created and out["other_org"].id != out["one"].id
    assert out["ran"] == Counter(done=1)
    assert out["after"].created and out["after"].id != out["one"].id


def test_r8_a_job_of_a_kind_with_no_handler_waits(world) -> None:
    _register_ok(world)
    org = world.p.org_a

    async def go() -> list[dict]:
        await _enqueue(org, NOBODY, {})
        return await _ticks(T, T + timedelta(days=1))

    assert _go(world, go) == [{}, {}]
    with world.p.admin_engine.connect() as c:
        row = c.execute(text("SELECT state, attempts FROM jobs WHERE kind = :k"),
                        {"k": NOBODY}).one()
    assert (row.state, row.attempts) == ("queued", 0)


def test_a_kind_takes_one_handler() -> None:
    async def h(db: Any, payload: dict[str, Any]) -> None:
        return None

    kind = f"test.br1a{TAG}.once"
    worker.register(kind, h)
    try:
        with pytest.raises(ValueError):
            worker.register(kind, h)
        worker.register(kind, h, replace=True)
        assert kind in worker.kinds()
    finally:
        worker.unregister(kind)
    assert kind not in worker.kinds()


def test_the_backoff_doubles_to_its_cap() -> None:
    assert [queue.backoff(n).total_seconds() for n in (1, 2, 3)] == [30, 60, 120]
    assert queue.backoff(30) == queue.BACKOFF_CAP


# ── The time limit, the signals and the clock (BR-1a review round 1) ───────


def test_the_handler_timeout_is_below_the_lease_and_the_idle_timeout() -> None:
    """A hung handler must fail its attempt before the lease passes, and
    before Postgres kills the idle transaction that holds the row lock."""
    from acb_common import db as seam

    idle = timedelta(milliseconds=int(seam._IDLE_IN_TXN_TIMEOUT_MS))
    assert idle == timedelta(minutes=10)
    assert timedelta(seconds=worker.HANDLER_TIMEOUT) < queue.LEASE < idle


def test_r8_a_hung_handler_times_out_and_the_next_org_still_runs(world, monkeypatch) -> None:
    monkeypatch.setattr(worker, "HANDLER_TIMEOUT", 0.2)
    started: list[str] = []

    async def hung(db: Any, payload: dict[str, Any]) -> None:
        started.append(current_tenant())
        await asyncio.Event().wait()  # never set

    worker.register(HUNG, hung)
    _register_ok(world)
    a, b = world.p.org_a, world.p.org_b

    async def go() -> tuple:
        stuck = await _enqueue(a, HUNG, {}, max_attempts=2)
        ok = await _enqueue(b, OK, {"n": 4})
        # The orgs run in the order a, b. A limit, so a lost timeout fails fast.
        return stuck, ok, await asyncio.wait_for(worker.run_tick(T), 20)

    with structlog.testing.capture_logs() as caps:
        stuck, ok, tick = _go(world, go)
    assert tick == {a: Counter(retry=1), b: Counter(done=1)}
    assert started == [a] and world.runs[4] == 1
    row = _row(world, stuck)
    assert row.state == "queued" and row.attempts == 1 and row.lease_until is None
    assert row.last_error == "HandlerTimeout: the handler timed out after 0.2 s"
    assert row.run_at == T + queue.backoff(1)
    assert _row(world, ok).state == "done"
    retry = _events(caps, "jobs.attempt_failed")
    assert len(retry) == 1 and retry[0]["error"] == "HandlerTimeout"
    assert not _events(caps, "jobs.failed")


def test_r8_a_job_that_fails_for_good_logs_jobs_failed_once(world) -> None:
    async def bad(db: Any, payload: dict[str, Any]) -> None:
        raise RuntimeError("no")

    worker.register(BAD, bad)
    org = world.p.org_a
    job = _go(world, lambda: _enqueue(org, BAD, {}, max_attempts=2))

    with structlog.testing.capture_logs() as first:
        assert _go(world, lambda: _ticks(T)) == [{org: Counter(retry=1)}]
    assert not _events(first, "jobs.failed")
    retry = _events(first, "jobs.attempt_failed")
    assert len(retry) == 1 and retry[0]["log_level"] == "warning"
    assert retry[0]["job_id"] == job and retry[0]["attempt"] == 1

    with structlog.testing.capture_logs() as last:
        assert _go(world, lambda: _ticks(T + queue.backoff(1))) == [{org: Counter(failed=1)}]
    failed = _events(last, "jobs.failed")
    assert len(failed) == 1 and not _events(last, "jobs.attempt_failed")
    assert failed[0]["log_level"] == "error"
    assert (failed[0]["job_id"], failed[0]["kind"], failed[0]["attempts"],
            failed[0]["org"]) == (job, BAD, 2, org)
    assert _row(world, job).state == "failed"


def test_a_tick_that_ran_a_job_logs_one_summary_line(monkeypatch) -> None:
    with structlog.testing.capture_logs() as caps:
        worker._log_tick({})
        worker._log_tick({"a": Counter(done=2, retry=1), "b": Counter(done=1, failed=1)})
    lines = _events(caps, "jobs.tick")
    assert len(lines) == 1 and lines[0]["log_level"] == "info"
    assert {k: lines[0][k] for k in ("orgs", "done", "retry", "failed")} == {
        "orgs": 2, "done": 3, "retry": 1, "failed": 1}


def test_r8_the_worker_reads_the_clock_for_each_job(world) -> None:
    _register_ok(world)
    org = world.p.org_a
    # claim 1, settle 1, claim 2, settle 2, and the claim that finds no job.
    s = timedelta(seconds=1)
    instants = iter([T, T + 3 * s, T + 7 * s, T + 9 * s, T + 11 * s])

    async def go() -> tuple:
        # The claim takes the oldest run_at first, so the order is fixed.
        first = await _enqueue(org, OK, {"n": 1}, run_at=T - timedelta(seconds=1))
        second = await _enqueue(org, OK, {"n": 2})
        return first, second, await worker.run_org(org, clock=lambda: next(instants))

    first, second, counts = _go(world, go)
    assert counts == Counter(done=2)
    # Each settle reads the clock after its handler (review P1-1).
    assert _row(world, first).finished_at == T + timedelta(seconds=3)
    assert _row(world, second).finished_at == T + timedelta(seconds=9)


def test_r8_a_complete_that_changes_no_row_rolls_back_the_handler(world) -> None:
    """``hold`` passes, the handler writes, and then ``complete`` changes no
    row. The run raises inside its session, so no write of the handler
    commits for a job that stays ``running``."""
    async def steal(db: Any, payload: dict[str, Any]) -> None:
        await queue.enqueue(db, CHILD, {"side": "effect"}, now=T)
        await db.execute(text(
            "UPDATE jobs SET locked_by = gen_random_uuid() "
            "WHERE kind = :k AND state = 'running'"), {"k": OK})

    worker.register(OK, steal)
    org = world.p.org_b

    async def go() -> tuple:
        job = await _enqueue(org, OK, {})
        return job, await worker.run_one(org, T)

    with structlog.testing.capture_logs() as caps:
        job, outcome = _go(world, go)
    assert outcome == "lost"
    assert _count(world, CHILD) == 0
    row = _row(world, job)
    assert row.state == "running" and row.attempts == 1 and row.finished_at is None
    assert len(_events(caps, "jobs.complete_lost")) == 1


# ── BR-1b-a: the detached mode, jobs.prune and the timeout label ───────────


class _SessionCounter:
    """A ``tenant_session`` that counts the sessions open now, and the
    sessions opened in all."""

    def __init__(self) -> None:
        self.open = 0
        self.opened = 0

    def __call__(self, org: str | None = None) -> Any:
        import contextlib

        @contextlib.asynccontextmanager
        async def counted() -> Any:
            self.open += 1
            self.opened += 1
            try:
                async with tenant_session(org) as db:
                    yield db
            finally:
                self.open -= 1

        return counted()


def _checked_out() -> int:
    """The connections of the ``acb_common.db`` pool that are in use now."""
    from acb_common import db as seam

    return int(seam.get_engine().sync_engine.pool.checkedout())


def test_register_takes_a_mode_and_refuses_another() -> None:
    async def h(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        return None

    kind = f"test.br1a{TAG}.mode"
    with pytest.raises(ValueError):
        worker.register(kind, h, mode="inline")
    worker.register(kind, h, mode="detached")
    try:
        assert worker._HANDLERS[kind].mode == "detached"
    finally:
        worker.unregister(kind)
    assert kind not in worker.kinds()


def test_the_prune_kind_is_registered_at_import() -> None:
    """The worker module registers ``jobs.prune``, so the queue prunes as
    soon as ``job_queue`` is on. Off, the worker runs nothing."""
    entry = worker._HANDLERS[worker.PRUNE_KIND]
    assert worker.PRUNE_KIND == "jobs.prune"
    assert entry.mode == "session" and entry.handler is worker.prune_job


def test_r8_a_detached_handler_runs_with_no_session_open(world, monkeypatch) -> None:
    """Acceptance 1. During the provider call, no session is open and the
    pool lends no connection. A new session then settles the job."""
    sessions = _SessionCounter()
    monkeypatch.setattr(worker, "tenant_session", sessions)
    during: list[tuple[int, int]] = []
    org = world.p.org_a

    async def detached(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        assert current_tenant() == org_ == org  # run_org binds the tenant
        during.append((sessions.open, _checked_out()))  # the provider stub
        async with sessions(org_) as db:
            assert await queue.hold(db, claim)
            await queue.enqueue(db, CHILD, {"result": payload["n"]}, now=T)
            assert await queue.complete(db, claim, now=T) == 1

    worker.register(DETACHED, detached, mode="detached")

    async def go() -> tuple:
        job = await _enqueue(org, DETACHED, {"n": 5})
        return job, await worker.run_org(org, T)

    job, counts = _go(world, go)
    assert during == [(0, 0)]
    assert counts == Counter(done=1)
    row = _row(world, job)
    assert row.state == "done" and row.attempts == 1 and row.finished_at == T
    assert _count(world, CHILD) == 1


def test_r8_a_detached_handler_that_lost_its_lease_settles_nothing(world) -> None:
    """Acceptance 2. The lease passes during the provider call, and a second
    claim takes the row. The late settle of the first handler changes
    nothing."""
    org = world.p.org_b
    late = T + queue.LEASE
    out: dict[str, Any] = {}

    async def detached(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        async with tenant_session(org_) as db:  # the second worker
            out["second"] = await queue.claim(db, [DETACHED], now=late)
        async with tenant_session(org_) as db:  # the first handler comes back
            out["hold"] = await queue.hold(db, claim)
            out["complete"] = await queue.complete(db, claim, now=late)
            out["fail"] = await queue.fail(db, claim, "late", now=late)
            out["give_up"] = await queue.give_up(db, claim, "Late: gone", now=late)

    worker.register(DETACHED, detached, mode="detached")

    async def go() -> tuple:
        job = await _enqueue(org, DETACHED, {})
        return job, await worker.run_one(org, T)

    job, outcome = _go(world, go)
    second = out["second"]
    assert second is not None and second.id == job and second.attempts == 2
    assert out["hold"] is False and out["complete"] == 0 and out["fail"] is None
    assert out["give_up"] is False
    assert outcome == "lost"
    row = _row(world, job)
    assert row.state == "running" and str(row.locked_by) == second.token
    assert row.attempts == 2 and row.last_error is None and row.finished_at is None


def _intent_table(world: Any) -> None:
    from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE

    with world.p.admin_engine.begin() as c:
        c.execute(text(
            f"CREATE TABLE IF NOT EXISTS {INTENT_TABLE} ("
            "key text PRIMARY KEY, marked_at timestamptz, result text)"))
        c.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {INTENT_TABLE} TO {_APP_ROLE}"))


def test_r8_an_intent_mark_stops_a_second_provider_call(world) -> None:
    """Acceptance 3, and the pattern of ``docs/job_queue.md`` §5a. The
    handler commits the mark, calls the provider, and dies. The retry reads
    the mark and does not call the provider again. The send is then
    uncertain, so the retry gives the job up: it is ``failed``, with a named
    error and a ``jobs.failed`` line, and never a silent ``done``."""
    _intent_table(world)
    org = world.p.org_a
    provider = Counter()

    async def detached(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        key = payload["key"]
        async with tenant_session(org_) as db:
            mark = (await db.execute(text(
                f"SELECT marked_at FROM {INTENT_TABLE} WHERE key = :k"),
                {"k": key})).scalar_one_or_none()
        if mark is not None:
            # A mark with no result: the call possibly ran. Do not call again,
            # and do not report a send that nobody saw land.
            async with tenant_session(org_) as db:
                await db.execute(text(
                    f"UPDATE {INTENT_TABLE} SET result = 'unknown' WHERE key = :k"),
                    {"k": key})
                await queue.give_up(db, claim, UNCERTAIN, now=T + queue.backoff(1))
            return
        async with tenant_session(org_) as db:  # the mark commits here
            await db.execute(text(
                f"INSERT INTO {INTENT_TABLE} (key, marked_at) VALUES (:k, now())"),
                {"k": key})
        provider["calls"] += 1  # the provider stub, with no session open
        raise RuntimeError("the worker died after the provider call")

    # The handler reads its mark first, so a second try is safe to run.
    worker.register(DETACHED, detached, mode="detached", idempotent=True)
    key = f"intent-{uuid.uuid4().hex[:8]}"
    try:
        job = _go(world, lambda: _enqueue(org, DETACHED, {"key": key}, max_attempts=3))
        with structlog.testing.capture_logs() as caps:
            first, second = _go(world, lambda: _ticks(T, T + queue.backoff(1)))
        assert first == {org: Counter(retry=1)} and second == {org: Counter(failed=1)}
        assert provider["calls"] == 1
        row = _row(world, job)
        # Tries were left, and the job still failed for good.
        assert row.state == "failed" and row.attempts == 2 < row.max_attempts
        assert row.last_error == UNCERTAIN
        assert row.finished_at == T + queue.backoff(1) and row.locked_by is None
        failed = _events(caps, "jobs.failed")
        assert len(failed) == 1 and failed[0]["log_level"] == "error"
        assert (failed[0]["job_id"], failed[0]["error"], failed[0]["outcome"]) == (
            job, "UncertainSend", "given_up")
        with world.p.admin_engine.connect() as c:
            mark = c.execute(text(f"SELECT marked_at, result FROM {INTENT_TABLE} "
                                  "WHERE key = :k"), {"k": key}).one()
        assert mark.marked_at is not None and mark.result == "unknown"
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text(f"DROP TABLE IF EXISTS {INTENT_TABLE}"))


def _put(world: Any, org: str, state: str, finished: datetime | None) -> str:
    """One row of *state*, written as the admin, with *finished* as its
    ``finished_at``."""
    running = state == "running"
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO jobs (organization_id, kind, state, attempts, max_attempts, "
            "locked_by, lease_until, finished_at) VALUES (CAST(:o AS uuid), :k, :s, "
            ":a, 1, :lb, :lu, :f) RETURNING id"), {
                "o": org, "k": OLD, "s": state, "a": 1 if state != "queued" else 0,
                "lb": str(uuid.uuid4()) if running else None,
                "lu": T if running else None, "f": finished}).scalar_one())


def test_r8_prune_deletes_only_old_terminal_rows_of_the_bound_org(
    world, app_engine, monkeypatch,  # noqa: F811
) -> None:
    """Acceptance 4. The prune job runs as the app role under FORCE RLS. It
    deletes the old ``done`` and ``failed`` rows of org A, in batches, and
    nothing else."""
    from tests.unit.test_email_keep_separate import _assert_non_priv

    _assert_non_priv(app_engine)
    monkeypatch.setattr(worker, "_now", lambda: T)
    monkeypatch.setattr(queue, "PRUNE_BATCH", 2)
    a, b = world.p.org_a, world.p.org_b
    old, older = T - timedelta(days=31), T - timedelta(days=60)
    gone = [_put(world, a, "done", old) for _ in range(3)] + [_put(world, a, "failed", older)]
    kept = {
        "recent": _put(world, a, "done", T - timedelta(days=29)),
        "queued": _put(world, a, "queued", older),
        "running": _put(world, a, "running", older),
        "org_b": _put(world, b, "done", older),
    }
    hooks: list[tuple[str, Any]] = []

    async def hook(db: Any, today: Any) -> None:
        bound = (await db.execute(text(
            "SELECT current_setting('app.tenant_id', true)"))).scalar_one()
        hooks.append((bound, today))

    worker.prune_hooks.append(hook)
    worker.register(worker.PRUNE_KIND, worker.prune_job)
    with structlog.testing.capture_logs() as caps:
        counts = _go(world, lambda: worker.run_org(a, T))
    assert counts == Counter(done=1)
    for job in gone:
        assert _row(world, job) is None, job
    for name, job in kept.items():
        assert _row(world, job) is not None, name
    assert hooks == [(a, T.date())]
    pruned = _events(caps, "jobs.pruned")
    assert len(pruned) == 1 and pruned[0]["deleted"] == 4


def test_r8_run_org_seeds_one_prune_job_for_each_org_and_day(world, monkeypatch) -> None:
    """Acceptance 5. The first tick of a UTC day seeds the job. A later tick
    of that day costs no statement, because the cache answers it."""
    monkeypatch.setattr(worker, "_now", lambda: T)
    sessions = _SessionCounter()
    monkeypatch.setattr(worker, "tenant_session", sessions)
    seeds = Counter()
    real_enqueue = queue.enqueue

    async def spy(db: Any, kind: str, *args: Any, **kw: Any) -> queue.Enqueued:
        seeds[(kind, kw.get("dedupe_key"))] += 1
        return await real_enqueue(db, kind, *args, **kw)

    monkeypatch.setattr(queue, "enqueue", spy)
    worker.register(worker.PRUNE_KIND, worker.prune_job)
    a, b = world.p.org_a, world.p.org_b
    day1, day2 = T.date().isoformat(), (T + timedelta(days=1)).date().isoformat()

    def prune_rows(org: str) -> list[Any]:
        with world.p.admin_engine.connect() as c:
            return c.execute(text(
                "SELECT dedupe_key, state, max_attempts FROM jobs WHERE kind = :k "
                "AND organization_id = CAST(:o AS uuid) ORDER BY dedupe_key"),
                {"k": worker.PRUNE_KIND, "o": org}).fetchall()

    first = _go(world, lambda: worker.run_org(a, T))
    assert first == Counter(done=1)
    assert seeds == Counter({(worker.PRUNE_KIND, f"prune:{day1}"): 1})
    opened = sessions.opened
    later = _go(world, lambda: worker.run_org(a, T + timedelta(hours=14)))
    assert later == Counter()
    # The cost of a later tick: the claim session only, and no seed.
    assert sessions.opened - opened == 1
    assert sum(seeds.values()) == 1
    assert [(r.dedupe_key, r.state, r.max_attempts) for r in prune_rows(a)] == [
        (f"prune:{day1}", "done", 3)]

    next_day = _go(world, lambda: worker.run_org(a, T + timedelta(days=1)))
    assert next_day == Counter(done=1)
    assert seeds[(worker.PRUNE_KIND, f"prune:{day2}")] == 1
    assert [r.dedupe_key for r in prune_rows(a)] == [f"prune:{day1}", f"prune:{day2}"]

    # Two workers with two caches: the dedupe key keeps one live job.
    async def two_seeds() -> None:
        await worker._seed_prune(b, T)
        worker._PRUNE_SEEDED.clear()
        await worker._seed_prune(b, T)

    _go(world, two_seeds)
    assert seeds[(worker.PRUNE_KIND, f"prune:{day1}")] == 3
    assert [(r.dedupe_key, r.state) for r in prune_rows(b)] == [(f"prune:{day1}", "queued")]


def test_r8_no_prune_job_is_seeded_with_no_prune_handler(world) -> None:
    _register_ok(world)
    a = world.p.org_a
    assert _go(world, lambda: _ticks(T, T + timedelta(days=1))) == [{}, {}]
    with world.p.admin_engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM jobs WHERE kind = :k AND organization_id "
                           "= CAST(:o AS uuid)"), {"k": worker.PRUNE_KIND, "o": a}).scalar_one()
    assert n == 0 and worker._PRUNE_SEEDED == {}


def test_r8_a_handler_that_hangs_in_the_database_fails_as_a_timeout(
    world, monkeypatch,
) -> None:
    """Acceptance 6. The limit cancels a query, and the session then breaks.
    The attempt still fails as ``HandlerTimeout``, and the next org runs."""
    monkeypatch.setattr(worker, "HANDLER_TIMEOUT", 0.3)

    async def sleepy(db: Any, payload: dict[str, Any]) -> None:
        await db.execute(text("SELECT pg_sleep(5)"))

    worker.register(HUNG, sleepy)
    _register_ok(world)
    a, b = world.p.org_a, world.p.org_b

    async def go() -> tuple:
        stuck = await _enqueue(a, HUNG, {}, max_attempts=2)
        await _enqueue(b, OK, {"n": 6})
        return stuck, await asyncio.wait_for(worker.run_tick(T), 20)

    with structlog.testing.capture_logs() as caps:
        stuck, tick = _go(world, go)
    assert tick == {a: Counter(retry=1), b: Counter(done=1)}
    row = _row(world, stuck)
    assert row.state == "queued" and row.attempts == 1
    assert row.last_error.startswith("HandlerTimeout"), row.last_error
    retry = _events(caps, "jobs.attempt_failed")
    assert len(retry) == 1 and retry[0]["error"] == "HandlerTimeout"
    assert world.runs[6] == 1


def test_r8_a_detached_handler_that_hangs_times_out(world, monkeypatch) -> None:
    """Acceptance 7. The limit cancels a detached handler too."""
    monkeypatch.setattr(worker, "HANDLER_TIMEOUT", 0.2)
    sessions = _SessionCounter()
    monkeypatch.setattr(worker, "tenant_session", sessions)

    async def hung(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        await asyncio.Event().wait()  # the provider never answers

    worker.register(DETACHED, hung, mode="detached")
    org = world.p.org_b

    async def go() -> tuple:
        job = await _enqueue(org, DETACHED, {}, max_attempts=2)
        return job, await asyncio.wait_for(worker.run_org(org, T), 20)

    job, counts = _go(world, go)
    assert counts == Counter(retry=1)
    row = _row(world, job)
    assert row.state == "queued" and row.lease_until is None
    assert row.last_error == "HandlerTimeout: the handler timed out after 0.2 s"
    assert sessions.open == 0


def test_r8_a_detached_handler_must_settle_its_job(world) -> None:
    """A detached handler that returns with the row still ``running`` fails
    the attempt. One that raises fails it in a new session."""
    org = world.p.org_a

    async def forgets(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        if payload["raise"]:
            raise ValueError("provider refused")

    worker.register(DETACHED, forgets, mode="detached")

    async def go() -> tuple:
        quiet = await _enqueue(org, DETACHED, {"raise": False}, max_attempts=1,
                               run_at=T - timedelta(seconds=1))
        loud = await _enqueue(org, DETACHED, {"raise": True}, max_attempts=2)
        return quiet, loud, await worker.run_org(org, T)

    quiet, loud, counts = _go(world, go)
    assert counts == Counter(failed=1, retry=1)
    q = _row(world, quiet)
    assert q.state == "failed" and q.last_error == (
        "Unsettled: the handler returned without settling")
    loud_row = _row(world, loud)
    assert loud_row.state == "queued" and loud_row.last_error == "ValueError: provider refused"


# ── BR-1b-a review round 1 ─────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["session", "detached"])
def test_r8_a_slow_try_takes_its_backoff_from_its_end(world, mode) -> None:
    """Review P1-1. A try runs for 240 s on the clock and fails. The retry
    time counts from the end of the try, so the same batch does not claim
    the job again at once, and a tick inside the backoff claims nothing."""
    org = world.p.org_a
    t = [T]
    slow = timedelta(seconds=240)

    def step() -> None:
        t[0] = t[0] + slow  # the slow provider

    if mode == "session":
        async def handler(db: Any, payload: dict[str, Any]) -> None:
            step()
            raise RuntimeError("slow and wrong")

        worker.register(SLOW, handler)
    else:
        async def handler(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
            step()
            raise RuntimeError("slow and wrong")

        worker.register(SLOW, handler, mode="detached", idempotent=True)

    async def go() -> tuple:
        job = await _enqueue(org, SLOW, {}, max_attempts=3)
        return job, await worker.run_org(org, clock=lambda: t[0])

    job, counts = _go(world, go)
    end = T + slow
    assert counts == Counter(retry=1)
    row = _row(world, job)
    assert row.state == "queued" and row.attempts == 1
    assert row.run_at == end + queue.backoff(1)
    # A tick inside the backoff claims nothing. The tick at its end does.
    inside = _go(world, lambda: worker.run_org(org, end + queue.backoff(1) - timedelta(seconds=1)))
    assert inside == Counter() and _row(world, job).attempts == 1
    t[0] = end + queue.backoff(1)
    assert _go(world, lambda: worker.run_org(org, clock=lambda: t[0])) == Counter(retry=1)
    assert _row(world, job).attempts == 2


@pytest.mark.parametrize("idempotent", [False, True])
def test_r8_a_detached_kind_that_is_not_idempotent_gets_one_try(world, idempotent) -> None:
    """Review P2-a. With ``idempotent=False`` (the default), the claim of try
    2 fails the job for good, and the handler does not run again. With
    ``idempotent=True``, try 2 runs."""
    org = world.p.org_b
    calls = Counter()

    async def handler(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        calls["n"] += 1
        raise RuntimeError("the provider broke")

    if idempotent:
        worker.register(DETACHED, handler, mode="detached", idempotent=True)
    else:
        worker.register(DETACHED, handler, mode="detached")
    assert worker._HANDLERS[DETACHED].idempotent is idempotent
    job = _go(world, lambda: _enqueue(org, DETACHED, {}, max_attempts=3))
    with structlog.testing.capture_logs() as caps:
        first, second = _go(world, lambda: _ticks(T, T + queue.backoff(1)))
    assert first == {org: Counter(retry=1)}
    row = _row(world, job)
    if idempotent:
        assert second == {org: Counter(retry=1)} and calls["n"] == 2
        assert row.state == "queued" and row.attempts == 2
        assert not _events(caps, "jobs.failed")
    else:
        assert second == {org: Counter(failed=1)} and calls["n"] == 1
        assert row.state == "failed" and row.attempts == 2
        assert row.last_error == worker.NOT_IDEMPOTENT == "not idempotent: refusing a second try"
        failed = _events(caps, "jobs.failed")
        assert len(failed) == 1 and failed[0]["outcome"] == "given_up"


def test_register_refuses_an_idempotent_flag_that_is_not_a_bool() -> None:
    async def h(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        return None

    with pytest.raises(ValueError):
        worker.register(f"test.br1a{TAG}.flag", h, mode="detached", idempotent="yes")


def test_r8_one_prune_run_takes_a_capped_number_of_rows(world, monkeypatch) -> None:
    """Review P2-c. One run deletes at most ``PRUNE_MAX_BATCHES`` batches,
    and the next run takes the rest."""
    monkeypatch.setattr(queue, "PRUNE_BATCH", 2)
    monkeypatch.setattr(queue, "PRUNE_MAX_BATCHES", 2)
    a = world.p.org_a
    rows = [_put(world, a, "done", T - timedelta(days=40)) for _ in range(5)]

    async def run() -> int:
        async with tenant_session(a) as db:
            return await queue.prune(db, now=T)

    assert [_go(world, run) for _ in range(3)] == [4, 1, 0]
    assert all(_row(world, r) is None for r in rows)


class _SyncDb:
    """A sync connection as the async ``db`` of :func:`queue.prune`."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def execute(self, *args: Any) -> Any:
        return self.conn.execute(*args)


def test_r8_a_prune_on_a_session_that_bypasses_rls_takes_the_bound_org_only(world) -> None:
    """Review P2-d. The admin role bypasses RLS. With a tenant bound, the
    prune still deletes the rows of that org only. With no tenant bound, it
    deletes nothing."""
    a, b = world.p.org_a, world.p.org_b
    old = T - timedelta(days=40)
    row_a, row_b = _put(world, a, "done", old), _put(world, b, "done", old)
    with world.p.admin_engine.begin() as c:
        assert asyncio.run(queue.prune(_SyncDb(c), now=T)) == 0
    assert _row(world, row_a) is not None and _row(world, row_b) is not None
    with world.p.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": a})
        assert asyncio.run(queue.prune(_SyncDb(c), now=T)) >= 1
    assert _row(world, row_a) is None
    assert _row(world, row_b) is not None
