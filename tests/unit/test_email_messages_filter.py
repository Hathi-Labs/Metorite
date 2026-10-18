"""Unit tests for the message-list label filter (Phase A — label UX).

Verifies `list_messages` adds the `= ANY(...)` membership clause and a bound
`label` param only when a label is supplied. The DB session is mocked, so the
SQL is inspected rather than executed.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m
from tests.unit._email_fakes import bind_db

_ROOT = Path(__file__).resolve().parents[2]


async def _run_list(label):
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.scalar.return_value = 0     # count query
        r.fetchall.return_value = []  # page query → no rows
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        resp = await m.list_messages(
            account_id="a0000000-0000-4000-8000-000000000001", folder="inbox", label=label,
            query=None, thread_id=None,
            received_after=None, received_before=None, is_read=None,
            is_starred=None, has_attachments=None, importance=None,
            from_email=None, sender_category=None, sort="newest",
            collapse=False, page=1, page_size=50, user=user,
        )
    return resp, captured


async def test_label_filter_adds_any_clause_and_bound_param():
    resp, captured = await _run_list("Newsletter")
    sql = " ".join(s for s, _ in captured)
    assert "= ANY(em.categories)" in sql
    assert "ANY(COALESCE(em.labels" in sql
    assert any(p.get("label") == "Newsletter" for _, p in captured)
    assert resp["total"] == 0


async def test_no_label_means_no_label_clause():
    _, captured = await _run_list(None)
    sql = " ".join(s for s, _ in captured)
    assert "ANY(em.categories)" not in sql
    assert all("label" not in p for _, p in captured)


# ── Rich inbox-query filters (date / state / sender / category / sort) ────────

async def _run_list_full(**kw):
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.scalar.return_value = 0
        r.fetchall.return_value = []
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    user = SimpleNamespace(email="u@example.com")
    args = dict(
        account_id="a0000000-0000-4000-8000-000000000001", folder="inbox", label=None, query=None,
        thread_id=None, received_after=None, received_before=None, is_read=None,
        is_starred=None, has_attachments=None, importance=None, from_email=None,
        sender_category=None, sort="newest", collapse=False,
        page=1, page_size=50, user=user,
    )
    args.update(kw)
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        resp = await m.list_messages(**args)
    return resp, captured


async def test_no_filters_keeps_default_clauses():
    _resp, captured = await _run_list_full()
    sql = " ".join(s for s, _ in captured)
    assert "received_after" not in sql
    assert "is_read = :is_read" not in sql
    assert "email_senders se" not in sql
    assert "ORDER BY em.received_at DESC" in sql


async def test_date_state_and_sender_filters():
    from datetime import datetime
    _resp, captured = await _run_list_full(
        received_after="2026-05-01",
        received_before="2026-06-01T00:00:00Z",
        is_read=False, from_email="Acme.com", sender_category="Marketing",
        importance="high",
    )
    sql = " ".join(s for s, _ in captured)
    params: dict = {}
    for _s, p in captured:
        params.update(p)
    assert "em.received_at >= :received_after" in sql
    assert "em.received_at <= :received_before" in sql
    assert "em.is_read = :is_read" in sql
    assert "LOWER(em.from_address->>'email') LIKE :from_email" in sql
    assert "EXISTS (SELECT 1 FROM email_senders se" in sql
    assert "LOWER(em.importance) = LOWER(:importance)" in sql
    assert params["is_read"] is False
    assert params["from_email"] == "%acme.com%"     # lowercased + wrapped
    assert params["sender_category"] == "Marketing"
    assert isinstance(params["received_after"], datetime)


async def test_query_uses_websearch_tsquery_not_plainto():
    # find_urgent sends "urgent OR deadline OR ASAP …"; plainto_tsquery ANDs
    # every term so it could never match. The /messages FTS must use
    # websearch_to_tsquery (matching transport/search.py) so OR works.
    #
    # Since the incident of 2026-10-18 the match runs in the SQL function
    # email_message_search (migration 286), so the grammar lives in that
    # file. The route passes the text through, unchanged, to the function.
    _resp, captured = await _run_list_full(query="urgent OR deadline OR ASAP")
    sql = " ".join(s for s, _ in captured)
    params: dict = {}
    for _s, p in captured:
        params.update(p)
    assert "public.email_message_search(:q, ARRAY(" in sql
    assert "plainto_tsquery" not in sql
    assert params["q"] == "urgent OR deadline OR ASAP"
    migration = next(
        (_ROOT / "infra" / "postgres").glob("*_email_search_index.sql")
    ).read_text(encoding="utf-8")
    body = migration.split("CREATE OR REPLACE FUNCTION public.email_message_search", 1)[1]
    assert body.count("websearch_to_tsquery('english', p_query)") == 2
    # Three paths: the ladder shape, the exact GIN path and the scoped path.
    # Each one reads only the given mailboxes and stops at the cap.
    assert body.count("m.account_id = ANY(p_accounts)") == 3
    assert body.count("LIMIT v_limit") == 3
    assert "plainto_tsquery" not in body


async def test_query_never_runs_an_inline_tsvector_under_rls():
    # Under FORCE RLS the app role cannot use a GIN index for an inline
    # `to_tsvector(...) @@`, because `@@` is not LEAKPROOF. The inline form
    # read every body of the org per call (incident 2026-10-18).
    resp, captured = await _run_list_full(query="Sanjeev Goel Payal Dave")
    sql = " ".join(s for s, _ in captured)
    assert "to_tsvector" not in sql
    # The match runs ONCE, scoped to the member's mailboxes, and both the
    # COUNT and the page bind its ids.
    calls = [s for s, _ in captured if "email_message_search" in s]
    assert len(calls) == 1
    assert "SELECT id FROM email_accounts WHERE user_id = :uid" in calls[0]
    shared = [s for s, _ in captured if "em.id = ANY(CAST(:match_ids AS uuid[]))" in s]
    assert len(shared) == 2
    assert resp["truncated"] is False and resp["match_cap"] == 20000


async def test_sort_importance_orders_by_priority_case():
    _resp, captured = await _run_list_full(sort="importance")
    sql = " ".join(s for s, _ in captured)
    assert "WHEN 'high' THEN 0" in sql
    assert "em.is_read ASC" in sql


# ── /email/priority ranked "important emails to check" ────────────────────────

async def test_priority_inbox_ranks_and_excludes_bulk():
    captured: list[tuple[str, dict]] = []

    async def fake_execute(stmt, params=None):
        captured.append((str(stmt), params or {}))
        r = MagicMock()
        r.fetchall.return_value = [SimpleNamespace(
            id="m1", thread_id="t1", subject="Contract review",
            from_address={"name": "Jo", "email": "jo@x.com"},
            received_at=None, is_read=False, importance="high",
            is_starred=False, reply_status="NEEDS_REPLY",
            sender_category="Personal", score=170)]
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.transport.messages, "_tenant_session",
                      bind_db(db)), \
            patch.object(m.transport.messages, "_assert_account_owner",
                         AsyncMock()):
        resp = await m.priority_inbox(
            account_id="a0000000-0000-4000-8000-000000000001", days=30, limit=20, user=user)

    sql = " ".join(s for s, _ in captured)
    assert "NEEDS_REPLY" in sql
    assert "NOT IN" in sql and "newsletter" in sql  # bulk senders excluded
    assert resp["count"] == 1
    e = resp["emails"][0]
    assert e["message_id"] == "m1"
    assert "needs reply" in e["reason"] and "unread" in e["reason"]
    assert e["score"] == 170


# ── Per-message attachments in a conversation load (thread_id) ────────────────

def _msg_row(*, has_attachments: bool):
    return SimpleNamespace(
        id="m1", provider_message_id="pm1", thread_id="t1", account_id="a0000000-0000-4000-8000-000000000001",
        folder="inbox", labels=[],
        from_address={"name": "Jo", "email": "jo@x.com"},
        to_addresses=[], cc_addresses=[], bcc_addresses=[], subject="Hi",
        body_text="b", body_html=None, snippet="b",
        has_attachments=has_attachments, is_read=True, is_starred=False,
        is_flagged=False, importance="normal", categories=[],
        received_at=None, synced_at=None,
    )


async def _run_thread_list(*, thread_id, msg_row, att_rows):
    """Drive list_messages with a routed mock so we can feed the page query a
    real message row, the batch attachment query its rows, and inspect both."""
    captured: list[str] = []

    async def fake_execute(stmt, params=None):
        sql = str(stmt)
        captured.append(sql)
        r = MagicMock()
        if "FROM email_attachments" in sql:
            r.fetchall.return_value = att_rows
        elif "GROUP BY thread_id" in sql:
            r.fetchall.return_value = []          # thread-count query
        elif "COUNT(*)" in sql:
            r.scalar.return_value = 1             # total
            r.fetchall.return_value = []
        else:
            r.fetchall.return_value = [msg_row]   # page query
        return r

    db = AsyncMock()
    db.execute.side_effect = fake_execute
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)):
        resp = await m.list_messages(
            account_id="a0000000-0000-4000-8000-000000000001", folder=None, label=None, query=None,
            thread_id=thread_id, received_after=None, received_before=None,
            is_read=None, is_starred=None, has_attachments=None, importance=None,
            from_email=None, sender_category=None, sort="oldest",
            collapse=False, page=1, page_size=50, user=user,
        )
    return resp, captured


async def test_thread_list_populates_per_message_attachments():
    # The conversation load batch-hydrates each message's attachments so an
    # earlier message's files are viewable, not just the open one.
    att = SimpleNamespace(
        message_id="m1", id="att1", filename="quote.pdf",
        mime_type="application/pdf", size_bytes=2048)
    resp, captured = await _run_thread_list(
        thread_id="t1", msg_row=_msg_row(has_attachments=True), att_rows=[att])

    assert any("FROM email_attachments" in s for s in captured)  # batched query ran
    atts = resp["emails"][0]["attachments"]
    assert len(atts) == 1
    assert atts[0]["id"] == "att1"
    assert atts[0]["filename"] == "quote.pdf"


async def test_folder_list_skips_attachment_hydration():
    # No thread_id (a folder list) → no per-message attachment query; the list
    # keeps only the has_attachments flag, staying lean.
    resp, captured = await _run_thread_list(
        thread_id=None, msg_row=_msg_row(has_attachments=True), att_rows=[])

    assert not any("FROM email_attachments" in s for s in captured)
    assert resp["emails"][0]["attachments"] == []
    assert resp["emails"][0]["has_attachments"] is True
