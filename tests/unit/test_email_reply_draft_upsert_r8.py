"""R8 for one reply draft per conversation (owner directive, 2026-10-22).

The hermetic half is ``test_email_reply_draft_upsert.py``. This half runs the
real routes and the real SQL of ``drafting._upsert_reply_draft`` against the
phase-4-promoted two-org catalog of ``test_h3_rls_promotion_rehearsal``,
under FORCE RLS, as the role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS).
The provider is a mock, so no mail leaves.

1. Two chat drafts of one mail leave ONE draft row in the thread, and the
   second updates the provider draft that the first made.
   ``test_two_chat_drafts_leave_one_row``.
2. A draft that the member saved in the Email app is the draft that the chat
   updates. ``test_the_chat_updates_the_members_draft``.
3. A Gmail-like new id moves the row by its row id.
   ``test_a_new_provider_id_moves_the_row``.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_reply_draft_upsert_r8.py -v -rs
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from email_ingestion.providers.outlook import OutlookProvider
from gateway.routes.email.automation import drafting
from sqlalchemy import text

from tests.unit.test_email_drafts_keep_html import _box, _drop, _seed_mail
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv
from tests.unit.test_gmail_send_and_drafts import RAVI, _as_member
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _outlook(update_ids: list[str] | None = None) -> OutlookProvider:
    """The real Outlook class with each draft call mocked. ``update_ids``
    gives the id that each update returns, in order (a Gmail-like move)."""
    box = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    box.create_draft = AsyncMock(return_value="AAMk-1")  # type: ignore[method-assign]
    if update_ids:
        box.update_draft = AsyncMock(side_effect=update_ids)  # type: ignore[method-assign]
    else:
        box.update_draft = AsyncMock(side_effect=lambda pmid, **_k: pmid)  # type: ignore[method-assign]
    return box


def _thread_drafts(admin: Any, account_id: str) -> list[Any]:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT id::text AS id, provider_message_id, body_text FROM email_messages"
            " WHERE account_id = CAST(:a AS uuid) AND thread_id = 't-r'"
            " AND folder = 'drafts' ORDER BY id"), {"a": account_id}).fetchall()


def _seed_member_draft(m: Any, pmid: str, body: str) -> str:
    with m.admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id,"
            " folder, subject, body_text, from_address, to_addresses, organization_id)"
            " VALUES (CAST(:a AS uuid), :p, 't-r', 'drafts', 'Re: Quote', :b,"
            " '{}'::jsonb, '[]'::jsonb, CAST(:o AS uuid)) RETURNING id::text"),
            {"a": m.aid, "p": pmid, "b": body, "o": m.org}).scalar_one())


def _wire(monkeypatch: pytest.MonkeyPatch, m: Any, texts: list[str]) -> None:
    async def context(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"subject": "Quote", "from": RAVI, "thread_id": "t-r",
                "self": m.owner, "body": "Can you check?"}

    async def about(*_a: Any, **_k: Any) -> tuple[str, str]:
        return "", ""

    async def draft(*_a: Any, **_k: Any) -> str:
        return texts.pop(0)

    async def keep(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(drafting, "_build_reply_context", context)
    monkeypatch.setattr(drafting, "_load_assistant_about", about)
    monkeypatch.setattr(drafting, "_agent_draft_reply", draft)
    monkeypatch.setattr(drafting, "_store_ai_draft", keep)


async def _chat_draft(m: Any, mail: str) -> dict[str, Any]:
    async with _as_member(m):
        return await drafting.draft_reply_smart(drafting.DraftReplyRequest(
            account_id=m.aid, message_id=mail, create_draft=True), user=m.me)


async def test_two_chat_drafts_leave_one_row(promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "outlook", _outlook)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        _wire(monkeypatch, m, ["First text.", "Second text."])
        assert (await _chat_draft(m, mail))["created"] is True
        assert (await _chat_draft(m, mail))["created"] is True
        [row] = _thread_drafts(m.admin, m.aid)
        assert row.provider_message_id == "AAMk-1"
        assert row.body_text.startswith("Second text.")
        first, second = m.providers
        first.create_draft.assert_awaited_once()
        second.create_draft.assert_not_awaited()
        assert second.update_draft.await_args.args[0] == "AAMk-1"
    finally:
        _drop(m)


async def test_the_chat_updates_the_members_draft(promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "outlook", _outlook)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        local = _seed_member_draft(m, "AAMk-mine", "Started in the Email app.")
        _wire(monkeypatch, m, ["The chat text."])
        await _chat_draft(m, mail)
        [row] = _thread_drafts(m.admin, m.aid)
        assert row.id == local and row.provider_message_id == "AAMk-mine"
        assert row.body_text.startswith("The chat text.")
        m.providers[0].create_draft.assert_not_awaited()
    finally:
        _drop(m)


async def test_a_new_provider_id_moves_the_row(promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _box(promoted, monkeypatch, "outlook", lambda: _outlook(["AAMk-moved"]))
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        local = _seed_member_draft(m, "AAMk-mine", "Old text.")
        _wire(monkeypatch, m, ["New text."])
        await _chat_draft(m, mail)
        [row] = _thread_drafts(m.admin, m.aid)
        assert (row.id, row.provider_message_id) == (local, "AAMk-moved")
        assert row.body_text.startswith("New text.")
    finally:
        _drop(m)
