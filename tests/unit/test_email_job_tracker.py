"""The shared JobTracker's monotonic-token guard.

Both the rule runner's process-past job and the cleaner's sweep publish
per-account progress a background task mutates while a request reads it. The
hazard is a superseded (older) run's late write landing on a NEWER run's row —
marking the newer job done, or overwriting its counts. The runner had a token
guard; the cleaner did not. This is that one guard, and these tests pin it.
"""
from __future__ import annotations

from gateway.routes.email.automation.jobs import JobTracker


def test_start_mints_monotonic_tokens() -> None:
    t = JobTracker()
    a = t.start("acc", status="running")
    b = t.start("acc", status="running")
    assert b > a  # strictly increasing, so "newer" is well-defined


def test_a_superseded_run_cannot_mutate_the_newer_row() -> None:
    t = JobTracker()
    old = t.start("acc", status="running", processed=0)
    new = t.start("acc", status="running", processed=0)  # supersedes `old`

    # The OLD run's late tick must no-op — the row belongs to `new` now.
    assert t.guarded("acc", old) is None
    t.update("acc", old, processed=999)
    assert t.get("acc")["processed"] == 0

    # The NEW run's writes land.
    t.update("acc", new, processed=5)
    assert t.get("acc")["processed"] == 5


def test_a_superseded_run_cannot_finish_the_newer_row() -> None:
    t = JobTracker()
    old = t.start("acc", status="running")
    t.start("acc", status="running")  # newer run
    t.finish("acc", old, status="done")  # old run's terminal write
    assert t.get("acc")["status"] == "running"  # newer run still going


def test_guarded_returns_the_live_row_for_in_place_mutation() -> None:
    t = JobTracker()
    tok = t.start("acc", status="running", applied=0)
    row = t.guarded("acc", tok)
    assert row is not None
    row["applied"] += 3  # mutating the returned dict mutates the stored row
    assert t.get("acc")["applied"] == 3


def test_tokenless_update_is_unguarded() -> None:
    # The pre-token call sites pass no token; those updates always apply.
    t = JobTracker()
    t.start("acc", status="running", n=0)
    t.update("acc", None, n=1)
    assert t.get("acc")["n"] == 1


def test_is_running_reflects_status() -> None:
    t = JobTracker()
    assert t.is_running("acc") is False          # nothing started
    tok = t.start("acc", status="running")
    assert t.is_running("acc") is True
    t.finish("acc", tok, status="done")
    assert t.is_running("acc") is False


def test_missing_account_is_safe() -> None:
    t = JobTracker()
    assert t.get("nope") is None
    assert t.guarded("nope", 1) is None
    t.update("nope", 1, x=1)   # no-op, no raise
    t.finish("nope", 1)        # no-op, no raise


def test_one_mailbox_id_in_any_form_finds_one_row() -> None:
    """A start route keys by the raw body id. A status route reads the
    canonical id of ``account_ref``. Both forms reach the same row."""
    aid = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
    t = JobTracker()
    tok = t.start(aid.upper(), status="running")
    for form in (aid, "{" + aid + "}", aid.replace("-", ""), f" {aid} "):
        assert t.get(form) == {"token": tok, "status": "running"}, form
        assert t.is_running(form) is True
    t.finish(aid, tok, status="done")
    assert t.get(aid.upper())["status"] == "done"
