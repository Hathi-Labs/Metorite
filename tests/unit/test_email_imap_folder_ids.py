"""Task 4100 (data loss): an IMAP mail outside INBOX never takes an INBOX row.

An IMAP UID is unique in one mailbox only. The folder backfill
(``routes/email/transport/folders.py``) upserts on
``(account_id, provider_message_id)``, and the IMAP id was the bare UID. So a
Drafts or Sent mail overwrote the INBOX row with the same UID number.

For a mail with no Message-ID, the IMAP parse stores the In-Reply-To of the
parent as ``thread_id``. The drafting paths passed that ``thread_id`` to the
trash check, so a trash of such a Drafts row matched the parent, and the
parent at the same INBOX UID went to Trash.

The fix: a mail outside INBOX gets the id ``<mailbox>:<uid>``. The parse
stores the mail's own Message-ID in ``internet_message_id``, and the drafting
paths send that one. ``FakeImap`` of ``test_email_imap_trash.py`` keeps real
UIDs for each folder.

The R8 half of the stored-id read is
``test_email_imap_trash_uidvalidity_r8.py``.

Run: ``uv run pytest tests/unit/test_email_imap_folder_ids.py -q``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from email_ingestion.providers.base import trash_checked
from email_ingestion.providers.imap import (
    IMAPProvider,
    imap_message_id,
    split_imap_message_id,
)
from gateway.routes.email.transport import folders as route

from tests.unit.test_email_imap_trash import FakeImap, _mail

USER = SimpleNamespace(email="jane@acme.test")
BOX = "00000000-0000-0000-0000-0000000000b0"

#: A draft reply to A with NO Message-ID, as a mail client can save it.
DRAFT = (b"Subject: Re: A\r\nIn-Reply-To: <A@test>\r\n"
         b"References: <A@test>\r\n\r\nmy reply")


def _server() -> FakeImap:
    """INBOX holds A (uid 1) and B (uid 2). Drafts holds the draft reply to
    A at uid 1, and Sent holds S1 at uid 2, so each UID number is taken
    twice."""
    return FakeImap({
        "INBOX": [(_mail("A"), set()), (_mail("B"), set())],
        "Drafts": [(DRAFT, {"\\Draft"})],
        "Sent": [(_mail("S0"), {"\\Seen"}), (_mail("S1"), {"\\Seen"})],
        "Trash": [],
    })


def _provider(server: FakeImap) -> IMAPProvider:
    p = IMAPProvider({})
    p._imap = server  # type: ignore[assignment]
    p.authenticate = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return p


def _wire_backfill(monkeypatch: pytest.MonkeyPatch, provider: IMAPProvider,
                   store: dict[str, Any]) -> None:
    """The real backfill route. The store keys on the provider id, as the
    unique index of ``email_messages`` does, and an upsert replaces a row."""
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = SimpleNamespace(
        provider="imap", credentials_encrypted="x", stored_bytes=0)
    db.execute.return_value = result

    @asynccontextmanager
    async def _session():
        yield db

    async def _upsert(_db: Any, _aid: str, msg: Any, **_k: Any) -> None:
        store[msg.provider_message_id] = msg

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_account_filter", AsyncMock(return_value=BOX))
    monkeypatch.setattr(route, "_instantiate_provider", lambda *_a: provider)
    monkeypatch.setattr(route, "_upsert_message", _upsert)
    store_key = SimpleNamespace(decrypt=lambda _v: "{}", encrypt=lambda v: v)
    import acb_llm.key_store as key_store
    monkeypatch.setattr(key_store, "get_key_store", lambda: store_key)


async def _synced(server: FakeImap) -> dict[str, Any]:
    """The rows that the INBOX sync writes, by provider id."""
    result = await _provider(server).sync_messages(None)
    return {m.provider_message_id: m for m in result.messages}


# ── Gap 1: the folder backfill keeps the INBOX row ────────────────────────────


@pytest.mark.parametrize("folder", ["drafts", "sent"])
async def test_a_folder_backfill_keeps_the_inbox_row(monkeypatch, folder):
    server = _server()
    store = await _synced(server)
    assert {k: m.subject for k, m in store.items()} == {"1": "A", "2": "B"}
    provider = _provider(server)
    # The LIST parse of ``list_folders`` reads an atom name (Dovecot) wrong,
    # an old gap outside this task. So the listing is given here.
    provider.list_folders = AsyncMock(return_value=[  # type: ignore[method-assign]
        SimpleNamespace(name=n, provider_folder_id=n)
        for n in ("INBOX", "Drafts", "Sent", "Trash")])
    _wire_backfill(monkeypatch, provider, store)

    out = await route.backfill_folder(
        BOX, route.BackfillRequest(folder=folder, max_pages=1), user=USER)

    assert out["synced"] >= 1
    assert store["1"].subject == "A" and store["1"].folder == "INBOX"
    assert store["2"].subject == "B" and store["2"].folder == "INBOX"
    others = {k: m for k, m in store.items() if k not in ("1", "2")}
    assert others, "the backfill wrote no row"
    for key, msg in others.items():
        mailbox, uid = split_imap_message_id(key)
        assert mailbox.lower() == folder and uid.isdigit()
        assert msg.folder == folder


async def test_the_drafts_row_keeps_its_own_message_id_apart_from_the_thread():
    server = _server()
    msgs, _ = await _provider(server).list_messages(
        folder="Drafts", canonical_override="drafts")
    [draft] = msgs
    assert draft.provider_message_id == "Drafts:1"
    assert draft.thread_id == "<A@test>"  # the parent, as before
    assert draft.internet_message_id is None  # the draft has none
    [inbox_a] = [m for m in (await _synced(server)).values() if m.subject == "A"]
    assert inbox_a.internet_message_id == "<A@test>"


# ── Gap 2: a trash of the Drafts row moves no INBOX mail ──────────────────────


async def test_a_trash_of_the_drafts_row_moves_no_inbox_mail():
    """The drafting paths now send the draft's own Message-ID. The draft has
    none, so the trash raises and no mail moves, with each id that a Drafts
    row can hold: the new ``Drafts:1``, and the bare ``1`` of an old row."""
    server = _server()
    before = server.snapshot()
    provider = _provider(server)
    for pmid in ("Drafts:1", "1"):
        with pytest.raises(ValueError):
            await trash_checked(provider, pmid, None)
    with pytest.raises(ValueError, match="UID of an INBOX message"):
        await trash_checked(provider, "Drafts:1", "<A@test>")
    assert server.snapshot() == before
    assert server.boxes["Trash"] == {}


async def test_the_drafting_paths_send_the_drafts_own_message_id(monkeypatch):
    """``trash_chat_draft`` reads the Drafts row and trashes it. The trash
    gets the row's ``internet_message_id``, never the thread id, which is
    the Message-ID of the parent."""
    from gateway.routes.email.automation import drafting

    server = _server()
    before = server.snapshot()
    provider = _provider(server)
    row = SimpleNamespace(id="r1", provider_message_id="1",
                          internet_message_id=None, body_text="my reply")
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = row
    db.execute.return_value = result
    monkeypatch.setattr(drafting, "_account_signature", AsyncMock(return_value=""))
    monkeypatch.setattr(drafting, "chat_draft_record",
                        AsyncMock(return_value=["my reply"]))
    monkeypatch.setattr(drafting, "_live_is_chat_text", AsyncMock(return_value=True))
    seen: list[Any] = []
    real = drafting.trash_checked

    async def _spy(p: Any, pmid: str, mid: Any) -> Any:
        seen.append((pmid, mid))
        return await real(p, pmid, mid)

    monkeypatch.setattr(drafting, "trash_checked", _spy)
    with pytest.raises(ValueError):
        await drafting.trash_chat_draft(
            db, provider, BOX, "<A@test>", "00000000-0000-0000-0000-0000000000c1")
    assert seen == [("1", None)]
    assert server.snapshot() == before


# ── The other reads and writes go to the mailbox that the id names ────────────


async def test_a_flag_change_reaches_the_drafts_mail_and_not_the_inbox_mail():
    server = _server()
    await _provider(server).modify_message("Drafts:1", add_labels=["STARRED"])
    assert "\\Flagged" in server.boxes["Drafts"][1][1]
    assert "\\Flagged" not in server.boxes["INBOX"][1][1]


async def test_get_message_reads_the_named_mailbox_and_keeps_the_id():
    server = _server()
    msg = await _provider(server).get_message("Sent:2")
    assert msg.subject == "S1"
    assert msg.provider_message_id == "Sent:2"
    inbox = await _provider(server).get_message("2")
    assert inbox.subject == "B" and inbox.provider_message_id == "2"


def test_the_id_reads_back_for_any_mailbox_name():
    for mailbox in ("Drafts", "[Gmail]/Sent Mail", "Work:2024", "INBOX.Sent"):
        pid = imap_message_id(mailbox, "17")
        assert split_imap_message_id(pid) == (mailbox, "17")
    assert imap_message_id("INBOX", "17") == "17"
    assert imap_message_id("inbox", "17") == "17"
    assert split_imap_message_id("17") == ("INBOX", "17")
    assert split_imap_message_id("draft-Drafts") == ("INBOX", "draft-Drafts")
    assert split_imap_message_id(":17") == ("INBOX", ":17")
