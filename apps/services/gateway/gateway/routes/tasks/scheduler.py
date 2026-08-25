"""Tasks · scheduler — server-side periodic provider refresh.

Until now the GTD mirror was refreshed only on demand: the Tasks UI calls
``POST /tasks/sync`` on app-open (when ``auto_sync_on_open`` is set) or from the
per-workspace Sync button. That means the agent's picture of ClickUp
(projects, members, stages, and other people's tasks) goes stale between
visits — and an agent run that happens while the app is closed reasons over
old data.

This module adds the missing piece: a background loop per sync-enabled
``task_accounts`` row, launched from the gateway lifespan, that

  1. pulls the workspace's tasks into the ``gtd_items`` mirror
     (``sync._sync_account`` — incremental via ``last_delta_token``, members
     cache refreshed every run), and
  2. periodically re-fetches the full provider schema
     (projects/statuses/hierarchy into ``task_accounts.schema_cache`` +
     mirrored ``gtd_projects``) so the clarify pickers and the agent's
     project/stage knowledge stay current, and
  3. sleeps ``sync_interval_secs`` (re-read each cycle so a settings change
     takes effect without a restart).

It is modelled 1:1 on ``email_ingestion/scheduler.py`` — the email app's
proven per-account asyncio-loop pattern — but reuses the tasks package's own
pooled engine (``core._get_db``) and the existing per-account pull/refresh
functions instead of standing up a second engine. Read-only toward the
provider (constraint C-04 untouched: the only upstream write is the explicit
``POST /items/{id}/push``).

"Update needed" is simply ``now() - last_synced_at > sync_interval_secs`` — the
loop enforces it by construction; ``get_scheduler_status()`` exposes the live
picture for health/debug.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from acb_common import get_logger

# ✅ H4 DONE for this module (WS-29 MT-1d, `saas_multitenancy_handover.md` §H4).
# This is a BACKGROUND CONSUMER, not a request handler — every loop below runs
# from `asyncio.create_task` long after any request (and its tenant binding) is
# gone, so the runbook forbids inheriting an ambient tenant. The fix is the H4
# shape (exemplars: `crm/auto_lead` + `projects/run_lifecycle_sweep`): thread an
# EXPLICIT `organization_id` down the call chain and open `tenant_session(org)`
# per unit of DB work, REFUSING (`TenantUnbound`, never defaulting) if no org is
# resolvable. `task_accounts`/`gtd_settings` are FORCE-RLS'd under phase 4, so a
# single unbound read returns ZERO rows — the enumeration is therefore a per-org
# sweep over the RLS-EXEMPT `organization` table (readable unbound, it is the
# "which tenants exist" decision), binding `tenant_session(org)` per org.
# DARK (pre-phase-4, RLS off): additive bind-wrapping only, no schema change; the
# per-org reads are unscoped and deduped back to a byte-identical launch set.
# The last remaining unbound `get_db()` here is `broker_handlers.py`'s (a separate
# PR — dormant unless `ACTION_BROKER_ENFORCE`).
from gateway.db import TenantUnbound, current_tenant
from gateway.routes.tasks.core import _get_db, _tenant_session
from sqlalchemy import text

_log = get_logger("gateway.tasks.scheduler")


def _known_providers() -> set[str]:
    """Provider names a loop can actually sync — read from the SAME registry
    :func:`providers.build_provider` consults, never a list written here.

    ⚠️ **This is the D52 selection guard** (board WS-39 S1 repair round 1).
    Deleting ``ClickUpProvider`` emptied ``providers._CONNECTORS`` but did not
    delete the ``task_accounts`` ROWS that name it: every surviving
    ``provider='clickup'`` row is still ``sync_enabled = true``, so the
    enumeration below would launch a loop whose every cycle calls
    ``build_provider('clickup')`` → ``HTTPException(400, "Unknown provider")``
    → ``_sync_account`` records ``sync_status='error'`` → a permanent "Sync
    failed" badge on a live ``/tasks``, re-earned every 300 s, forever.

    One vocabulary, deliberately: the set is derived from the registry rather
    than hardcoded, so a connector re-added under a future decision is synced
    again with no second list to remember. Today it is EMPTY, which is the
    correct answer — no account of any provider is syncable, because nothing
    can be built to sync it (D52; ``tests/unit/test_no_task_provider_connectors.py``).
    """
    from gateway.routes.tasks.providers import connector_names

    return set(connector_names())


# Full provider-schema refresh cadence: every Nth sync cycle we re-fetch
# projects/statuses/hierarchy (heavier than the task pull, and the task pull
# already refreshes the member cache every run, so this can be less frequent).
_SCHEMA_REFRESH_EVERY_N_CYCLES = 12  # e.g. hourly at the 300s default interval
_DEFAULT_INTERVAL_SECS = 300
# Floor so a misconfigured tiny interval can't hammer the provider's rate limit.
_MIN_INTERVAL_SECS = 60

# ── Singleton state (mirrors the email scheduler) ────────────────────────────

_scheduler_tasks: dict[str, asyncio.Task] = {}   # account_id -> running loop
_scheduler_lock = asyncio.Lock()
_scheduler_running = False


# ── One sync cycle for one account ───────────────────────────────────────────

async def _run_one_cycle(account_id: str, org_id: str, *,
                         refresh_schema: bool) -> None:
    """Pull tasks for one account (and, on schema cycles, re-fetch its full
    provider schema). Each step is isolated so one failing doesn't skip the
    other; the pull itself records ``sync_status``/``sync_error`` on the row.

    ⚠️ **H4 (MT-1d):** ``org_id`` is the account's tenant, threaded from the
    enumerating sweep — never inherited from an ambient binding, never defaulted.
    Each of the up-to-three steps runs in its OWN ``tenant_session(org_id)``
    transaction: ``_sync_account`` commits internally as its last action and
    ``SET LOCAL app.tenant_id`` resets on commit, so one wrapping block would
    leave the schema/reconcile steps UNBOUND (0 rows under phase-4 RLS)."""
    if not org_id:
        raise TenantUnbound(
            "tasks.scheduler._run_one_cycle needs an explicit organization_id — "
            "a scheduled sync must bind the account's own tenant, never inherit "
            "an ambient one or default (saas_multitenancy_handover.md §H4)")

    from gateway.routes.tasks.accounts import _reconcile_people, _refresh_schema
    from gateway.routes.tasks.sync import _sync_account

    # 1) Pull tasks into the mirror. `_sync_account` commits internally as its
    #    LAST action (updating sync_status/last_synced_at/last_delta_token and
    #    refreshing the member cache), so it is the sole DB writer in this block
    #    and the wrapper's commit on clean exit is an empty no-op; the account
    #    read above it runs under the same tenant GUC. It CAN raise (bad creds /
    #    provider 401), so the block is wrapped and the failure logged — the
    #    schema-refresh step below still runs (on its own fresh transaction).
    account = None
    try:
        async with _tenant_session(org_id) as db:
            account = (await db.execute(
                text("SELECT * FROM task_accounts WHERE id = :id"),
                {"id": account_id},
            )).fetchone()
            if account is None:
                _log.info("tasks.scheduler.account_gone",
                          account_id=account_id[:12])
                return
            if not account.sync_enabled:
                return
            result = await _sync_account(db, account, full=False)
            if result.error:
                _log.warning("tasks.scheduler.pull_error",
                             account_id=account_id[:12],
                             error=result.error[:160])
            else:
                _log.info("tasks.scheduler.pulled",
                          account_id=account_id[:12], pulled=result.pulled,
                          created=result.created, updated=result.updated)
    except Exception as exc:  # defence in depth — _sync_account shouldn't raise
        _log.warning("tasks.scheduler.pull_failed",
                     account_id=account_id[:12], error=str(exc)[:160])

    if account is None:
        return  # account gone or the read itself failed — nothing to refresh

    # 2) Periodically re-fetch the full schema so projects/stages the clarify
    #    pickers (and the agent) rely on stay current. Its own tenant-bound
    #    transaction — the wrapper commits on clean exit (H2: `_refresh_schema`
    #    no longer commits itself, so a mid-block commit can't drop the GUC).
    if refresh_schema:
        try:
            async with _tenant_session(org_id) as db:
                await _refresh_schema(db, account_id, account.user_id)
            _log.info("tasks.scheduler.schema_refreshed",
                      account_id=account_id[:12])
        except Exception as exc:
            _log.warning("tasks.scheduler.schema_refresh_failed",
                         account_id=account_id[:12], error=str(exc)[:160])
        else:
            # 3) Reconcile the roster (best-effort), its own tenant transaction.
            try:
                async with _tenant_session(org_id) as db:
                    await _reconcile_people(db, account.user_id)
            except Exception as exc:  # best-effort, like the helper itself
                _log.warning("tasks.scheduler.reconcile_failed",
                             account_id=account_id[:12],
                             error=str(exc)[:160])


async def _account_sync_loop(account_id: str, org_id: str,
                             interval_secs: int) -> None:
    """Sync one account forever: pull every cycle, full schema refresh every
    Nth cycle, then sleep the (re-read) interval.

    ``org_id`` (H4) is the account's tenant, threaded in at launch and passed
    EXPLICITLY into every DB unit — the loop runs detached, so it can never
    inherit a request's binding."""
    interval_secs = max(interval_secs or _DEFAULT_INTERVAL_SECS,
                        _MIN_INTERVAL_SECS)
    _log.info("tasks.scheduler.loop_started",
              account_id=account_id[:12], interval=interval_secs)
    cycle = 0
    while True:
        try:
            # Refresh the full schema on the first cycle and every Nth after,
            # so a freshly (re)started loop primes projects/stages immediately.
            refresh_schema = (cycle % _SCHEMA_REFRESH_EVERY_N_CYCLES) == 0
            await _run_one_cycle(account_id, org_id,
                                 refresh_schema=refresh_schema)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad cycle kill the loop
            _log.warning("tasks.scheduler.cycle_failed",
                         account_id=account_id[:12], error=str(exc)[:160])
        cycle += 1

        # Re-read the interval so a settings change (or sync_enabled=false)
        # takes effect without a restart.
        interval_secs, still_enabled = await _read_interval(account_id, org_id)
        if not still_enabled:
            # Self-stop: just return. We deliberately do NOT pop
            # _scheduler_tasks here — mutating the registry outside
            # _scheduler_lock races refresh/remove/stop, and taking the lock
            # would deadlock against stop_background_sync (which holds it while
            # awaiting this task). The now-completed task entry is harmless and
            # is replaced/removed the next time refresh/remove runs for this
            # account.
            _log.info("tasks.scheduler.loop_self_stop",
                      account_id=account_id[:12])
            return
        await asyncio.sleep(interval_secs)


async def _read_interval(account_id: str, org_id: str) -> tuple[int, bool]:
    """(interval_secs, enabled) for an account, clamped to the floor. "enabled"
    is the account's sync_enabled AND the owner's background_sync toggle (a
    LEFT JOIN so a user with no settings row defaults to on). Missing account →
    (default, False) so the loop stops itself; a user turning background_sync
    off mid-run also self-stops the loop next cycle.

    ⚠️ **H4:** reads ``task_accounts``/``gtd_settings`` (both FORCE-RLS'd) inside
    ``tenant_session(org_id)`` — bound to the account's own tenant, threaded from
    the loop. Refuses (``TenantUnbound``) rather than reading on an unbound
    session (0 rows under phase-4 RLS → a spurious self-stop) or inheriting an
    ambient tenant.

    ⚠️ **"Enabled" also means SYNCABLE** (D52): an account whose provider has no
    connector in the registry answers ``False`` here, exactly as a disabled one
    does. Same predicate, same vocabulary as :func:`_enabled_accounts_by_org` —
    this is the second and last way a loop is launched (``refresh_account_sync``
    after a connect / PATCH), and it is also what makes a loop started before
    this guard existed **self-stop on its next cycle** rather than error forever.
    Deliberately silent: this runs every cycle, and the boot-time warning is
    where the one-per-account line is logged."""
    if not org_id:
        raise TenantUnbound(
            "tasks.scheduler._read_interval needs an explicit organization_id — "
            "a background loop must bind the account's own tenant, never read on "
            "an unbound session or inherit an ambient one "
            "(saas_multitenancy_handover.md §H4)")
    async with _tenant_session(org_id) as db:
        row = (await db.execute(
            text("""SELECT a.sync_interval_secs, a.sync_enabled, a.provider,
                           coalesce(s.background_sync, true) AS background_sync
                    FROM task_accounts a
               LEFT JOIN gtd_settings s ON s.user_id = a.user_id
                   WHERE a.id = :id"""),
            {"id": account_id},
        )).fetchone()
    if row is None:
        return _DEFAULT_INTERVAL_SECS, False
    interval = max(row.sync_interval_secs or _DEFAULT_INTERVAL_SECS,
                   _MIN_INTERVAL_SECS)
    syncable = str(row.provider or "") in _known_providers()
    return interval, bool(row.sync_enabled and row.background_sync and syncable)


# ── Lifecycle (start/stop/refresh/remove + status) ───────────────────────────

async def _enabled_accounts_by_org() -> dict[str, tuple[str, int]]:
    """Every sync-enabled account, mapped ``account_id -> (organization_id,
    interval_secs)``.

    ⚠️ **H4 (MT-1d) CROSS-TENANT SWEEP.** ``task_accounts``/``gtd_settings`` are
    FORCE-RLS'd, so a single unbound read returns ZERO rows under phase-4 (the
    fail-closed cliff) — the sync would silently stop for every customer. So
    enumerate organizations from the RLS-EXEMPT ``organization`` table on an
    unbound session (the one legitimately tenant-less read — it is deciding WHICH
    tenants exist), then bind ``tenant_session(org)`` per org and read that org's
    accounts under the policy.

    Deduped by ``account_id`` (first tenant wins): pre-phase-4 (RLS off, DARK)
    each per-org read is unscoped and returns every account, so the dedup keeps
    the launched set byte-identical to the old single unbound read; post-phase-4
    the per-org reads are disjoint and the dedup is a no-op.

    ⚠️ **An account whose provider is not in the registry is SKIPPED here, at
    the selection seam** (:func:`_known_providers`, D52). This is the only place
    the decision belongs: a loop launched for a retired provider cannot do
    anything but fail, and it fails *loudly on the customer's screen* — the
    error is written to ``task_accounts.sync_status``/``sync_error`` by
    ``_sync_account`` and rendered as "Sync failed". One structured warning is
    logged per skipped account per BOOT (this function runs once, from
    :func:`start_background_sync`), never per cycle."""
    known = _known_providers()
    resolver = await _get_db()
    try:
        org_rows = (await resolver.execute(
            text("SELECT id FROM organization"))).fetchall()
    finally:
        await resolver.close()

    out: dict[str, tuple[str, int]] = {}
    # Deduped across orgs like `out` is, and for the same pre-phase-4 reason:
    # each per-org read is unscoped while RLS is off, so a skipped account is
    # seen once per organization and would otherwise log N identical lines.
    skipped: set[str] = set()
    for org_row in org_rows:
        org_id = str(org_row.id)
        # Launch only for accounts whose owner hasn't turned background_sync off
        # (LEFT JOIN → users with no settings row default to on).
        async with _tenant_session(org_id) as db:
            rows = (await db.execute(
                text("""SELECT a.id, a.provider, a.sync_interval_secs
                        FROM task_accounts a
                   LEFT JOIN gtd_settings s ON s.user_id = a.user_id
                       WHERE a.sync_enabled = true
                         AND coalesce(s.background_sync, true) = true"""),
            )).fetchall()
        for row in rows:
            account_id = str(row.id)
            if account_id in out:
                continue  # first tenant that saw it wins (pre-phase-4 dedup)
            provider = str(row.provider or "")
            if provider not in known:
                if account_id not in skipped:
                    skipped.add(account_id)
                    _log.warning(
                        "tasks.scheduler.provider_retired_skipped",
                        account_id=account_id[:12], provider=provider,
                        known_providers=sorted(known),
                        detail=("no loop launched — this provider has no "
                                "connector in the registry (D52). The row and "
                                "its mirrored items are untouched; nothing "
                                "writes sync_status/sync_error for it."))
                continue
            interval = max(row.sync_interval_secs or _DEFAULT_INTERVAL_SECS,
                           _MIN_INTERVAL_SECS)
            out[account_id] = (org_id, interval)
    return out


async def start_background_sync() -> dict[str, int]:
    """Launch one sync loop per sync-enabled account. Called from the gateway
    lifespan on startup. Returns {account_id: interval_secs}."""
    global _scheduler_running
    async with _scheduler_lock:
        if _scheduler_running:
            _log.info("tasks.scheduler.already_running")
            return {}
        accounts = await _enabled_accounts_by_org()

        launched: dict[str, int] = {}
        for account_id, (org_id, interval) in accounts.items():
            _scheduler_tasks[account_id] = asyncio.create_task(
                _account_sync_loop(account_id, org_id, interval))
            launched[account_id] = interval
        _scheduler_running = True
        _log.info("tasks.scheduler.started", accounts=len(launched))
        return launched


async def stop_background_sync() -> None:
    """Cancel every sync loop. Called from the gateway lifespan on shutdown."""
    global _scheduler_running
    async with _scheduler_lock:
        _scheduler_running = False
        tasks = list(_scheduler_tasks.values())
        _scheduler_tasks.clear()
        for task in tasks:
            task.cancel()
    # Await cancellation OUTSIDE the lock so a self-stopping loop (or any
    # lock-taking teardown) can't deadlock against shutdown.
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _log.info("tasks.scheduler.stopped", cancelled=len(tasks))


async def refresh_account_sync(account_id: str) -> None:
    """Start or restart the loop for one account — call after connecting a
    workspace or toggling ``sync_enabled`` on. No-op if the scheduler hasn't
    started (startup will pick the account up).

    ⚠️ **H4:** the launched loop runs detached, so it needs an EXPLICIT tenant
    threaded in. This is only ever called from a tenant-bound REQUEST handler
    (account connect / PATCH), so the account's tenant is exactly
    :func:`current_tenant` — captured here as a plain value and passed on. It
    REFUSES (``TenantUnbound``) rather than launch an unbound loop if somehow
    reached with no tenant bound. The tenant is resolved only when the scheduler
    is actually running, so the "not started yet" no-op still needs none."""
    async with _scheduler_lock:
        if not _scheduler_running:
            return
        existing = _scheduler_tasks.pop(account_id, None)
        if existing:
            existing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await existing
    org_id = current_tenant()
    if not org_id:
        raise TenantUnbound(
            "tasks.scheduler.refresh_account_sync must be called from a "
            "tenant-bound request — the loop it launches runs detached and "
            "needs an EXPLICIT tenant threaded in (saas_multitenancy_handover.md "
            "§H4); refusing rather than launching an unbound loop")
    interval, enabled = await _read_interval(account_id, org_id)
    if not enabled:
        return
    async with _scheduler_lock:
        _scheduler_tasks[account_id] = asyncio.create_task(
            _account_sync_loop(account_id, org_id, interval))
        _log.info("tasks.scheduler.loop_refreshed",
                  account_id=account_id[:12], interval=interval)


async def remove_account_sync(account_id: str) -> None:
    """Stop the loop for one account — call after disconnecting a workspace or
    toggling ``sync_enabled`` off."""
    async with _scheduler_lock:
        task = _scheduler_tasks.pop(account_id, None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        _log.info("tasks.scheduler.loop_removed", account_id=account_id[:12])


def get_scheduler_status() -> dict[str, Any]:
    """Live scheduler state for health checks / debug."""
    return {
        "running": _scheduler_running,
        "accounts": list(_scheduler_tasks.keys()),
        "count": len(_scheduler_tasks),
    }
