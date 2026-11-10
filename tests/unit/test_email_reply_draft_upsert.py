"""The chat's reply draft lands in Drafts, and never writes over the member's.

Owner directive, 2026-10-22: "The latest email drafts done on chat should
also appear in the email draft, try not to add a new draft is there is
already a draft for the same email we need to reply to. We should be able to
start in chat, and go to email and finish and send."

What this file fences (R7). Each rule names the test that fails:

1. ``drafting._upsert_reply_draft`` updates a draft in place ONLY while it is
   still the text that Metorite wrote (``email_ai_drafts``), by the stored
   AND the live body. Else it makes a new draft beside the member's, and
   says so. ``test_the_members_draft_is_never_written_over``,
   ``test_an_edit_seen_only_live_keeps_the_draft``,
   ``test_the_chats_own_draft_is_updated_in_place``.
2. A forward and a draft with a file are never a candidate.
   ``test_a_forward_or_a_draft_with_a_file_is_never_updated``.
3. An update keeps the Cc and the Bcc of the draft when the caller gives
   none. ``test_a_revision_keeps_the_cc_and_bcc``.
4. Gmail moves the row, and IMAP falls back to a fresh draft.
   ``test_a_gmail_update_moves_the_row``, ``test_imap_falls_back_to_a_fresh_draft``.
5. Two chat revisions leave ONE draft. ``test_two_chat_revisions_leave_one_draft``.
6. ``reply_card._save_draft`` returns the LOCAL id.
   ``test_the_card_save_returns_the_local_id``.
7. Discard is the member's click, and it trashes only the card's own
   unedited draft. ``test_discard_trashes_only_the_cards_unedited_draft``,
   ``test_discard_keeps_an_edited_draft``, ``test_discard_keeps_another_draft``.
   The tool never trashes on a timeout: ``test_email_chat_reply.py``.

The database and the providers are fakes. The SQL runs against a real
Postgres in ``test_email_reply_draft_upsert_r8.py``.
"""
from __future__ import annotations

import json
import types
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("fastapi")

from email_ingestion.providers.gmail import GmailProvider
from gateway.routes.email.automation import drafting
from gateway.routes.email.automation import reply_card as rc

from tests.unit._email_fakes import bind_db

ACC = "acc-1"
THREAD = "t-1"
ME = "jane@acme.test"
CHAT = "Hi Kim, the chat wrote this."


def _addrs(items: list[str]) -> str:
    return json.dumps([{"name": "", "email": a} for a in items])


class _Db:
    """The rows of ``email_messages`` and the ``email_ai_drafts`` record."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.record: dict[str, str] = {}
        self.tick = 0

    def add(self, pmid: str, *, body: str = CHAT, thread: str = THREAD,
            subject: str = "Re: Quote", files: bool = False,
            cc: list[str] | None = None, bcc: list[str] | None = None) -> str:
        self.tick += 1
        rid = f"00000000-0000-4000-8000-{len(self.rows) + 1:012d}"
        self.rows.append({"id": rid, "account_id": ACC, "pmid": pmid,
                          "thread_id": thread, "folder": "drafts", "subject": subject,
                          "files": files, "body": body, "cc": _addrs(cc or []),
                          "bcc": _addrs(bcc or []), "at": self.tick})
        return rid

    def drafts(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["folder"] == "drafts"]

    @staticmethod
    def _ns(r: dict[str, Any]) -> Any:
        return types.SimpleNamespace(
            id=r["id"], provider_message_id=r["pmid"], body_text=r["body"],
            cc_addresses=r["cc"], bcc_addresses=r["bcc"])

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        p = params or {}
        many: list[Any] = []
        one: Any = None
        if sql == drafting._THREAD_DRAFT_SQL:
            hits = [r for r in self.drafts()
                    if r["account_id"] == p["aid"] and r["thread_id"] == p["tid"]
                    and not r["files"] and not r["subject"].lower().startswith("fw")]
            hits.sort(key=lambda r: (r["at"], r["id"]), reverse=True)
            many = [self._ns(r) for r in hits]
        elif sql == drafting._CHAT_RECORD_SQL:
            txt = self.record.get(p["tid"])
            one = types.SimpleNamespace(draft_text=txt) if txt else None
        elif sql == drafting._CARD_DRAFT_SQL:
            r = next((r for r in self.drafts() if r["id"] == p["id"]
                      and r["thread_id"] == p["tid"]), None)
            one = self._ns(r) if r else None
        elif sql.startswith("DELETE FROM email_messages WHERE id = :id"):
            self.rows = [r for r in self.rows if r["id"] != p["id"]]
        elif sql == drafting._DROP_SYNC_COPY_SQL:
            self.rows = [r for r in self.rows
                         if not (r["pmid"] == p["pmid"] and r["id"] != p["id"])]
        elif sql == drafting._MOVE_LOCAL_DRAFT_SQL:
            for r in self.rows:
                if r["id"] == p["id"]:
                    r["pmid"] = p["pmid"]
        elif "INSERT INTO email_messages" in sql:
            self.tick += 1
            row = next((r for r in self.rows
                        if r["account_id"] == p["aid"] and r["pmid"] == p["pmid"]), None)
            if row is None:
                row = {"id": p["id"], "account_id": p["aid"], "pmid": p["pmid"],
                       "thread_id": p["tid"], "subject": p["subject"], "files": False}
                self.rows.append(row)
            row.update(body=p["body"], folder="drafts", at=self.tick,
                       cc=p["cc_addrs"], bcc=p["bcc_addrs"])
            one = types.SimpleNamespace(id=row["id"])
        return types.SimpleNamespace(fetchone=lambda: one, fetchall=lambda: many)


class _Outlook:
    """A provider that updates a draft in place and keeps its id. ``live``
    is the body that the provider holds for each id."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.trashed: list[str] = []
        self.live: dict[str, str] = {}
        self.n = 0

    async def create_draft(self, *_a: Any, **kw: Any) -> str:
        self.created.append(kw)
        self.n += 1
        pmid = f"pm-new-{self.n}"
        self.live[pmid] = kw["body_text"]
        return pmid

    async def update_draft(self, draft_id: str, *_a: Any, **kw: Any) -> str:
        self.updated.append((draft_id, kw))
        self.live[draft_id] = kw.get("body_text") or ""
        return draft_id

    async def get_message_body(self, pmid: str) -> Any:
        return types.SimpleNamespace(body_text=self.live.get(pmid, CHAT), body_html="")

    async def trash_message(self, pmid: str) -> None:
        self.trashed.append(pmid)


class _Gmail(GmailProvider):
    """A Gmail provider: each update gives the draft a new message id."""

    def __init__(self) -> None:  # no client: each call is faked below
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.live: dict[str, str] = {}
        self.n = 0

    async def create_draft(self, *_a: Any, **kw: Any) -> str:  # type: ignore[override]
        self.created.append(kw)
        self.n += 1
        self.live[f"gm-{self.n}"] = kw["body_text"]
        return f"gm-{self.n}"

    async def update_draft(self, draft_id: str, *_a: Any, **kw: Any) -> str:  # type: ignore[override]
        self.updated.append((draft_id, kw))
        self.n += 1
        self.live[f"gm-{self.n}"] = kw.get("body_text") or ""
        return f"gm-{self.n}"

    async def get_message_body(self, pmid: str) -> Any:  # type: ignore[override]
        return types.SimpleNamespace(body_text=self.live.get(pmid, CHAT), body_html="")


class _Imap(_Outlook):
    async def update_draft(self, draft_id: str, *_a: Any, **kw: Any) -> str:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _no_signature(monkeypatch) -> None:
    async def sig(_db: Any, _aid: str) -> str:
        return ""

    monkeypatch.setattr(drafting, "_account_signature", sig)


async def _write(db: _Db, provider: Any, body: str = "New chat text",
                 cc: list[str] | None = None) -> drafting.ReplyDraftWrite:
    out = await drafting._upsert_reply_draft(
        db, provider, ACC, thread_id=THREAD, reply_to_provider_id="pm-mail",
        owner_email=ME, to=["kim@contoso.test"], cc=cc, subject="Re: Quote", body=body)
    db.record[THREAD] = body  # as the callers store it, after the save
    return out


# ── 1. Only the chat's own unedited draft is updated ────────────────────────


async def test_no_draft_in_the_thread_makes_one() -> None:
    db, box = _Db(), _Outlook()
    out = await _write(db, box)
    [made] = box.created
    assert made["reply_to_message_id"] == "pm-mail" and made["thread_id"] == THREAD
    assert box.updated == [] and out.kept_draft is False
    [row] = db.drafts()
    assert out.local_id == row["id"] and row["body"] == "New chat text"


async def test_the_chats_own_draft_is_updated_in_place() -> None:
    db, box = _Db(), _Outlook()
    rid = db.add("pm-chat")
    db.record[THREAD] = CHAT
    out = await _write(db, box, body="Chat revision")
    assert box.created == [] and out.kept_draft is False
    [(pmid, kw)] = box.updated
    assert pmid == "pm-chat" and kw["body_text"] == "Chat revision"
    assert out.local_id == rid


async def test_the_members_draft_is_never_written_over() -> None:
    """P0: the newest draft is the member's own text. It stays as it was, and
    the chat's text goes into a new draft beside it."""
    db, box = _Db(), _Outlook()
    mine = db.add("pm-mine", body="Typed by hand.")
    db.record[THREAD] = CHAT
    out = await _write(db, box)
    assert box.updated == [], "the member's draft was written over"
    assert len(box.created) == 1 and out.kept_draft is True
    assert next(r for r in db.rows if r["id"] == mine)["body"] == "Typed by hand."
    assert len(db.drafts()) == 2


async def test_with_no_record_every_draft_is_the_members() -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-mine", body=CHAT)
    out = await _write(db, box)
    assert box.updated == [] and out.kept_draft is True


async def test_an_edit_seen_only_live_keeps_the_draft() -> None:
    """The row still holds Metorite's text, and Outlook holds the member's
    edit. The live read decides: the draft stays."""
    db, box = _Db(), _Outlook()
    db.add("pm-chat")
    db.record[THREAD] = CHAT
    box.live["pm-chat"] = "Edited in Outlook."
    out = await _write(db, box)
    assert box.updated == [] and out.kept_draft is True


async def test_a_failed_live_read_keeps_the_draft() -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-chat")
    db.record[THREAD] = CHAT

    async def broken(_pmid: str) -> Any:
        raise RuntimeError("timeout")

    box.get_message_body = broken  # type: ignore[method-assign]
    out = await _write(db, box)
    assert box.updated == [] and out.kept_draft is True


async def test_the_chats_draft_beside_the_members_is_the_one_updated() -> None:
    """The member's draft is newer, and the chat's own draft is older. The
    chat updates its own, and the member's stays."""
    db, box = _Db(), _Outlook()
    ours = db.add("pm-chat")
    db.add("pm-mine", body="Typed by hand.")
    db.record[THREAD] = CHAT
    out = await _write(db, box, body="Chat revision")
    assert [p for p, _ in box.updated] == ["pm-chat"] and box.created == []
    assert out.local_id == ours and out.kept_draft is True


# ── 2. A forward and a draft with a file ────────────────────────────────────


@pytest.mark.parametrize("kw", [{"subject": "Fwd: Quote"}, {"subject": "FW: Quote"},
                                {"files": True}], ids=["fwd", "fw", "file"])
async def test_a_forward_or_a_draft_with_a_file_is_never_updated(kw: dict[str, Any]) -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-x", **kw)
    db.record[THREAD] = CHAT
    out = await _write(db, box)
    assert box.updated == [] and len(box.created) == 1
    assert out.kept_draft is False, "a forward or a file draft is no candidate"


# ── 3. Cc and Bcc ───────────────────────────────────────────────────────────


async def test_a_revision_keeps_the_cc_and_bcc() -> None:
    """The member added a Cc and a Bcc to the chat's draft. A chat revision
    with no Cc keeps both."""
    db, box = _Db(), _Outlook()
    rid = db.add("pm-chat", cc=["ops@contoso.test"], bcc=["boss@acme.test"])
    db.record[THREAD] = CHAT
    await _write(db, box, body="Chat revision")
    [(_pmid, kw)] = box.updated
    assert kw["cc"] == ["ops@contoso.test"] and kw["bcc"] == ["boss@acme.test"]
    row = next(r for r in db.rows if r["id"] == rid)
    assert "ops@contoso.test" in row["cc"] and "boss@acme.test" in row["bcc"]


async def test_a_card_save_writes_the_cc_the_member_chose() -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-chat", cc=["ops@contoso.test"])
    db.record[THREAD] = CHAT
    await drafting._upsert_reply_draft(
        db, box, ACC, thread_id=THREAD, reply_to_provider_id="pm-mail", owner_email=ME,
        to=["kim@contoso.test"], cc=[], subject="Re: Quote", body="x", exact_cc=True)
    [(_pmid, kw)] = box.updated
    assert kw["cc"] == []


# ── 4. Gmail and IMAP ───────────────────────────────────────────────────────


async def test_a_gmail_update_moves_the_row() -> None:
    db, box = _Db(), _Gmail()
    box.live["gm-old"] = CHAT
    rid = db.add("gm-old")
    db.record[THREAD] = CHAT
    out = await _write(db, box)
    assert box.created == [] and [p for p, _ in box.updated] == ["gm-old"]
    assert out.local_id == rid
    [row] = db.drafts()
    assert row["pmid"] == "gm-1", "the row did not move to the new Gmail id"


async def test_imap_falls_back_to_a_fresh_draft() -> None:
    db, box = _Db(), _Imap()
    rid = db.add("uid-7")
    db.record[THREAD] = CHAT
    out = await _write(db, box)
    assert len(box.created) == 1 and box.created[0]["exact_to"] is True
    assert box.trashed == ["uid-7"]
    assert out.local_id == rid
    [row] = db.drafts()
    assert row["pmid"] == "pm-new-1"


@pytest.mark.parametrize("make", [_Outlook, _Gmail, _Imap], ids=["outlook", "gmail", "imap"])
async def test_two_chat_revisions_leave_one_draft(make: Any) -> None:
    db, box = _Db(), make()
    first = await _write(db, box, body="First")
    second = await _write(db, box, body="Second, shorter")
    assert first.local_id == second.local_id
    [row] = db.drafts()
    assert row["body"] == "Second, shorter"


# ── 6. The routes ───────────────────────────────────────────────────────────


def _session(provider: Any) -> Any:
    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        yield types.SimpleNamespace(provider=provider, authed=True, account_id=ACC,
                                    provider_message_id="pm-mail", owner_email=ME)
    return provider_session


def _mail() -> Any:
    row = types.SimpleNamespace(
        account_id=ACC, provider_message_id="pm-mail", thread_id=THREAD,
        subject="Quote", from_address={"name": "Kim", "email": "kim@contoso.test"},
        to_addresses=[{"email": ME}], cc_addresses=[], received_at=None,
        snippet="", body_text="")
    me = types.SimpleNamespace(address=ME, label="Work", self_addresses=frozenset({ME}))
    return rc._Mail(row, me, [])


def _wire_rc(monkeypatch, db: _Db, box: Any) -> None:
    async def store(_db: Any, aid: str, tid: str, txt: str, *, commit: bool = True) -> None:
        db.record[tid] = txt

    monkeypatch.setattr(rc, "_tenant_session", bind_db(db))
    monkeypatch.setattr(rc, "provider_session", _session(box))
    monkeypatch.setattr(rc, "_store_ai_draft", store)


async def test_the_card_save_returns_the_local_id(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    rid = db.add("pm-chat")
    db.record[THREAD] = CHAT
    _wire_rc(monkeypatch, db, box)
    out = await rc._save_draft(_mail(), ME, ["kim@contoso.test"], [], "Re: Quote", "Edited")
    assert out == rid, "the card save must return the local id, not the provider id"
    [(pmid, kw)] = box.updated
    assert pmid == "pm-chat" and kw["body_text"] == "Edited"


async def test_each_card_updates_the_draft_of_the_card_before(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    _wire_rc(monkeypatch, db, box)
    first, _ = await rc._keep_card_draft(_mail(), ME, ["k@c.test"], [], "Re: Q", "Card 1")
    second, kept = await rc._keep_card_draft(_mail(), ME, ["k@c.test"], [], "Re: Q", "Card 2",
                                             prior=["Card 1"])
    assert first == second and kept is False and len(db.drafts()) == 1
    assert db.record[THREAD] == "Card 2"


async def test_a_failed_card_draft_still_shows_the_card(monkeypatch) -> None:
    @asynccontextmanager
    async def broken(*_a: Any, **_k: Any):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(rc, "_tenant_session", broken)
    assert await rc._keep_card_draft(_mail(), ME, ["k@c.test"], [], "Re: Q", "x") == ("", False)


# ── 7. Discard: the member's click, the card's own unedited draft only ──────


async def _discard(monkeypatch, db: _Db, box: Any, draft_id: str) -> dict[str, Any]:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    _wire_rc(monkeypatch, db, box)
    monkeypatch.setattr(rc, "_load_mail", load_mail)
    return await rc.reply_card_discard(
        rc.ReplyCardDiscardRequest(message_id="m", draft_id=draft_id),
        types.SimpleNamespace(email=ME))


async def test_discard_trashes_only_the_cards_unedited_draft(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    card = db.add("pm-card")
    other = db.add("pm-other")  # the same text, but another draft
    db.record[THREAD] = CHAT
    out = await _discard(monkeypatch, db, box, card)
    assert out == {"ok": True, "trashed": True}
    assert box.trashed == ["pm-card"]
    assert [r["id"] for r in db.drafts()] == [other]


async def test_discard_keeps_an_edited_draft(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    card = db.add("pm-card", body="The member edited this in the Email app.")
    db.record[THREAD] = CHAT
    out = await _discard(monkeypatch, db, box, card)
    assert out["trashed"] is False and box.trashed == [] and len(db.drafts()) == 1


async def test_discard_keeps_a_draft_edited_only_live(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    card = db.add("pm-card")
    db.record[THREAD] = CHAT
    box.live["pm-card"] = "Edited in Outlook."
    out = await _discard(monkeypatch, db, box, card)
    assert out["trashed"] is False and box.trashed == []


async def test_discard_keeps_another_draft(monkeypatch) -> None:
    """A draft id of another thread, or no uuid at all, trashes nothing."""
    db, box = _Db(), _Outlook()
    elsewhere = db.add("pm-else", thread="t-other")
    db.record[THREAD] = CHAT
    assert (await _discard(monkeypatch, db, box, elsewhere))["trashed"] is False
    assert (await _discard(monkeypatch, db, box, "not-a-uuid"))["trashed"] is False
    assert box.trashed == []


def test_the_discard_request_names_the_mail_and_the_draft_only() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        rc.ReplyCardDiscardRequest(message_id="m")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        rc.ReplyCardDiscardRequest(message_id="m", draft_id="d", to=["x"])  # type: ignore[call-arg]


# ── The draft-reply route ───────────────────────────────────────────────────


async def test_draft_reply_says_when_the_members_draft_stays(monkeypatch) -> None:
    """``POST /email/draft-reply`` reads the record BEFORE it stores the new
    text, so the chat's own draft is updated, and a member's draft stays."""
    from unittest.mock import AsyncMock

    db, box = _Db(), _Outlook()
    db.add("pm-chat")
    db.record[THREAD] = CHAT
    order: list[str] = []

    async def store(_db: Any, aid: str, tid: str, txt: str, *, commit: bool = True) -> None:
        order.append("store")
        db.record[tid] = txt

    monkeypatch.setattr(drafting, "_tenant_session", bind_db(db))
    monkeypatch.setattr(drafting, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting, "_build_reply_context", AsyncMock(return_value={
        "subject": "Quote", "body": "b", "thread_id": THREAD,
        "from": "kim@contoso.test", "self": ME}))
    monkeypatch.setattr(drafting, "load_draft_files", AsyncMock(return_value=types.SimpleNamespace(
        read_names=lambda: [], notices=lambda: [])))
    monkeypatch.setattr(drafting, "_load_assistant_about", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting, "_agent_draft_reply", AsyncMock(return_value="Revision"))
    monkeypatch.setattr(drafting, "_store_ai_draft", store)
    monkeypatch.setattr(drafting, "_reply_to_list",
                        AsyncMock(return_value=["kim@contoso.test"]))
    monkeypatch.setattr(drafting, "provider_session", _session(box))

    async def call() -> dict[str, Any]:
        return await drafting.draft_reply_smart(
            drafting.DraftReplyRequest(account_id=ACC, message_id="m1", create_draft=True),
            types.SimpleNamespace(email=ME))

    out = await call()
    assert out["created"] is True and out["kept_draft"] is False
    assert [p for p, _ in box.updated] == ["pm-chat"] and box.created == []
    # The member edits the draft. The next chat draft goes beside it.
    db.drafts()[0]["body"] = "Edited by the member."
    out = await call()
    assert out["kept_draft"] is True and len(box.created) == 1
    assert order == ["store", "store"]
