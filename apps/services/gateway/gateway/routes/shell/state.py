"""The life of a card on the server: ``POST /shell/feed/state`` and the state
join of ``GET /shell/feed`` (WS-58 MD-2a).

Spec: ``project-docs/specs/my_day_feed.md`` §6 (as §6.8 runs it), §7 and
§13.1. The table is ``my_day_card_state`` (migration
``258_my_day_card_state.sql``). This module is its only writer.

**The member comes from the session, never from the body** (R5e, R11). The
body names no member and no organization, and its model refuses an unknown
key with 422. Each statement names the bound tenant beside RLS, so an owner
role that bypasses RLS still reads one tenant.

**The state fingerprint** (§6.8 item 1) hashes the entity and its own facts
(``STATE_FACTS``): the due time, the notification ids, the waiting time and
the last message id of a thread. It leaves out the kind, the folded kinds and
the block facts. So a dismissed task stays dismissed when a block folds in,
and when it turns overdue. A new due date brings it back.

**The rules run at read time** (§6.8 item 2). ``RULES`` holds the eight rules
of §6.3 in their order, and the first one that matches decides. ``visits``
counts the visits of one local day, and ``visits_day`` holds that day. The
member's day starts at ``day_start_hour``, so ``feed_day`` is the local date
of the most recent day start.

**The lift instant of rule 4** is ``urgent_at`` less 2 hours
(``feed_rank.SOON``), the instant that the urgency term starts (§7). The
example of §6.3 rule 4 fixes it: a task due at 15:00 comes back at 13:00. A
card the member has not seen is lifted from that instant (§6.5 step 3). A
card the member saw is lifted only when the instant came after the last
sighting, so a card seen while it was already urgent does not move.

**The state never touches the needs view** (§6.8 item 9). The feed computes
``needs`` before the join, so a dismissed or snoozed needs-kind card still
counts in the bell.

Fences: ``tests/unit/test_my_day_state.py`` (each rule, their order, the
POST contract) and ``tests/unit/test_my_day_state_r8.py`` (a real Postgres,
FORCE RLS, two organizations).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.shell import feed_rank, needs
from gateway.routes.shell.needs import _instant
from gateway.routes.shell.search import router
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

#: The POST contract (§6.8).
MAX_ITEMS = 50
SIGNAL_ID_PATTERN = r"^[a-z_]+:[^\s]{1,200}$"
STATE_FP_PATTERN = r"^[0-9a-f]{8,64}$"
KIND_PATTERN = r"^[a-z_]{1,40}$"
Action = Literal["seen", "snooze", "dismiss", "not_useful", "acted"]
Until = Literal["later_today", "tomorrow", "next_week", "on_change"]

#: Two sightings within this time are one visit (§6.2).
VISIT_GAP = timedelta(minutes=30)
#: Rule 8: a seen card with this many visits in one day and no act folds.
FOLD_VISITS = 3
#: Rule 5: a needs-reply thread older than this returns each day.
STALE_REPLY = timedelta(hours=48)
#: "Later today" is this long, or the day end, whichever comes first (§6.4).
LATER_TODAY = timedelta(hours=3)
#: The column defaults of ``user_settings`` (``77_gtd_calendar_prefs.sql``),
#: for a member with no Calendar pane (§6.8 item 8).
DEFAULT_DAY_START = 7
DEFAULT_DAY_END = 22

#: The facts the state fingerprint hashes (§6.8 item 1). ⚠️ No block fact
#: and no countdown: a block that folds in, or a day more late, must not
#: bring a dismissed card back.
STATE_FACTS = ("due_at", "note_ids", "waiting_since", "message_id")

#: The columns of one state row, in the order of the table.
COLUMNS = ("kind", "state", "dismiss_reason", "fingerprint", "visits", "visits_day",
           "first_seen_at", "last_seen_at", "acted_at", "snoozed_until", "snooze_on_change")


def state_fp(signal_id: str, facts: dict[str, Any]) -> str:
    """The state fingerprint of one card (§6.8 item 1)."""
    own = {name: facts.get(name) for name in STATE_FACTS if facts.get(name) is not None}
    blob = json.dumps({"entity": signal_id, "facts": own}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


# ── The member's day ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Day:
    """The member's zone and day hours, read once for each request."""

    tz: ZoneInfo
    start_hour: int = DEFAULT_DAY_START
    end_hour: int = DEFAULT_DAY_END
    #: ISO weekdays. Only the POST reads them, for a snooze.
    working_days: frozenset[int] = field(default_factory=lambda: frozenset({1, 2, 3, 4, 5}))

    def feed_day(self, now: datetime) -> date:
        """The local date of the most recent day start. ``visits_day`` holds
        it, and rules 5 to 7 run when it moves on."""
        return (now.astimezone(self.tz) - timedelta(hours=self.start_hour)).date()

    def at(self, day: date, hour: int) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self.tz) + timedelta(hours=hour)

    def next_working_day(self, after: date) -> date:
        for step in range(1, 15):
            candidate = after + timedelta(days=step)
            if candidate.isoweekday() in self.working_days:
                return candidate
        return after + timedelta(days=1)  # a schedule with no working day


def _zone(name: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ValueError, OSError):
        return ZoneInfo("UTC")


def _hour(value: Any, default: int, high: int) -> int:
    try:
        return min(high, max(0, int(value)))
    except (TypeError, ValueError):
        return default


def snoozed_until(until: str, now: datetime, day: Day) -> datetime | None:
    """The end of a snooze (§6.8, the POST contract). The server computes it
    in the member's zone. It never takes a time from the client."""
    today = now.astimezone(day.tz).date()
    if until == "on_change":
        return None
    if until == "later_today":
        end = day.at(today, day.end_hour)
        if now >= end:
            return day.at(day.next_working_day(today), day.start_hour)
        return min(now + LATER_TODAY, end)
    if until == "tomorrow":
        return day.at(day.next_working_day(today), day.start_hour)
    if until == "next_week":
        monday = today + timedelta(days=8 - today.isoweekday())
        for step in range(14):
            candidate = monday + timedelta(days=step)
            if candidate.isoweekday() in day.working_days:
                return day.at(candidate, day.start_hour)
        return day.at(monday, day.start_hour)
    raise ValueError(until)


# ── The rules of §6.3, as §6.8 runs them ────────────────────────────────────


@dataclass(frozen=True)
class Context:
    now: datetime
    day: Day
    #: ``my_day.resurface`` (MD-6a). In MD-2 it has no answer (§6.8 item 3).
    resurface: Callable[[dict[str, Any]], bool | None] = lambda _card: None

    @property
    def today(self) -> date:
        return self.day.feed_day(self.now)


@dataclass(frozen=True)
class Outcome:
    """What the rules decided for one card. ``rule`` is the rule that
    matched, or None for the fallback."""

    rule: int | None
    visible: bool
    state: str = "new"
    group: str | None = None  # None keeps the card's own group
    visits: int = 0
    updated: bool = False
    still_open: bool = False


def held(row: dict[str, Any], fp: str, now: datetime) -> bool:
    """Whether rule 1 or rule 2 hides this card now."""
    same = row.get("fingerprint") == fp
    if row.get("state") == "snoozed":
        if row.get("snooze_on_change"):
            return same
        until = _instant(row.get("snoozed_until"))
        return until is not None and now < until
    return row.get("state") == "dismissed" and same


def _changed(row: dict[str, Any] | None, fp: str) -> bool:
    return row is not None and row.get("fingerprint") != fp


def _visits_today(row: dict[str, Any], ctx: Context) -> int:
    return int(row.get("visits") or 0) if row.get("visits_day") == ctx.today else 0


def _effective(row: dict[str, Any] | None) -> str:
    """The state that rules 4 to 8 read, once rules 1 to 3 did not match. A
    snooze that ended brings the card back as new."""
    if row is None or row.get("state") == "snoozed":
        return "new"
    return "later" if row.get("state") == "later" else "seen"


def _rule_1_snooze_holds(card, row, fp, ctx):
    if row is not None and row.get("state") == "snoozed" and held(row, fp, ctx.now):
        return Outcome(1, False)
    return None


def _rule_2_dismiss_holds(card, row, fp, ctx):
    if row is not None and row.get("state") == "dismissed" and held(row, fp, ctx.now):
        return Outcome(2, False)
    return None


def _rule_3_changed_returns_new(card, row, fp, ctx):
    if _changed(row, fp):
        return Outcome(3, True, "new", visits=0, updated=True)
    return None


def _rule_4_urgency_lifts(card, row, fp, ctx):
    urgent = _instant(card.get("urgent_at"))
    if urgent is None:
        return None
    lift = urgent - feed_rank.SOON
    if lift > ctx.now:
        return None
    state = _effective(row)
    if state == "new":
        return Outcome(4, True, "new", group="now", visits=0)
    seen = _instant(row.get("last_seen_at")) if row else None
    if seen is not None and seen < lift:
        return Outcome(4, True, "seen", group="now", visits=_visits_today(row, ctx),
                       still_open=True)
    return None


def _returns_daily(card: dict[str, Any], ctx: Context) -> bool:
    kinds = {card.get("kind"), *(card.get("folded") or [])}
    if "overdue" in kinds:
        return True
    if "needs_reply" in kinds:
        since = _instant((card.get("facts") or {}).get("waiting_since"))
        return since is not None and ctx.now - since > STALE_REPLY
    return False


def _rule_5_fixed_daily_return(card, row, fp, ctx):
    if _effective(row) != "later" or not _returns_daily(card, ctx):
        return None
    visits = _visits_today(row, ctx)
    if visits >= FOLD_VISITS:
        return None
    return Outcome(5, True, "seen", visits=visits)


def _rule_6_judged_return(card, row, fp, ctx):
    if _effective(row) != "later":
        return None
    if ctx.resurface(card) is True:
        return Outcome(6, True, "seen", visits=0)
    return Outcome(6, True, "later", group="later", visits=_visits_today(row, ctx))


def _rule_7_seen_starts_fresh(card, row, fp, ctx):
    if _effective(row) == "seen" and row.get("visits_day") != ctx.today:
        return Outcome(7, True, "seen", visits=0)
    return None


def _rule_8_three_visits_fold(card, row, fp, ctx):
    if (row is not None and row.get("state") == "seen"
            and int(row.get("visits") or 0) >= FOLD_VISITS):
        return Outcome(8, True, "later", group="later", visits=int(row["visits"]))
    return None


#: The eight rules of §6.3, in their order. The first that matches decides.
RULES = (
    _rule_1_snooze_holds,
    _rule_2_dismiss_holds,
    _rule_3_changed_returns_new,
    _rule_4_urgency_lifts,
    _rule_5_fixed_daily_return,
    _rule_6_judged_return,
    _rule_7_seen_starts_fresh,
    _rule_8_three_visits_fold,
)


def decide(card: dict[str, Any], row: dict[str, Any] | None, ctx: Context) -> Outcome:
    """The outcome of the first rule that matches, or the fallback."""
    fp = card["state_fp"]
    for rule in RULES:
        outcome = rule(card, row, fp, ctx)
        if outcome is not None:
            return outcome
    state = _effective(row)
    if state == "new":
        return Outcome(None, True, "new", visits=0)
    return Outcome(None, True, "seen", visits=int(row.get("visits") or 0) if row else 0)


def expired(card: dict[str, Any], now: datetime) -> bool:
    at = _instant(card.get("expires_at"))
    return at is not None and at <= now


def join(cards: list[dict[str, Any]], rows: dict[str, dict[str, Any]],
         ctx: Context) -> list[dict[str, Any]]:
    """The state join (§6.8 item 7): after the fold, before the order. A
    snoozed, dismissed or expired card leaves here, so it takes no slot. Each
    card that stays gains ``state``, ``updated``, ``still_open``, ``visits``
    and ``state_fp`` (§14.1a), and loses its fatigue (§7)."""
    out: list[dict[str, Any]] = []
    for card in cards:
        card = {**card, "state_fp": state_fp(card["signal_id"], card.get("facts") or {})}
        if expired(card, ctx.now):
            continue
        row = rows.get(card["signal_id"])
        outcome = decide(card, row, ctx)
        if not outcome.visible:
            continue
        acted = row is not None and row.get("state") == "acted"
        card.update({
            "state": outcome.state,
            "updated": outcome.updated,
            "still_open": outcome.still_open,
            "visits": outcome.visits,
            "score": int(card["score"]) - (0 if acted else feed_rank.fatigue(outcome.visits)),
        })
        if outcome.group is not None:
            card["group"] = outcome.group
        out.append(card)
    return out


# ── The POST: what a member's act writes ────────────────────────────────────


class StateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(pattern=SIGNAL_ID_PATTERN)
    state_fp: str = Field(pattern=STATE_FP_PATTERN)
    kind: str = Field(pattern=KIND_PATTERN)
    action: Action
    until: Until | None = None

    @model_validator(mode="after")
    def _until_only_on_snooze(self) -> StateItem:
        if self.action == "snooze" and self.until is None:
            raise ValueError("a snooze names its until")
        if self.action != "snooze" and self.until is not None:
            raise ValueError("only a snooze carries until")
        if self.kind not in feed_rank.BASE:
            raise ValueError(f"unknown kind: {self.kind}")
        return self


class StateBody(BaseModel):
    """The body of ``POST /shell/feed/state``, and nothing else (§6.8)."""

    model_config = ConfigDict(extra="forbid")

    items: list[StateItem] = Field(max_length=MAX_ITEMS)


_BLANK: dict[str, Any] = {
    "state": None, "dismiss_reason": None, "fingerprint": None, "visits": 0,
    "visits_day": None, "first_seen_at": None, "last_seen_at": None, "acted_at": None,
    "snoozed_until": None, "snooze_on_change": False,
}


def next_row(old: dict[str, Any] | None, item: StateItem, now: datetime,
             day: Day) -> dict[str, Any] | None:
    """The row that one item writes, or None for no write.

    A ``seen`` of a card that rule 1 or rule 2 still hides writes nothing. The
    visit rule is the server's: two sightings within 30 minutes are one
    visit. The sighting that gives a card its third visit of the day writes
    ``later`` (§6.8 item 5), and ``later`` stays until the card changes.
    """
    fp = item.state_fp
    row = {**_BLANK, **(old or {}), "kind": item.kind, "fingerprint": fp}
    changed = _changed(old, fp)
    if item.action == "seen":
        if old is not None and not changed and held(old, fp, now):
            return None
        today = day.feed_day(now)
        fresh = old is None or changed or old.get("state") in ("snoozed", "dismissed")
        last = _instant(old.get("last_seen_at")) if old else None
        if fresh or old.get("visits_day") != today:
            visits = 1
        elif last is None or now - last >= VISIT_GAP:
            visits = int(old.get("visits") or 0) + 1
        else:
            visits = int(old.get("visits") or 0)
        state = "later" if not fresh and old.get("state") == "later" else "seen"
        if state == "seen" and visits >= FOLD_VISITS:
            state = "later"
        row.update(state=state, visits=visits, visits_day=today, last_seen_at=now,
                   first_seen_at=row["first_seen_at"] or now, dismiss_reason=None,
                   snoozed_until=None, snooze_on_change=False)
        return row
    if item.action == "snooze":
        row.update(state="snoozed", dismiss_reason=None,
                   snoozed_until=snoozed_until(str(item.until), now, day),
                   snooze_on_change=item.until == "on_change")
        return row
    if item.action in ("dismiss", "not_useful"):
        row.update(state="dismissed", dismiss_reason=item.action,
                   snoozed_until=None, snooze_on_change=False)
        return row
    # `acted` is a record, never a hide (§6.8 item 4).
    row.update(state="acted", acted_at=now, dismiss_reason=None,
               snoozed_until=None, snooze_on_change=False)
    return row


# ── The database ────────────────────────────────────────────────────────────

#: The bound tenant, the one form of ``routes/tasks/settings.py``. It sits
#: beside RLS, not instead of it.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_READ_SQL = (
    f"SELECT signal_id, {', '.join(COLUMNS)} FROM my_day_card_state "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :member "
    "AND signal_id IN :ids"
)
_UPSERT_SQL = (
    f"INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
    f"{', '.join(COLUMNS)}, updated_at) "
    f"VALUES ({_BOUND_ORG}, :member, :signal_id, "
    f"{', '.join(':' + c for c in COLUMNS)}, now()) "
    "ON CONFLICT (organization_id, member_email, signal_id) DO UPDATE SET "
    f"{', '.join(f'{c} = EXCLUDED.{c}' for c in COLUMNS)}, updated_at = now()"
)


async def _read_rows(db: Any, member: str, ids: list[str], *,
                     lock: bool = False) -> dict[str, dict[str, Any]]:
    from sqlalchemy import bindparam, text

    if not ids:
        return {}
    sql = text(_READ_SQL + (" FOR UPDATE" if lock else "")).bindparams(
        bindparam("ids", expanding=True))
    result = await db.execute(sql, {"member": member, "ids": sorted(set(ids))})
    return {str(r.signal_id): dict(r._mapping) for r in result}


async def _hours(user: UserContext, db: Any | None) -> Day:
    """The zone and the day hours (§6.8 item 8). A member who holds
    ``feature:tasks`` reads them from ``get_user_settings``. Any other member
    reads the zone from the lens, with the column defaults."""
    if user.has_permission("feature:tasks"):
        from gateway.routes.tasks.settings import get_user_settings

        settings = await get_user_settings(user=user)
        return Day(_zone(getattr(settings, "timezone", None)),
                   _hour(getattr(settings, "day_start_hour", None), DEFAULT_DAY_START, 23),
                   _hour(getattr(settings, "day_end_hour", None), DEFAULT_DAY_END, 24))
    from gateway.routes.projects.personal import stored_zone

    zone = await stored_zone(db, user.email) if db is not None else None
    return Day(_zone(zone))


async def _working_days(db: Any, user: UserContext) -> frozenset[int]:
    """The member's working days (``work_schedule.effective_schedule``): the
    directory row's schedule, or the org's policy for a member with none."""
    from gateway.routes.people.core import find_self_row
    from gateway.work_schedule import effective_schedule, load_policy, person_schedule

    policy = await load_policy(db)
    try:
        row = await find_self_row(db, user)
    except Exception as exc:
        logger.warning("shell.feed.state working days unknown",
                       extra={"error": type(exc).__name__})
        row = None
    schedule = person_schedule(policy, row) if row is not None else effective_schedule(
        policy, None)
    return frozenset(int(d) for d in schedule.get("days") or ()) or frozenset({1, 2, 3, 4, 5})


async def apply_state(user: UserContext, cards: list[dict[str, Any]],
                      now: datetime) -> list[dict[str, Any]]:
    """The state join of ``GET /shell/feed``. The read writes nothing.

    A failed read keeps the feed: every card reads as new, and the failure
    goes to the log. A blank My Day would cost more than a dismissed card
    that shows again for one poll.
    """
    member = user.email or ""
    ids = [c["signal_id"] for c in cards]
    try:
        tasks_hours = user.has_permission("feature:tasks")
        day = await _hours(user, None) if tasks_hours else None
        async with _tenant_session() as db:
            if day is None:
                day = await _hours(user, db)
            rows = await _read_rows(db, member, ids)
    except Exception as exc:
        logger.warning("shell.feed state read failed", extra={"error": type(exc).__name__})
        return join(cards, {}, Context(now, Day(ZoneInfo("UTC"))))
    return join(cards, rows, Context(now, day))


async def write_state(user: UserContext, items: list[StateItem], now: datetime) -> int:
    """Write each item for the session's member, in one transaction."""
    from sqlalchemy import text

    member = user.email or ""
    tasks_hours = user.has_permission("feature:tasks")
    day = await _hours(user, None) if tasks_hours else None
    written = 0
    async with _tenant_session() as db:
        if day is None:
            day = await _hours(user, db)
        if any(i.action == "snooze" for i in items):
            day = Day(day.tz, day.start_hour, day.end_hour, await _working_days(db, user))
        rows = await _read_rows(db, member, [i.signal_id for i in items], lock=True)
        for item in items:
            new = next_row(rows.get(item.signal_id), item, now, day)
            if new is None:
                continue
            rows[item.signal_id] = new
            await db.execute(text(_UPSERT_SQL), {
                "member": member, "signal_id": item.signal_id,
                **{c: new[c] for c in COLUMNS}})
            written += 1
    return written


@router.post("/feed/state")
async def post_feed_state(
    body: StateBody,
    user: UserContext = Depends(get_current_user),
) -> dict[str, int]:
    """Record what the member did with some cards (§6.8, the POST contract).
    The member is the session's. The body names no member and no org."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not body.items:
        return {"written": 0}
    try:
        written = await write_state(user, body.items, needs._now())
    except Exception as exc:
        logger.warning("shell.feed state write failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"written": written}
