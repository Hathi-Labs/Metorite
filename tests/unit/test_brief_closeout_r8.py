"""WS-BRIEF BR-2b-1 on a real database: the 18:30 close-out.

Spec: ``project-docs/specs/core_brief.md`` §4.3 and acceptance row 7a. The
suite reuses the world of ``test_brief_run_r8.py``: two orgs, three members,
the app role, a fake clock, and the feed read faked. The Handled read is
faked too, because it reads the tables of three apps.

**Mutations (R7).**

* C1. ``run._composes``: drop the ``kind`` guard. ``test_r8_a_closeout_makes_
  no_model_call_with_compose_on`` goes red.
* C2. ``planner.dedupe_key`` call in ``_write``: drop the kind from the key
  (use ``KIND``). ``test_r8_two_ticks_give_one_closeout_job_and_one_row``
  goes red.
* C3. ``run._closeout_reads``: drop the ``closed.ok`` check. ``test_r8_a_
  handled_read_that_is_not_ok_retries`` goes red.
* C4. ``planner.kinds_of``: return both kinds always. ``test_r8_with_the_
  switch_off_no_closeout_is_planned`` goes red.
"""

# ruff: noqa: F811
# The fixtures `world`, `promoted` and `app_engine` come from the suite of the run.
from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.brief import flags, phrase, planner, store
from gateway.brief import run as brief_run
from gateway.reach import plan
from gateway.routes import brief_today
from gateway.routes.shell import handled
from gateway.routes.shell.needs import Closed

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_brief_run_r8 import (  # noqa: F401
    _DB_GATE,
    ANA,
    BEN,
    CARA,
    _claims,
    _dsn,
    _go,
    _jobs,
    _ledger_on,
    _runs,
    _tick,
    app_engine,
    card,
    promoted,
    world,
)

pytestmark = _DB_GATE

KOLKATA = ZoneInfo("Asia/Kolkata")
D = date(2099, 3, 10)
#: The planner's tick, 18:10 in Kolkata, and the run's tick, 18:26.
TC0 = datetime(2099, 3, 10, 12, 40, tzinfo=UTC)
TC1 = datetime(2099, 3, 10, 12, 56, tzinfo=UTC)
SLOT = datetime(2099, 3, 10, 18, 30, tzinfo=KOLKATA)


def _on(world: Any, monkeypatch: Any, **kw: Any) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**{
        "brief_enabled": True, "brief_orgs": world.p.org_a, "brief_closeout": True, **kw}))


def _closed(key: str = "tasks:42", title: str = "#42 Ship it") -> Closed:
    return Closed(key=key, app=key.split(":")[0], title=title, href=f"/{key.split(':')[0]}?x=1",
                  by={"kind": "member", "name": "Ana"}, at=TC0.isoformat())


def _handled(world: Any, monkeypatch: Any, rows: dict[str, list[Closed]] | None = None,
             ok: bool = True) -> Counter[str]:
    calls: Counter[str] = Counter()

    async def fake(user: Any, now: datetime, *, budget_s: float) -> handled.HandledRun:
        calls[user.email] += 1
        mine = (rows or {}).get(user.email, [])
        return handled.HandledRun(entries=[c.entry() for c in mine], ok=ok, closed=list(mine))

    monkeypatch.setattr(handled, "handled_run", fake)
    return calls


def _plan_and_run(world: Any) -> Counter[str]:
    _tick(world, world.p.org_a, TC0)
    return _tick(world, world.p.org_a, TC1)


def _closeouts(world: Any, member: str | None = None) -> list[Any]:
    return [r for r in _runs(world, member) if r.kind == "closeout"]


# ── The planner ─────────────────────────────────────────────────────────────


def test_r8_a_member_in_kolkata_gets_one_closeout_run_at_18_30(world, monkeypatch) -> None:
    _on(world, monkeypatch)
    _tick(world, world.p.org_a, TC0)
    jobs = _jobs(world, planner.RUN_KIND)
    assert sorted(j.payload["member"] for j in jobs) == [ANA, BEN]
    ana = next(j for j in jobs if j.payload["member"] == ANA)
    assert ana.payload == {"member": ANA, "local_date": "2099-03-10",
                           "slot_at": "2099-03-10T18:30:00+05:30", "kind": "closeout"}
    assert ana.dedupe_key == f"brief:{ANA}:closeout:2099-03-10"
    assert ana.run_at == SLOT - planner.LEAD


def test_r8_two_ticks_give_one_closeout_job_and_one_row(world, monkeypatch) -> None:
    """Mutation C2: the kind is part of the dedupe key."""
    _on(world, monkeypatch)
    _handled(world, monkeypatch)
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 12, 35, tzinfo=UTC))
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 12, 50, tzinfo=UTC))
    jobs = _jobs(world, planner.RUN_KIND)
    assert sorted(j.dedupe_key for j in jobs) == [
        f"brief:{ANA}:closeout:2099-03-10", f"brief:{BEN}:closeout:2099-03-10"]


def test_r8_a_brief_and_a_closeout_of_one_day_do_not_share_a_job(world, monkeypatch) -> None:
    """Both slots in one window (a 07:30 brief and a closeout moved to 07:35):
    the kind in the key keeps two jobs. Without it the second is dropped."""
    from tests.unit.test_brief_run_r8 import T0, _set_prefs

    _on(world, monkeypatch)
    _set_prefs(world, ANA, world.p.org_a, {"closeout_time": "07:35"})
    _set_prefs(world, BEN, world.p.org_a, {"enabled": False})
    _tick(world, world.p.org_a, T0)
    kinds = sorted(j.payload.get("kind", "brief") for j in _jobs(world, planner.RUN_KIND))
    assert kinds == ["brief", "closeout"]


def test_r8_a_day_off_gives_no_closeout(world, monkeypatch) -> None:
    _on(world, monkeypatch)
    world.working = False
    _tick(world, world.p.org_a, TC0)
    assert _jobs(world, planner.RUN_KIND) == []


def test_r8_with_the_switch_off_no_closeout_is_planned(world, monkeypatch) -> None:
    """Mutation C4. ``BRIEF_CLOSEOUT`` off: no close-out job, and the brief
    path is as before (a brief job has no ``kind`` in its payload)."""
    from tests.unit.test_brief_run_r8 import T0

    _on(world, monkeypatch, brief_closeout=False)
    _tick(world, world.p.org_a, TC0)
    assert _jobs(world, planner.RUN_KIND) == []
    _tick(world, world.p.org_a, T0)
    jobs = _jobs(world, planner.RUN_KIND)
    assert len(jobs) == 2 and all("kind" not in j.payload for j in jobs)


def test_r8_a_queued_close_out_settles_done_when_the_switch_goes_off(world, monkeypatch) -> None:
    _on(world, monkeypatch)
    _tick(world, world.p.org_a, TC0)
    _on(world, monkeypatch, brief_closeout=False)
    _handled(world, monkeypatch)
    _tick(world, world.p.org_a, TC1)
    assert all(j.state == "done" for j in _jobs(world, planner.RUN_KIND))
    assert _runs(world) == []


# ── The run ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ledger", ["off", "on"])
def test_r8_a_closed_task_and_an_open_card_make_a_done_and_a_moved_claim(
        world, monkeypatch, ledger) -> None:
    _on(world, monkeypatch, brief_in_app=True)
    _ledger_on(monkeypatch, ledger == "on")
    _handled(world, monkeypatch, {ANA: [_closed("tasks:42", "#42 Ship it")]})
    world.cards[ANA] = [card("email:acc:t1")]
    _plan_and_run(world)
    run = _closeouts(world, ANA)[0]
    assert run.kind == "closeout" and run.local_date == D and run.slot_at == SLOT
    assert run.state == "delivered" and run.lead_source == "template"
    assert run.lead == "Your close-out, Tue 10 Mar. 1 done and 1 moved to tomorrow."
    got = _claims(world, str(run.id))
    assert [(c.position, c.section) for c in got] == [(0, "done"), (1, "moved")]
    done, moved = got
    assert (done.source_id, done.source_app, done.source_kind) == ("tasks:42", "tasks", "handled")
    assert done.text == "#42 Ship it" and done.href == "/tasks?x=1"
    assert moved.source_id == "email:acc:t1" and moved.section == "moved"
    assert moved.text == plan.line(world.cards[ANA][0], TC1)
    # BEN has neither: empty.
    ben = _closeouts(world, BEN)[0]
    assert ben.state == "empty" and ben.lead is None and _claims(world, str(ben.id)) == []
    from sqlalchemy import text as sql

    with world.p.admin_engine.connect() as c:
        rows = c.execute(sql("SELECT state FROM unattended_runs WHERE job = 'brief.closeout' "
                             "AND slot LIKE :s"), {"s": f"{ANA}:%"}).fetchall()
    assert len(rows) == (1 if ledger == "on" else 0)


def test_r8_two_ticks_and_a_replay_give_one_row_and_one_ledger_entry(world, monkeypatch) -> None:
    _on(world, monkeypatch)
    _ledger_on(monkeypatch, True)
    _handled(world, monkeypatch, {ANA: [_closed()]})
    _plan_and_run(world)
    _tick(world, world.p.org_a, TC1 + timedelta(minutes=1))
    assert len(_closeouts(world, ANA)) == 1
    from sqlalchemy import text as sql

    with world.p.admin_engine.connect() as c:
        n = c.execute(sql("SELECT count(*) FROM unattended_runs WHERE job = 'brief.closeout' "
                          "AND slot = :s"), {"s": f"{ANA}:2099-03-10"}).scalar()
    assert n == 1


def test_r8_only_done_and_only_moved(world, monkeypatch) -> None:
    _on(world, monkeypatch)
    _handled(world, monkeypatch, {ANA: [_closed("tasks:1", "One"), _closed("approvals:9", "Nine")]})
    world.cards[BEN] = [card("email:acc:t2"), card("tasks:0043", "overdue")]
    _plan_and_run(world)
    ana, ben = _closeouts(world, ANA)[0], _closeouts(world, BEN)[0]
    assert ana.lead == "Your close-out, Tue 10 Mar. 2 done."
    assert [c.section for c in _claims(world, str(ana.id))] == ["done", "done"]
    assert ben.lead == "Your close-out, Tue 10 Mar. 2 moved to tomorrow."
    assert [c.position for c in _claims(world, str(ben.id))] == [0, 1]


def test_r8_a_closeout_makes_no_model_call_with_compose_on(world, monkeypatch) -> None:
    """Mutation C1. ``BRIEF_COMPOSE`` and the Router are on, and the run has
    4 claims. The brief would phrase. The close-out does not."""
    from acb_llm import routed

    _on(world, monkeypatch, brief_compose=True)
    monkeypatch.setattr(routed, "routing_is_on", lambda: True)
    calls: list[str] = []

    async def spy(*a: Any, **k: Any) -> None:
        calls.append("phrase")

    monkeypatch.setattr(phrase, "phrase", spy)
    _handled(world, monkeypatch, {ANA: [_closed(f"tasks:{i}", f"Task {i}") for i in range(2)]})
    world.cards[ANA] = [card("email:acc:t1"), card("tasks:0043", "overdue")]
    _plan_and_run(world)
    run = _closeouts(world, ANA)[0]
    assert len(_claims(world, str(run.id))) == 4
    assert calls == [] and run.state == "ready" and run.lead_source == "template"
    assert run.model_request_id is None


def test_r8_a_handled_read_that_is_not_ok_retries(world, monkeypatch) -> None:
    """Mutation C3. A lost Handled source must not read as a quiet day."""
    _on(world, monkeypatch)
    _handled(world, monkeypatch, ok=False)
    _plan_and_run(world)
    jobs = [j for j in _jobs(world, planner.RUN_KIND)]
    assert jobs and all(j.state == "queued" and j.attempts == 1 for j in jobs)
    assert all(j.last_error.startswith("HandledIncomplete") for j in jobs)
    assert all(r.state == "planned" for r in _closeouts(world))
    _handled(world, monkeypatch, {ANA: [_closed()]})
    _tick(world, world.p.org_a, TC1 + brief_run.queue.backoff(1) + timedelta(seconds=1))
    assert {r.member_email: r.state for r in _closeouts(world)} == {ANA: "ready", BEN: "empty"}


def test_r8_a_payload_with_no_kind_runs_as_a_brief(world, monkeypatch) -> None:
    from tests.unit.test_brief_run_r8 import T1, _seed_and_run

    _on(world, monkeypatch)
    calls = _handled(world, monkeypatch)
    world.cards[ANA] = [card("email:acc:t1")]
    _seed_and_run(world, at=T1)
    run = _runs(world, ANA)[0]
    assert run.kind == "brief" and run.lead.startswith("Your day,")
    assert calls == Counter()  # a brief reads no Handled


# ── The read route, and the sections under FORCE RLS ────────────────────────


def _get(world: Any, email: str, org: str, kind: str = "closeout") -> Any:
    from acb_auth import UserContext, UserRole, build_access

    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:chat"], roles=["member"]))

    async def go() -> Any:
        token = bind_tenant(org)
        try:
            return await brief_today.get_today(kind=kind, user=user)
        except HTTPException as exc:
            return exc.status_code
        finally:
            release_tenant(token)

    async def scoped() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await go()

    import asyncio

    return asyncio.run(scoped())


def test_r8_get_today_serves_the_closeout_to_its_member_only(world, monkeypatch) -> None:
    _on(world, monkeypatch, brief_in_app=True)
    monkeypatch.setattr(brief_today, "_now", lambda: TC1 + timedelta(minutes=10))
    _handled(world, monkeypatch, {ANA: [_closed()]})
    world.cards[ANA] = [card("email:acc:t1")]
    _plan_and_run(world)
    body = _get(world, ANA, world.p.org_a)
    assert isinstance(body, dict), body
    assert body["kind"] == "closeout" and body["local_date"] == "2099-03-10"
    assert [(c["section"], c["source_kind"]) for c in body["claims"]] == [
        ("done", "handled"), ("moved", "needs_reply")]
    assert _get(world, ANA, world.p.org_a, kind="brief") == 404
    # Another member: BEN's close-out is empty, so 404. CARA is in org B.
    assert _get(world, BEN, world.p.org_a) == 404
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        brief_enabled=True, brief_orgs=f"{world.p.org_a},{world.p.org_b}", brief_in_app=True))
    assert _get(world, CARA, world.p.org_b) == 404
    assert _get(world, ANA, world.p.org_b) == 404


def test_r8_done_and_moved_claims_insert_under_force_rls(world) -> None:
    """The app role (FORCE RLS) writes both new sections, and org B reads none."""
    claims = [
        store.ClaimIn(position=0, section="done", text="Done", source_app="tasks",
                      source_kind="handled", source_id="tasks:1", href="/tasks"),
        store.ClaimIn(position=1, section="moved", text="Moved", source_app="email",
                      source_kind="needs_reply", source_id="email:a:t", href="/email")]

    async def go() -> tuple[list[Any], list[Any]]:
        from acb_common.db import tenant_session

        async with tenant_session(world.p.org_a) as db:
            run = (await store.create_run(db, ANA, kind="closeout", local_date=D,
                                          slot_at=SLOT)).run
            await store.add_claims(db, ANA, run.id, claims)
            own = await store.read_claims(db, ANA, run.id)
        async with tenant_session(world.p.org_b) as db:
            other = await store.read_claims(db, ANA, run.id)
        return own, other

    own, other = _go(world, go)
    assert [c.section for c in own] == ["done", "moved"] and other == []
