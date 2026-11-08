"""WS-AUTO AUTO-8a: the headless turn on a real database (R8).

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
    assert (row["state"], row["version"], row["round_count"]) == ("active", 2, 1)
    assert row["cost_used_credits"] == Decimal("1")
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
    assert (row["state"], row["block_code"], row["version"]) == ("blocked", "cap_reached", 2)
    assert (row["round_count"], row["cost_used_credits"]) == (1, Decimal("2"))
    assert len(runner.calls) == 1 and [g.version for g in reports.seen] == [2]
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
    assert _row(world, ana.id)["version"] == 2
    assert _job(world, second)["state"] == "done"
    assert _ledger(world, second) == []


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
    assert (row["round_count"], row["round_cap"], row["version"]) == (1, 41, 3)
    assert row["cost_used_credits"] == Decimal("1")
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


# ── The wiring ──────────────────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_headless_turn_r8\.py \\?$", workflow, re.M)
