"""WS-BRIEF BR-2a-1 — the planner and the brief run on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §4.1, §4.2, §9 and §10 row 2.
Code: ``gateway/brief/{planner,gather,run}.py``, the seed hooks of
``gateway/jobs/worker.py``, ``gateway/brief/store.py``.

The suite runs on a phase-4 promoted scratch database, as the app role, so
FORCE RLS binds. Two orgs, three members, a frozen clock, the worker's own
``run_org``. The feed, the day and the identity reads are fakes: the seams
``loop.resolve``, ``loop.snapshot``, ``my_day_loop._read_day`` and
``feed._working_day`` answer what the test sets. ``acompletion_with_fallback``
and ``outbox.plan`` fail the test when anything calls them.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_run_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``store._CREATE_SQL``: drop ``ON CONFLICT ... DO NOTHING`` and catch
  the error in ``create_run`` as a second row. ``test_r8_two_ticks_and_a_
  restart_give_one_row_one_ledger_row_and_one_claim_set[off]`` goes red.
* M2. ``run.run``: drop ``unattended.release`` from the ``except``.
  ``test_r8_a_failed_first_try_and_a_retry_give_one_claim_set[on]`` goes red.
* M3. ``gather.claim_of``: ``app = card["app"]``.
  ``test_r8_each_claim_names_its_record`` goes red.
* M4. ``planner._due``: drop the ``enabled`` check.
  ``test_r8_an_opted_out_member_gets_no_job`` goes red.
* M5. ``planner.slot``: ``today = day.feed_day(now)``.
  ``test_r8_the_slot_date_is_the_calendar_date`` goes red.
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

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.brief import flags, planner, store
from gateway.brief import run as brief_run
from gateway.jobs import queue, unattended, worker
from gateway.reach import loop, outbox, plan
from gateway.routes.shell import state
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@br2a.test"
BEN = f"ben.{TAG}@br2a.test"
CARA = f"cara.{TAG}@br2a.test"
LIKE = f"%.{TAG}@br2a.test"

KOLKATA = ZoneInfo("Asia/Kolkata")
DAY = state.Day(KOLKATA, 9, 18)
#: The planner's tick: Tuesday 10 March 2099, 07:10 in Asia/Kolkata.
T0 = datetime(2099, 3, 10, 1, 40, tzinfo=UTC)
#: The run's tick: 07:26 local, after ``run_at`` (07:25).
T1 = datetime(2099, 3, 10, 1, 56, tzinfo=UTC)
D = date(2099, 3, 10)
SLOT = datetime(2099, 3, 10, 7, 30, tzinfo=KOLKATA)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def card(sid: str, kind: str = "needs_reply", **kw) -> dict:
    facts = kw.pop("facts", None) or (
        {"who": "Priya", "waiting_since": (T1 - timedelta(minutes=5)).isoformat()}
        if kind == "needs_reply" else {})
    base = {"signal_id": sid, "app": "calendar", "kind": kind, "group": "needs_you",
            "score": 50, "title": "T", "facts": facts, "fingerprint": f"fp{sid[-4:]:>06}",
            "state": "new", "at": None, "why": "W", "href": f"/{sid.split(':')[0]}?x=1"}
    return {**base, **kw}


def _insert_members(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (CARA, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:org AS uuid))"),
                {"e": email, "org": org})


def _set_prefs(world: Any, email: str, org: str, prefs: dict[str, Any]) -> None:
    """The stored profile of *email*: ``apps.brief``, as a later settings UI
    writes it."""
    import json

    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, assistant_profile) "
            "VALUES (:u, CAST(:org AS uuid), CAST(:p AS jsonb))"),
            {"u": email, "org": org, "p": json.dumps({"v": 1, "apps": {"brief": prefs}})})


def _set_org_zone(world: Any, org: str, zone: str | None) -> None:
    """The ``default_timezone`` of the org's work-schedule policy, or no
    policy row for None."""
    import json

    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_settings WHERE key = 'work_schedule' "
                       "AND organization_id = CAST(:o AS uuid)"), {"o": org})
        if zone is not None:
            c.execute(text(
                "INSERT INTO org_settings (organization_id, key, value) "
                "VALUES (CAST(:o AS uuid), 'work_schedule', CAST(:v AS jsonb))"),
                {"o": org, "v": json.dumps({"default_timezone": zone})})


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    _insert_members(p)
    w = SimpleNamespace(p=p, now=T0, cards={}, working=True, complete=True, resolve_as={},
                        reads=Counter(), days={})
    # Org A's default zone is Kolkata (the work-schedule policy). The members
    # of the suite store no zone, so the planner's prefilter reads this one.
    _set_org_zone(w, p.org_a, "Asia/Kolkata")
    # The ladder's owner is an active member of org A. They opt out, so the
    # counts below are the three members of this suite.
    _set_prefs(w, p.owner_email, p.org_a, {"enabled": False})

    async def orgs() -> list[str]:
        return [p.org_a, p.org_b]

    monkeypatch.setattr(worker, "_orgs", orgs)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=True))
    monkeypatch.setattr(worker, "_HANDLERS", {})
    monkeypatch.setattr(worker, "_PRUNE_SEEDED", {})
    monkeypatch.setattr(worker, "_SEEDED", {})
    monkeypatch.setattr(worker, "seed_hooks", [])
    monkeypatch.setattr(worker, "prune_hooks", [])
    worker._stopping.clear()
    brief_run.register_jobs()
    monkeypatch.setattr(flags, "get_settings",
                        lambda: SimpleNamespace(brief_enabled=True, brief_orgs=p.org_a))
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(unattended_ledger=False))
    monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))
    monkeypatch.setattr(planner, "_now", lambda: w.now)
    monkeypatch.setattr(brief_run, "_now", lambda: w.now)

    async def resolve(org: str, member: str) -> Any:
        w.reads["resolve"] += 1
        got = w.resolve_as.get(member)
        return got if isinstance(got, str) else SimpleNamespace(
            email=member, organization_id=org)

    async def snapshot(org: str, user: Any, now: datetime | None = None) -> loop.Snapshot:
        w.reads["snapshot"] += 1
        return loop.Snapshot(list(w.cards.get(user.email, [])), now or w.now, DAY,
                             w.working, w.complete)

    async def read_day(org: str, user: Any) -> state.Day:
        got = w.days.get(user.email, DAY)
        if isinstance(got, Exception):
            raise got
        return got

    async def working_day(user: Any, today: date) -> bool:
        return w.working

    async def never(*a: Any, **k: Any) -> Any:
        raise AssertionError("the slice calls no model and sends nothing")

    from gateway import my_day_loop
    from gateway.routes.shell import feed

    monkeypatch.setattr(loop, "resolve", resolve)
    monkeypatch.setattr(loop, "snapshot", snapshot)
    monkeypatch.setattr(my_day_loop, "_read_day", read_day)
    monkeypatch.setattr(feed, "_working_day", working_day)
    monkeypatch.setattr(outbox, "plan", never)
    import acb_llm.context

    monkeypatch.setattr(acb_llm.context, "acompletion_with_fallback", never)
    yield w
    worker._stopping.clear()
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE member_email LIKE :t"), {"t": LIKE})
        c.execute(text("DELETE FROM unattended_runs WHERE job LIKE 'brief.%' "
                       "AND slot LIKE :t"), {"t": f"%{TAG}%"})
        c.execute(text("DELETE FROM unattended_incidents WHERE job LIKE 'brief.%'"))
        c.execute(text("DELETE FROM jobs WHERE kind LIKE 'brief.%' AND organization_id IN "
                       "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM user_settings WHERE lower(user_id) LIKE :t OR user_id = :o"),
                  {"t": LIKE, "o": p.owner_email})
        c.execute(text("DELETE FROM org_settings WHERE key = 'work_schedule' AND "
                       "organization_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _ledger_on(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(unattended_ledger=on))


def _jobs(world: Any, kind: str, org: str | None = None) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM jobs WHERE kind = :k AND organization_id = CAST(:o AS uuid) "
            "ORDER BY created_at, id"), {"k": kind, "o": org or world.p.org_a}).fetchall())


def _runs(world: Any, member: str | None = None) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM brief_runs WHERE member_email LIKE :t "
            "AND (CAST(:m AS text) IS NULL OR member_email = CAST(:m AS text)) "
            "ORDER BY member_email"),
            {"t": LIKE, "m": member}).fetchall())


def _claims(world: Any, run_id: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM brief_claims WHERE run_id = CAST(:r AS uuid) ORDER BY position"),
            {"r": run_id}).fetchall())


def _ledger(world: Any, member: str | None = None) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM unattended_runs WHERE job = 'brief.brief' AND slot LIKE :t "
            "ORDER BY started_at"), {"t": f"{member}:%" if member else f"%{TAG}%"}).fetchall())


def _tick(world: Any, org: str, now: datetime) -> Counter[str]:
    world.now = now
    return _go(world, lambda: worker.run_org(org, now))


# ── The planner ─────────────────────────────────────────────────────────────


def test_r8_a_member_in_kolkata_gets_one_run_at_their_brief_time(world) -> None:
    """Acceptance: one run at 07:30 local. The tick at 07:10 seeds the plan
    job, runs it, and the plan enqueues ``brief.run`` for 07:25 local."""
    counts = _tick(world, world.p.org_a, T0)
    assert counts == Counter(done=1)  # the plan job
    plans = _jobs(world, planner.PLAN_KIND)
    assert len(plans) == 1 and plans[0].dedupe_key == "plan:2099-03-10T01:30"
    runs = _jobs(world, planner.RUN_KIND)
    assert sorted(j.payload["member"] for j in runs) == [ANA, BEN]
    ana = next(j for j in runs if j.payload["member"] == ANA)
    assert ana.run_at == SLOT - planner.LEAD == datetime(2099, 3, 10, 1, 55, tzinfo=UTC)
    assert ana.dedupe_key == f"brief:{ANA}:brief:2099-03-10"
    assert ana.payload == {"member": ANA, "local_date": "2099-03-10",
                           "slot_at": "2099-03-10T07:30:00+05:30"}
    assert ana.state == "queued" and ana.max_attempts == planner.RUN_ATTEMPTS
    # Org B is not on the list: no plan job, no run job, no read.
    assert _jobs(world, planner.PLAN_KIND, world.p.org_b) == []
    assert _tick(world, world.p.org_b, T0) == Counter()
    assert _jobs(world, planner.RUN_KIND, world.p.org_b) == []


def test_r8_two_planner_ticks_give_one_run_job(world) -> None:
    """Mutation M7 (repair round 1, P2-a). Both ticks fall before ``run_at``
    (07:25), so no ``brief_runs`` row exists at the second tick, and only
    the dedupe key keeps one live job for each member."""
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 7, 5, tzinfo=KOLKATA))
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 7, 20, tzinfo=KOLKATA))
    assert len(_jobs(world, planner.PLAN_KIND)) == 2  # one for each bucket
    assert _runs(world) == []  # no row masks the dedupe key
    runs = _jobs(world, planner.RUN_KIND)
    assert sorted(j.payload["member"] for j in runs) == [ANA, BEN]
    assert all(j.state == "queued" for j in runs)


def test_r8_a_day_off_gives_no_run(world) -> None:
    world.working = False
    assert _tick(world, world.p.org_a, T0) == Counter(done=1)
    assert _jobs(world, planner.RUN_KIND) == []


def test_r8_an_opted_out_member_gets_no_job(world) -> None:
    _set_prefs(world, BEN, world.p.org_a, {"enabled": False})
    _tick(world, world.p.org_a, T0)
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [ANA]


def test_r8_a_member_with_a_brief_time_of_nine_gets_the_slot_at_nine(world) -> None:
    _set_prefs(world, BEN, world.p.org_a, {"brief_time": "09:00"})
    _tick(world, world.p.org_a, T0)
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [ANA]
    at_845 = datetime(2099, 3, 10, 8, 45, tzinfo=KOLKATA)
    _tick(world, world.p.org_a, at_845)
    ben = [j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == BEN]
    assert len(ben) == 1
    assert ben[0].run_at == datetime(2099, 3, 10, 8, 55, tzinfo=KOLKATA)
    assert ben[0].payload["slot_at"] == "2099-03-10T09:00:00+05:30"


def test_r8_the_slot_date_is_the_calendar_date(world) -> None:
    """Mutation M5. The day starts at 09:00, so the feed day at 07:10 is the
    9th. The run is still the brief of the 10th."""
    assert DAY.feed_day(T0) == date(2099, 3, 9)
    _tick(world, world.p.org_a, T0)
    assert {j.payload["local_date"] for j in _jobs(world, planner.RUN_KIND)} == {"2099-03-10"}


def test_r8_a_departed_member_is_skipped_and_the_others_are_planned(world) -> None:
    world.resolve_as[ANA] = "departed"
    _tick(world, world.p.org_a, T0)
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [BEN]


def test_r8_a_planner_outage_before_the_slot_still_gives_the_brief(world) -> None:
    """Repair round 1, P1-2. No tick ran before the slot. The first tick, at
    08:30 local, is one hour after 07:30, inside the look-back, so it plans
    the slot, and the run gathers it in the same tick."""
    world.cards[ANA] = [card("email:acc:t1")]
    late = datetime(2099, 3, 10, 8, 30, tzinfo=KOLKATA)
    _tick(world, world.p.org_a, late)
    runs = _jobs(world, planner.RUN_KIND)
    assert sorted(j.payload["member"] for j in runs) == [ANA, BEN]
    assert all(j.payload["slot_at"] == "2099-03-10T07:30:00+05:30" for j in runs)
    ana = next(r for r in _runs(world) if r.member_email == ANA)
    assert ana.state == "ready" and ana.slot_at == SLOT


def test_r8_a_slot_older_than_the_look_back_is_recorded_once(world, capsys) -> None:
    """Repair round 1, P1-2. The first tick is at 10:00 local, past the
    look-back. No run, and each member's slot gets one ``failed`` row,
    ``slot_missed``, with one warning. A later tick adds nothing."""
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 10, 0, tzinfo=KOLKATA))
    assert _jobs(world, planner.RUN_KIND) == []
    runs = _runs(world)
    assert [(r.member_email, r.state, r.error) for r in runs] == [
        (ANA, "failed", planner.MISSED), (BEN, "failed", planner.MISSED)]
    assert all(r.slot_at == SLOT for r in runs)
    first = capsys.readouterr().out
    assert first.count("brief.slot_missed") == 2
    _tick(world, world.p.org_a, datetime(2099, 3, 10, 10, 15, tzinfo=KOLKATA))
    assert len(_runs(world)) == 2 and "brief.slot_missed" not in capsys.readouterr().out
    assert world.reads["resolve"] == 2  # the second tick read no member


def test_r8_a_case_variant_settings_row_cannot_opt_a_member_back_in(world) -> None:
    """Repair round 1, P2-b. BEN opted out in the row of his address. A
    second row in another case says ``enabled``. The one reader gives BEN
    once, by the row rule of ``READ_SQL``, so BEN gets no job."""
    import json

    _set_prefs(world, BEN, world.p.org_a, {"enabled": False})
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, assistant_profile) "
            "VALUES (:u, CAST(:org AS uuid), CAST(:p AS jsonb))"),
            {"u": BEN.upper(), "org": world.p.org_a,
             "p": json.dumps({"v": 1, "apps": {"brief": {"enabled": True}}})})
    _tick(world, world.p.org_a, T0)
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [ANA]


def test_r8_the_bulk_reader_returns_one_entry_and_only_the_brief_keys(world) -> None:
    """Repair round 1, P2-b. ``members_app_prefs`` answers one entry for each
    member, and the brief keys only: never the rest of a private profile."""
    import json

    from acb_common import assistant_profile

    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, assistant_profile) "
            "VALUES (:u, CAST(:org AS uuid), CAST(:p AS jsonb))"),
            {"u": ANA.upper(), "org": world.p.org_a, "p": json.dumps({
                "v": 1, "facts": [{"id": "f1", "text": "private"}],
                "apps": {"brief": {"brief_time": "08:15"}}})})

    async def go() -> list:
        async with tenant_session(world.p.org_a) as db:
            return await assistant_profile.members_app_prefs(db, world.p.org_a, "brief")

    got = dict(_go(world, go))
    assert list(got).count(ANA) == 1
    assert got[ANA] == {"enabled": True, "brief_time": "08:15", "closeout_time": "18:30"}
    assert got[BEN] == assistant_profile.APP_PREFERENCES["brief"]
    assert CARA not in got  # another org


def test_r8_a_member_with_no_zone_takes_the_org_zone(world) -> None:
    """Repair round 1, P2-h. ANA's day reads UTC: she stored no zone. The
    org's default zone is Kolkata, so her slot is 07:30 in Kolkata. With no
    org zone, the same tick finds no slot (07:30 UTC is hours away)."""
    world.days[ANA] = state.Day(ZoneInfo("UTC"))
    _tick(world, world.p.org_a, T0)
    ana = next(j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == ANA)
    assert ana.payload["slot_at"] == "2099-03-10T07:30:00+05:30"
    _set_org_zone(world, world.p.org_a, None)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM jobs WHERE kind LIKE 'brief.%' "
                       "AND organization_id = CAST(:a AS uuid)"), {"a": world.p.org_a})
    world.days[BEN] = state.Day(ZoneInfo("UTC"))
    worker._SEEDED.clear()
    _tick(world, world.p.org_a, T0)
    assert _jobs(world, planner.RUN_KIND) == []


def test_r8_one_member_whose_day_read_fails_does_not_stop_the_plan(world) -> None:
    """Repair round 1, P2-f. ANA's day read raises. The plan logs it, skips
    her, and plans BEN. The plan job is done."""
    world.days[ANA] = RuntimeError("the day read broke")
    assert _tick(world, world.p.org_a, T0) == Counter(done=1)
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [BEN]


def test_r8_a_member_whose_slot_is_far_gets_no_identity_read(world) -> None:
    """Repair round 1, P2-f. BEN's brief is at 13:00. At 07:10 no zone puts
    it in the window, so the plan reads ANA only."""
    _set_prefs(world, BEN, world.p.org_a, {"brief_time": "13:00"})
    _tick(world, world.p.org_a, T0)
    assert world.reads["resolve"] == 1
    assert [j.payload["member"] for j in _jobs(world, planner.RUN_KIND)] == [ANA]


# ── The run ─────────────────────────────────────────────────────────────────


def _seed_and_run(world: Any, *, at: datetime = T1) -> Counter[str]:
    """The planner's tick, then the run's tick. The second tick opens a new
    15-minute bucket, so it also seeds and runs a second plan job, which
    enqueues nothing: each slot has its ``brief_runs`` row by then. So the
    count of a run tick is the runs plus one."""
    _tick(world, world.p.org_a, T0)
    return _tick(world, world.p.org_a, at)


def test_r8_each_claim_names_its_record(world) -> None:
    """Acceptance: each claim has a source_app, a source_id, an href and a
    fingerprint or null, and its text is ``plan.line`` of its card. The
    source_app is the prefix of the signal id, and not ``card["app"]``."""
    cards = [card("tasks:0042", "block_soon", group="now", app="calendar"),
             card("email:acc:t1"), card("tasks:0043", "overdue", fingerprint=None)]
    world.cards[ANA] = cards
    counts = _seed_and_run(world)
    assert counts == Counter(done=3)  # two runs, and the second plan job
    assert len(_jobs(world, planner.RUN_KIND)) == 2  # the second plan enqueued nothing
    run = next(r for r in _runs(world) if r.member_email == ANA)
    assert run.state == "ready" and run.lead_source == "template"
    assert run.ready_at == T1 and run.error is None and run.unattended_run_id is None
    shown = plan.brief_items(cards)[:plan.BRIEF_LINES]
    parts = plan.brief_parts(cards, D, now=T1)
    assert run.lead == plan.brief_lead(parts) == "Your day, Tue 10 Mar. 3 things need you."
    got = _claims(world, str(run.id))
    assert [c.source_id for c in got] == [c["signal_id"] for c in shown]
    for c, source in zip(got, shown, strict=True):
        assert c.source_app == source["signal_id"].split(":")[0] != "calendar"
        assert c.source_kind == source["kind"] and c.section == source["group"]
        assert c.href == source["href"] and c.href.startswith("/")
        assert c.text == plan.line(source, T1) and c.text
        assert c.fingerprint == (source["fingerprint"] or None)
    assert [c.position for c in got] == [0, 1, 2]
    assert world.reads["snapshot"] == 2  # one for each member, none twice


def test_r8_a_member_with_no_open_card_gets_empty(world) -> None:
    world.cards[ANA] = [card("email:acc:t1")]
    world.cards[BEN] = [card("email:acc:t2", group="fyi"), card("email:acc:t3", state="dismissed")]
    _seed_and_run(world)
    ben = next(r for r in _runs(world) if r.member_email == BEN)
    assert ben.state == "empty" and ben.lead is None and ben.lead_source is None
    assert _claims(world, str(ben.id)) == [] and ben.ready_at is None


def test_r8_a_card_with_an_unknown_prefix_is_skipped(world) -> None:
    world.cards[ANA] = [card("email:acc:t1"), card("nobody:1"), card("tasks:0042", "overdue")]
    _seed_and_run(world)
    ana = next(r for r in _runs(world) if r.member_email == ANA)
    assert ana.state == "ready"
    got = _claims(world, str(ana.id))
    assert "nobody" not in {c.source_app for c in got} and len(got) == 2
    # The lead counts every item of the feed, and the skip hides none.
    assert ana.lead.endswith("3 things need you.")


@pytest.mark.parametrize("ledger", ["off", "on"])
def test_r8_two_ticks_and_a_restart_give_one_row_one_ledger_row_and_one_claim_set(
        world, monkeypatch, ledger) -> None:
    """Acceptance, and mutation M1. The run job runs, a restart re-enqueues
    the same slot, and it runs again. One ``brief_runs`` row, one ledger
    row with the ledger on, and one claim set."""
    _ledger_on(monkeypatch, ledger == "on")
    world.cards[ANA] = [card("email:acc:t1"), card("tasks:0042", "overdue")]
    assert _seed_and_run(world) == Counter(done=3)
    first = next(r for r in _runs(world) if r.member_email == ANA)
    claims = _claims(world, str(first.id))
    assert len(claims) == 2 and first.state == "ready"
    rows = _ledger(world, ANA)
    if ledger == "on":
        assert len(rows) == 1 and rows[0].state == "ok" and rows[0].scheduled_for == SLOT
        assert str(first.unattended_run_id) == str(rows[0].id)
    else:
        assert rows == [] and first.unattended_run_id is None
    # The restart: a new runner, and the slot queued again (the first job is done).
    monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))

    async def again() -> None:
        async with tenant_session(world.p.org_a) as db:
            out = await queue.enqueue(db, planner.RUN_KIND, {
                "member": ANA, "local_date": "2099-03-10",
                "slot_at": "2099-03-10T07:30:00+05:30"},
                dedupe_key=planner.dedupe_key(ANA, "brief", D), now=T1)
            assert out.created

    _go(world, again)
    assert _tick(world, world.p.org_a, T1 + timedelta(minutes=1)) == Counter(done=1)
    assert len(_runs(world, ANA)) == 1
    second = _runs(world, ANA)[0]
    assert second.state == "ready" and second.ready_at == T1
    assert [c.id for c in _claims(world, str(second.id))] == [c.id for c in claims]
    assert len(_ledger(world, ANA)) == (1 if ledger == "on" else 0)
    assert world.reads["snapshot"] == 2  # ANA once, BEN once: no second gather


@pytest.mark.parametrize("ledger", ["off", "on"])
def test_r8_a_failed_first_try_and_a_retry_give_one_claim_set(world, monkeypatch, ledger) -> None:
    """Acceptance, and mutation M2. The first try reads an incomplete
    snapshot and raises. The slot goes back, the queue retries, and the
    retry gathers once."""
    _ledger_on(monkeypatch, ledger == "on")
    world.cards[ANA] = [card("email:acc:t1")]
    world.cards[BEN] = [card("email:acc:t2")]
    world.complete = False
    assert _seed_and_run(world) == Counter(retry=2, done=1)
    runs = _runs(world)
    assert [r.state for r in runs] == ["planned", "planned"]
    assert all(_claims(world, str(r.id)) == [] for r in runs)
    assert _ledger(world) == []  # released, so a retry can begin the slot
    jobs = _jobs(world, planner.RUN_KIND)
    assert len(jobs) == 2  # the second plan job added none: each slot has its row
    assert all(j.state == "queued" and j.attempts == 1 for j in jobs)
    assert all(j.last_error.startswith("SnapshotIncomplete") for j in jobs)
    world.complete = True
    later = T1 + queue.backoff(1) + timedelta(seconds=1)
    assert _tick(world, world.p.org_a, later) == Counter(done=2)
    for r in _runs(world):
        assert r.state == "ready" and len(_claims(world, str(r.id))) == 1
    rows = _ledger(world)
    assert len(rows) == (2 if ledger == "on" else 0)
    assert all(r.state == "ok" for r in rows)


def test_r8_a_gather_past_the_handler_timeout_gives_the_slot_back(world, monkeypatch) -> None:
    """Mutation M6 (repair round 1, P1-1). The worker's time limit cancels
    the handler, and a cancel is not an ``Exception``. The handler still
    gives the ledger slot back, so the retry begins it fresh and gathers
    once: one claim set and one ledger row, ``ok``."""
    _ledger_on(monkeypatch, True)
    monkeypatch.setattr(worker, "HANDLER_TIMEOUT", 0.5)
    world.cards[ANA] = [card("email:acc:t1"), card("tasks:0042", "overdue")]
    real = loop.snapshot
    hangs = {"left": 1}

    async def slow(org: str, user: Any, now: datetime | None = None) -> loop.Snapshot:
        if user.email == ANA and hangs["left"]:
            hangs["left"] -= 1
            await asyncio.sleep(30)
        return await real(org, user, now)

    monkeypatch.setattr(loop, "snapshot", slow)
    assert _seed_and_run(world) == Counter(done=2, retry=1)
    job = next(j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == ANA)
    assert job.state == "queued" and job.last_error.startswith("HandlerTimeout")
    assert [r.state for r in _runs(world, ANA)] == ["planned"]
    assert _ledger(world, ANA) == []  # given back, under the cancel
    later = T1 + queue.backoff(1) + timedelta(seconds=1)
    assert _tick(world, world.p.org_a, later) == Counter(done=1)
    (run,) = _runs(world, ANA)
    assert run.state == "ready" and len(_claims(world, str(run.id))) == 2
    (row,) = _ledger(world, ANA)
    assert row.state == "ok" and str(run.unattended_run_id) == str(row.id)


def test_r8_a_member_who_left_before_the_run_gets_no_row(world, monkeypatch) -> None:
    """Repair round 1, P2-c. The run resolves the member BEFORE it makes a
    row, so a member who left after the plan gets no ``planned`` row. The
    slot goes back to the ledger, and no snapshot runs."""
    _ledger_on(monkeypatch, True)
    world.cards[ANA] = [card("email:acc:t1")]
    _tick(world, world.p.org_a, T0)
    world.resolve_as[ANA] = "departed"
    _tick(world, world.p.org_a, T1)
    assert _runs(world, ANA) == [] and _ledger(world, ANA) == []
    job = next(j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == ANA)
    assert job.state == "done"
    assert world.reads["snapshot"] == 1  # BEN only


def test_r8_a_planned_row_of_a_member_who_left_turns_failed(world, monkeypatch) -> None:
    """Repair round 1, P2-c. The first try made the row and then failed. The
    member leaves before the retry, so the row turns ``failed`` with the
    reason, and never stays ``planned``."""
    _ledger_on(monkeypatch, True)
    world.cards[ANA] = [card("email:acc:t1")]
    world.complete = False
    _seed_and_run(world)
    assert [r.state for r in _runs(world, ANA)] == ["planned"]
    world.complete = True
    world.resolve_as[ANA] = "departed"
    _tick(world, world.p.org_a, T1 + queue.backoff(1) + timedelta(seconds=1))
    (run,) = _runs(world, ANA)
    assert run.state == "failed" and run.error == "member_departed"
    assert _claims(world, str(run.id)) == [] and _ledger(world, ANA) == []


@pytest.mark.parametrize("change, reason", [
    ({"enabled": False}, "opted_out"), ({"brief_time": "09:00"}, "moved")])
def test_r8_the_run_reads_the_preferences_again(world, monkeypatch, change, reason) -> None:
    """Repair round 1, P2-d. The member turns the brief off, or moves it,
    between the plan and the run. The run reads the preferences again and
    gathers nothing: no row, no snapshot, and the slot goes back."""
    _ledger_on(monkeypatch, True)
    world.cards[ANA] = [card("email:acc:t1")]
    _tick(world, world.p.org_a, T0)
    _set_prefs(world, ANA, world.p.org_a, change)
    _tick(world, world.p.org_a, T1)
    assert _runs(world, ANA) == [] and _ledger(world, ANA) == []
    assert world.reads["snapshot"] == 1  # BEN only
    if reason == "moved":
        # The planner plans the new time, and the run gathers it.
        _tick(world, world.p.org_a, datetime(2099, 3, 10, 8, 45, tzinfo=KOLKATA))
        _tick(world, world.p.org_a, datetime(2099, 3, 10, 8, 56, tzinfo=KOLKATA))
        (run,) = _runs(world, ANA)
        assert run.state == "ready"
        assert run.slot_at == datetime(2099, 3, 10, 9, 0, tzinfo=KOLKATA)


def test_r8_a_lost_claim_does_not_finish_the_ledger_slot(world, monkeypatch) -> None:
    """Repair round 1, P2-c. The claim is gone when the gather would write.
    Nothing commits, and the ledger slot goes back instead of ``ok``, so the
    try that holds the job can begin it."""
    _ledger_on(monkeypatch, True)
    world.cards[ANA] = [card("email:acc:t1")]
    real = queue.hold
    lose = {"left": 1}

    async def hold(db: Any, claim: queue.Claim) -> bool:
        if claim.kind == planner.RUN_KIND and claim.payload.get("member") == ANA \
                and world.reads["snapshot"] and lose["left"]:
            lose["left"] -= 1
            return False
        return await real(db, claim)

    monkeypatch.setattr(queue, "hold", hold)
    _seed_and_run(world)
    (run,) = _runs(world, ANA)
    assert run.state == "planned" and _claims(world, str(run.id)) == []
    assert _ledger(world, ANA) == []


def test_r8_a_slot_that_the_ledger_holds_is_not_gathered_again(world, monkeypatch) -> None:
    """A run that a crash cut: the ledger row is ``started`` under another
    runner. ``begin`` marks it unknown and the job settles done with no
    gather, so the brief is lost for the day on purpose (BR-2d)."""
    _ledger_on(monkeypatch, True)
    world.cards[ANA] = [card("email:acc:t1")]

    async def crashed() -> None:
        monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))
        run = await unattended.begin(world.p.org_a, brief_run.LEDGER_JOB, f"{ANA}:2099-03-10",
                                     now=T0)
        assert run.fresh
        monkeypatch.setattr(unattended, "RUNNER", str(uuid.uuid4()))

    _go(world, crashed)
    # ANA's run, BEN's run, the second plan job, and ANA's run again: the
    # planner sees no row of ANA and enqueues the slot once more, while it is
    # in the window. Each try finds the slot taken and gathers nothing.
    assert _seed_and_run(world) == Counter(done=4)
    assert _runs(world, ANA) == []
    rows = _ledger(world, ANA)
    assert len(rows) == 1 and rows[0].state == "unknown"
    assert world.reads["snapshot"] == 1  # BEN only


def test_r8_org_b_and_member_two_read_no_run_of_member_one(world) -> None:
    world.cards[ANA] = [card("email:acc:t1")]
    _seed_and_run(world)
    ana = next(r for r in _runs(world) if r.member_email == ANA)

    async def reads() -> tuple:
        async with tenant_session(world.p.org_b) as db:
            other_org = await store.read_run(db, ANA, kind="brief", local_date=D)
            other_org_claims = await store.read_claims(db, ANA, str(ana.id))
        async with tenant_session(world.p.org_a) as db:
            other_member = await store.read_claims(db, BEN, str(ana.id))
            own = await store.read_run(db, ANA, kind="brief", local_date=D)
        return other_org, other_org_claims, other_member, own

    other_org, other_org_claims, other_member, own = _go(world, reads)
    assert other_org is None and other_org_claims == [] and other_member == []
    assert own is not None and own.state == "ready"


def test_r8_with_the_switch_off_no_job_no_row_and_a_waiting_run_settles_done(
        world, monkeypatch) -> None:
    world.cards[ANA] = [card("email:acc:t1")]
    for settings in (SimpleNamespace(brief_enabled=False, brief_orgs=world.p.org_a),
                     SimpleNamespace(brief_enabled=True, brief_orgs="")):
        monkeypatch.setattr(flags, "get_settings", lambda s=settings: s)
        assert _tick(world, world.p.org_a, T0) == Counter()
        assert _jobs(world, planner.PLAN_KIND) == [] and _jobs(world, planner.RUN_KIND) == []
        assert _runs(world) == []

    async def waiting() -> None:
        async with tenant_session(world.p.org_a) as db:
            await queue.enqueue(db, planner.RUN_KIND, {
                "member": ANA, "local_date": "2099-03-10",
                "slot_at": "2099-03-10T07:30:00+05:30"}, now=T1)

    _go(world, waiting)
    assert _tick(world, world.p.org_a, T1) == Counter(done=1)
    assert _jobs(world, planner.RUN_KIND)[0].state == "done"
    assert _runs(world) == [] and world.reads["snapshot"] == 0


def test_r8_the_prune_hook_deletes_a_run_of_91_days_and_keeps_one_of_89(world) -> None:
    today = date(2099, 6, 1)

    async def go() -> tuple:
        async with tenant_session(world.p.org_a) as db:
            await store.create_run(db, ANA, kind="brief", local_date=today - timedelta(days=91),
                                   slot_at=T0)
            await store.create_run(db, ANA, kind="brief", local_date=today - timedelta(days=89),
                                   slot_at=T0)
        async with tenant_session(world.p.org_a) as db:
            await brief_run.prune(db, today)
        return tuple(r.local_date for r in _runs(world, ANA))

    assert _go(world, go) == (today - timedelta(days=89),)
    assert brief_run.prune in worker.prune_hooks
