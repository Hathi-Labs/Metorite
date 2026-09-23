"""Calendar-aware scheduling replies (review 3.10).

When an incoming email asks about timing, the drafter should see the owner's
upcoming hard-date commitments (the internal calendar) so it can offer clashing-
free slots and never double-book. Only then — an ordinary reply must not carry
the schedule. These pin the intent heuristic and the calendar fetch.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.routes.email.automation import drafting as d


def test_detects_scheduling_questions() -> None:
    assert d._asks_about_scheduling("Meeting next week?", "Are you available?")
    assert d._asks_about_scheduling("", "when are you free to hop on a call?")
    assert d._asks_about_scheduling("Re: reschedule", "")
    assert d._asks_about_scheduling("Quick sync", "let's meet to go over it")


def test_ignores_ordinary_mail() -> None:
    assert not d._asks_about_scheduling(
        "Invoice #42", "Please find the invoice attached, thanks.")
    assert not d._asks_about_scheduling("Hello", "just saying thanks!")


class _Seam:
    """The one task seam (WS-39 S8c), answering `hard_dated_items`."""

    name = "recording"

    def __init__(self, items, error=None):
        self.items = items
        self.error = error
        self.calls = []

    async def hard_dated_items(self, db, uid, *, days, limit):
        self.calls.append((uid, days, limit))
        if self.error:
            raise self.error
        return list(self.items)


def _seam(monkeypatch, items, error=None):
    from gateway.routes.tasks import item_source as seam

    src = _Seam(items, error)
    monkeypatch.setattr(seam, "item_source", lambda: src)
    return src


def _owner_db(user_id="me@fracktal.in"):
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(
        return_value=SimpleNamespace(user_id=user_id) if user_id else None))
    return db


async def test_calendar_fetch_reads_upcoming_hard_dates(monkeypatch) -> None:
    src = _seam(monkeypatch, [
        SimpleNamespace(due_at=datetime(2026, 7, 24, 14, 0, tzinfo=UTC),
                        title="Board meeting"),
        SimpleNamespace(due_at=datetime(2026, 7, 25, 9, 30, tzinfo=UTC),
                        title="Dentist"),
    ])
    db = _owner_db()
    out = await d._fetch_calendar_context(db, "acc-1")
    # THIS account's user, through the one seam. Open hard-date items in the
    # upcoming window is the seam's rule (the Calendar's predicate).
    assert src.calls == [("me@fracktal.in", 10, 15)]
    sql = str(db.execute.await_args[0][0])
    assert "FROM email_accounts" in sql and "gtd_" not in sql
    assert out == ("- Fri Jul 24, 14:00 — Board meeting\n"
                   "- Sat Jul 25, 09:30 — Dentist")


async def test_calendar_fetch_is_best_effort(monkeypatch) -> None:
    # The tasks feature may be absent; a draft must never fail on the calendar.
    _seam(monkeypatch, [], RuntimeError("relation pm_tasks does not exist"))
    assert await d._fetch_calendar_context(_owner_db(), "acc-1") == ""


async def test_no_scheduled_items_returns_empty(monkeypatch) -> None:
    _seam(monkeypatch, [])
    assert await d._fetch_calendar_context(_owner_db(), "acc-1") == ""


async def test_an_unknown_account_has_no_calendar(monkeypatch) -> None:
    src = _seam(monkeypatch, [SimpleNamespace(due_at=None, title="x")])
    assert await d._fetch_calendar_context(_owner_db(None), "acc-x") == ""
    assert not src.calls
