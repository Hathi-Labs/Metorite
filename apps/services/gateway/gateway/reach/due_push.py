"""The morning summary push of due tasks (RX-5).

Owner directive, 2026-11-01. Spec: ``calendar_events_reminders.md`` §5.11.

A due date is a day and a reminder is a moment, so a due task never fires at an
hour. The member gets ONE push in the morning: "3 due today, 1 overdue", with
the first title. A tap opens My Day. The rest of the day stays quiet.

**Two steps, as a reminder push has.**

* :func:`plan_org` — the plan phase of ``reminders.loop.run_tick`` calls it once
  for each org, after the steps of every org and before the send phase. It
  makes no network call. It writes one planned ``web_push`` row through
  ``outbox.plan_push``.
* :func:`read_facts` — the claim of ``push_channel._send_one`` calls it. It
  reads the tasks again on the claim's own session, and builds the message.
  There is no second send path: ``send_planned``, ``claim_push``,
  ``finish_push`` and ``push.sender`` carry the row, as they carry a reminder.

**When.** The first tick at or after the member's day start
(``user_settings.day_start_hour``, in the member's zone, through
``state.settings_day``). A day start more than the TTL of the kind behind
``now`` plans nothing (``push_channel.too_late``), so an outage sends no late
summary.

**One for each day.** The key is ``due_summary:<member>:<local date>``, under
the UNIQUE of ``(organization_id, dedupe_key)``, and ``plan_push`` inserts with
``ON CONFLICT DO NOTHING``. So a retry and a second process plan one row. The
local date is ``Day.feed_day``, the date of the most recent day start.

**Nothing due.** No row and no push. The Redis key
``reach:due_checked:<member>:<local date>`` records that the read found
nothing, so a task that lands later that day sends no push, and the member's
tasks are read once. The key is a saving and the quiet rule, never the guard of
a duplicate: with Redis down, the read runs on each tick of the window, and a
task that lands in the window can then send the one push of the day.

**Who.** Each active member of the org (``app_user.status``, one read), who
holds ``feature:projects``, the check that My Day makes before it shows a task
card. ``reach.loop.resolve`` is the one member check. The member comes from
that read, the org comes from the loop, and neither comes from input (R5).

**What.** ``personal.my_due_rows``, the body of ``my_due_tasks``, and
``needs.due_kind``, the split of My Day. So the push and My Day count the same
rows. The first title is the oldest deadline, the first row of My Day's order.

**The gate.** ``push_channel.push_on()`` (``WEB_PUSH_ENABLED`` and
``CALENDAR_REMINDERS``) and ``flags.due_push_allowed`` (``DUE_PUSH_ORGS``,
default empty). No ``REACH_*`` flag gates it.

**A quiet tick costs a few statements.** The plan runs each minute, for each
org, all day. So :func:`_open_slots` reads each member's day in one read, and a
member with no settings row takes the People seed from one set read
(``tasks.settings.work_schedule_seeds``). A member costs a statement only in
the window of the day start, once.

**The caps.** The kind has no cap of its own (``push_channel.cap_of``), because
the key gives one a day. A cap counts its own kind only, and the WhatsApp reads
name ``channel = 'whatsapp'``, so this row never counts against another cap.
It is no ``urgent`` kind, so a row that reaches the send in quiet hours ends
``skipped``, with the reason ``quiet``, and no push goes for that day. It also
sorts after every reminder row (``outbox._PLANNED_PUSHES_SQL``).

**The outbox holds no title.** ``body`` is the fixed text :data:`BODY`, and the
send step builds the words from a fresh read.

Fences: ``tests/unit/test_due_push_r8.py`` (a real Postgres, FORCE RLS, two
orgs, a frozen clock) and ``tests/unit/test_reach_push_message.py``.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from acb_common import get_logger, tenant_redis
from acb_common.db import current_tenant, tenant_session
from acb_common.tenant_redis import key, organization_scope
from sqlalchemy import text

from gateway.reach import flags, outbox, push_channel, push_message

_log = get_logger("reach.due_push")

KIND = push_message.DUE_SUMMARY
#: The fixed outbox body. No title goes in the outbox.
BODY = KIND
#: The most rows that one read takes. The read is oldest first, so a member
#: past this cap sees a floor (``Summary.capped``), never a false count.
READ_LIMIT = 200
#: The members that one tick plans for one org, at most. The rest wait for the
#: next tick, so one org cannot hold the steps of the next org.
PLAN_BATCH = 100
#: Seconds that the mark of an empty read lives: past the end of the window.
CHECK_TTL_S = 36 * 3600
#: Seconds that one Redis call may take. Past it, the plan reads the tasks.
REDIS_TIMEOUT_S = 0.25

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: Each active member of the bound org with the row of ``tasks.settings._load``
#: (any case: the lower spelling, then the newest). ``NULLIF`` is the rule of
#: ``_load``, which reads a stored 0 as the default hour. A member with no row
#: reads their day from the People seed, because ``_load`` does.
_MEMBERS_SQL = (
    "SELECT DISTINCT ON (lower(u.email)) lower(u.email) AS member, s.timezone, "
    "NULLIF(s.day_start_hour, 0) AS day_start_hour, s.user_id IS NOT NULL AS has_row "
    "FROM app_user u LEFT JOIN user_settings s "
    "ON s.organization_id = u.organization_id AND lower(s.user_id) = lower(u.email) "
    f"WHERE u.organization_id = {_BOUND_ORG} AND u.status = 'active' "
    "ORDER BY lower(u.email), (s.user_id = lower(u.email)) DESC NULLS LAST, "
    "s.updated_at DESC NULLS LAST"
)

#: The keys that already hold a row, in any state. The UNIQUE index serves it.
_PLANNED_SQL = (
    "SELECT dedupe_key FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND dedupe_key = ANY(CAST(:keys AS text[]))"
)


def dedupe_key(member: str, local_date: date) -> str:
    """``due_summary:<member>:<local date>``: one row for each member and day."""
    return f"{KIND}:{member}:{local_date.isoformat()}"


@dataclass(frozen=True)
class Slot:
    """One member whose summary may go in this tick."""

    member: str
    local_date: date

    @property
    def key(self) -> str:
        return dedupe_key(self.member, self.local_date)


@dataclass(frozen=True)
class Summary:
    """What a member's due read found."""

    due_today: int
    overdue: int
    first: str
    #: True when the read stopped at :data:`READ_LIMIT`.
    capped: bool = False


async def summary(db: Any, member: str, now: datetime) -> Summary | None:
    """The member's due work on *db*, or None when nothing is due. The read and
    the split of My Day (``my_due_rows``, ``needs.due_kind``)."""
    from gateway.routes.projects.personal import my_due_rows
    from gateway.routes.shell.needs import due_kind, local_today

    answer = await my_due_rows(db, member, limit=READ_LIMIT, at=now)
    rows = answer.get("rows") or []
    zone, today = answer.get("timezone"), local_today(answer)
    kept = [(kind, row) for row in rows
            if (kind := due_kind(row, zone, today)) is not None]
    if not kept:
        return None
    overdue = sum(1 for kind, _ in kept if kind == "overdue")
    return Summary(len(kept) - overdue, overdue, str(kept[0][1].get("title") or ""),
                   len(rows) >= READ_LIMIT)


# ── The plan step ───────────────────────────────────────────────────────────


def _guard(slot: Slot) -> Any:
    return key("reach", "due_checked", slot.member, slot.local_date.isoformat())


async def _unchecked(org: str, slots: list[Slot]) -> list[Slot]:
    """The slots with no mark of an empty read. Redis down: all of them."""
    if not slots:
        return slots
    try:
        with organization_scope(org):
            marks = await asyncio.wait_for(
                tenant_redis.get_tenant_redis().mget([_guard(s) for s in slots]),
                REDIS_TIMEOUT_S)
    except Exception as exc:  # the key is a saving: read the tasks instead
        _log.warning("reach.due.guard_read_failed", error=type(exc).__name__)
        return slots
    return [s for s, mark in zip(slots, marks, strict=True) if not mark]


async def _mark_checked(org: str, slot: Slot) -> None:
    """Record an empty read, so the rest of the day stays quiet."""
    try:
        with organization_scope(org):
            await asyncio.wait_for(
                tenant_redis.get_tenant_redis().set(_guard(slot), "1", ex=CHECK_TTL_S),
                REDIS_TIMEOUT_S)
    except Exception as exc:  # the next tick reads the tasks again
        _log.warning("reach.due.guard_write_failed", error=type(exc).__name__)


async def _open_slots(db: Any, now: datetime) -> list[Slot]:
    """The active members of the bound org whose day started in the window,
    with no row yet. One read of the members, one set read of the seed of the
    members with no settings row, and one read of the keys. No read for each
    member: this runs on each tick, all day."""
    from gateway.routes.shell.state import settings_day
    from gateway.routes.tasks.settings import UserSettingsModel, work_schedule_seeds

    rows = (await db.execute(text(_MEMBERS_SQL))).all()
    unseeded = [str(r.member) for r in rows if not r.has_row]
    seeds = await work_schedule_seeds(db, unseeded) if unseeded else {}
    slots: list[Slot] = []
    for row in rows:
        member = str(row.member)
        day = settings_day(row if row.has_row
                           else UserSettingsModel(**seeds.get(member, {})))
        if not push_channel.too_late(KIND, day.day_start(now), now):
            slots.append(Slot(member, day.feed_day(now)))
    if not slots:
        return slots
    taken = {str(r[0]) for r in (await db.execute(
        text(_PLANNED_SQL), {"keys": [s.key for s in slots]})).all()}
    return [s for s in slots if s.key not in taken]


async def _may_read(org: str, member: str) -> bool | None:
    """True when the member may read task cards, False when the member may
    not, and None when the read failed, so a later tick tries again.

    ``member_context`` binds a tenant and never releases it, so the check runs
    in a task of its own."""
    from gateway.reach.loop import resolve

    async def check() -> bool | None:
        user = await resolve(org, member)
        if user == "identity_failed":
            return None
        return False if isinstance(user, str) else bool(user.has_permission("feature:projects"))

    try:
        return await asyncio.create_task(check())
    except Exception as exc:
        _log.warning("reach.due.member_failed", error=type(exc).__name__)
        return None


async def _plan_one(org: str, slot: Slot, now: datetime) -> str:
    """One member: read, check, plan. Answers the outcome."""
    async with tenant_session(org) as db:
        found = await summary(db, slot.member, now)
    if found is None:
        await _mark_checked(org, slot)
        return "due_nothing"
    allowed = await _may_read(org, slot.member)
    if allowed is None:
        return "due_retry"
    if not allowed:
        await _mark_checked(org, slot)
        return "due_no_feature"
    async with tenant_session(org) as db:
        planned = await outbox.plan_push(
            db, slot.member, kind=KIND, signal_ids=[], key=slot.key, body=BODY,
            local_date=slot.local_date)
    return "due_planned" if planned else "due_duplicate"


async def plan_org(org: str, now: datetime,
                   stopping: Callable[[], bool] | None = None) -> Counter[str]:
    """Plan the morning summary of each member of *org* whose day just started.
    No network call. Answers the count of each outcome, and never raises: a
    failed plan never stops the steps of the reminders.

    The gate comes first, so an org off the list costs no statement."""
    counts: Counter[str] = Counter()
    if not push_channel.push_on() or not flags.due_push_allowed(org):
        return counts
    try:
        async with tenant_session(org) as db:
            slots = await _open_slots(db, now)
        for slot in (await _unchecked(org, slots))[:PLAN_BATCH]:
            if stopping is not None and stopping():
                break
            try:
                counts[await _plan_one(org, slot, now)] += 1
            except Exception as exc:  # the next tick tries this member again
                _log.warning("reach.due.member_plan_failed", error=type(exc).__name__)
                counts["due_failed"] += 1
    except Exception as exc:
        _log.warning("reach.due.plan_failed", error=type(exc).__name__)
        counts["due_failed"] += 1
    return counts


# ── The send step ───────────────────────────────────────────────────────────


async def read_facts(db: Any, row: outbox.PushRow, now: datetime) -> push_channel.Facts:
    """What the claim of ``push_channel._send_one`` reads for one summary row,
    on the claim's own session. The caller checked the TTL of the row, quiet
    hours and the cap. A task closed since the plan leaves the row's count, and
    a member with nothing left gets no push (``nothing_due``)."""
    from gateway.reminders.loop import member_day

    if not flags.due_push_allowed(current_tenant()):
        return push_channel.Facts("flag_off")  # the switch stops a planned row too
    if row.local_date is not None:
        day = await member_day(db, row.member)
        if push_channel.too_late(KIND, day.at(row.local_date, day.start_hour), now):
            return push_channel.Facts("expired")
    found = await summary(db, row.member, now)
    if found is None:
        return push_channel.Facts("nothing_due")
    org_name, hide = await push_channel.reader_of(db, row.member)
    return push_channel.Facts(None, message=push_message.build_due(
        due_today=found.due_today, overdue=found.overdue, first_title=found.first,
        org_name=org_name, hide_details=hide, capped=found.capped))
