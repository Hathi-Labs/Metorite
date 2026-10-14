"""The ``my_day`` loop (WS-58 MD-5b and MD-6a-2, ``my_day_feed.md`` §8.3,
§8.4 and §10.9).

Every 60 seconds the loop looks at each organization. For each one it does
three things:

* **It trims the active set and reads it.** Inside
  ``organization_scope(org)`` it drops each member of ``key("my_day",
  "active")`` whose last feed read is older than :data:`ACTIVE_DAYS` days,
  then reads the rest (item 13).
* **It runs the sweep, once a day.** The guard is ``SET key("my_day",
  "swept", <date>) NX EX 90000`` (item 23). The process that wins the guard
  runs ``state.sweep_old_rows`` inside ``tenant_session(org)``. That
  statement deletes only old ``seen``, ``later`` and ended time-snooze rows,
  and keeps ``dismissed``, ``acted`` and the snoozes until a change. Then it
  runs ``feed_decide.sweep_old_rows``, which deletes the org's
  ``my_day_judgments`` rows older than 30 days (§13.2).
* **It runs the member pass** (MD-6a-2, §10.9 items 7 to 9). It runs after
  :func:`run_org`, outside :data:`PER_ORG_TIMEOUT_S`, and outside the org's
  slot. :func:`run_members` holds the rules.

**The cheap gate comes first** (§10.9 item 9). With each ``my_day.*`` mode
``off``, or with the org outside ``MY_DAY_DECIDE_ORGS``, the member pass
stops before it reads Redis, before an identity read, before any SQL and
before ``member_context``. The test is ``feed_decide.active_features``, the
one that ``judge`` makes. It reads settings only.

**When a member is due** (§8.3, §10.9 item 7). Two reasons, and nothing else:

* **The morning pass.** Once for each member each local day, from 15
  minutes before ``day_start_hour`` to the day end, in the member's zone.
  The guard is ``SET key("my_day", "morning", <member>, <local date>) NX``.
* **A dirty member.** ``cache.versions(member)`` differs from the versions
  stored under ``key("my_day", "judged", <member>)``. The guard ``SET
  key("my_day", "dirty", <member>) NX EX 120`` gives one pass in 2 minutes
  at most. The bumps write only while ``MY_DAY_CACHE`` is on, so with it off
  only the morning pass runs.

The judged key also holds the member's zone and day hours, as the last pass
read them. So the loop finds that a member is not due from Redis alone, with
no SQL. Each pass that runs reads the day again.

**Each member, in order** (§8.4 items 12 and 24): the departed check, then
``member_context``, then ``feed_decide.judge``. A member is departed when
``resolve_identity`` gives no org and the read did not fail, or gives
another org, or ``app_user.status`` is not ``active``. A departed member
leaves the active set, and ``member_context`` never runs for them. A failed
identity read skips the member and keeps them in the set. ``member_context``
binds a tenant and never releases it, so each member runs in a task of its
own. Three members run at once at most, and each has 30 seconds.

**Members come from the active set only.** :func:`note_active` is the one
writer of the set. ``GET /shell/feed`` calls it with the session's own
``UserContext``, so each email in the set came from a signed-in session and
never from request input (§8.3, item 22). It writes the session's exact
email, scored by the time of the read (fix round 1: the member tables key on
that spelling). The loop's own guard keys use the lower case.

**A failure holds the member** (fix round 1). After a failed identity read,
or a pass that failed, timed out or judged nothing, ``key("my_day", "hold",
<member>)`` holds the member for :data:`HOLD_S`. An org in the ``decide``
cool-down skips the slow path.

**Dark by default** (item 10). ``MY_DAY_LOOP_ENABLED`` is a ``Settings``
field, default false, and :func:`loop_enabled` is its one reader. With it
off, :func:`start_my_day_loop` starts no task and :func:`note_active` writes
nothing. main.py calls the start and the stop unconditionally.

**Redis fails open.** A failed active-set write logs and the feed read goes
on (item 22). A failed trim or read skips the organization for one tick.

**Two bindings.** ``tenant_redis`` binds its own tenant, apart from
``acb_common.db`` (item 20). So the Redis work runs inside
``organization_scope(org)`` and the SQL inside ``tenant_session(org)``.

**The one unbound read.** :func:`_organization_ids` lists the RLS-EXEMPT
``organization`` table. It is a tenant-discovery site in
``tests/unit/test_db_engine_seam.py``, as the CRM mirror loop's is. The
identity read of the departed check is ``acb_auth.access.resolve_identity``,
the sign-in path's own read, and this module adds no other. The status read
runs inside ``tenant_session(org)``.

Fences: ``tests/unit/test_my_day_loop.py`` (hermetic) and
``tests/unit/test_my_day_loop_r8.py`` (R8: the sweeps and the departed
checks on a real Postgres).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger, tenant_redis
from acb_common.db import bind_tenant, get_db, release_tenant, tenant_session
from acb_common.settings import get_settings
from acb_common.tenant_redis import key, organization_scope
from sqlalchemy import text

_log = get_logger("my_day.loop")

#: How often the loop looks at the organizations (§8.3).
TICK_SECONDS = 60.0
#: A member with no feed read in this many days is not active (§8.3).
ACTIVE_DAYS = 3
ACTIVE_WINDOW_S = ACTIVE_DAYS * 86400
#: The life of the daily sweep guard, in seconds (§8.4 item 23).
SWEEP_GUARD_S = 90000
#: Organizations that run at the same time (``crm_sync/scheduler.py``).
CONCURRENCY = 3
#: The longest the feed read waits for its active-set write.
ACTIVE_WRITE_TIMEOUT_S = 0.25
#: Time to wait for a tick in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0
# One organization's trim, read and sweep. A Redis that accepts and never
# answers must not hold the three slots of the tick for ever.
PER_ORG_TIMEOUT_S = 20.0
#: Members whose pass runs at the same time, across every org (§8.4 item 13).
MEMBER_CONCURRENCY = 3
#: The bound of one member's pass (§10.9 item 8). Past it the pass is
#: cancelled, and the next member runs.
MEMBER_TIMEOUT_S = 30.0
#: The morning pass opens this long before the member's day start (§8.3).
MORNING_LEAD = timedelta(minutes=15)
#: The life of the morning guard, in seconds: about 26 hours.
MORNING_GUARD_S = 26 * 3600
#: The life of the judged versions, in seconds (§10.9 item 7): 26 hours.
JUDGED_TTL_S = 26 * 3600
#: A dirty member gets one pass in this many seconds at most (§8.3).
DIRTY_GAP_S = 120
#: After a failed identity read, a failed or timed-out pass, or a pass that
#: judged nothing, the member waits this many seconds (fix round 1, P2-2).
HOLD_S = 120

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def loop_enabled() -> bool:
    """True when ``MY_DAY_LOOP_ENABLED`` is on. Default OFF. The one reader."""
    return get_settings().my_day_loop_enabled is True


def _active_key() -> Any:
    return key("my_day", "active")


# ── The feed read's write ───────────────────────────────────────────────────


async def note_active(user: Any) -> None:
    """Add the signed-in member to the active set of their organization.

    ``GET /shell/feed`` calls this with its session ``UserContext``. The
    email and the organization come from that context and nothing else.
    With the switch off it does nothing. A failure logs, and never raises.

    ⚠️ **The set holds the session's EXACT address** (MD-6a-2 fix round 1).
    ``my_day_card_state``, ``user_settings`` and ``my_day_judgments`` key on
    the session's own spelling, and an IdP can send ``First.Last@...``. A
    lower-case copy would build a context that finds none of the member's
    rows. A lower-case copy that an older build wrote leaves the set here.
    """
    if not loop_enabled():
        return
    email = (getattr(user, "email", None) or "").strip()
    org = getattr(user, "organization_id", None)
    if not email or not org:
        return

    async def _write() -> None:
        redis = tenant_redis.get_tenant_redis()
        await redis.zadd(_active_key(), {email: time.time()})
        if email.lower() != email:
            await redis.zrem(_active_key(), email.lower())

    try:
        with organization_scope(str(org)):
            await asyncio.wait_for(_write(), ACTIVE_WRITE_TIMEOUT_S)
    except Exception as exc:
        _log.warning("my_day.active_write_failed class=%s", type(exc).__name__)


# The writes in flight, held so the event loop does not collect them early.
_writes: set[asyncio.Task[None]] = set()


def mark_active(user: Any) -> None:
    """Start :func:`note_active` beside the read, and do not wait for it.

    ``GET /shell/feed`` calls this, so a slow Redis never adds to the poll.
    With the switch off it starts nothing.
    """
    if not loop_enabled():
        return
    task = asyncio.get_running_loop().create_task(note_active(user))
    _writes.add(task)
    task.add_done_callback(_writes.discard)


# ── One organization ────────────────────────────────────────────────────────


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table."""
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


async def active_members(org: str, now: float | None = None) -> list[str]:
    """Trim the active set of *org* to the last :data:`ACTIVE_DAYS` days, and
    read it. The loop reads members from here only (§8.3)."""
    cutoff = (time.time() if now is None else now) - ACTIVE_WINDOW_S
    with organization_scope(org):
        redis = tenant_redis.get_tenant_redis()
        k = _active_key()
        await redis.zremrangebyscore(k, "-inf", cutoff)
        return [str(m) for m in await redis.zrangebyscore(k, cutoff, "+inf")]


async def sweep(org: str, today: str | None = None) -> int | None:
    """The daily sweep of *org* (§8.4 items 14 and 23, §13.2). Answers the
    count of deleted rows of both tables, or ``None`` when another tick or
    process already swept today.

    Two statements, each in its own ``tenant_session(org)``: the card
    states (``state.sweep_old_rows``), then the judgments
    (``feed_decide.sweep_old_rows``). Each module is the one writer of its
    table. A failed judgments sweep keeps the card-state sweep."""
    day = today or datetime.now(UTC).date().isoformat()
    with organization_scope(org):
        claimed = await tenant_redis.get_tenant_redis().set(
            key("my_day", "swept", day), "1", nx=True, ex=SWEEP_GUARD_S)
    if not claimed:
        return None
    from gateway.routes.shell import feed_decide, state

    async with tenant_session(org) as db:
        deleted = await state.sweep_old_rows(db)
    judged = 0
    try:
        async with tenant_session(org) as db:
            judged = await feed_decide.sweep_old_rows(db)
    except Exception as exc:
        _log.warning("my_day.judgments_sweep_failed class=%s", type(exc).__name__)
    _log.info("my_day.swept deleted=%s judgments=%s", deleted, judged)
    return deleted + judged


async def run_org(org: str) -> list[str]:
    """One organization, once: trim and read the active set, then sweep.

    Answers the active members. The member pass (:func:`run_members`) runs
    after this, outside :data:`PER_ORG_TIMEOUT_S` (§10.9 item 8).
    """
    try:
        members = await active_members(org)
    except Exception as exc:  # Redis down: skip this org for one tick
        _log.warning("my_day.active_read_failed class=%s", type(exc).__name__)
        members = []
    try:
        await sweep(org)
    except Exception as exc:
        _log.warning("my_day.sweep_failed class=%s", type(exc).__name__)
    return members


# ── The member pass (MD-6a-2) ───────────────────────────────────────────────


def judging_on(org: str) -> bool:
    """The cheap gate (§10.9 item 9): True when ``judge`` would ask a
    feature for *org*. It is ``feed_decide.active_features``, the test that
    ``judge`` makes, with *org* bound for ``mode_for`` and then released. It
    reads settings only: no Redis, no identity read and no SQL."""
    from gateway.routes.shell import feed_decide

    token = bind_tenant(org)
    try:
        return bool(feed_decide.active_features())
    finally:
        release_tenant(token)


class IdentityReadFailed(RuntimeError):
    """The identity read failed, so the loop cannot say if the member left."""


#: The member's status in the bound org (§8.4 item 24). ``resolve_identity``
#: reads ``app_user`` with no status filter while ``IDENTITY_CUTOVER`` is
#: off, and the shadow ``org_membership.status`` can be stale while it is
#: on. So the loop reads the status of record itself. Both sides are lower
#: case: the set holds the session's spelling, and ``app_user`` holds the
#: spelling it was stored with (migration 162 kept it).
_STATUS_SQL = (
    "SELECT status FROM app_user "
    "WHERE organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid) "
    "AND lower(email) = lower(:email) LIMIT 1"
)


async def _member_status(org: str, member: str) -> str | None:
    """``app_user.status`` of *member* in *org*, or None for no row."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_STATUS_SQL), {"email": member})).first()
    return None if row is None else str(row[0])


async def departed(org: str, member: str) -> str | None:
    """Why *member* left *org*, or None when they did not (§8.4 item 24).

    * ``"no_org"``: ``resolve_identity`` gives no org, and the read did not
      fail.
    * ``"other_org"``: it gives an org that is not *org*.
    * ``"status"``: ``app_user.status`` in *org* is not ``active``, or the
      row is gone.

    A failed identity read raises :class:`IdentityReadFailed`, and a pool
    timeout raises ``IdentityUnavailable``. The caller then keeps the
    member, because a failed read does not say that they left."""
    from acb_auth.access import identity_read_failed, resolve_identity

    _user_id, home = await resolve_identity(member)
    if not home:
        if identity_read_failed():
            raise IdentityReadFailed(member)
        return "no_org"
    if str(home) != org:
        return "other_org"
    if await _member_status(org, member) != "active":
        return "status"
    return None


async def admit(org: str, member: str) -> str | None:
    """The departed check, and its effect. None admits *member*. Else the
    answer is the reason the pass skips them. A departed member leaves the
    active set. A failed read keeps them in it."""
    from acb_auth.access import IdentityUnavailable

    try:
        reason = await departed(org, member)
    except (IdentityUnavailable, IdentityReadFailed):
        return "identity_failed"
    except Exception as exc:  # the status read failed: keep the member
        _log.warning("my_day.member_check_failed class=%s", type(exc).__name__)
        return "identity_failed"
    if reason is None:
        return None
    with organization_scope(org):
        await tenant_redis.get_tenant_redis().zrem(_active_key(), member)
    _log.info("my_day.member_departed reason=%s", reason)
    return "departed"


def _guard(member: str) -> str:
    """The member in the loop's own keys: lower case, as ``cache`` keys it.
    So one person has one guard, whatever the spelling in the set."""
    return member.strip().lower()


def _judged_key(member: str) -> Any:
    return key("my_day", "judged", _guard(member))


def _morning_key(member: str, local_date: str) -> Any:
    return key("my_day", "morning", _guard(member), local_date)


def _dirty_key(member: str) -> Any:
    return key("my_day", "dirty", _guard(member))


def _hold_key(member: str) -> Any:
    return key("my_day", "hold", _guard(member))


def org_cooling(org: str) -> bool:
    """Whether *org* is in the ``decide`` cool-down after a 402 or a 403.
    ``judge`` would stop there, so the pass skips the slow path. An
    in-memory read: no Redis and no SQL."""
    from gateway import decide_features as df

    token = bind_tenant(org)
    try:
        return df.cooling()
    finally:
        release_tenant(token)


def _day_hint(day: Any) -> dict[str, Any]:
    """The zone and the day hours of a ``state.Day``, for the judged key."""
    return {"tz": day.tz.key, "start": day.start_hour, "end": day.end_hour}


def _day_of(hint: Any) -> Any:
    """The ``state.Day`` of a stored hint, or None when there is none."""
    from gateway.routes.shell import state

    if not isinstance(hint, dict):
        return None
    try:
        return state.Day(state._zone(hint.get("tz")), int(hint["start"]), int(hint["end"]))
    except (KeyError, TypeError, ValueError):
        return None


def morning_date(day: Any, now: datetime) -> str | None:
    """The local date whose morning pass is due at *now*, or None (§8.3).

    The window opens :data:`MORNING_LEAD` before the day start and closes at
    the day end, in the member's zone. A loop that was down at the opening
    still runs the pass later that day. The guard keys on the date, so the
    pass runs once a day."""
    first = day.feed_day(now + MORNING_LEAD)
    opens = day.at(first, day.start_hour) - MORNING_LEAD
    end = day.end_hour if day.end_hour > day.start_hour else day.start_hour + 24
    return first.isoformat() if opens <= now < day.at(first, end) else None


async def _read_day(org: str, user: Any) -> Any:
    """The member's day, through the state module's read (``state._hours``).

    A member who holds ``feature:tasks`` reads it through
    ``get_user_settings``, which opens its own session. So this opens none
    for them, as ``state.apply_state`` does. A session held around that read
    would hold two pool connections for one member (fix round 1, P2-1)."""
    from gateway.routes.shell import state

    if user.has_permission("feature:tasks"):
        return await state._hours(user, None)
    async with tenant_session(org) as db:
        return await state._hours(user, db)


#: The ``judge`` outcomes that judged nothing. A morning pass that ends so
#: gives its guard back, and the member is held for :data:`HOLD_S`.
NOT_JUDGED = frozenset({"cooldown", "cap", "failed"})
#: The results that hold a member back for :data:`HOLD_S`, so a starved
#: database does not get the slow path again at each tick (P2-2).
HELD_AFTER = frozenset({"identity_failed", "timeout", "failed",
                        *(f"judged:{r}" for r in NOT_JUDGED)})


async def _look(org: str, member: str, when: datetime
                ) -> tuple[dict[str, Any] | None, list[str] | None, bool, Any, str | None]:
    """What Redis says of *member*: the judged value, the versions now, if
    the member is dirty, the stored day and the morning date that is due.
    Redis only."""
    from gateway.routes.shell import cache

    with organization_scope(org):
        redis = tenant_redis.get_tenant_redis()
        raw = await redis.get(_judged_key(member))
        seen = list(await cache.versions(member)) if cache.enabled() else None
    try:
        judged = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        judged = None
    judged = judged if isinstance(judged, dict) else None
    # §10.9 item 7: no stored versions, or other ones, is dirty. With
    # MY_DAY_CACHE off the versions never move, so nothing is dirty.
    dirty = seen is not None and (judged is None or judged.get("ver") != seen)
    day = _day_of((judged or {}).get("day"))
    morning = morning_date(day, when) if day is not None else None
    with organization_scope(org):
        if morning is not None and await redis.exists(_morning_key(member, morning)):
            morning = None  # the morning pass of that date already ran
        if dirty and await redis.exists(_dirty_key(member)):
            dirty = False  # a pass ran less than 2 minutes ago
    return judged, seen, dirty, day, morning


async def _claim(org: str, member: str, morning: str | None, dirty: bool
                 ) -> tuple[bool, bool]:
    """Take the guards of a pass. Answers whether the pass runs, and whether
    it took the morning guard. Each pass takes the 2-minute guard, the
    morning pass too, so a dirty member gets no second pass a tick later."""
    redis = tenant_redis.get_tenant_redis()
    with organization_scope(org):
        by_morning = False
        if morning is not None:
            by_morning = bool(await redis.set(_morning_key(member, morning), "1", nx=True,
                                              ex=MORNING_GUARD_S))
        by_dirty = False
        if by_morning or dirty:
            by_dirty = bool(await redis.set(_dirty_key(member), "1", nx=True,
                                            ex=DIRTY_GAP_S))
    return by_morning or by_dirty, by_morning


async def member_pass(org: str, member: str, now: datetime | None = None) -> str:
    """One member's pass (§8.3, §8.4 items 12 and 24, §10.9 item 7).

    *member* is the session's exact address, as the active set holds it.
    Answers what it did: ``not_due``, ``held``, ``cooldown``, ``departed``,
    ``identity_failed``, ``other_org``, or ``judged:<reason>``. Run it in a
    task of its own, because ``member_context`` binds a tenant and never
    releases it.
    """
    when = now or datetime.now(UTC)
    judged, seen, dirty, day, morning = await _look(org, member, when)
    if day is not None and not dirty and morning is None:
        return "not_due"  # Redis alone: no identity read, and no SQL
    redis = tenant_redis.get_tenant_redis()
    with organization_scope(org):
        if await redis.exists(_hold_key(member)):
            return "held"  # a read failed less than 2 minutes ago
    if org_cooling(org):
        return "cooldown"

    # §8.4 item 24, BEFORE member_context: a departed address never reaches
    # the request path's resolve, so it files no sign-in request.
    refused = await admit(org, member)
    if refused is not None:
        return refused
    from acb_auth.deps import member_context

    user = await member_context(member)
    if str(getattr(user, "organization_id", None) or "") != org:
        # The check of routes/whatsapp_channel/views.py: a context of another
        # org is refused, never used.
        _log.warning("my_day.member_other_org")
        return "other_org"
    fresh = _day_hint(await _read_day(org, user))
    if day is None:
        morning = morning_date(_day_of(fresh), when)
        if morning is not None:
            with organization_scope(org):
                if await redis.exists(_morning_key(member, morning)):
                    morning = None

    ran, by_morning = await _claim(org, member, morning, dirty)
    if not ran:
        # Keep the day for the next tick, and the versions as they were.
        with organization_scope(org):
            await redis.set(_judged_key(member),
                            json.dumps({"ver": (judged or {}).get("ver"), "day": fresh}),
                            ex=JUDGED_TTL_S)
        return "not_due"

    from gateway.routes.shell import feed_decide

    result = await feed_decide.judge(user, when)
    with organization_scope(org):
        if result.reason in NOT_JUDGED:
            # Nothing was judged. Give the morning back, keep the old
            # versions, and let the hold space out the next try.
            if by_morning and morning is not None:
                await redis.delete(_morning_key(member, morning))
            ver = (judged or {}).get("ver")
        else:
            # The versions read BEFORE the pass (§8.4 item 17): a write
            # during the pass bumps past them, so the member is dirty again.
            ver = seen
        await redis.set(_judged_key(member), json.dumps({"ver": ver, "day": fresh}),
                        ex=JUDGED_TTL_S)
    _log.info("my_day.member_judged reason=%s rows=%s", result.reason, result.rows)
    return f"judged:{result.reason}"


async def _hold(org: str, member: str) -> None:
    """Hold *member* back for :data:`HOLD_S` after a failure. A Redis
    failure here only logs."""
    try:
        with organization_scope(org):
            await tenant_redis.get_tenant_redis().set(
                _hold_key(member), "1", nx=True, ex=HOLD_S)
    except Exception as exc:
        _log.warning("my_day.member_hold_failed class=%s", type(exc).__name__)


async def _one_member(org: str, member: str, gate: asyncio.Semaphore,
                      now: datetime | None) -> str | None:
    async with gate:
        if _stopping.is_set():
            return None
        # A task of its own, so the tenant that member_context binds for ever
        # stays in that task. In Python 3.12, wait_for on a bare coroutine
        # awaits it in OUR task, and the binding would reach the next member
        # that this caller runs.
        task = asyncio.get_running_loop().create_task(
            member_pass(org, member, now), name="my-day-member")
        try:
            did = await asyncio.wait_for(task, MEMBER_TIMEOUT_S)
        except TimeoutError:  # §10.9 item 8: cancelled, and the next one runs
            _log.warning("my_day.member_timed_out")
            did = "timeout"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one member must not stop the others
            _log.warning("my_day.member_failed class=%s", type(exc).__name__)
            did = "failed"
        if did in HELD_AFTER:
            await _hold(org, member)
        return did


async def run_members(org: str, members: list[str], gate: asyncio.Semaphore | None = None,
                      now: datetime | None = None) -> list[str | None]:
    """The member pass of *org* (§10.9 items 8 and 9). Answers what each
    member's pass did, in the order of *members*.

    With every ``my_day.*`` mode off for *org*, it does nothing at all: no
    Redis, no identity read, no SQL and no ``member_context``. *gate* holds
    :data:`MEMBER_CONCURRENCY` slots, shared by every org of the tick."""
    if not members or not judging_on(org):
        return []
    gate = gate or asyncio.Semaphore(MEMBER_CONCURRENCY)
    return list(await asyncio.gather(*(_one_member(org, m, gate, now) for m in members)))


# ── The loop ────────────────────────────────────────────────────────────────


async def _run_one(org: str, gate: asyncio.Semaphore,
                   members_gate: asyncio.Semaphore | None = None) -> int:
    async with gate:
        if _stopping.is_set():
            return 0
        try:
            members = await asyncio.wait_for(run_org(org), PER_ORG_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning("my_day.org_failed class=%s", type(exc).__name__)
            return 0
    # §10.9 item 8: after run_org, outside PER_ORG_TIMEOUT_S and outside the
    # org's slot. Each member has its own bound.
    try:
        await run_members(org, list(members or []), members_gate)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log.warning("my_day.members_failed class=%s", type(exc).__name__)
    return 1


async def run_tick() -> int:
    """Each organization, up to :data:`CONCURRENCY` at a time, and then its
    member pass, up to :data:`MEMBER_CONCURRENCY` members at a time across
    the tick. Answers how many orgs ran."""
    gate = asyncio.Semaphore(CONCURRENCY)
    members_gate = asyncio.Semaphore(MEMBER_CONCURRENCY)
    done = await asyncio.gather(
        *(_run_one(org, gate, members_gate) for org in await _organization_ids()))
    return sum(done)


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("my_day.loop_error class=%s", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_my_day_loop() -> bool:
    """Start the loop when ``MY_DAY_LOOP_ENABLED`` is on. Idempotent.

    With the switch off it starts no task, logs once, and returns False.
    """
    global _task
    if not loop_enabled():
        _log.info("my_day.loop.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="my-day-loop")
    _log.info("my_day.loop.started tick_secs=%s", TICK_SECONDS)
    return True


async def stop_my_day_loop() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("my_day.loop.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "ACTIVE_DAYS",
    "CONCURRENCY",
    "DIRTY_GAP_S",
    "MEMBER_CONCURRENCY",
    "MEMBER_TIMEOUT_S",
    "MORNING_LEAD",
    "SWEEP_GUARD_S",
    "TICK_SECONDS",
    "IdentityReadFailed",
    "active_members",
    "admit",
    "departed",
    "judging_on",
    "loop_enabled",
    "loop_running",
    "member_pass",
    "morning_date",
    "note_active",
    "run_members",
    "run_org",
    "run_tick",
    "start_my_day_loop",
    "stop_my_day_loop",
    "sweep",
]
