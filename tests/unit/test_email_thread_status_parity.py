"""H5: the backfill and the live runner must agree about a thread.

Two jobs run the same rules over mail — ``_run_rules_job`` on new mail and
``_process_past_emails_job`` on a date range. The live one recorded the thread's
Reply Zero status and collapsed its conversation labels to one; the backfill did
neither. So the same email got a different outcome depending on which button
processed it: chips saying "Reply" while Reply Zero showed nothing, and threads
wearing Reply AND Awaiting AND Done at once.

The bug existed because nothing stopped the two paths drifting. These tests are
that stop.
"""
from __future__ import annotations

import inspect

from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import runner as r


# ── both paths must do the two thread-level writes ──────────────────────────


def test_both_jobs_record_thread_status_and_reconcile_labels() -> None:
    """The status row and the labels are two views of ONE decision. A path that
    writes only one of them is how they came to disagree."""
    live = inspect.getsource(r._run_rules_job)
    backfill = (inspect.getsource(r._process_past_emails_job)
                + inspect.getsource(r._project_thread_status_for_backfill))
    for name, src in (("live", live), ("backfill", backfill)):
        assert "project_reply_status_from_matches" in src, (
            f"{name} path no longer records Reply Zero thread status"
        )
        assert "_reconcile_thread_labels" in src, (
            f"{name} path no longer collapses the thread's conversation labels"
        )


def test_the_periodic_classifier_also_reconciles() -> None:
    """_maybe_classify_threads wrote the status row but never reconciled the
    labels — 68 threads on the live account ended up with messages disagreeing
    with each other."""
    src = inspect.getsource(rz._maybe_classify_threads)
    assert "project_reply_status_from_matches" in src
    assert "_reconcile_thread_labels" in src, (
        "the periodic classifier no longer collapses stale conversation labels"
    )


# ── the ordering trap ───────────────────────────────────────────────────────


def test_the_two_jobs_iterate_in_opposite_directions() -> None:
    """This is why the fix could not be copy-pasted.

    The live runner reads newest-first and projects the FIRST message it sees
    per thread. The backfill reads OLDEST-first so learned patterns build up
    chronologically. Projecting inline there would let a thread's oldest message
    decide its status — precisely what the live runner's ordering prevents.
    """
    assert "ORDER BY em.received_at DESC" in inspect.getsource(r._run_rules_job)
    assert "ORDER BY em.received_at ASC" in inspect.getsource(
        r._process_past_emails_job)


def test_backfill_projects_after_the_loop_not_inside_it() -> None:
    src = inspect.getsource(r._process_past_emails_job)
    assert "latest_by_thread[r.thread_id] = (r, matches)" in src, (
        "the backfill no longer tracks the newest in-range message per thread"
    )
    # The projection call must sit outside the per-message loop.
    body = src.split("for r in rows:", 1)[1]
    call = "_project_thread_status_for_backfill("
    assert call in body
    projection_line = body.index(call)
    tick_line = body.rindex("_past_job_tick(")
    assert projection_line > tick_line, (
        "projection runs inside the message loop — it must run once per thread "
        "after the whole range is walked, or the oldest message wins"
    )


# ── restraints that keep it cheap and correct ───────────────────────────────


def test_projection_skips_threads_with_newer_mail_out_of_range() -> None:
    """If newer mail exists outside the date window, the newest message the run
    saw is NOT the thread's newest — projecting from it moves the thread
    backwards. Those belong to the periodic classifier."""
    src = inspect.getsource(r._project_thread_status_for_backfill)
    assert "stale" in src and "row.newest > seen[0].received_at" in src


def test_the_backfill_projection_spends_no_model_calls() -> None:
    """One determination per thread across a whole mailbox is exactly the token
    waste this pipeline was asked to stop. The deterministic rule projection is
    free; the periodic classifier spends the budget on inbox threads instead."""
    src = inspect.getsource(r._project_thread_status_for_backfill)
    assert "resolve_conversation_status_matches" not in src, (
        "the backfill projection now makes an AI call per thread"
    )


def test_one_bad_thread_does_not_abort_the_rest() -> None:
    src = inspect.getsource(r._project_thread_status_for_backfill)
    assert "past_project_status_failed" in src


# ── the classifier must be able to reach old mail at all ───────────────────


def test_classifier_selects_threads_that_need_work_not_merely_recent_ones() -> None:
    """It used to take the newest 200 threads from the last 30 days and skip the
    already-classified ones — which, on a real mailbox, is all of them. Measured
    live: 295 of 3,487 threads had a status, and nothing older than a month
    could ever acquire one."""
    src = inspect.getsource(rz._maybe_classify_threads)
    assert "interval '30 days'" not in src, (
        "the 30-day window is back — threads older than a month become "
        "permanently unclassifiable"
    )
    assert "LEFT JOIN email_thread_status" in src, (
        "the classifier no longer filters to threads that need work"
    )


def test_classifier_prioritises_inbox_threads() -> None:
    """Filed threads outnumber live ones by ~10:1. Ordering by date alone put
    2,622 already-handled threads ahead of the 273 that might still need a
    reply."""
    src = inspect.getsource(rz._maybe_classify_threads)
    assert "WHEN 'inbox' THEN 0" in src


def test_filed_threads_are_resolved_without_an_ai_call() -> None:
    """Inbound-last but already archived = the user dealt with it. Deterministic
    FYI: this is the bulk of an old mailbox and re-litigating it with a model
    would cost thousands of calls on mail already put away."""
    # Since EM-T1b-2 fix round 1 the filed phase is its own helper, called
    # from the backfill, so a failed filed write cannot skip the sent threads.
    assert "_write_filed_fyi(" in inspect.getsource(rz._maybe_classify_threads)
    src = inspect.getsource(rz._write_filed_fyi)
    assert "_upsert_thread_status(" in src
    assert "preserve_done=True" in src, (
        "a thread the user explicitly marked Done must not be reset to FYI"
    )
