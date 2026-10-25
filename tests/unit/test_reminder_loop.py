"""WS-66 RM-2 — the reminder loop on a real database (R8, frozen clock).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.3 to §5.6 ·
D114.

The loop of ``gateway/reminders/loop.py`` runs on a phase-4 promoted scratch
database, as the non-privileged app role, so FORCE RLS binds. Two
organizations. Each tick takes a frozen ``now``, so a whole ladder of three
days runs in one test.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_reminder_loop.py -v -rs

**Acceptance (§10.1 RM-2).** A reminder for 15:00 fires at 15:00, and its
check-back comes at 16:00. No answer moves it to the brief, then to one new
slot. After 2 cycles it is ``unconfirmed``. No step 2 to 5 lands in quiet
hours. Two workers never fire one row twice. A member move out of
``unconfirmed`` is a fresh start. A member who is not active fires nothing.
``/reschedule`` with ``smart`` gives a slot outside quiet hours and busy
blocks. The feed shows the card of each state. The flag off gives no loop
and an absent source.

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``loop._CLAIM_SQL``: drop ``FOR UPDATE SKIP LOCKED``.
  ``test_r8_two_workers_never_fire_one_row_twice``. (Drop ``SKIP LOCKED``
  alone and it stays green, by design: the lock and the due predicate of
  the one statement still hold the second worker, which then skips.)
* M2. ``slot.quiet``: answer False.
  ``test_r8_no_step_2_to_5_lands_in_quiet_hours``.
* M3. ``loop.run_org``: skip no member (drop the ``_is_active`` check).
  ``test_r8_a_member_who_is_not_active_fires_nothing``.
* M4. ``reminders._fresh_start``: answer ``[]``.
  ``test_r8_a_member_move_out_of_unconfirmed_is_a_fresh_start``.
* M5. ``loop._ladder``: drop the ``MAX_CYCLES`` test.
  ``test_r8_a_reminder_for_15_00_fires_at_15_00_and_checks_back_at_16_00``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.reach import classes
from gateway.reminders import loop, slot
from gateway.routes.projects import personal
from gateway.routes.projects import reminders as rm
from gateway.routes.shell import cache, feed, feed_rank, needs
from gateway.routes.shell.state import Day
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATION = next(
    p for p in (Path(__file__).resolve().parents[2] / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS reminders (" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
#: Org B. Each member has a settings row: UTC, 07:00 to 22:00, but Priya.
ALICE = f"alice-{TAG}@rm2.test"
BOB = f"bob-{TAG}@rm2.test"
#: Org B, with a settings row: Kolkata, 08:00 to 20:00.
PRIYA = f"priya-{TAG}@rm2.test"
#: Org A.
CAROL = f"carol-{TAG}@rm2.test"
MEMBERS = (ALICE, BOB, PRIYA, CAROL)
IST = ZoneInfo("Asia/Kolkata")
PRIYA_DAY = Day(IST, 8, 20)
ALICE_DAY = Day(ZoneInfo("UTC"), 7, 22)
BASE = "/projects/my/reminders"


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant of March 2099 in UTC. Tuesday 10 March is day 10."""
    return datetime(2099, 3, day, hour, minute, tzinfo=UTC)


def _ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2099, 3, day, hour, minute, tzinfo=IST)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


# ── The world ──────────────────────────────────────────────────────────────


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Four members in two orgs, the flag on, a fake directory, and the two
    orgs as the loop's discovery read."""
    p = promoted
    with p.admin_engine.begin() as c:
        for email, org in ((ALICE, p.org_b), (BOB, p.org_b), (PRIYA, p.org_b),
                           (CAROL, p.org_a)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": org})
        for email, org, zone, start, end in (
                (ALICE, p.org_b, "UTC", 7, 22), (BOB, p.org_b, "UTC", 7, 22),
                (CAROL, p.org_a, "UTC", 7, 22), (PRIYA, p.org_b, "Asia/Kolkata", 8, 20)):
            c.execute(text(
                "INSERT INTO user_settings (organization_id, user_id, timezone, "
                "day_start_hour, day_end_hour) VALUES (CAST(:o AS uuid), :u, :z, :s, :e)"),
                {"o": org, "u": email, "z": zone, "s": start, "e": end})
    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=True))
    ns = SimpleNamespace(p=p, active=set(MEMBERS), bumps=[])

    async def is_active(org: str, member: str) -> bool:
        return member in ns.active

    async def orgs() -> list[str]:
        return [p.org_a, p.org_b]

    async def bump(org: Any, email: Any) -> None:
        ns.bumps.append((str(org), str(email)))

    async def no_emit(event_type: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(loop, "_is_active", is_active)
    monkeypatch.setattr(loop, "_orgs", orgs)
    monkeypatch.setattr(cache, "bump_member_safe", bump)
    monkeypatch.setattr(personal, "emit", no_emit)
    loop._stopping.clear()
    yield ns
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM reminders WHERE member_email = ANY(:m)"),
                  {"m": list(MEMBERS)})
        c.execute(text("DELETE FROM pm_tasks WHERE created_by = ANY(:m)"),
                  {"m": list(MEMBERS)})
        c.execute(text("DELETE FROM pm_projects WHERE lower(personal_owner) = ANY(:m)"),
                  {"m": list(MEMBERS)})
        c.execute(text("DELETE FROM user_settings WHERE user_id = ANY(:m)"),
                  {"m": list(MEMBERS)})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:m)"), {"m": list(MEMBERS)})


def _seed(world: Any, member: str, fire_at: datetime, *, org: str | None = None,
          **cols: Any) -> str:
    """One reminder, as the superuser, past RLS. ``cols`` sets any column."""
    values = {"title": "Get a haircut", "kind": "action", "state": "scheduled",
              "important": False, "estimate_minutes": None, "cycles": 0,
              "first_fired_at": None, "next_check_at": None, **cols}
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO reminders (organization_id, member_email, title, kind, fire_at, "
            "state, important, estimate_minutes, cycles, first_fired_at, next_check_at) "
            "VALUES (CAST(:o AS uuid), :m, :title, :kind, :fire_at, :state, :important, "
            ":estimate_minutes, :cycles, :first_fired_at, :next_check_at) RETURNING id"),
            {"o": org or world.p.org_b, "m": member, "fire_at": fire_at, **values},
        ).scalar_one())


def _row(world: Any, rid: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT * FROM reminders WHERE id = CAST(:i AS uuid)"),
                         {"i": rid}).fetchone()


def _ledger(world: Any, rid: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT event, detail, organization_id FROM reminder_events "
            "WHERE reminder_id = CAST(:i AS uuid) ORDER BY at, id"), {"i": rid}))


def _events(world: Any, rid: str) -> list[str]:
    return [r.event for r in _ledger(world, rid)]


def _ticks(world: Any, *instants: datetime) -> list[dict[str, Counter]]:
    """One tick of the loop at each frozen instant, as the app role."""
    async def go() -> list[dict[str, Counter]]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return [await loop.run_tick(at) for at in instants]
    return asyncio.run(go())


# ── The vocabulary ─────────────────────────────────────────────────────────


def _check_values(sql: str, name: str) -> set[str]:
    found = re.search(rf"CONSTRAINT {name} CHECK \((.*?)\)\),?\s*$", sql, re.S | re.M)
    assert found, name
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def test_the_loop_writes_only_events_and_states_of_the_migration() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    events = _check_values(sql, "reminder_events_event")
    source = Path(loop.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'_ledger\(db, row, "([a-z_]+)"', source))
    assert written == set(loop.EVENTS) and written <= events
    states = set(re.findall(r"state = '([a-z_]+)'", source))
    assert states <= _check_values(sql, "reminders_state")
    assert {"due", "scheduled", "unconfirmed"} <= states


def test_the_three_card_kinds_reach_nobody_until_re_9() -> None:
    provider = needs.REGISTRY["reminders"]
    assert provider.kinds == feed.REMINDER_KINDS == frozenset(
        {"reminder_due", "reminder_check", "reminder_unconfirmed"})
    assert provider.features == ("projects",) and provider.needs is False
    assert provider.gated is True and provider.app == "calendar"
    for kind in feed.REMINDER_KINDS:
        assert classes.REACH_CLASS[kind] == "none"
        assert kind in feed_rank.BASE


# ── The ladder (§5.4) ──────────────────────────────────────────────────────


def test_r8_a_reminder_for_15_00_fires_at_15_00_and_checks_back_at_16_00(
    world, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_email_keep_separate import _assert_non_priv

    _assert_non_priv(app_engine)
    org = world.p.org_b
    rid = _seed(world, ALICE, _utc(10, 15))

    [early, fired] = _ticks(world, _utc(10, 14, 59), _utc(10, 15))
    assert early == {} and fired == {org: Counter(fired=1)}
    row = _row(world, rid)
    assert row.state == "due" and row.first_fired_at == _utc(10, 15)
    assert row.next_check_at == _utc(10, 16)  # 60 minutes: no estimate

    [wait, check] = _ticks(world, _utc(10, 15, 59), _utc(10, 16))
    assert wait == {} and check == {org: Counter(check_back=1)}
    # The brief is the next start of the member's day.
    assert _row(world, rid).next_check_at == _utc(11, 7)

    [night, brief] = _ticks(world, _utc(11, 6, 59), _utc(11, 7))
    assert night == {} and brief == {org: Counter(rescheduled=1)}
    row = _row(world, rid)
    # One new slot: the same time on the next day.
    assert row.state == "scheduled" and row.fire_at == _utc(11, 15)
    assert row.cycles == 1 and row.next_check_at is None
    assert row.first_fired_at == _utc(10, 15) and row.snoozes == 0

    # The second and the third cycle. After 2 cycles, the brief gives up.
    _ticks(world, _utc(11, 15), _utc(11, 16), _utc(12, 7), _utc(12, 15), _utc(12, 16))
    assert _row(world, rid).cycles == 2
    [gave_up, after] = _ticks(world, _utc(13, 7), _utc(20, 7))
    assert gave_up == {org: Counter(gave_up=1)} and after == {}
    row = _row(world, rid)
    assert row.state == "unconfirmed" and row.resolution is None
    assert row.next_check_at is None and row.first_fired_at == _utc(10, 15)

    ledger = _ledger(world, rid)
    assert [r.event for r in ledger] == [
        "fired", "check_back", "briefed", "rescheduled",
        "fired", "check_back", "briefed", "rescheduled",
        "fired", "check_back", "gave_up",
    ]
    assert all(r.detail["by"] == "metorite" for r in ledger)
    assert all(str(r.organization_id) == org for r in ledger)
    assert ledger[3].detail["from"].startswith("2099-03-10T15:00")
    assert ledger[3].detail["to"].startswith("2099-03-11T15:00")
    assert ledger[-1].detail["reason"] == "cycles"
    # Each write bumped the member's My Day version.
    assert set(world.bumps) == {(org, ALICE)}


def test_r8_72_hours_after_the_first_fire_it_gives_up(world) -> None:
    first = _utc(10, 15)
    late = _seed(world, ALICE, _utc(13, 15), state="due", first_fired_at=first,
                 next_check_at=_utc(13, 16))
    young = _seed(world, ALICE, _utc(13, 13), state="due", first_fired_at=first,
                  next_check_at=_utc(13, 14))
    _ticks(world, _utc(13, 14), _utc(13, 16))
    assert _row(world, young).state == "due" and _events(world, young) == ["check_back"]
    assert _row(world, late).state == "unconfirmed"
    [gave_up] = _ledger(world, late)
    assert gave_up.event == "gave_up" and gave_up.detail["reason"] == "72h"


def test_r8_no_step_2_to_5_lands_in_quiet_hours(world) -> None:
    """Priya's day is 08:00 to 20:00 in Kolkata."""
    org = world.p.org_b
    # 19:30 with an estimate of 60: the check-back would land at 21:00.
    evening = _seed(world, PRIYA, _ist(10, 19, 30), estimate_minutes=60)
    # 23:00 is quiet, and the member's own time still fires (D114.3).
    night = _seed(world, PRIYA, _ist(10, 23))
    # A check time in quiet hours, from before a change of the day hours.
    stale = _seed(world, PRIYA, _ist(10, 18), state="due", first_fired_at=_ist(10, 18),
                  next_check_at=_ist(10, 21, 30))

    [first, second, waits] = _ticks(world, _ist(10, 19, 30), _ist(10, 23), _ist(10, 23, 30))
    assert first[org]["fired"] == 1 and second[org]["fired"] == 1
    assert waits == {org: Counter(quiet=1)}
    assert _row(world, evening).next_check_at == _ist(11, 8)
    assert _row(world, night).next_check_at == _ist(11, 8)
    # The stale step waited, and nothing was written.
    assert _row(world, stale).next_check_at == _ist(10, 21, 30) and _events(world, stale) == []

    [morning] = _ticks(world, _ist(11, 8))
    assert morning == {org: Counter(check_back=3)}
    # The brief, the next day start, then a move. Then the next cycles.
    _ticks(world, _ist(11, 7, 59), _ist(12, 8))
    for rid in (evening, night, stale):
        row = _row(world, rid)
        assert row.state == "scheduled" and row.cycles == 1
        assert not slot.quiet(PRIYA_DAY, row.fire_at), (rid, row.fire_at)
    # 23:00 is quiet on every day, so its move is the first free quarter.
    assert _row(world, night).fire_at == _ist(12, 8, 15)
    assert _row(world, evening).fire_at == _ist(12, 19, 30)

    # Run every reminder to its end, a tick each quarter for two days.
    walk = [_ist(12, 8, 15) + timedelta(minutes=15 * n) for n in range(2 * 96)]
    _ticks(world, *walk)
    for rid in (evening, night, stale):
        assert _row(world, rid).state == "unconfirmed"
        for entry in _ledger(world, rid):
            if entry.event in ("check_back", "briefed", "rescheduled", "gave_up"):
                when = datetime.fromisoformat(entry.detail.get("at") or entry.detail["to"])
                assert not slot.quiet(PRIYA_DAY, when), (entry.event, when)
            if entry.event == "rescheduled":
                to = datetime.fromisoformat(entry.detail["to"])
                assert not slot.quiet(PRIYA_DAY, to), to


def test_r8_an_important_reminder_checks_back_four_times_then_briefs(world) -> None:
    rid = _seed(world, ALICE, _utc(10, 9), important=True)
    _ticks(world, _utc(10, 9))
    assert _row(world, rid).next_check_at == _utc(10, 9, 15)
    checks = [_utc(10, 9, 15), _utc(10, 10, 15), _utc(10, 11, 15), _utc(10, 12, 15)]
    nexts = [_utc(10, 10, 15), _utc(10, 11, 15), _utc(10, 12, 15), _utc(11, 7)]
    for at, then in zip(checks, nexts, strict=True):
        _ticks(world, at)
        assert _row(world, rid).next_check_at == then, at
    _ticks(world, _utc(11, 7))
    assert _row(world, rid).fire_at == _utc(11, 9) and _row(world, rid).cycles == 1
    assert _events(world, rid) == [
        "fired", "check_back", "check_back", "check_back", "check_back",
        "briefed", "rescheduled"]


def test_r8_a_notice_fires_once_and_waits(world) -> None:
    rid = _seed(world, ALICE, _utc(10, 15), kind="notice")
    _ticks(world, _utc(10, 15), _utc(10, 17), _utc(11, 7), _utc(14, 7))
    row = _row(world, rid)
    assert row.state == "due" and row.next_check_at is None and row.cycles == 0
    assert _events(world, rid) == ["fired"]


# ── Two workers, two orgs, the member (§5.4) ───────────────────────────────


class _Pairing:
    """Two workers meet here after a claim. Each waits a short time for the
    other, so a claim that two workers hold at once is held at once."""

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


def test_r8_two_workers_never_fire_one_row_twice(world, monkeypatch) -> None:
    org = world.p.org_b
    rids = [_seed(world, who, _utc(10, 15), title=f"Row {n}")
            for n, who in enumerate((ALICE, BOB) * 3)]
    pairing = _Pairing()
    real_day = loop.member_day

    async def after_claim(db: Any, member: str) -> Any:
        await pairing.meet()  # the claim is held here
        return await real_day(db, member)

    monkeypatch.setattr(loop, "member_day", after_claim)

    async def race() -> list[Counter]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(org)
            try:
                return list(await asyncio.gather(
                    loop.run_org(org, _utc(10, 15)), loop.run_org(org, _utc(10, 15))))
            finally:
                release_tenant(token)

    first, second = asyncio.run(race())
    # Non-vacuity: the two workers held two claims at the same time.
    assert pairing.met >= 1
    assert first["fired"] + second["fired"] == len(rids)
    assert first["fired"] > 0 and second["fired"] > 0
    for rid in rids:
        assert _events(world, rid) == ["fired"], rid
        assert _row(world, rid).state == "due"


def test_r8_each_org_fires_its_own_rows_in_its_own_tenant(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    theirs = _seed(world, CAROL, _utc(10, 15), org=a)
    ours = _seed(world, ALICE, _utc(10, 15))

    async def org_b_only() -> Counter:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(b)
            try:
                return await loop.run_org(b, _utc(10, 15))
            finally:
                release_tenant(token)

    # Bound to org B, the loop never sees the row of org A.
    assert asyncio.run(org_b_only()) == Counter(fired=1)
    assert _row(world, theirs).state == "scheduled" and _events(world, theirs) == []
    [both] = _ticks(world, _utc(10, 15))
    assert both == {a: Counter(fired=1)}
    for rid, org in ((theirs, a), (ours, b)):
        [fired] = _ledger(world, rid)
        assert fired.event == "fired" and str(fired.organization_id) == org


def test_r8_a_member_who_is_not_active_fires_nothing(world) -> None:
    world.active.discard(BOB)
    gone = _seed(world, BOB, _utc(10, 15))
    gone_due = _seed(world, BOB, _utc(10, 14), state="due", first_fired_at=_utc(10, 14),
                     next_check_at=_utc(10, 15))
    here = _seed(world, ALICE, _utc(10, 15))
    [got] = _ticks(world, _utc(10, 15))
    assert got == {world.p.org_b: Counter(fired=1, skipped=1)}
    assert _row(world, gone).state == "scheduled" and _events(world, gone) == []
    assert _row(world, gone_due).next_check_at == _utc(10, 15) and _events(world, gone_due) == []
    assert _events(world, here) == ["fired"]
    assert (world.p.org_b, BOB) not in world.bumps


def test_r8_the_member_check_is_the_departed_check_of_my_day(world, monkeypatch) -> None:
    """The real ``_is_active``: ``my_day_loop.departed`` on real rows. Erin is
    active. Dora was removed and is not purged, so her rows stay."""
    from acb_auth.access import mirror_identity_membership

    from tests.unit.test_my_day_loop_r8 import _seed_user, _unseed

    # Undo the fixture's fake directory: the loop's own check runs here.
    monkeypatch.undo()
    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=True))
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    p, org = world.p, world.p.org_b
    erin, dora = f"erin-{TAG}@rm2.test", f"dora-{TAG}@rm2.test"
    _seed_user(p, erin, org, "active")
    _seed_user(p, dora, org, "removed")
    try:
        here = _seed(world, erin, _utc(10, 15))
        gone = _seed(world, dora, _utc(10, 15))

        async def go() -> Counter:
            async with tenant_engine_scope(_dsn(p.app_url)):
                await mirror_identity_membership(email=erin, org_id=org, status="active")
                await mirror_identity_membership(email=dora, org_id=org, status="removed")
                token = bind_tenant(org)
                try:
                    return await loop.run_org(org, _utc(10, 15))
                finally:
                    release_tenant(token)

        assert asyncio.run(go()) == Counter(fired=1, skipped=1)
        assert _events(world, here) == ["fired"]
        assert _row(world, gone).state == "scheduled" and _events(world, gone) == []
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM reminders WHERE member_email = ANY(:m)"),
                      {"m": [erin, dora]})
        _unseed(p, erin)
        _unseed(p, dora)


def test_r8_the_flag_off_starts_no_loop_and_fires_nothing(world, monkeypatch) -> None:
    rid = _seed(world, ALICE, _utc(10, 15))
    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=False))

    async def go() -> tuple[bool, dict]:
        started = await loop.start_reminder_loop()
        return started, await loop.run_tick(_utc(10, 15))

    assert asyncio.run(go()) == (False, {})
    assert loop._task is None
    assert _row(world, rid).state == "scheduled" and _events(world, rid) == []
    with pytest.raises(needs.SourceAbsent):
        asyncio.run(feed._reminders(SimpleNamespace(email=ALICE)))


# ── The member's routes (RM-1 edits) ───────────────────────────────────────


def _app() -> FastAPI:
    from acb_auth import get_current_user
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user

    people = {m: member_user(m) for m in MEMBERS}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


@asynccontextmanager
async def _client(world: Any, org: str) -> AsyncIterator[httpx.AsyncClient]:
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://gateway.test") as client:
                yield client
    finally:
        release_tenant(token)


def _as(email: str) -> dict[str, str]:
    return {"x-user-email": email}


def _give_up(world: Any, rid: str, *, state: str = "unconfirmed") -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE reminders SET state = :s, cycles = 2, first_fired_at = :f, "
            "next_check_at = NULL WHERE id = CAST(:i AS uuid)"),
            {"s": state, "f": _utc(10, 15), "i": rid})


def test_r8_a_member_move_out_of_unconfirmed_is_a_fresh_start(world) -> None:
    later = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
    moves = (
        ("POST", "snooze", {"fire_at": later}, "snoozed"),
        ("POST", "reschedule", {"fire_at": "2099-04-01T09:00:00+00:00"}, "rescheduled"),
        ("PATCH", "", {"fire_at": "2099-04-02T09:00:00+00:00"}, "rescheduled"),
    )

    async def go() -> list[str]:
        made = []
        async with _client(world, world.p.org_b) as c:
            for n, (method, tail, body, _event) in enumerate(moves):
                resp = await c.post(BASE, json={"title": f"Move {n}",
                                                "fire_at": "2099-03-10T15:00:00+00:00"},
                                    headers=_as(ALICE))
                rid = resp.json()["id"]
                made.append(rid)
                _give_up(world, rid)
                path = f"{BASE}/{rid}/{tail}" if tail else f"{BASE}/{rid}"
                resp = await c.request(method, path, json=body, headers=_as(ALICE))
                assert resp.status_code == 200, resp.text
                out = resp.json()
                assert out["state"] == "scheduled" and out["cycles"] == 0
                assert out["first_fired_at"] is None and out["snoozes"] == 1
            # The control: a move out of `due` keeps the counts.
            resp = await c.post(BASE, json={"title": "Due",
                                            "fire_at": "2099-03-10T15:00:00+00:00"},
                                headers=_as(ALICE))
            due = resp.json()["id"]
            _give_up(world, due, state="due")
            resp = await c.post(f"{BASE}/{due}/snooze", json={"fire_at": later},
                                headers=_as(ALICE))
            assert resp.json()["cycles"] == 2 and resp.json()["first_fired_at"] is not None
            made.append(due)
        return made

    *fresh, due = asyncio.run(go())
    for rid, (_m, _t, _b, event) in zip(fresh, moves, strict=True):
        rows = _ledger(world, rid)
        assert [r.event for r in rows] == ["created", event]
        assert rows[1].detail["fresh_start"] is True and rows[1].detail["from_cycles"] == 2
        assert rows[1].detail["from_state"] == "unconfirmed"
    assert "fresh_start" not in _ledger(world, due)[1].detail
    # The fresh start counts again from the next fire.
    rid = fresh[1]
    _ticks(world, _utc(31, 9))  # 1 April 09:00 is after 31 March
    assert _row(world, rid).state == "scheduled"
    [fired] = _ticks(world, datetime(2099, 4, 1, 9, tzinfo=UTC))
    assert fired[world.p.org_b]["fired"] >= 1
    assert _row(world, rid).first_fired_at == datetime(2099, 4, 1, 9, tzinfo=UTC)


def test_r8_reschedule_smart_gives_a_slot_outside_quiet_hours_and_busy_blocks(world) -> None:
    """Priya's day is 08:00 to 20:00 in Kolkata. A block covers 10:00 on the
    next day, so option 2 moves to the day after. A reminder at 23:00 takes
    the next free gap from now."""
    block = (_ist(11, 9, 30), _ist(11, 11))

    async def go() -> tuple[dict, dict, list[Any]]:
        async with _client(world, world.p.org_b) as c:
            resp = await c.post("/projects/my/tasks", json={
                "title": "Dentist", "scheduled_start": block[0].isoformat(),
                "scheduled_end": block[1].isoformat()}, headers=_as(PRIYA))
            assert resp.status_code == 201, resp.text
            near = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
            resp = await c.post("/projects/my/tasks", json={
                "title": "Review", "scheduled_start": near.isoformat(),
                "scheduled_end": (near + timedelta(hours=3)).isoformat()}, headers=_as(PRIYA))
            assert resp.status_code == 201, resp.text
            day = await c.post(BASE, json={"title": "Call the bank",
                                           "fire_at": _ist(10, 10).isoformat()},
                               headers=_as(PRIYA))
            late = await c.post(BASE, json={"title": "Water the plants",
                                            "fire_at": _ist(10, 23).isoformat()},
                                headers=_as(PRIYA))
            one = await c.post(f"{BASE}/{day.json()['id']}/reschedule",
                               json={"fire_at": "smart"}, headers=_as(PRIYA))
            two = await c.post(f"{BASE}/{late.json()['id']}/reschedule",
                               json={"fire_at": " Smart "}, headers=_as(PRIYA))
            # Smart is for /reschedule only.
            for method, path in (("POST", f"{BASE}/{day.json()['id']}/snooze"),
                                 ("PATCH", f"{BASE}/{day.json()['id']}")):
                resp = await c.request(method, path, json={"fire_at": "smart"},
                                       headers=_as(PRIYA))
                assert resp.status_code == 422, resp.text
            assert one.status_code == 200 and two.status_code == 200, (one.text, two.text)
            return one.json(), two.json(), [block, (near, near + timedelta(hours=3))]

    one, two, busy = asyncio.run(go())
    now = datetime.now(UTC)
    assert datetime.fromisoformat(one["fire_at"]) == _ist(12, 10)
    for out in (one, two):
        at = datetime.fromisoformat(out["fire_at"])
        assert at > now and not slot.quiet(PRIYA_DAY, at), at
        assert not slot.inside(at, tuple(busy)), at
        assert out["snoozes"] == 1 and out["cycles"] == 0 and out["state"] == "scheduled"
        [_created, moved] = _ledger(world, out["id"])
        assert moved.event == "rescheduled" and moved.detail["slot"] == "smart"
        assert moved.detail["by"] == "member"


# ── The feed (§5.6) ────────────────────────────────────────────────────────


def test_r8_the_feed_shows_the_card_of_each_state(world, monkeypatch) -> None:
    from tests.unit._projects_fakes import member_user

    # The loop writes each state: 07:00 fires the moved one, 08:00 checks it
    # back and fires the bank, 09:00 checks the bank back and fires the
    # haircut. The cards are read from those rows.
    moved = _seed(world, ALICE, _utc(10, 7), title="Pay the rent", cycles=1)
    check = _seed(world, ALICE, _utc(10, 8), title="Call the bank")
    due = _seed(world, ALICE, _utc(10, 9), title="Get a haircut")
    gone = _seed(world, ALICE, _utc(10, 6), title="Old one", state="unconfirmed", cycles=2)
    waiting = _seed(world, ALICE, _utc(10, 18), title="Later today")
    done = _seed(world, ALICE, _utc(10, 7, 30), title="Closed", state="done")
    bobs = _seed(world, BOB, _utc(10, 9), title="Bob's own")
    _ticks(world, _utc(10, 7), _utc(10, 8), _utc(10, 9))
    assert _events(world, moved) == ["fired", "check_back"]
    assert _events(world, check) == ["fired", "check_back"]
    assert _events(world, due) == ["fired"] and _events(world, bobs) == ["fired"]

    now = _utc(10, 9, 40)
    monkeypatch.setattr(needs, "_now", lambda: now)

    async def go() -> needs.Found:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(world.p.org_b)
            try:
                return await feed._reminders(member_user(ALICE))
            finally:
                release_tenant(token)

    found = asyncio.run(go())
    signals = {s.signal_id: s for s in found.signals()}
    kinds = {sid.split(":", 1)[1]: s.kind for sid, s in signals.items()}
    assert kinds == {due: "reminder_due", check: "reminder_check",
                     moved: "reminder_check", gone: "reminder_unconfirmed"}
    assert waiting not in kinds and done not in kinds and bobs not in kinds
    # The next edge: the check-back of the haircut, 60 minutes after 09:00.
    assert found.next_edge == _utc(10, 10).isoformat()
    cards = {c["entity"]["id"]: c for c in feed.build_cards(
        list(signals.values()), now, ZoneInfo("UTC"), now.date())}
    assert cards[due]["why"] == "Reminder for 09:00 today." and cards[due]["group"] == "now"
    assert cards[check]["why"] == "Is it done? It was set for 08:00 today."
    assert cards[moved]["why_key"] == "reminder_moved"
    assert cards[gone]["why_key"] == "reminder_stopped"
    assert {cards[r]["group"] for r in (check, moved, gone)} == {"needs_you"}
    for card in cards.values():
        assert card["href"] == f"/chat?reminder={card['entity']['id']}"
        assert card["app"] == "calendar" and card["acts"][0]["way"] == "open"
