"""UNDO-1 round 2, fix B: the rule for ``completed_at`` on a status write.

The twin on a real database is ``test_projects_done_date_r8.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException
from gateway.routes.projects import core

TASK = {
    "id": "t", "status_id": "s-old", "project_id": "p",
    "created_at": datetime.now(UTC) - timedelta(days=10),
}


def _transition(when, old="todo", new="done"):
    old_s = SimpleNamespace(id="s-old", name="Old", category=old)
    new_s = SimpleNamespace(id="s-new", name="New", category=new)
    written: dict = {}

    async def fake_update(db, table, row_id, values):
        written.update(values)
        return SimpleNamespace(id=row_id, **values)

    with (
        patch.object(core, "require_row", AsyncMock(return_value=old_s)),
        patch.object(core, "status_owner_id", AsyncMock(return_value="p")),
        patch.object(core, "require_status_in_project", AsyncMock(return_value=new_s)),
        patch.object(core, "update_row", fake_update),
        patch.object(core, "record_activity", AsyncMock()),
        patch("gateway.routes.projects.recurrence.spawn_successor",
              AsyncMock(return_value=None)),
    ):
        asyncio.run(core.apply_status_transition(
            None, SimpleNamespace(**TASK), "s-new", created_by="a@x.test",
            completed_at=when))
    return written


def test_a_past_date_is_kept_on_the_crossing_into_done():
    when = datetime.now(UTC) - timedelta(days=3)
    assert _transition(when)["completed_at"] == when


def test_no_date_stamps_now():
    got = _transition(None)["completed_at"]
    assert abs((datetime.now(UTC) - got).total_seconds()) < 5


def test_a_future_date_is_refused_with_a_plain_message():
    with pytest.raises(HTTPException) as err:
        _transition(datetime.now(UTC) + timedelta(days=1))
    assert err.value.status_code == 422
    assert "future" in err.value.detail


def test_a_date_before_creation_is_refused():
    with pytest.raises(HTTPException) as err:
        _transition(datetime.now(UTC) - timedelta(days=30))
    assert err.value.status_code == 422
    assert "before the task was created" in err.value.detail


def test_the_date_is_ignored_when_the_task_does_not_close():
    when = datetime.now(UTC) - timedelta(days=3)
    assert "completed_at" not in _transition(when, old="todo", new="started")


def test_a_reopen_clears_the_date_whatever_was_sent():
    when = datetime.now(UTC) - timedelta(days=3)
    assert _transition(when, old="done", new="todo")["completed_at"] is None


def test_parse_completed_at_refuses_junk_and_accepts_z():
    assert core.parse_completed_at(None) is None
    assert core.parse_completed_at("2026-01-02T03:04:05Z").tzinfo is not None
    with pytest.raises(HTTPException) as err:
        core.parse_completed_at("yesterday")
    assert err.value.status_code == 422
