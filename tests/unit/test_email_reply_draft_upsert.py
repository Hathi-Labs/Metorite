"""One reply draft per conversation, from the chat or the Email app.

Owner directive, 2026-10-22: "The latest email drafts done on chat should
also appear in the email draft, try not to add a new draft is there is
already a draft for the same email we need to reply to. We should be able to
start in chat, and go to email and finish and send."

What this file fences (R7). Each rule names the test that fails:

1. ``drafting._upsert_reply_draft`` updates the newest draft of the thread in
   place, and makes a draft only when the thread holds none.
   ``test_no_draft_in_the_thread_makes_one``,
   ``test_a_draft_in_the_thread_is_updated_in_place``.
2. Gmail gives the draft a new id at each update, and the local row moves to
   it. IMAP has no update, so it gets a fresh draft and the old one goes.
   ``test_a_gmail_update_moves_the_row``, ``test_imap_falls_back_to_a_fresh_draft``.
3. Two chat revisions leave ONE draft. ``test_two_chat_revisions_leave_one_draft``.
4. ``reply_card._save_draft`` returns the LOCAL id, which
   ``/email/drafts/schedule`` takes. ``test_the_card_save_returns_the_local_id``.
5. A shown card writes its text as the draft and records it as Metorite's
   text, so a send or a discard trashes it only while it is unedited.
   ``test_a_card_draft_is_recorded_as_metorites_text``,
   ``test_discard_runs_the_one_cleanup``.

The database and the providers are fakes. The SQL of the helper runs against
a real Postgres in ``test_email_reply_draft_upsert_r8.py``.
"""
from __future__ import annotations

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


class _Db:
    """The rows of ``email_messages`` that the helper reads and writes."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.tick = 0
        self.sql: list[str] = []

    def add(self, pmid: str, *, thread: str = THREAD, folder: str = "drafts",
            body: str = "") -> str:
        self.tick += 1
        rid = f"row-{len(self.rows) + 1}"
        self.rows.append({"id": rid, "account_id": ACC, "pmid": pmid,
                          "thread_id": thread, "folder": folder,
                          "body": body, "at": self.tick})
        return rid

    def drafts(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["folder"] == "drafts"]

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        p = params or {}
        self.sql.append(sql)
        one: Any = None
        if sql == drafting._THREAD_DRAFT_SQL:
            hits = [r for r in self.drafts()
                    if r["account_id"] == p["aid"] and r["thread_id"] == p["tid"]]
            hits.sort(key=lambda r: r["at"], reverse=True)
            if hits:
                one = types.SimpleNamespace(id=hits[0]["id"],
                                            provider_message_id=hits[0]["pmid"])
        elif sql == drafting._DROP_SYNC_COPY_SQL:
            self.rows = [r for r in self.rows
                         if not (r["pmid"] == p["pmid"] and r["id"] != p["id"])]
        elif sql == drafting._MOVE_LOCAL_DRAFT_SQL:
            for r in self.rows:
                if r["id"] == p["id"]:
                    r["pmid"] = p["pmid"]
                    self.tick += 1
                    r["at"] = self.tick
        elif "INSERT INTO email_messages" in sql:
            self.tick += 1
            row = next((r for r in self.rows
                        if r["account_id"] == p["aid"] and r["pmid"] == p["pmid"]), None)
            if row is None:
                row = {"id": p["id"], "account_id": p["aid"], "pmid": p["pmid"],
                       "thread_id": p["tid"], "folder": "drafts"}
                self.rows.append(row)
            row.update(body=p["body"], folder="drafts", at=self.tick)
            one = types.SimpleNamespace(id=row["id"])
        return types.SimpleNamespace(fetchone=lambda: one)


class _Outlook:
    """A provider that updates a draft in place and keeps its id."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.trashed: list[str] = []
        self.next_id = 0

    async def create_draft(self, **kw: Any) -> str:
        self.created.append(kw)
        self.next_id += 1
        return f"pm-new-{self.next_id}"

    async def update_draft(self, draft_id: str, **kw: Any) -> str:
        self.updated.append((draft_id, kw))
        return draft_id

    async def trash_message(self, pmid: str) -> None:
        self.trashed.append(pmid)


class _Gmail(GmailProvider):
    """A Gmail provider: each update gives the draft a new message id."""

    def __init__(self) -> None:  # no client: each call is faked below
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.n = 0

    async def create_draft(self, *_a: Any, **kw: Any) -> str:  # type: ignore[override]
        self.created.append(kw)
        self.n += 1
        return f"gm-{self.n}"

    async def update_draft(self, draft_id: str, *_a: Any, **kw: Any) -> str:  # type: ignore[override]
        self.updated.append((draft_id, kw))
        self.n += 1
        return f"gm-{self.n}"


class _Imap(_Outlook):
    async def update_draft(self, draft_id: str, **kw: Any) -> str:
        raise NotImplementedError


async def _write(db: _Db, provider: Any, body: str = "Hi Kim") -> str:
    return await drafting._upsert_reply_draft(
        db, provider, ACC, thread_id=THREAD, reply_to_provider_id="pm-mail",
        owner_email=ME, to=["kim@contoso.test"], subject="Re: Quote", body=body)


# ── 1. Update in place, else create ─────────────────────────────────────────


async def test_no_draft_in_the_thread_makes_one() -> None:
    db, box = _Db(), _Outlook()
    local = await _write(db, box)
    [made] = box.created
    assert made["reply_to_message_id"] == "pm-mail" and made["thread_id"] == THREAD
    assert box.updated == []
    [row] = db.drafts()
    assert local == row["id"] and row["pmid"] == "pm-new-1" and row["body"] == "Hi Kim"


async def test_a_draft_in_the_thread_is_updated_in_place() -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-old", thread="t-other")
    rid = db.add("pm-draft", body="Member text")
    local = await _write(db, box, body="Chat text")
    assert box.created == []
    [(pmid, kw)] = box.updated
    assert pmid == "pm-draft" and kw["body_text"] == "Chat text"
    assert kw["thread_id"] == THREAD and kw["to"] == ["kim@contoso.test"]
    assert local == rid
    assert [r["body"] for r in db.drafts() if r["thread_id"] == THREAD] == ["Chat text"]


async def test_the_newest_draft_of_the_thread_is_the_one() -> None:
    db, box = _Db(), _Outlook()
    db.add("pm-older")
    newer = db.add("pm-newer")
    assert await _write(db, box) == newer
    assert [p for p, _ in box.updated] == ["pm-newer"]


async def test_a_failed_update_still_lands_a_fresh_draft() -> None:
    """The draft left the provider after the last sync: a chat draft still lands."""
    db, box = _Db(), _Outlook()
    db.add("pm-gone")

    async def gone(draft_id: str, **kw: Any) -> str:
        raise RuntimeError("404")

    box.update_draft = gone  # type: ignore[method-assign]
    local = await _write(db, box)
    assert len(box.created) == 1
    assert local == next(r["id"] for r in db.rows if r["pmid"] == "pm-new-1")


# ── 2. Gmail moves the row, IMAP falls back ─────────────────────────────────


async def test_a_gmail_update_moves_the_row() -> None:
    db, box = _Db(), _Gmail()
    rid = db.add("gm-old")
    local = await _write(db, box)
    assert box.created == [] and [p for p, _ in box.updated] == ["gm-old"]
    assert local == rid
    [row] = db.drafts()
    assert row["pmid"] == "gm-1", "the row did not move to the new Gmail id"


async def test_a_gmail_update_drops_the_sync_copy_of_the_new_id() -> None:
    """E-A2: the sync can write the new id as its own row first. One row stays."""
    db, box = _Db(), _Gmail()
    db.add("gm-1", thread="")  # the sync's copy of the id the update returns
    rid = db.add("gm-old")
    assert await _write(db, box) == rid
    assert [r["id"] for r in db.drafts()] == [rid]


async def test_imap_falls_back_to_a_fresh_draft() -> None:
    db, box = _Db(), _Imap()
    rid = db.add("uid-7")
    local = await _write(db, box)
    assert len(box.created) == 1 and box.created[0]["exact_to"] is True
    assert box.trashed == ["uid-7"]
    assert local == rid
    [row] = db.drafts()
    assert row["pmid"] == "pm-new-1"


# ── 3. Two chat revisions, one draft ────────────────────────────────────────


@pytest.mark.parametrize("make", [_Outlook, _Gmail, _Imap], ids=["outlook", "gmail", "imap"])
async def test_two_chat_revisions_leave_one_draft(make: Any) -> None:
    db, box = _Db(), make()
    first = await _write(db, box, body="First")
    second = await _write(db, box, body="Second, shorter")
    assert first == second
    [row] = db.drafts()
    assert row["body"] == "Second, shorter"
    assert len(box.created) == (2 if make is _Imap else 1)


# ── 4. The routes use the one helper ────────────────────────────────────────


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


async def test_the_card_save_returns_the_local_id(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    rid = db.add("pm-draft")
    monkeypatch.setattr(rc, "_tenant_session", bind_db(db))
    monkeypatch.setattr(rc, "provider_session", _session(box))
    out = await rc._save_draft(_mail(), ME, ["kim@contoso.test"], [], "Re: Quote", "Edited")
    assert out == rid, "the card save must return the local id, not the provider id"
    [(pmid, kw)] = box.updated
    assert pmid == "pm-draft" and kw["body_text"] == "Edited"


async def test_a_card_draft_is_recorded_as_metorites_text(monkeypatch) -> None:
    db, box = _Db(), _Outlook()
    stored: list[tuple[str, str, str]] = []

    async def store(_db: Any, aid: str, tid: str, txt: str, *, commit: bool = True) -> None:
        stored.append((aid, tid, txt))

    monkeypatch.setattr(rc, "_tenant_session", bind_db(db))
    monkeypatch.setattr(rc, "provider_session", _session(box))
    monkeypatch.setattr(rc, "_store_ai_draft", store)
    local = await rc._keep_card_draft(_mail(), ME, ["kim@contoso.test"], [], "Re: Quote",
                                      "Card text")
    assert local and stored == [(ACC, THREAD, "Card text")]
    # The act's save is the member's text, so it records nothing.
    await rc._save_draft(_mail(), ME, ["kim@contoso.test"], [], "Re: Quote", "Edited")
    assert len(stored) == 1 and len(db.drafts()) == 1


async def test_a_failed_card_draft_still_shows_the_card(monkeypatch) -> None:
    @asynccontextmanager
    async def broken(*_a: Any, **_k: Any):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(rc, "_tenant_session", broken)
    assert await rc._keep_card_draft(_mail(), ME, ["k@c.test"], [], "Re: Q", "x") == ""


async def test_discard_runs_the_one_cleanup(monkeypatch) -> None:
    """Discard trashes through ``_cleanup_thread_drafts``, which keeps a draft
    whose live body is not Metorite's text (EM-G11c2)."""
    calls: list[tuple[str, str]] = []

    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def cleanup(aid: str, tid: str) -> None:
        calls.append((aid, tid))

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "_cleanup_thread_drafts", cleanup)
    out = await rc.reply_card_discard(rc.ReplyCardDiscardRequest(message_id="m"),
                                      types.SimpleNamespace(email=ME))
    assert out == {"ok": True} and calls == [(ACC, THREAD)]


def test_the_discard_request_takes_nothing_but_the_mail() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        rc.ReplyCardDiscardRequest(message_id="m", draft_id="d")  # type: ignore[call-arg]


async def test_draft_reply_updates_the_threads_draft(monkeypatch) -> None:
    """``POST /email/draft-reply`` writes through the one helper."""
    from unittest.mock import AsyncMock

    db, box = _Db(), _Outlook()
    rid = db.add("pm-draft")
    monkeypatch.setattr(drafting, "_tenant_session", bind_db(db))
    monkeypatch.setattr(drafting, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting, "_build_reply_context", AsyncMock(return_value={
        "subject": "Quote", "body": "b", "thread_id": THREAD,
        "from": "kim@contoso.test", "self": ME}))
    monkeypatch.setattr(drafting, "load_draft_files", AsyncMock(return_value=types.SimpleNamespace(
        read_names=lambda: [], notices=lambda: [])))
    monkeypatch.setattr(drafting, "_load_assistant_about", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting, "_agent_draft_reply", AsyncMock(return_value="Hi Kim"))
    monkeypatch.setattr(drafting, "_store_ai_draft", AsyncMock())
    monkeypatch.setattr(drafting, "_reply_to_list",
                        AsyncMock(return_value=["kim@contoso.test"]))
    monkeypatch.setattr(drafting, "provider_session", _session(box))
    out = await drafting.draft_reply_smart(
        drafting.DraftReplyRequest(account_id=ACC, message_id="m1", create_draft=True),
        types.SimpleNamespace(email=ME))
    assert out["created"] is True and box.created == []
    assert [p for p, _ in box.updated] == ["pm-draft"]
    assert [r["id"] for r in db.drafts()] == [rid]
