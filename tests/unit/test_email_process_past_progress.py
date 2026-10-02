"""Live-progress tracking for the "Process past emails" background job.

The job runs fire-and-forget, so the UI polls GET /rules/process-past/status to
show an ongoing indicator. These tests cover the in-memory tracker lifecycle and
that the handler seeds it — historical apply DOWNLOADS the requested range from
upstream first, so it always schedules (starting in the 'downloading' phase),
even when nothing is synced locally yet.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from gateway.routes import email as m
from tests.unit._email_fakes import bind_db

runner = m.automation.runner


@pytest.fixture(autouse=True)
def _clear_jobs():
    runner._PAST_JOBS.clear()
    yield
    runner._PAST_JOBS.clear()


def test_tracker_lifecycle_running_to_done() -> None:
    runner._past_job_start("acc-1", "u@example.com", total=3, dry_run=False)
    job = runner._PAST_JOBS.get("acc-1")
    assert job["status"] == "running"
    assert (job["total"], job["processed"]) == (3, 0)

    runner._past_job_tick("acc-1", applied=1, skipped=0)
    runner._past_job_tick("acc-1", applied=0, skipped=1)
    assert (job["processed"], job["applied"], job["skipped"]) == (2, 1, 1)

    runner._past_job_finish("acc-1")
    assert job["status"] == "done"
    assert job["finished_at"] is not None
    assert job["error"] is None


def test_zero_total_starts_done_and_ticks_noop() -> None:
    # An empty range is finished on the spot — no background job runs.
    runner._past_job_start("acc-1", "u@example.com", total=0, dry_run=False)
    assert runner._PAST_JOBS.get("acc-1")["status"] == "done"
    runner._past_job_tick("acc-1", applied=1)  # ignored once not running
    assert runner._PAST_JOBS.get("acc-1")["processed"] == 0


def test_finish_records_error() -> None:
    runner._past_job_start("acc-1", "u@example.com", total=2, dry_run=False)
    runner._past_job_finish("acc-1", error="boom")
    job = runner._PAST_JOBS.get("acc-1")
    assert job["status"] == "error"
    assert job["error"] == "boom"


def test_stale_token_cannot_clobber_newer_run() -> None:
    # A first run starts, then a second run for the SAME account supersedes it.
    tok1 = runner._past_job_start("acc-1", "u@example.com", total=2, dry_run=False)
    tok2 = runner._past_job_start("acc-1", "u@example.com", total=9, dry_run=False)
    assert tok2 != tok1

    # The first (stale) job finishing must not mark the new run done.
    runner._past_job_finish("acc-1", token=tok1)
    job = runner._PAST_JOBS.get("acc-1")
    assert job["status"] == "running" and job["total"] == 9

    # …and its ticks land on nothing.
    runner._past_job_tick("acc-1", token=tok1, applied=1)
    assert job["processed"] == 0

    # The current run advances and finishes normally.
    runner._past_job_tick("acc-1", token=tok2, applied=1)
    runner._past_job_finish("acc-1", token=tok2)
    assert job["processed"] == 1 and job["status"] == "done"


async def test_status_endpoint_idle_when_no_job() -> None:
    user = SimpleNamespace(email="u@example.com")
    res = await m.process_past_status("acc-1", user=user)
    assert res == {"status": "idle"}


async def test_status_endpoint_returns_progress_without_owner() -> None:
    runner._past_job_start("acc-1", "u@example.com", total=5, dry_run=True)
    runner._past_job_tick("acc-1", applied=1)
    user = SimpleNamespace(email="u@example.com")
    res = await m.process_past_status("acc-1", user=user)
    assert res["status"] == "running"
    assert res["total"] == 5
    assert res["processed"] == 1
    assert res["dry_run"] is True
    assert "owner" not in res            # never leak the owner identity


async def test_status_endpoint_hidden_from_other_user() -> None:
    runner._past_job_start("acc-1", "owner@example.com", total=5, dry_run=False)
    other = SimpleNamespace(email="someone@else.com")
    res = await m.process_past_status("acc-1", user=other)
    assert res == {"status": "idle"}


async def test_handler_seeds_tracker_and_schedules_when_mail_exists() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = SimpleNamespace(c=4)
    db.execute.return_value = result
    user = SimpleNamespace(email="u@example.com")
    bg = BackgroundTasks()
    req = m.RuleProcessPastRequest(
        account_id="acc-1", start_date="2026-01-01", end_date="2026-01-31",
        is_test=False, include_read=True)
    with patch.object(runner, "_tenant_session", bind_db(db)), \
            patch.object(runner, "_assert_account_owner", AsyncMock()):
        res = await m.process_past_emails(req, background=bg, user=user)

    # Both switches echo back so the caller can confirm what it just started.
    # draft_replies is FALSE unless asked for (a backfill files old mail rather
    # than answering it, one drafting call per message otherwise), and
    # skip_processed is TRUE so a repeat run doesn't re-classify what it already
    # did. already_processed is 0 here: the mock returns the same count for the
    # filtered and unfiltered queries, so nothing was excluded.
    assert res == {"scheduled": True, "count": 4, "dry_run": False,
                   "draft_replies": False, "skip_processed": True,
                   "already_processed": 0}
    assert len(bg.tasks) == 1                       # background job scheduled
    job = runner._PAST_JOBS.get("acc-1")
    assert job["status"] == "running" and job["total"] == 4


@pytest.mark.parametrize("is_test", [False, True])
async def test_the_job_gets_dry_run_equal_to_is_test(is_test: bool) -> None:
    """An apply (`is_test=False`) must reach the job as `dry_run=False`, and a
    preview as `dry_run=True`. From f1a13861 to 2026-10-02 the handler passed
    `not req.is_test`, so the UI's apply ran as a preview and a preview applied."""
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = SimpleNamespace(c=4)
    db.execute.return_value = result
    user = SimpleNamespace(email="u@example.com")
    bg = BackgroundTasks()
    req = m.RuleProcessPastRequest(
        account_id="acc-1", start_date="2026-01-01", end_date="2026-01-31",
        is_test=is_test, include_read=True)
    with patch.object(runner, "_tenant_session", bind_db(db)), \
            patch.object(runner, "_assert_account_owner", AsyncMock()):
        await m.process_past_emails(req, background=bg, user=user)

    task = bg.tasks[0]
    assert task.func is runner._process_past_emails_job
    sig = inspect.signature(runner._process_past_emails_job)
    bound = sig.bind(*task.args, **task.kwargs)
    assert bound.arguments["dry_run"] is is_test


async def test_handler_schedules_download_even_when_nothing_local() -> None:
    # Historical apply over a range must DOWNLOAD that range from upstream first,
    # so it schedules even when nothing is synced locally yet (count == 0). The
    # tracker starts in the 'downloading' phase; the real in-range total is
    # computed after the backfill lands. (Regression guard for the range-first fix.)
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = SimpleNamespace(c=0)
    db.execute.return_value = result
    user = SimpleNamespace(email="u@example.com")
    bg = BackgroundTasks()
    # A start date is now required — an open-ended range is every message ever
    # received, at one AI call each (see _assert_span_within_cap).
    req = m.RuleProcessPastRequest(
        account_id="acc-1", is_test=False, start_date="2026-05-01")
    with patch.object(runner, "_tenant_session", bind_db(db)), \
            patch.object(runner, "_assert_account_owner", AsyncMock()):
        res = await m.process_past_emails(req, background=bg, user=user)

    assert res == {"scheduled": True, "count": 0, "dry_run": False,
                   "draft_replies": False, "skip_processed": True,
                   "already_processed": 0}
    assert len(bg.tasks) == 1                        # scheduled to download first
    job = runner._PAST_JOBS.get("acc-1")
    assert job["status"] == "running"
    assert job["phase"] == "downloading"
