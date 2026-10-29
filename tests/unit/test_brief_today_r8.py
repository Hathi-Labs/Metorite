"""WS-BRIEF BR-2c-1 — ``GET /brief/today`` on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §5 item 3, §10 row 5. Code:
``gateway/routes/brief_today.py``, ``gateway/brief/store.py``.

The real handler and the real store run on a phase-4 promoted scratch
database, as the non-privileged app role, so FORCE RLS binds. Two orgs, a
frozen clock. Only the member's ``Day`` is a fake (``my_day_loop._read_day``
answers the zone that the test sets). The org's default zone is the real
read of the work-schedule policy. ``queue.enqueue`` fails the test when
anything calls it.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_today_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**Acceptance (BR-2c-1).**

a. A ``ready`` run of member A is served to A with its claims in order. The
   same request is 404 for member B of the org, and for the same address
   in org B. An ``empty`` and a ``failed`` run are 404.
b. The GET is a pure read. A ``ready`` and a ``delivered`` run are served
   with the stored state, and the row is the same before and after the
   read, with the switch on and with it off.
c. A member in Asia/Kolkata and one in America/New_York each read their own
   run on their own calendar date, at one instant.
d. A run of the day before is served after a zone change, and after a
   brief time near midnight that the look-back wrote on the day before.
   An ``empty`` run of today is 404, and the run of the day before does
   not come back. A run whose slot is still ahead is not served.
f. No read enqueues a job, and the ``jobs`` table does not change.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``store._READ_RUN_SQL``: drop ``AND member_email = :m``.
  ``test_r8_a_ready_run_is_served_to_its_member_only`` goes red.
* M2. ``brief_today.read_today``: move a served ``ready`` run to
  ``delivered`` (a write in the GET).
  ``test_r8_the_get_is_a_pure_read[ready]`` goes red.
* M3. ``brief_today.brief_local_date``: answer ``day.feed_day(now)``.
  ``test_r8_each_member_reads_their_own_run_on_their_own_date`` goes red,
  with two more.
* M4. ``brief_today.LOOK_BACK_DAYS = 0``.
  ``test_r8_a_run_of_the_day_before_is_served`` goes red.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.brief import flags, store
from gateway.jobs import queue
from gateway.routes import brief_today
from gateway.routes.shell import state
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@br2c.test"
BEN = f"ben.{TAG}@br2c.test"
NED = f"ned.{TAG}@br2c.test"
LIKE = f"%.{TAG}@br2c.test"

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
#: 08:00 on Tuesday 10 March 2099 in Kolkata, and 22:30 on Monday 9 March in
#: New York. With a 09:00 day start, the Kolkata feed day is still 9 March,
#: so a read by the feed day would miss the morning brief.
NOW = datetime(2099, 3, 10, 2, 30, tzinfo=UTC)
LATER = datetime(2099, 3, 10, 3, 45, tzinfo=UTC)
D = date(2099, 3, 10)
D_NY = date(2099, 3, 9)
SLOT = datetime(2099, 3, 10, 2, 0, tzinfo=UTC)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """The switch on for org A, the member's zone by address, the frozen
    clock, and a queue that refuses every call."""
    p = promoted
    monkeypatch.setattr(flags, "get_settings", lambda: _on(p.org_a))
    zones = {BEN: KOLKATA, ANA: KOLKATA, NED: NEW_YORK}

    async def read_day(org, user):
        return state.Day(zones.get(user.email, KOLKATA), 9, 18)

    async def refuse(*a, **kw):
        raise AssertionError("GET /brief/today must never enqueue")

    from gateway import my_day_loop

    monkeypatch.setattr(my_day_loop, "_read_day", read_day)
    monkeypatch.setattr(queue, "enqueue", refuse)
    monkeypatch.setattr(brief_today, "_now", lambda: NOW)
    yield p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE member_email LIKE :t"), {"t": LIKE})


def _on(org: str, **kw: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(**{"brief_enabled": True, "brief_orgs": org,
                              "brief_in_app": True, **kw})


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(["feature:chat"], roles=["member"]))


def _claim(position: int, text_: str) -> store.ClaimIn:
    return store.ClaimIn(
        position=position, section="needs_you", text=text_, source_app="email",
        source_kind="needs_reply", source_id=f"email:t{position}",
        href=f"/email?thread=t{position}")


async def _seed(org: str, member: str, *, local_date: date = D, state_: str = "ready",
                lead: str = "Your day, Tue 10 Mar. 3 things need you.",
                lines: tuple[str, ...] = ("first", "second", "third"),
                slot_at: datetime = SLOT) -> str:
    """One run of *member* in *org*, moved to *state_* through the store's
    own guarded moves. The claims go in out of order, on purpose."""
    async with tenant_session(org) as db:
        run = (await store.create_run(db, member, kind="brief", local_date=local_date,
                                      slot_at=slot_at)).run
        if state_ in ("empty", "failed"):
            await store.set_state(db, member, run.id, state_, now=SLOT, from_state="planned",
                                  error="slot_missed" if state_ == "failed" else None)
            return run.id
        claims = [_claim(i, t) for i, t in enumerate(lines)]
        await store.add_claims(db, member, run.id, list(reversed(claims)))
        await store.set_state(db, member, run.id, "gathered", now=SLOT, from_state="planned")
        await store.set_lead(db, member, run.id, lead, source="template")
        await store.set_state(db, member, run.id, "ready", now=SLOT, from_state="gathered")
        if state_ == "delivered":
            await store.set_state(db, member, run.id, "delivered", now=SLOT,
                                  from_state="ready")
    return run.id


async def _get(user: Any, kind: str = "brief") -> Any:
    """The real handler, under the session's bound tenant. Answers the body,
    or the status of the refusal."""
    token = bind_tenant(user.organization_id)
    try:
        return await brief_today.get_today(kind=kind, user=user)
    except HTTPException as exc:
        return exc.status_code
    finally:
        release_tenant(token)


def _row(world: Any, run_id: str) -> Any:
    with world.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT state, delivered_at FROM brief_runs WHERE id = CAST(:i AS uuid)"),
            {"i": run_id}).first()


def _whole(world: Any, run_id: str) -> tuple[Any, list[Any]]:
    """The whole row of the run and its claims, every column."""
    with world.admin_engine.connect() as c:
        run = c.execute(text("SELECT * FROM brief_runs WHERE id = CAST(:i AS uuid)"),
                        {"i": run_id}).first()
        claims = list(c.execute(text(
            "SELECT * FROM brief_claims WHERE run_id = CAST(:i AS uuid) ORDER BY position"),
            {"i": run_id}).fetchall())
    return run, claims


def _jobs(world: Any) -> int:
    with world.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM jobs")).scalar_one())


async def test_r8_a_ready_run_is_served_to_its_member_only(world) -> None:
    p = world
    jobs = _jobs(p)
    async with tenant_engine_scope(_dsn(p.app_url)):
        run_id = await _seed(p.org_a, ANA)
        ben = await _get(_user(BEN, p.org_a))
        # The same address in org B: the bound org scopes the read.
        monkey_org_b = _user(ANA, p.org_b)
        flags_b = await _with_settings(_on(f"{p.org_a},{p.org_b}"), _get, monkey_org_b)
        ana = await _get(_user(ANA, p.org_a))
    assert ben == 404
    assert flags_b == 404
    assert isinstance(ana, dict), ana
    assert [c["text"] for c in ana["claims"]] == ["first", "second", "third"]
    assert [c["position"] for c in ana["claims"]] == [0, 1, 2]
    assert ana["claims"][0] == {"position": 0, "section": "needs_you", "text": "first",
                                "source_app": "email", "source_kind": "needs_reply",
                                "href": "/email?thread=t0"}
    assert (ana["kind"], ana["local_date"], ana["state"], ana["lead"], ana["lead_source"]) == (
        "brief", "2099-03-10", "ready", "Your day, Tue 10 Mar. 3 things need you.",
        "template")
    assert ana["ready_at"] == SLOT.isoformat()
    # No read moved the run.
    assert _row(p, run_id).state == "ready" and _row(p, run_id).delivered_at is None
    assert _jobs(p) == jobs


async def _with_settings(settings: Any, fn: Any, *args: Any) -> Any:
    """Call *fn* with *settings* as the brief switches, then put them back."""
    old = flags.get_settings
    flags.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        return await fn(*args)
    finally:
        flags.get_settings = old  # type: ignore[assignment]


@pytest.mark.parametrize("state_", ["empty", "failed"])
async def test_r8_an_empty_or_failed_run_is_404(world, state_) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        run_id = await _seed(p.org_a, BEN, state_=state_)
        got = await _get(_user(BEN, p.org_a))
    assert got == 404
    assert _row(p, run_id).state == state_ and _row(p, run_id).delivered_at is None


async def test_r8_no_run_and_an_unknown_kind(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        none = await _get(_user(BEN, p.org_a))
        closeout = await _get(_user(BEN, p.org_a), kind="closeout")
        bad = await _get(_user(BEN, p.org_a), kind="weekly")
    assert (none, closeout, bad) == (404, 404, 422)


@pytest.mark.parametrize("state_", ["ready", "delivered"])
async def test_r8_the_get_is_a_pure_read(world, monkeypatch, state_) -> None:
    """The run sets ``delivered``, and the GET never writes. The row and its
    claims are the same before and after two reads, with the switch on and
    with it off."""
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        run_id = await _seed(p.org_a, ANA, state_=state_)
        before = _whole(p, run_id)
        first = await _get(_user(ANA, p.org_a))
        monkeypatch.setattr(brief_today, "_now", lambda: LATER)
        second = await _get(_user(ANA, p.org_a))
        after_on = _whole(p, run_id)
        off = await _with_settings(_on(p.org_a, brief_in_app=False), _get, _user(ANA, p.org_a))
    assert first["state"] == second["state"] == state_
    assert first == second
    assert off == 404
    assert after_on == before == _whole(p, run_id)
    assert before[0].state == state_


async def test_r8_each_member_reads_their_own_run_on_their_own_date(world) -> None:
    """At one instant, Ana in Kolkata is on 10 March and Ned in New York on
    9 March. Each has a decoy run on the other date, and each reads only the
    run of their own calendar date."""
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _seed(p.org_a, ANA, local_date=D, lines=("ana today",))
        await _seed(p.org_a, ANA, local_date=D_NY, lines=("ana yesterday",),
                    lead="Your day, Mon 9 Mar. 1 thing needs you.",
                    slot_at=SLOT - timedelta(days=1))
        await _seed(p.org_a, NED, local_date=D_NY, lines=("ned today",),
                    lead="Your day, Mon 9 Mar. 1 thing needs you.")
        await _seed(p.org_a, NED, local_date=D, lines=("ned tomorrow",))
        ana = await _get(_user(ANA, p.org_a))
        ned = await _get(_user(NED, p.org_a))
    assert (ana["local_date"], [c["text"] for c in ana["claims"]]) == ("2099-03-10", ["ana today"])
    assert (ned["local_date"], [c["text"] for c in ned["claims"]]) == ("2099-03-09", ["ned today"])


async def test_r8_the_switch_off_is_404_and_delivers_nothing(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        run_id = await _seed(p.org_a, ANA)
        off = await _with_settings(_on(p.org_a, brief_in_app=False), _get, _user(ANA, p.org_a))
        not_listed = await _with_settings(_on(""), _get, _user(ANA, p.org_a))
    assert (off, not_listed) == (404, 404)
    row = _row(p, run_id)
    assert row.state == "ready" and row.delivered_at is None


@pytest.mark.parametrize("case", ["zone_change", "near_midnight"])
async def test_r8_a_run_of_the_day_before_is_served(world, case) -> None:
    """A read at 08:00 in Kolkata on 10 March finds a run dated 9 March.

    * ``zone_change``: Ana was in New York at 07:30 on 9 March, so the run
      has the date of New York. She is in Kolkata now.
    * ``near_midnight``: a brief time of 23:45 on 9 March in Kolkata, which
      the planner's look-back ran after midnight, on the date of the slot.
    """
    p = world
    slot = (datetime(2099, 3, 9, 7, 30, tzinfo=NEW_YORK) if case == "zone_change"
            else datetime(2099, 3, 9, 23, 45, tzinfo=KOLKATA))
    async with tenant_engine_scope(_dsn(p.app_url)):
        run_id = await _seed(p.org_a, ANA, local_date=D_NY, slot_at=slot,
                             lines=("from the ninth",),
                             lead="Your day, Mon 9 Mar. 1 thing needs you.")
        got = await _get(_user(ANA, p.org_a))
    assert isinstance(got, dict), got
    assert (got["local_date"], [c["text"] for c in got["claims"]]) == (
        "2099-03-09", ["from the ninth"])
    assert _row(p, run_id).state == "ready"


async def test_r8_an_empty_run_of_today_does_not_bring_back_the_day_before(world) -> None:
    """The newest run is the one to serve. An ``empty`` run of today is 404,
    and the ``ready`` run of the day before does not come back."""
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _seed(p.org_a, ANA, local_date=D_NY, slot_at=SLOT - timedelta(days=1),
                    lines=("yesterday",), lead="Your day, Mon 9 Mar. 1 thing needs you.")
        before = await _get(_user(ANA, p.org_a))
        await _seed(p.org_a, ANA, state_="empty")
        after = await _get(_user(ANA, p.org_a))
    # Before today's run, the newest run is the one of the day before.
    assert isinstance(before, dict) and before["local_date"] == "2099-03-09"
    assert after == 404


async def test_r8_a_run_whose_slot_is_ahead_is_not_served(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _seed(p.org_a, ANA, slot_at=NOW + timedelta(minutes=5))
        got = await _get(_user(ANA, p.org_a))
    assert got == 404
