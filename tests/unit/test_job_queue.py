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
    worker._stopping.clear()
    yield ns
    worker._stopping.clear()
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM jobs WHERE kind LIKE :k"), {"k": f"test.br1a{TAG}.%"})


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
    # No statement names an org: RLS binds each one.
    for name in dir(queue):
        if name.endswith("_SQL"):
            assert "organization_id =" not in getattr(queue, name), name
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
    instants = iter([T, T + timedelta(seconds=7), T + timedelta(seconds=9)])

    async def go() -> tuple:
        # The claim takes the oldest run_at first, so the order is fixed.
        first = await _enqueue(org, OK, {"n": 1}, run_at=T - timedelta(seconds=1))
        second = await _enqueue(org, OK, {"n": 2})
        return first, second, await worker.run_org(org, clock=lambda: next(instants))

    first, second, counts = _go(world, go)
    assert counts == Counter(done=2)
    assert _row(world, first).finished_at == T
    assert _row(world, second).finished_at == T + timedelta(seconds=7)


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
