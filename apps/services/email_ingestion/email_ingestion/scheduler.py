"""Background email sync scheduler.

Periodically syncs all enabled email accounts using their configured
sync_interval_secs (default 300s).  Runs as a set of asyncio tasks managed
by the gateway lifespan.

Architecture:
- On startup: list the organizations, then per organization query
  email_accounts WHERE sync_enabled = true inside ``tenant_session(org)``
- For each account: launch an asyncio task that binds its organization and
  calls _sync_account() in a loop
- On shutdown: cancel all tasks, wait for in-flight syncs to finish
- Accounts added/removed at runtime via /email accounts endpoints also
  refresh the task set via the registry pattern.

Tenancy (WS-17 EM-T1b-1, ``email_app_master_plan.md`` §10.4.2). Every session
comes from the shared seam ``acb_common.db``. Each sync loop binds the
organization of its account, and every write runs in ``tenant_session(org)``.
⚠️ A ``commit()`` inside a ``tenant_session`` ends ``SET LOCAL``, and each
statement after it runs with no tenant. So the sync core is split into
PHASES, each one its own ``tenant_session(org)`` with no ``commit()``. The
fence is ``tests/unit/test_email_scheduler_tenancy.py``.

⚠️ No session stays open across an external call (WS-17 EM-T4a-1,
``email_app_master_plan.md`` §10.4.6). Phase (b) authenticates and fetches
with no session. Phases (e) and (f) read in one block, call the provider or
the model with no session, and write in a second block. A session held
across a slow call holds one of the 12 pool slots for the whole call. The
watched fakes of the same test file fail on a call made with a block open.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common.db import (
    TenantUnbound,
    bind_tenant,
    current_tenant,
    get_db,
    release_tenant,
    tenant_session,
)
from sqlalchemy import text

from email_ingestion import body_backfill, email_embeddings, import_window
from email_ingestion.persist import upsert_message
from email_ingestion.post_sync import hooks, run_hook, run_label_learn_hook
from email_ingestion.providers.factory import build_provider
from email_ingestion.reconcile import (
    import_reconcile_candidates,
    reconcile_full_snapshot,
    trash_import_rows,
)

# Ceiling for the failure backoff. When an account's sync keeps failing (a
# revoked token, an account the user disconnected upstream), polling it every
# ``sync_interval_secs`` (300s) just burns a failing auth handshake and a Graph
# call twelve times an hour, forever. Back off exponentially from the normal
# interval up to this cap (~1h), and reset to the interval the moment a sync
# succeeds again.
_MAX_SYNC_BACKOFF_SECS = 3600

logger = logging.getLogger(__name__)

# -- Singleton state ----------------------------------------------------------

_scheduler_tasks: dict[str, asyncio.Task] = {}  # account_id -> running task
_scheduler_lock = asyncio.Lock()
_scheduler_running = False

# -- One sync for each mailbox (WS-17 EM-T4f part 2) --------------------------
#
# ``_sync_account`` takes the lock of its mailbox, so one sync cycle runs for
# each mailbox at a time. Two cycles of one mailbox upsert the same
# ``(account_id, provider_message_id)`` keys, and one waited on the
# uncommitted rows of the other until the statement timeout (production,
# 2026-10-02). The gateway is one uvicorn process
# (``deploy/hostinger/acb-gateway.service``), so a lock in this process is
# enough. A second process would need a database lock instead.
#
# ``_sync_lock_users`` counts the holder and the waiters of each lock. When the
# count goes to zero, both entries go, so the dicts hold only the mailboxes
# that sync now.
_sync_locks: dict[str, asyncio.Lock] = {}
_sync_lock_users: dict[str, int] = {}
# Mailboxes where a caller skipped while a sync held the lock. The holder runs
# one more shallow cycle for them (fix round 2). The set empties with the lock.
_sync_rerun: set[str] = set()

#: Consecutive cycles whose catch-up stopped short, for each mailbox (fix
#: round 3). It lives in this process, as the mailbox lock does, and a
#: restart starts it again at 0. The later full fix is a watermark for each
#: folder.
_catch_up_misses: dict[str, int] = {}

#: After this many short catch-ups in a row, the watermark moves on, so one
#: folder that always fails cannot stall the mailbox (fix round 3).
CATCH_UP_MAX_MISSES = 6

#: How long a caller that waits (the deep downloads of cleanup and Process
#: past) waits for the sync that holds the mailbox. In production, the
#: first sync of 6410 messages fell inside a window of about 4 minutes (08:47
#: to 08:51 UTC, 2026-10-02). Ten minutes is more than twice that. A longer
#: wait means the holder is stuck, and the waiter gives up rather than queue
#: behind it. The statement timeout (2 minutes) bounds each phase of the
#: holder, and the provider client bounds each request.
SYNC_LOCK_WAIT_SECS = 600.0

#: What ``_sync_account`` returns when it does not run because another sync
#: holds the mailbox. A caller treats it as a success with nothing synced.
SYNC_SKIPPED_BUSY: dict[str, Any] = {"skipped": "busy", "synced": 0}

#: What a sync cycle returns when the account row is gone. It is an error for
#: each caller, and the loop stops on ``gone`` (EM-T4f part 2).
ACCOUNT_GONE: dict[str, Any] = {"error": "Account not found", "gone": True}

#: The job error of a deep download whose wait reached its bound.
DOWNLOAD_BUSY_ERROR = (
    "Another sync of this mailbox ran for too long. Try again when it ends."
)


def download_failure(result: Any) -> str | None:
    """Why a deep download did not fetch, or ``None`` when it ran.

    The deep downloads of cleanup and Process past read this (EM-T4f fix
    round 2). A busy result means that the wait for another sync reached its
    bound. An ``error`` result is a failed download. Both end the job as an
    error, where the job used to say "done" with 0 fetched.
    """
    if not isinstance(result, dict):
        return "The download of older mail gave no result."
    if result.get("skipped") == "busy":
        return DOWNLOAD_BUSY_ERROR
    if result.get("error"):
        return f"The download of older mail failed: {result['error']}"[:200]
    return None


# -- The import in batches (WS-17 EM-T6b) -------------------------------------

#: The messages in one batch of an import. One ``tenant_session(org)`` writes
#: each batch and its progress.
IMPORT_BATCH_SIZE = 100

#: The margin of the catch-up watermark: the recurring sweep reads back to
#: ``last_synced_at`` minus this (EM-T6b item 9, D-EM-13).
CATCH_UP_MARGIN = timedelta(hours=1)

#: Before the first batch. A fresh import starts its count at 0. A resume
#: keeps the count that it reached. The estimate comes next.
_IMPORT_COUNTING = text(
    """UPDATE email_accounts
       SET import_phase = 'counting', import_count = :count,
           import_estimate = NULL, updated_at = now()
       WHERE id = :id"""
)

_IMPORT_ESTIMATE = text(
    """UPDATE email_accounts
       SET import_estimate = :estimate, updated_at = now()
       WHERE id = :id"""
)

#: With each batch, in the block that writes its messages. A batch that wrote
#: no dated message keeps the point that the import reached.
_IMPORT_BATCH = text(
    """UPDATE email_accounts
       SET import_phase = 'importing',
           import_reached_at = COALESCE(CAST(:reached AS timestamptz),
                                        import_reached_at),
           import_count = :count, updated_at = now()
       WHERE id = :id"""
)

_IMPORT_DONE = text(
    """UPDATE email_accounts
       SET initial_sync_done = true, import_phase = 'done', updated_at = now()
       WHERE id = :id"""
)

#: Phase (d) of a cycle whose import failed (fix round 2). The new mail of
#: the cycle has landed. The account shows the error, and the next cycle
#: resumes the import.
_IMPORT_ERROR_ACCOUNT = text(
    """UPDATE email_accounts
       SET sync_status = 'error', sync_error = :import_error,
           updated_at = now()
       WHERE id = :id"""
)

_IMPORT_ERROR_LOG = text(
    """UPDATE email_sync_log
       SET status = 'error', error_message = :import_error
       WHERE id = :log_id"""
)


async def _record_import_error(
    db: Any, account_id: str, sync_log_id: Any, import_error: str | None,
) -> None:
    """Write the error of a failed import in the block of phase (d), after
    the account and log rows of the cycle. With no error, it writes nothing.
    It never calls ``commit()``."""
    if not import_error:
        return
    params = {"id": account_id, "log_id": sync_log_id,
              "import_error": import_error[:1000]}
    await db.execute(_IMPORT_ERROR_ACCOUNT, params)
    await db.execute(_IMPORT_ERROR_LOG, params)

_TRASH_DELETED = text(
    """UPDATE email_messages
       SET folder = 'TRASH', updated_at = now()
       WHERE account_id = :account_id
         AND provider_message_id = :provider_id"""
)

_STORED_CATEGORIES = text(
    "SELECT categories FROM email_messages "
    "WHERE account_id = :aid AND provider_message_id = :pid"
)


def _next_backoff(current: int, interval: int, *, failed: bool) -> int:
    """The next sleep length for a sync loop.

    On success returns 0 — the caller sleeps the normal ``interval``. On failure
    doubles from the interval each consecutive time (``interval*2`` → ``*4`` → …)
    capped at ``_MAX_SYNC_BACKOFF_SECS``, so a persistently failing account is
    polled ever less often instead of every interval forever.
    """
    if not failed:
        return 0
    return min(current * 2 if current else interval * 2, _MAX_SYNC_BACKOFF_SECS)


async def _close_orphaned_syncs(db: Any, organization_id: str) -> None:
    """Close sync-log rows (and account statuses) left mid-flight by a process
    that crashed or restarted during a sync. Nothing completes them once that
    process is gone, so they linger 'running'/'syncing' forever and lie to any
    "is a sync in progress?" check. A fresh scheduler owns every sync now, so any
    pre-existing in-flight row is by definition orphaned.

    The caller hands in a ``tenant_session(org)``. Both statements ALSO filter
    on ``organization_id``, so on a catalog without RLS they still touch only
    the rows of that one organization. The startup sweep calls it once per
    organization."""
    params = {"org": organization_id}
    await db.execute(text(
        "UPDATE email_sync_log SET status = 'error', "
        "error_message = 'interrupted by scheduler restart', "
        "completed_at = now() WHERE status = 'running' "
        "AND organization_id = CAST(:org AS uuid)"), params)
    await db.execute(text(
        "UPDATE email_accounts SET sync_status = 'idle', "
        "updated_at = now() WHERE sync_status = 'syncing' "
        "AND organization_id = CAST(:org AS uuid)"), params)


async def _list_organizations() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of the scheduler: it answers "which tenants exist",
    so it cannot run inside one. Same shape as ``routes/tasks/calendar.py``
    ``_run_rollover_sweep``. Ordered, so the sweep is deterministic.
    """
    db = await get_db()
    try:
        rows = (await db.execute(
            text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


async def _sweep_one_org(organization_id: str) -> list[tuple[str, int | None]]:
    """Close the orphaned syncs of one organization and list its accounts
    with ``sync_enabled``, inside ONE ``tenant_session(org)``.

    The account read filters on ``organization_id`` as well as RLS, so an
    account binds only the organization that owns it on every catalog."""
    async with tenant_session(organization_id) as db:
        await _close_orphaned_syncs(db, organization_id)
        result = await db.execute(
            text(
                """SELECT id, sync_interval_secs
                   FROM email_accounts
                   WHERE sync_enabled = true
                     AND organization_id = CAST(:org AS uuid)"""
            ),
            {"org": organization_id},
        )
        # str(), not row.id. asyncpg hands back a UUID OBJECT, and this
        # value is the `account_id: str` threaded through the entire
        # new-mail pipeline — sync, rules, drafting, memory scoping.
        # Anything that merely puts it in a SQL parameter works fine, so
        # the wrong type stayed invisible until something did string
        # work with it: the reply drafter raised
        # "'asyncpg.pgproto.pgproto.UUID' object has no attribute
        # 'strip'" and silently produced no draft, while the LABEL
        # action on the same rule succeeded. Every other entry point
        # (routes, webhook) already passes a real str.
        return [(str(row.id), row.sync_interval_secs)
                for row in result.fetchall()]


# -- Core sync logic (shared with manual /email/sync endpoint) ---------------


async def _backfill_bodies(org: str, account_id: str, provider: Any) -> int:
    """Phase (e): hydrate a bounded batch of empty bodies. Returns how many
    it wrote.

    Read the candidates in one ``tenant_session(org)``. Fetch each body with
    NO session open. Write the bodies in a second block (EM-T4a-1). With no
    candidates it opens one block and makes no provider call. No step commits,
    because the seam commits when each block exits."""
    async with tenant_session(org) as db:
        candidates = await body_backfill.select_missing_bodies(db, account_id)
    if not candidates:
        return 0
    fetched = await body_backfill.fetch_bodies(provider, account_id, candidates)
    if not fetched:
        return 0
    async with tenant_session(org) as db:
        return await body_backfill.write_bodies(db, account_id, fetched)


async def _embed_messages(org: str, account_id: str) -> int:
    """Phase (f): embed a bounded batch for semantic search. Returns how many
    it wrote.

    Read the pending messages in one ``tenant_session(org)``. The read step
    returns ``None`` when semantic search is off. Call the model with NO
    session open. Write the vectors in a second block (EM-T4a-1)."""
    async with tenant_session(org) as db:
        pending = await email_embeddings.select_pending_embeddings(
            db, account_id)
    if pending is None:
        return 0
    vectors = await email_embeddings.compute_embeddings(pending)
    if vectors is None:
        return 0
    async with tenant_session(org) as db:
        return await email_embeddings.write_embeddings(
            db, account_id, pending, vectors)


_WRITE_CREDENTIALS = text(
    """UPDATE email_accounts
       SET credentials_encrypted = :creds, updated_at = now()
       WHERE id = :id"""
)

#: The stored ids among the messages below the floor (EM-T6a item 6).
_STORED_IDS = text(
    """SELECT provider_message_id FROM email_messages
       WHERE account_id = :aid AND provider_message_id = ANY(:pids)"""
)


async def _drop_below_floor(
    db: Any, account_id: str, messages: list[Any], floor: datetime,
) -> list[Any]:
    """The messages that phase (c) may write: the backstop of the floor.

    It drops each message older than ``floor`` (D-EM-10, EM-T6a item 6). It
    keeps such a message when its row is already stored, so the sync still
    updates it. It keeps a message with no ``received_at``. With no message
    below the floor, it makes no query. Otherwise ONE query reads the stored
    ids. The caller hands in the session of phase (c)."""
    old = {m.provider_message_id for m in messages
           if import_window.below_floor(m.received_at, floor)}
    if not old:
        return messages
    rows = (await db.execute(
        _STORED_IDS, {"aid": account_id, "pids": sorted(old)})).fetchall()
    drop = old - {r.provider_message_id for r in rows}
    kept = [m for m in messages if m.provider_message_id not in drop
            or not import_window.below_floor(m.received_at, floor)]
    if len(kept) < len(messages):
        logger.info("sync.dropped_below_floor account=%s count=%d",
                    account_id, len(messages) - len(kept))
    return kept


async def _write_messages(
    db: Any, account_id: str, messages: list[Any], floor: datetime, *,
    learn_labels: bool = False,
) -> tuple[list[Any], list[Any]]:
    """Write *messages* in the session of the caller. Returns the messages
    that it wrote, and the label changes.

    The ONE write of phase (c) and of each import batch (EM-T6b). It drops
    each message below the floor first (``_drop_below_floor``). A
    ``[DELETED]`` marker moves its row to TRASH. Every other message goes
    through the shared upsert. With ``learn_labels``, it reads the stored
    categories of an existing row first, because the upsert overwrites them.
    It never calls ``commit()``."""
    kept = await _drop_below_floor(db, account_id, messages, floor)
    label_changes: list[Any] = []
    for msg in kept:
        if msg.subject == "[DELETED]":
            await db.execute(_TRASH_DELETED, {
                "account_id": account_id,
                "provider_id": msg.provider_message_id})
            continue
        old_categories = None
        if learn_labels:
            stored = (await db.execute(_STORED_CATEGORIES, {
                "aid": account_id,
                "pid": msg.provider_message_id})).fetchone()
            # Existing rows only — a brand-new message has no prior
            # categories to diff against.
            old_categories = list(stored.categories or []) if stored else None
        # ONE shared ingest upsert (message + attachments); see
        # email_ingestion.persist.upsert_message.
        await upsert_message(db, account_id, msg)
        if old_categories is not None:
            label_changes.append((msg, old_categories))
    return kept, label_changes


def _catch_up_watermark(row: Any) -> datetime | None:
    """How far back the recurring sweep reads after a pause (EM-T6b item 9).

    ``last_synced_at`` minus ``CATCH_UP_MARGIN``, or ``created_at`` when the
    mailbox never finished a sync (D-EM-13). ``None`` when the row holds
    neither, and the sweep then reads its normal pages."""
    synced = getattr(row, "last_synced_at", None)
    if synced is not None:
        return synced - CATCH_UP_MARGIN
    return getattr(row, "created_at", None)


def _watermark_outcome(
    account_id: str, sync_result: Any, row: Any, import_error: str | None,
) -> tuple[bool, str | None]:
    """Whether phase (d) keeps ``last_synced_at``, and the ``sync_error``
    note of a short catch-up (fix round 3).

    * A failed import on a mailbox that never finished a sync keeps
      ``last_synced_at`` NULL. The next sweep then reads back to
      ``created_at``, so the mail since the connect still lands.
    * A sweep folder that stopped short of the watermark keeps it too, and
      the note names the folder, never a provider message. The loop counts
      the cycle as a soft failure and backs off.
    * After ``CATCH_UP_MAX_MISSES`` short cycles in a row, the watermark
      moves on, and the log says ``sync.catch_up_abandoned`` with the folder
      and the gap.
    """
    keep_null = import_error is not None and getattr(row, "last_synced_at", None) is None
    key = _lock_key(account_id)
    if not getattr(sync_result, "catch_up_incomplete", False):
        _catch_up_misses.pop(key, None)
        return keep_null, None
    folders = ", ".join(getattr(sync_result, "catch_up_folders", None) or []) or "a folder"
    misses = _catch_up_misses.get(key, 0) + 1
    if misses < CATCH_UP_MAX_MISSES:
        _catch_up_misses[key] = misses
        return True, f"The catch-up of {folders} is incomplete. The next sync tries again."
    _catch_up_misses.pop(key, None)
    watermark = _catch_up_watermark(row)
    gap = (datetime.now(UTC) - watermark).total_seconds() if watermark else 0.0
    logger.warning("sync.catch_up_abandoned account=%s folder=%s gap_secs=%d misses=%d",
                   account_id, folders, int(gap), misses)
    return keep_null, (f"The catch-up of {folders} stopped after {misses} tries. "
                       "Older mail of the pause can be missing.")


def _cycle_result(
    synced: int, history_id: Any, import_error: str | None, note: str | None,
) -> dict[str, Any]:
    """The result of a sync cycle. A failed import is an error for each
    caller, after the new mail landed. A short catch-up is a soft failure:
    the loop backs off, and no caller sees an error (fix round 3)."""
    result: dict[str, Any] = {"synced": synced, "history_id": history_id}
    if note:
        result["catch_up_incomplete"] = True
    if import_error:
        result["error"] = import_error
    return result


def _oldest_received(messages: list[Any]) -> datetime | None:
    """The oldest ``received_at`` among *messages*, or None with no date."""
    dated = [m.received_at for m in messages if m.received_at is not None]
    return min(dated) if dated else None


async def _confirm_gone(
    provider: Any, candidates: list[tuple[Any, str | None]],
) -> list[Any]:
    """The candidate rows whose message the provider no longer has.

    It asks the provider by ``internet_message_id``, with no session open
    (fix round 3). A move in the Outlook client gives the message a new id,
    so the import did not read it, and the lookup still finds it. A row with
    no internet message id, a failed lookup, or a provider with no lookup
    keeps its row: the reconcile never trashes on doubt."""
    exists = getattr(provider, "message_exists", None)
    if exists is None:
        return []
    gone: list[Any] = []
    for row_id, internet_message_id in candidates:
        if not internet_message_id:
            continue
        try:
            found = await exists(internet_message_id)
        except Exception as exc:
            logger.info("sync.import_reconcile_lookup_failed error=%s",
                        type(exc).__name__)
            continue
        if found is False:
            gone.append(row_id)
    return gone


async def _reconcile_import(
    org: str, account_id: str, provider: Any,
    snapshot: list[tuple[str, str, Any]], started_at: datetime | None,
) -> None:
    """Reconcile deletions against the full snapshot of a member-act import.

    One block reads the candidates. The provider then confirms each one with
    no session open, and a second block moves the confirmed rows to trash
    (fix round 3). A failure rolls back only the reconcile, and the sync goes
    on (fix round 1). With no database time for the start, it trashes
    nothing (fix round 2)."""
    if started_at is None:
        logger.warning("sync.import_reconcile_skipped account=%s reason=no_start",
                       account_id)
        return
    try:
        async with tenant_session(org) as db:
            candidates = await import_reconcile_candidates(
                db, account_id, snapshot, started_at=started_at)
        gone = await _confirm_gone(provider, candidates)
        if not gone:
            return
        async with tenant_session(org) as db:
            removed = await trash_import_rows(db, gone, started_at=started_at)
        logger.info("sync.import_reconciled_deletions account=%s removed=%d "
                    "candidates=%d", account_id, removed, len(candidates))
    except Exception as exc:
        logger.warning("sync.import_reconcile_failed account=%s err=%s",
                       account_id, str(exc)[:160])


async def _import_in_batches(
    org: str, account_id: str, provider: Any, row: Any, *,
    floor: datetime, progress: bool,
) -> tuple[int, str | None]:
    """Run the import, and return the rows that it wrote and its error.

    The error is ``None`` when the import ended. A failed import does not
    raise (fix round 2): the cycle still runs the recurring sweep, so new mail
    lands (owner answer Q2), and then records the error. The next cycle
    resumes the import."""
    tally = {"written": 0}
    try:
        await _run_import(org, account_id, provider, row, floor=floor,
                          progress=progress, tally=tally)
    except Exception as exc:
        logger.warning("sync.import_failed account=%s written=%d error=%s",
                       account_id, tally["written"], str(exc)[:200])
        return tally["written"], str(exc)
    return tally["written"], None


async def _run_import(
    org: str, account_id: str, provider: Any, row: Any, *,
    floor: datetime, progress: bool, tally: dict[str, int],
) -> None:
    """Import the mail of the mailbox newest first, one batch at a time.
    ``tally`` counts the rows that it wrote. WS-17 EM-T6b items 4 to 8.

    It fetches each batch with NO session open. One ``tenant_session(org)``
    then writes the messages of the batch, and the progress when
    ``progress`` is true. No block calls ``commit()``. An import batch runs
    neither the reconcile nor the label learner (item 10).

    ``progress`` is true for the first import only. That import writes
    ``import_phase = 'counting'``, then the estimate, then ``'importing'``,
    ``import_reached_at`` and ``import_count`` with each batch, and at the
    end ``initial_sync_done = true`` and ``import_phase = 'done'``. A resume
    starts at ``import_reached_at``, with that point as ``until``, and keeps
    the count. The upsert makes the overlap at that point harmless. The deep
    sync of a member act imports from now to the floor, and writes no
    progress column and no ``initial_sync_done``. An error raises past the
    progress, which stays as the last batch left it.

    When the deep sync of a member act ends with no error, and the provider
    reads every folder (``import_full_snapshot``), one more block reconciles
    deletions against the id, folder and time of each message the import
    wrote (fix round 1). The recurring sweep reaches only its newest pages,
    so a Resync otherwise kept older mail that the member deleted in Outlook.
    It keeps a row written after phase (a), and it skips a folder with too
    many candidates (``import_reconcile_candidates``, fix round 2). A failed
    reconcile is logged and does not fail the sync."""
    snapshot: list[tuple[str, str, Any]] | None = (
        [] if not progress and getattr(provider, "import_full_snapshot", False)
        else None)
    until = getattr(row, "import_reached_at", None) if progress else None
    count = (getattr(row, "import_count", None) or 0) if until is not None else 0
    on_estimate = None
    if progress:
        async with tenant_session(org) as db:
            await db.execute(_IMPORT_COUNTING, {"id": account_id, "count": count})
        base = count

        async def on_estimate(estimate: int | None) -> None:
            # A resume counts only the mail below the point it reached.
            total = None if estimate is None else base + estimate
            if total is None:
                logger.info("sync.import_estimate_unknown account=%s", account_id)
            async with tenant_session(org) as db:
                await db.execute(_IMPORT_ESTIMATE,
                                 {"id": account_id, "estimate": total})

    async with aclosing(provider.import_batches(
            since=floor, until=until, size=IMPORT_BATCH_SIZE,
            on_estimate=on_estimate)) as batches:
        async for batch in batches:
            async with tenant_session(org) as db:
                kept, _ = await _write_messages(db, account_id, batch, floor)
                if progress:
                    count += len(kept)
                    await db.execute(_IMPORT_BATCH, {
                        "id": account_id, "reached": _oldest_received(kept),
                        "count": count})
            tally["written"] += len(kept)
            if snapshot is not None:
                snapshot.extend((m.provider_message_id, m.folder, m.received_at)
                                for m in kept)
    if snapshot is not None:
        await _reconcile_import(org, account_id, provider, snapshot,
                                getattr(row, "db_now", None))
    if progress:
        async with tenant_session(org) as db:
            await db.execute(_IMPORT_DONE, {"id": account_id})
        logger.info("sync.import_done account=%s count=%d", account_id, count)


def _credentials_json(provider: Any) -> str | None:
    """The credentials of *provider* as JSON when a refresh changed them.

    ``None`` when no provider was built, or when nothing changed. Two of
    these values tell whether a refresh happened between them (EM-T4c)."""
    if provider is None or not provider.credentials_dirty():
        return None
    return json.dumps(provider.export_credentials())


def _dirty_credentials(provider: Any, store: Any) -> str | None:
    """The encrypted credentials of *provider* when a refresh changed them.

    The error path of ``_sync_account`` writes this value (EM-T4c). It is
    ``None`` when no provider was built, or when nothing changed. A failure
    to encrypt is logged and also gives ``None``, so the error status still
    lands."""
    if store is None:
        return None
    try:
        plain = _credentials_json(provider)
        return None if plain is None else store.encrypt(plain)
    except Exception as exc:
        logger.warning("sync.credentials_keep_failed error=%s", str(exc)[:160])
        return None


async def _write_credentials_refreshed_since(
    org: str, account_id: str, provider: Any, store: Any, written: str | None,
) -> None:
    """Write the credentials when a refresh changed them after *written*.

    Phase (d) writes the credentials with the account row. Phase (e) then
    fetches bodies, and a 401 there refreshes the token again (EM-T4c).
    Microsoft can rotate the refresh token on use. So this short block
    writes the new tokens, and the next sync does not start with an old one.
    With no change since phase (d), it opens no session.

    It never raises (fix round 2). A failed write is tried once more in a
    new block. The sync has done its work, so a failure here must not turn
    it into an error: ``_webhook_sync`` would skip the new-mail pipeline,
    and the loop would double its backoff. The log names the class of the
    error and never a token."""
    now = _credentials_json(provider)
    if now is None or now == written:
        return
    for attempt in (1, 2):
        try:
            creds = store.encrypt(now)
            async with tenant_session(org) as db:
                await db.execute(_WRITE_CREDENTIALS,
                                 {"id": account_id, "creds": creds})
        except Exception as exc:
            logger.warning(
                "sync.credentials_write_failed account=%s attempt=%d error=%s",
                account_id, attempt, type(exc).__name__)
            continue
        if attempt > 1:
            logger.info("sync.credentials_write_retried account=%s", account_id)
        return
    logger.error("sync.credentials_write_lost account=%s", account_id)


def _lock_key(account_id: str) -> str:
    """The key of a mailbox lock. A UUID in upper case is the same mailbox."""
    return str(account_id).strip().lower()


def sync_busy(account_id: str) -> bool:
    """True while a sync of the mailbox runs, or a caller waits to run one."""
    return _sync_lock_users.get(_lock_key(account_id), 0) > 0


def _leave_sync_lock(key: str) -> None:
    """Count one holder or waiter out, and drop the lock when none is left."""
    left = _sync_lock_users.get(key, 0) - 1
    if left > 0:
        _sync_lock_users[key] = left
        return
    _sync_lock_users.pop(key, None)
    _sync_locks.pop(key, None)
    _sync_rerun.discard(key)


async def _sync_account(
    account_id: str, *, organization_id: str | None = None,
    deep: bool | None = None, since: datetime | None = None,
    if_busy: str = "wait", purge: bool = False, reset_cursor: bool = False,
) -> dict[str, Any]:
    """Run one sync cycle of a mailbox, and never two at once (EM-T4f part 2).

    Every caller comes through here: the loop, the manual sync, the resync,
    the webhook sync, and the deep downloads of cleanup and of Process past.
    The cycle itself is ``_sync_cycle``. The lock key is the account id in
    lower case.

    ``if_busy`` says what a caller does when another sync holds the mailbox:

    * ``"skip"`` (the loop, the webhook, the manual sync and the resync):
      return ``SYNC_SKIPPED_BUSY`` at once, log ``sync.skipped_busy``, and
      mark the mailbox "rerun requested". The manual sync and the resync then
      answer 409.
    * ``"wait"`` (the default: the deep downloads of cleanup and Process
      past): wait up to ``SYNC_LOCK_WAIT_SECS``, then run. After the bound,
      log ``sync.busy_wait_timeout`` and return ``SYNC_SKIPPED_BUSY``.

    The holder clears the rerun mark when its cycle starts, because that
    cycle fetches after each earlier skip. A skip during the cycle sets the
    mark again. When the cycle ends with no error and no cancel, and no
    caller waits, the holder runs ONE more shallow cycle (``_rerun_once``).
    The rerun keeps the tenant binding of the holder.

    ``purge`` and ``reset_cursor`` (the resync) run in phase (a) of the
    cycle, under the lock, so a running tick cannot write the cursor back.

    No session is open while a caller waits, because the cycle opens its own
    sessions after the lock. The lock is released on every exit of the cycle,
    a cancel included, and a cancelled waiter leaves with no lock.
    """
    if if_busy not in ("wait", "skip"):
        raise ValueError(f"if_busy must be 'wait' or 'skip', not {if_busy!r}")
    key = _lock_key(account_id)
    if if_busy == "skip" and sync_busy(key):
        _sync_rerun.add(key)
        logger.info("sync.skipped_busy account_id=%s rerun=requested",
                    account_id)
        return dict(SYNC_SKIPPED_BUSY)

    lock = _sync_locks.setdefault(key, asyncio.Lock())
    _sync_lock_users[key] = _sync_lock_users.get(key, 0) + 1
    try:
        try:
            async with asyncio.timeout(SYNC_LOCK_WAIT_SECS):
                await lock.acquire()
        except TimeoutError:
            logger.warning("sync.busy_wait_timeout account_id=%s waited=%s",
                           account_id, SYNC_LOCK_WAIT_SECS)
            return dict(SYNC_SKIPPED_BUSY)
        try:
            # This cycle fetches after each skip made before it starts.
            _sync_rerun.discard(key)
            result = await _sync_cycle(
                account_id, organization_id=organization_id,
                deep=deep, since=since, purge=purge, reset_cursor=reset_cursor)
            return await _rerun_once(key, account_id, organization_id, result)
        finally:
            lock.release()
    finally:
        _leave_sync_lock(key)


async def _rerun_once(
    key: str, account_id: str, organization_id: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Run ONE more shallow cycle when a caller skipped during this one.

    The holder still holds the lock. Nothing runs when no skip came, when a
    caller waits (the cycle of that caller fetches the mail), or when this
    cycle failed. A failed rerun is logged and keeps the result of the
    holder. A merged result adds the two ``synced`` counts, so the new-mail
    pipeline of the caller also sees the mail of the rerun.
    """
    if key not in _sync_rerun or _sync_lock_users.get(key, 0) > 1:
        return result
    _sync_rerun.discard(key)
    if not isinstance(result, dict) or result.get("error"):
        return result
    logger.info("sync.rerun account_id=%s", account_id)
    try:
        again = await _sync_cycle(
            account_id, organization_id=organization_id, deep=False)
    except Exception as exc:
        logger.warning("sync.rerun_failed account_id=%s error=%s",
                       account_id, type(exc).__name__)
        return result
    if not isinstance(again, dict) or again.get("error"):
        logger.warning("sync.rerun_failed account_id=%s error=%s",
                       account_id, str((again or {}).get("error"))[:160])
        return result
    merged = dict(result)
    merged["synced"] = (int(result.get("synced") or 0)
                        + int(again.get("synced") or 0))
    merged["reran"] = True
    return merged


async def _apply_resync(
    db: Any, account_id: str, cursor: Any, *, purge: bool, reset_cursor: bool,
) -> Any:
    """The purge and the cursor reset of a resync, inside phase (a).

    The caller passes the open block of phase (a), so both writes run under
    the mailbox lock. Returns the cursor that the fetch uses: ``None`` after
    a reset, else the stored one."""
    if purge:
        await db.execute(
            text("DELETE FROM email_messages WHERE account_id = :id"),
            {"id": account_id},
        )
    if reset_cursor:
        await db.execute(
            text(
                """UPDATE email_accounts
                   SET last_history_id = NULL, updated_at = now()
                   WHERE id = :id"""
            ),
            {"id": account_id},
        )
        return None
    return cursor


async def _sync_cycle(
    account_id: str, *, organization_id: str | None = None,
    deep: bool | None = None, since: datetime | None = None,
    purge: bool = False, reset_cursor: bool = False,
) -> dict[str, Any]:
    """Run a full sync cycle for a single account.  Returns sync summary.

    Call ``_sync_account``, never this, so one cycle runs for each mailbox at
    a time (EM-T4f part 2).

    ``purge`` deletes the local messages of the account, and
    ``reset_cursor`` clears ``last_history_id``. Both run in phase (a), after
    the row read, so the resync applies them under the mailbox lock.

    This is the same logic as POST /email/sync but usable from background tasks.

    ``organization_id`` or the bound tenant (``current_tenant()``) names the
    organization. With neither, it raises ``TenantUnbound`` before any write.
    The work runs in phases, and each phase is its own ``tenant_session(org)``
    with no ``commit()`` (EM-T1b-1 item 6):

    (a) read the account, set ``syncing``, write the ``email_sync_log`` row;
    (b) authenticate and fetch from the provider with NO session open — a
        rotated token is persisted in a short session between the two calls.
        The first import, or the deep sync of a member act, runs here in
        batches (``_import_in_batches``, EM-T6b): it fetches each batch with
        NO session open, and one block writes the batch and its progress.
        The recurring sweep then fetches with NO session open;
    (c) drop each message below the floor that is not stored yet
        (``_drop_below_floor``), persist the messages, then the reconcile;
    (d) the credentials, account and sync-log rows;
    (e) the body backfill (``_backfill_bodies``): read the empty-body
        candidates, fetch each body with NO session open, and write the
        bodies in a second block. A 401 on a body fetch refreshes the token
        AFTER phase (d) wrote the credentials. So a short block after this
        phase writes them again, only when they changed since phase (d)
        (EM-T4c);
    (f) the embeddings (``_embed_messages``): read the pending messages,
        call the model with NO session open, and write the vectors in a
        second block. It makes no provider call.

    The error path writes in a new ``tenant_session(org)``. When a refresh
    during the sync changed the credentials, it writes them in that block too
    (EM-T4c), so a later failure cannot lose a rotated refresh token. The
    write after phase (e) never reaches the error path: it tries a failed
    write once more in a new block, logs the result, and the sync keeps its
    success.

    ``deep=None`` runs the first import while ``initial_sync_done`` is false.
    ``deep=True`` forces the deep sync of a member act, which writes no
    progress. ``since`` is the explicit floor of a member act
    (Process past emails, Clean older mail). Every sync passes a floor to the
    provider, deep or shallow (WS-17 EM-T6a, ``import_window.sync_floor``).
    With no ``since``, the floor is ``import_since``, the range that the member
    chose. The ceiling of 180 days binds every floor (D-EM-10).
    """
    org = organization_id or current_tenant()
    if not org:
        raise TenantUnbound(
            f"_sync_account({account_id}) has no organization — pass "
            "organization_id or bind a tenant (EM-T1b-1)"
        )

    sync_log_id: Any = None
    # The error path reads these two, to keep the credentials that a refresh
    # during the sync rotated (EM-T4c).
    provider: Any = None
    store: Any = None
    try:
        # ── (a) the account row, the 'syncing' status and the log row ──────
        async with tenant_session(org) as db:
            row = (await db.execute(
                text(
                    """SELECT id, provider, credentials_encrypted, last_history_id,
                              sync_interval_secs, initial_sync_done,
                              import_since, import_reached_at, import_count,
                              last_synced_at, created_at,
                              clock_timestamp() AS db_now
                       FROM email_accounts
                       WHERE id = :id"""
                ),
                {"id": account_id},
            )).fetchone()
            if not row:
                # ``gone`` tells the loop to stop (EM-T4f part 2).
                return dict(ACCOUNT_GONE)

            # The resync (EM-T4f fix round 2): the purge and the cursor reset
            # run here, under the mailbox lock, so a tick that ran before it
            # cannot write the old cursor back.
            history_id = await _apply_resync(
                db, account_id, row.last_history_id,
                purge=purge, reset_cursor=reset_cursor)

            await db.execute(
                text(
                    """UPDATE email_accounts
                       SET sync_status = 'syncing', updated_at = now()
                       WHERE id = :id"""
                ),
                {"id": account_id},
            )
            sync_log_id = (await db.execute(
                text(
                    """INSERT INTO email_sync_log (account_id, started_at, status)
                       VALUES (:id, now(), 'running')
                       RETURNING id"""
                ),
                {"id": account_id},
            )).fetchone().id

        provider_name = row.provider

        # ── (b) the provider, with no session open ──────────────────────────
        from acb_llm.key_store import get_key_store
        store = get_key_store()
        creds = json.loads(store.decrypt(row.credentials_encrypted))

        # Instantiate provider (raises ValueError for an unknown provider,
        # surfaced as a sync failure by the outer handler).
        provider = build_provider(provider_name, creds)

        if not await provider.authenticate():
            raise RuntimeError("Provider authentication failed")

        # Persist a rotated refresh token IMMEDIATELY after auth — Microsoft
        # rotates it on refresh, and if a later sync step fails before the
        # end-of-sync persist, the new token would be lost and the account
        # would need a manual reconnect.
        if provider.credentials_dirty():
            async with tenant_session(org) as db:
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET credentials_encrypted = :creds, updated_at = now()
                           WHERE id = :id"""
                    ),
                    {
                        "id": account_id,
                        "creds": store.encrypt(
                            json.dumps(provider.export_credentials())
                        ),
                    },
                )

        # The floor binds EVERY sync, deep or shallow (EM-T6a items 3 and 4).
        # An explicit ``since`` is a member act. Without one, the range that
        # the member chose binds. The ceiling of 180 days binds both.
        floor = import_window.sync_floor(
            since=since, import_since=getattr(row, "import_since", None))

        # The first import, or the deep sync of a member act, runs in batches
        # newest first, and only the first import writes progress (EM-T6b
        # items 4 to 8). A caller forces a deep sync with ``deep=True``.
        first_import = deep is None and not getattr(row, "initial_sync_done", False)
        imported, import_error = 0, None
        if first_import or deep:
            # A failed import does not raise. Phase (d) records its error,
            # and the next cycle resumes it (fix round 2).
            imported, import_error = await _import_in_batches(
                org, account_id, provider, row, floor=floor,
                progress=first_import)

        # The recurring sweep runs in every call, after an import too, and
        # after a failed import, so new mail always syncs (owner answer Q2,
        # spec §10.2). It reads past its newest pages back to the catch-up
        # watermark after a pause (EM-T6b item 9, D-EM-13). When a page fails
        # short of the watermark, the sweep keeps what it read and sets
        # ``catch_up_incomplete``. Phase (d) then writes that mail and keeps
        # ``last_synced_at``, so the next cycle reads the pause again.
        sync_result = await provider.sync_messages(
            history_id=history_id,
            max_results=100,
            deep=False,
            since=floor,
            catch_up=_catch_up_watermark(row),
        )
        # Fix round 3: whether phase (d) keeps the watermark, and the note
        # of a short catch-up (``_watermark_outcome``).
        keep_watermark, sync_note = _watermark_outcome(
            account_id, sync_result, row, import_error)

        # Capture pre-upsert categories so the post-sync learner can detect
        # label changes the USER made in their mail client — the upsert
        # overwrites categories on a categories-authoritative provider
        # (Outlook), destroying the "before". The learner and the reconcile
        # stay on the recurring sweep. An import batch runs neither, because
        # a backfill replays history and would mislearn (EM-T6b item 10).
        learn_labels = hooks.learn_label_changes is not None

        # ── (c) persist the messages ────────────────────────────────────────
        async with tenant_session(org) as db:
            # The backstop of the floor, for a provider that ignores ``since``.
            # The reconcile below then reads the same list.
            sync_result.messages, label_changes = await _write_messages(
                db, account_id, sync_result.messages, floor,
                learn_labels=learn_labels)
        persisted_count = imported + len(sync_result.messages)

        # Revive label-learning on the scheduler path (email item 2.1): the
        # gateway-registered hook learns FROM-classification patterns from
        # the manual label changes captured above. Best-effort — a learning
        # failure never fails the sync. The hook opens its own session.
        try:
            await run_label_learn_hook(
                hooks.learn_label_changes, account_id, label_changes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.label_learn_failed account=%s err=%s",
                           account_id, str(exc)[:160])

        # Reconcile provider-side deletions on a full snapshot (Outlook):
        # trash local messages that vanished from the mailbox entirely. Its
        # own phase, so a failure rolls back only the reconcile.
        try:
            async with tenant_session(org) as db:
                removed = await reconcile_full_snapshot(db, account_id, sync_result)
            if removed:
                logger.info("sync.reconciled_deletions account=%s removed=%d",
                            account_id, removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.reconcile_failed account=%s err=%s",
                           account_id, str(exc)[:160])

        # ── (d) the credentials, account and log rows ───────────────────────
        # Persist refreshed OAuth tokens if the provider rotated them, so the
        # next sync cycle doesn't reuse a stale (and soon-invalid) token.
        # ``written`` is what this phase writes, so the write after phase (e)
        # can tell a later refresh from this one (EM-T4c).
        written = _credentials_json(provider)
        async with tenant_session(org) as db:
            if written is not None:
                await db.execute(_WRITE_CREDENTIALS,
                                 {"id": account_id,
                                  "creds": store.encrypt(written)})

            # Update account sync state. The end of the first import writes
            # ``initial_sync_done``, and a deep sync of a member act never
            # does (EM-T6b items 6 and 8). A failed import records its error
            # here, after the new mail landed. A catch-up that stopped short
            # keeps ``last_synced_at`` and writes its note (fix rounds 2, 3).
            await db.execute(
                text(
                    """UPDATE email_accounts
                       SET sync_status = 'idle',
                           last_synced_at = CASE
                               WHEN CAST(:keep_watermark AS boolean)
                               THEN last_synced_at ELSE now() END,
                           last_history_id = COALESCE(
                               :history_id, last_history_id),
                           sync_error = CAST(:sync_note AS text),
                           updated_at = now()
                       WHERE id = :id"""
                ),
                {"id": account_id, "history_id": sync_result.new_history_id,
                 "keep_watermark": keep_watermark, "sync_note": sync_note},
            )

            # Mark sync log success
            await db.execute(
                text(
                    """UPDATE email_sync_log
                       SET status = 'success', completed_at = now(),
                           messages_synced = :synced, messages_skipped = :skipped,
                           provider_history_id = :history_id
                       WHERE id = :log_id"""
                ),
                {
                    "log_id": sync_log_id,
                    "synced": persisted_count,
                    "skipped": 0,
                    "history_id": sync_result.new_history_id,
                },
            )
            # A failed import records its error after the new mail landed.
            await _record_import_error(db, account_id, sync_log_id, import_error)

        # ── (e) drain a bounded slice of the empty-body backlog ─────────────
        # So full-text search can match on the body of messages the user
        # hasn't opened (Outlook syncs headers-only). Best-effort and bounded
        # — never fails or stalls the sync; the backlog empties over
        # successive ticks. No session is open across the provider calls.
        try:
            await _backfill_bodies(org, account_id, provider)
        except Exception as exc:
            logger.warning("sync.body_backfill_failed account=%s err=%s",
                           account_id, str(exc)[:160])
        # A 401 in phase (e) may have rotated the tokens after phase (d)
        # wrote them (EM-T4c). It never raises, so the sync keeps its success.
        await _write_credentials_refreshed_since(
            org, account_id, provider, store, written)

        # ── (f) semantic search: embed a bounded batch ──────────────────────
        # No-op unless email_semantic_search_enabled. Best-effort; never fails
        # the sync. No session is open across the model call.
        try:
            await _embed_messages(org, account_id)
        except Exception as exc:
            logger.warning("sync.email_embed_failed account=%s err=%s",
                           account_id, str(exc)[:160])

        logger.info(
            "sync.account_done account_id=%s provider=%s synced=%s",
            account_id, provider_name, persisted_count,
        )
        return _cycle_result(persisted_count, sync_result.new_history_id,
                             import_error, sync_note)

    except Exception as exc:
        logger.warning(
            "sync.account_failed account_id=%s error=%s",
            account_id, str(exc),
        )

        # A refresh during the sync may have rotated the tokens (EM-T4c).
        # Encrypt them BEFORE the block opens, so a failure here cannot roll
        # back the error status below.
        creds_blob = _dirty_credentials(provider, store)

        # Mark account as error, in a NEW session: the phase that failed has
        # already rolled back.
        try:
            async with tenant_session(org) as db:
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET sync_status = 'error', sync_error = :error,
                               updated_at = now()
                           WHERE id = :id"""
                    ),
                    {"id": account_id, "error": str(exc)},
                )
                if sync_log_id is not None:
                    await db.execute(
                        text(
                            """UPDATE email_sync_log
                               SET status = 'error', completed_at = now(),
                                   error_message = :error
                               WHERE id = :log_id"""
                        ),
                        {"log_id": sync_log_id, "error": str(exc)},
                    )
                # Keep the rotated tokens. Microsoft revokes the old refresh
                # token on use, so losing the new one forces a reconnect.
                if creds_blob is not None:
                    await db.execute(_WRITE_CREDENTIALS,
                                     {"id": account_id, "creds": creds_blob})
        except Exception:
            pass

        return {"error": str(exc)}


# -- Per-account sync loop ----------------------------------------------------


def _forget_this_loop(account_id: str) -> None:
    """Drop the entry of *account_id* only when it is the calling task.

    No ``await`` and no ``_scheduler_lock``: ``remove_account_sync`` holds that
    lock while it waits for this task, so taking it here could hang both.
    """
    if _scheduler_tasks.get(account_id) is asyncio.current_task():
        _scheduler_tasks.pop(account_id, None)


async def _account_sync_loop(
    account_id: str, interval_secs: int, *, organization_id: str,
) -> None:
    """Run sync in a loop for a single account forever.

    ⚠️ It binds ``organization_id`` at the top and releases it in ``finally``.
    The binding REPLACES any tenant that the task inherited from a request
    (H4 forbids a forever-loop on a request context). Every hook below then
    runs bound, so ``_ensure_subscription`` can create the Graph push
    subscription again (§10.4.1 risk R-3).

    EM-T4f part 2. A tick skips when another sync holds the mailbox
    (``if_busy="skip"``): the skip is a success with nothing synced, so it
    adds no backoff. When the account row is gone, the loop stops. It removes
    its entry from ``_scheduler_tasks`` only while that entry is this task,
    so it never drops a loop that ``refresh_account_sync`` started after it.
    """
    token = bind_tenant(organization_id)
    try:
        logger.info(
            "sync.loop_started account_id=%s interval=%s",
            account_id, interval_secs,
        )
        backoff_secs = 0  # 0 = healthy, sleep the normal interval; >0 = failing
        while True:
            sync_failed = False
            try:
                result = await _sync_account(
                    account_id, organization_id=organization_id,
                    if_busy="skip")
                if isinstance(result, dict) and result.get("gone"):
                    _forget_this_loop(account_id)
                    logger.info("sync.loop_row_gone account_id=%s", account_id)
                    return
                # _sync_account returns {"error": ...} on a handled failure
                # (auth, provider) rather than raising, so a bad sync is a dict
                # with an "error" key — not an exception. Treat both as failure
                # for backoff.
                # A short catch-up is a soft failure: it backs off too (fix
                # round 3), and its new mail still goes through the pipeline.
                sync_failed = (not isinstance(result, dict)) or ("error" in result)                     or bool(result.get("catch_up_incomplete"))
                new_mail = isinstance(result, dict) and result.get("synced", 0)
                # Process new mail through the shared pipeline — auto-run
                # rules, categorize senders, classify threads (Reply Zero),
                # auto-archive. The gateway registers this hook; it isolates
                # each step's failures internally. The SAME pipeline is
                # enqueued by the manual-sync route and the webhook (H1) so
                # mail is processed identically however it arrived.
                if new_mail:
                    try:
                        await run_hook(hooks.on_new_mail, account_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "sync.process_new_mail_failed account_id=%s error=%s",
                            account_id, str(exc),
                        )
                # Reply Zero classification runs EVERY cycle, not only when
                # new mail landed. It works a backlog — threads older than the
                # rules, or ones a capped earlier cycle didn't reach — so
                # gating it on new mail left a quiet mailbox permanently
                # behind. Measured on a live account: 295 of 3,487 threads had
                # a status. Cheap when idle: the selection query returns no
                # rows and the hook does nothing.
                try:
                    await run_hook(hooks.classify_threads, account_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sync.classify_threads_failed account_id=%s error=%s",
                        account_id, str(exc),
                    )
                # Send a scheduled digest if one is due (opt-in per account).
                try:
                    await run_hook(hooks.send_digest, account_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sync.digest_check_failed account_id=%s error=%s",
                        account_id, str(exc),
                    )
                # Label / nudge threads waiting too long for a reply (opt-in).
                try:
                    await run_hook(hooks.send_follow_up_reminders, account_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sync.follow_up_check_failed account_id=%s error=%s",
                        account_id, str(exc),
                    )
                # Ensure a Graph push subscription exists / is renewed so new
                # mail is processed in near real time (polling stays as a
                # fallback).
                try:
                    await run_hook(hooks.ensure_subscription, account_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sync.subscription_check_failed account_id=%s error=%s",
                        account_id, str(exc),
                    )
            except Exception as exc:
                sync_failed = True
                logger.warning(
                    "sync.loop_iteration_failed account_id=%s error=%s",
                    account_id, str(exc),
                )

            # Re-read interval in case it was changed via PATCH
            try:
                current_interval = await _get_account_sync_interval(
                    account_id, organization_id)
                if current_interval:
                    interval_secs = current_interval
            except Exception:
                pass

            # Exponential backoff on failure (double each time, capped ~1h);
            # reset to the normal interval the moment a sync succeeds. Stops a
            # revoked account from being hammered every interval forever.
            backoff_secs = _next_backoff(
                backoff_secs, interval_secs, failed=sync_failed)
            if backoff_secs:
                logger.info("sync.backoff account_id=%s next_in=%s",
                            account_id, backoff_secs)
            await asyncio.sleep(backoff_secs or interval_secs)
    finally:
        release_tenant(token)


async def _get_account_sync_interval(
    account_id: str, organization_id: str,
) -> int | None:
    """Read the current sync_interval_secs for an account, in its tenant."""
    async with tenant_session(organization_id) as db:
        row = (await db.execute(
            text("SELECT sync_interval_secs FROM email_accounts WHERE id = :id"),
            {"id": account_id},
        )).fetchone()
    return row.sync_interval_secs if row else None


# -- Scheduler lifecycle ------------------------------------------------------


async def start_background_sync() -> dict[str, int]:
    """Start background sync for all enabled email accounts.

    Called from the gateway lifespan on startup.  Returns {account_id: interval_secs}
    for all launched sync loops.

    WS-29 launch-defang kill-switch. The gate lives HERE, inside the start
    function — never as an ``if`` at the gateway call site, so the flag has
    exactly one reader (two places that both have to agree about what the flag
    means is how a loop ends up running with the flag off). Default ON;
    ``EMAIL_SYNC_ENABLED`` false stops the loop cleanly, returns an empty dict
    and opens no session.

    The sweep binds per organization (EM-T1b-1 item 2). One unbound read lists
    the RLS-EXEMPT ``organization`` table. For each organization, one
    ``tenant_session(org)`` closes the orphaned syncs and lists the accounts
    with ``sync_enabled``. Each loop starts with its own organization.

    ⚠️ Every per-org statement ALSO filters on ``organization_id = :org``.
    Under FORCE RLS the filter is redundant (defence in depth). On a catalog
    without RLS, each per-org read would otherwise see every row, and an
    account could bind an organization that does not own it. The filter makes
    the binding correct on every catalog. A failure in one organization is
    logged as ``email.sync_sweep_org_failed`` and the sweep goes on.
    """
    global _scheduler_running

    if os.getenv("EMAIL_SYNC_ENABLED", "").strip().lower() in {
        "0", "false", "no", "off",
    }:
        logger.info("sync.email_sync_disabled")
        return {}

    async with _scheduler_lock:
        if _scheduler_running:
            logger.info("sync.scheduler_already_running")
            return {}

        accounts: list[tuple[str, int | None, str]] = []
        for org in await _list_organizations():
            try:
                org_accounts = await _sweep_one_org(org)
            except Exception as exc:
                # One organization's failure must not stop the others.
                logger.warning(
                    "email.sync_sweep_org_failed org=%s error=%s",
                    org, str(exc)[:200],
                )
                continue
            for account_id, interval in org_accounts:
                accounts.append((account_id, interval, org))

        launched: dict[str, int] = {}
        for account_id, interval, org in accounts:
            interval = interval or 300
            task = asyncio.create_task(
                _account_sync_loop(account_id, interval, organization_id=org))
            _scheduler_tasks[account_id] = task
            launched[account_id] = interval

        _scheduler_running = True
        logger.info("sync.scheduler_started accounts=%s", len(launched))
        return launched


async def stop_background_sync() -> None:
    """Stop all background sync tasks gracefully.

    Called from the gateway lifespan on shutdown.
    """
    global _scheduler_running

    async with _scheduler_lock:
        _scheduler_running = False
        tasks = list(_scheduler_tasks.values())
        _scheduler_tasks.clear()

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("sync.scheduler_stopped tasks_cancelled=%s", len(tasks))


async def refresh_account_sync(
    account_id: str, organization_id: str | None = None,
) -> None:
    """Start or restart the sync loop for a single account.

    Call this after creating a new account or toggling sync_enabled on, and
    AFTER the write has committed, so the interval read can see the row.

    ``organization_id`` is required. The OAuth callback passes the organization
    of the verified state, and the account routes pass the organization of the
    authenticated ``UserContext``. A missing organization raises
    ``TenantUnbound``. It is never defaulted.
    """
    if not organization_id:
        raise TenantUnbound(
            f"refresh_account_sync({account_id}) has no organization (EM-T1b-1)"
        )
    async with _scheduler_lock:
        # Cancel existing task if any
        existing = _scheduler_tasks.pop(account_id, None)
        if existing:
            existing.cancel()
            try:
                await existing
            except asyncio.CancelledError:
                pass

        # Read current config
        interval = await _get_account_sync_interval(account_id, organization_id)
        if interval is None:
            logger.warning(
                "sync.refresh_account_not_found account_id=%s", account_id
            )
            return

        task = asyncio.create_task(_account_sync_loop(
            account_id, interval, organization_id=organization_id))
        _scheduler_tasks[account_id] = task
        logger.info(
            "sync.loop_refreshed account_id=%s interval=%s",
            account_id, interval,
        )


async def remove_account_sync(account_id: str) -> None:
    """Stop the sync loop for a single account.

    Call this after deleting an account or toggling sync_enabled off.
    """
    async with _scheduler_lock:
        task = _scheduler_tasks.pop(account_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("sync.loop_removed account_id=%s", account_id)


def get_scheduler_status() -> dict[str, Any]:
    """Return current scheduler state (for health checks / debug)."""
    return {
        "running": _scheduler_running,
        "accounts": list(_scheduler_tasks.keys()),
        "count": len(_scheduler_tasks),
    }
