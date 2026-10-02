"""Reclassify must drain the WHOLE mailbox, resumably (review 3.7).

Before this, the rebuild ran a fixed 8 passes — 200 threads a pass — so on a
3,500-thread mailbox it rebuilt only the newest ~1,600 and left the rest on the
old classifier. Now it loops until nothing still needs a status, stops cleanly
when a pass makes no progress (LLM down → resumable), publishes progress, and
refuses a concurrent rebuild. These pin that, with the DB + classifier mocked.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks
from gateway.routes.email.automation import replyzero as r
from tests.unit._email_fakes import bind_db

_ACC = "acc-reclass"


class _User:
    email = "u@example.com"


@contextmanager
def _mock_db_ctx():
    """The DB seam → an AsyncMock db whose execute/commit/close all no-op.

    The routes here are converted to `_tenant_session` (H2), and since
    EM-T4a-0 the background `_reclassify_reply_zero_job` is too, so one
    double serves both.
    """
    with patch.object(r, "_tenant_session", bind_db(AsyncMock())):
        yield


async def test_drain_classifies_until_no_threads_need_a_status() -> None:
    r._RECLASSIFY_JOBS.clear()
    # total (post-reset) then the per-pass remaining: 3 → 2 → 1 → 0.
    counts = iter([3, 3, 2, 1, 0])
    passes: list[str] = []

    async def fake_count(_db, _aid):
        return next(counts)

    async def fake_classify(aid):
        passes.append(aid)

    with _mock_db_ctx(), \
            patch.object(r, "_count_reply_zero_backlog", fake_count), \
            patch.object(r, "_maybe_classify_threads", fake_classify):
        token = r._RECLASSIFY_JOBS.start(_ACC, status="running")
        await r._reclassify_reply_zero_job(_ACC, token=token)

    job = r._RECLASSIFY_JOBS.get(_ACC)
    assert job["status"] == "done"
    assert job["total"] == 3 and job["remaining"] == 0
    assert len(passes) == 3, "kept classifying until the backlog was drained"


async def test_drain_stops_when_a_pass_makes_no_progress() -> None:
    # LLM down: the remainder can't be classified, so remaining never drops.
    # The drain must STOP (resumable), not spin to the safety cap.
    r._RECLASSIFY_JOBS.clear()
    counts = iter([5, 5, 5, 5, 5, 5])
    passes: list[str] = []

    async def fake_count(_db, _aid):
        return next(counts)

    async def fake_classify(aid):
        passes.append(aid)

    with _mock_db_ctx(), \
            patch.object(r, "_count_reply_zero_backlog", fake_count), \
            patch.object(r, "_maybe_classify_threads", fake_classify):
        token = r._RECLASSIFY_JOBS.start(_ACC, status="running")
        await r._reclassify_reply_zero_job(_ACC, token=token)

    job = r._RECLASSIFY_JOBS.get(_ACC)
    assert job["status"] == "done"
    assert job["remaining"] == 5           # left for a resume
    assert len(passes) == 1, "one pass showed no progress → stop, don't spin"


async def test_endpoint_seeds_running_state_before_scheduling() -> None:
    r._RECLASSIFY_JOBS.clear()
    bg = BackgroundTasks()
    with _mock_db_ctx(), patch.object(r, "_assert_account_owner", AsyncMock()):
        res = await r.reclassify_reply_zero(
            r.ReplyZeroReclassifyRequest(account_id=_ACC), bg, user=_User())
    assert res == {"scheduled": True}
    # A poll landing immediately must see "running", not an empty gap …
    job = r._RECLASSIFY_JOBS.get(_ACC)
    assert job["status"] == "running"
    # … and the drain task was queued WITH the guard token.
    assert bg.tasks and bg.tasks[0].kwargs.get("token") == job["token"]


async def test_endpoint_refuses_a_concurrent_rebuild() -> None:
    r._RECLASSIFY_JOBS.clear()
    r._RECLASSIFY_JOBS.start(_ACC, status="running")
    bg = BackgroundTasks()
    with _mock_db_ctx(), patch.object(r, "_assert_account_owner", AsyncMock()):
        res = await r.reclassify_reply_zero(
            r.ReplyZeroReclassifyRequest(account_id=_ACC), bg, user=_User())
    assert res == {"scheduled": False, "already_running": True}
    assert not bg.tasks, "must not queue a second draining job"


async def test_status_endpoint_hides_the_guard_token() -> None:
    r._RECLASSIFY_JOBS.clear()
    r._RECLASSIFY_JOBS.start(
        _ACC, status="running", total=10, remaining=4, processed=6)
    with _mock_db_ctx(), patch.object(r, "_assert_account_owner", AsyncMock()):
        res = await r.reclassify_reply_zero_status(
            account_id=_ACC, user=_User())
    assert res["status"] == "running"
    assert res["remaining"] == 4 and res["processed"] == 6
    assert "token" not in res


async def test_status_is_idle_when_nothing_has_run() -> None:
    r._RECLASSIFY_JOBS.clear()
    with _mock_db_ctx(), patch.object(r, "_assert_account_owner", AsyncMock()):
        res = await r.reclassify_reply_zero_status(
            account_id=_ACC, user=_User())
    assert res == {"status": "idle"}
