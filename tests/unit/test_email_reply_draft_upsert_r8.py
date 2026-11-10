"""R8 for one reply draft per conversation (owner directive, 2026-10-22).

The hermetic half is ``test_email_reply_draft_upsert.py``. This half runs the
real routes and the real SQL of ``drafting._upsert_reply_draft`` against the
phase-4-promoted two-org catalog of ``test_h3_rls_promotion_rehearsal``,
under FORCE RLS, as the role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS).
The provider is a mock, so no mail leaves. ``_store_ai_draft`` is real.

1. Two chat drafts of one mail leave ONE draft row in the thread, and the
   second updates the provider draft that the first made.
   ``test_two_chat_drafts_leave_one_row``.
2. A draft that the member saved in the Email app is never written over: it
   stays, and the chat's text goes beside it.
   ``test_the_members_draft_is_kept_and_a_new_one_made``.
3. A Gmail-like new id moves the row by its row id, and each save moves
   ``received_at``. ``test_a_new_provider_id_moves_the_row_and_its_received_at``.
4. The member's Discard trashes the card's own unedited draft only.
   ``test_discard_trashes_the_cards_unedited_draft_only``.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_reply_draft_upsert_r8.py -v -rs
"""
from __future__ import annotations

from types import SimpleNamespace
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


class _Box:
    """The live bodies of one fake Outlook mailbox, shared by each request."""

    def __init__(self, update_ids: list[str] | None = None) -> None:
        self.live: dict[str, str] = {}
        self.update_ids = list(update_ids or [])
        self.made: list[OutlookProvider] = []
        self.n = 0

    def provider(self) -> OutlookProvider:
        box = OutlookProvider({"access_token": "x", "refresh_token": "y"})

        async def create(**kw: Any) -> str:
            self.n += 1
            pmid = f"AAMk-{self.n}"
            self.live[pmid] = kw["body_text"]
            return pmid

        async def update(pmid: str, **kw: Any) -> str:
            new = self.update_ids.pop(0) if self.update_ids else pmid
            self.live[new] = kw.get("body_text") or ""
            return new

        async def body(pmid: str) -> Any:
            return SimpleNamespace(body_text=self.live.get(pmid, ""), body_html="")

        box.create_draft = AsyncMock(side_effect=create)  # type: ignore[method-assign]
        box.update_draft = AsyncMock(side_effect=update)  # type: ignore[method-assign]
        box.get_message_body = AsyncMock(side_effect=body)  # type: ignore[method-assign]
        box.trash_message = AsyncMock()  # type: ignore[method-assign]
        self.made.append(box)
        return box


def _thread_drafts(admin: Any, account_id: str) -> list[Any]:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT id::text AS id, provider_message_id, body_text, received_at"
            " FROM email_messages"
            " WHERE account_id = CAST(:a AS uuid) AND thread_id = 't-r'"
            " AND folder = 'drafts' ORDER BY received_at"), {"a": account_id}).fetchall()


def _seed_member_draft(m: Any, pmid: str, body: str) -> str:
    with m.admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id,"
            " folder, subject, body_text, from_address, to_addresses, organization_id,"
            " received_at)"
            " VALUES (CAST(:a AS uuid), :p, 't-r', 'drafts', 'Re: Quote', :b,"
            " '{}'::jsonb, '[]'::jsonb, CAST(:o AS uuid), now() + interval '1 hour')"
            " RETURNING id::text"),
            {"a": m.aid, "p": pmid, "b": body, "o": m.org}).scalar_one())


def _wire(monkeypatch: pytest.MonkeyPatch, m: Any, texts: list[str]) -> None:
    """The drafter is a fake. ``_store_ai_draft`` is REAL, so the record of
    the chat's text runs through Postgres under FORCE RLS."""
    async def context(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"subject": "Quote", "from": RAVI, "thread_id": "t-r",
                "self": m.owner, "body": "Can you check?"}

    async def about(*_a: Any, **_k: Any) -> tuple[str, str]:
        return "", ""

    async def draft(*_a: Any, **_k: Any) -> str:
        return texts.pop(0)

    monkeypatch.setattr(drafting, "_build_reply_context", context)
    monkeypatch.setattr(drafting, "_load_assistant_about", about)
    monkeypatch.setattr(drafting, "_agent_draft_reply", draft)


async def _chat_draft(m: Any, mail: str) -> dict[str, Any]:
    async with _as_member(m):
        return await drafting.draft_reply_smart(drafting.DraftReplyRequest(
            account_id=m.aid, message_id=mail, create_draft=True), user=m.me)


async def test_two_chat_drafts_leave_one_row(promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    box = _Box()
    m = _box(promoted, monkeypatch, "outlook", box.provider)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        _wire(monkeypatch, m, ["First text.", "Second text."])
        first = await _chat_draft(m, mail)
        second = await _chat_draft(m, mail)
        assert first["created"] and second["created"] and not second["kept_draft"]
        [row] = _thread_drafts(m.admin, m.aid)
        assert row.provider_message_id == "AAMk-1"
        assert row.body_text.startswith("Second text.")
        assert box.n == 1, "the second chat draft made a second provider draft"
    finally:
        _drop(m)


async def test_the_members_draft_is_kept_and_a_new_one_made(
        promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    box = _Box()
    m = _box(promoted, monkeypatch, "outlook", box.provider)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        mine = _seed_member_draft(m, "AAMk-mine", "Started in the Email app.")
        _wire(monkeypatch, m, ["The chat text."])
        out = await _chat_draft(m, mail)
        assert out["kept_draft"] is True
        rows = {r.id: r for r in _thread_drafts(m.admin, m.aid)}
        assert len(rows) == 2
        assert rows[mine].body_text == "Started in the Email app."
        assert rows[mine].provider_message_id == "AAMk-mine"
        for made in box.made:
            made.update_draft.assert_not_awaited()
    finally:
        _drop(m)


async def test_a_new_provider_id_moves_the_row_and_its_received_at(
        promoted, app_engine, monkeypatch):  # noqa: F811
    """A Gmail-like new id moves the chat's row by its row id. The save moves
    ``received_at`` too, so the Email app sees the draft saved last as the
    newest (``draftToContinue``)."""
    _assert_non_priv(app_engine)
    box = _Box(update_ids=["AAMk-moved"])
    m = _box(promoted, monkeypatch, "outlook", box.provider)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        _wire(monkeypatch, m, ["Old text.", "New text."])
        await _chat_draft(m, mail)
        [before] = _thread_drafts(m.admin, m.aid)
        await _chat_draft(m, mail)
        [row] = _thread_drafts(m.admin, m.aid)
        assert (row.id, row.provider_message_id) == (before.id, "AAMk-moved")
        assert row.body_text.startswith("New text.")
        assert row.received_at > before.received_at
    finally:
        _drop(m)


async def test_discard_trashes_the_cards_unedited_draft_only(
        promoted, app_engine, monkeypatch):  # noqa: F811
    """The real SQL of ``trash_chat_draft``: the card's own unedited draft
    goes, and the member's draft beside it stays."""
    _assert_non_priv(app_engine)
    box = _Box()
    m = _box(promoted, monkeypatch, "outlook", box.provider)
    try:
        mail = _seed_mail(m.admin, org=m.org, account_id=m.aid, pmid="i-1")
        mine = _seed_member_draft(m, "AAMk-mine", "Mine.")
        _wire(monkeypatch, m, ["The chat text."])
        await _chat_draft(m, mail)
        ours = next(r.id for r in _thread_drafts(m.admin, m.aid) if r.id != mine)
        async with _as_member(m), drafting._tenant_session() as db:
            assert not await drafting.trash_chat_draft(
                db, box.provider(), m.aid, "t-r", mine)
            assert await drafting.trash_chat_draft(
                db, box.provider(), m.aid, "t-r", ours)
        assert [r.id for r in _thread_drafts(m.admin, m.aid)] == [mine]
    finally:
        _drop(m)
