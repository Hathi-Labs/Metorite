"""The link between a task block and its provider event (CE-13, D215).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1a points 7 and 8.
Table: ``calendar_task_links`` (migration 419). One row for each task, member
and connection. The provider event id lives there, and nowhere else.

This module holds the pure rules and the two reads that the read loop needs:

* :func:`block_hash` is the fingerprint of what the provider holds.
* :func:`is_task_link` tells the read loop to skip an event of ours.
* :func:`route_remote_change` is the return path. A move or a resize at the
  provider moves the block. A delete or a cancel at the provider unschedules
  the block, and the task stays. The newer time wins: the provider's
  ``updated`` against the ``updated_at`` of the block. An ``etag`` equal to the
  stored one is our own echo and changes nothing.

Every function takes ``db`` from ``tenant_session`` and ``org`` from the
server, so row level security binds. Each query also filters on the member of
the connection, the second axis.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

# Name of the outcomes of the return path, for the log and the tests.
ECHO = "echo"
MOVED = "moved"
UNSCHEDULED = "unscheduled"
KEPT = "kept"  # the block is newer than the provider: Metorite wins
IGNORED = "ignored"  # not an event of ours


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def block_hash(title: str, starts_at: datetime, ends_at: datetime) -> str:
    """The fingerprint of the event that the provider holds for a block."""
    s, e = _utc(starts_at), _utc(ends_at)
    raw = f"{title}\x1f{s.isoformat()}\x1f{e.isoformat()}"  # type: ignore[union-attr]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def is_task_link(db: Any, org: str, connection_id: str, external_id: str) -> bool:
    """True when the event is the provider copy of a task block of ours."""
    row = (await db.execute(
        text(
            "SELECT 1 FROM calendar_task_links "
            "WHERE organization_id = CAST(:org AS uuid) "
            "AND connection_id = CAST(:cid AS uuid) AND external_id = :eid"
        ),
        {"org": org, "cid": connection_id, "eid": external_id},
    )).fetchone()
    return row is not None


async def route_remote_change(db: Any, org: str, conn: Any, ev: Any) -> str:
    """Apply a change at the provider to the block. Returns an outcome word.

    *conn* needs ``id`` and ``member_email``. *ev* is a ``RemoteEvent``
    (``external_id``, ``etag``, ``starts_at``, ``ends_at``, ``status``,
    ``deleted`` and, when the provider core gives it, ``updated``).
    """
    me = str(conn.member_email).lower()
    link = (await db.execute(
        text(
            "SELECT task_id::text AS task_id, etag, synced_block_hash, provider_updated "
            "FROM calendar_task_links "
            "WHERE organization_id = CAST(:org AS uuid) "
            "AND connection_id = CAST(:cid AS uuid) AND external_id = :eid "
            "AND member_email = :me FOR UPDATE"
        ),
        {"org": org, "cid": str(conn.id), "eid": ev.external_id, "me": me},
    )).fetchone()
    if link is None:
        return IGNORED

    gone = bool(getattr(ev, "deleted", False)) or getattr(ev, "status", "") == "cancelled"
    if gone:
        await _unschedule(db, link.task_id, me)
        await db.execute(
            text(
                "DELETE FROM calendar_task_links "
                "WHERE organization_id = CAST(:org AS uuid) "
                "AND connection_id = CAST(:cid AS uuid) "
                "AND task_id = CAST(:tid AS uuid) AND member_email = :me"
            ),
            {"org": org, "cid": str(conn.id), "tid": link.task_id, "me": me},
        )
        return UNSCHEDULED

    if ev.etag is not None and ev.etag == link.etag:
        return ECHO

    starts, ends = _utc(ev.starts_at), _utc(ev.ends_at)
    if starts is None or ends is None or ends <= starts:
        # An all-day event or an impossible pair is not a block. Keep ours.
        return KEPT

    block = (await db.execute(
        text(
            "SELECT p.scheduled_start, p.scheduled_end, p.updated_at, t.title "
            "FROM pm_task_personal p JOIN pm_tasks t ON t.id = p.task_id "
            "WHERE p.task_id = CAST(:tid AS uuid) AND lower(p.member_email) = :me"
        ),
        {"tid": link.task_id, "me": me},
    )).fetchone()
    if block is None:
        return IGNORED

    if (_utc(block.scheduled_start) == starts and _utc(block.scheduled_end) == ends):
        # Same time: only the etag moved (a rename, a colour). Remember it.
        await _touch(db, org, conn, link.task_id, me, etag=ev.etag, updated=getattr(ev, "updated", None))
        return ECHO

    remote_updated = _utc(getattr(ev, "updated", None))
    # The time of the BLOCK did not change since the last sync when its hash
    # still equals the stored one. Then Metorite holds no newer plan, and the
    # provider wins even if an unrelated overlay edit (a note, a flag) bumped
    # `updated_at`. Only a block that Metorite changed goes to last-writer-wins.
    unchanged = block_hash(block.title, block.scheduled_start, block.scheduled_end) == link.synced_block_hash
    if unchanged:
        # A change older than the last one we saw is a late arrival: drop it.
        seen = _utc(link.provider_updated)
        if remote_updated is not None and seen is not None and remote_updated < seen:
            return KEPT
    elif remote_updated is not None and remote_updated <= _utc(block.updated_at):
        return KEPT  # the block changed later: it goes out at the next pass

    await _move_block(db, link.task_id, me, starts, ends)
    await _touch(
        db, org, conn, link.task_id, me, etag=ev.etag, updated=remote_updated,
        hash_=block_hash(block.title, starts, ends),
    )
    return MOVED


async def _touch(
    db: Any, org: str, conn: Any, task_id: str, me: str, *,
    etag: str | None, updated: datetime | None, hash_: str | None = None,
) -> None:
    await db.execute(
        text(
            "UPDATE calendar_task_links SET etag = :etag, "
            "provider_updated = COALESCE(:upd, provider_updated), "
            "synced_block_hash = COALESCE(:h, synced_block_hash), updated_at = now() "
            "WHERE organization_id = CAST(:org AS uuid) "
            "AND connection_id = CAST(:cid AS uuid) "
            "AND task_id = CAST(:tid AS uuid) AND member_email = :me"
        ),
        {"etag": etag, "upd": updated, "h": hash_, "org": org,
         "cid": str(conn.id), "tid": task_id, "me": me},
    )


async def _move_block(db: Any, task_id: str, me: str, starts: datetime, ends: datetime) -> None:
    """Move MY block, through the one overlay writer."""
    from gateway.routes.projects.personal import _upsert_personal

    await _upsert_personal(db, task_id, me, {"scheduled_start": starts, "scheduled_end": ends})


async def _unschedule(db: Any, task_id: str, me: str) -> None:
    """Clear MY block. The task stays."""
    from gateway.routes.projects.personal import _upsert_personal

    await _upsert_personal(db, task_id, me, {"scheduled_start": None, "scheduled_end": None})


async def unschedule_missing(
    db: Any, org: str, conn: Any, live_ids: set[str], start: datetime, end: datetime,
) -> int:
    """After a FULL list: a link whose event the list no longer holds, and
    whose block starts inside the listed window, was deleted at the provider.
    Unschedule its block and drop the link. The task stays."""
    me = str(conn.member_email).lower()
    rows = (await db.execute(
        text(
            "SELECT l.task_id::text AS task_id, l.external_id FROM calendar_task_links l "
            "JOIN pm_task_personal p ON p.task_id = l.task_id "
            "AND lower(p.member_email) = l.member_email "
            "WHERE l.organization_id = CAST(:org AS uuid) "
            "AND l.connection_id = CAST(:cid AS uuid) AND l.member_email = :me "
            "AND l.external_id IS NOT NULL AND l.sync_state = 'sent' "
            "AND p.scheduled_start >= :s AND p.scheduled_start < :e"
        ),
        {"org": org, "cid": str(conn.id), "me": me, "s": start, "e": end},
    )).fetchall()
    n = 0
    for r in rows:
        if r.external_id in live_ids:
            continue
        await _unschedule(db, r.task_id, me)
        await db.execute(
            text(
                "DELETE FROM calendar_task_links "
                "WHERE organization_id = CAST(:org AS uuid) "
                "AND connection_id = CAST(:cid AS uuid) "
                "AND task_id = CAST(:tid AS uuid) AND member_email = :me"
            ),
            {"org": org, "cid": str(conn.id), "tid": r.task_id, "me": me},
        )
        n += 1
    return n
