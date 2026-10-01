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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
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
    pre-existing in-flight row is by definition orphaned.

    The caller hands in a ``tenant_session(org)``, so under FORCE RLS this
    closes the rows of that one organization. The startup sweep calls it once
    per organization."""
    await db.execute(text(
        "UPDATE email_sync_log SET status = 'error', "
        "error_message = 'interrupted by scheduler restart', "
        "completed_at = now() WHERE status = 'running'"))
    await db.execute(text(
        "UPDATE email_accounts SET sync_status = 'idle', "
        "updated_at = now() WHERE sync_status = 'syncing'"))


async def _list_organizations() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of the scheduler: it answers "which tenants exist",
    so it cannot run inside one. Same shape as ``routes/tasks/calendar.py``
    ``_run_rollover_sweep``.
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


# -- Core sync logic (shared with manual /email/sync endpoint) ---------------


async def _sync_account(
    account_id: str, *, organization_id: str | None = None,
    deep: bool | None = None, since: datetime | None = None,
) -> dict[str, Any]:
    """Run a full sync cycle for a single account.  Returns sync summary.

    This is the same logic as POST /email/sync but usable from background tasks.

    ``organization_id`` or the bound tenant (``current_tenant()``) names the
    organization. With neither, it raises ``TenantUnbound`` before any write.
    The work runs in phases, and each phase is its own ``tenant_session(org)``
    with no ``commit()`` (EM-T1b-1 item 6):

    (a) read the account, set ``syncing``, write the ``email_sync_log`` row;
    (b) authenticate and fetch from the provider with NO session open — a
        rotated token is persisted in a short session between the two calls;
    (c) persist the messages, then the reconcile;
    (d) the final credentials, account and sync-log rows;
    (e) ``backfill_missing_bodies``;
    (f) ``embed_pending_messages``.

    The error path writes in a new ``tenant_session(org)``.

    ``deep``/``since`` override the automatic (first-sync) heuristic so a caller
    can force a deep backfill from an arbitrary date floor — e.g. historical
    rule-apply downloading a past date range before running rules over it. Both
    default to None, which preserves the normal first-sync-deep / then-incremental
    behaviour exactly.
    """
    org = organization_id or current_tenant()
    if not org:
        raise TenantUnbound(
            f"_sync_account({account_id}) has no organization — pass "
            "organization_id or bind a tenant (EM-T1b-1)"
        )

    sync_log_id: Any = None
    try:
        # ── (a) the account row, the 'syncing' status and the log row ──────
        async with tenant_session(org) as db:
            row = (await db.execute(
                text(
                    """SELECT id, provider, credentials_encrypted, last_history_id,
                              sync_interval_secs, initial_sync_done
                       FROM email_accounts
                       WHERE id = :id"""
                ),
                {"id": account_id},
            )).fetchone()
            if not row:
                return {"error": "Account not found"}

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

        # ── (c) persist the messages ────────────────────────────────────────
        persisted_count = 0
        async with tenant_session(org) as db:
            for msg in sync_result.messages:
                if msg.subject == "[DELETED]":
                    await db.execute(
                        text(
                            """UPDATE email_messages
                               SET folder = 'TRASH', updated_at = now()
                               WHERE account_id = :account_id
                                 AND provider_message_id = :provider_id"""
                        ),
                        {"account_id": account_id,
                         "provider_id": msg.provider_message_id},
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

        # ── (d) the final credentials, account and log rows ─────────────────
        async with tenant_session(org) as db:
            # Persist refreshed OAuth tokens if the provider rotated them, so
            # the next sync cycle doesn't reuse a stale (and soon-invalid)
            # token.
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

        # ── (e) drain a bounded slice of the empty-body backlog ─────────────
        # So full-text search can match on the body of messages the user
        # hasn't opened (Outlook syncs headers-only). Best-effort and bounded
        # — never fails or stalls the sync; the backlog empties over
        # successive ticks. The helper commits at its END, after its last
        # statement, so the tenant binding covers every write it makes.
        try:
            from email_ingestion.body_backfill import backfill_missing_bodies
            async with tenant_session(org) as db:
                await backfill_missing_bodies(db, account_id, provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync.body_backfill_failed account=%s err=%s",
                           account_id, str(exc)[:160])

        # ── (f) semantic search: embed a bounded batch ──────────────────────
        # No-op unless email_semantic_search_enabled. Best-effort; never fails
        # the sync. The helper also commits only at its end.
        try:
            from email_ingestion.email_embeddings import embed_pending_messages
            async with tenant_session(org) as db:
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
        except Exception:
            pass

        return {"error": str(exc)}


# -- Per-account sync loop ----------------------------------------------------


async def _account_sync_loop(
    account_id: str, interval_secs: int, *, organization_id: str,
) -> None:
    """Run sync in a loop for a single account forever.

    ⚠️ It binds ``organization_id`` at the top and releases it in ``finally``.
    The binding REPLACES any tenant that the task inherited from a request
    (H4 forbids a forever-loop on a request context). Every hook below then
    runs bound, so ``_ensure_subscription`` can create the Graph push
    subscription again (§10.4.1 risk R-3).
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
                    account_id, organization_id=organization_id)
                # _sync_account returns {"error": ...} on a handled failure
                # (auth, provider) rather than raising, so a bad sync is a dict
                # with an "error" key — not an exception. Treat both as failure
                # for backoff.
                sync_failed = (not isinstance(result, dict)) or ("error" in result)
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
    with ``sync_enabled``. Each loop starts with its own organization. Deduped
    by account id (first organization wins) so a catalog without FORCE RLS,
    where each per-org read sees every row, still starts one loop per account.
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

        seen: set[str] = set()
        accounts: list[tuple[str, int | None, str]] = []
        for org in await _list_organizations():
            async with tenant_session(org) as db:
                await _close_orphaned_syncs(db)
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
                org_accounts = [(str(row.id), row.sync_interval_secs)
                                for row in result.fetchall()]
            for account_id, interval in org_accounts:
                if account_id in seen:
                    continue
                seen.add(account_id)
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
