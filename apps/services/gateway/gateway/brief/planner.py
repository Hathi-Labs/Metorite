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
brief slot in the next :data:`HORIZON`, and enqueues ``brief.run`` with
``run_at`` = the slot minus :data:`LEAD`, and the dedupe key
``brief:<member>:brief:<local_date>``. Two planner ticks see the same slot,
and the dedupe key keeps one live job. A slot whose ``brief_runs`` row exists
already ran, or is running, and gets no second job.

**Who (Q1, owner 2026-10-29).** Every active member of the org, minus the
members who turned the brief off. The list is one read of ``app_user``
inside ``tenant_session(org)``. The org comes from the worker, never from
the payload. The preferences come from the profile seam
(``acb_common.assistant_profile.app_prefs``, ``personalization.md`` §12.6):
``enabled``, ``brief_time`` and ``closeout_time``.

**When (Q5).** The slot is the member's brief time (default 07:30) on the
member's calendar date in their zone. The zone and the hours come from
``Day`` (``routes/shell/state.py``), the one reader of them. A day that is
not a working day gives no run. A member whose identity read fails is
skipped on this tick and logged, and the next tick tries again: the horizon
is two ticks wide.

The close-out time is stored and read here, and BR-2b uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

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
#: A slot inside this window of the planner's clock gets a run.
HORIZON = timedelta(minutes=30)
#: The run starts this long before the slot.
LEAD = timedelta(minutes=5)
#: The tries of a plan job, and of a run job (a retry is safe: the
#: ``brief_runs`` UNIQUE and the claim positions hold one set).
PLAN_ATTEMPTS = 3
RUN_ATTEMPTS = 3

#: The active members of the bound org, with the stored profile of each.
#: ``user_settings.user_id`` is the address, as ``/tasks/settings`` writes
#: it. The two binds, RLS and the predicate, are the shape of the queue.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
_MEMBERS_SQL = (
    "SELECT lower(u.email), s.assistant_profile FROM app_user u "
    "LEFT JOIN user_settings s ON s.organization_id = u.organization_id "
    "AND lower(s.user_id) = lower(u.email) "
    f"WHERE u.organization_id = {_BOUND_ORG} AND u.status = 'active' "
    "ORDER BY lower(u.email)"
)


@dataclass(frozen=True)
class Member:
    """One eligible member and their brief preferences."""

    email: str
    enabled: bool
    brief_time: time
    closeout_time: time


def _time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def member_of(email: str, profile: Any) -> Member:
    """*email* with the brief preferences of the stored *profile*. A key
    that is absent or of the wrong shape takes its declared default."""
    prefs = assistant_profile.app_prefs(profile, "brief")
    return Member(email.strip().lower(), bool(prefs["enabled"]),
                  _time(prefs["brief_time"]), _time(prefs["closeout_time"]))


async def members(db: Any) -> list[Member]:
    """The active members of the bound org, in address order."""
    rows = (await db.execute(text(_MEMBERS_SQL))).all()
    return [member_of(str(r[0]), r[1]) for r in rows]


def slot(day: Any, at: time, now: datetime) -> tuple[date, datetime] | None:
    """The slot of *at* on the member's calendar date, when it falls in
    ``[now, now + HORIZON)``: the date and the instant. The date is the
    calendar date in the member's zone, today or tomorrow, and never the
    feed day (``Day.feed_day`` moves with the day-start hour)."""
    today = now.astimezone(day.tz).date()
    for local_date in (today, today + timedelta(days=1)):
        when = datetime.combine(local_date, at, tzinfo=day.tz)
        if now <= when < now + HORIZON:
            return local_date, when
    return None


def dedupe_key(member: str, kind: str, local_date: date) -> str:
    """``brief:<member>:<kind>:<local_date>`` (``core_brief.md`` §4.1)."""
    return f"brief:{member}:{kind}:{local_date.isoformat()}"


def _now() -> datetime:
    return datetime.now(UTC)


async def _due(org: str, people: list[Member], now: datetime) -> list[tuple[Member, date, datetime]]:
    """The members with a slot in the window, on a working day. Each read
    runs as the member, through the app's own read, with no session held."""
    from gateway import my_day_loop
    from gateway.reach import loop
    from gateway.routes.shell import feed

    out: list[tuple[Member, date, datetime]] = []
    for m in people:
        if not m.enabled:
            continue
        user = await loop.resolve(org, m.email)
        if isinstance(user, str):
            _log.warning("brief.plan_skipped", reason=user)
            continue
        day = await my_day_loop._read_day(org, user)
        found = slot(day, m.brief_time, now)
        if found is None:
            continue
        local_date, when = found
        if not await feed._working_day(user, local_date):
            continue
        out.append((m, local_date, when))
    return out


async def plan(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`PLAN_KIND`. It enqueues one
    ``brief.run`` for each member with a slot in the window. *payload* is
    empty: the org is the worker's, and the members are the org's."""
    now = _now()
    if not flags.org_allowed(org):
        _log.info("brief.plan_off")
        due: list[tuple[Member, date, datetime]] = []
    else:
        async with tenant_session(org) as db:
            people = await members(db)
        due = await _due(org, people, now)
    async with tenant_session(org) as db:
        if not await queue.hold(db, claim):
            return
        planned = 0
        for m, local_date, when in due:
            if await store.read_run(db, m.email, kind=KIND, local_date=local_date) is not None:
                continue  # the slot ran, or its job runs now
            out = await queue.enqueue(
                db, RUN_KIND,
                {"member": m.email, "local_date": local_date.isoformat(),
                 "slot_at": when.isoformat()},
                run_at=when - LEAD, dedupe_key=dedupe_key(m.email, KIND, local_date),
                max_attempts=RUN_ATTEMPTS, now=now)
            planned += int(out.created)
        await queue.complete(db, claim, now=now)
    _log.info("brief.planned", members=len(due), new=planned)


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
