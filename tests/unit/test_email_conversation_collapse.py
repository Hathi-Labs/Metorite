"""Conversation collapse in the mailbox list (review 3.2).

The browse list reads at thread level: a conversation is ONE row (its newest
message in view), and the total counts conversations, not messages — the UI half
of the one-classification-per-conversation invariant. But a thread *load*
(thread_id given) and the assistant's per-message inbox queries must stay
un-collapsed. These pin which SQL each path emits.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes.email.transport import messages as m
from tests.unit._email_fakes import bind_db


class _User:
    email = "u@example.com"


def _capture_db():
    """A db whose execute() records every SQL string and returns an empty page
    (scalar()→0 for the count, fetchall()→[] for the fetch)."""
    seen: list[str] = []
    db = AsyncMock()

    async def _exec(sql, params=None):
        seen.append(str(sql))
        return MagicMock(scalar=MagicMock(return_value=0),
                         fetchall=MagicMock(return_value=[]))

    db.execute.side_effect = _exec
    return db, seen


# FastAPI Query() defaults don't resolve on a direct call — the params arrive as
# Query objects (truthy), so pass every optional filter its real "off" value.
_OFF = {
    "account_id": None, "folder": "INBOX", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}


async def _run(**kw):
    args = {**_OFF, **kw}
    db, seen = _capture_db()
    with patch.object(m, "_tenant_session", bind_db(db)):
        out = await m.list_messages(user=_User(), **args)
    return out, seen


async def test_collapse_dedupes_by_conversation_and_counts_conversations() -> None:
    _out, seen = await _run(account_id="a", folder="INBOX", collapse=True)
    joined = "\n".join(seen)
    # One row per conversation, newest-in-view first. The key names the
    # mailbox, so a conversation never spans two mailboxes (EM-T8d, MB-12).
    key = "(em.account_id::text || ':' || COALESCE(em.thread_id, em.id::text))"
    assert f"DISTINCT ON ({key})" in joined
    assert f"ORDER BY {key}, em.received_at DESC" in joined
    # Total is conversations, not messages.
    assert f"COUNT(DISTINCT {key})" in joined


async def test_default_is_per_message_for_the_assistant() -> None:
    # collapse defaults off → the assistant's inbox queries stay per-message.
    _out, seen = await _run(account_id="a", folder="INBOX")
    joined = "\n".join(seen)
    assert "DISTINCT ON" not in joined
    assert "COUNT(*)" in joined


async def test_thread_load_never_collapses() -> None:
    # Even with collapse=true, loading a specific thread returns every message.
    _out, seen = await _run(account_id="a", thread_id="t-1", collapse=True)
    joined = "\n".join(seen)
    assert "DISTINCT ON" not in joined
    assert "em.thread_id = :thread_id" in joined
