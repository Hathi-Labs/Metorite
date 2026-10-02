"""Unit tests for Phase 7 — learning from the user's draft edits."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m

from tests.unit._email_fakes import bind_db


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert m._normalize_text("  Hi   THERE\n\n") == "hi there"


async def test_learn_from_sent_stores_memories_when_edited() -> None:
    # One row satisfies every query the function makes (draft, inbound, user).
    row = SimpleNamespace(
        draft_text="Hi,\n\nThanks for reaching out. Best regards, Vijay Varada",
        from_address={"email": "alice@acme.com"},
        body_text="Original incoming message", snippet="",
        user_id="u@example.com")
    res = MagicMock()
    res.fetchone.return_value = row
    db = AsyncMock()
    db.execute.return_value = res
    memories = AsyncMock(return_value=[
        {"content": "Keep sign-offs to my first name.", "kind": "PREFERENCE",
         "scope": "GLOBAL", "topic": ""},
    ])
    with patch.object(m.automation.drafting, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.drafting, "_llm_extract_reply_memories",
                         memories):
        await m._learn_from_sent("acc-1", "t1", "Hi,\n\nThanks. Best, Vijay")
    memories.assert_awaited()
    # delete-commit + insert-commit
    assert db.commit.await_count == 2


async def test_learn_skips_llm_when_unchanged() -> None:
    draft_row = SimpleNamespace(draft_text="Hi there, thanks!")
    res = MagicMock()
    res.fetchone.return_value = draft_row
    db = AsyncMock()
    db.execute.return_value = res
    llm = AsyncMock()
    with patch.object(m.automation.drafting, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.drafting, "_llm_extract_reply_memories", llm):
        # Same text (modulo whitespace) → nothing to learn.
        await m._learn_from_sent("acc-1", "t1", "Hi there,   thanks!")
    llm.assert_not_awaited()


async def test_learn_skips_llm_when_only_quote_was_appended() -> None:
    # The composer sends the drafted body + the quoted chain concatenated. That
    # is NOT an edit — stripping the quote must make it equal the stored draft so
    # no extraction fires (and the correspondent's quoted prose never leaks into
    # learned preferences).
    draft_row = SimpleNamespace(draft_text="Hi Alice,\n\nSounds good — Friday works.")
    res = MagicMock()
    res.fetchone.return_value = draft_row
    db = AsyncMock()
    db.execute.return_value = res
    llm = AsyncMock()
    sent = (
        "Hi Alice,\n\nSounds good — Friday works.\n\n"
        "On Mon, 21 Jul 2026, Alice <alice@acme.com> wrote:\n"
        "> Are you free Friday to review the quote?\n"
    )
    with patch.object(m.automation.drafting, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.drafting, "_llm_extract_reply_memories", llm):
        await m._learn_from_sent("acc-1", "t1", sent)
    llm.assert_not_awaited()


async def test_learn_noop_when_no_stored_draft() -> None:
    res = MagicMock()
    res.fetchone.return_value = None
    db = AsyncMock()
    db.execute.return_value = res
    llm = AsyncMock()
    with patch.object(m.automation.drafting, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.drafting, "_llm_extract_reply_memories", llm):
        await m._learn_from_sent("acc-1", "t1", "anything")
    llm.assert_not_awaited()


async def test_store_ai_draft_upserts() -> None:
    db = AsyncMock()
    await m._store_ai_draft(db, "acc-1", "t1", "Draft body")
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


async def test_store_ai_draft_skips_empty() -> None:
    db = AsyncMock()
    await m._store_ai_draft(db, "acc-1", "", "body")   # no thread id
    await m._store_ai_draft(db, "acc-1", "t1", "   ")  # blank body
    db.execute.assert_not_awaited()
