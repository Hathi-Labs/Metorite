"""WS-BRIEF AUTO-2 — the ledger of unattended runs on a real database (R8).

Reference: ``docs/job_queue.md`` §9. Code: ``gateway/jobs/unattended.py`` and
its first user, the brief send of ``gateway/reach/loop.py``.

The ledger runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Two organizations. Each call
takes a frozen ``now``. A "kill" is an exception that is not an
``Exception``: it leaves the committed row ``started`` with no end, as a
dead process does. A "restart" is a new ``unattended.RUNNER``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_unattended_ledger.py -v -rs -p no:xdist

Without the database the R8 tests SKIP, and the run reads green. A skip is
not a pass.

**Acceptance (AUTO-2, task #814).**

1. A run killed after ``begin`` and before ``finish``: the restart finds the
   slot taken and does not run the job. One ledger row.
2. A send killed after the intent mark: the restart sends zero times and
   marks ``unknown``. The fake provider counts at most 1 call.
3. Five failures of one job and error class give one open incident with
   count 5. Another error class gives a second incident. A second tenant
   sees none (RLS).
4. A fake clock: no progress for 10 minutes is ``stalled``. A run that
   calls ``progress()`` is not.
5. A Reach brief on one day gives one ledger row for that member and day.
6. With the switch off, nothing is written, and Reach behaves as before.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``unattended.begin``: add ``await db.rollback()`` after the INSERT, so
  the row never commits before the work.
  ``test_r8_1_a_run_killed_after_begin_does_not_run_again`` goes red.
* M2. Drop the RLS of ``unattended_incidents`` in migration 313 AND in
  ``generated/04_policies.sql``.
  ``test_r8_3_failures_group_into_one_incident_for_each_class`` goes red.
* M3. ``reach.loop._brief_step``: drop the ``unattended.recover`` call.
  ``test_r8_2_a_send_killed_after_the_intent_mark_is_unknown`` goes red.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import structlog

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.jobs import unattended
from gateway.reach import loop, plan
from gateway.reach import whatsapp as channel
from gateway.routes.shell import state
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS unattended_runs (" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
JOB = f"test.auto2{TAG}.job"
ME = f"auto2-{TAG}@auto2.test"

#: The frozen clock: Thursday 15 October 2026, 13:30 in Kolkata, a working day.
NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
TODAY = date(2026, 10, 15)
DAY = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)


class Killed(BaseException):
    """A kill of the process: no ``except Exception`` catches it."""


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _on(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(unattended_ledger=on))


def _restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new process: the rows of the earlier runner are orphans."""
    monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))


# ── The pure parts (no database) ───────────────────────────────────────────


def test_the_migration_holds_the_states_the_keys_and_the_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    for st in unattended.STATES:
        assert f"'{st}'" in sql
    assert "CONSTRAINT unattended_runs_slot_once UNIQUE (organization_id, job, slot)" in sql
    assert "ON unattended_incidents (organization_id, job, error_class)\n    WHERE state = 'open'" \
        in sql
    for table in ("unattended_runs", "unattended_incidents"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
    assert sql.count(
        "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)") == 2
    assert "gtd_" not in sql
    # No statement names an org: RLS binds each one.
    for name in dir(unattended):
        if name.endswith("_SQL"):
            assert "organization_id" not in getattr(unattended, name).split(
                "ON CONFLICT")[0], name


def test_the_ledger_is_dark_by_default(monkeypatch) -> None:
    from acb_common.env_guard import PLATFORM_ENV_PREFIXES
    from acb_common.settings import Settings

    assert Settings.model_fields["unattended_ledger"].default is False
    assert "UNATTENDED_LEDGER" in PLATFORM_ENV_PREFIXES
    _on(monkeypatch, False)
    assert unattended.ledger_enabled() is False

    async def go() -> tuple[Any, ...]:
        # No session is opened: an unbound engine would raise.
        run = await unattended.begin("no-org", JOB, "slot", now=NOW)
        return (run, await unattended.finish("no-org", run, state="failed", now=NOW),
                await unattended.record_failure("no-org", JOB, "Boom", now=NOW),
                await unattended.recover("no-org", JOB, "slot", now=NOW))

    run, finished, incident, recovered = asyncio.run(go())
    assert run.id is None and run.fresh is True
    assert (finished, incident, recovered) == (False, None, False)


def test_a_job_name_and_a_slot_are_checked() -> None:
    with pytest.raises(unattended.LedgerError):
        unattended.check_job("Reach Brief")
    with pytest.raises(unattended.LedgerError):
        unattended.check_slot("x" * (unattended.SLOT_CHARS + 1))
    assert unattended.error_class(TimeoutError()) == "TimeoutError"
    assert unattended.error_class("send_unsure") == "send_unsure"
    # The text of a failure never reaches a row.
    assert unattended.error_class("refused: +91 98000 00001") == "Error"


class FakeClock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at

    def move(self, **kw: float) -> None:
        self.at += timedelta(**kw)


def test_4_no_progress_for_10_minutes_is_stalled() -> None:
    clock = FakeClock()
    guard = unattended.StallGuard(clock)
    clock.move(minutes=9, seconds=59)
    assert guard.stalled() is False
    clock.move(seconds=1)
    assert guard.stalled() is True


def test_4_a_run_that_calls_progress_is_not_stalled() -> None:
    clock = FakeClock()
    guard = unattended.StallGuard(clock)
    for _ in range(6):
        clock.move(minutes=5)
        guard.progress()
        assert guard.stalled() is False
    assert clock.at - NOW == timedelta(minutes=30)


@pytest.mark.parametrize(("got", "want"), [
    ("sent", ("ok", None)),
    ("empty", ("ok", None)),
    ("duplicate", ("ok", None)),
    ("skipped:no_link", ("ok", None)),
    ("failed:send_unsure", ("unknown", "send_unsure")),
    ("failed:send_retry", ("failed", "send_retry")),
    ("failed:send_refused", ("failed", "send_refused")),
])
def test_the_ledger_state_of_a_brief_outcome(got: str, want: tuple[str, str | None]) -> None:
    assert loop.brief_outcome(got) == want


def test_a_failed_member_pass_counts_in_an_incident(monkeypatch) -> None:
    seen: list[tuple[str, str, object]] = []

    async def record(org, job, error, **kw):
        seen.append((org, job, error))

    async def boom(org, member, now):
        raise RuntimeError("x")

    async def boom_record(org, job, error, **kw):
        raise OSError("the database is down")

    monkeypatch.setattr(unattended, "record_failure", record)
    monkeypatch.setattr(loop, "member_pass", boom)
    assert asyncio.run(loop._one("org-1", ME, NOW)) == "failed"
    assert [(o, j, type(e).__name__) for o, j, e in seen] == [
        ("org-1", loop.MEMBER_JOB, "RuntimeError")]
    # A failed record never stops the loop.
    monkeypatch.setattr(unattended, "record_failure", boom_record)
    assert asyncio.run(loop._one("org-1", ME, NOW)) == "failed"


# ── The R8 world ───────────────────────────────────────────────────────────


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """The ledger on, a fake WhatsApp provider, and a clean ledger."""
    p = promoted
    _on(monkeypatch)
    ns = SimpleNamespace(p=p, calls=Counter(), mode="sent")

    async def deliver(org, member, *, kind, body, outbox_id, template=None,
                      template_params=None, reach=None, record=None):
        ns.calls[(org, kind)] += 1
        if ns.mode == "kill":
            raise Killed
        if ns.mode == "unsure":
            return channel.Delivery("failed", reason="send_unsure")
        return channel.Delivery("sent", "free_text", provider_message_id="wamid.1")

    monkeypatch.setattr(channel, "deliver", deliver)
    _wipe(p)
    yield ns
    _wipe(p)


def _wipe(p: Any) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM unattended_runs"))
        c.execute(text("DELETE FROM unattended_incidents"))
        c.execute(text("DELETE FROM reach_signal_log WHERE member_email = :m"), {"m": ME})
        c.execute(text("DELETE FROM reach_outbox WHERE member_email = :m"), {"m": ME})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _runs(world: Any, job: str | None = None) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, job, slot, state, error_class, "
            "incident_id::text AS incident_id, finished_at FROM unattended_runs "
            "WHERE (CAST(:j AS text) IS NULL OR job = :j) ORDER BY started_at"), {"j": job})
        return [dict(r._mapping) for r in got]


def _incidents(world: Any) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, job, error_class, state, count, "
            "first_seen, last_seen FROM unattended_incidents ORDER BY first_seen, error_class"))
        return [dict(r._mapping) for r in got]


def _outbox(world: Any) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT kind, state, dedupe_key FROM reach_outbox WHERE member_email = :m"),
            {"m": ME})
        return [dict(r._mapping) for r in got]


def _card() -> dict[str, Any]:
    return {"signal_id": "tasks:1", "app": "tasks", "kind": "overdue", "group": "needs_you",
            "score": 50, "title": "Ship the deck", "facts": {}, "fingerprint": "fp000001",
            "state": "new", "at": None, "why": "W"}


def _snap() -> loop.Snapshot:
    return loop.Snapshot([_card()], NOW, DAY, True, True)


async def _brief(org: str) -> tuple[str | None, str]:
    return await loop._brief_step(org, ME, _snap(), ("919800000001", True))


# ── 1. A run killed after begin ────────────────────────────────────────────


@_DB_GATE
def test_r8_1_a_run_killed_after_begin_does_not_run_again(world, monkeypatch) -> None:
    org = world.p.org_a
    ran = Counter()

    async def killed(guard: Any) -> None:
        ran["first"] += 1
        raise Killed

    async def work(guard: Any) -> str:
        ran["again"] += 1
        return "done"

    async def first() -> None:
        await unattended.once(org, JOB, "2026-10-15", killed, clock=lambda: NOW)

    with pytest.raises(Killed):
        _go(world, first)
    rows = _runs(world, JOB)
    assert [(r["org"], r["state"]) for r in rows] == [(org, "started")]

    _restart(monkeypatch)
    run, out = _go(world, lambda: unattended.once(org, JOB, "2026-10-15", work,
                                                  clock=lambda: NOW + timedelta(minutes=1)))
    assert (run.fresh, out, ran["again"]) == (False, None, 0)
    rows = _runs(world, JOB)
    assert len(rows) == 1
    assert (rows[0]["state"], rows[0]["error_class"]) == ("unknown", "OutcomeUnknown")
    assert rows[0]["incident_id"] is not None


@_DB_GATE
def test_r8_a_finished_slot_does_not_run_again(world) -> None:
    org = world.p.org_a
    ran = Counter()

    async def work(guard: Any) -> int:
        ran["n"] += 1
        return ran["n"]

    async def twice() -> list[Any]:
        return [await unattended.once(org, JOB, "s1", work, clock=lambda: NOW)
                for _ in range(2)]

    (first, out1), (second, out2) = _go(world, twice)
    assert (first.fresh, out1, second.fresh, second.state, out2) == (True, 1, False, "ok", None)
    assert ran["n"] == 1 and [r["state"] for r in _runs(world, JOB)] == ["ok"]


@_DB_GATE
def test_r8_a_live_run_of_this_runner_is_not_an_orphan_until_10_minutes(world) -> None:
    org = world.p.org_a

    async def go() -> list[str]:
        await unattended.begin(org, JOB, "s1", now=NOW)
        again = await unattended.begin(org, JOB, "s1", now=NOW + timedelta(minutes=9))
        late = await unattended.begin(org, JOB, "s1", now=NOW + timedelta(minutes=11))
        return [again.state, late.state]

    assert _go(world, go) == ["started", "unknown"]


@_DB_GATE
def test_r8_a_failing_run_ends_failed_and_raises(world) -> None:
    org = world.p.org_a

    async def bad(guard: Any) -> None:
        raise ValueError("no")

    with pytest.raises(ValueError):
        _go(world, lambda: unattended.once(org, JOB, "s1", bad, clock=lambda: NOW))
    rows = _runs(world, JOB)
    assert [(r["state"], r["error_class"]) for r in rows] == [("failed", "ValueError")]
    assert rows[0]["finished_at"] is not None


# ── 2. A send killed after the intent mark ─────────────────────────────────


@_DB_GATE
def test_r8_2_a_send_killed_after_the_intent_mark_is_unknown(world, monkeypatch) -> None:
    org = world.p.org_a
    world.mode = "kill"
    with pytest.raises(Killed):
        _go(world, lambda: _brief(org))
    # The outbox row (the intent mark) and the ledger row both committed.
    assert [(r["kind"], r["state"]) for r in _outbox(world)] == [("brief", "planned")]
    assert [r["state"] for r in _runs(world, loop.BRIEF_JOB)] == ["started"]

    _restart(monkeypatch)
    world.mode = "sent"
    assert _go(world, lambda: _brief(org)) == ("brief:unknown", "none")
    assert _go(world, lambda: _brief(org))[0] is None  # and nothing on the next pass
    assert sum(world.calls.values()) == 1
    rows = _runs(world, loop.BRIEF_JOB)
    assert [(r["slot"], r["state"]) for r in rows] == [(plan.brief_key(ME, TODAY), "unknown")]
    assert [(i["job"], i["error_class"], i["count"]) for i in _incidents(world)] == [
        (loop.BRIEF_JOB, "OutcomeUnknown", 1)]


@_DB_GATE
def test_r8_an_unsure_send_is_unknown_and_never_retried(world) -> None:
    org = world.p.org_a
    world.mode = "unsure"
    got = _go(world, lambda: _brief(org))
    assert got[0] == "brief:failed:send_unsure"
    world.mode = "sent"
    for _ in range(2):
        _go(world, lambda: _brief(org))
    assert sum(world.calls.values()) == 1
    assert [(r["state"], r["error_class"]) for r in _runs(world, loop.BRIEF_JOB)] == [
        ("unknown", "send_unsure")]


# ── 3. Incidents ───────────────────────────────────────────────────────────


@_DB_GATE
def test_r8_3_failures_group_into_one_incident_for_each_class(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> list[Any]:
        out = [await unattended.record_failure(a, JOB, "TimeoutError",
                                               now=NOW + timedelta(minutes=i))
               for i in range(5)]
        out.append(await unattended.record_failure(a, JOB, KeyError(), now=NOW))
        return out

    with structlog.testing.capture_logs() as caps:
        got = _go(world, go)
    assert [i.opened for i in got] == [True, False, False, False, False, True]
    assert [i.count for i in got[:5]] == [1, 2, 3, 4, 5]
    opened = [c for c in caps if c.get("event") == "unattended.incident_opened"]
    assert [(c["log_level"], c["error_class"]) for c in opened] == [
        ("error", "TimeoutError"), ("error", "KeyError")]
    rows = _incidents(world)
    assert [(r["org"], r["error_class"], r["state"], r["count"]) for r in rows] == [
        (a, "KeyError", "open", 1), (a, "TimeoutError", "open", 5)]
    timeout = next(r for r in rows if r["error_class"] == "TimeoutError")
    assert timeout["last_seen"] - timeout["first_seen"] == timedelta(minutes=4)

    async def as_b() -> tuple[int, int]:
        async with tenant_session(b) as db:
            seen = (await db.execute(text(
                "SELECT count(*) FROM unattended_incidents"))).scalar_one()
        mine = await unattended.record_failure(b, JOB, "TimeoutError", now=NOW)
        return int(seen), mine.count

    # Org B sees none of org A, and its own first failure opens its own incident.
    assert _go(world, as_b) == (0, 1)
    assert sorted((r["org"] == b, r["count"]) for r in _incidents(world)
                  if r["error_class"] == "TimeoutError") == [(False, 5), (True, 1)]


@_DB_GATE
def test_r8_org_b_cannot_read_or_end_a_run_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> tuple[Any, ...]:
        run = await unattended.begin(a, JOB, "s1", now=NOW)
        async with tenant_session(b) as db:
            seen = (await db.execute(text("SELECT count(*) FROM unattended_runs"))).scalar_one()
        # The same slot in org B is its own run.
        theirs = await unattended.begin(b, JOB, "s1", now=NOW)
        ended = await unattended.finish(b, run, state="ok", now=NOW)
        return int(seen), theirs.fresh, ended

    assert _go(world, go) == (0, True, False)
    assert sorted((r["org"] == a, r["state"]) for r in _runs(world, JOB)) == [
        (False, "started"), (True, "started")]


# ── 4. The stall guard on the ledger ───────────────────────────────────────


@_DB_GATE
def test_r8_4_a_run_with_no_progress_is_stopped_and_stalled(world) -> None:
    org = world.p.org_a
    clock = FakeClock()
    cancelled = Counter()

    async def hang(guard: Any) -> None:
        clock.move(minutes=10)  # time passes, and the work never moves
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled["n"] += 1
            raise

    async def busy(guard: Any) -> str:
        for _ in range(30):
            clock.move(minutes=1)
            guard.progress()
            await asyncio.sleep(0.005)
        return "done"

    async def go() -> list[Any]:
        first = await unattended.once(org, JOB, "hang", hang, clock=clock, poll=0.01)
        second = await unattended.once(org, JOB, "busy", busy, clock=clock, poll=0.01)
        return [first[1], second[1]]

    assert _go(world, go) == [None, "done"]
    assert cancelled["n"] == 1
    got = {r["slot"]: (r["state"], r["error_class"]) for r in _runs(world, JOB)}
    assert got == {"hang": ("stalled", "Stalled"), "busy": ("ok", None)}
    assert [(i["error_class"], i["count"]) for i in _incidents(world)] == [("Stalled", 1)]


# ── 5 and 6. The Reach brief ───────────────────────────────────────────────


@_DB_GATE
def test_r8_5_a_reach_brief_on_one_day_gives_one_ledger_row(world) -> None:
    org = world.p.org_a
    got = [_go(world, lambda: _brief(org)) for _ in range(3)]
    assert got[0] == ("brief:sent", "free_text")
    assert [g[0] for g in got[1:]] == [None, None]
    assert world.calls == Counter({(org, "brief"): 1})
    rows = _runs(world, loop.BRIEF_JOB)
    assert [(r["org"], r["slot"], r["state"]) for r in rows] == [
        (org, plan.brief_key(ME, TODAY), "ok")]
    assert [(r["kind"], r["state"], r["dedupe_key"]) for r in _outbox(world)] == [
        ("brief", "sent", plan.brief_key(ME, TODAY))]


@_DB_GATE
def test_r8_6_with_the_switch_off_reach_writes_no_ledger_row(world, monkeypatch) -> None:
    _on(monkeypatch, False)
    org = world.p.org_a
    assert _go(world, lambda: _brief(org)) == ("brief:sent", "free_text")
    assert _go(world, lambda: _brief(org))[0] is None
    assert sum(world.calls.values()) == 1
    assert [(r["kind"], r["state"]) for r in _outbox(world)] == [("brief", "sent")]
    assert _runs(world) == [] and _incidents(world) == []
