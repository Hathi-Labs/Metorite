"""Metorite task blocks out to the calendar "Metorite" (CE-13, D215).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1a points 7 to 9.

**One seam, by reconcile.** A block is written by the UI, the chat tools, the
day planner and the roll-over, and the task is closed or deleted by even more
paths. This module hooks none of them. Each pass compares the DESIRED set (the
timed blocks of the member on open tasks) with the LINKS (what the provider
holds, ``calendar_task_links``) and closes the gap:

* a block with no link makes one event;
* a block whose :func:`task_links.block_hash` differs updates its event;
* a link whose task is done, cancelled, archived, deleted, or has no block any
  more deletes its event.

So every write path is covered, today's and tomorrow's, with no call in any of
them. The cost is a delay of one pass (the 60 s loop).

**Dark.** :func:`reconcile_task_blocks` returns at once, and sends no request,
unless ``flags.write_allowed(org, member)`` is true (``CALENDAR_SYNC_WRITE``).

**Failures.** A provider error marks the link ``failed`` with the short error
CODE (never a token, at most 500 characters) and sets ``next_attempt_at`` with
a backoff. A lease on ``next_attempt_at`` keeps two workers from sending the
same link. ``NeedsReconnect`` stops the pass: the connection row already says
so.

**Tenancy.** Every session is ``tenant_session(org)``. The connection is read
inside it, so org B can never name a connection of org A. The member of the
connection decides which blocks go out, never an argument.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import tenant_session
from sqlalchemy import text

from gateway.calendar_sync.task_links import block_hash

_log = get_logger("calendar_sync.task_out")

#: A block that ended earlier than this is history. It is not pushed (the read
#: window of the sync is 30 days back).
HISTORY_DAYS = 30
#: The most blocks one pass sends for one connection.
MAX_PER_PASS = 100
#: The lease on a link while a request is in flight.
LEASE = timedelta(minutes=2)
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP = timedelta(hours=1)
ERROR_MAX = 500


@dataclass
class PassStats:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: str | None = None  # why the pass did nothing at all


def backoff(attempts: int) -> timedelta:
    """30 s, 1 m, 2 m ... capped at 1 h."""
    return min(timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0))), BACKOFF_CAP)


def _code(exc: BaseException) -> str:
    """A short word. Never a message: a message could carry a token."""
    code = getattr(exc, "code", None) or type(exc).__name__
    return str(code)[:ERROR_MAX]


_DESIRED = """
SELECT p.task_id::text AS task_id, t.title, p.scheduled_start, p.scheduled_end
  FROM pm_task_personal p
  JOIN pm_tasks t ON t.id = p.task_id
  JOIN pm_task_statuses s ON s.id = t.status_id
 WHERE lower(p.member_email) = :me
   AND p.scheduled_start IS NOT NULL AND p.scheduled_end IS NOT NULL
   AND s.category NOT IN ('done', 'cancelled', 'triage')
   AND t.archived_at IS NULL
"""

_LINKS = """
SELECT task_id::text AS task_id, external_id, synced_block_hash, attempts,
       sync_state, next_attempt_at
  FROM calendar_task_links
 WHERE organization_id = CAST(:org AS uuid)
   AND connection_id = CAST(:cid AS uuid) AND member_email = :me
"""


async def plan(db: Any, org: str, connection_id: str, me: str, now: datetime) -> dict[str, list]:
    """Read the gap. Pure reads. Returns ``push`` and ``remove`` lists."""
    desired = (await db.execute(text(_DESIRED), {"me": me})).fetchall()
    links = {r.task_id: r for r in (await db.execute(
        text(_LINKS), {"org": org, "cid": connection_id, "me": me})).fetchall()}
    horizon = now - timedelta(days=HISTORY_DAYS)
    alive = {d.task_id for d in desired}
    push: list[tuple[Any, Any]] = []
    for d in desired:
        if d.scheduled_end < horizon:
            continue
        link = links.get(d.task_id)
        h = block_hash(d.title, d.scheduled_start, d.scheduled_end)
        if link is not None:
            if link.next_attempt_at is not None and link.next_attempt_at > now:
                continue  # leased, or waiting out a backoff
            if link.sync_state == "sent" and link.synced_block_hash == h:
                continue
        push.append((d, link))
    remove = [
        link for tid, link in links.items()
        if tid not in alive and (link.next_attempt_at is None or link.next_attempt_at <= now)
    ]
    return {"push": push[:MAX_PER_PASS], "remove": remove[:MAX_PER_PASS]}


async def _lease(db: Any, org: str, connection_id: str, me: str, task_id: str,
                 now: datetime, *, create: bool) -> bool:
    """Take the link for one send. False when another worker holds it."""
    params = {"org": org, "cid": connection_id, "me": me, "tid": task_id,
              "until": now + LEASE, "now": now}
    if create:
        r = await db.execute(text(
            "INSERT INTO calendar_task_links (organization_id, task_id, member_email, "
            "connection_id, sync_state, next_attempt_at) "
            "VALUES (CAST(:org AS uuid), CAST(:tid AS uuid), :me, CAST(:cid AS uuid), "
            "'pending', :until) ON CONFLICT DO NOTHING RETURNING task_id"), params)
        if r.fetchone() is not None:
            return True
    r = await db.execute(text(
        "UPDATE calendar_task_links SET next_attempt_at = :until "
        "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
        "AND task_id = CAST(:tid AS uuid) AND member_email = :me "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) RETURNING task_id"), params)
    return r.fetchone() is not None


async def _mark_failed(org: str, connection_id: str, me: str, task_id: str,
                       exc: BaseException, attempts: int, now: datetime) -> None:
    async with tenant_session(org) as db:
        await db.execute(text(
            "UPDATE calendar_task_links SET sync_state = 'failed', sync_error = :err, "
            "attempts = :n, next_attempt_at = :next, updated_at = now() "
            "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
            "AND task_id = CAST(:tid AS uuid) AND member_email = :me"),
            {"err": _code(exc), "n": attempts + 1, "next": now + backoff(attempts + 1),
             "org": org, "cid": connection_id, "tid": task_id, "me": me})


def out_event(row: Any, task_url: str | None) -> Any:
    """The provider-neutral event of a block. No attendees: no mail goes out."""
    from gateway.calendar_sync.provider import OutEvent

    return OutEvent(
        title=(row.title or "(No title)")[:200], notes=None, location=None,
        starts_at=row.scheduled_start, ends_at=row.scheduled_end,
        start_date=None, end_date=None, tz=None, busy=True, task_url=task_url,
    )


async def reconcile_task_blocks(
    org: str,
    connection_id: str,
    *,
    provider: Any | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PassStats:
    """One pass for one connection. See the module text."""
    from gateway.calendar_sync import connections, flags
    from gateway.calendar_sync.provider import NeedsReconnect
    from gateway.crm_sources.base import SourceError

    stats = PassStats()
    t0 = now()

    async with tenant_session(org) as db:
        conn = await connections.get_connection(db, org, connection_id)
        if conn is None or getattr(conn, "status", "connected") != "connected":
            stats.skipped = "no_connection"
            return stats
        me = str(conn.member_email).lower()
        if not flags.write_allowed(org, me):
            stats.skipped = "flag_off"  # no request, no cost
            return stats
        work = await plan(db, org, str(conn.id), me, t0)
    if not work["push"] and not work["remove"]:
        stats.skipped = "nothing_to_do"
        return stats

    if provider is None:
        from gateway.calendar_sync.scheduler import PROVIDERS

        provider = PROVIDERS[_provider_key(conn.provider)]()

    cred = await connections.fresh_credential(org, conn, provider)
    if cred is None:
        stats.skipped = "no_credential"
        return stats
    async with tenant_session(org) as db:
        cal_id = await connections.ensure_metorite_calendar(db, org, conn, provider, cred)
    task_url_for = _task_url_builder()

    for d, link in work["push"]:
        creating = link is None
        async with tenant_session(org) as db:
            if not await _lease(db, org, str(conn.id), me, d.task_id, t0, create=creating):
                continue
        attempts = 0 if link is None else int(link.attempts or 0)
        try:
            res = await provider.upsert_event(
                cred, cal_id, out_event(d, task_url_for(d.task_id)),
                None if link is None else link.external_id)
        except NeedsReconnect:
            stats.skipped = "needs_reconnect"
            await _release(org, str(conn.id), me, d.task_id)
            return stats
        except SourceError as exc:
            stats.failed += 1
            await _mark_failed(org, str(conn.id), me, d.task_id, exc, attempts, t0)
            continue
        async with tenant_session(org) as db:
            await db.execute(text(
                "UPDATE calendar_task_links SET external_id = :eid, etag = :etag, "
                "provider_updated = :upd, synced_block_hash = :h, sync_state = 'sent', "
                "sync_error = NULL, attempts = 0, next_attempt_at = NULL, updated_at = now() "
                "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
                "AND task_id = CAST(:tid AS uuid) AND member_email = :me"),
                {"eid": res.external_id, "etag": res.etag, "upd": now(),
                 "h": block_hash(d.title, d.scheduled_start, d.scheduled_end),
                 "org": org, "cid": str(conn.id), "tid": d.task_id, "me": me})
        stats.created += 1 if creating or not link.external_id else 0
        stats.updated += 0 if creating or not link.external_id else 1

    for link in work["remove"]:
        async with tenant_session(org) as db:
            if not await _lease(db, org, str(conn.id), me, link.task_id, t0, create=False):
                continue
        try:
            if link.external_id:
                await provider.delete_event(cred, cal_id, link.external_id)  # 404 is success
        except NeedsReconnect:
            stats.skipped = "needs_reconnect"
            await _release(org, str(conn.id), me, link.task_id)
            return stats
        except SourceError as exc:
            stats.failed += 1
            await _mark_failed(org, str(conn.id), me, link.task_id, exc, int(link.attempts or 0), t0)
            continue
        async with tenant_session(org) as db:
            await db.execute(text(
                "DELETE FROM calendar_task_links WHERE organization_id = CAST(:org AS uuid) "
                "AND connection_id = CAST(:cid AS uuid) AND task_id = CAST(:tid AS uuid) "
                "AND member_email = :me"),
                {"org": org, "cid": str(conn.id), "tid": link.task_id, "me": me})
        stats.deleted += 1
    return stats


async def _release(org: str, connection_id: str, me: str, task_id: str) -> None:
    async with tenant_session(org) as db:
        await db.execute(text(
            "UPDATE calendar_task_links SET next_attempt_at = NULL "
            "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
            "AND task_id = CAST(:tid AS uuid) AND member_email = :me"),
            {"org": org, "cid": connection_id, "tid": task_id, "me": me})


def _provider_key(provider: str) -> str:
    return provider


def _task_url_builder() -> Callable[[str], str | None]:
    try:
        from gateway.routes.email.transport.oauth import _workbench_public_url

        base = _workbench_public_url().rstrip("/")
    except Exception:  # noqa: BLE001 - a missing base must not stop the sync
        return lambda _tid: None
    return lambda tid: f"{base}/projects?task={tid}"


# ── The return path: read the calendar "Metorite" ──────────────────────────

META_TASK_CURSOR = "task_cursor"


async def pull_remote_changes(
    org: str,
    connection_id: str,
    *,
    provider: Any,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Read the changes of the calendar "Metorite" and apply them to the blocks.

    Returns how many changes it routed. It sends no write to the provider, but
    it IS a request, so the flag gates it too. The cursor of this calendar
    lives in ``provider_meta["task_cursor"]``, apart from the cursor of the
    main read.

    A full list (no cursor, or a dead cursor) names every live event of the
    window. A link whose event is absent from it, and whose block starts inside
    the window, was deleted at the provider: the block is unscheduled.
    """
    from gateway.calendar_sync import connections, flags
    from gateway.calendar_sync.provider import CursorExpired, NeedsReconnect, Window
    from gateway.connections import store
    from gateway.crm_sources.base import SourceError

    from gateway.calendar_sync import task_links

    async with tenant_session(org) as db:
        conn = await connections.get_connection(db, org, connection_id)
    if conn is None or conn.status != "connected":
        return 0
    me = str(conn.member_email).lower()
    cal_id = connections.metorite_calendar_id(conn)
    if not cal_id or not flags.write_allowed(org, me):
        return 0
    raw = conn.provider_meta.get(META_TASK_CURSOR)
    cursor = raw if isinstance(raw, str) and raw else None
    t = now()
    window = Window.around(t)
    cred = await connections.fresh_credential(org, conn, provider)
    if cred is None:
        return 0
    try:
        try:
            result = await provider.sync(cred, cursor, window, calendar_id=cal_id)
        except CursorExpired:
            cursor = None
            result = await provider.sync(cred, None, window, calendar_id=cal_id)
    except NeedsReconnect:
        return 0
    except SourceError as exc:
        _log.warning("calendar_sync.task_pull_failed", organization_id=org, code=_code(exc))
        return 0

    routed = 0
    async with tenant_session(org) as db:
        locked = await store.lock_by_id(db, org, connection_id)
        if locked is None or locked.status != "connected":
            return 0
        for ev in result.changes:
            outcome = await task_links.route_remote_change(db, org, locked, ev)
            routed += outcome != task_links.IGNORED
        if cursor is None and result.full:
            routed += await task_links.unschedule_missing(
                db, org, locked, {e.external_id for e in result.changes if not e.deleted},
                window.start, window.end)
        await store.merge_provider_meta(
            db, org, connection_id, {META_TASK_CURSOR: result.next_cursor or ""})
    return routed


async def run_pass(org: str, connection_id: str, *, provider: Any) -> PassStats:
    """What the loop calls after the main read: pull first, then push."""
    await pull_remote_changes(org, connection_id, provider=provider)
    return await reconcile_task_blocks(org, connection_id, provider=provider)
