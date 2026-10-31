"""The reminder loop: fire, check back, brief, reschedule, give up (WS-66 RM-2).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.3 and §5.4 ·
D114. The shape is ``routes/email/automation/send_later.py``: a tick every
:data:`TICK_SECONDS`, the one tenant-discovery read of the orgs, then
``tenant_session(org)`` for each org and a claim with ``FOR UPDATE SKIP
LOCKED``. It does NOT copy the workflows scheduler, which is still on the
unbound ``get_db()``.

**Two sets of rows** (§5.4). ``state = 'scheduled' AND fire_at <= now``
fires. ``state = 'due' AND kind = 'action' AND next_check_at <= now`` takes
the next step of the ladder.

**The ladder of an ``action`` reminder.** Each step is a row move and its
``reminder_events`` row, in ONE transaction:

1. ``fired`` at ``fire_at``. The state turns ``due``. ``first_fired_at`` is
   set on the first fire only. ``next_check_at`` is the fire plus the grace
   (``slot.grace``), or plus 15 minutes for an ``important`` reminder.
2. ``check_back`` at ``next_check_at``. Then ``next_check_at`` is the next
   start of the member's day, the brief. An ``important`` reminder checks
   back every hour, 4 times at most, before the brief.
3. ``briefed`` at the next ``day_start_hour``. Then, in the same
   transaction:
4. ``rescheduled`` with ``{"by": "metorite"}``: ``slot.next_slot`` gives the
   new ``fire_at``, the state turns ``scheduled``, and ``cycles`` gains 1.
5. ``gave_up`` when ``cycles`` reached :data:`MAX_CYCLES` at the brief, or
   :data:`GIVE_UP_AFTER` passed after ``first_fired_at`` at any step. The
   state turns ``unconfirmed``. ``resolution`` does not change.

A ``notice`` reminder has step 1 only: it turns ``due`` with no
``next_check_at``.

**Quiet hours** (``slot.quiet``, the Reach rule). Step 1 fires at the
member's own time, also in quiet hours (D114.3). Steps 2 to 5 never land in
quiet hours: a check time that would land there moves to the next day start,
and a step that comes due in quiet hours waits, with nothing written.

**Two workers never fire one row twice.** The claim names the row's id AND
its due predicate, with ``FOR UPDATE SKIP LOCKED``. A second worker skips a
row the first one holds, and after the first commits the predicate no longer
matches.

**A member who is not active is skipped** (§5.4, RM-1 review). The check is
``my_day_loop.departed``, cached for each member for one tick. A failed read
skips the member for the tick and says nothing about them. Their rows stay as
they are, so no step fires and no push goes.

**Tenancy (R5).** The orgs come from ``email_ingestion.scheduler.
_list_organizations``, the one discovery read that ``send_later`` uses.
Every statement runs inside ``tenant_session(org)``, so RLS binds it, and
the member is the row's own ``member_email``, which the route took from the
session. After each write the loop bumps the member's My Day version
(``cache.bump_member_safe``), so the feed reads the new card.

**The web push** (WS-66 RE-9b, §5.6 "Settled 2026-10-30 (RE-9)"). Steps 1,
2 and 5 each plan ONE web push in their own transaction, through
``reach.push_channel.plan_reminder_push`` on the step's session ``db``, keyed
by the ledger row of the step. A step more than the TTL of its kind after
its due time plans no push (ruling j). The loop never calls the sender
itself, and it holds no session while a push goes out. A crash leaves a
planned row, and the next tick sends it. Both need ``WEB_PUSH_ENABLED`` and
``calendar_reminders``, and no ``REACH_*`` flag.

**Fires first, then one bounded send phase** (ruling k). :func:`run_tick`
takes the steps of EVERY org first, with no network call. Then one send
phase calls ``push_channel.send_planned`` for each org, under one deadline
for the whole tick: no row starts after :data:`SEND_PHASE_S`. So a stalled
push service in one org never delays the fires of a later org. A row that
did not start stays planned, and the next tick sends it. No
``asyncio.wait_for`` cancels a send: ``push.sender.send_to_member`` bounds
itself with ``SEND_BUDGET_S``, and a cancel in its middle would leave its
device sends running and its row claimed.

**No org starves** (ruling k). Each tick starts the send phase at the next
org (:data:`_send_turn`). In the first round, each org may start rows only
in its fair share of the time left, and always one row. A second round
gives the time still left to the orgs that still hold rows.

**Dark by default.** With ``calendar_reminders`` off, :func:`start_reminder_loop`
starts no task, and :func:`run_tick` does nothing.

Fence: ``tests/unit/test_reminder_loop.py`` (R8, frozen clock, two orgs, two
workers), ``tests/unit/test_reminder_slot.py`` (pure) and
``tests/unit/test_reach_push.py`` (R8, the web push).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import text

from gateway.reach import push_channel
from gateway.reminders import slot

_log = get_logger("reminders.loop")

#: Seconds between two ticks.
TICK_SECONDS = 60
#: The rows that one listing of one org takes.
BATCH = 50
#: The listings of one org in one tick, at most.
ROUNDS = 4
#: The seconds that a stop waits for a tick in progress.
STOP_GRACE_SECONDS = 30
#: The seconds in which the send phase of one tick may START a row (ruling
#: k). A row that started runs to its end, inside the 20 s of
#: ``push.sender.SEND_BUDGET_S``. A fire comes within one tick of
#: ``fire_at``, and its ``send_to_member`` call starts inside the send phase
#: of that tick. So ``TICK_SECONDS + SEND_PHASE_S`` (90 s) bounds the call,
#: the 90 s of §5.6. A healthy push service sends each row in well under a
#: second, so the call usually starts a few seconds after the fire.
SEND_PHASE_S = 30

#: Step 5 (§5.4): give up at the brief once Metorite moved the reminder this
#: many times, or at any step once this long passed after the first fire.
MAX_CYCLES = 2
GIVE_UP_AFTER = timedelta(hours=72)
#: The check-backs before the brief: one, or four for ``important`` (§5.4).
CHECKS = 1
IMPORTANT_CHECKS = 4
#: The ladder of an ``important`` reminder: the first check-back, then each
#: next one.
IMPORTANT_FIRST = timedelta(minutes=15)
IMPORTANT_EVERY = timedelta(hours=1)

#: The five events that this loop writes, all in migration 307's CHECK.
EVENTS: tuple[str, ...] = ("fired", "check_back", "briefed", "rescheduled", "gave_up")

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()
#: The turn of the send phase: each tick starts at the next org, so an org
#: that fills the phase on every tick starves no other org (ruling k). Each
#: process keeps its own turn.
_send_turn = 0


def loop_enabled() -> bool:
    """The switch, ``calendar_reminders``. Its one reader is
    ``reminders.reminders_enabled``."""
    from gateway.routes.projects.reminders import reminders_enabled

    return reminders_enabled()


def _now() -> datetime:
    """The loop's one clock. A test freezes THIS."""
    return datetime.now(UTC)


def _monotonic() -> float:
    """The stopwatch of the send phase. A test advances THIS."""
    return time.monotonic()


async def _orgs() -> list[str]:
    from email_ingestion.scheduler import _list_organizations

    return await _list_organizations()


# ── The SQL ─────────────────────────────────────────────────────────────────
#
# RLS binds each statement to the org of `tenant_session(org)`. The loop acts
# for every member of the org, so a statement names the row by its id.

#: The rows that may need a step now, oldest first. Members who are not
#: active (`skip`), the `due` rows of members in quiet hours (`waiting`) and
#: the rows already tried in this tick (`seen`) stay out.
_CANDIDATES_SQL = (
    "SELECT id, member_email, state FROM reminders "
    "WHERE ((state = 'scheduled' AND fire_at <= :now) "
    "    OR (state = 'due' AND kind = 'action' AND next_check_at <= :now "
    "        AND NOT (member_email = ANY(CAST(:waiting AS text[]))))) "
    "  AND NOT (member_email = ANY(CAST(:skip AS text[]))) "
    "  AND NOT (id = ANY(CAST(:seen AS uuid[]))) "
    "ORDER BY CASE WHEN state = 'scheduled' THEN fire_at ELSE next_check_at END, id "
    "LIMIT :batch"
)

#: ONE row, locked for this transaction, only while it is still due. A row
#: that another worker holds is skipped, and a row that another worker moved
#: no longer matches.
_CLAIM_SQL = (
    "SELECT * FROM reminders WHERE id = CAST(:id AS uuid) "
    "AND ((state = 'scheduled' AND fire_at <= :now) "
    "  OR (state = 'due' AND kind = 'action' AND next_check_at <= :now)) "
    "FOR UPDATE SKIP LOCKED"
)

#: The check-backs since the newest fire. The ledger's `at` is the clock of
#: each write (`clock_timestamp()`), so the order is the order of the writes.
#: A fire and a check-back are never one transaction, so two never tie.
_CHECKS_SQL = (
    "SELECT count(*) FROM reminder_events "
    "WHERE reminder_id = CAST(:id AS uuid) AND event = 'check_back' "
    "AND at > COALESCE((SELECT max(f.at) FROM reminder_events f "
    "  WHERE f.reminder_id = CAST(:id AS uuid) AND f.event = 'fired'), "
    "  '-infinity'::timestamptz)"
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# ── The member ──────────────────────────────────────────────────────────────


async def _is_active(org: str, member: str) -> bool:
    """True when *member* is active in *org* (``my_day_loop.departed``). A
    failed read answers False for this tick and logs, because it does not
    say that the member left."""
    from acb_auth.access import IdentityUnavailable

    from gateway.my_day_loop import IdentityReadFailed, departed

    try:
        reason = await departed(org, member)
    except (IdentityUnavailable, IdentityReadFailed):
        _log.warning("reminders.member_read_failed")
        return False
    except Exception as exc:  # the status read failed: skip for one tick
        _log.warning("reminders.member_check_failed", error=type(exc).__name__)
        return False
    if reason is not None:
        _log.info("reminders.member_skipped", reason=reason)
        return False
    return True


async def member_day(db: Any, member: str) -> Any:
    """The member's ``state.Day`` (the zone and the day hours), read from
    their settings row of the bound tenant on *db*. The read is
    ``tasks.settings.member_settings``, the body of ``GET /tasks/settings``,
    and the rule is ``state.settings_day``, the one of the feed."""
    from gateway.routes.shell.state import settings_day
    from gateway.routes.tasks.settings import member_settings

    return settings_day(await member_settings(db, member))


async def member_busy(db: Any, member: str, now: datetime,
                      base: datetime | None = None) -> slot.Busy:
    """The member's open blocks on *db*, for ``slot.next_slot``: the
    planner's own read of a window (``planning.LENS_SOURCE.busy_window``).

    Option 3 of the slot looks from *now*, and option 2 from *base*, the
    reminder's own time. So the read covers the days after each, in one
    window when they meet and in two when *base* is far from *now*."""
    from gateway.routes.projects.planning import LENS_SOURCE

    start, end = now - slot.BUSY_BEFORE, now + slot.BUSY_AFTER
    windows = [(start, end)]
    if base is not None:
        low, high = base - slot.BUSY_BEFORE, base + slot.BUSY_AFTER
        if low > end or high < start:
            windows.append((low, high))
        else:
            windows = [(min(start, low), max(end, high))]
    rows: list[Any] = []
    for low, high in windows:
        rows.extend(await LENS_SOURCE.busy_window(db, member, low, high))
    return slot.busy_blocks(rows)


# ── One row ─────────────────────────────────────────────────────────────────


async def _ledger(db: Any, row: Any, event: str, detail: dict[str, Any]) -> str:
    """One ledger row of a step. Answers its id, the second half of the key of
    the step's web push (RE-9b)."""
    from gateway.routes.projects.reminders import _ledger as write

    return await write(db, row.id, event, {**detail, "by": "metorite"})


async def _fire(db: Any, row: Any, day: Any, now: datetime) -> str:
    """Step 1: ``due``, ``first_fired_at`` once, and the first check time."""
    check_at: datetime | None = None
    if row.kind == "action":
        wait = IMPORTANT_FIRST if row.important else slot.grace(row.estimate_minutes)
        check_at = slot.awake(day, now + wait)
    await db.execute(text(
        "UPDATE reminders SET state = 'due', "
        "first_fired_at = COALESCE(first_fired_at, :now), "
        "next_check_at = :check, updated_at = now() "
        "WHERE id = CAST(:id AS uuid)"),
        {"now": now, "check": check_at, "id": str(row.id)})
    event_id = await _ledger(db, row, "fired", {
        "fire_at": _iso(row.fire_at), "at": _iso(now),
        "next_check_at": _iso(check_at), "cycles": int(row.cycles),
    })
    await push_channel.plan_reminder_push(db, row, event_id, "reminder_due", day, now,
                                          due_at=row.fire_at)
    return "fired"


async def _give_up(db: Any, row: Any, day: Any, now: datetime, reason: str) -> str:
    """Step 5: ``unconfirmed``. Nothing more, until the member answers."""
    await db.execute(text(
        "UPDATE reminders SET state = 'unconfirmed', next_check_at = NULL, "
        "updated_at = now() WHERE id = CAST(:id AS uuid)"), {"id": str(row.id)})
    event_id = await _ledger(db, row, "gave_up", {
        "reason": reason, "at": _iso(now), "cycles": int(row.cycles),
        "from_state": row.state,
    })
    await push_channel.plan_reminder_push(db, row, event_id, "reminder_unconfirmed", day, now,
                                          due_at=row.next_check_at)
    return "gave_up"


async def _check_back(db: Any, row: Any, day: Any, now: datetime, done: int) -> str:
    """Step 2: one check-back, and the time of the next step."""
    limit = IMPORTANT_CHECKS if row.important else CHECKS
    number = done + 1
    if row.important and number < limit:
        next_at = slot.awake(day, now + IMPORTANT_EVERY)
    else:
        next_at = slot.next_day_start(day, now)
    await db.execute(text(
        "UPDATE reminders SET next_check_at = :next, updated_at = now() "
        "WHERE id = CAST(:id AS uuid)"), {"next": next_at, "id": str(row.id)})
    event_id = await _ledger(db, row, "check_back", {
        "number": number, "at": _iso(now), "next_check_at": _iso(next_at),
    })
    await push_channel.plan_reminder_push(db, row, event_id, "reminder_check", day, now,
                                          due_at=row.next_check_at)
    return "check_back"


async def _brief_and_move(db: Any, row: Any, day: Any, now: datetime) -> str:
    """Steps 3 and 4: the brief, then one new slot. The brief line says the
    new time ("Moved to Sat 10:00"), so both rows go in one transaction."""
    busy = await member_busy(db, row.member_email, now, row.fire_at)
    new_at = slot.next_slot(slot.MemberDay(day, busy), row, now)
    await _ledger(db, row, "briefed", {"at": _iso(now)})
    await db.execute(text(
        "UPDATE reminders SET state = 'scheduled', fire_at = :fire_at, "
        "next_check_at = NULL, cycles = cycles + 1, updated_at = now() "
        "WHERE id = CAST(:id AS uuid)"), {"fire_at": new_at, "id": str(row.id)})
    await _ledger(db, row, "rescheduled", {
        "from": _iso(row.fire_at), "to": _iso(new_at), "from_state": row.state,
        "cycles": int(row.cycles) + 1, "slot": "smart",
    })
    return "rescheduled"


async def _ladder(db: Any, row: Any, day: Any, now: datetime) -> str:
    """Steps 2 to 5 of a ``due`` row whose check time came."""
    first = row.first_fired_at or now
    if now - first >= GIVE_UP_AFTER:
        return await _give_up(db, row, day, now, "72h")
    done = int((await db.execute(text(_CHECKS_SQL), {"id": str(row.id)})).scalar_one())
    if done < (IMPORTANT_CHECKS if row.important else CHECKS):
        return await _check_back(db, row, day, now, done)
    if int(row.cycles) >= MAX_CYCLES:
        return await _give_up(db, row, day, now, "cycles")
    return await _brief_and_move(db, row, day, now)


async def step(org: str, reminder_id: str, now: datetime) -> str | None:
    """Claim one row of *org* and take its next step, in one transaction.

    Answers the event written, ``"quiet"`` for a step that waits for the
    member's day, or None when the row is held by another worker or is no
    longer due."""
    async with tenant_session(org) as db:
        row = (await db.execute(
            text(_CLAIM_SQL), {"id": reminder_id, "now": now})).fetchone()
        if row is None:
            return None
        day = await member_day(db, row.member_email)
        if row.state == "scheduled":
            return await _fire(db, row, day, now)
        if slot.quiet(day, now):
            return "quiet"
        return await _ladder(db, row, day, now)


# ── One org, and the tick ───────────────────────────────────────────────────


async def run_org(org: str, now: datetime | None = None) -> Counter[str]:
    """Every due row of *org*, once: the steps only, with no network call.
    The send phase of :func:`run_tick` sends their pushes. Answers the count
    of each outcome."""
    at = now or _now()
    counts: Counter[str] = Counter()
    active: dict[str, bool] = {}
    skip: set[str] = set()
    waiting: set[str] = set()
    seen: set[str] = set()
    touched: set[str] = set()
    for _ in range(ROUNDS):
        if _stopping.is_set():
            break
        async with tenant_session(org) as db:
            rows = (await db.execute(text(_CANDIDATES_SQL), {
                "now": at, "waiting": sorted(waiting), "skip": sorted(skip),
                "seen": sorted(seen), "batch": BATCH,
            })).fetchall()
        if not rows:
            break
        for row in rows:
            rid, member = str(row.id), str(row.member_email)
            seen.add(rid)
            if member in skip or (row.state == "due" and member in waiting):
                continue
            if member not in active:
                active[member] = await _is_active(org, member)
            if not active[member]:
                skip.add(member)
                counts["skipped"] += 1
                continue
            try:
                outcome = await step(org, rid, at)
            except Exception as exc:
                _log.warning("reminders.step_failed", error=type(exc).__name__)
                counts["failed"] += 1
                continue
            if outcome == "quiet":
                waiting.add(member)
            if outcome is not None:
                counts[outcome] += 1
            if outcome in EVENTS:
                touched.add(member)
    if touched:
        from gateway.routes.shell import cache

        for member in sorted(touched):
            await cache.bump_member_safe(org, member)
    return counts


class _Share:
    """The stop test of one org in the first round of the send phase: the
    phase is over, or the org's share of the time ended. The first row of
    the org always starts while the phase runs. ``send_planned`` asks it
    only while a row is left, so ``stopped`` says that the org still holds
    rows."""

    def __init__(self, over: Callable[[], bool], until: float) -> None:
        self.over, self.until = over, until
        self.first, self.stopped = True, False

    def __call__(self) -> bool:
        first, self.first = self.first, False
        if self.over() or (not first and _monotonic() >= self.until):
            self.stopped = True
        return self.stopped


async def _send_org(org: str, clock: Callable[[], datetime],
                    stop: Callable[[], bool]) -> Counter[str]:
    """The planned pushes of *org*, bound to *org*. It never raises."""
    token = bind_tenant(org)
    try:
        return await push_channel.send_planned(org, clock, stop)
    except Exception as exc:  # a failed send never stops the loop
        _log.warning("reminders.push_failed", error=type(exc).__name__)
        return Counter()
    finally:
        release_tenant(token)


async def _send_phase(orgs: list[str], now: datetime | None,
                      out: dict[str, Counter[str]]) -> None:
    """The send step of RE-9b, once in a tick, after the steps of EVERY org
    (ruling k). Each org's planned pushes go through
    ``push_channel.send_planned``, bound to that org. One deadline bounds
    the whole phase: no row starts :data:`SEND_PHASE_S` after the phase
    began. A row that did not start stays planned for the next tick.

    **No org starves.** The phase starts at the next org on each tick
    (:data:`_send_turn`). In the first round, each org may start rows only
    in its share of the time left: that time over the count of orgs still to
    go. Its first row always starts. The second round gives the time still
    left to the orgs that still hold rows, in the same order.

    The clock of a row is the time of its start, so the quiet hours of a row
    read its send time, not the start of the tick. With a frozen *now* the
    clock starts there and moves with the stopwatch. It adds each org's
    counts to *out*, and it never raises."""
    global _send_turn
    if _stopping.is_set() or not orgs:
        return
    start = _monotonic()
    deadline = start + SEND_PHASE_S
    base = now or _now()

    def clock() -> datetime:
        return base + timedelta(seconds=_monotonic() - start)

    def over() -> bool:
        return _stopping.is_set() or _monotonic() >= deadline

    turn = _send_turn % len(orgs)
    _send_turn += 1
    order = orgs[turn:] + orgs[:turn]
    held: list[str] = []
    for n, org in enumerate(order):
        if over():
            break
        at = _monotonic()
        share = _Share(over, at + (deadline - at) / (len(order) - n))
        sent = await _send_org(org, clock, share)
        if sent:
            out.setdefault(org, Counter()).update(sent)
        if share.stopped:
            held.append(org)
    for org in held:
        if over():
            break
        sent = await _send_org(org, clock, over)
        if sent:
            out.setdefault(org, Counter()).update(sent)
    if over():
        _log.info("reminders.push_phase_over")


async def run_tick(now: datetime | None = None) -> dict[str, Counter[str]]:
    """One pass over every org: the steps of each org first, then one send
    phase for all of them (ruling k). Never raises."""
    if not loop_enabled():
        return {}
    out: dict[str, Counter[str]] = {}
    orgs = await _orgs()
    for org in orgs:
        if _stopping.is_set():
            break
        token = bind_tenant(org)
        try:
            counts = await run_org(org, now)
            if counts:
                out[org] = counts
        except Exception as exc:
            _log.warning("reminders.org_failed", error=type(exc).__name__)
        finally:
            release_tenant(token)
    await _send_phase(orgs, now, out)
    return out


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("reminders.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_reminder_loop() -> bool:
    """Start the loop when ``calendar_reminders`` is on. Idempotent."""
    global _task
    if not loop_enabled():
        _log.info("reminders.loop_disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="reminders")
    _log.info("reminders.loop_started", tick_secs=TICK_SECONDS)
    return True


async def stop_reminder_loop() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never
    started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still
