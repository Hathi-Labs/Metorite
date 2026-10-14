"""The ``my_day`` loop (WS-58 MD-5b, ``my_day_feed.md`` §8.3 and §8.4).

Every 60 seconds the loop looks at each organization. For each one it does
two things, and nothing more (§8.4 item 11):

* **It trims the active set and reads it.** Inside
  ``organization_scope(org)`` it drops each member of ``key("my_day",
  "active")`` whose last feed read is older than :data:`ACTIVE_DAYS` days,
  then reads the rest (item 13). MD-5b builds NO member pass. MD-6a builds
  it, with its first judge, and §8.4 items 12 and 24 bind it.
* **It runs the sweep, once a day.** The guard is ``SET key("my_day",
  "swept", <date>) NX EX 90000`` (item 23). The process that wins the guard
  runs ``state.sweep_old_rows`` inside ``tenant_session(org)``. That
  statement deletes only old ``seen``, ``later`` and ended time-snooze rows,
  and keeps ``dismissed``, ``acted`` and the snoozes until a change.

**Members come from the active set only.** :func:`note_active` is the one
writer of the set. ``GET /shell/feed`` calls it with the session's own
``UserContext``, so each email in the set came from a signed-in session and
never from request input (§8.3, item 22). It writes the lower-case email,
scored by the time of the read.

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
``tests/unit/test_db_engine_seam.py``, as the CRM mirror loop's is.

Fences: ``tests/unit/test_my_day_loop.py`` (hermetic) and
``tests/unit/test_my_day_loop_r8.py`` (R8, the sweep on a real Postgres).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger, tenant_redis
from acb_common.db import get_db, tenant_session
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
    """
    if not loop_enabled():
        return
    email = (getattr(user, "email", None) or "").strip().lower()
    org = getattr(user, "organization_id", None)
    if not email or not org:
        return
    try:
        with organization_scope(str(org)):
            await asyncio.wait_for(
                tenant_redis.get_tenant_redis().zadd(_active_key(), {email: time.time()}),
                ACTIVE_WRITE_TIMEOUT_S)
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
    """The daily sweep of *org* (§8.4 item 23). Answers the count of deleted
    rows, or ``None`` when another tick or process already swept today."""
    day = today or datetime.now(UTC).date().isoformat()
    with organization_scope(org):
        claimed = await tenant_redis.get_tenant_redis().set(
            key("my_day", "swept", day), "1", nx=True, ex=SWEEP_GUARD_S)
    if not claimed:
        return None
    from gateway.routes.shell import state

    async with tenant_session(org) as db:
        deleted = await state.sweep_old_rows(db)
    _log.info("my_day.swept deleted=%s", deleted)
    return deleted


async def run_org(org: str) -> list[str]:
    """One organization, once: trim and read the active set, then sweep.

    Answers the active members. MD-5b runs no member pass over them, and
    MD-6a adds it here (§8.4 item 11).
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


# ── The loop ────────────────────────────────────────────────────────────────


async def _run_one(org: str, gate: asyncio.Semaphore) -> int:
    async with gate:
        if _stopping.is_set():
            return 0
        try:
            await asyncio.wait_for(run_org(org), PER_ORG_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning("my_day.org_failed class=%s", type(exc).__name__)
            return 0
        return 1


async def run_tick() -> int:
    """Each organization, up to :data:`CONCURRENCY` at a time. Answers how
    many ran."""
    gate = asyncio.Semaphore(CONCURRENCY)
    done = await asyncio.gather(*(_run_one(org, gate) for org in await _organization_ids()))
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
    "SWEEP_GUARD_S",
    "TICK_SECONDS",
    "active_members",
    "loop_enabled",
    "loop_running",
    "note_active",
    "run_org",
    "run_tick",
    "start_my_day_loop",
    "stop_my_day_loop",
    "sweep",
]
