"""The sync loop must back off a failing account and clean up after a crash.

Two resilience properties of the background sync scheduler:

  * A persistently failing account (revoked token, disconnected upstream) must
    not be polled every 300s forever — each poll is a wasted auth handshake and
    Graph call. Back off exponentially, cap ~1h, reset on the first success.
  * A process that crashed or restarted mid-sync leaves sync-log rows stuck
    'running' and accounts stuck 'syncing' — nothing ever completes them. A
    fresh scheduler owns every sync, so it closes those orphans on startup.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

from email_ingestion import scheduler as s


# ── backoff ─────────────────────────────────────────────────────────────────

def test_success_resets_to_the_normal_interval() -> None:
    # 0 means "sleep the interval"; a prior backoff is cleared on success.
    assert s._next_backoff(0, 300, failed=False) == 0
    assert s._next_backoff(1800, 300, failed=False) == 0


def test_first_failure_backs_off_to_double_the_interval() -> None:
    assert s._next_backoff(0, 300, failed=True) == 600


def test_each_failure_doubles_the_backoff() -> None:
    b = s._next_backoff(0, 300, failed=True)      # 600
    b = s._next_backoff(b, 300, failed=True)      # 1200
    assert b == 1200
    b = s._next_backoff(b, 300, failed=True)      # 2400
    assert b == 2400


def test_backoff_is_capped_around_one_hour() -> None:
    b = 3000
    for _ in range(10):
        b = s._next_backoff(b, 300, failed=True)
    assert b == s._MAX_SYNC_BACKOFF_SECS == 3600


# ── orphaned-sync cleanup ───────────────────────────────────────────────────

async def test_startup_closes_orphaned_running_sync_logs_and_statuses() -> None:
    db = AsyncMock()
    await s._close_orphaned_syncs(db, "org-1")
    stmts = [str(c[0][0]) for c in db.execute.call_args_list]
    assert len(stmts) == 2
    # EM-T1b-1: both statements filter on the owning org, so a catalog
    # without RLS still closes only the rows of that one organization.
    for call in db.execute.call_args_list:
        assert "organization_id = CAST(:org AS uuid)" in str(call[0][0])
        assert call[0][1] == {"org": "org-1"}
    log_sql = next(x for x in stmts if "email_sync_log" in x)
    acc_sql = next(x for x in stmts if "email_accounts" in x)
    # sync-log: only the still-'running' rows, marked error + completed.
    assert "status = 'running'" in log_sql
    assert "status = 'error'" in log_sql
    assert "completed_at = now()" in log_sql
    # accounts: only those stuck 'syncing', reset to idle.
    assert "sync_status = 'syncing'" in acc_sql
    assert "sync_status = 'idle'" in acc_sql
