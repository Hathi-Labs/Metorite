"""The planner of the brief: who gets a run, and when (WS-BRIEF BR-2a-1,
``core_brief.md`` §4.1 and §9).

**The seed.** :func:`seed` is a ``worker.seed_hooks`` hook. Each 15-minute
bucket it enqueues one ``brief.plan`` job for each org that
``flags.org_allowed`` admits, with the dedupe key ``plan:<bucket>``. An org
that the switch or the list refuses gets no job.

**The plan.** ``brief.plan`` is a detached handler (``docs/job_queue.md``
§5a): the day read of a member opens the member's own sessions
(``my_day_loop._read_day`` and ``feed._working_day``), and a session
handler may open no second session. For each eligible member it finds the
brief slot in the window ``[now - LOOKBACK, now + HORIZON)``, and enqueues
``brief.run`` with ``run_at`` = the slot minus :data:`LEAD`, and the dedupe
key ``brief:<member>:brief:<local_date>``. Two planner ticks see the same
slot, and the dedupe key keeps one live job. A slot whose ``brief_runs``
row exists already ran, or is running, and gets no second job.

**A planner outage.** The look-back (:data:`LOOKBACK`, 2 hours) plans a slot
that no tick planned, late. A slot that is older than the look-back, by up
to :data:`MISSED_SCAN`, with no row, gets a ``failed`` row with the error
``slot_missed``, and the plan logs ``brief.slot_missed`` (warning) once. So
a missed brief always leaves a record for BR-2d.

**Who (Q1, owner 2026-10-29).** Every active member of the org, minus the
members who turned the brief off. The list and the preferences are ONE read
of the profile seam, ``acb_common.assistant_profile.members_app_prefs``
(``personalization.md`` §12.6): ``enabled``, ``brief_time`` and
``closeout_time``, one entry for each member in any case. The org comes
from the worker, never from the payload.

**When (Q5).** The slot is the member's brief time (default 07:30) on the
member's calendar date in their zone. The zone comes from ``Day``
(``routes/shell/state.py``). A member with no zone reads UTC there, and the
planner then takes the org's default zone, ``default_timezone`` of the
work-schedule policy (``work_schedule.load_policy``). A day that is not a
working day gives no run.

**The cost.** The stored zones (one read of ``user_settings.timezone``)
and the rows of ``brief_runs`` pick the members whose slot can fall in the
window and has no row. Only those get the identity and day reads. One
member whose read fails is logged and skipped, and the next tick tries
again: the window is wider than two ticks.

The close-out time is stored and read here, and BR-2b uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from acb_common import assistant_profile, get_logger
from acb_common.db import tenant_session
from sqlalchemy import text

from gateway.brief import flags, store
from gateway.jobs import queue

_log = get_logger("brief.planner")

#: The two kinds of the queue.
PLAN_KIND = "brief.plan"
RUN_KIND = "brief.run"
#: The kind of the run that this slice plans (``store.KINDS``).
KIND = "brief"
#: A slot inside this window ahead of the planner's clock gets a run.
HORIZON = timedelta(minutes=30)
#: A slot this far behind the planner's clock, with no row, still gets a
#: run: a planner outage shorter than this loses no brief.
LOOKBACK = timedelta(hours=2)
#: A slot older than the look-back, by up to this much, with no row, gets a
#: ``failed`` row (``slot_missed``) and one warning.
MISSED_SCAN = timedelta(hours=6)
#: The error of a slot that no tick planned in time.
MISSED = "slot_missed"
#: The run starts this long before the slot.
LEAD = timedelta(minutes=5)
#: The tries of a plan job, and of a run job (a retry is safe: the
#: ``brief_runs`` UNIQUE and the claim positions hold one set).
PLAN_ATTEMPTS = 3
RUN_ATTEMPTS = 3

#: The stored zones of the bound org, by address in lower case. Only the
#: zone: the prefilter of the plan, never the profile. The two binds, RLS
#: and the predicate, are the shape of the queue.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
_ZONES_SQL = (
    "SELECT lower(user_id), timezone FROM user_settings "
    f"WHERE organization_id = {_BOUND_ORG}"
)


@dataclass(frozen=True)
class Member:
    """One eligible member and their brief preferences."""

    email: str
    enabled: bool
    brief_time: time
    closeout_time: time


@dataclass(frozen=True)
class Slot:
    """One slot that a plan writes: a run to enqueue, or a missed slot."""

    member: Member
    local_date: date
    at: datetime
    missed: bool = False


def _time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def member_of(email: str, prefs: dict[str, Any]) -> Member:
    """*email* with the brief preferences *prefs* of ``app_prefs``."""
    return Member(email.strip().lower(), bool(prefs["enabled"]),
                  _time(prefs["brief_time"]), _time(prefs["closeout_time"]))


async def members(db: Any, org: str) -> list[Member]:
    """The active members of the bound org, in address order, one for each
    address in any case (``assistant_profile.members_app_prefs``)."""
    rows = await assistant_profile.members_app_prefs(db, org, KIND)
    return [member_of(email, prefs) for email, prefs in rows]


def member_zone(tz: ZoneInfo, org_zone: ZoneInfo) -> ZoneInfo:
    """The member's zone, or *org_zone* when the member has none. A member
    with no zone, or a zone that does not exist, reads UTC
    (``state.zone_of``), so UTC means "no zone" here. A member who chose
    UTC in an org with another default gets the org's zone: the column's
    DEFAULT is UTC, and a stored UTC cannot tell the two apart."""
    return org_zone if tz.key == "UTC" else tz


def slot(tz: ZoneInfo, at: time, now: datetime) -> tuple[date, datetime] | None:
    """The slot of *at* on the member's calendar date in *tz*, when it falls
    in ``[now - LOOKBACK, now + HORIZON)``: the date and the instant. The
    date is the calendar date in the member's zone, and never the feed day
    (``Day.feed_day`` moves with the day-start hour)."""
    today = now.astimezone(tz).date()
    for local_date in (today - timedelta(days=1), today, today + timedelta(days=1)):
        when = datetime.combine(local_date, at, tzinfo=tz)
        if now - LOOKBACK <= when < now + HORIZON:
            return local_date, when
    return None


def missed(tz: ZoneInfo, at: time, now: datetime) -> tuple[date, datetime] | None:
    """The slot of *at* in ``[now - LOOKBACK - MISSED_SCAN, now - LOOKBACK)``:
    a slot that the look-back no longer plans."""
    today = now.astimezone(tz).date()
    for local_date in (today, today - timedelta(days=1)):
        when = datetime.combine(local_date, at, tzinfo=tz)
        if now - LOOKBACK - MISSED_SCAN <= when < now - LOOKBACK:
            return local_date, when
    return None


def dedupe_key(member: str, kind: str, local_date: date) -> str:
    """``brief:<member>:<kind>:<local_date>`` (``core_brief.md`` §4.1)."""
    return f"brief:{member}:{kind}:{local_date.isoformat()}"


def _now() -> datetime:
    return datetime.now(UTC)


def _found(tz: ZoneInfo, m: Member, now: datetime) -> list[tuple[date, datetime, bool]]:
    """The slot in the window and the missed slot of *m* in *tz*."""
    out: list[tuple[date, datetime, bool]] = []
    for found, is_missed in ((slot(tz, m.brief_time, now), False),
                             (missed(tz, m.brief_time, now), True)):
        if found is not None:
            out.append((found[0], found[1], is_missed))
    return out


async def _org_zone(db: Any) -> ZoneInfo:
    """The org's default zone: ``default_timezone`` of the work-schedule
    policy of the bound org, UTC when it has none."""
    from gateway.routes.shell.state import zone_of
    from gateway.work_schedule import load_policy

    return zone_of((await load_policy(db)).get("default_timezone"))


async def _candidates(db: Any, org: str, now: datetime) -> tuple[list[Member], ZoneInfo]:
    """The enabled members whose slot can fall in the window, or was missed,
    under any zone that the member's day can read, and whose row for that
    date does not exist. The zones are each stored zone of the member in
    any case, and the org's zone. This is a superset of the day read."""
    from gateway.routes.shell.state import zone_of

    people = await members(db, org)
    org_zone = await _org_zone(db)
    stored: dict[str, set[str | None]] = {}
    for email, zone in (await db.execute(text(_ZONES_SQL))).all():
        stored.setdefault(str(email), set()).add(zone)
    out: list[Member] = []
    for m in people:
        if not m.enabled:
            continue
        zones = {member_zone(zone_of(z), org_zone) for z in stored.get(m.email, set())}
        dates = {d for tz in zones | {org_zone} for d, _at, _m in _found(tz, m, now)}
        for d in sorted(dates):
            if await store.read_run(db, m.email, kind=KIND, local_date=d) is None:
                out.append(m)
                break
    return out, org_zone


async def _member_slots(org: str, m: Member, org_zone: ZoneInfo,
                        now: datetime) -> list[Slot]:
    """The slots of one member, through the app's own reads, with no
    session held."""
    from gateway import my_day_loop
    from gateway.reach import loop
    from gateway.routes.shell import feed

    user = await loop.resolve(org, m.email)
    if isinstance(user, str):
        _log.warning("brief.plan_skipped", reason=user)
        return []
    day = await my_day_loop._read_day(org, user)
    tz = member_zone(day.tz, org_zone)
    out: list[Slot] = []
    for local_date, when, is_missed in _found(tz, m, now):
        if await feed._working_day(user, local_date):
            out.append(Slot(m, local_date, when, is_missed))
    return out


async def _due(org: str, people: list[Member], org_zone: ZoneInfo,
               now: datetime) -> list[Slot]:
    """The slots of *people*. One member whose read fails is logged and
    skipped, and the others are planned."""
    out: list[Slot] = []
    for m in people:
        try:
            out += await _member_slots(org, m, org_zone, now)
        except Exception as exc:
            _log.warning("brief.plan_member_failed", error=type(exc).__name__)
    return out


async def _write(db: Any, s: Slot, now: datetime) -> bool:
    """Enqueue the run of slot *s*, or record it as missed. True when a new
    job or a new row went in. A slot that has its row gets nothing."""
    if await store.read_run(db, s.member.email, kind=KIND, local_date=s.local_date) is not None:
        return False  # the slot ran, or its job runs now
    if s.missed:
        created = await store.create_run(db, s.member.email, kind=KIND,
                                         local_date=s.local_date, slot_at=s.at)
        if not created.created:
            return False
        await store.set_state(db, s.member.email, created.run.id, "failed", now=now,
                              error=MISSED, from_state="planned")
        _log.warning("brief.slot_missed", local_date=s.local_date.isoformat(),
                     late_minutes=int((now - s.at).total_seconds() // 60))
        return True
    out = await queue.enqueue(
        db, RUN_KIND,
        {"member": s.member.email, "local_date": s.local_date.isoformat(),
         "slot_at": s.at.isoformat()},
        run_at=s.at - LEAD, dedupe_key=dedupe_key(s.member.email, KIND, s.local_date),
        max_attempts=RUN_ATTEMPTS, now=now)
    return out.created


async def plan(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`PLAN_KIND`. It enqueues one
    ``brief.run`` for each member with a slot in the window, and records
    each missed slot. *payload* is empty: the org is the worker's, and the
    members are the org's."""
    now = _now()
    due: list[Slot] = []
    if not flags.org_allowed(org):
        _log.info("brief.plan_off")
    else:
        async with tenant_session(org) as db:
            people, org_zone = await _candidates(db, org, now)
        due = await _due(org, people, org_zone, now)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return
        written = 0
        for s in due:
            written += int(await _write(db, s, now))
        await queue.complete(db, claim, now=now)
    _log.info("brief.planned", members=len({s.member.email for s in due}), new=written)


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook: one ``brief.plan`` job for each org
    and 15-minute bucket, when the org gets a brief."""
    from gateway.jobs import worker

    if not flags.org_allowed(org):
        return
    bucket = worker.seed_bucket(now)
    async with tenant_session(org) as db:
        await queue.enqueue(db, PLAN_KIND, {}, dedupe_key=f"plan:{bucket}",
                            max_attempts=PLAN_ATTEMPTS, now=now)
