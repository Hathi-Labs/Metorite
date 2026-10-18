"""Processing past emails is the one surface that spends a model call per
message on a range the user types into a date box.

Two things were missing between the picker and the Process button: a number, and
a ceiling. The only way to discover a range covered 4,000 emails was to spend
4,000 AI calls finding out — and once "Clean older mail" could pull a whole
43,000-message mailbox into the local store, an open-ended range stopped meaning
"a few thousand" and started meaning "everything ever received".

    "When processing past emails with AI, display a warning that extensive
     categorization can be costly; limit processing to a few months up to a
     year."                                                    — 2026-07-20

The cap is enforced in the API, not just the dialog: a bound that lives only in
the client is not a bound.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from gateway.routes.email.automation import runner as m
from tests.unit._email_fakes import bind_db

_ACC = "a0000000-0000-4000-8000-00000000c057"
_USER = SimpleNamespace(email="u@example.com")


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# ── the ceiling ─────────────────────────────────────────────────────────────


def test_a_year_is_allowed() -> None:
    """The user's own ceiling: "a few months up to a year"."""
    assert m._assert_span_within_cap(_d("2025-08-01"), _d("2026-07-01")) <= 366


def test_beyond_a_year_is_refused() -> None:
    with pytest.raises(HTTPException) as e:
        m._assert_span_within_cap(_d("2021-01-01"), _d("2026-07-01"))
    assert e.value.status_code == 400
    assert "AI call" in e.value.detail, (
        "the refusal has to say WHY, or it reads as an arbitrary limit"
    )


def test_an_open_ended_range_is_refused() -> None:
    """No start date is not "a small range" — it is every message ever received.
    Before the history backfill existed that meant one year of mail; now it means
    the whole mailbox."""
    with pytest.raises(HTTPException) as e:
        m._assert_span_within_cap(None, _d("2026-07-01"))
    assert e.value.status_code == 400
    assert "start date" in e.value.detail


def test_an_open_ended_END_is_measured_to_today() -> None:
    """Only the start is required — an omitted end means "up to now", which is
    bounded. It must still be measured, not waved through."""
    with pytest.raises(HTTPException):
        m._assert_span_within_cap(
            datetime.now(timezone.utc) - timedelta(days=900), None)


async def test_the_endpoint_enforces_the_cap_before_touching_the_db() -> None:
    """A bound checked after the work has begun is not a bound. This also keeps
    the refusal cheap: no connection, no owner check, no count."""
    seam = bind_db(AsyncMock())
    with patch.object(m, "_tenant_session", seam):
        with pytest.raises(HTTPException) as e:
            await m.process_past_emails(
                m.RuleProcessPastRequest(
                    account_id=_ACC, start_date="2019-01-01",
                    end_date="2026-07-01"),
                background=BackgroundTasks(), user=_USER)
    assert e.value.status_code == 400
    assert seam.calls == []  # no session was ever opened


async def test_a_refused_range_schedules_nothing() -> None:
    bg = BackgroundTasks()
    with patch.object(m, "_tenant_session", bind_db(AsyncMock())), \
            patch.object(m, "_assert_account_owner", AsyncMock()):
        with pytest.raises(HTTPException):
            await m.process_past_emails(
                m.RuleProcessPastRequest(account_id=_ACC),
                background=bg, user=_USER)
    assert bg.tasks == []


# ── the number ──────────────────────────────────────────────────────────────


def _db_returning(*counts: int) -> AsyncMock:
    db = AsyncMock()
    results = [MagicMock(fetchone=MagicMock(
        return_value=SimpleNamespace(c=n))) for n in counts]
    db.execute.side_effect = results
    return db


async def _estimate(*counts: int, limit: int = 1000) -> dict:
    db = _db_returning(*counts)
    with patch.object(m, "_tenant_session", bind_db(db)), \
            patch.object(m, "_assert_account_owner", AsyncMock()):
        return await m.process_past_estimate(
            account_id=_ACC, start_date="2026-01-01", end_date="2026-07-01",
            limit=limit, user=_USER)


async def test_the_estimate_reports_what_will_actually_be_sent() -> None:
    # in_range=500, eligible=300, held_back=50
    res = await _estimate(500, 300, 50)
    assert res["in_range"] == 500
    assert res["eligible"] == 300
    assert res["already_processed"] == 200
    assert res["will_process"] == 300
    assert res["capped"] is False


async def test_the_silent_truncation_is_surfaced() -> None:
    """The job reads LIMIT :limit rows and the tracker then reports that figure
    as the total — so a 4,000-email range looked like it "processed everything"
    when it had processed the oldest 1,000. Say so up front instead."""
    res = await _estimate(4000, 4000, 0)
    assert res["will_process"] == 1000
    assert res["capped"] is True, (
        "a range wider than the row limit must be flagged, or the run reports "
        "a truncated total as a complete one"
    )


async def test_held_back_history_is_counted_separately() -> None:
    """Mail "Clean older mail" fetched and kept away from the model is eligible
    here — a deliberate, bounded run is exactly what the hold-back leaves room
    for — but the user should know the range is mostly fresh history."""
    res = await _estimate(900, 900, 850)
    assert res["held_back"] == 850
    assert res["eligible"] == 900


async def test_the_estimate_asks_the_db_for_held_back_mail_explicitly() -> None:
    db = _db_returning(10, 10, 3)
    with patch.object(m, "_tenant_session", bind_db(db)), \
            patch.object(m, "_assert_account_owner", AsyncMock()):
        await m.process_past_estimate(
            account_id=_ACC, start_date="2026-01-01", user=_USER)
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "rules_held_back_at IS NOT NULL" in sql
