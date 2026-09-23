"""Unit tests for the inbox digest generator.

The DB session is mocked, so the SQL and bound params are inspected rather than
executed. Focus: the two honesty fixes — the "awaiting your reply" count reads
the Reply Zero status table (not an all-time inbox heuristic), and the category
filter normalises the user's selections to canonical cleanup categories.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m


def _fake_db(captured: list[tuple[str, dict]]):
    async def fake_execute(stmt, params=None):
        sql = str(stmt)
        captured.append((sql, params or {}))
        r = MagicMock()
        if "FROM email_accounts" in sql:
            r.fetchone.return_value = SimpleNamespace(self="me@fracktal.in")
        elif "COUNT(*) AS total" in sql:
            r.fetchone.return_value = SimpleNamespace(
                total=10, unread=3, inbox=8, attachments=2)
        elif "email_thread_status" in sql:
            r.scalar.return_value = 4
            r.fetchall.return_value = []
        else:
            r.fetchall.return_value = []
            r.fetchone.return_value = None
            r.scalar.return_value = 0
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    return db


async def test_needs_reply_reads_thread_status_not_inbox_heuristic() -> None:
    captured: list[tuple[str, dict]] = []
    out = await m.digest._generate_digest(_fake_db(captured), "acc-1", 7)
    sql = " ".join(s for s, _ in captured)
    # Reads the Reply Zero status table for NEEDS_REPLY …
    assert "email_thread_status" in sql
    assert "NEEDS_REPLY" in sql
    # … and NOT the old all-time "latest message in inbox" heuristic.
    assert "WITH latest AS" not in sql
    assert out["totals"]["needs_reply"] == 4


async def test_category_filter_normalises_and_drops_non_categories() -> None:
    """The configured rule names normalise through the canonicaliser and filter
    the COMPOSED analytics aggregate's rows — since 2.7 the filter is a
    projection over the one shared computation, not a second SQL variant."""
    from unittest.mock import patch

    from gateway.routes.email.automation import analytics as an

    captured: list[tuple[str, dict]] = []
    db = _fake_db(captured)
    rows = [
        {"category": "Cold Email", "count": 3, "prev_count": 0},
        {"category": "Newsletter", "count": 5, "prev_count": 1},
        {"category": "Receipt", "count": 2, "prev_count": 0},
        {"category": "Reply", "count": 1, "prev_count": 0},
    ]
    with patch.object(an, "_categories", AsyncMock(return_value=rows)):
        out = await m.digest._generate_digest(
            db, "acc-1", 7,
            # rule-name variants: plural alias, wrong casing/whitespace, and a
            # name that isn't a category at all.
            ["Cold Emails", " newsletter ", "My weekly roundup"],
        )
    # Only the normalised, de-junked selections survive; the thread-status row
    # ("Reply") and unselected categories are projected out.
    assert [r["category"] for r in out["by_category"]] == [
        "Cold Email", "Newsletter"]


# ── 2.7: projection + honesty ────────────────────────────────────────────────

def _fake_db_with_totals(captured: list[tuple[str, dict]]):
    """Like _fake_db but returns real inbox totals for the new aggregate query."""
    async def fake_execute(stmt, params=None):
        sql = str(stmt)
        captured.append((sql, params or {}))
        r = MagicMock()
        if "FROM email_accounts" in sql:
            r.fetchone.return_value = SimpleNamespace(self="me@fracktal.in")
        elif "COUNT(*) AS inbox" in sql:
            r.fetchone.return_value = SimpleNamespace(
                inbox=8, unread=3, attachments=2)
        elif "email_thread_status" in sql:
            r.scalar.return_value = 4
            r.fetchall.return_value = []
        else:
            r.fetchall.return_value = []
            r.fetchone.return_value = None
            r.scalar.return_value = 0
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    return db


async def test_every_aggregate_excludes_the_accounts_own_mail() -> None:
    # The digest email itself lands in the inbox from the account; counting it
    # (or self-notes / BCC-to-self) would inflate the next digest. Every windowed
    # aggregate excludes self and binds :self.
    captured: list[tuple[str, dict]] = []
    await m.digest._generate_digest(_fake_db_with_totals(captured), "acc-1", 7)
    # The WINDOWED inbox aggregates bind :days (the digest window). The backlog-
    # aging / commitments queries read thread status + tasks, not recent inbox
    # mail, so they aren't windowed and don't carry the self-exclusion.
    windowed = [(s, p) for s, p in captured
                if "email_messages em" in s and "days" in p]
    assert windowed, "expected the windowed inbox aggregates"
    for sql, params in windowed:
        assert "from_address->>'email') <> :self" in sql, sql[:120]
        assert params.get("self") == "me@fracktal.in"


async def test_generate_digest_returns_totals_and_both_bodies() -> None:
    out = await m.digest._generate_digest(
        _fake_db_with_totals([]), "acc-1", 1)
    # needs_reply AND awaiting: the brief reports both sides of the ledger
    # (what you owe, and what's owed to you).
    assert out["totals"] == {
        "inbox": 8, "unread": 3, "attachments": 2,
        "needs_reply": 4, "awaiting": 4}
    # Both a Markdown and an HTML body are produced for the email.
    assert "Inbox digest" in out["markdown"]
    assert out["html"].startswith("<div") and "8</b> new in inbox" in out["html"]


def test_empty_digest_is_detected() -> None:
    empty = {"totals": {"inbox": 0, "unread": 0, "attachments": 0,
                        "needs_reply": 0},
             "by_category": [], "top_senders": []}
    assert m.digest._digest_is_empty(empty) is True
    # Any new mail OR anything awaiting a reply makes it non-empty.
    assert m.digest._digest_is_empty({**empty,
        "totals": {**empty["totals"], "inbox": 1}}) is False
    assert m.digest._digest_is_empty({**empty,
        "totals": {**empty["totals"], "needs_reply": 1}}) is False


def test_digest_html_escapes_sender_and_category_text() -> None:
    html = m.digest._render_digest_html(
        "day", {"inbox": 1, "unread": 0, "attachments": 0}, 0,
        [{"category": "A & <b>", "count": 1}],
        [{"name": "<script>", "email": "x@y.com", "count": 2}])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; &lt;b&gt;" in html


# ── 3.11: the daily brief — backlog aging + commitments due ───────────────────


def _one_shot_db(rows: list):
    """A DB whose single execute returns `rows` from fetchall."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=rows))
    return db


def _thread_row(**over):
    base = dict(thread_id="t1", last_message_id="m1", subject="Invoice?",
                from_name="Ada", from_email="ada@x.com",
                to_name=None, to_email=None, age_days=5,
                high=False, is_read=True)
    base.update(over)
    return SimpleNamespace(**base)


def _thread_list_db(rows: list):
    """First execute returns the thread rows; the account-self lookup that
    follows gets a fetchone."""
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=rows),
        fetchone=MagicMock(return_value=SimpleNamespace(self="me@x.com")))
    return db


async def test_backlog_aging_reads_needs_reply_oldest_first() -> None:
    db = _thread_list_db([_thread_row()])
    out = await m.digest._digest_backlog_aging(db, "acc-1")
    sql = str(db.execute.call_args_list[0][0][0])
    assert "status = 'NEEDS_REPLY'" in sql
    assert "ORDER BY ts.last_message_at ASC" in sql   # oldest first (the brief)
    # Rows carry identity for the dashboard: the thread AND its last message,
    # plus WHO the loop is with (sender, since the last message is theirs), and
    # the priority signals the dashboard badges.
    assert out == [{"subject": "Invoice?", "age_days": 5, "thread_id": "t1",
                    "message_id": "m1", "who": "Ada",
                    "important": False, "unread": False}]


async def test_morning_brief_is_off_by_default_no_llm_call() -> None:
    # The opt-in one-liner must cost nothing unless enabled: with the flag off
    # (or unset), _digest_brief returns "" without calling the model.
    called = {"llm": False}

    async def fake_llm(*a, **k):
        called["llm"] = True
        return {"brief": "x"}, "", {}

    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(morning_brief_enabled=False)))
    with patch.object(m.digest, "_llm_json", fake_llm):
        out = await m.digest._digest_brief(
            db, "acc-1", [{"subject": "Invoice?"}], [])
    assert out == "" and called["llm"] is False


async def test_morning_brief_generates_when_enabled() -> None:
    async def fake_llm(*a, **k):
        return {"brief": "2 urgent: Ada's invoice, the contract."}, "", {}

    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(morning_brief_enabled=True)))
    with patch.object(m.digest, "_llm_json", fake_llm):
        out = await m.digest._digest_brief(
            db, "acc-1", [{"subject": "Invoice?", "who": "Ada", "age_days": 3}], [])
    assert "urgent" in out


async def test_dashboard_backlog_ranks_by_priority_not_age() -> None:
    # The live dashboard (prioritized=True) orders the reply queue by an urgency
    # score — importance + unread + capped age — so an important unread thread
    # from today outranks an ancient dead loop. The emailed brief stays
    # oldest-first (asserted above).
    db = _thread_list_db([_thread_row(high=True, is_read=False)])
    out = await m.digest._digest_backlog_aging(db, "acc-1", prioritized=True)
    sql = str(db.execute.call_args_list[0][0][0])
    assert "importance" in sql.lower() and "DESC" in sql
    assert "ORDER BY ts.last_message_at ASC" not in sql  # not the plain-age order
    assert out[0]["important"] is True and out[0]["unread"] is True


async def test_thread_lists_ignore_trashed_threads() -> None:
    """A thread whose last message the user trashed/junked is not an open loop.
    Live: a trashed sick-leave thread sat in "awaiting your reply" for weeks."""
    db = _thread_list_db([])
    await m.digest._digest_backlog_aging(db, "acc-1")
    sql = str(db.execute.call_args_list[0][0][0])
    assert "'trash'" in sql and "'junk'" in sql
    # …and so does the count the stat row shows.
    db2 = AsyncMock()
    db2.execute.return_value = MagicMock(scalar=MagicMock(return_value=0))
    await m.digest._digest_needs_reply(db2, "acc-1")
    assert "'trash'" in str(db2.execute.call_args[0][0])


async def test_thread_lists_ignore_snoozed_threads() -> None:
    """A currently-snoozed conversation is not a live open loop: the inbox hides
    it, and the dashboard's Snooze action must actually remove the row — before
    this, the optimistic drop was undone by the quiet re-sync and the thread
    (and its count) came straight back. It reappears once snoozed_until passes."""
    db = _thread_list_db([])
    await m.digest._digest_backlog_aging(db, "acc-1")
    sql = str(db.execute.call_args_list[0][0][0])
    assert "snoozed_until > now()" in sql
    # The stat-row counts share the same predicate.
    db2 = AsyncMock()
    db2.execute.return_value = MagicMock(scalar=MagicMock(return_value=0))
    await m.digest._digest_needs_reply(db2, "acc-1")
    assert "snoozed_until > now()" in str(db2.execute.call_args[0][0])


async def test_awaiting_reads_the_other_side_of_the_ledger() -> None:
    """"Waiting on them" = AWAITING threads; when the last message is the
    user's own, the counterparty shown is its recipient."""
    db = _thread_list_db([_thread_row(
        from_name=None, from_email="me@x.com", to_name="Bob",
        to_email="bob@y.com")])
    out = await m.digest._digest_awaiting(db, "acc-1")
    sql = str(db.execute.call_args_list[0][0][0])
    assert "status = 'AWAITING'" in sql
    assert out[0]["who"] == "Bob"


async def test_commitments_are_best_effort_when_tasks_absent() -> None:
    # The tasks feature may be absent in a deploy; a digest must never fail
    # on it.
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("relation pm_tasks does not exist")
    assert await m.digest._digest_commitments(db, "acc-1") == []
    # …and best-effort means the SESSION survives: a failed query aborts the
    # transaction, so without a rollback every later query in the request dies
    # with InFailedSQLTransaction (how /digest/send 500'd in prod while the
    # preview looked fine — the preview simply had no queries after this one).
    db.rollback.assert_awaited_once()


class _Seam:
    """The one task seam (WS-39 S8c), answering `items_by_origin`."""

    name = "recording"

    def __init__(self, items):
        self.items = items
        self.calls = []

    async def items_by_origin(self, db, uid, key, value, **kw):
        self.calls.append((uid, key, value, kw))
        return list(self.items)


def _seam(monkeypatch, items):
    from gateway.routes.tasks import item_source as seam

    src = _Seam(items)
    monkeypatch.setattr(seam, "item_source", lambda: src)
    return src


def _commitments_db(latest=()):
    """The owner lookup, then the latest-message lookup per thread."""
    calls = []

    async def execute(stmt, params=None):
        sql = str(stmt)
        calls.append((sql, params or {}))
        r = MagicMock()
        if "FROM email_accounts" in sql:
            r.fetchone.return_value = SimpleNamespace(user_id="me@fracktal.in")
        else:
            r.fetchall.return_value = [
                SimpleNamespace(thread_id=t, message_id=mid)
                for t, mid in latest]
        return r

    db = AsyncMock()
    db.execute.side_effect = execute
    db.calls = calls
    return db


def _task(tid, title, due=None, thread=None):
    origin = {"kind": "email", "account_id": "acc-1"}
    if thread:
        origin["thread_id"] = thread
    return SimpleNamespace(id=tid, title=title, due_at=due, origin=origin)


async def test_commitments_read_the_one_seam_for_the_accounts_owner(
    monkeypatch,
) -> None:
    """WS-39 S8c: the tasks are the account OWNER'S, keyed on
    `origin.account_id`, and read through `item_source()`. Which tasks are
    open is the seam's rule, not a query here."""
    now = datetime.now(UTC)
    overdue = now - timedelta(days=2)
    src = _seam(monkeypatch, [
        _task("task-1", "Send quote", due=overdue, thread="t9"),
        _task("task-2", "Far away", due=now + timedelta(days=30)),
        _task("task-3", "Call back"),
    ])
    db = _commitments_db(latest=[("t9", "m9")])
    out = await m.digest._digest_commitments(db, "acc-1")
    assert src.calls == [("me@fracktal.in", "account_id", "acc-1", {})]
    # Horizon 3 days: the far task and the undated one are left out.
    assert out == [{"title": "Send quote", "due": overdue.strftime("%b %d"),
                    "overdue": True, "task_id": "task-1", "thread_id": "t9",
                    "message_id": "m9"}]
    # message_id (the latest message of the linked thread) lets the dashboard
    # open the email the commitment came from. One query for all threads.
    (sql, params), = [c for c in db.calls if "email_messages" in c[0]]
    assert "DISTINCT ON (em.thread_id)" in sql
    assert params == {"aid": "acc-1", "tids": ["t9"]}
    assert not any("gtd_" in c[0] for c in db.calls)


async def test_dashboard_mode_includes_undated_commitments(monkeypatch) -> None:
    """3 of the 4 live commitments had no due date and were invisible to the
    horizon filter. The dashboard (full) projection includes them; the emailed
    brief keeps the due-soon horizon."""
    soon = datetime.now(UTC) + timedelta(days=1)
    _seam(monkeypatch, [_task("task-2", "Call back"),
                        _task("task-1", "Soon", due=soon)])
    db = _commitments_db()
    out = await m.digest._digest_commitments(
        db, "acc-1", include_undated=True)
    # Dated first, undated last: the order the SQL gave with NULLS LAST.
    assert [c["task_id"] for c in out] == ["task-1", "task-2"]
    assert out[1]["due"] is None and out[1]["overdue"] is False
    # No linked thread → message_id null, row not openable, and no lookup.
    assert out[1]["message_id"] is None
    assert not [c for c in db.calls if "email_messages" in c[0]]
    # …and the renderers say "no due date" instead of crashing on None.
    assert "no due date" in m.digest._due_phrase(out[1])


async def test_commitments_limit_applies_after_the_order(monkeypatch) -> None:
    now = datetime.now(UTC)
    _seam(monkeypatch, [
        _task(f"task-{i}", f"T{i}", due=now + timedelta(hours=10 - i))
        for i in range(5)])
    out = await m.digest._digest_commitments(
        _commitments_db(), "acc-1", limit=2)
    assert [c["task_id"] for c in out] == ["task-4", "task-3"]


async def test_commitments_for_an_unknown_account_are_empty(
    monkeypatch,
) -> None:
    src = _seam(monkeypatch, [_task("task-1", "x")])
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
    assert await m.digest._digest_commitments(db, "acc-x") == []
    assert not src.calls


def test_a_quiet_inbox_with_commitments_still_sends() -> None:
    # The point of a daily brief: even a day with no new mail is worth sending if
    # I owe something. Commitments (or an aging backlog) keep it non-empty.
    base = {"totals": {"inbox": 0, "unread": 0, "attachments": 0,
                       "needs_reply": 0},
            "by_category": [], "top_senders": [], "backlog": []}
    assert m.digest._digest_is_empty(base) is True
    assert m.digest._digest_is_empty(
        {**base, "commitments": [{"title": "x", "due": "Jul 25",
                                  "overdue": False}]}) is False
    assert m.digest._digest_is_empty(
        {**base, "backlog": [{"subject": "y", "age_days": 3}]}) is False


def test_brief_sections_render_in_both_bodies() -> None:
    backlog = [{"subject": "Contract review", "age_days": 4}]
    commitments = [{"title": "Send the deck", "due": "Jul 25", "overdue": True}]
    md = m.digest._render_digest_markdown(
        "day", {"inbox": 1, "unread": 0, "attachments": 0}, 2,
        [], [], backlog, commitments)
    assert "Commitments due" in md and "Send the deck" in md
    assert "Awaiting your reply" in md and "Contract review" in md
    html = m.digest._render_digest_html(
        "day", {"inbox": 1, "unread": 0, "attachments": 0}, 2,
        [], [], backlog, commitments)
    assert "Commitments due" in html and "overdue" in html
    assert "Contract review" in html
