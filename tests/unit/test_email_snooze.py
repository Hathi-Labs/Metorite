"""Snooze a conversation out of the inbox until later (review 3.3).

Snooze is inbox triage applied to the whole conversation: every message in the
thread is stamped, so it leaves and returns together. The wake is at query time
(no scheduler) — every browse hides sleeping mail, and a dedicated Snoozed view
lists it. These pin the thread-wide stamp, the un-snooze, and the query filters.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from gateway.routes.email import core
from gateway.routes.email.transport import messages as m
from tests.unit._email_fakes import bind_db


class _User:
    email = "u@example.com"


# ── folder_scope: the Snoozed view predicate ────────────────────────────────

def test_snoozed_view_scope_selects_still_sleeping() -> None:
    assert core.folder_scope("snoozed", {}) == "em.snoozed_until > now()"


# ── list_messages: browses hide sleeping mail; the Snoozed view shows it ─────

def _capture_db():
    seen: list[str] = []
    db = AsyncMock()

    async def _exec(sql, params=None):
        seen.append(str(sql))
        return MagicMock(scalar=MagicMock(return_value=0),
                         fetchall=MagicMock(return_value=[]))

    db.execute.side_effect = _exec
    return db, seen


_OFF = {
    "account_id": "a0000000-0000-4000-8000-000000000001", "folder": "INBOX", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}


async def _list(**kw):
    db, seen = _capture_db()
    with patch.object(m, "_tenant_session", bind_db(db)):
        await m.list_messages(user=_User(), **{**_OFF, **kw})
    return "\n".join(seen)


async def test_inbox_browse_excludes_snoozed() -> None:
    sql = await _list(folder="INBOX")
    assert "em.snoozed_until IS NULL OR em.snoozed_until <= now()" in sql


async def test_snoozed_view_does_not_re_exclude() -> None:
    # The Snoozed view's own predicate (> now()) is the only snooze filter — it
    # must not ALSO get the exclusion, or it would always be empty.
    sql = await _list(folder="snoozed")
    assert "em.snoozed_until <= now()" not in sql
    assert "em.snoozed_until > now()" in sql


async def test_thread_load_shows_snoozed_messages() -> None:
    # A thread load selects the column but applies NO snooze filter — opening a
    # snoozed conversation still shows all of it.
    sql = await _list(thread_id="t-1")
    assert "snoozed_until <= now()" not in sql
    assert "snoozed_until > now()" not in sql


# ── snooze endpoint: stamps the whole thread, and clears on un-snooze ────────

def _snooze_db(row):
    db = AsyncMock()
    calls: list[tuple[str, dict]] = []

    async def _exec(sql, params=None):
        calls.append((str(sql), params or {}))
        # First call is the ownership/thread lookup; later is the UPDATE.
        if "UPDATE" in str(sql):
            return MagicMock(rowcount=3)
        return MagicMock(fetchone=MagicMock(return_value=row))

    db.execute.side_effect = _exec
    db.calls = calls
    return db


async def test_snooze_stamps_the_whole_thread() -> None:
    row = SimpleNamespace(account_id="acc-1", thread_id="th-9")
    db = _snooze_db(row)
    with patch.object(m, "_tenant_session", bind_db(db)):
        out = await m.snooze_message(
            "msg-1", m.SnoozeRequest(until="2026-08-01T08:00:00Z"),
            user=_User())
    upd = [c for c in db.calls if "UPDATE" in c[0]][0]
    assert "thread_id = :tid" in upd[0]
    assert upd[1]["tid"] == "th-9"
    assert upd[1]["until"] is not None
    assert out["ok"] and out["snoozed_until"] is not None


async def test_unsnooze_clears_the_stamp() -> None:
    row = SimpleNamespace(account_id="acc-1", thread_id="th-9")
    db = _snooze_db(row)
    with patch.object(m, "_tenant_session", bind_db(db)):
        out = await m.snooze_message("msg-1", m.SnoozeRequest(until=None),
                                     user=_User())
    upd = [c for c in db.calls if "UPDATE" in c[0]][0]
    assert upd[1]["until"] is None
    assert out["snoozed_until"] is None


async def test_lone_message_snoozes_by_id() -> None:
    row = SimpleNamespace(account_id="acc-1", thread_id=None)
    db = _snooze_db(row)
    with patch.object(m, "_tenant_session", bind_db(db)):
        await m.snooze_message("msg-1", m.SnoozeRequest(until="2026-08-01T08:00:00Z"),
                               user=_User())
    upd = [c for c in db.calls if "UPDATE" in c[0]][0]
    assert "id = :mid" in upd[0]
    assert upd[1]["mid"] == "msg-1"


async def test_unowned_message_is_404() -> None:
    db = _snooze_db(None)
    with patch.object(m, "_tenant_session", bind_db(db)):
        with pytest.raises(HTTPException) as ei:
            await m.snooze_message("msg-1", m.SnoozeRequest(until=None),
                                   user=_User())
    assert ei.value.status_code == 404
