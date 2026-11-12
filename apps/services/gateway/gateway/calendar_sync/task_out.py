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

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import tenant_session
from sqlalchemy import text

from gateway.calendar_sync.provider import ProviderError
from gateway.calendar_sync.task_links import block_hash
from gateway.routes.projects.personal import MY_TASKS_FROM, my_tasks_binds

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


# The member's own timed blocks, through the ONE definition of "mine"
# (``personal.MY_TASKS_FROM``): the overlay narrows a grant and never widens
# it, so a block on a task the member lost access to stops going out. The
# fragment drops archived tasks and archived projects.
_DESIRED = (
    "SELECT t.id::text AS task_id, t.title, p.scheduled_start, p.scheduled_end "
    + MY_TASKS_FROM
    + "  AND p.scheduled_start IS NOT NULL AND p.scheduled_end IS NOT NULL\n"
    + "  AND s.category NOT IN ('done', 'cancelled', 'triage')\n"
)

_LINKS = """
SELECT task_id::text AS task_id, external_id, synced_block_hash, attempts,
       sync_state, next_attempt_at
  FROM calendar_task_links
 WHERE organization_id = CAST(:org AS uuid)
   AND connection_id = CAST(:cid AS uuid) AND member_email = :me
"""


async def plan(db: Any, org: str, connection_id: str, me: str, now: datetime) -> dict[str, list]:
    """Read the gap. Pure reads. Returns ``push`` and ``remove`` lists."""
    binds = await my_tasks_binds(db, me, archived=False)
    desired = (await db.execute(text(_DESIRED), binds)).fetchall()
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
                 clock: Callable[[], datetime], *, create: bool) -> bool:
    """Take the link for one send. False when another worker holds it.

    The time comes from a FRESH ``clock()`` call, not from the start of the
    pass: a pass that runs long must not see a lease that has already ended.
    A create takes a row only when no event exists yet (``external_id IS NULL``
    and the state is not ``sent``), so two workers cannot both create.
    """
    now = clock()
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
        + ("AND external_id IS NULL AND sync_state <> 'sent' " if create else "")
        + "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) RETURNING task_id"), params)
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


def client_event_id(task_id: str, connection_id: str) -> str:
    """A stable id for the INSERT of one block on one connection.

    Base32hex, lower case, 32 characters: legal for a Google event ``id`` and
    for a Graph ``transactionId``. A retry after a timeout then names the same
    event, and the provider answers "exists" instead of making a second one.
    """
    digest = hashlib.sha256(f"metorite-task{task_id}{connection_id}".encode()).digest()[:20]
    return base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")


def out_event(row: Any, task_url: str | None, client_id: str | None = None) -> Any:
    """The provider-neutral event of a block. No attendees: no mail goes out."""
    from gateway.calendar_sync.provider import OutEvent

    return OutEvent(
        title=(row.title or "(No title)")[:200], notes=None, location=None,
        starts_at=row.scheduled_start, ends_at=row.scheduled_end,
        start_date=None, end_date=None, tz=None, busy=True, task_url=task_url,
        client_id=client_id,
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
    if not work["push"] and not work["remove"] and connections.metorite_calendar_id(conn):
        stats.skipped = "nothing_to_do"
        return stats
    # No cached calendar id (a reconnect drops it, a deleted calendar forgets
    # it): go on, so the calendar is found or made and the links are bound to it.
    if not connections.metorite_calendar_id(conn) and _calendar_retry_active(conn.provider_meta, t0):
        stats.skipped = "calendar_backoff"  # the last try failed: no request yet
        return stats

    if provider is None:
        from gateway.calendar_sync.scheduler import PROVIDERS

        provider = PROVIDERS[_provider_key(conn.provider)]()

    cred = await connections.fresh_credential(org, conn, provider)
    if cred is None:
        stats.skipped = "no_credential"
        return stats
    try:
        async with tenant_session(org) as db:
            cal_id = await connections.ensure_metorite_calendar(db, org, conn, provider, cred)
    except NeedsReconnect:
        stats.skipped = "needs_reconnect"
        return stats
    except SourceError as exc:  # e.g. a grant with no calendar.app.created scope
        _log.warning("calendar_sync.task_calendar_failed", organization_id=org, code=_code(exc))
        await _note_calendar_failure(org, str(conn.id), t0)
        stats.skipped = "calendar_failed"
        return stats
    await _clear_calendar_failure(org, str(conn.id), conn.provider_meta)
    if await calendar_identity(org, str(conn.id), me, cal_id, conn.provider_meta) != SAME:
        # The calendar is new or was not bound: the links changed. Plan again.
        async with tenant_session(org) as db:
            work = await plan(db, org, str(conn.id), me, t0)
    if not work["push"] and not work["remove"]:
        stats.skipped = "nothing_to_do"
        return stats
    task_url_for = _task_url_builder()

    for d, link in work["push"]:
        creating = link is None or not link.external_id
        async with tenant_session(org) as db:
            if not await _lease(db, org, str(conn.id), me, d.task_id, now, create=creating):
                continue
        attempts = 0 if link is None else int(link.attempts or 0)
        cid = client_event_id(d.task_id, str(conn.id))
        ev_out = out_event(d, task_url_for(d.task_id), cid if creating else None)
        try:
            try:
                res = await provider.upsert_event(
                    cred, cal_id, ev_out, None if creating else link.external_id)
            except ProviderError as exc:
                if creating and getattr(exc, "code", "") == "exists":
                    # An earlier create reached the provider and its answer
                    # was lost. The event holds our client id: adopt it.
                    res = await provider.upsert_event(cred, cal_id, ev_out, cid)
                else:
                    raise
        except NeedsReconnect:
            stats.skipped = "needs_reconnect"
            await _release(org, str(conn.id), me, d.task_id)
            return stats
        except Exception as exc:  # noqa: BLE001 - any failure marks the link, never leaks
            if _code(exc) == "not_found":
                if await _calendar_missing(provider, cred, cal_id):
                    await _forget_calendar(org, str(conn.id))
                    await _release(org, str(conn.id), me, d.task_id)
                    stats.skipped = "calendar_gone"
                    return stats
                if not creating:
                    # The event is gone at the provider: make it again.
                    await _reset_event(org, str(conn.id), me, d.task_id)
                    continue
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
                {"eid": res.external_id, "etag": res.etag, "upd": None,
                 "h": block_hash(d.title, d.scheduled_start, d.scheduled_end),
                 "org": org, "cid": str(conn.id), "tid": d.task_id, "me": me})
        stats.created += 1 if creating else 0
        stats.updated += 0 if creating else 1

    for link in work["remove"]:
        async with tenant_session(org) as db:
            if not await _lease(db, org, str(conn.id), me, link.task_id, now, create=False):
                continue
        try:
            # A create whose answer was lost left no id, but the event may exist
            # under our client id. 404 is success, so this is always safe.
            await provider.delete_event(
                cred, cal_id, link.external_id or client_event_id(link.task_id, str(conn.id)))
        except NeedsReconnect:
            stats.skipped = "needs_reconnect"
            await _release(org, str(conn.id), me, link.task_id)
            return stats
        except Exception as exc:  # noqa: BLE001
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


async def _calendar_missing(provider: Any, cred: Any, cal_id: str) -> bool:
    """True only when the provider LISTS calendars and "Metorite" is not there.
    A failed or empty list proves nothing."""
    try:
        listed = await provider.list_calendars(cred)
    except Exception:  # noqa: BLE001
        return False
    return bool(listed) and all(getattr(c, "id", None) != cal_id for c in listed)


async def _forget_calendar(org: str, connection_id: str) -> None:
    """The member deleted the calendar "Metorite". Drop the cached id, so the
    next pass makes a new calendar. ``calendar_identity`` then binds or drops the links."""
    from gateway.calendar_sync.connections import META_CALENDAR_ID
    from gateway.connections import store

    async with tenant_session(org) as db:
        await store.remove_meta_keys(db, org, connection_id, (META_CALENDAR_ID,))


async def _reset_event(org: str, connection_id: str, me: str, task_id: str) -> None:
    """The event is gone at the provider while its block lives: forget the id,
    so the next pass creates it again."""
    async with tenant_session(org) as db:
        await db.execute(text(
            "UPDATE calendar_task_links SET external_id = NULL, etag = NULL, "
            "sync_state = 'pending', next_attempt_at = NULL, updated_at = now() "
            "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
            "AND task_id = CAST(:tid AS uuid) AND member_email = :me"),
            {"org": org, "cid": connection_id, "tid": task_id, "me": me})


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
META_TASK_CALENDAR_ID = "task_calendar_id"
#: When the calendar "Metorite" could not be found or made (a grant without
#: ``calendar.app.created`` answers 403), no request is made before this time.
META_TASK_RETRY_AT = "task_calendar_retry_at"
CALENDAR_RETRY = timedelta(minutes=10)


SAME = "same"  # the links belong to this calendar
BOUND = "bound"  # no stored id (first pass, or a reconnect): the links are kept
CHANGED = "changed"  # another calendar id: the old events went with the old calendar


def _identity_of(cal_id: str, meta: dict[str, Any]) -> str:
    stored = meta.get(META_TASK_CALENDAR_ID)
    if stored == cal_id:
        return SAME
    return CHANGED if stored else BOUND


async def _bind(db: Any, org: str, connection_id: str, me: str, cal_id: str, outcome: str) -> None:
    """Write the binding of the links to the calendar, in the caller's transaction.

    * ``CHANGED``: the old events went with the old calendar. Drop every link of
      this connection and member WITHOUT touching a block. The next push
      creates each event again.
    * ``BOUND``: keep the links (a remove that waited for the reconnect must
      still run), keep each hash, and mark each ``pending`` so the next push
      updates its event once. A 404 there makes the event again.
    The stored id and the reset cursor are written LAST, with the binding."""
    from gateway.connections import store

    if outcome == CHANGED:
        await db.execute(text(
            "DELETE FROM calendar_task_links WHERE organization_id = CAST(:org AS uuid) "
            "AND connection_id = CAST(:cid AS uuid) AND member_email = :me"),
            {"org": org, "cid": connection_id, "me": me})
    elif outcome == BOUND:
        await db.execute(text(
            "UPDATE calendar_task_links SET sync_state = 'pending', next_attempt_at = NULL "
            "WHERE organization_id = CAST(:org AS uuid) AND connection_id = CAST(:cid AS uuid) "
            "AND member_email = :me AND external_id IS NOT NULL"),
            {"org": org, "cid": connection_id, "me": me})
    await store.merge_provider_meta(
        db, org, connection_id, {META_TASK_CALENDAR_ID: cal_id, META_TASK_CURSOR: ""})


async def calendar_identity(org: str, connection_id: str, me: str, cal_id: str,
                            meta: dict[str, Any], *, commit: bool = True) -> str:
    """Tell which calendar the links belong to, and (with ``commit``) bind them.

    * ``SAME``: the stored id is this calendar. Nothing to do.
    * ``CHANGED``: the stored id differs. See :func:`_bind`.
    * ``BOUND``: no stored id. A reconnect REPLACES ``provider_meta``
      (``save_credential``), so the origin of the links is unknown. The caller
      must NOT sweep for missing events on the one full list that follows,
      because the list may come from a new calendar.

    The pull passes ``commit=False`` and binds only after its first full list
    succeeded, so a failed read leaves the state as it was and the push waits.
    """
    from gateway.connections import store

    outcome = _identity_of(cal_id, meta)
    if outcome == SAME or not commit:
        return outcome
    async with tenant_session(org) as db:
        if await store.lock_by_id(db, org, connection_id) is None:
            return SAME
        await _bind(db, org, connection_id, me, cal_id, outcome)
    return outcome


def _calendar_retry_active(meta: dict[str, Any], t: datetime) -> bool:
    raw = meta.get(META_TASK_RETRY_AT)
    if not isinstance(raw, str) or not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (until if until.tzinfo else until.replace(tzinfo=UTC)) > t


async def _note_calendar_failure(org: str, connection_id: str, t: datetime) -> None:
    from gateway.connections import store

    async with tenant_session(org) as db:
        await store.merge_provider_meta(
            db, org, connection_id, {META_TASK_RETRY_AT: (t + CALENDAR_RETRY).isoformat()})


async def _clear_calendar_failure(org: str, connection_id: str, meta: dict[str, Any]) -> None:
    from gateway.connections import store

    if META_TASK_RETRY_AT not in meta:
        return
    async with tenant_session(org) as db:
        await store.remove_meta_keys(db, org, connection_id, (META_TASK_RETRY_AT,))


async def pull_remote_changes(
    org: str,
    connection_id: str,
    *,
    provider: Any,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """:func:`_pull` for a caller that needs only the count."""
    return await _pull(org, connection_id, provider=provider, now=now, state={})


async def _pull(
    org: str,
    connection_id: str,
    *,
    provider: Any,
    now: Callable[[], datetime],
    state: dict[str, bool],
) -> int:
    """Read the changes of the calendar "Metorite" and apply them to the blocks.

    ``state["unbound"]`` is True from the moment the links are not yet bound to
    this calendar until the first full list was applied and the binding was
    written. ``run_pass`` skips the push while it is True.

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
    if not flags.write_allowed(org, me):
        return 0
    cred = await connections.fresh_credential(org, conn, provider)
    if cred is None:
        return 0
    t = now()
    cal_id = connections.metorite_calendar_id(conn)
    if not cal_id:
        if _calendar_retry_active(conn.provider_meta, t):
            return 0  # the last try failed: no request yet
        # Forgotten (a deleted calendar) or dropped (a reconnect): find or make it.
        try:
            async with tenant_session(org) as db:
                cal_id = await connections.ensure_metorite_calendar(db, org, conn, provider, cred)
        except NeedsReconnect:
            return 0
        except SourceError as exc:
            _log.warning("calendar_sync.task_calendar_failed", organization_id=org, code=_code(exc))
            await _note_calendar_failure(org, str(conn.id), t)
            return 0
        await _clear_calendar_failure(org, str(conn.id), conn.provider_meta)
    raw = conn.provider_meta.get(META_TASK_CURSOR)
    cursor = raw if isinstance(raw, str) and raw else None
    identity = await calendar_identity(
        org, str(conn.id), me, cal_id, conn.provider_meta, commit=False)
    if identity != SAME:
        cursor = None
        state["unbound"] = True  # bound only after the first full list is applied
    window = Window.around(t)
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
        if _code(exc) == "not_found" and await _calendar_missing(provider, cred, cal_id):
            await _forget_calendar(org, str(conn.id))
        return 0

    routed = 0
    async with tenant_session(org) as db:
        locked = await store.lock_by_id(db, org, connection_id)
        if locked is None or locked.status != "connected":
            return 0
        for ev in result.changes:
            try:
                # A savepoint: one bad event must not hold the cursor.
                async with db.begin_nested():
                    outcome = await task_links.route_remote_change(db, org, locked, ev)
            except Exception as exc:  # noqa: BLE001
                _log.warning("calendar_sync.task_route_failed", organization_id=org,
                             error=type(exc).__name__)
                continue
            routed += outcome != task_links.IGNORED
        live = {e.external_id for e in result.changes if not e.deleted}
        # An empty full list is not proof of a delete: never act on it.
        if cursor is None and result.full and live and identity == SAME:
            routed += await task_links.unschedule_missing(
                db, org, locked, live, window.start, window.end)
        if identity != SAME:
            await _bind(db, org, connection_id, me, cal_id, identity)
        await store.merge_provider_meta(
            db, org, connection_id, {META_TASK_CURSOR: result.next_cursor or ""})
        state["unbound"] = False
    return routed


async def _has_links(org: str, connection_id: str) -> bool:
    async with tenant_session(org) as db:
        row = (await db.execute(text(
            "SELECT 1 FROM calendar_task_links WHERE organization_id = CAST(:org AS uuid) "
            "AND connection_id = CAST(:cid AS uuid) LIMIT 1"),
            {"org": org, "cid": connection_id})).fetchone()
    return row is not None


async def run_pass(
    org: str, connection_id: str, *, provider: Any,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PassStats:
    """What the loop calls after the main read: pull first, then push.

    A pull that did not finish while the links are not bound to the calendar (a
    reconnect, a new calendar) skips the push: the push would send an update
    that revives an event the member deleted in the gap."""
    state: dict[str, bool] = {}
    try:
        await _pull(org, connection_id, provider=provider, now=now, state=state)
    except Exception as exc:  # noqa: BLE001 - a failed read must not stop a bound push
        _log.warning("calendar_sync.task_pull_crashed", organization_id=org, error=type(exc).__name__)
    if state.get("unbound") and await _has_links(org, connection_id):
        return PassStats(skipped="pull_incomplete")  # an update could revive a gap delete
    return await reconcile_task_blocks(org, connection_id, provider=provider, now=now)
