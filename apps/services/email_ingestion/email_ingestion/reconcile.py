"""Inbound deletion reconciliation for full-snapshot syncs.

Outlook's delta sync is disabled (it stalled in prod), so deletions made
*directly in Outlook* aren't pushed to us via a change feed.  Instead, the
Outlook sync returns a FULL multi-folder snapshot; this module reconciles it:
a message we have stored that is absent from the snapshot (and not present in
any other swept folder) was removed on the provider, so we trash it locally.

Moved messages need no special handling — they reappear in their new folder in
the same snapshot and the upsert already corrected ``folder``.  Reconciliation
is bounded per folder to the refetched window (its oldest ``received_at``), so
mail older than what we re-fetched this cycle is never touched.

Only runs when ``SyncResult.full_snapshot`` is True (Outlook sweep); incremental
results (Gmail history, IMAP UIDNEXT) emit their own ``[DELETED]`` markers and
must NOT be reconciled (absence there means "unchanged", not "deleted").
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: A folder of an import trashes at most this many rows, or this share of its
#: rows in the window, whichever is larger (EM-T6b fix round 2). More means a
#: gap in the read, not real deletes, so the folder is skipped.
IMPORT_RECONCILE_MIN_CAP = 50
IMPORT_RECONCILE_SHARE = 0.02

#: The stored rows of one folder of an import window.
_IMPORT_WINDOW_ROWS = text(
    """SELECT id, provider_message_id, updated_at
       FROM email_messages
       WHERE account_id = :aid
         AND LOWER(folder) = :folder
         AND LOWER(folder) <> 'trash'
         AND received_at >= :min_recv"""
)

_TRASH_ROWS = text(
    "UPDATE email_messages SET folder = 'trash', updated_at = now() "
    "WHERE id = ANY(:ids)"
)


async def reconcile_import_snapshot(
    db: Any, account_id: str,
    snapshot: list[tuple[str, str, datetime | None]], *, started_at: datetime,
) -> int:
    """Trash stored rows that the full import of a member act did not read.

    ``snapshot`` holds ``(id, folder, received_at)`` for each message that
    the import wrote. Each folder is bounded to its oldest message in the
    snapshot. A folder with no message in it is not touched, so a folder
    skipped on 404, mail below the floor, and mail below the last page of a
    capped folder stay.

    Two guards (fix round 2). A row whose ``updated_at`` is at or after
    ``started_at`` stays: a move, a rule action or a draft during the import
    wrote it, and Graph gives a moved message a new id. A folder with more
    candidates than the cap is skipped and logged as
    ``sync.import_reconcile_skipped``. Returns the rows trashed. The caller
    owns the block and never commits inside it."""
    seen = {pid for pid, _, _ in snapshot}
    folder_min: dict[str, datetime] = {}
    for _, folder, received in snapshot:
        if received is None:
            continue
        key = (folder or "inbox").lower()
        if key not in folder_min or received < folder_min[key]:
            folder_min[key] = received

    trashed = 0
    for folder, min_recv in folder_min.items():
        rows = (await db.execute(_IMPORT_WINDOW_ROWS, {
            "aid": account_id, "folder": folder, "min_recv": min_recv})).fetchall()
        gone = [r.id for r in rows if r.provider_message_id not in seen
                and r.updated_at is not None and r.updated_at < started_at]
        cap = max(IMPORT_RECONCILE_MIN_CAP,
                  math.ceil(IMPORT_RECONCILE_SHARE * len(rows)))
        if len(gone) > cap:
            logger.warning(
                "sync.import_reconcile_skipped account=%s folder=%s "
                "candidates=%d rows=%d cap=%d",
                account_id, folder, len(gone), len(rows), cap)
            continue
        if gone:
            await db.execute(_TRASH_ROWS, {"ids": gone})
            trashed += len(gone)
    return trashed


async def reconcile_full_snapshot(db: Any, account_id: str, sync_result: Any) -> int:
    """Trash local messages that vanished from a full provider snapshot.

    Returns the number of messages trashed.  Caller commits.
    """
    if not getattr(sync_result, "full_snapshot", False):
        return 0
    msgs = [m for m in sync_result.messages if m.subject != "[DELETED]"]
    global_seen = {m.provider_message_id for m in msgs}

    # Oldest received_at actually returned per (lowercased) folder = the floor of
    # the window we can safely reconcile.
    folder_min: dict[str, datetime] = {}
    for m in msgs:
        if m.received_at is None:
            continue
        key = (m.folder or "inbox").lower()
        cur = folder_min.get(key)
        if cur is None or m.received_at < cur:
            folder_min[key] = m.received_at

    trashed = 0
    for folder, min_recv in folder_min.items():
        rows = (await db.execute(
            text(
                """SELECT id, provider_message_id
                   FROM email_messages
                   WHERE account_id = :aid
                     AND LOWER(folder) = :folder
                     AND LOWER(folder) <> 'trash'
                     AND received_at >= :min_recv"""
            ),
            {"aid": account_id, "folder": folder, "min_recv": min_recv},
        )).fetchall()
        for r in rows:
            if r.provider_message_id in global_seen:
                continue  # still present (here, or moved to another folder)
            await db.execute(
                text(
                    "UPDATE email_messages SET folder = 'trash', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": r.id},
            )
            trashed += 1
    return trashed
