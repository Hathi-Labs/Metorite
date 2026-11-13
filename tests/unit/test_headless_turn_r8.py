"""WS-AUTO AUTO-8a, AUTO-8b-1 and AUTO-8b-2a: the headless turn on a real
database (R8).

Spec: ``project-docs/specs/proactive_agent.md`` §4.4 and §11. Code:
``apps/services/gateway/gateway/jobs/headless_turn.py``. The hermetic half is
``test_headless_turn.py``.

The suite runs the REAL worker (``worker.run_org``), the REAL queue, the REAL
goal store, the REAL ledger and the REAL departed check, on a phase-4
promoted scratch database, as a role that is NOT a superuser, NOT the owner
and has NO BYPASSRLS. So FORCE RLS binds each statement. Two orgs and two
members, and a third member who left. The runner and the meter are fakes: no
test calls a model.

**The R8 cases of the audit.**

* Two orgs, two members: the candidate query and the goal read return the
  rows of org A only.
* ``cap_reached`` writes the goal of A only. The goal of org B stays active
  at version 1. A second delivery reports nothing.
* A stale goal version (``StaleGoal``) ends the turn with no second round.
* A job for a member who left makes no model call and ends ok.
* The default meter makes no model call.
* AUTO-8b-1: a meter that runs out of time after the call charges the rest
  of the budget. The goal of A blocks with ``cap_reached``, org B sees
  nothing, and the ledger row ends ``ok``. AUTO-8b-2a P2 (4): the case
  asserts an order, and the record gets the time that the turn has left.
* AUTO-8b-2a, the mark (two orgs): a goal of A that holds a mark and no
  record blocks with ``cap_reached`` at the next turn, with 0 runner calls.
  The goal of B with the same shape does not change. A failed mark write
  makes no call. A normal round clears the mark. A lost round (a cancel
  during the call, a ``RecordTimeout``) leaves the mark, and the NEXT turn
  blocks. The mutations M11 and M12 of ``test_headless_turn.py`` turn
  ``test_r8_a_lost_round_leaves_the_mark_and_the_next_turn_blocks`` and
  ``test_r8_a_normal_round_clears_the_mark`` red.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    docker exec metorite-scratch-tenant createdb -U acb auto8a_build
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/auto8a_build
    uv run pytest tests/unit/test_headless_turn_r8.py -q -p no:cacheprovider -rfEs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.jobs", reason="gateway not installed")

from acb_auth.access import mirror_identity_membership
from acb_common import get_run_context, get_settings, goal_store
from acb_common.db import tenant_session
from gateway.jobs import headless_turn as ht
from gateway.jobs import queue, unattended, worker
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_headless_turn import FakeMeter, FakeRunner, Reports

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

TAG = uuid.uuid4().hex[:8]
#: Org A: Ana, and Leo, who left. Org B: Ben.
ANA = f"ana.{TAG}@auto8a.test"
LEO = f"leo.{TAG}@auto8a.test"
BEN = f"ben.{TAG}@auto8a.test"
LIKE = f"%.{TAG}@auto8a.test"
CHECK = "r8.goals"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _seed_members(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for email, org, status in ((ANA, p.org_a, "active"), (LEO, p.org_a, "removed"),
                                   (BEN, p.org_b, "active")):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid))"),
                {"e": email, "s": status, "o": org})


def _unseed(p: Any) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM goal_records WHERE member_email LIKE :t"), {"t": LIKE})
        c.execute(text("DELETE FROM unattended_runs WHERE job = :j"), {"j": ht.KIND})
        c.execute(text("DELETE FROM unattended_incidents WHERE job = :j"), {"j": ht.KIND})
        c.execute(text("DELETE FROM jobs WHERE kind = :k"), {"k": ht.KIND})
        c.execute(text(
            "DELETE FROM org_membership m USING user_identity ui "
            "WHERE m.user_id = ui.id AND lower(ui.email) LIKE :t"), {"t": LIKE})
        c.execute(text("DELETE FROM user_identity WHERE lower(email) LIKE :t"), {"t": LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": LIKE})


@pytest.fixture(params=[False, True], ids=["ledger_off", "ledger_on"])
def world(request, promoted, monkeypatch):  # noqa: F811
    p = promoted
    _seed_members(p)
    w = SimpleNamespace(p=p, ledger=request.param, seen=[], checked=[])
    # The departed check reads the shadow identity with the cutover on, as
    # ``test_my_day_loop_r8.py`` does. Leo's shadow stays active, and his
    # ``app_user`` row says ``removed``: the status check finds him.
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")

    async def mirror() -> None:
        for email, org in ((ANA, p.org_a), (LEO, p.org_a), (BEN, p.org_b)):
            await mirror_identity_membership(email=email, org_id=org, status="active")

    _go(w, mirror)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=True))
    monkeypatch.setattr(worker, "_HANDLERS", {})
    monkeypatch.setattr(worker, "_PRUNE_SEEDED", {})
    monkeypatch.setattr(worker, "_SEEDED", {})
    monkeypatch.setattr(worker, "seed_hooks", [])
    monkeypatch.setattr(worker, "prune_hooks", [])
    worker._stopping.clear()
    assert ht.register_jobs() is True
    monkeypatch.setattr(get_settings(), "headless_turn_orgs", f"{p.org_a},{p.org_b}")
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(unattended_ledger=request.param))
    monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))

    async def check(org: str, member: str, payload: dict[str, Any]) -> bool:
        # The candidate query: no member filter and no org filter, so only
        # RLS binds it.
        async with tenant_session(org) as db:
            rows = (await db.execute(text(
                "SELECT member_email FROM goal_records WHERE member_email LIKE :t "
                "ORDER BY member_email"), {"t": LIKE})).all()
        w.checked.append((org, member, sorted(str(r[0]) for r in rows)))
        return True

    w.checks = {CHECK: check}
    w.meter = FakeMeter()
    yield w
    worker._stopping.clear()
    _unseed(p)


def _use(monkeypatch: pytest.MonkeyPatch, turn: ht.HeadlessTurn) -> None:
    monkeypatch.setattr(ht, "_DEFAULT", turn)


async def _create(org: str, member: str, **kw: Any) -> goal_store.Goal:
    args: dict[str, Any] = {"objective": "Watch the open deals.", "cost_cap_credits": 10,
                            "kind": ht.GOAL_KIND, **kw}
    async with tenant_session(org) as db:
        return await goal_store.create_goal(db, member, **args)


def _goal(world: Any, org: str, member: str, **kw: Any) -> goal_store.Goal:
    return _go(world, lambda: _create(org, member, **kw))


def _enqueue(world: Any, org: str, goal_id: str, *, max_attempts: int = 1) -> str:
    async def go() -> str:
        async with tenant_session(org) as db:
            got = await queue.enqueue(db, ht.KIND, {"goal_id": goal_id, "candidate": CHECK},
                                      max_attempts=max_attempts)
        return got.id
    return _go(world, go)


def _tick(world: Any, org: str) -> Counter[str]:
    return _go(world, lambda: worker.run_org(org))


def _row(world: Any, goal_id: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        return dict(c.execute(text(
            "SELECT * FROM goal_records WHERE id = CAST(:i AS uuid)"),
            {"i": goal_id}).mappings().one())


def _job(world: Any, job_id: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        return dict(c.execute(text(
            "SELECT * FROM jobs WHERE id = CAST(:i AS uuid)"), {"i": job_id}).mappings().one())


def _ledger(world: Any, job_id: str) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT * FROM unattended_runs WHERE job = :j AND slot = :s"),
            {"j": ht.KIND, "s": job_id}).mappings()]


def _assert_untouched(world: Any, goal: goal_store.Goal) -> None:
    row = _row(world, goal.id)
    assert (row["state"], row["version"], row["round_count"]) == ("active", 1, 0)
    assert row["cost_used_credits"] == Decimal("0")


# ── Two orgs, two members ───────────────────────────────────────────────────


def test_r8_the_turn_reads_org_a_only_and_bills_the_goal_member(world, monkeypatch) -> None:
    """The candidate query and the goal read see the rows of org A only. The
    round runs marked, as Ana, VERIFIED, under the name ``auto.headless``.
    Org B's goal does not move."""
    ana = _goal(world, world.p.org_a, ANA)
    ben = _goal(world, world.p.org_b, BEN)
    runner = FakeRunner(world.meter, cost="1")
    seen: list[Any] = []

    def _context(_n: int) -> None:
        seen.append(dict(get_run_context()))

    runner.side = _context
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert world.checked == [(world.p.org_a, ANA, [ANA])]  # Ben's row is not there
    assert [(c["member"], c["org"], c["marked"]) for c in runner.calls] == [
        (ANA, world.p.org_a, True)]
    assert [(s.get("user"), s.get("member_verified"), s.get("agent"), s.get("app"))
            for s in seen] == [(ANA, "1", ht.AGENT, ht.APP)]
    row = _row(world, ana.id)
    # Version 2 is the mark, and version 3 the round that clears it.
    assert (row["state"], row["version"], row["round_count"]) == ("active", 3, 1)
    assert row["cost_used_credits"] == Decimal("1") and row["job_id"] is None
    _assert_untouched(world, ben)
    assert _job(world, job_id)["state"] == "done"
    want = [("ok", None)] if world.ledger else []
    assert [(r["state"], r["error_class"]) for r in _ledger(world, job_id)] == want


def test_r8_a_job_of_org_a_cannot_reach_a_goal_of_org_b(world, monkeypatch) -> None:
    ben = _goal(world, world.p.org_b, BEN)
    runner = FakeRunner(world.meter)
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks))
    job_id = _enqueue(world, world.p.org_a, ben.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert runner.calls == [] and world.checked == [] and world.meter.reads == 0
    _assert_untouched(world, ben)
    assert _job(world, job_id)["state"] == "done"
    assert _ledger(world, job_id) == []


# ── The cap, once ───────────────────────────────────────────────────────────


def test_r8_cap_reached_writes_the_goal_of_a_only_and_reports_once(world, monkeypatch) -> None:
    """Acceptance 1 on real rows. The round past the cap blocks Ana's goal
    in one write. Ben's goal stays active at version 1. The same job again,
    and a new job of the same payload, call nothing and report nothing."""
    ana = _goal(world, world.p.org_a, ANA, cost_cap_credits=1)
    ben = _goal(world, world.p.org_b, BEN, cost_cap_credits=1)
    runner, reports = FakeRunner(world.meter, cost="2"), Reports()
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter, report=reports,
                                      candidates=world.checks, go_on=lambda _r: True))
    first = _enqueue(world, world.p.org_a, ana.id, max_attempts=2)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    row = _row(world, ana.id)
    assert (row["state"], row["block_code"], row["version"]) == ("blocked", "cap_reached", 3)
    assert (row["round_count"], row["cost_used_credits"]) == (1, Decimal("2"))
    assert len(runner.calls) == 1 and [g.version for g in reports.seen] == [3]
    _assert_untouched(world, ben)

    # The same job again, as after a lost lease: the worker gives it up.
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE jobs SET state = 'queued', run_at = now() - interval '1 hour', "
            "locked_by = NULL, "
            "locked_at = NULL, lease_until = NULL, finished_at = NULL "
            "WHERE id = CAST(:i AS uuid)"), {"i": first})
    assert _tick(world, world.p.org_a) == Counter(failed=1)
    assert _job(world, first)["last_error"] == worker.NOT_IDEMPOTENT
    # A new job of the same payload reads a blocked goal.
    second = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert len(runner.calls) == 1 and len(reports.seen) == 1
    assert _row(world, ana.id)["version"] == 3
    assert _job(world, second)["state"] == "done"
    assert _ledger(world, second) == []


# ── The settle in time (AUTO-8b-1) ──────────────────────────────────────────


def test_r8_a_meter_past_the_margin_blocks_the_goal_of_a_only(world, monkeypatch) -> None:
    """AUTO-8b-1 acceptance 5. Round 1 spends 1 credit. In round 2 the meter
    after the call sleeps past its part of ``SETTLE_MARGIN_S``. The round
    charges the rest of the budget (10 - 1), so Ana's goal blocks with
    ``cap_reached`` in one write. Ben's goal of org B does not move, and the
    ledger row of the turn ends ``ok``.

    AUTO-8b-2a P2 (4): the case asserts an ORDER, never a wall-clock time.
    The call of round 2 ends past the deadline of the turn's clock, so the
    meter gets one part (P2 (1)). Then the clock comes back, so the record
    on the real database gets the time that the turn has left, not 0.5 s."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 1.5)
    ana = _goal(world, world.p.org_a, ANA, cost_cap_credits=10)
    ben = _goal(world, world.p.org_b, BEN, cost_cap_credits=10)
    runner, reports = FakeRunner(world.meter, cost="1"), Reports()
    skew = [0.0]
    events: list[str] = []

    def _hang_in_round_2(n: int) -> None:
        events.append("called")
        if n == 2:
            world.meter.sleep_after = 60.0
            skew[0] = 1000.0

    async def meter(org: str, run_id: str) -> Any:
        # The share of this read is already set. The record gets the time left.
        skew[0] = 0.0
        return await world.meter(org, run_id)

    real = goal_store.record_round

    async def ordered(*a: Any, **kw: Any) -> Any:
        events.extend(("meter_cut" if world.meter.cancelled else "meter_answered", "recorded"))
        return await real(*a, **kw)

    monkeypatch.setattr(goal_store, "record_round", ordered)
    runner.side = _hang_in_round_2
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=meter, report=reports,
                                      candidates=world.checks, go_on=lambda _r: True,
                                      clock=lambda: time.monotonic() + skew[0]))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert events == ["called", "meter_answered", "recorded",
                      "called", "meter_cut", "recorded"]
    row = _row(world, ana.id)
    # Two rounds, each a mark and a record.
    assert (row["state"], row["block_code"], row["version"]) == ("blocked", "cap_reached", 5)
    assert (row["round_count"], row["cost_used_credits"]) == (2, Decimal("10"))
    assert row["job_id"] is None
    assert len(runner.calls) == 2 and world.meter.cancelled is True
    assert [g.version for g in reports.seen] == [5]
    _assert_untouched(world, ben)
    assert _job(world, job_id)["state"] == "done"
    want = [("ok", None)] if world.ledger else []
    assert [(r["state"], r["error_class"]) for r in _ledger(world, job_id)] == want


# ── A stale goal ────────────────────────────────────────────────────────────


def test_r8_a_stale_goal_ends_the_turn_with_no_second_round(world, monkeypatch) -> None:
    """Another write moves the version during the call. The round is
    recorded once on the new version, and no second round starts, though the
    rule of the next round says go on."""
    ana = _goal(world, world.p.org_a, ANA)
    calls: list[str] = []

    async def runner(message: str, *, run_id: str, org: str, member: str,
                     tools: tuple[str, ...]) -> dict[str, Any]:
        calls.append(run_id)
        world.meter.spent[run_id] = Decimal("1")
        async with tenant_session(org) as db:
            got = await goal_store.get_goal(db, member, ana.id)
            await goal_store.update_goal(db, member, ana.id, got.version, round_cap=41)
        return {"answer": "x"}

    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks, go_on=lambda _r: True))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert len(calls) == 1
    row = _row(world, ana.id)
    # 2 the mark, 3 the other write, 4 the round on the new version.
    assert (row["round_count"], row["round_cap"], row["version"]) == (1, 41, 4)
    assert row["cost_used_credits"] == Decimal("1") and row["job_id"] is None
    assert _job(world, job_id)["state"] == "done"


# ── A member who left ───────────────────────────────────────────────────────


def test_r8_a_job_for_a_member_who_left_makes_no_model_call(world, monkeypatch) -> None:
    leo = _goal(world, world.p.org_a, LEO)
    runner = FakeRunner(world.meter)
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks))
    job_id = _enqueue(world, world.p.org_a, leo.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert runner.calls == [] and world.checked == [] and world.meter.reads == 0
    _assert_untouched(world, leo)
    assert _job(world, job_id)["state"] == "done"


# ── The default meter ───────────────────────────────────────────────────────


def test_r8_the_default_meter_makes_no_model_call(world, monkeypatch) -> None:
    """With the flag on, a candidate and no cost reader: no call, the goal
    untouched, the job failed with a named reason."""
    ana = _goal(world, world.p.org_a, ANA)
    runner = FakeRunner(world.meter)
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, candidates=world.checks))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(failed=1)
    assert runner.calls == []
    _assert_untouched(world, ana)
    job = _job(world, job_id)
    assert job["state"] == "failed" and job["last_error"] == ht.NO_READER
    want = [("failed", "NoCostReader")] if world.ledger else []
    assert [(r["state"], r["error_class"]) for r in _ledger(world, job_id)] == want


# ── The mark of a call (AUTO-8b-2a) ─────────────────────────────────────────


def _set_mark(world: Any, org: str, member: str, goal_id: str, mark: str) -> None:
    """A call of an earlier run whose round has no record: the mark stays."""
    async def go() -> None:
        async with tenant_session(org) as db:
            got = await goal_store.get_goal(db, member, goal_id)
            await goal_store.update_goal(db, member, goal_id, got.version, job_id=mark)
    _go(world, go)


@pytest.mark.parametrize("whose", ["another_job", "the_same_job_id_as_the_claim"])
def test_r8_a_mark_with_no_record_blocks_the_goal_of_a_only(world, monkeypatch, whose) -> None:
    """Ana's goal holds a mark and no record, so the new turn charges the
    rest (10) in one write that clears the mark. It blocks with
    ``cap_reached``, with 0 runner calls, 0 meter reads and no candidate
    check. Ben's goal of org B has the same shape, and it does not change.

    The mark of the job id of the claim itself is the mark of an earlier try
    of the same job, so it is a lost round too."""
    other = str(uuid.uuid4())
    ana = _goal(world, world.p.org_a, ANA, cost_cap_credits=10)
    ben = _goal(world, world.p.org_b, BEN, cost_cap_credits=10, job_id=other)
    runner, reports = FakeRunner(world.meter), Reports()
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter, report=reports,
                                      candidates=world.checks, go_on=lambda _r: True))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    _set_mark(world, world.p.org_a, ANA, ana.id,
              job_id if whose != "another_job" else other)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert runner.calls == [] and world.meter.reads == 0 and world.checked == []
    row = _row(world, ana.id)
    assert (row["state"], row["block_code"], row["job_id"]) == ("blocked", "cap_reached", None)
    assert (row["round_count"], row["cost_used_credits"], row["version"]) == (
        1, Decimal("10"), 3)
    assert [g.version for g in reports.seen] == [3]
    b = _row(world, ben.id)
    assert (b["state"], b["version"], b["round_count"], str(b["job_id"])) == (
        "active", 1, 0, other)
    assert b["cost_used_credits"] == Decimal("0")
    assert _job(world, job_id)["state"] == "done"
    assert _ledger(world, job_id) == []


def test_r8_a_failed_mark_write_makes_no_call(world, monkeypatch) -> None:
    """The mark write runs its UPDATE, then fails before the commit. The
    session rolls back, so no mark stays, and NO call runs. The job gives up
    with ``MarkFailed``."""
    ana = _goal(world, world.p.org_a, ANA)
    ben = _goal(world, world.p.org_b, BEN)
    runner = FakeRunner(world.meter)
    real = goal_store.update_goal

    async def writes_then_fails(*a: Any, **kw: Any) -> Any:
        await real(*a, **kw)
        raise ConnectionError("the commit did not answer")

    monkeypatch.setattr(goal_store, "update_goal", writes_then_fails)
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks, go_on=lambda _r: True))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(failed=1)
    assert runner.calls == []
    _assert_untouched(world, ana)
    assert _row(world, ana.id)["job_id"] is None
    _assert_untouched(world, ben)
    job = _job(world, job_id)
    assert job["state"] == "failed" and job["last_error"] == ht.MARK_FAILED
    want = [("failed", "MarkFailed")] if world.ledger else []
    assert [(r["state"], r["error_class"]) for r in _ledger(world, job_id)] == want


def test_r8_a_normal_round_clears_the_mark(world, monkeypatch) -> None:
    """During the call, the row holds the job id as its mark. The record of
    the round clears it in the same write. Mutation M12 (no clear in
    ``record_round``) turns this red."""
    ana = _goal(world, world.p.org_a, ANA)
    ben = _goal(world, world.p.org_b, BEN)
    runner = FakeRunner(world.meter, cost="1")
    during: list[str] = []
    runner.side = lambda _n: during.append(str(_row(world, ana.id)["job_id"]))
    _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                      candidates=world.checks))
    job_id = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert during == [job_id]
    row = _row(world, ana.id)
    assert (row["job_id"], row["round_count"], row["version"], row["state"]) == (
        None, 1, 3, "active")
    assert row["cost_used_credits"] == Decimal("1")
    _assert_untouched(world, ben)
    assert _row(world, ben.id)["job_id"] is None


@pytest.mark.parametrize("how", ["cancel_during_the_call", "record_timeout"])
def test_r8_a_lost_round_leaves_the_mark_and_the_next_turn_blocks(
        world, monkeypatch, how) -> None:
    """The P1 example on real rows: round 1 spends 8 of 10 credits, and its
    cost does not reach the goal row. A stop of the worker cancels the tick
    during the call, or the record does not end in time. The mark stays.
    The NEXT turn charges the rest (10), blocks with ``cap_reached`` and
    makes no call. Mutation M11 (no mark write) turns this red."""
    ana = _goal(world, world.p.org_a, ANA, cost_cap_credits=10)
    ben = _goal(world, world.p.org_b, BEN, cost_cap_credits=10)
    runner = FakeRunner(world.meter, cost="8")
    if how == "cancel_during_the_call":
        runner.sleep = 60.0
        _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                          candidates=world.checks, go_on=lambda _r: True))
        first = _enqueue(world, world.p.org_a, ana.id)

        async def stop_during_the_call() -> None:
            called = asyncio.Event()
            runner.side = lambda _n: called.set()
            tick = asyncio.get_running_loop().create_task(worker.run_org(world.p.org_a))
            await asyncio.wait_for(called.wait(), timeout=60)
            tick.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tick
            # The rounds run in the task of the stall guard. Let its cancel land.
            for _ in range(3000):
                if runner.cancelled:
                    return
                await asyncio.sleep(0.01)

        _go(world, stop_during_the_call)
        assert runner.cancelled is True
    else:
        monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 1.5)
        real = goal_store.record_round

        async def hangs(*_a: Any, **_kw: Any) -> Any:
            await asyncio.sleep(60)

        monkeypatch.setattr(goal_store, "record_round", hangs)
        skew = [0.0]
        # The call ends past the deadline, so the record gets one part.
        runner.side = lambda _n: skew.__setitem__(0, 1000.0)
        _use(monkeypatch, ht.HeadlessTurn(runner=runner, meter=world.meter,
                                          candidates=world.checks, go_on=lambda _r: True,
                                          clock=lambda: time.monotonic() + skew[0]))
        first = _enqueue(world, world.p.org_a, ana.id)
        assert _tick(world, world.p.org_a) == Counter(failed=1)
        assert _job(world, first)["last_error"] == ht.TURN_FAILED
        monkeypatch.setattr(goal_store, "record_round", real)
    assert len(runner.calls) == 1
    lost = _row(world, ana.id)
    assert (str(lost["job_id"]), lost["round_count"], lost["state"], lost["version"]) == (
        first, 0, "active", 2)
    assert lost["cost_used_credits"] == Decimal("0")
    # The next turn of the goal: a new job.
    following, reports = FakeRunner(world.meter, cost="1"), Reports()
    _use(monkeypatch, ht.HeadlessTurn(runner=following, meter=world.meter, report=reports,
                                      candidates=world.checks, go_on=lambda _r: True))
    second = _enqueue(world, world.p.org_a, ana.id)
    assert _tick(world, world.p.org_a) == Counter(done=1)
    assert following.calls == []
    row = _row(world, ana.id)
    assert (row["state"], row["block_code"], row["job_id"]) == ("blocked", "cap_reached", None)
    assert (row["cost_used_credits"], row["version"]) == (Decimal("10"), 3)
    assert len(reports.seen) == 1
    assert _job(world, second)["state"] == "done"
    _assert_untouched(world, ben)


# ── The wiring ──────────────────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_headless_turn_r8\.py \\?$", workflow, re.M)
