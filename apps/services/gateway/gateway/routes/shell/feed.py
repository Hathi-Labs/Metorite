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
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
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
#: worst case is ``needs.TOTAL_BUDGET_S`` + this + ``state.STATE_TIMEOUT_S``:
#: 4.0 + 2.4 + 1.5 = 7.9 seconds, under the 8 seconds of the BFF route
#: (``app/api/shell/feed/route.ts`` ``TIMEOUT_MS``). 2.5 would make 8.0, which
#: is no margin. Fence: ``test_shell_feed.py::TestTheBudgetSum``.
FEED_TOTAL_S = 2.4
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
    day = at.astimezone(tz).date()
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
_NOTE_WORDS = {"assigned": "gave you this task", "mention": "mentioned you on this task",
               "comment": "commented on this task", "nudge": "nudged you about this task"}


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
    raise KeyError(kind)


def _folded_clause(kind: str, facts: dict[str, Any], tz: ZoneInfo, today: date) -> str:
    """The sentence a folded kind adds to the card's why line (§4.5)."""
    if kind == "block_soon":
        return f"Its block starts in {_duration(facts.get('minutes'))}."
    if kind == "notification":
        return _note_clause(facts, tz, today)[1]
    return ""


# ── The fold (§4.5) and the card (§14.1a) ───────────────────────────────────


#: The facts a fingerprint hashes (§14.1a): the ones that change only when
#: the entity changes in substance. ⚠️ A countdown such as ``minutes`` or
#: ``days_late`` never goes in, or the fingerprint moves on every poll and
#: MD-2's "changed in substance" rule (§6.3) fires for nothing.
STABLE_FACTS = ("due_at", "block_start", "block_end", "overlap_ids", "note_ids",
                "waiting_since")


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
        cards.append({
            "signal_id": key,
            "app": main.app,
            "kind": main.kind,
            "folded": folded,
            # The most urgent group of every kind on the card (§4.5). The main
            # kind still sets the title, the lead of the why line and the base.
            "group": feed_rank.most_urgent(
                [feed_rank.group(main.kind, facts, now, today)]
                + [feed_rank.group(s.kind, s.facts, now, today) for s in rest]),
            "score": max(feed_rank.score(main.kind, facts, s.urgent_at, now) for s in ordered),
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
        timeout_s=FEED_PROVIDER_TIMEOUT_S, event="shell.feed",
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
            signals.extend(answer.signals()[:PER_APP])
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
