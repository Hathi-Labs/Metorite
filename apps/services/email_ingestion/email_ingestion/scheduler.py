"""Background email sync scheduler.

Periodically syncs all enabled email accounts using their configured
sync_interval_secs (default 300s).  Runs as a set of asyncio tasks managed
by the gateway lifespan.

Architecture:
- On startup: query email_accounts WHERE sync_enabled = true
- For each account: launch an asyncio task that calls _sync_account() in a loop
- On shutdown: cancel all tasks, wait for in-flight syncs to finish
- Accounts added/removed at runtime via /email accounts endpoints also
  refresh the task set via the registry pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from email_ingestion.persist import upsert_message
from email_ingestion.post_sync import hooks, run_hook, run_label_learn_hook
from email_ingestion.providers.factory import build_provider
from email_ingestion.reconcile import reconcile_full_snapshot

# Deep initial-sync history window (days). Older mail is pulled lazily via the
# /backfill endpoint.
INITIAL_SYNC_DAYS = 365

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


def _get_db_url() -> str:
    """Get the asyncpg database URL from environment."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # Convert sync psycopg URL to async asyncpg if needed
        if "postgresql+psycopg" in db_url:
            db_url = db_url.replace("postgresql+psycopg", "postgresql+asyncpg")
        elif db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
        return db_url
    try:
        from acb_common.settings import get_settings
        settings = get_settings()
        raw = settings.database_url
        if "postgresql+psycopg" in raw:
            return raw.replace("postgresql+psycopg", "postgresql+asyncpg")
        if raw.startswith("postgresql://"):
            return raw.replace("postgresql://", "postgresql+asyncpg://")
        return raw
    except Exception:
        raise RuntimeError(
            "DATABASE_URL env var or Postgres settings are required "
            "for email sync scheduler"
        )


def _connect_timeout() -> int:
    """Seconds to bound the asyncpg CONNECT phase so a slow/unreachable DB
    fails fast instead of stalling a sync tick (see settings.db_connect_timeout)."""
    try:
        from acb_common.settings import get_settings
        return get_settings().db_connect_timeout
    except Exception:
        return 10


def _connect_args() -> dict:
    """Connect args for every engine in this service.

    :func:`_connect_timeout` bounds getting IN; the server-side
    ``idle_in_transaction_session_timeout`` bounds staying in. A sync tick holds
    an open session across provider calls, so a tick that wedges would otherwise
    hold its row locks for as long as the process lives — which on 2026-08-06
    (gateway side, same shape) queued a migration's ALTER TABLE behind it and
    took the whole app down. Mirrors ``gateway.db.engine_connect_args``; the two
    are separate packages and cannot share the constant."""
    return {
        "timeout": _connect_timeout(),
        "server_settings": {
            "idle_in_transaction_session_timeout": "600000",  # 10 min
        },
    }


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


async def _close_orphaned_syncs(db: Any) -> None:
    """Close sync-log rows (and account statuses) left mid-flight by a process
    that crashed or restarted during a sync. Nothing completes them once that
    process is gone, so they linger 'running'/'syncing' forever and lie to any
    "is a sync in progress?" check. A fresh scheduler owns every sync now, so any
    pre-existing in-flight row is by definition orphaned."""
    await db.execute(text(
        "UPDATE email_sync_log SET status = 'error', "
        "error_message = 'interrupted by scheduler restart', "
        "completed_at = now() WHERE status = 'running'"))
    await db.execute(text(
        "UPDATE email_accounts SET sync_status = 'idle', "
        "updated_at = now() WHERE sync_status = 'syncing'"))


# -- Core sync logic (shared with manual /email/sync endpoint) ---------------


async def _sync_account(
    account_id: str, *, deep: bool | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Run a full sync cycle for a single account.  Returns sync summary.

    This is the same logic as POST /email/sync but usable from background tasks.

    ``deep``/``since`` override the automatic (first-sync) heuristic so a caller
    can force a deep backfill from an arbitrary date floor — e.g. historical
    rule-apply downloading a past date range before running rules over it. Both
    default to None, which preserves the normal first-sync-deep / then-incremental
    behaviour exactly.
    """
    db_url = _get_db_url()
    # NullPool: this engine is created per sync call and the ``engine.dispose()``
    # at the tail is unreachable (every branch below returns from inside the
    # ``async with``). With the default QueuePool that leaked one idle connection
    # per tick until GC — on a multi-account box that exhausted Postgres
    # ``max_connections``. NullPool closes each connection deterministically when
    # the session releases it, so the missing dispose no longer leaks.
    engine = create_async_engine(
        db_url, echo=False, poolclass=NullPool,
        connect_args=_connect_args(),
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT id, provider, credentials_encrypted, last_history_id,
                              sync_interval_secs, initial_sync_done
                       FROM email_accounts
                       WHERE id = :id"""
                ),
                {"id": account_id},
            )
            row = result.fetchone()
            if not row:
                return {"error": "Account not found"}

            provider_name = row.provider

            # Update sync status
            await db.execute(
                text(
                    """UPDATE email_accounts
                       SET sync_status = 'syncing', updated_at = now()
                       WHERE id = :id"""
                ),
                {"id": account_id},
            )
            await db.commit()

            # Create sync log
            sync_log_result = await db.execute(
                text(
                    """INSERT INTO email_sync_log (account_id, started_at, status)
                       VALUES (:id, now(), 'running')
                       RETURNING id"""
                ),
                {"id": account_id},
            )
            sync_log_id = sync_log_result.fetchone().id
            await db.commit()

            # Decrypt credentials
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
                await db.commit()

            # First-ever sync for this account → deep 1-year backfill across all
            # folders; afterwards stay shallow/incremental (cheap polls). A caller
            # may override to force a deep sync from a specific `since` floor
            # (historical rule-apply backfilling a past date range).
            do_deep = (
                not bool(getattr(row, "initial_sync_done", False))
                if deep is None else bool(deep)
            )
            floor = since
            if floor is None:
                floor = (
                    datetime.now(timezone.utc) - timedelta(days=INITIAL_SYNC_DAYS)
                    if do_deep else None
                )
            sync_result = await provider.sync_messages(
                history_id=row.last_history_id,
                max_results=100,
                deep=do_deep,
                since=floor,
            )

            # Capture pre-upsert categories so the post-sync learner can detect
            # label changes the USER made in their mail client — the upsert
            # overwrites categories on a categories-authoritative provider
            # (Outlook), destroying the "before". Only when a learner is wired
            # AND this is an incremental sync: a deep backfill replays history
            # and would mislearn (the same gate the manual route uses).
            learn_labels = hooks.learn_label_changes is not None and not do_deep
            label_changes: list[Any] = []

            # Persist messages
            persisted_count = 0
            for msg in sync_result.messages:
                if msg.subject == "[DELETED]":
                    await db.execute(
                        text(
                            """UPDATE email_messages
                               SET folder = 'TRASH', updated_at = now()
                               WHERE account_id = :account_id
                                 AND provider_message_id = :provider_id"""
                        ),
                        {"account_id": account_id, "provider_id": msg.provider_message_id},
                    )
                    persisted_count += 1
                else:
                    old_categories = None
                    if learn_labels:
                        ocr = (await db.execute(
                            text(
                                "SELECT categories FROM email_messages "
                                "WHERE account_id = :aid "
                                "AND provider_message_id = :pid"
                            ),
                            {"aid": account_id,
                             "pid": msg.provider_message_id},
                        )).fetchone()
                        # Existing rows only — a brand-new message has no prior
                        # categories to diff against.
                        old_categories = (
                            list(ocr.categories or []) if ocr else None)
                    # ONE shared ingest upsert (message + attachments); see
                    # email_ingestion.persist.upsert_message.
                    await upsert_message(db, account_id, msg)
                    persisted_count += 1
                    if old_categories is not None:
                        label_changes.append((msg, old_categories))

            await db.commit()

            # Revive label-learning on the scheduler path (email item 2.1): the
            # gateway-registered hook learns FROM-classification patterns from
            # the manual label changes captured above. Best-effort — a learning
            # failure never fails the sync.
            try:
                await run_label_learn_hook(
                    hooks.learn_label_changes, account_id, label_changes)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sync.label_learn_failed account=%s err=%s",
                               account_id, str(exc)[:160])

            # Reconcile provider-side deletions on a full snapshot (Outlook):
            # trash local messages that vanished from the mailbox entirely.
            try:
                removed = await reconcile_full_snapshot(db, account_id, sync_result)
                if removed:
                    await db.commit()
                    logger.info("sync.reconciled_deletions account=%s removed=%d",
                                account_id, removed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sync.reconcile_failed account=%s err=%s",
                               account_id, str(exc)[:160])

            # Persist refreshed OAuth tokens if the provider rotated them, so the
            # next sync cycle doesn't reuse a stale (and soon-invalid) token.
            if provider.credentials_dirty():
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
                await db.commit()

            # Update account sync state. Mark the one-time deep sync done so
            # subsequent polls stay shallow.
            await db.execute(
                text(
                    """UPDATE email_accounts
                       SET sync_status = 'idle', last_synced_at = now(),
                           last_history_id = COALESCE(
                               :history_id, last_history_id),
                           sync_error = NULL,
                           initial_sync_done = initial_sync_done OR :deep,
                           updated_at = now()
                       WHERE id = :id"""
                ),
                {"id": account_id, "history_id": sync_result.new_history_id,
                 "deep": do_deep},
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
            await db.commit()

            # Drain a bounded slice of the empty-body backlog so full-text search
            # can match on the body of messages the user hasn't opened (Outlook
            # syncs headers-only). Best-effort and bounded — never fails or stalls
            # the sync; the backlog empties over successive ticks.
            try:
                from email_ingestion.body_backfill import backfill_missing_bodies
                await backfill_missing_bodies(db, account_id, provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sync.body_backfill_failed account=%s err=%s",
                               account_id, str(exc)[:160])

            # Semantic search (Phase 2): embed a bounded batch of not-yet-embedded
            # messages for hybrid vector ranking. No-op unless
            # email_semantic_search_enabled. Best-effort; never fails the sync.
            try:
                from email_ingestion.email_embeddings import embed_pending_messages
                await embed_pending_messages(db, account_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sync.email_embed_failed account=%s err=%s",
                               account_id, str(exc)[:160])

            logger.info(
                "sync.account_done account_id=%s provider=%s synced=%s",
                account_id, provider_name, persisted_count,
            )
            return {"synced": persisted_count, "history_id": sync_result.new_history_id}

        except Exception as exc:
            logger.warning(
                "sync.account_failed account_id=%s error=%s",
                account_id, str(exc),
            )

            # Mark account as error
            try:
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET sync_status = 'error', sync_error = :error,
                               updated_at = now()
                           WHERE id = :id"""
                    ),
                    {"id": account_id, "error": str(exc)},
                )
                await db.execute(
                    text(
                        """UPDATE email_sync_log
                           SET status = 'error', completed_at = now(),
                               error_message = :error
                           WHERE id = :log_id"""
                    ),
                    {"log_id": sync_log_id, "error": str(exc)},
                )
                await db.commit()
            except Exception:
                pass

            return {"error": str(exc)}

    await engine.dispose()


# -- Per-account sync loop ----------------------------------------------------


async def _account_sync_loop(account_id: str, interval_secs: int) -> None:
    """Run sync in a loop for a single account forever."""
    logger.info(
        "sync.loop_started account_id=%s interval=%s",
        account_id, interval_secs,
    )
    backoff_secs = 0  # 0 = healthy, sleep the normal interval; >0 = failing
    while True:
        sync_failed = False
        try:
            result = await _sync_account(account_id)
            # _sync_account returns {"error": ...} on a handled failure (auth,
            # provider) rather than raising, so a bad sync is a dict with an
            # "error" key — not an exception. Treat both as failure for backoff.
            sync_failed = (not isinstance(result, dict)) or ("error" in result)
            new_mail = isinstance(result, dict) and result.get("synced", 0)
            # Process new mail through the shared pipeline — auto-run rules,
            # categorize senders, classify threads (Reply Zero), auto-archive.
            # The gateway registers this hook; it isolates each step's failures
            # internally. The SAME pipeline is enqueued by the manual-sync route
            # and the webhook (H1) so mail is processed identically however it
            # arrived.
            if new_mail:
                try:
                    await run_hook(hooks.on_new_mail, account_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "sync.process_new_mail_failed account_id=%s error=%s",
                        account_id, str(exc),
                    )
            # Reply Zero classification runs EVERY cycle, not only when new mail
            # landed. It works a backlog — threads older than the rules, or ones
            # a capped earlier cycle didn't reach — so gating it on new mail left
            # a quiet mailbox permanently behind. Measured on a live account:
            # 295 of 3,487 threads had a status. Cheap when idle: the selection
            # query returns no rows and the hook does nothing.
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
            # Ensure a Graph push subscription exists / is renewed so new mail
            # is processed in near real time (polling stays as a fallback).
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
            current_interval = await _get_account_sync_interval(account_id)
            if current_interval:
                interval_secs = current_interval
        except Exception:
            pass

        # Exponential backoff on failure (double each time, capped ~1h); reset
        # to the normal interval the moment a sync succeeds. Stops a revoked
        # account from being hammered every interval forever.
        backoff_secs = _next_backoff(
            backoff_secs, interval_secs, failed=sync_failed)
        if backoff_secs:
            logger.info("sync.backoff account_id=%s next_in=%s",
                        account_id, backoff_secs)
        await asyncio.sleep(backoff_secs or interval_secs)


async def _get_account_sync_interval(account_id: str) -> int | None:
    """Read the current sync_interval_secs for an account."""
    db_url = _get_db_url()
    engine = create_async_engine(
        db_url, echo=False, connect_args=_connect_args()
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await db.execute(
                text("SELECT sync_interval_secs FROM email_accounts WHERE id = :id"),
                {"id": account_id},
            )
            row = result.fetchone()
            return row.sync_interval_secs if row else None
    finally:
        await engine.dispose()


# -- Scheduler lifecycle ------------------------------------------------------


async def start_background_sync() -> dict[str, int]:
    """Start background sync for all enabled email accounts.

    Called from the gateway lifespan on startup.  Returns {account_id: interval_secs}
    for all launched sync loops.

    WS-29 launch-defang kill-switch. The per-account sync loop is out of launch
    scope and not yet tenant-bound (H4 slice 6b), so after the RLS cutover it
    would write UNBOUND under FORCE ROW LEVEL SECURITY. The gate lives HERE,
    inside the start function — never as an ``if`` at the gateway call site, so
    the flag has exactly one reader (two places that both have to agree about
    what the flag means is how a loop ends up running with the flag off).
    Default ON = byte-identical to before; the RLS-cutover runbook sets
    ``EMAIL_SYNC_ENABLED`` false to stop the loop cleanly. Returns an empty dict
    (no loops launched) when disabled.
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

        db_url = _get_db_url()
        engine = create_async_engine(
            db_url, echo=False, connect_args=_connect_args()
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as db:
                await _close_orphaned_syncs(db)
                await db.commit()

                result = await db.execute(
                    text(
                        """SELECT id, sync_interval_secs
                           FROM email_accounts
                           WHERE sync_enabled = true"""
                    )
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
                accounts = [(str(row.id), row.sync_interval_secs)
                            for row in result.fetchall()]
        finally:
            await engine.dispose()

        launched: dict[str, int] = {}
        for account_id, interval in accounts:
            interval = interval or 300
            task = asyncio.create_task(_account_sync_loop(account_id, interval))
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


async def refresh_account_sync(account_id: str) -> None:
    """Start or restart the sync loop for a single account.

    Call this after creating a new account or toggling sync_enabled on.
    """
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
        interval = await _get_account_sync_interval(account_id)
        if interval is None:
            logger.warning(
                "sync.refresh_account_not_found account_id=%s", account_id
            )
            return

        task = asyncio.create_task(_account_sync_loop(account_id, interval))
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
