"""GET /shell/feed — My Day as one ranked column of cards (WS-58 MD-1a).

Spec: ``project-docs/specs/my_day_feed.md`` §4, §5.1, §7, §11.1 and §14.1a.
The page frame, the bell and the needs contract stay ``navigation_shell.md``'s.

**One provider run.** The feed runs the needs providers of ``needs.REGISTRY``
first, in today's order and on today's budget (``needs.needs_run``). Then it
runs the feed-only providers, on a budget of their own. So a slow feed-only
provider can never turn a needs source to ``failed``. The feed-only providers
run two at a time at most, each with its own limit from its start, under one
ceiling. So a slow one fails alone (MD-3b). A gated one runs only when
``MY_DAY_PROVIDERS`` lists it. ``source_apps`` names the app of each source.

**The answer carries the needs view.** ``needs`` is exactly the answer of
``GET /shell/needs?limit=30``, from the same run, before the fold. The
client writes it into the needs cache key, so the bell and the sidebar
counts read it with no second request (§14.1).

**One entity, one card** (§4.5). A signal keys on its entity. The kind with
the highest ``feed_rank.BASE`` is the card's kind, and each other kind is
folded into the card: its facts, a clause of the why line, and its acts.

**No model and no write.** Code writes every why line from a template
(§4.2). Each act names its way (§11.1): ``done`` and ``read`` are ``tap``,
and run through the owning app's route, as the needs rows do. Every other
act is ``open``, a link. The route is read-only. The client reads it only
behind ``NEXT_PUBLIC_MY_DAY_FEED`` (MD-1b).

**The state join** (MD-2a, §6.8 item 7) runs after the fold and before the
order (``state.apply_state``). It reads the member's rows of
``my_day_card_state`` and writes nothing. A snoozed, dismissed or expired
card leaves there, so it takes no slot of ``limit``. ``needs`` is computed
BEFORE the join, so the state never changes the bell (§6.8 item 9).

**Handled** (MD-2c) runs at the same time as the state join, inside its 1.5
seconds (``handled.handled_run``). The answer gains ``handled``, what the
apps closed for the member today, and ``handled_ok``, the claim flag. Neither
is a card or a group, and neither counts against ``limit``.

**The read cache** (MD-5a, §8.4) wraps the run: ``shell_feed`` asks
``cache.serve``, and only a miss calls ``feed_answer``. ``feed_answer`` hands
back the answer with its time edges, so the cache knows when the clock changes
it. ``MY_DAY_CACHE`` (default off) gates it, and ``cache.py`` holds the rules.

**The Calendar provider** (§5.1, feed only) needs ``feature:tasks`` (the
Calendar pane) AND ``feature:projects`` (the lens read of blocks). It calls
the apps' own reads, each of which opens its own tenant session (R5b):

=================  ==========================================================
Read               What it gives
=================  ==========================================================
``day_summary``    Today's open blocks and ``capacity_mins``, in the
                   member's zone (``routes/tasks/calendar.py``).
``get_user_settings``  ``day_start_hour`` and the zone
                   (``routes/tasks/settings.py``).
``my_calendar``    The member's blocks over the next 7 days, from the lens
                   (``routes/projects/personal.py``). It is not bounded, so
                   the provider stops at 15 signals and sets ``capped``.
``get_me``         Only for ``no_plan_yet``: the member's working days, from
                   the People app's own self read (``routes/people/
                   selfservice.py``). A member with no directory row gets
                   the default policy's days.
=================  ==========================================================

Each read takes the member from the session and nothing else, so another
member's block never reaches a card. Fence: ``tests/unit/test_shell_feed.py``
and ``tests/unit/test_shell_feed_r8.py``.

**The task kinds** (MD-3c, §5.9 item 11) come from the gated source
``tasks_followups``: ``waiting_quiet``, ``stale_next``, ``unblocked`` and
``absent_on_due``. A card names its ``source`` when the source id is not its
app, so the client's carry keeps a failed source's own cards (§5.9 item 1).
This file holds the why templates of all eleven MD-3 kinds. Fence:
``tests/unit/test_feed_task_kinds.py`` and ``test_feed_task_kinds_r8.py``.

**People and chat** (MD-3f, §5.9 item 14) come from two gated sources, each
its own app. ``people`` gives ``colleague_out`` and needs the HR grant.
``chat`` gives ``agent_asks``, with no question text. Fence:
``tests/unit/test_feed_people_chat_kinds.py`` and its ``_r8`` twin.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from acb_common.priority import task_cell
from fastapi import Depends, HTTPException, Request, Response
from gateway.routes.shell import cache, feed_rank, handled, needs, state
from gateway.routes.shell.needs import (
    PER_APP,
    Found,
    Provider,
    Signal,
    _instant,
    _iso,
    day_href,
    link,
    register,
    task_entity,
    task_href,
)
from gateway.routes.shell.search import router

logger = logging.getLogger(__name__)

#: The feed's default length, and its ceiling (§14.1a).
DEFAULT_FEED_LIMIT = 40
MAX_FEED_LIMIT = 80
#: Seconds for EACH feed-only provider, AFTER the needs run, from the moment
#: it takes its slot (MD-3b, §5.8 item 3). So a slow one fails alone.
FEED_BUDGET_S = 2.0
#: Seconds one feed-only provider may take.
FEED_PROVIDER_TIMEOUT_S = 2.0
#: The feed-only providers that run at the same time, at most, for a read.
#: Each holds a pool connection, and the pool is small (MD-2c,
#: EMAXCONNSESSION 2026-09-20).
FEED_CONCURRENCY = 2
#: The same for the My Day pass of the loop (``read=False``). The loop runs
#: three members at once, so it holds three of these connections at most.
FEED_PASS_CONCURRENCY = 1
#: Seconds for all the feed-only providers, from the start of their run. A
#: provider still queued or running then reads ``failed`` (MD-3b). The read's
#: worst case is ``needs.TOTAL_BUDGET_S`` + this + ``needs.CANCEL_WAIT_S`` +
#: ``state.STATE_TIMEOUT_S``: 4.0 + 2.3 + 0.1 + 1.5 = 7.9 seconds, under the
#: 8 seconds of the BFF route (``app/api/shell/feed/route.ts``
#: ``TIMEOUT_MS``). The cancel wait counts, because it also runs when the
#: ceiling fires. Fence: ``test_shell_feed.py::TestTheBudgetSum``.
FEED_TOTAL_S = 2.3
#: The days of blocks that ``block_overlap`` reads, today first.
WEEK_DAYS = 7
#: A block that starts within this time is ``block_soon``.
SOON = timedelta(minutes=30)
#: ``no_plan_yet`` waits this long after the start of the member's day.
PLAN_GRACE = timedelta(minutes=60)
#: The day start of a member with no stored hour: the column default of
#: ``user_settings.day_start_hour`` (``infra/postgres/77_gtd_calendar_prefs.sql``).
DEFAULT_DAY_START_HOUR = 7
#: The effective dispositions of a block that is no longer open.
CLOSED = frozenset({"DONE", "TRASH"})
#: The Calendar kinds of MD-1 (§5.1).
CALENDAR_KINDS = frozenset({"block_soon", "block_overlap", "day_overbooked", "no_plan_yet"})


# ── The Calendar provider (§5.1, feed only) ─────────────────────────────────


async def _calendar(user: UserContext) -> Found:
    from gateway.routes.projects.personal import my_calendar, zone_name
    from gateway.routes.tasks.calendar import day_summary
    from gateway.routes.tasks.settings import get_user_settings

    now = needs._now()  # the feed's one clock (needs._now)
    summary = await day_summary(date=None, user=user)
    settings = await get_user_settings(user=user)
    zone = zone_name(getattr(settings, "timezone", None))
    tz = ZoneInfo(zone)
    today = date.fromisoformat(str(summary["day"]))
    day0 = datetime.combine(today, time.min, tzinfo=tz)
    tomorrow0 = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    # DONE blocks too, so a day whose plan is all done is not "no plan".
    week = await my_calendar(start=day0.isoformat(),
                             end=(day0 + timedelta(days=WEEK_DAYS)).isoformat(),
                             include_done=True, user=user)
    rows = list(getattr(week, "rows", None) or [])
    level = {str(r["id"]): task_cell(r) for r in rows}

    blocks = [b for b in (summary.get("scheduled") or []) if not b.get("done")]
    signals: list[Signal] = []
    signals.extend(_block_soon(blocks, level, now))
    day_key = f"calendar:{today.isoformat()}"
    day_entity = {"type": "day", "id": today.isoformat(), "label": "Today"}
    planned = _planned_mins(blocks)
    capacity = int(summary.get("capacity_mins") or 0)
    if planned > capacity:
        signals.append(Signal(
            signal_id=day_key, app="calendar", kind="day_overbooked", entity=day_entity,
            title="Today is overbooked",
            facts={"day": today.isoformat(), "planned_mins": planned,
                   "capacity_mins": capacity},
            href=day_href(today), acts=(link("fit", "Fit what's left", day_href(today)),),
            expires_at=_iso(tomorrow0),
        ))
    elif not blocks and not any(
        _on_day(r, today, tz) for r in rows if r.get("disposition") != "TRASH"
    ):
        # An unset hour reads as 7, the column default (migration 77). Never
        # as midnight, or no_plan_yet fires seven hours early. 0 is a real
        # hour, so this checks for None, never with `or`.
        stored_hour = getattr(settings, "day_start_hour", None)
        start_hour = DEFAULT_DAY_START_HOUR if stored_hour is None else int(stored_hour)
        started = datetime.combine(today, time(hour=min(23, max(0, start_hour))), tzinfo=tz)
        if now >= started + PLAN_GRACE and await _working_day(user, today):
            signals.append(Signal(
                signal_id=day_key, app="calendar", kind="no_plan_yet", entity=day_entity,
                title="Nothing is planned for today",
                facts={"day": today.isoformat(), "day_start": _iso(started)},
                href=day_href(today),
                acts=(link("plan-day", "Plan my day", day_href(today)),),
                expires_at=_iso(tomorrow0),
            ))
    signals.extend(_overlaps(rows, level, now, tz))
    return Found([], 0, capped=len(signals) > PER_APP,
                 signals=lambda: signals[:PER_APP], zone=zone,
                 next_edge=_next_block_edge(blocks, now))


def _next_block_edge(blocks: list[dict[str, Any]], now: datetime) -> str | None:
    """The instant the next block of today enters the window of
    ``block_soon``: its start minus 30 minutes, for a block that starts after
    the window (``my_day_feed.md`` §8.4 item 2). A block inside the window is
    already a signal, and its ``expires_at`` is its own edge."""
    edges = [start - SOON for start in (_instant(b.get("start")) for b in blocks)
             if start is not None and start > now + SOON]
    return _iso(min(edges)) if edges else None


def _block_soon(blocks: list[dict[str, Any]], level: dict[str, str],
                now: datetime) -> list[Signal]:
    out: list[Signal] = []
    for block in blocks:
        start, end = _instant(block.get("start")), _instant(block.get("end"))
        if start is None or not (now < start <= now + SOON):
            continue
        task_id = str(block["id"])
        entity = task_entity(task_id, block.get("title"))
        out.append(Signal(
            signal_id=f"tasks:{task_id}", app="calendar", kind="block_soon",
            entity=entity, title=entity["label"],
            facts={"block_start": _iso(start), "block_end": _iso(end),
                   "minutes": math.ceil((start - now).total_seconds() / 60),
                   "priority": level.get(task_id)},
            href=task_href(task_id), acts=(link("open-task", "Open task", task_href(task_id)),),
            at=_iso(start), urgent_at=_iso(start), expires_at=_iso(start),
        ))
    return out


def _planned_mins(blocks: list[dict[str, Any]]) -> int:
    """The planned minutes of today's open blocks, the number the planner
    plans against ``capacity_mins`` (§5.1)."""
    total = 0.0
    for block in blocks:
        start, end = _instant(block.get("start")), _instant(block.get("end"))
        if start is not None and end is not None and end > start:
            total += (end - start).total_seconds()
    return int(total // 60)


def _on_day(row: dict[str, Any], day: date, tz: ZoneInfo) -> bool:
    start = _instant(row.get("scheduled_start"))
    return start is not None and start.astimezone(tz).date() == day


def _overlaps(rows: list[dict[str, Any]], level: dict[str, str], now: datetime,
              tz: ZoneInfo) -> list[Signal]:
    """``block_overlap``: one signal for each task whose block starts first
    in an overlap (§5.1, "One overlap, one card"). The other tasks are
    facts on it. An overlap that already ended is left out."""
    live = []
    for row in rows:
        if row.get("disposition") in CLOSED:
            continue
        start, end = _instant(row.get("scheduled_start")), _instant(row.get("scheduled_end"))
        if start is not None and end is not None and end > start:
            live.append((start, end, row))
    live.sort(key=lambda x: (x[0], str(x[2]["id"])))
    out: list[Signal] = []
    for i, (start, end, row) in enumerate(live):
        others: list[dict[str, Any]] = []
        first: datetime | None = None
        last: datetime | None = None
        for start2, end2, row2 in live[i + 1:]:
            if start2 >= end:
                break  # sorted by start, so nothing later overlaps
            if str(row2["id"]) == str(row["id"]):
                continue
            low, high = max(start, start2), min(end, end2)
            if high <= now:
                continue
            others.append(row2)
            first = low if first is None else min(first, low)
            last = high if last is None else max(last, high)
        if not others or first is None:
            continue
        task_id = str(row["id"])
        entity = task_entity(task_id, row.get("title"))
        out.append(Signal(
            signal_id=f"tasks:{task_id}", app="calendar", kind="block_overlap",
            entity=entity, title=entity["label"],
            facts={"overlaps": [task_entity(str(o["id"]), o.get("title"))["label"]
                                for o in others],
                   "overlap_ids": [str(o["id"]) for o in others],
                   "overlap_at": _iso(first),
                   "overlap_day": first.astimezone(tz).date().isoformat(),
                   "block_start": _iso(start), "block_end": _iso(end),
                   "priority": level.get(task_id)},
            # Until MD-8 the act is a link to the Calendar. It opens the day
            # of the block at its start, in the member's zone (MD-3a, §5.8).
            href=task_href(task_id),
            acts=(link("open-day", "Open the day", day_href(start.astimezone(tz).date(),
                                                            start.astimezone(tz))),),
            at=_iso(start), urgent_at=_iso(first), expires_at=_iso(last),
        ))
    return out


async def _working_day(user: UserContext, today: date) -> bool:
    """Whether ``today`` is one of the member's working days, from the People
    app's own self read. A read that fails says no, so the feed stays quiet
    rather than nag on a day off."""
    from gateway.routes.people.selfservice import get_me
    from gateway.work_schedule import DEFAULT_POLICY

    try:
        me = await get_me(user=user)
    except Exception as exc:
        logger.warning("shell.feed working day unknown", extra={"error": type(exc).__name__})
        return False
    person = getattr(me, "person", None) or {}
    days = None
    if getattr(me, "state", None) == "resolved":
        days = (person.get("schedule") or {}).get("days")
    if not days:
        days = DEFAULT_POLICY["working_days"]
    return today.isoweekday() in {int(d) for d in days}


register(Provider("calendar", ("tasks", "projects"), _calendar, CALENDAR_KINDS, needs=False))


# ── The task kinds (§5.9 item 11, MD-3c, feed only, gated) ──────────────────
#
# The source `tasks_followups`: four kinds about my own tasks, each keyed on
# `tasks:<task id>`, so each folds into the task's one card (§4.5). The
# provider calls four reads, each of which opens its own tenant session
# (R5b), one after another: `my_quiet_tasks`, `my_unblocked_since`, my
# absences (`selfservice.my_absences`), and `my_due_ahead` only when an
# absence falls in its window. So it opens 4 sessions at most (§5.9 item 7).
#
# ⚠️ No task kind carries a state fact (`state.STATE_FACTS`). A notification
# card has no `due_at`, so a kind that carried one would change the state
# fingerprint of that card when it folds in (done-when 11). The fingerprint
# of the card (`STABLE_FACTS`) takes each kind's own names.

#: The kinds of the source (§5.9 item 11).
TASK_FOLLOWUP_KINDS = frozenset({"waiting_quiet", "stale_next", "unblocked", "absent_on_due"})
#: The days that ``absent_on_due`` reads: today, and the 14 after it.
ABSENCE_AHEAD_DAYS = 14
#: The absences that make a day away. A ``partial`` absence does not.
FULL_ABSENCES = frozenset({"away", "holiday"})


def _local_days(earlier: Any, today: date, tz: ZoneInfo) -> int:
    """Whole local days from ``earlier`` to ``today``."""
    at = _instant(earlier)
    return 0 if at is None else max(0, (today - at.astimezone(tz).date()).days)


def _waiting_who(waiting_on: Any) -> str | None:
    """Who a waiting task waits on, by the bell's ``_who``: the name the
    delegation stored, or the address's local part. None when the task names
    nobody."""
    if not isinstance(waiting_on, dict):
        return None
    raw = str(waiting_on.get("name") or waiting_on.get("email") or "").strip()
    return needs._who(raw) if raw else None


def _task_facts(task: dict[str, Any]) -> dict[str, Any]:
    """The facts every task kind carries: the D78 level, which the old-path
    weight reads, and the project name."""
    return {"priority": task_cell(task), "project": task.get("project_name")}


def _quiet_signal(task: dict[str, Any], today: date, tz: ZoneInfo) -> Signal | None:
    """``waiting_quiet`` or ``stale_next``, from one row of ``my_quiet_tasks``."""
    task_id = str(task["id"])
    entity = task_entity(task_id, task.get("title"))
    facts = _task_facts(task)
    if task.get("disposition") == "WAITING":
        delegated = _instant(task.get("delegated_at"))
        if delegated is None:
            return None  # a task with no delegated_at never fires
        who = _waiting_who(task.get("waiting_on"))
        facts.update({"delegated_at": _iso(delegated),
                      "waited_days": _local_days(delegated, today, tz), "who": who})
        return Signal(
            signal_id=f"tasks:{task_id}", app="tasks", kind="waiting_quiet",
            entity=entity, title=entity["label"], facts=facts, href=task_href(task_id),
            # Class B: a nudge reaches another person. Until MD-8 it opens the task.
            acts=(link("nudge", f"Nudge {who}" if who else "Nudge", task_href(task_id),
                       cls="B"),),
            at=_iso(delegated),
        )
    if task.get("disposition") == "NEXT":
        changed = _instant(task.get("last_change_at"))
        if changed is None:
            return None
        facts.update({"changed_at": _iso(changed),
                      "stale_days": _local_days(changed, today, tz)})
        return Signal(
            signal_id=f"tasks:{task_id}", app="tasks", kind="stale_next",
            entity=entity, title=entity["label"], facts=facts, href=task_href(task_id),
            acts=(link("move-someday", "Move to Someday", task_href(task_id)),),
            at=_iso(changed),
        )
    return None


def _unblocked_signal(task: dict[str, Any]) -> Signal:
    """``unblocked``, from one row of ``my_unblocked_since``. ⚠️ The facts
    of a blocker that the member cannot read hold no title, no id and no
    actor: only that a blocker closed, and when."""
    task_id = str(task["id"])
    entity = task_entity(task_id, task.get("title"))
    blocker = task.get("unblocked_by") or {}
    facts = _task_facts(task)
    facts["unblocked_at"] = _iso(blocker.get("closed_at"))
    if blocker.get("readable") and blocker.get("id"):
        facts.update({"blocker": task_entity(str(blocker["id"]), blocker.get("title"))["label"],
                      "blocker_ids": [str(blocker["id"])],
                      "who": needs._who(blocker.get("closed_by"))})
    return Signal(
        signal_id=f"tasks:{task_id}", app="tasks", kind="unblocked",
        entity=entity, title=entity["label"], facts=facts, href=task_href(task_id),
        acts=(link("open", "Open", task_href(task_id)),),
        at=_iso(blocker.get("closed_at")),
    )


def _absent_signal(task: dict[str, Any], away: set[date], tz: ZoneInfo) -> Signal | None:
    """``absent_on_due``: the local due date of the task is one of my days
    away. The day is the fact, so no state fact rides on the fold."""
    due = _instant(task.get("due_at"))
    if due is None:
        return None
    day = due.astimezone(tz).date()
    if day not in away:
        return None
    task_id = str(task["id"])
    entity = task_entity(task_id, task.get("title"))
    facts = _task_facts(task)
    facts["absence_day"] = day.isoformat()
    return Signal(
        signal_id=f"tasks:{task_id}", app="tasks", kind="absent_on_due",
        entity=entity, title=entity["label"], facts=facts, href=task_href(task_id),
        acts=(link("move-date", "Move the date", task_href(task_id)),),
        at=_iso(due),
    )


async def _my_days_away(user: UserContext, today: date) -> set[date]:
    """My days of full absence, from today to today + 14, through the People
    app's own self read. ⚠️ A 404 says that I have no People row. That is no
    absence, and NOT ``SourceAbsent``, because the other three kinds still
    hold (§5.9 item 11). Any other error raises, so the source fails."""
    from gateway.routes.people.selfservice import my_absences

    try:
        answer = await my_absences(user=user)
    except HTTPException as exc:
        if exc.status_code == 404:
            return set()
        raise
    last = today + timedelta(days=ABSENCE_AHEAD_DAYS)
    days: set[date] = set()
    for row in (answer or {}).get("rows") or []:
        if row.get("kind") not in FULL_ABSENCES:
            continue
        start = max(date.fromisoformat(str(row["starts_on"])), today)
        end = min(date.fromisoformat(str(row["ends_on"])), last)
        while start <= end:
            days.add(start)
            start += timedelta(days=1)
    return days


def _followup_order(signal: Signal) -> tuple[int, float, str]:
    """By ``BASE``, then by age: the oldest time first (§5.9 item 11)."""
    at = _instant(signal.at)
    return (-feed_rank.base(signal.kind), at.timestamp() if at is not None else float("inf"),
            signal.signal_id)


async def _tasks_followups(user: UserContext) -> Found:
    from gateway.routes.projects.personal import (
        my_due_ahead,
        my_quiet_tasks,
        my_unblocked_since,
    )

    now = needs._now()  # the feed's one clock (needs._now)
    size = PER_APP + 1  # one row more than the cap, so the cap is known
    quiet = await my_quiet_tasks(user, limit=size, now=now)
    zone = quiet.get("timezone")
    tz = state.zone_of(zone)
    today = now.astimezone(tz).date()
    # "Since my day start": the member's day starts at `day_start_hour`, by
    # the one rule of the state join and of Handled (`state._hours`, through
    # `personal.member_day`). The quiet read gave the hour on its own
    # session, so no session opens for it. None (no Calendar pane) reads as
    # the column default, as `state._hours` does.
    day = state.Day(tz, state._hour(quiet.get("day_start_hour"), state.DEFAULT_DAY_START, 23))
    unblocked = await my_unblocked_since(user, day.day_start(now), limit=size, today=today)
    away = await _my_days_away(user, today)
    ahead: dict[str, Any] = {"rows": []}
    if away:
        ahead = await my_due_ahead(
            user, datetime.combine(min(away), time.min, tzinfo=tz),
            datetime.combine(max(away) + timedelta(days=1), time.min, tzinfo=tz),
            limit=size, days=sorted(away), zone=tz.key)

    signals: list[Signal] = []
    signals += [s for s in (_quiet_signal(t, today, tz) for t in quiet.get("rows") or []) if s]
    signals += [_unblocked_signal(t) for t in unblocked.get("rows") or []]
    signals += [s for s in (_absent_signal(t, away, tz) for t in ahead.get("rows") or []) if s]
    signals.sort(key=_followup_order)
    full = any(len(r.get("rows") or []) >= size for r in (quiet, unblocked, ahead))
    return Found([], 0, capped=full or len(signals) > PER_APP,
                 signals=lambda: signals[:PER_APP], zone=zone)


# The gate (§5.9 item 11): the lens reads need `feature:projects`. Gated, so
# it runs only while MY_DAY_PROVIDERS lists it (§5.8 item 1). Its cards are
# the `tasks` app's, and no Handled read: a closed task is My Tasks' row.
register(Provider("tasks_followups", ("projects",), _tasks_followups, TASK_FOLLOWUP_KINDS,
                  needs=False, gated=True, app="tasks"))


# ── The mail kinds (§5.9 item 12, MD-3d, feed only, gated) ──────────────────
#
# The source `email_followups`: `promise_due`, an open promise that I made by
# mail and that falls due, and `no_answer`, a mail I sent that waits too long
# for an answer. The reads are the email app's own, two owner-checked
# wrappers in `routes/email/digest.py`: `promise_tasks` and
# `awaiting_threads`. Each opens one tenant session and raises on an error.
# So the provider opens 1 session for the mailboxes, plus 2 for each mailbox
# (§5.9 item 7). A failed mailbox costs only its own rows, as in the needs
# provider `_email`.

#: The kinds of the source (§5.9 item 12).
MAIL_FOLLOWUP_KINDS = frozenset({"promise_due", "no_answer"})
#: A promise is due within this many days, or past due: the horizon of the
#: emailed digest (``digest._generate_digest``).
PROMISE_HORIZON_DAYS = 3


def _promise_signal(row: dict[str, Any], account_id: str) -> Signal:
    """``promise_due``, from one row of ``digest.promise_tasks``. It keys on
    the task, so it folds into the task's one card (§4.5). ⚠️ The mail id is
    in the link of "Open thread" only, never in ``message_id``, which is a
    state fact (§5.9 item 4)."""
    task_id = str(row["task_id"])
    entity = task_entity(task_id, row.get("title"))
    due = _iso(row.get("due_at"))
    acts = [needs.tap("done", "Done", "done", task_id)]
    if row.get("message_id"):
        acts.append(link("open-thread", "Open thread",
                         needs._email_href(str(row["message_id"]), account_id)))
    return Signal(
        signal_id=f"tasks:{task_id}", app="email", kind="promise_due",
        entity=entity, title=entity["label"],
        facts={"due_at": due, "priority": row.get("priority")},
        href=task_href(task_id), acts=tuple(acts), at=due, urgent_at=due,
    )


def _no_answer_signal(thread: dict[str, Any], account_id: str) -> Signal | None:
    """``no_answer``, from one row of ``digest.awaiting_threads``. It keys on
    the thread, as a needs-reply row does. A thread with no last message has
    nothing to open, so it gives no signal."""
    message_id = thread.get("message_id")
    if not message_id:
        return None
    subject = str(thread.get("subject") or "").strip() or "(no subject)"
    href = needs._email_href(str(message_id), account_id)
    sent = _iso(thread.get("last_message_at"))
    return Signal(
        signal_id=f"email:{account_id}:{thread['thread_id']}", app="email",
        kind="no_answer",
        entity={"type": "thread", "id": str(thread["thread_id"]), "label": subject},
        title=subject,
        facts={"who": str(thread.get("who") or "").strip() or None,
               "waiting_since": sent, "account": account_id,
               "message_id": str(message_id)},
        href=href, acts=(link("follow-up", "Follow up", href),), at=sent,
    )


async def _email_followups(user: UserContext) -> Found:
    from gateway.routes.email import digest
    from gateway.routes.email.automation.followups import _FOLLOW_UP_MAX_AGE_DAYS
    from gateway.routes.email.transport.accounts import feed_mailboxes

    now = needs._now()  # the feed's one clock (needs._now)
    size = PER_APP + 1  # one row more than the cap, so the cap is known
    # A thread older than the follow-up job's ceiling is left alone, as the
    # job leaves it (`followups._FOLLOW_UP_MAX_AGE_DAYS`).
    after = now - timedelta(days=_FOLLOW_UP_MAX_AGE_DAYS)
    # A promise is a task, so only a member with the lens gets one (§5.9 item
    # 12). Without it, the provider makes no promise read at all.
    promises = user.has_permission("feature:projects")

    async def _mailbox(account_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        threads = await digest.awaiting_threads(user, account_id, size, after=after)
        rows: list[dict[str, Any]] = []
        if promises:
            rows = await digest.promise_tasks(
                user, account_id, horizon_days=PROMISE_HORIZON_DAYS, limit=size, now=now)
        return threads, rows

    signals: list[Signal] = []
    asked = failed = 0
    full = False
    for mailbox in await feed_mailboxes(user):
        if not needs._in_all_inboxes(mailbox):
            continue  # D-EM-30: a separate mailbox stays out of the feed
        account_id = str(mailbox["id"])
        try:
            # One bound for the mailbox, both reads in it (§5.9 item 12).
            threads, rows = await asyncio.wait_for(_mailbox(account_id),
                                                   needs.MAILBOX_TIMEOUT_S)
        except HTTPException as exc:
            if exc.status_code == 404:
                # The owner check names the bound tenant. Read past RLS, the
                # mailbox list could hold the member's mailbox of another
                # organization, and it is not this feed's to read.
                continue
            asked += 1
            failed += 1
            logger.warning("shell.feed mailbox failed provider=email_followups error=%s",
                           type(exc).__name__)
            continue
        except Exception as exc:
            # This mailbox only. The type only: a database error's text can
            # hold the member's address.
            asked += 1
            failed += 1
            logger.warning("shell.feed mailbox failed provider=email_followups error=%s",
                           type(exc).__name__)
            continue
        asked += 1
        full = full or len(threads) >= size or len(rows) >= size
        signals += [s for s in (_no_answer_signal(t, account_id) for t in threads) if s]
        signals += [_promise_signal(r, account_id) for r in rows if r.get("due_at")]
    if asked and failed == asked:
        # Every mailbox failed: the source failed, never an empty `ok`.
        raise RuntimeError("every mailbox failed")
    signals.sort(key=_followup_order)
    return Found([], 0, capped=full or failed > 0 or len(signals) > PER_APP,
                 signals=lambda: signals[:PER_APP])


# The gate (§5.9 item 12): the mail reads need `feature:email`, and the
# provider asks for `feature:projects` before a promise read. Gated, so it
# runs only while MY_DAY_PROVIDERS lists it (§5.8 item 1). Its cards are the
# `email` app's, and no Handled read: a closed thread is the needs source's.
register(Provider("email_followups", ("email",), _email_followups, MAIL_FOLLOWUP_KINDS,
                  needs=False, gated=True, app="email"))
# ── The CRM kinds (§5.9 item 13, MD-3e, feed only, gated) ───────────────────
#
# The source `crm`: `crm_followup_due`, `deal_quiet` and `deal_close_passed`.
# Each keys on `crm:<deal|lead|contact|organization>:<id>`, so one record
# gives one card, and links to the record through `sources.crm_href`.
# WS-53 CRM-Q28 item 14 adds `digest_ready`: the weekly digest of the org was
# stored inside the last day. It keys on `crm:digest:<week_start>`, its why
# line is a code template, and its one act opens Reports. It shows no model
# text. Only an org on `CRM_DIGEST_ORGS` reads it (`aggregate.my_day_rows`).
#
# The gate, in the order of the CRM router: `feature:crm` (the Provider),
# then the kill switch, then the access rule. A closed switch and a 403 of
# the access rule each raise `SourceAbsent`, BEFORE any CRM read, so the
# source reads `absent` and not `failed` (§5.8 item 4).
#
# Two sessions at most (§5.9 item 7): the access rule opens one for a member
# who is not an admin, and `aggregate.my_day_rows` opens one for the zone and
# the three reads. This file opens none. ⚠️ Never `stage_dwell`: that read
# covers the whole organization. Fence:
# `tests/unit/test_feed_crm_kinds.py` and `test_feed_crm_kinds_r8.py`.

#: The kinds of the source (§5.9 item 13). WS-53 CRM-Q32-4 adds
#: `crm_open_questions`: a new question about the data of the org, for a CRM
#: admin. It keys on `crm:questions:<UTC date of new_at>`, so one day gives
#: one card, and its one act opens CRM settings at Open questions. Only an
#: org on `CRM_QUESTIONS_ORGS` reads it (`aggregate.my_day_rows`).
CRM_KINDS = frozenset({"crm_followup_due", "deal_quiet", "deal_close_passed",
                       "digest_ready", "crm_open_questions"})
#: The record word of a signal id → the kind of ``sources.CRM_KINDS``.
_CRM_RECORDS = {"deal": "deal", "lead": "lead", "contact": "contact",
                "organization": "company"}


def _crm_signal(kind: str, record: str, record_id: str, label: Any, *, facts: dict[str, Any],
                act: tuple[str, str], at: Any, urgent_at: Any = None) -> Signal:
    """One CRM signal. ``record`` is ``deal``, ``lead``, ``contact`` or
    ``organization``. Its one act is a link to the record (§5.9 item 13)."""
    from gateway.retrieval.sources import crm_href

    href = crm_href(_CRM_RECORDS[record], record_id)
    title = str(label or "").strip() or "(untitled)"
    return Signal(
        signal_id=f"crm:{record}:{record_id}", app="crm", kind=kind,
        entity={"type": record, "id": str(record_id), "label": title}, title=title,
        facts=facts, href=href, acts=(link(act[0], act[1], href),),
        at=_iso(at), urgent_at=_iso(urgent_at),
    )


def _followup_signal(row: dict[str, Any], tz: ZoneInfo) -> Signal:
    """``crm_followup_due``: the earliest open follow-up on one record
    (§5.9 item 13), by the one CRM day rule (``crm_platform.md`` §19.7).

    A task (``all_day``) holds a DATE at 00:00 UTC. Its ``due_at`` here is
    00:00 of that date in my zone, as ``deal_close_passed`` does, and it has
    no ``urgent_at``: a midnight is not a time, so it never lifts a card to
    Now. A call or a meeting keeps its real time, and its ``urgent_at`` is
    its ``due_at``."""
    from gateway.retrieval.sources import CRM_KINDS as RECORD_KINDS

    record = str(row["record_kind"])
    noun = RECORD_KINDS[_CRM_RECORDS[record]][0].lower()
    at = _instant(row.get("due_at"))
    all_day = bool(row.get("all_day"))
    if all_day and at is not None:
        due = _iso(datetime.combine(at.astimezone(ZoneInfo("UTC")).date(), time.min,
                                         tzinfo=tz))
        urgent = None
    else:
        due = urgent = _iso(at)
    facts: dict[str, Any] = {"due_at": due, "subject": row.get("subject")}
    if all_day:
        facts["all_day"] = True
    return _crm_signal("crm_followup_due", record, str(row["record_id"]), row.get("label"),
                       facts=facts, act=("open-record", f"Open {noun}"), at=due,
                       urgent_at=urgent)


def _quiet_deal_signal(row: dict[str, Any], now: datetime) -> Signal:
    """``deal_quiet``: an open deal of mine past the limit of its stage. The
    state fact is ``waiting_since``, the stage clock (§5.9 item 4).
    ``stage_days`` rounds the time in the stage UP, the rule of the SQL: a
    deal fires once it is MORE than ``limit`` days in the stage, so on its
    first day it reads ``limit + 1``, never ``limit``."""
    since = _instant(row.get("status_changed_at"))
    days = 0 if since is None else max(0, math.ceil((now - since).total_seconds() / 86400))
    return _crm_signal(
        "deal_quiet", "deal", str(row["id"]), row.get("name"),
        facts={"stage": row.get("stage"), "stage_days": days,
               "stage_limit": row.get("limit_days"), "waiting_since": _iso(since)},
        act=("log-call", "Log a call"), at=since)


def _close_passed_signal(row: dict[str, Any], tz: ZoneInfo) -> Signal:
    """``deal_close_passed``: an open deal of mine whose close date passed.
    ``due_at`` is the close date at 00:00 in the member's zone (§5.9 item 4)."""
    day = row["expected_close_date"]
    if not isinstance(day, date):
        day = date.fromisoformat(str(day))
    due = _iso(datetime.combine(day, time.min, tzinfo=tz))
    return _crm_signal("deal_close_passed", "deal", str(row["id"]), row.get("name"),
                       facts={"due_at": due}, act=("move-date", "Move the date"), at=due)


def _digest_signal(row: dict[str, Any]) -> Signal:
    """``digest_ready``: the weekly digest of the org is ready (CRM-Q28 item
    14). The facts are the week and the time it was stored, never the text.
    Its one act opens Reports, through the one builder of a CRM link."""
    from gateway.retrieval.sources import crm_reports_href

    href = crm_reports_href()
    week = row["week_start"]
    if not isinstance(week, date):
        week = date.fromisoformat(str(week))
    title = "Weekly CRM digest"
    return Signal(
        signal_id=f"crm:digest:{week.isoformat()}", app="crm", kind="digest_ready",
        entity={"type": "digest", "id": week.isoformat(), "label": title}, title=title,
        facts={"week_start": week.isoformat(), "stored_at": _iso(row.get("stored_at"))},
        href=href, acts=(link("open-reports", "Open Reports", href),),
        at=_iso(row.get("stored_at")),
    )


def _questions_signal(row: dict[str, Any]) -> Signal:
    """``crm_open_questions``: the CRM holds a new question about the data of
    the org (CRM-Q32-4, ``crm_platform.md`` §18.14.5). The signal id is the
    UTC date of ``new_at``, so a second card on one day is the same card.
    The facts are counts and a time, never a question. Its one act opens
    CRM settings at Open questions, through the one builder of a CRM link."""
    from gateway.retrieval.sources import crm_settings_href

    href = crm_settings_href("questions")
    at = _instant(row.get("new_at"))
    # The feed reads no clock of its own: the card always carries `new_at`.
    day = at.astimezone(ZoneInfo("UTC")).date().isoformat() if at is not None else "undated"
    title = "Open questions about your CRM data"
    return Signal(
        signal_id=f"crm:questions:{day}", app="crm", kind="crm_open_questions",
        entity={"type": "questions", "id": day, "label": title}, title=title,
        facts={"count": int(row.get("count") or 0), "open": int(row.get("open") or 0),
               "money": row.get("money") is True, "new_at": _iso(at),
               # CRM-Q32-8: the new answers that members proposed.
               "proposed": int(row.get("proposed") or 0)},
        href=href, acts=(link("answer", "Answer", href),), at=_iso(at),
    )


def _crm_order(signal: Signal, today: date, tz: ZoneInfo) -> tuple[int, int, float, str]:
    """By ``BASE``, then a follow-up due from my local today on before an
    older one, then by age (§5.9 item 13). So the cap of 15 never cuts a
    follow-up of today for a stale one, as the read's LIMIT does not."""
    at = _instant(signal.at)
    day0 = datetime.combine(today, time.min, tzinfo=tz)
    stale = signal.kind == "crm_followup_due" and (at is None or at < day0)
    return (-feed_rank.base(signal.kind), int(stale),
            at.timestamp() if at is not None else float("inf"), signal.signal_id)


async def _crm_gate(user: UserContext) -> None:
    """The kill switch, then the access rule, as the CRM router applies them.
    Each refusal raises ``SourceAbsent`` (§5.8 item 4), and no CRM read runs
    before both pass."""
    from gateway.routes.crm import access, flags

    if not flags.crm_org_allowed(user.organization_id):
        raise needs.SourceAbsent
    try:
        await access.require_crm_access(user)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise needs.SourceAbsent from None
        raise


async def _crm(user: UserContext) -> Found:
    from gateway.routes.crm.aggregate import my_day_rows

    await _crm_gate(user)
    now = needs._now()  # the feed's one clock (needs._now)
    size = PER_APP + 1  # one row more than the cap, so the cap is known
    # The CRM app's own read: the zone and the three reads in ONE session.
    rows = await my_day_rows(user, now=now, limit=size)
    zone, today = rows["zone"], rows["today"]
    tz = ZoneInfo(zone)
    due, quiet, passed = rows["due"], rows["quiet"], rows["passed"]

    signals: list[Signal] = [_followup_signal(r, tz) for r in due]
    signals += [_quiet_deal_signal(r, now) for r in quiet]
    signals += [_close_passed_signal(r, tz) for r in passed]
    if rows.get("digest"):
        signals.append(_digest_signal(rows["digest"]))
    if rows.get("questions"):
        signals.append(_questions_signal(rows["questions"]))
    signals.sort(key=lambda s: _crm_order(s, today, tz))
    full = any(len(part) >= size for part in (due, quiet, passed))
    return Found([], 0, capped=full or len(signals) > PER_APP,
                 signals=lambda: signals[:PER_APP], zone=zone)


# The gate (§5.9 item 13): `feature:crm`. Gated, so it runs only while
# MY_DAY_PROVIDERS lists it (§5.8 item 1). The source id is the app id, so
# the carry needs no `source` field, and it has no Handled read.
register(Provider("crm", ("crm",), _crm, CRM_KINDS, needs=False, gated=True))
# ── People and chat (§5.9 item 14, MD-3f, feed only, gated) ─────────────────
#
# `people` gives `colleague_out`: a co-assignee of a task with a block today
# is away all day today. It calls three reads, one after another, and each
# opens its own tenant session (R5b): `get_user_settings`, `my_calendar` and
# `absences.away_on`. So it opens 3 sessions at most (§5.9 item 7).
#
# `chat` gives `agent_asks`: a question of a run that waits for the member,
# in a room where the member can still send. It reads the chat app's own
# list of the member's questions (one session), then the room of each thread
# (one session each), one after another, 15 threads at most.

#: The kinds of the two sources (§5.9 item 14).
PEOPLE_KINDS = frozenset({"colleague_out"})
CHAT_KINDS = frozenset({"agent_asks"})
#: The threads whose room the chat provider checks, at most (§5.9 item 7).
CHAT_THREADS = 15
#: The prefix of an agent assignee (``projects/agent_dispatch.AGENT_PREFIX``).
#: An agent is never "away", so it never gives `colleague_out`.
AGENT_ASSIGNEE = "agent:"


async def _people(user: UserContext) -> Found:
    from gateway.routes.people.absences import away_on
    from gateway.routes.projects.personal import my_calendar, zone_name
    from gateway.routes.tasks.settings import get_user_settings

    now = needs._now()  # the feed's one clock (needs._now)
    settings = await get_user_settings(user=user)
    zone = zone_name(getattr(settings, "timezone", None))
    tz = ZoneInfo(zone)
    today = now.astimezone(tz).date()
    day0 = datetime.combine(today, time.min, tzinfo=tz)
    tomorrow0 = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    # My open blocks of today, each with its assignees (`attach_assignees`).
    day = await my_calendar(start=day0.isoformat(), end=tomorrow0.isoformat(), user=user)
    me = str(user.email or "").strip().lower()
    # Each co-assignee once, with the first task, by the start of its block.
    shared: dict[str, str] = {}
    for row in sorted(getattr(day, "rows", None) or [],
                      key=lambda r: str(r.get("scheduled_start") or "")):
        for who in row.get("assignees") or []:
            email = str(who or "").strip().lower()
            if not email or email == me or email.startswith(AGENT_ASSIGNEE):
                continue
            shared.setdefault(email, task_entity(str(row["id"]), row.get("title"))["label"])
    away = await away_on(user, list(shared), today) if shared else {}
    # `away_on` gives full absences only. The kind is checked again, so a
    # change of that read can only narrow the card (§5.9 item 14).
    out = [(email, away[email]) for email in shared
           if (away.get(email) or {}).get("kind") in FULL_ABSENCES]
    signals: list[Signal] = []
    if out:
        names = [str(info.get("name") or "").strip() or needs._who(email)
                 for email, info in out]
        title = (f"{names[0]} is away today" if len(names) == 1
                 else f"{len(names)} colleagues are away today")
        signals.append(Signal(
            # The day's id, so the card folds into a Calendar card of today
            # (§4.5), and a dismiss holds for this day only (§5.9 item 4).
            signal_id=f"calendar:{today.isoformat()}", app="people", kind="colleague_out",
            entity={"type": "day", "id": today.isoformat(), "label": "Today"},
            title=title,
            # The facts its template reads (§5.9 item 11), and no state fact.
            facts={"out": names, "task": shared[out[0][0]]},
            href=day_href(today),
            acts=(link("open-day", "Open the day", day_href(today)),),
            expires_at=_iso(tomorrow0),
        ))
    return Found([], 0, capped=False, signals=lambda: signals, zone=zone)


def _chat_href(thread_id: str) -> str:
    """The chat app's link that opens one thread (§5.9 rule 10.3)."""
    return f"/chat?do=open-chat&fill.session={quote(thread_id, safe='')}"


async def _chat(user: UserContext) -> Found:
    from gateway.routes.agent import _resolve_room
    from gateway.routes.chat import _my_waiting_asks
    from orchestrator import pending_ask

    if not pending_ask.durable_asks_enabled():
        # CHAT_DURABLE_ASKS (WS-51) is off: no question is durable, so the
        # source holds nothing to say. A refusal, not a failure.
        raise needs.SourceAbsent("CHAT_DURABLE_ASKS is off")
    org = str(getattr(user, "organization_id", None) or "").strip()
    email = str(user.email or "").strip()
    if not org or not email:
        raise needs.SourceAbsent("no tenant or no member")
    me = email.lower()
    asks = await _my_waiting_asks(org, me)
    if asks is None:
        # A database error. An empty answer would hide each question (WS-51
        # S5), so the source fails and the carry keeps its cards.
        raise RuntimeError("the waiting questions could not be read")
    threads = [(tid, row) for tid, row in asks.items()
               if re.fullmatch(state.SIGNAL_ID_PATTERN, f"chat:{tid}")
               and str(row.get("actor_email") or "").strip().lower() == me]
    signals: list[Signal] = []
    # The room check costs one session for each thread, so the provider
    # checks CHAT_THREADS threads at most (§5.9 item 7). `capped` then says
    # that some threads were not checked, so they may hold more cards.
    for tid, row in threads[:CHAT_THREADS]:
        # The rule of `list_pending_asks`: only while the member may send.
        room = await asyncio.to_thread(_resolve_room, tid, email, org)
        if room is None or not getattr(room, "can_send", False):
            continue
        agent = (str(row.get("newest_agent_name") or row.get("agent_name") or "").strip()
                 or "An agent")
        # The NEWEST question of the thread (§5.9 item 4). So a new question
        # changes the state fingerprint, and a dismissed card comes back.
        asked = _iso(row.get("newest_asked_at") or row.get("asked_at"))
        ids = row.get("request_ids") or [str(row.get("request_id") or "")]
        href = _chat_href(tid)
        signals.append(Signal(
            signal_id=f"chat:{tid}", app="chat", kind="agent_asks",
            entity={"type": "chat", "id": tid, "label": agent},
            # ⚠️ No question text: a model wrote it (§4.1, §5.9 item 14).
            title=f"{agent} asked you a question",
            facts={"waiting_since": asked, "request_ids": sorted(str(i) for i in ids)},
            href=href, acts=(link("open-chat", "Open chat", href),),
            at=asked,
            expires_at=_iso(row.get("latest_expires_at") or row.get("expires_at")),
        ))
    return Found([], 0, capped=len(threads) > CHAT_THREADS,
                 signals=lambda: signals[:PER_APP])


# The gates (§5.9 item 14). Each is gated, so it runs only while
# MY_DAY_PROVIDERS lists it (§5.8 item 1). Each source id is its app id, so
# the carry needs no `source` field. `people` needs the HR grant: a member
# without it gets the source `absent`, and the provider never runs.
register(Provider("people", ("people", "tasks", "projects"), _people, PEOPLE_KINDS,
                  needs=False, gated=True, permission="admin:members:read"))
register(Provider("chat", ("chat",), _chat, CHAT_KINDS, needs=False, gated=True))


# ── Knowledge (WS-61 KB-9a, knowledge_base.md §9, feed only, gated) ─────────
#
# `kb_gap`: a question that two members or more asked inside 30 days, that
# Knowledge could not answer, and that nobody closed. It keys on
# `knowledge:gap:<id>`. Its first act opens the Gaps view with the New page
# form, the question as the title. The source names `knowledge:manage` as
# its permission, because a gap stores no space. A closed Knowledge switch or
# a closed `KNOWLEDGE_GAPS` raises `SourceAbsent` before any read.
# `gaps.my_day_rows` opens ONE tenant session, and this file opens none.
# KB-9b adds `doc_needs_review`, `doc_shared_with_me` and
# `source_sync_failed` to this source. Fences: `tests/unit/test_kb_gaps.py`
# and `tests/unit/test_kb_gaps_r8.py` (R8).

#: The kinds of the source. KB-9a builds the first.
KNOWLEDGE_KINDS = frozenset({"kb_gap"})


def _gap_signal(row: dict[str, Any]) -> Signal:
    """``kb_gap``: one open question with no page. The title is the first
    wording a member typed. The facts are a count and a time."""
    from gateway.routes.knowledge.gaps import GAPS_HREF, write_href

    gap_id = str(row["id"])
    question = str(row.get("question") or "").strip() or "(no words)"
    href = write_href(gap_id)
    return Signal(
        signal_id=f"knowledge:gap:{gap_id}", app="knowledge", kind="kb_gap",
        entity={"type": "gap", "id": gap_id, "label": question}, title=question,
        facts={"askers": int(row.get("ask_count") or 0),
               "last_asked_at": _iso(row.get("last_asked_at"))},
        href=href,
        acts=(link("write-page", "Write a page", href),
              link("open-gaps", "See all gaps", GAPS_HREF)),
        at=_iso(row.get("last_asked_at")),
    )


async def _knowledge(user: UserContext) -> Found:
    from gateway.routes.knowledge import flags as kb_flags
    from gateway.routes.knowledge.gaps import my_day_rows

    if not kb_flags.knowledge_gaps_allowed(user.organization_id):
        raise needs.SourceAbsent("KNOWLEDGE_GAPS does not name the org")
    rows = await my_day_rows(user, PER_APP + 1)
    signals = [_gap_signal(r) for r in rows]
    return Found([], 0, capped=len(signals) > PER_APP,
                 signals=lambda: signals[:PER_APP])


# The gate (§9): `feature:knowledge` and `knowledge:manage`. Gated, so it
# runs only while MY_DAY_PROVIDERS lists `knowledge` (§5.8 item 1). The
# source id is the app id, and it has no Handled read.
register(Provider("knowledge", ("knowledge",), _knowledge, KNOWLEDGE_KINDS, needs=False,
                  gated=True, permission="knowledge:manage"))


# ── The templates (§4.2): code writes every why line ────────────────────────


def _clock(value: Any, tz: ZoneInfo) -> str:
    """``HH:MM``, 24-hour: the one clock of My Day. Today and the Calendar
    draw the same form, so one screen shows one format (MD-1b review)."""
    at = _instant(value)
    if at is None:
        return ""
    local = at.astimezone(tz)
    return f"{local.hour:02d}:{local.minute:02d}"


def _when(value: Any, tz: ZoneInfo, today: date) -> str:
    """A day in words, as the member reads it: today, yesterday, tomorrow,
    on Monday, or on 12 Oct."""
    at = _instant(value)
    if at is None:
        return ""
    return _day_words(at.astimezone(tz).date(), today)


def _day_words(day: date, today: date) -> str:
    """A local date in words, the rule of :func:`_when`."""
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == -1:
        return "yesterday"
    if delta == 1:
        return "tomorrow"
    if abs(delta) <= 6:
        return f"on {day.strftime('%A')}"
    return f"on {day.day} {day.strftime('%b')}"


def _duration(mins: Any) -> str:
    total = max(0, int(mins or 0))
    hours, rest = divmod(total, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h" if not rest else f"{hours} h {rest} min"


def _names(titles: list[str]) -> str:
    if not titles:
        return "another block"
    return titles[0] if len(titles) == 1 else f"{titles[0]} and {len(titles) - 1} more"


#: A notification's words, by its kind (``app/projects/lib/notifications.ts``).
#: ``agent_question`` (WS-55 EA-13f, EA3-46) is the question an outside agent
#: asks. Its ``who`` is the client label, because the actor of that row is the
#: client stamp (EA12-8), and ``needs._who`` reads a stamp as its label. So the
#: line reads "Claude Code asked you a question on this task."
_NOTE_WORDS = {"assigned": "gave you this task", "mention": "mentioned you on this task",
               "comment": "commented on this task", "nudge": "nudged you about this task",
               "agent_question": "asked you a question on this task"}


def _note_clause(facts: dict[str, Any], tz: ZoneInfo, today: date) -> tuple[str, str]:
    kind = str(facts.get("notification") or "")
    words = _NOTE_WORDS.get(kind, "changed this task")
    when = _when(facts.get("at"), tz, today)
    who = facts.get("who") or "Somebody"
    return (f"notification_{kind}" if kind in _NOTE_WORDS else "notification",
            f"{who} {words}{' ' + when if when else ''}.")


def why(kind: str, facts: dict[str, Any], at: Any, now: datetime, tz: ZoneInfo,
        today: date) -> tuple[str, str]:
    """``(why_key, why)``: the template of a kind, filled from ``facts``."""
    if kind == "overdue":
        late = int(facts.get("days_late") or 0)
        if late >= 1:
            return "days_late", (f"Due {_when(facts.get('due_at'), tz, today)}. "
                                 f"{late} day{'' if late == 1 else 's'} late.")
        return "late_today", f"Due at {_clock(facts.get('due_at'), tz)} today."
    if kind == "due_today":
        due = _instant(facts.get("due_at"))
        if due is not None and due - now <= feed_rank.SOON:
            return "due_soon", f"Due in {_duration(math.ceil((due - now).total_seconds() / 60))}."
        return "due_today", f"Due today at {_clock(due, tz)}."
    if kind == "notification":
        return _note_clause(facts, tz, today)
    if kind == "approval":
        when = _when(at, tz, today)
        return "proposed", f"Proposed by {facts.get('proposer') or 'an agent'}" + (
            f" {when}." if when else ".")
    if kind == "needs_reply":
        if _instant(at) is None:
            return "waiting", "It waits for your reply."
        return "asked", f"Asked {_when(at, tz, today)} at {_clock(at, tz)}."
    if kind == "block_soon":
        return "starts_in", (f"Starts in {_duration(facts.get('minutes'))}, "
                             f"at {_clock(facts.get('block_start'), tz)}.")
    if kind == "block_overlap":
        # `overlap_at` is where the overlap starts, which is not where both
        # blocks start, so the line says only that (MD-1b review).
        return "overlap", (f"It overlaps {_names(facts.get('overlaps') or [])}. They overlap "
                           f"from {_clock(facts.get('overlap_at'), tz)} "
                           f"{_when(facts.get('overlap_at'), tz, today)}.")
    if kind == "day_overbooked":
        return "overbooked", (f"{_duration(facts.get('planned_mins'))} planned in a "
                              f"{_duration(facts.get('capacity_mins'))} day.")
    if kind == "no_plan_yet":
        return "no_plan", (f"Your day started at {_clock(facts.get('day_start'), tz)}, "
                           "and nothing is on the calendar.")
    return _md3_why(kind, facts, tz, today)


def _days(n: Any) -> str:
    count = max(0, int(n or 0))
    return f"{count} day" if count == 1 else f"{count} days"


def _absent_line(facts: dict[str, Any], today: date) -> str:
    try:
        day = date.fromisoformat(str(facts.get("absence_day")))
    except ValueError:
        return "Due on a day you are away."
    return f"Due {_day_words(day, today)}. You are away that day."


def _followup_line(facts: dict[str, Any], tz: ZoneInfo, today: date) -> str:
    """The why line of ``crm_followup_due`` (§5.9 item 13). A task holds a
    date and no time (``crm_platform.md`` §19.7), so it names no clock."""
    due = facts.get("due_at")
    when = _when(due, tz, today)
    if not facts.get("all_day"):
        when = f"{when} at {_clock(due, tz)}"
    subject = str(facts.get("subject") or "").strip()
    if subject:
        return f"{subject[:1].upper()}{subject[1:]} is due {when}."
    return f"A follow-up is due {when}."


def _digest_line(facts: dict[str, Any]) -> str:
    """The why line of ``digest_ready`` (CRM-Q28 item 14). A code template:
    it names the week and never shows a word of the model's text."""
    try:
        week = date.fromisoformat(str(facts.get("week_start")))
    except ValueError:
        return "The weekly CRM digest is ready in Reports."
    return (f"The CRM digest of the week from {week.day} {week.strftime('%b')} "
            "is ready in Reports.")


def _questions_line(facts: dict[str, Any]) -> str:
    """The why line of ``crm_open_questions`` (CRM-Q32-4). A code template
    over a count: it shows no question and no model text. CRM-Q32-8: it
    names the answers that members proposed too, and it says "the AI",
    never the internal name of the model (the owner's copy rule)."""
    def _n(key: str) -> int:
        raw = facts.get(key)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0

    n, proposed = _n("count"), _n("proposed")
    asks = ("The AI has 1 new question about your CRM data." if n == 1 else
            f"The AI has {n} new questions about your CRM data." if n > 1 else "")
    if proposed:
        team = ("1 answer proposed by your team." if proposed == 1
                else f"{proposed} answers proposed by your team.")
        return f"{asks} {team}" if asks else team
    return asks or "The AI has new questions about your CRM data."


def _colleague_line(facts: dict[str, Any]) -> str:
    names = [str(n) for n in facts.get("out") or [] if n]
    task = facts.get("task") or "a task"
    if len(names) > 1:
        return (f"{names[0]} and {len(names) - 1} more are away today. "
                f"You share {task} with them.")
    return f"{names[0] if names else 'A colleague'} is away today. You share {task} with them."


def _gap_line(facts: dict[str, Any]) -> str:
    """The why line of ``kb_gap`` (WS-61 KB-9a, ``knowledge_base.md`` §9). A
    code template over a count. The question is the card's title."""
    raw = facts.get("askers")
    n = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0
    who = f"{n} people asked this" if n > 1 else "People asked this"
    return f"{who} in the last 30 days. Nobody has written it down in Knowledge."


#: The why line of each card of the org, a CRM card or a Knowledge gap
#: (KB-9a): its template id and its line.
_ORG_CARD_LINES: dict[str, tuple[str, Any]] = {
    "digest_ready": ("digest_ready", _digest_line),
    "crm_open_questions": ("open_questions", _questions_line),
    "kb_gap": ("gap", _gap_line),
}


def _md3_why(kind: str, facts: dict[str, Any], tz: ZoneInfo,
             today: date) -> tuple[str, str]:
    """The templates of the eleven MD-3 kinds (§5.9 items 11 to 14). MD-3c
    writes them all, so MD-3d to MD-3f add only their providers. Each reads
    the facts that its provider must give, and the spec names them (§5.9,
    "The facts of each template"). WS-53 CRM-Q28 adds ``digest_ready``, a
    CRM kind of its own. An unknown kind raises."""
    if kind == "waiting_quiet":
        n = _days(facts.get("waited_days"))
        who = facts.get("who")
        if who:
            return "waited", f"You have waited {n} on {who}."
        return "waited_anon", f"You have waited {n} for this."
    if kind == "stale_next":
        return "no_change", f"No change for {_days(facts.get('stale_days'))}."
    if kind == "unblocked":
        if facts.get("blocker"):
            return "unblocked", (f"{facts.get('who') or 'Somebody'} closed "
                                 f"{facts['blocker']}, which you waited on.")
        return "unblocked_hidden", "A task that blocked this is closed."
    if kind == "absent_on_due":
        return "absent_on_due", _absent_line(facts, today)
    if kind == "promise_due":
        return "promised", (f"You promised this by mail. It is due "
                            f"{_when(facts.get('due_at'), tz, today)}.")
    if kind == "no_answer":
        return "no_answer", (f"You wrote {_when(facts.get('waiting_since'), tz, today)}. "
                             f"{facts.get('who') or 'They'} "
                             f"{'has' if facts.get('who') else 'have'} not answered.")
    if kind == "crm_followup_due":
        return "followup_due", _followup_line(facts, tz, today)
    if kind == "deal_quiet":
        return "in_stage", (f"In {facts.get('stage') or 'this stage'} for "
                            f"{_days(facts.get('stage_days'))}. The stage limit is "
                            f"{_days(facts.get('stage_limit'))}.")
    if kind == "deal_close_passed":
        return "close_passed", (f"The close date was {_when(facts.get('due_at'), tz, today)}, "
                                "and the deal is still open.")
    if kind == "colleague_out":
        return "colleague_out", _colleague_line(facts)
    if kind in _ORG_CARD_LINES:
        # The two CRM cards of the org (CRM-Q28 and CRM-Q32-4) and the
        # Knowledge gap (KB-9a): one template each over the facts, never a
        # word of a model.
        template, line = _ORG_CARD_LINES[kind]
        return template, line(facts)
    if kind == "agent_asks":
        at = facts.get("waiting_since")
        return "asks", (f"Asked {_when(at, tz, today)} at {_clock(at, tz)}. "
                        "The run waits for your answer.")
    raise KeyError(kind)


def _folded_clause(kind: str, facts: dict[str, Any], tz: ZoneInfo, today: date) -> str:
    """The sentence a folded kind adds to the card's why line (§4.5, and
    §5.9 items 11, 12 and 14 for MD-3)."""
    if kind == "block_soon":
        return f"Its block starts in {_duration(facts.get('minutes'))}."
    if kind == "notification":
        return _note_clause(facts, tz, today)[1]
    if kind == "absent_on_due":
        return "You are away on its due day."
    if kind == "promise_due":
        return "You promised this by mail."
    if kind == "colleague_out":
        return _colleague_line(facts)
    return ""


# ── The fold (§4.5) and the card (§14.1a) ───────────────────────────────────


#: The facts a fingerprint hashes (§14.1a): the ones that change only when
#: the entity changes in substance. ⚠️ A countdown such as ``minutes`` or
#: ``days_late`` never goes in, or the fingerprint moves on every poll and
#: MD-2's "changed in substance" rule (§6.3) fires for nothing.
#: MD-3c adds the names of the MD-3 kinds (§5.9 item 4): ``delegated_at``,
#: ``blocker_ids``, ``absence_day``, ``out`` and ``request_ids``. A card of
#: MD-1 holds none of them, so its fingerprint does not change.
STABLE_FACTS = ("due_at", "block_start", "block_end", "overlap_ids", "note_ids",
                "waiting_since", "delegated_at", "blocker_ids", "absence_day", "out",
                "request_ids")


def _fingerprint(signal_id: str, kind: str, folded: list[str],
                 facts: dict[str, Any]) -> str:
    stable = {name: facts.get(name) for name in STABLE_FACTS if name in facts}
    blob = json.dumps({"entity": signal_id, "kind": kind, "folded": sorted(folded),
                       "facts": stable}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


def _place(href: str) -> str:
    """The place a link opens. The time of a Calendar link (``at``) is where
    the day view scrolls, not another place, so it is left out. A link to the
    same day at two times is one place (MD-3a review)."""
    path, _, query = href.partition("?")
    kept = [part for part in query.split("&") if part and not part.startswith("at=")]
    return f"{path}?{'&'.join(kept)}" if kept else path


def _merge_acts(signals: list[Signal]) -> list[dict[str, Any]]:
    """The acts of every signal on the card, the main kind's first. One act
    for each id, and one link for each place (``_place``). So the main kind's
    link wins a place: a due-today task's "Block time" keeps today, and a
    folded overlap on the same day adds no second Calendar link."""
    acts: list[dict[str, Any]] = []
    ids: set[str] = set()
    places: set[str] = set()
    for signal in signals:
        for act in signal.acts:
            if act["id"] in ids or (act["way"] == "open" and _place(act["href"]) in places):
                continue
            ids.add(act["id"])
            if act["way"] == "open":
                places.add(_place(act["href"]))
            acts.append(dict(act))
    return acts


def _states_entity_facts(kind: str) -> bool:
    """Whether a FOLDED signal of ``kind`` may add a state fact
    (``state.STATE_FACTS``) to its card (§5.9 item 4, MD-3d fix round 1).

    Only a needs kind may (``needs.KIND_ORDER``). Its facts are the
    entity's own: a task's unread notes, a thread's last message. So a new
    note on a dismissed overdue task still brings it back (§6.8 item 1).

    A feed-only kind (the Calendar kinds and every MD-3 kind) may not. Its
    signal comes and goes with a window, a horizon or a failed source, so a
    state fact of it would move the fingerprint with no change to the
    entity, and a dismissed card would come back. Its own facts still drive
    its own why clause, group and urgency, and as a MAIN kind it keeps them
    all. Fence: ``test_feed_mail_kinds.py::TestAFoldNeverMovesTheStateFingerprint``.
    """
    return kind in needs.KIND_ORDER


def build_cards(signals: list[Signal], now: datetime, tz: ZoneInfo,
                today: date) -> list[dict[str, Any]]:
    """One card for each entity (§4.5), in the shape of §14.1a, unordered."""
    by_key: dict[str, dict[str, Signal]] = {}
    for signal in signals:
        # One signal of each kind on an entity. Each provider already gives
        # one signal for each entity and kind: a task's unread notifications
        # are ONE signal, whose `read` act covers every id (`act_refs`).
        by_key.setdefault(signal.signal_id, {}).setdefault(signal.kind, signal)

    parts: dict[str, tuple[list[Signal], dict[str, Any]]] = {}
    for key, kinds in by_key.items():
        ordered = sorted(kinds.values(), key=lambda s: (-feed_rank.base(s.kind), s.kind))
        facts = copy.deepcopy(ordered[0].facts)
        for signal in ordered[1:]:
            for name, value in signal.facts.items():
                if name in state.STATE_FACTS and not _states_entity_facts(signal.kind):
                    continue  # a fold must not move the state fingerprint
                facts.setdefault(name, copy.deepcopy(value))
        parts[key] = (ordered, facts)

    # "When the other task has a card of its own, that card carries the same
    # fact" (§5.1): each task in an overlap names the one it overlaps.
    for key, (ordered, _facts) in list(parts.items()):
        for signal in ordered:
            if signal.kind != "block_overlap":
                continue
            for other_id in signal.facts.get("overlap_ids") or []:
                other = parts.get(f"tasks:{other_id}")
                if other is None or f"tasks:{other_id}" == key:
                    continue
                other_facts = other[1]
                ids = other_facts.setdefault("overlap_ids", [])
                if signal.entity["id"] not in ids:
                    ids.append(signal.entity["id"])
                    other_facts.setdefault("overlaps", []).append(signal.title)

    cards: list[dict[str, Any]] = []
    for key, (ordered, facts) in parts.items():
        main, rest = ordered[0], ordered[1:]
        folded = [s.kind for s in rest]
        why_key, line = why(main.kind, facts, main.at, now, tz, today)
        clauses = [line]
        clauses += [c for c in (_folded_clause(s.kind, s.facts, tz, today) for s in rest) if c]
        if main.kind != "block_overlap" and facts.get("overlaps"):
            clauses.append(f"Its block overlaps {_names(facts['overlaps'])}.")
        card: dict[str, Any] = {"signal_id": key, "app": main.app}
        # The source of the main signal, for the carry of a failed source
        # (MD-3c, §5.9 item 1). Sent only when it is not the app, so a card of
        # an MD-1 source keeps its bytes.
        if main.source and main.source != main.app:
            card["source"] = main.source
        cards.append({
            **card,
            "kind": main.kind,
            "folded": folded,
            # The most urgent group of every kind on the card (§4.5). The main
            # kind still sets the title, the lead of the why line and the base.
            "group": feed_rank.most_urgent(
                [feed_rank.group(main.kind, facts, now, today)]
                + [feed_rank.group(s.kind, s.facts, now, today) for s in rest]),
            # Every kind on the card, for the promise term of the weight
            # (MD-3d, §5.9 item 8). A card with no promise keeps its score.
            "score": max(feed_rank.score(main.kind, facts, s.urgent_at, now,
                                         [k.kind for k in ordered]) for s in ordered),
            "entity": dict(main.entity),
            "title": main.title,
            "why": " ".join(clauses),
            "why_key": why_key,
            "facts": facts,
            "fingerprint": _fingerprint(key, main.kind, folded, facts),
            "urgent_at": main.urgent_at,
            "expires_at": main.expires_at,
            "at": main.at,
            "href": main.href,
            "acts": _merge_acts(ordered),
        })
    return cards


@router.get("/feed")
async def shell_feed(
    limit: int = DEFAULT_FEED_LIMIT,
    user: UserContext = Depends(get_current_user),
    request: Request = None,  # type: ignore[assignment]  # None only in a direct call
    response: Response = None,  # type: ignore[assignment]
) -> Any:
    """My Day's cards for this member, from the apps the member holds."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    size = max(1, min(int(limit), MAX_FEED_LIMIT))
    # MD-5b: the session member joins the org's active set (§8.4 item 22).
    # Gated by MY_DAY_LOOP_ENABLED, runs beside the read, and never raises.
    # Before the cache, so a hit marks the member active too.
    from gateway.my_day_loop import mark_active
    mark_active(user)
    # The read cache (MD-5a, §8.4): a hit runs no provider. Dark by MY_DAY_CACHE.
    return await cache.serve(user, size, lambda: feed_answer(user, size),
                             if_none_match=cache.if_none_match(request), response=response)


@dataclass(frozen=True)
class FeedRun:
    """One run of the providers, folded and joined: the feed's ONE compute
    (WS-58 MD-6a-1, ``my_day_feed.md`` §10.9 item 1).

    ``cards`` are the cards after the state join and before the order.
    ``needs_view`` and ``closed`` are set only for the read (``read=True``):
    the My Day pass runs no Handled read and no needs view.
    """

    cards: list[dict[str, Any]]
    joined: state.Applied
    signals: list[Signal]
    found: dict[str, Found]
    sources: dict[str, str]
    capped: list[str]
    now: datetime
    tz: ZoneInfo
    needs_view: dict[str, Any] | None = None
    closed: handled.HandledRun | None = None


async def feed_cards(user: UserContext, now: datetime | None = None, *,
                     read: bool = False) -> FeedRun:
    """``needs_run``, then ``build_cards``, then ``apply_state``: the cards
    of one member, unordered. The read (:func:`feed_answer`) and the My Day
    pass both call it, so there is no second compute path (§10.9 item 1).

    ``now`` is the pass's clock. None takes the feed's one clock after the
    providers ran, as the read always did. ``read=True`` adds what only the
    read needs: the needs view, taken before the feed-only providers, and
    the Handled reads, beside the state join.
    """
    # The needs providers first, in today's order and on today's budget.
    run = await needs.needs_run(user)
    # The needs view, at the needs route's own default limit, whatever the
    # feed's limit: the client keeps it under the key of ?limit=30.
    needs_view = needs.needs_answer(run, needs.DEFAULT_LIMIT) if read else None
    # Then the feed-only providers, on a budget of their own: two at a time,
    # each with its own limit from its start, under one ceiling (MD-3b).
    # MY_DAY_PROVIDERS picks the gated ones (`needs.feed_only_providers`).
    # The loop's pass takes one slot, so the loop alone cannot fill the pool.
    extra = await needs.run_providers(
        user, needs.feed_only_providers(), budget_s=FEED_BUDGET_S,
        timeout_s=FEED_PROVIDER_TIMEOUT_S, event=needs.FEED_EVENT,
        slots=FEED_CONCURRENCY if read else FEED_PASS_CONCURRENCY,
        ceiling_s=FEED_TOTAL_S)

    sources = {**run.sources, **extra.sources}
    capped = [*run.capped, *extra.capped]
    found = {**run.found, **extra.found}
    signals: list[Signal] = []
    for key, answer in found.items():
        if answer.signals is None:
            continue
        try:
            # Each signal is tagged with its source id (MD-3c, §5.9 item 1).
            signals.extend(dataclasses.replace(s, source=s.source or key)
                           for s in answer.signals()[:PER_APP])
        except Exception as exc:
            # The rows stay in the needs view. Only the feed loses them.
            logger.warning("shell.feed signals failed",
                           extra={"provider": key, "error": type(exc).__name__})
            sources[key] = "failed"

    if now is None:
        now = needs._now()  # the feed's one clock (needs._now)
    tz = state.zone_of(next((a.zone for a in found.values() if a.zone), None))
    today = now.astimezone(tz).date()
    # The state join: after the fold, before the order (§6.8 item 7). The
    # Handled reads run at the same time, inside the join's own budget
    # (MD-2c), so the feed's worst case does not grow.
    cards = build_cards(signals, now, tz, today)
    closed: handled.HandledRun | None = None
    if read:
        joined, closed = await asyncio.gather(
            state.apply_state(user, cards, now),
            handled.handled_run(user, now, budget_s=state.STATE_TIMEOUT_S),
        )
    else:
        joined = await state.apply_state(user, cards, now)
    return FeedRun(joined.cards, joined, signals, found, sources, capped, now, tz,
                   needs_view, closed)


async def feed_answer(user: UserContext, size: int) -> cache.Computed:
    """One run of the providers: the answer, and what the read cache needs
    to keep it (its time edges, and whether item 18 lets it be stored)."""
    fr = await feed_cards(user, read=True)
    joined, closed, now, tz = fr.joined, fr.closed, fr.now, fr.tz
    assert closed is not None  # read=True runs the Handled reads
    cards = feed_rank.order(fr.cards, size)
    counts = {g: sum(1 for c in cards if c["group"] == g) for g in feed_rank.GROUPS}
    body = {
        "cards": cards,
        "groups": [{"id": g, "count": n} for g, n in counts.items() if n],
        "needs": fr.needs_view,
        "sources": fr.sources,
        # The app of each source (MD-3b, §14.1a): a source id such as
        # `crm_followups` is not an app id.
        "source_apps": {s: needs.source_app(s) for s in fr.sources},
        "capped": fr.capped,
        # Never a card and never a group, and not counted in `limit` (MD-2c).
        "handled": closed.entries,
        "handled_ok": closed.ok,
    }
    # The next time edge (§8.4 item 2): what the clock changes, and when.
    edges = [*cache.signal_edges(fr.signals), *cache.day_edges(joined.day, tz, now)]
    edges += [e for e in (needs._instant(a.next_edge) for a in fr.found.values())
              if e is not None]
    if joined.snooze_edge is not None:
        edges.append(joined.snooze_edge)
    return cache.Computed(body, tuple(edges), cache.storable(body, state_ok=joined.ok))
