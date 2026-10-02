"""Clean older mail: fetch history, then categorize it without a model.

The Email Cleaner showed 564 outstanding messages on a mailbox holding ~43,000.
It was not failing to categorize them — it had never seen them. Before EM-T6a,
``_sync_account`` fetched one year on an account's FIRST sync, and every sync
after that is incremental, so older mail was simply absent. Measured on the
live account: 6,803 messages held, every folder starting within days of one
year before the account was connected. Since EM-T6a the first sync fetches the
range that the member chose, and no path reaches back past 180 days (D-EM-10).
"Everything" now means back to that ceiling.

This is the deterministic counterpart of "Process past emails": same two-phase
shape, but it spends nothing on models.

The load-bearing detail is ``_mark_history_held_back``. The scheduled rule run
classifies unprocessed inbox mail 50 per cycle, one model call each. Backfilled
mail all arrives unprocessed, so downloading 36,000 messages would silently
queue 36,000 model calls — precisely the cost this feature exists to avoid.

Held back is a SEPARATE state from processed (migration 84). Collapsing the two
broke the feature in both directions: the first version's floor was inert on the
live mailbox, and had it worked it would have hidden every backfilled message
from "Process past emails" forever.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes.email.automation import cleanup as c
from tests.unit._email_fakes import bind_db

_ACC = "acc-backfill"


# ── the request ─────────────────────────────────────────────────────────────


def test_no_date_means_the_whole_mailbox() -> None:
    """"Everything" sends no date. The route maps it to the ceiling of 180
    days, never to the smaller import range (EM-T6a,
    ``test_email_import_floor.py``)."""
    assert c.CleanupBackfillRequest(account_id=_ACC).since_date is None


def test_a_floor_can_be_given() -> None:
    req = c.CleanupBackfillRequest(account_id=_ACC, since_date="2023-01-01")
    assert req.since_date == "2023-01-01"


# ── holding history back from the model ─────────────────────────────────────


async def test_history_is_held_back() -> None:
    """Without this, a backfill is a bill."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=1234)
    n = await c._mark_history_held_back(
        db, _ACC, datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert n == 1234
    sql = str(db.execute.call_args[0][0])
    assert "rules_held_back_at = now()" in sql
    assert "rules_held_back_at IS NULL" in sql


async def test_held_back_is_not_the_same_as_processed() -> None:
    """The distinction this whole column exists for.

    Held back means "downloaded as history, deliberately not sent to the model".
    Processed means "the rules decided about this". Writing rules_processed_at
    here made backfilled mail permanently invisible to "Process past emails" —
    which skips rules_processed_at IS NOT NULL — and made the UI report it as
    already processed, which was false. The user asked for exactly the opposite:

      "Ensure this does not interfere with AI categorization of past emails."
    """
    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=0)
    await c._mark_history_held_back(
        db, _ACC, datetime(2026, 7, 20, tzinfo=timezone.utc))
    sql = str(db.execute.call_args[0][0])
    assert "SET rules_held_back_at" in sql
    assert "rules_processed_at = now()" not in sql, (
        "holding history back must not claim the rules ran over it — that is "
        "what hid backfilled mail from AI categorization"
    )


async def test_the_floor_is_the_job_not_the_oldest_message_held() -> None:
    """The regression. The first version used MIN(received_at) over the account
    as the floor, assuming anything older than the oldest message held must be
    newly downloaded. One stray old message defeats that: on the live account
    MIN was 2019-06-28 (a single item in Trash), so the stamp matched almost
    nothing and the cost guarantee was inert.

    created_at is the row's insert time, so it identifies exactly the rows THIS
    backfill inserted, whatever else is sitting in Trash.
    """
    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=0)
    started = datetime(2026, 7, 20, tzinfo=timezone.utc)
    await c._mark_history_held_back(db, _ACC, started)
    sql = str(db.execute.call_args[0][0])
    assert "created_at >= :started" in sql, (
        "the floor must identify rows this backfill inserted, not mail older "
        "than whatever happens to be the oldest message in the mailbox"
    )
    assert db.execute.call_args[0][1]["started"] == started


async def test_mail_arriving_during_the_backfill_stays_eligible() -> None:
    """A message that lands mid-backfill is inserted by the job (created_at is
    new) but was received after it started, so it is not history. Holding it
    back would silently stop new mail being classified."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=0)
    await c._mark_history_held_back(
        db, _ACC, datetime(2026, 7, 20, tzinfo=timezone.utc))
    sql = str(db.execute.call_args[0][0])
    assert "received_at < :started" in sql


def test_the_live_rule_run_skips_held_back_mail() -> None:
    """The other half: holding mail back is only worth anything if the per-cycle
    run actually honours it."""
    from gateway.routes.email.automation import runner
    src = inspect.getsource(runner._run_rules_job)
    assert "em.rules_held_back_at IS NULL" in src, (
        "the scheduled rule run no longer skips held-back history — a full "
        "backfill would queue one model call per backfilled inbox message"
    )


def test_process_past_still_reaches_held_back_mail() -> None:
    """And the point of the split: a deliberate, bounded, user-initiated AI run
    must still be able to categorize backfilled history."""
    from gateway.routes.email import core
    clause, _ = core._date_range_clause(
        _ACC, None, None, unprocessed_only=True)
    assert "rules_held_back_at" not in clause, (
        "Process past emails must not inherit the automatic run's hold-back "
        "guard, or backfilled history can never be AI-categorized at all"
    )


def test_the_job_holds_back_before_it_sweeps() -> None:
    """Sweeping first would be harmless, but the RULE RUN fires on its own
    schedule — every cycle the stamp is late is a cycle it can bill for."""
    src = inspect.getsource(c._backfill_and_clean_job)
    assert src.index("_mark_history_held_back") < src.index(
        "sweep_uncategorized"), (
        "history must be held back from the model BEFORE the long sweep starts"
    )


def test_the_sweep_ignores_both_watermarks() -> None:
    """Holding mail back from the model must not make it uncleanable — the whole
    plan is that the deterministic sweep still covers it, for free."""
    assert "rules_processed_at" not in c._CLEANUP_SCOPE
    assert "rules_held_back_at" not in c._CLEANUP_SCOPE


# ── progress ────────────────────────────────────────────────────────────────


def test_both_phases_are_reported() -> None:
    """Downloading years of mail takes minutes. A job that reports nothing until
    it finishes reads as stuck, and the user presses the button again. The
    'downloading' phase is seeded by the endpoint (before the task starts); the
    'cleaning' phase is set by the job once the download lands."""
    endpoint = inspect.getsource(c.cleanup_backfill)
    job = inspect.getsource(c._backfill_and_clean_job)
    assert 'phase="downloading"' in endpoint
    assert 'phase="cleaning"' in job


def test_the_tracker_is_seeded_before_any_slow_work() -> None:
    """The UI polls immediately after the POST returns; an empty tracker would
    read as "idle" and the flow would look like it never started. The endpoint
    now seeds the row (and mints the guard token) with .start() BEFORE scheduling
    the background task, closing the check-then-act race the old in-job seed had."""
    src = inspect.getsource(c.cleanup_backfill)
    assert src.index("_SWEEP_JOBS.start(") < src.index("add_task")


def test_a_failure_is_recorded_rather_than_lost() -> None:
    src = inspect.getsource(c._backfill_and_clean_job)
    assert 'status="error"' in src


# ── the endpoint ────────────────────────────────────────────────────────────


async def test_a_second_run_is_refused_while_one_is_in_flight() -> None:
    """Two deep syncs on one mailbox race the provider and each other's progress
    row. Say so rather than silently start a second."""
    c._SWEEP_JOBS.set(_ACC, {"owner": "u@x", "status": "running"})
    try:
        with patch.object(c, "_tenant_session", bind_db(AsyncMock())), \
                patch.object(c, "_assert_account_owner", AsyncMock()):
            from fastapi import BackgroundTasks
            res = await c.cleanup_backfill(
                c.CleanupBackfillRequest(account_id=_ACC),
                background=BackgroundTasks(),
                user=SimpleNamespace(email="u@x"))
        assert res["scheduled"] is False
        assert res["reason"] == "already_running"
    finally:
        c._SWEEP_JOBS.pop(_ACC, None)


async def test_a_finished_run_does_not_block_the_next_one() -> None:
    c._SWEEP_JOBS.set(_ACC, {"owner": "u@x", "status": "done"})
    try:
        bg = None
        with patch.object(c, "_tenant_session", bind_db(AsyncMock())), \
                patch.object(c, "_assert_account_owner", AsyncMock()):
            from fastapi import BackgroundTasks
            bg = BackgroundTasks()
            res = await c.cleanup_backfill(
                c.CleanupBackfillRequest(account_id=_ACC),
                background=bg, user=SimpleNamespace(email="u@x"))
        assert res["scheduled"] is True
        assert len(bg.tasks) == 1
    finally:
        c._SWEEP_JOBS.pop(_ACC, None)
