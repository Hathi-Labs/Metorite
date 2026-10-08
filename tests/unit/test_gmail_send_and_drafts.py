"""WS-17 EM-G3a — Gmail send and drafts (GM-10 to GM-13).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.3, items 1 to 9,
the dispatch points E-A1 to E-A5, and O-GM-2 in §12.2.

* Items 1 to 3: ONE builder (``gmail._build_gmail_mail``) serves
  ``send_message``, ``create_draft`` and ``update_draft``. A body with HTML
  is ``multipart/alternative``, the text part first. Attachments sit in
  ``multipart/mixed``, each with the type that the caller gives, else the type
  of its name. ``add_header(..., filename=...)`` keeps each file name.
* Items 4 and 5: a reply reads its parent from Gmail with ``format=metadata``
  and sets ``In-Reply-To`` and ``References``. With only a thread id, the
  parent is the newest message of the thread that is not a draft.
* Items 6 and 7 (O-GM-2): a draft write returns the MESSAGE id of the draft.
  ``update_draft`` and ``send_draft`` take that id and find the draft id
  through ``drafts.list``. A message id that is no draft raises
  ``GmailDraftNotFound``.
* Item 8, E-A1 and E-A2: the save moves the local row to the new message id
  by its row id, and the signed send gives ``send_draft`` the id that the
  update returns. A copy that the sync wrote of the new id folds into the
  local row.
* E-A3: a parent read that fails never fails the send.
* E-A5: ``trash_message`` discards a draft with ``drafts.delete``, and the
  delete route removes its local row.

The hermetic tests drive the REAL ``GmailProvider`` through the real
``_get_client`` against a fake Gmail on ``httpx.MockTransport``, as
``test_gmail_rate_limits.py`` does. The fake keeps state: an update gives the
draft a new message id, so a send of the old id finds no draft.

Since EM-G3c-1 a mail with a file goes to the upload URI. The fake parses
that upload (``parse_upload``) into the JSON body that it stands for, so
``sent`` and ``saved`` keep one shape, and these fences read ``raw`` as
before. ``uploads`` records each upload. ``test_gmail_mail_size.py`` holds
the fences of the URI.

**R8.** The class ``TestTheLocalDraftRow`` runs the real routes and the real
upsert against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal`` under FORCE RLS, as the role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and
reads the rows. It is the harness of ``test_email_rekey_reclaim.py`` and
``test_email_duplicates.py``.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_gmail_send_and_drafts.py -v -rs
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from email import message_from_bytes
from email.message import Message
from email.mime.text import MIMEText
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import email_ingestion.providers.gmail as gmail_mod
import httpx
import pytest
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.gmail import GmailDraftNotFound, GmailProvider

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from email_ingestion.persist import upsert_message
from fastapi import BackgroundTasks
from gateway.routes.email import core
from gateway.routes.email.automation import drafting
from gateway.routes.email.transport import messages as messages_mod
from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _purge,
)

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "/gmail/v1/users/me"
#: The upload URI of the three writes (EM-G3c-1 item 3).
UPLOAD = "/upload" + API
LOGGER = "email_ingestion.providers.gmail"
RAVI = "ravi@contoso-em-g3a.test"


# ── the fake Gmail ──────────────────────────────────────────────────────────


def _b64(text_value: str) -> str:
    return base64.urlsafe_b64encode(text_value.encode()).decode().rstrip("=")


def _decode(raw: str) -> Message:
    return message_from_bytes(base64.urlsafe_b64decode(raw))


def _not_found() -> httpx.Response:
    return httpx.Response(404, json={"error": {"code": 404}})


def parse_upload(request: httpx.Request) -> tuple[dict[str, Any], bytes]:
    """The metadata and the mail of one upload write (EM-G3c-1 item 9).

    The request must be ``multipart/related`` with ``uploadType=multipart``.
    Part 1 is the metadata in JSON, and part 2 is the raw mail as
    ``message/rfc822``. Any other shape raises ``ValueError``, so the fake
    answers 400 for it, as Gmail does."""
    content_type = request.headers.get("content-type", "")
    prefix = "multipart/related; boundary="
    if (not content_type.startswith(prefix)
            or request.url.params.get("uploadType") != "multipart"):
        raise ValueError(f"not an upload: {content_type!r}")
    delimiter = b"--" + content_type.removeprefix(prefix).encode()
    body = request.content
    head, end = delimiter + b"\r\n", b"\r\n" + delimiter + b"--\r\n"
    if not (body.startswith(head) and body.endswith(end)):
        raise ValueError("the body does not open and close with the boundary")
    first, second = body[len(head):-len(end)].split(
        b"\r\n" + delimiter + b"\r\n")
    meta_head, meta = first.split(b"\r\n\r\n", 1)
    mail_head, mail = second.split(b"\r\n\r\n", 1)
    if (meta_head != b"Content-Type: application/json; charset=UTF-8"
            or mail_head != b"Content-Type: message/rfc822"):
        raise ValueError("the parts have the wrong types")
    return json.loads(meta), mail


def _upload_as_json(meta: dict[str, Any], mail: bytes) -> dict[str, Any]:
    """The JSON body that one upload stands for. The mail goes into ``raw``
    where the metadata keeps it, so ``sent`` and ``saved`` hold one shape
    for both URIs, and the fences of EM-G3a read them unchanged."""
    raw = base64.urlsafe_b64encode(mail).decode()
    if "message" in meta:
        return {"message": {"raw": raw, **meta["message"]}}
    return {"raw": raw, **meta}


class _Gmail:
    """A fake Gmail API that keeps its drafts and messages.

    ``drafts.update`` gives the draft a new message id, and the old message
    goes, as Gmail does. ``drafts.list`` pages by ``page_size``. ``fail``
    gives a (method, path) one answer with an error status."""

    def __init__(self) -> None:
        self.drafts: dict[str, str] = {}          # draft id → message id
        self.messages: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []      # bodies of messages.send
        self.saved: list[dict[str, Any]] = []     # bodies of drafts.create/update
        self.updated: list[str] = []              # new message ids of updates
        self.sent_drafts: list[str] = []          # draft ids of drafts.send
        self.deleted_drafts: list[str] = []
        self.trashed: list[str] = []
        self.files: dict[str, bytes] = {}         # attachment id → bytes
        self.raws: dict[str, str] = {}            # message id → raw mail
        self.seen: list[tuple[str, str, httpx.QueryParams]] = []
        #: Each write on the upload URI: the method, the path under
        #: ``/users/me``, the URL, the metadata, the raw mail and the
        #: ``Authorization`` header (EM-G3c-1).
        self.uploads: list[SimpleNamespace] = []
        self.page_size = 500
        self.fail: dict[tuple[str, str], int] = {}
        #: Runs after drafts.update, with the new message id (E-A2).
        self.after_update: Callable[[str], Awaitable[None]] | None = None
        self.n = 0

    def _next(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n}"

    # ── seeds ──

    def add_message(self, mid: str, *, thread: str, labels: list[str],
                    at: int, message_id: str | None = None,
                    references: str | None = None,
                    subject: str = "Quote") -> None:
        headers = [{"name": "Subject", "value": subject},
                   {"name": "From", "value": f"Ravi <{RAVI}>"}]
        if message_id:
            headers.append({"name": "Message-ID", "value": message_id})
        if references:
            headers.append({"name": "References", "value": references})
        self.messages[mid] = {
            "id": mid, "threadId": thread, "labelIds": labels,
            "internalDate": str(at), "snippet": "",
            "payload": {"mimeType": "text/plain", "headers": headers,
                        "body": {"data": _b64("hello")}},
        }
        mail = MIMEText("hello")
        for header in headers:
            mail[header["name"]] = header["value"]
        self.raws[mid] = base64.urlsafe_b64encode(mail.as_bytes()).decode()

    def add_draft(self, did: str, mid: str, *, thread: str, at: int = 9) -> None:
        self.drafts[did] = mid
        self.add_message(mid, thread=thread, labels=["DRAFT"], at=at)

    def add_raw_draft(self, did: str, mid: str, mail: bytes, *,
                      thread: str) -> None:
        """A draft made in Gmail web, from its raw mail (review round 2)."""
        self.drafts[did] = mid
        self.raws[mid] = base64.urlsafe_b64encode(mail).decode()
        self.messages[mid] = {
            "id": mid, "threadId": thread, "labelIds": ["DRAFT"],
            "internalDate": "9", "snippet": "",
            "payload": self._payload(message_from_bytes(mail))}

    def calls(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.seen if (m, p) == (method, path))

    # ── the transport ──

    def _payload(self, part: Message) -> dict[str, Any]:
        """The ``payload`` that Gmail gives for one MIME part. A file part
        gets an ``attachmentId``, as Gmail gives it (review round 1, F1)."""
        out: dict[str, Any] = {
            "mimeType": part.get_content_type(),
            "filename": part.get_filename() or "",
            "headers": [{"name": k, "value": str(v)} for k, v in part.items()]}
        if part.is_multipart():
            out["body"] = {"size": 0}
            out["parts"] = [self._payload(p) for p in part.get_payload()]
        elif part.get_filename():
            data = part.get_payload(decode=True) or b""
            aid = f"att-{len(self.files) + 1}"
            self.files[aid] = data
            out["body"] = {"attachmentId": aid, "size": len(data)}
        else:
            data = part.get_payload(decode=True) or b""
            out["body"] = {"data": base64.urlsafe_b64encode(data).decode(),
                           "size": len(data)}
        return out

    def _store_draft(self, did: str, body: dict[str, Any]) -> dict[str, Any]:
        message = body.get("message") or {}
        mid = self._next("m")
        thread = message.get("threadId") or mid
        payload = self._payload(_decode(message["raw"]))
        payload["headers"].append({"name": "From", "value": "me@em-g3a.test"})
        self.drafts[did] = mid
        self.raws[mid] = message["raw"]
        self.messages[mid] = {
            "id": mid, "threadId": thread, "labelIds": ["DRAFT"],
            "internalDate": "9", "snippet": "", "payload": payload}
        return {"id": did, "message": {"id": mid, "threadId": thread,
                                       "labelIds": ["DRAFT"]}}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "at-2"})
        # An upload write has the same path under ``/upload`` (EM-G3c-1), so
        # ``seen`` and ``fail`` key both URIs alike.
        upload = request.url.path.startswith(UPLOAD)
        path = request.url.path.removeprefix(UPLOAD if upload else API)
        if path == "/profile":
            return httpx.Response(200, json={"historyId": "1"})
        method = request.method
        self.seen.append((method, path, request.url.params))
        status = self.fail.get((method, path))
        if status:
            return httpx.Response(status, json={"error": {"code": status}})
        if upload:
            try:
                meta, mail = parse_upload(request)
            except ValueError as exc:
                return httpx.Response(400, json={"error": {"message": str(exc)}})
            self.uploads.append(SimpleNamespace(
                method=method, path=path, url=request.url, meta=meta,
                mail=mail, bearer=request.headers.get("authorization")))
            body = _upload_as_json(meta, mail)
        else:
            body = json.loads(request.content) if request.content else {}
        parts = path.strip("/").split("/")
        if parts[0] == "drafts":
            return await self._drafts(method, parts, body, request.url.params)
        if parts[0] == "messages":
            return self._messages(method, parts, body, request.url.params)
        if (method, parts[0]) == ("GET", "threads") and len(parts) == 2:
            listed = [m for m in self.messages.values()
                      if m["threadId"] == parts[1]]
            if listed:
                return httpx.Response(200, json={"id": parts[1],
                                                 "messages": listed})
        if (method, path) == ("GET", "/labels"):
            return httpx.Response(200, json={"labels": []})
        return _not_found()

    def _messages(self, method: str, parts: list[str], body: dict[str, Any],
                  params: httpx.QueryParams) -> httpx.Response:
        if (method, parts[1:]) == ("POST", ["send"]):
            self.sent.append(body)
            mid = self._next("s")
            return httpx.Response(200, json={
                "id": mid, "threadId": body.get("threadId") or mid})
        if method == "POST" and parts[2:] == ["trash"]:
            self.trashed.append(parts[1])
            return httpx.Response(200, json={"id": parts[1]})
        if (method == "GET" and len(parts) == 4 and parts[2] == "attachments"
                and parts[3] in self.files):
            data = self.files[parts[3]]
            return httpx.Response(200, json={
                "data": base64.urlsafe_b64encode(data).decode(),
                "size": len(data)})
        if method == "GET" and len(parts) == 2 and parts[1] in self.messages:
            found = self.messages[parts[1]]
            if params.get("format") == "raw":
                return httpx.Response(200, json={
                    "id": found["id"], "threadId": found["threadId"],
                    "raw": self.raws[parts[1]]})
            return httpx.Response(200, json=found)
        return _not_found()

    async def _drafts(self, method: str, parts: list[str], body: dict[str, Any],
                      params: httpx.QueryParams) -> httpx.Response:
        if (method, parts[1:]) == ("POST", []):
            self.saved.append(body)
            return httpx.Response(200, json=self._store_draft(
                self._next("r"), body))
        if (method, parts[1:]) == ("GET", []):
            return self._list_drafts(params)
        did = body.get("id", "") if parts[1:] == ["send"] else parts[-1]
        if did not in self.drafts:
            return _not_found()
        if (method, parts[1:]) == ("POST", ["send"]):
            self.sent_drafts.append(did)
            self.messages.pop(self.drafts.pop(did), None)
            return httpx.Response(200, json={"id": self._next("s")})
        if method == "PUT":
            self.saved.append(body)
            self.messages.pop(self.drafts[did], None)
            answer = self._store_draft(did, body)
            self.updated.append(answer["message"]["id"])
            if self.after_update is not None:
                await self.after_update(answer["message"]["id"])
            return httpx.Response(200, json=answer)
        if method == "DELETE":
            self.deleted_drafts.append(did)
            self.messages.pop(self.drafts.pop(did), None)
            return httpx.Response(204)
        return _not_found()

    def _list_drafts(self, params: httpx.QueryParams) -> httpx.Response:
        start = int(params.get("pageToken") or 0)
        ids = list(self.drafts)
        page = ids[start:start + self.page_size]
        answer: dict[str, Any] = {
            "drafts": [{"id": did, "message": {
                "id": self.drafts[did],
                "threadId": self.messages[self.drafts[did]]["threadId"]}}
                for did in page],
            "resultSizeEstimate": len(ids)}
        if start + self.page_size < len(ids):
            answer["nextPageToken"] = str(start + self.page_size)
        return httpx.Response(200, json=answer)


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _Gmail:
    """Send each ``httpx.AsyncClient`` that the provider builds to one fake.

    The rate-limit helper waits through ``gmail._wait``, so no test sleeps."""
    service = _Gmail()
    transport = httpx.MockTransport(service.handle)
    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(gmail_mod, "_wait", _no_sleep)
    return service


def _provider() -> GmailProvider:
    return GmailProvider({"access_token": "at-1", "refresh_token": "r-1"},
                         app=OAuthApp(client_id="cid", client_secret="secret"))


def _thread(fake: _Gmail) -> None:
    """Thread ``t-1``: two received mails, then a draft that is newer."""
    fake.add_message("p-1", thread="t-1", labels=["INBOX"], at=100,
                     message_id="<p1@contoso.test>")
    fake.add_message("p-2", thread="t-1", labels=["INBOX"], at=200,
                     message_id="<p2@contoso.test>",
                     references="<p0@contoso.test> <p1@contoso.test>")
    fake.add_draft("r-90", "m-90", thread="t-1", at=300)


# ── items 1 to 3: the one builder ───────────────────────────────────────────


def _alternative_parts(msg: Message) -> list[tuple[str, str]]:
    assert msg.get_content_type() == "multipart/alternative"
    return [(part.get_content_type(), part.get_payload(decode=True).decode())
            for part in msg.get_payload()]


async def test_send_has_a_text_part_and_an_html_part(fake: _Gmail) -> None:
    """GM-10, M1. The text part goes first and the HTML part second, and
    neither one carries the body of the other."""
    await _provider().send_message(
        to=[RAVI], subject="Hi", body_text="Hello\nRavi",
        body_html="<p>Hello</p><p>Ravi</p>")

    msg = _decode(fake.sent[-1]["raw"])
    assert _alternative_parts(msg) == [
        ("text/plain", "Hello\nRavi"), ("text/html", "<p>Hello</p><p>Ravi</p>")]
    assert msg["To"] == RAVI


async def test_a_body_with_no_html_is_one_text_part(fake: _Gmail) -> None:
    await _provider().send_message(to=[RAVI], subject="Hi", body_text="Hello")

    msg = _decode(fake.sent[-1]["raw"])
    assert msg.get_content_type() == "text/plain"
    assert msg.get_payload(decode=True).decode() == "Hello"


async def test_create_draft_has_a_text_part_and_an_html_part(fake: _Gmail) -> None:
    """GM-10, M1. ``create_draft`` uses the same builder."""
    await _provider().create_draft(
        to=[RAVI], subject="Hi", body_text="Hello", body_html="<b>Hello</b>")

    msg = _decode(fake.saved[-1]["message"]["raw"])
    assert _alternative_parts(msg) == [
        ("text/plain", "Hello"), ("text/html", "<b>Hello</b>")]


async def test_an_update_has_a_text_part_and_an_html_part(fake: _Gmail) -> None:
    fake.add_draft("r-7", "m-7", thread="t-7")

    await _provider().update_draft(
        "m-7", to=[RAVI], subject="Hi", body_text="Hello",
        body_html="<b>Hello</b>")

    msg = _decode(fake.saved[-1]["message"]["raw"])
    assert _alternative_parts(msg) == [
        ("text/plain", "Hello"), ("text/html", "<b>Hello</b>")]


@pytest.mark.parametrize("given, name, expected", [
    ("application/pdf", "quote.pdf", "application/pdf"),
    ("image/png", "scan.bin", "image/png"),
    ("application/octet-stream", "quote.pdf", "application/pdf"),
    (None, "photo.png", "image/png"),
    (None, "notes.g3a-unknown", "application/octet-stream"),
], ids=["given", "given-wins-over-the-name", "octet-stream-is-no-type",
        "from-the-name", "unknown"])
async def test_an_attachment_keeps_its_mime_type(
        fake: _Gmail, given: str | None, name: str, expected: str) -> None:
    """GM-12, M3. The body sits inside ``multipart/mixed``. Each file takes
    the type that the caller gives, else the type of its name, else
    ``application/octet-stream``."""
    att: dict[str, Any] = {"filename": name, "content": b"%PDF-1.7 bytes"}
    if given is not None:
        att["mime_type"] = given

    await _provider().send_message(
        to=[RAVI], subject="Files", body_text="See the file.",
        body_html="<p>See the file.</p>", attachments=[att])

    msg = _decode(fake.sent[-1]["raw"])
    assert msg.get_content_type() == "multipart/mixed"
    body, part = msg.get_payload()
    assert body.get_content_type() == "multipart/alternative"
    assert part.get_content_type() == expected
    assert part.get_content_disposition() == "attachment"
    assert part.get_payload(decode=True) == b"%PDF-1.7 bytes"


@pytest.mark.parametrize("name", [
    'Angebot "Müller" 2026.pdf', "見積書.pdf", "plain name.pdf",
], ids=["quote-and-umlaut", "cjk", "ascii"])
async def test_a_file_name_outside_ascii_survives(fake: _Gmail, name: str) -> None:
    """Item 3. ``add_header(..., filename=...)`` writes RFC 2231 when the
    name needs it, so a quote or a letter outside ASCII survives."""
    await _provider().create_draft(
        to=[RAVI], subject="Files", body_text="x",
        attachments=[{"filename": name, "content": b"x",
                      "mime_type": "application/pdf"}])

    raw = fake.saved[-1]["message"]["raw"]
    _body, part = _decode(raw).get_payload()
    assert part.get_filename() == name
    if not name.isascii():
        assert b"filename*=" in base64.urlsafe_b64decode(raw)


# ── items 4 and 5: threading ────────────────────────────────────────────────


def _parent_reads(fake: _Gmail) -> list[tuple[str, str, httpx.QueryParams]]:
    return [(m, p, q) for m, p, q in fake.seen
            if m == "GET" and p.startswith(("/messages/", "/threads/"))]


@pytest.mark.parametrize("act", ["send", "create_draft"])
async def test_a_reply_sets_in_reply_to_and_references(
        fake: _Gmail, act: str) -> None:
    """GM-11, M2. The parent comes from Gmail with ``format=metadata``, and
    ``References`` is the ``References`` of the parent, then its
    ``Message-ID``. The reply keeps ``threadId``."""
    _thread(fake)
    p = _provider()
    kwargs: dict[str, Any] = {"to": [RAVI], "subject": "Re: Quote",
                              "body_text": "Thanks",
                              "reply_to_message_id": "p-2", "thread_id": "t-1"}
    if act == "send":
        await p.send_message(**kwargs)
        sent = fake.sent[-1]
    else:
        await p.create_draft(**kwargs)
        sent = fake.saved[-1]["message"]

    msg = _decode(sent["raw"])
    assert msg["In-Reply-To"] == "<p2@contoso.test>"
    assert msg["References"].split() == [
        "<p0@contoso.test>", "<p1@contoso.test>", "<p2@contoso.test>"]
    assert sent["threadId"] == "t-1"
    [(_, path, query)] = _parent_reads(fake)
    assert path == "/messages/p-2"
    assert query.get("format") == "metadata"
    assert query.get_list("metadataHeaders") == ["Message-ID", "References"]


async def test_a_reply_with_only_a_thread_id_reads_the_newest_message_that_is_no_draft(
        fake: _Gmail) -> None:
    """Item 5 (E-A5). With only ``thread_id``, the parent is the newest
    message of the thread that is not a draft. The draft ``m-90`` is newer,
    and it is not the parent."""
    _thread(fake)

    await _provider().send_message(
        to=[RAVI], subject="Re: Quote", body_text="Thanks", thread_id="t-1")

    msg = _decode(fake.sent[-1]["raw"])
    assert msg["In-Reply-To"] == "<p2@contoso.test>"
    [(_, path, query)] = _parent_reads(fake)
    assert path == "/threads/t-1"
    assert query.get("format") == "metadata"


async def test_an_update_sets_in_reply_to_again(fake: _Gmail) -> None:
    """Item 5. Gmail replaces the whole draft, so the update sets the two
    headers again, from the thread."""
    _thread(fake)

    await _provider().update_draft(
        "m-90", to=[RAVI], subject="Re: Quote", body_text="Thanks v2",
        thread_id="t-1")

    sent = fake.saved[-1]["message"]
    msg = _decode(sent["raw"])
    assert msg["In-Reply-To"] == "<p2@contoso.test>"
    assert msg["References"].split()[-1] == "<p2@contoso.test>"
    assert sent["threadId"] == "t-1"


async def test_a_mail_that_is_no_reply_reads_no_parent(fake: _Gmail) -> None:
    await _provider().send_message(to=[RAVI], subject="Hi", body_text="New")

    msg = _decode(fake.sent[-1]["raw"])
    assert msg["In-Reply-To"] is None and msg["References"] is None
    assert _parent_reads(fake) == []


@pytest.mark.parametrize("path, status", [
    ("/messages/p-2", 500), ("/messages/p-2", 404), ("/messages/p-2", 429),
    ("/threads/t-1", 503),
], ids=["message-500", "message-404", "message-rate-limited", "thread-503"])
async def test_a_failed_parent_read_still_sends(
        fake: _Gmail, caplog: pytest.LogCaptureFixture, path: str,
        status: int) -> None:
    """E-A3, M7. The mail goes with no ``In-Reply-To`` and no
    ``References``, and one log line names no subject and no address."""
    _thread(fake)
    fake.fail[("GET", path)] = status
    reply_to = "p-2" if path.startswith("/messages/") else None

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        sent_id = await _provider().send_message(
            to=[RAVI], subject="Secret merger plan", body_text="Thanks",
            reply_to_message_id=reply_to, thread_id="t-1")

    assert sent_id.startswith("s-")
    msg = _decode(fake.sent[-1]["raw"])
    assert msg["In-Reply-To"] is None and msg["References"] is None
    assert fake.sent[-1]["threadId"] == "t-1"
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("gmail.parent_read_failed")]
    assert len(lines) == 1
    assert "Secret" not in lines[0] and RAVI not in lines[0]


# ── items 6 and 7: the draft ids ────────────────────────────────────────────


async def test_create_and_update_return_the_message_id(fake: _Gmail) -> None:
    """Item 6, M4. The local row holds the message id, the id that the sync
    finds, and never the draft id."""
    p = _provider()

    created = await p.create_draft(to=[RAVI], subject="Hi", body_text="v1")
    [(did, mid)] = fake.drafts.items()
    assert created == mid and created != did

    updated = await p.update_draft(created, to=[RAVI], body_text="v2")
    assert updated == fake.drafts[did] and updated != created


async def test_update_and_send_resolve_a_message_id_to_its_draft(
        fake: _Gmail) -> None:
    """Item 7. A draft made in Gmail web has only its message id. The update
    and the send find the draft id through ``drafts.list``, and the cache of
    the instance serves the second call."""
    fake.add_draft("r-7", "m-7", thread="t-7")
    p = _provider()

    new_id = await p.update_draft("m-7", to=[RAVI], body_text="edited")
    await p.send_draft(new_id)

    assert fake.calls("PUT", "/drafts/r-7") == 1
    assert fake.sent_drafts == ["r-7"]
    assert fake.calls("GET", "/drafts") == 1


async def test_a_message_id_that_is_no_draft_raises_a_clear_error(
        fake: _Gmail) -> None:
    """Item 7 (E-A5). A clear error, never an HTTP 400 of Gmail, and no
    write reaches Gmail."""
    fake.add_draft("r-7", "m-7", thread="t-7")
    p = _provider()

    for act in (lambda: p.update_draft("m-404", body_text="x"),
                lambda: p.send_draft("m-404")):
        with pytest.raises(GmailDraftNotFound) as caught:
            await act()
        assert not isinstance(caught.value, httpx.HTTPError)
        assert "m-404" in str(caught.value)
    assert fake.saved == [] and fake.sent_drafts == []


async def test_the_draft_lookup_reads_each_page(fake: _Gmail) -> None:
    """Item 7 (E-A5). The lookup follows ``nextPageToken`` and stops at the
    page that holds the id. A second lookup of a known id reads nothing."""
    fake.page_size = 2
    for i in range(1, 6):
        fake.add_draft(f"r-{i}", f"m-{i}", thread=f"t-{i}")
    p = _provider()

    await p.send_draft("m-5")
    tokens = [q.get("pageToken") for m, path, q in fake.seen
              if (m, path) == ("GET", "/drafts")]
    assert tokens == [None, "2", "4"]
    assert fake.sent_drafts == ["r-5"]

    await p.update_draft("m-3", body_text="x")
    assert fake.calls("GET", "/drafts") == 3


async def test_the_lookup_after_an_update_drops_the_old_id(fake: _Gmail) -> None:
    """After an update the old message id is no draft. A send of it reads
    ``drafts.list`` again and raises, so a caller that sends the old id
    fails loudly (E-A1)."""
    fake.add_draft("r-7", "m-7", thread="t-7")
    p = _provider()

    await p.update_draft("m-7", body_text="v2")
    with pytest.raises(GmailDraftNotFound):
        await p.send_draft("m-7")
    assert fake.sent_drafts == []


# ── E-A5: the trash of a draft ──────────────────────────────────────────────


async def test_trash_of_a_draft_discards_it(fake: _Gmail) -> None:
    """E-A5. A draft goes through ``drafts.delete`` (Discard draft), and
    each other message goes to Trash. One full ``drafts.list`` serves each
    trash of one instance."""
    fake.add_draft("r-7", "m-7", thread="t-7")
    fake.add_message("i-1", thread="t-8", labels=["INBOX"], at=5)
    p = _provider()

    await p.trash_message("m-7")
    await p.trash_message("i-1")
    await p.move_to_folder("i-1", "trash")

    assert fake.deleted_drafts == ["r-7"]
    assert fake.trashed == ["i-1", "i-1"]
    assert p.discarded_drafts == {"m-7"}
    assert fake.calls("GET", "/drafts") == 1


async def test_a_failed_draft_lookup_still_trashes_a_mail(
        fake: _Gmail, caplog: pytest.LogCaptureFixture) -> None:
    fake.fail[("GET", "/drafts")] = 500
    fake.add_message("i-1", thread="t-8", labels=["INBOX"], at=5)
    p = _provider()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await p.trash_message("i-1")

    assert fake.trashed == ["i-1"]
    assert p.discarded_drafts == set()
    assert any(r.getMessage().startswith("gmail.draft_lookup_failed")
               for r in caplog.records)


# ── E-A1: the signed send gives send_draft the id of the update ─────────────


class _Rows:
    """A session double for the send route: the draft row, the signature."""

    def __init__(self, draft: SimpleNamespace, signature: str) -> None:
        self.draft = draft
        self.signature = signature
        self.statements: list[str] = []

    async def execute(self, stmt: Any, _params: Any = None) -> Any:
        sql = str(stmt)
        self.statements.append(sql)
        row: Any = None
        if "FROM email_messages WHERE id" in sql and sql.startswith("SELECT"):
            row = self.draft
        elif "email_assistant_settings" in sql:
            row = SimpleNamespace(signature=self.signature)
        return SimpleNamespace(fetchone=lambda: row)


def _route_patches(monkeypatch: pytest.MonkeyPatch, db: Any, provider: Any) -> None:
    @asynccontextmanager
    async def _session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield db

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=provider, owner_email="me@em-g3a.test")

    async def _owner(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(drafting, "_tenant_session", _session)
    monkeypatch.setattr(drafting, "provider_session", _provider_session)
    monkeypatch.setattr(drafting, "_assert_account_owner", _owner)


_ME = UserContext(email="me@em-g3a.test", role=UserRole.EMPLOYEE)


def _listed(*addresses: str) -> list[dict[str, str]]:
    return [{"name": "", "email": a} for a in addresses]


def _draft_row(pmid: str, *, to: tuple[str, ...] = (RAVI,),
               cc: tuple[str, ...] = (), bcc: tuple[str, ...] = ()
               ) -> SimpleNamespace:
    return SimpleNamespace(
        provider_message_id=pmid, subject="Re: Quote", body_text="Thanks",
        to_addresses=_listed(*to), cc_addresses=_listed(*cc),
        bcc_addresses=_listed(*bcc), thread_id="t-1")


async def test_a_signed_send_sends_the_id_that_the_update_returns(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-A1, M6. The route signs the draft in place, then sends the message
    id that the update returned. The old id is no draft after the update."""
    _thread(fake)
    p = _provider()
    sent_with: list[str] = []
    real_send = p.send_draft

    async def _spy(draft_id: str) -> str | None:
        sent_with.append(draft_id)
        return await real_send(draft_id)

    p.send_draft = _spy  # type: ignore[method-assign]
    _route_patches(monkeypatch, _Rows(_draft_row("m-90"), "Asha, Contoso"), p)

    out = await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    assert out == {"sent": True}
    assert sent_with == fake.updated == [sent_with[0]]
    assert sent_with[0] != "m-90"
    assert fake.sent_drafts == ["r-90"]
    signed = _decode(fake.saved[-1]["message"]["raw"])
    text_part, html_part = signed.get_payload()
    assert "Asha, Contoso" in text_part.get_payload(decode=True).decode()
    assert html_part.get_content_type() == "text/html"


async def test_a_signed_send_of_a_provider_that_keeps_its_id_is_unchanged(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Outlook returns the same id from ``update_draft``, so its send gets
    the same id as before EM-G3a."""
    provider = SimpleNamespace(
        update_draft=AsyncMock(return_value="AAMk-1"),
        send_draft=AsyncMock(return_value=None))
    _route_patches(monkeypatch, _Rows(_draft_row("AAMk-1"), "Asha"), provider)

    await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    provider.send_draft.assert_awaited_once_with("AAMk-1")


# ── review round 1: an update keeps the files, and a 409 for a lost draft ───


def _file_parts(raw: str) -> list[tuple[str, str, bytes]]:
    """(name, type, bytes) of each file part of a raw mail."""
    return [(p.get_filename(), p.get_content_type(), p.get_payload(decode=True))
            for p in _decode(raw).walk() if p.get_filename()]


async def test_an_update_keeps_the_files_of_the_draft(fake: _Gmail) -> None:
    """Review round 1, F1. Gmail replaces the whole draft, so an update with
    no ``attachments`` still carries each file of the draft. Files that the
    caller gives are added to them."""
    p = _provider()
    mid = await p.create_draft(
        to=[RAVI], subject="Quote", body_text="v1",
        attachments=[{"filename": "quote.pdf", "content": b"%PDF-1",
                      "mime_type": "application/pdf"}])

    mid = await p.update_draft(mid, to=[RAVI], body_text="v2")
    assert _file_parts(fake.saved[-1]["message"]["raw"]) == [
        ("quote.pdf", "application/pdf", b"%PDF-1")]

    await p.update_draft(mid, to=[RAVI], body_text="v3", attachments=[
        {"filename": "photo.png", "content": b"PNG", "mime_type": "image/png"}])
    assert _file_parts(fake.saved[-1]["message"]["raw"]) == [
        ("quote.pdf", "application/pdf", b"%PDF-1"),
        ("photo.png", "image/png", b"PNG")]


async def test_a_failed_file_read_fails_the_update(fake: _Gmail) -> None:
    """An update that cannot read the files of the draft writes nothing,
    because an update without them would delete them in Gmail."""
    fake.add_draft("r-7", "m-7", thread="t-7")
    fake.fail[("GET", "/messages/m-7")] = 503

    with pytest.raises(httpx.HTTPStatusError):
        await _provider().update_draft("m-7", body_text="v2")
    assert fake.saved == []


async def test_a_draft_that_changed_in_gmail_answers_409(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 1, item 5. ``GmailDraftNotFound`` becomes a 409 with a
    clear message on the save and on the send, never a 500."""
    from fastapi import HTTPException

    fake.add_draft("r-7", "m-7", thread="t-7")
    for signature in ("", "Asha"):
        _route_patches(monkeypatch, _Rows(_draft_row("m-gone"), signature),
                       _provider())
        with pytest.raises(HTTPException) as caught:
            await drafting.send_draft_endpoint(
                drafting.DraftSendRequest(account_id="acc-1",
                                          draft_id="local-1"),
                BackgroundTasks(), user=_ME)
        assert caught.value.status_code == 409
        assert caught.value.detail == drafting.DRAFT_CHANGED_DETAIL

    _route_patches(monkeypatch, _Rows(_draft_row("m-gone"), ""), _provider())
    with pytest.raises(HTTPException) as caught:
        await drafting.upsert_draft(drafting.DraftUpsertRequest(
            account_id="acc-1", draft_id="local-1", body="v2"), user=_ME)
    assert caught.value.status_code == 409
    assert fake.saved == [] and fake.sent_drafts == []


# ── review round 2: the Cc, the Bcc, the IMAP fallback, an attached mail ───

LEAD = "lead@contoso-em-g3a.test"
AUDIT = "audit@contoso-em-g3a.test"


def _outlook() -> tuple[Any, AsyncMock]:
    """The real Outlook provider on an ``AsyncMock`` Graph client."""
    from email_ingestion.providers.outlook import OutlookProvider

    outlook = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    client = AsyncMock()
    answer = SimpleNamespace(status_code=200, headers={},
                             json=lambda: {"id": "AAMk-1"},
                             raise_for_status=lambda: None)
    client.post.return_value = answer
    client.patch.return_value = answer
    outlook._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]
    return outlook, client


@pytest.mark.parametrize("cc, bcc", [((), ()), ((LEAD,), (AUDIT,))],
                         ids=["no-cc-no-bcc", "cc-and-bcc"])
async def test_an_outlook_signed_send_patches_only_the_lists_the_row_holds(
        monkeypatch: pytest.MonkeyPatch, cc: tuple[str, ...],
        bcc: tuple[str, ...]) -> None:
    """Review round 2, P3-1 and P3-2. A row with no Cc sends no
    ``ccRecipients`` key, so Outlook keeps its own Cc. A row with a Cc and a
    Bcc sends both lists."""
    outlook, client = _outlook()
    _route_patches(monkeypatch, _Rows(_draft_row("AAMk-1", cc=cc, bcc=bcc),
                                      "Asha"), outlook)

    await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    [patch] = client.patch.await_args_list
    sent = patch.kwargs["json"]
    if cc:
        assert [r["emailAddress"]["address"] for r in sent["ccRecipients"]] == [
            LEAD]
        assert [r["emailAddress"]["address"]
                for r in sent["bccRecipients"]] == [AUDIT]
    else:
        assert "ccRecipients" not in sent and "bccRecipients" not in sent
    assert [c.args[0] for c in client.post.await_args_list] == [
        "/me/messages/AAMk-1/send"]


async def test_a_signed_gmail_send_keeps_the_bcc(
        fake: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 2, P3-2. Gmail rebuilds the whole draft on the signing
    update, so the update carries the Bcc of the row."""
    _thread(fake)
    _route_patches(monkeypatch, _Rows(_draft_row("m-90", bcc=(AUDIT,)),
                                      "Asha"), _provider())

    await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    assert _decode(fake.saved[-1]["message"]["raw"])["Bcc"] == AUDIT
    assert fake.sent_drafts == ["r-90"]


@pytest.mark.parametrize("signature", ["Asha", ""], ids=["signed", "unsigned"])
async def test_the_imap_fallback_sends_each_to_cc_and_bcc(
        monkeypatch: pytest.MonkeyPatch, signature: str) -> None:
    """Review round 2, P3-3. IMAP has no update or send of a draft, so the
    route sends a new mail. That mail goes to each To, Cc and Bcc address of
    the row."""
    from email_ingestion.providers.imap import IMAPProvider

    imap = IMAPProvider({"smtp_host": "smtp.example.com", "smtp_port": 587,
                         "smtp_username": "me@example.com",
                         "smtp_password": "pw"})
    imap.send_message = AsyncMock(return_value="sent")  # type: ignore[method-assign]
    imap.trash_message = AsyncMock(return_value=None)  # type: ignore[method-assign]
    row = _draft_row("draft-Drafts", to=(RAVI, "meera@contoso-em-g3a.test"),
                     cc=(LEAD,), bcc=(AUDIT,))
    _route_patches(monkeypatch, _Rows(row, signature), imap)

    await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    sent = imap.send_message.await_args.kwargs
    assert sent["to"] == [RAVI, "meera@contoso-em-g3a.test"]
    assert sent["cc"] == [LEAD] and sent["bcc"] == [AUDIT]
    imap.trash_message.assert_awaited_once_with("draft-Drafts")


def _forwarded_draft(name: str | None) -> bytes:
    """A draft that forwards a mail as an attachment. The attached mail holds
    a text part and ``inner.pdf``, as ``fixtures/gmail/i_forwarded_rfc822``
    holds a nested mail."""
    from email.mime.base import MIMEBase
    from email.mime.message import MIMEMessage
    from email.mime.multipart import MIMEMultipart

    inner = MIMEMultipart("mixed")
    inner["From"] = f"Ravi <{RAVI}>"
    inner["Subject"] = "Original"
    inner["Message-ID"] = "<original@contoso.test>"
    inner.attach(MIMEText("The original mail."))
    pdf = MIMEBase("application", "pdf")
    pdf.set_payload(b"%PDF-inner")
    from email import encoders

    encoders.encode_base64(pdf)
    pdf.add_header("Content-Disposition", "attachment", filename="inner.pdf")
    inner.attach(pdf)
    outer = MIMEMultipart("mixed")
    outer["To"] = RAVI
    outer["Subject"] = "Fwd: Original"
    outer.attach(MIMEText("See the mail below."))
    forwarded = MIMEMessage(inner)
    if name:
        forwarded.add_header("Content-Disposition", "attachment", filename=name)
    else:
        forwarded.add_header("Content-Disposition", "attachment")
    outer.attach(forwarded)
    return outer.as_bytes()


@pytest.mark.parametrize("name, kept_as", [
    (None, "attached.eml"), ("forward.eml", "forward.eml"),
], ids=["no-name", "named-eml-with-inner-pdf"])
async def test_an_attached_mail_stays_one_file_after_an_update(
        fake: _Gmail, name: str | None, kept_as: str) -> None:
    """Review round 2, P3-4. Gmail opens an attached mail into its parts.
    The update reads the draft as one raw mail and never opens a file, so
    the draft keeps exactly one attached mail, with ``inner.pdf`` still in
    it, and no ``inner.pdf`` at the top."""
    fake.add_raw_draft("r-f", "m-f", _forwarded_draft(name), thread="t-f")

    await _provider().update_draft("m-f", to=[RAVI], body_text="v2")

    rebuilt = _decode(fake.saved[-1]["message"]["raw"])
    top = rebuilt.get_payload()
    assert [part.get_content_type() for part in top] == [
        "text/plain", "message/rfc822"]
    mail_part = top[1]
    assert mail_part.get_filename() == kept_as
    [inner] = mail_part.get_payload()
    assert inner["Subject"] == "Original"
    assert [(p.get_filename(), p.get_payload(decode=True))
            for p in inner.walk() if p.get_filename()] == [
        ("inner.pdf", b"%PDF-inner")]


# ── R8: the local draft row (items 6 and 8, E-A2, E-A5) ─────────────────────


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _account(admin: Any, *, org: str, owner: str, provider: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, "
            "sync_status, organization_id) "
            "VALUES (:u, :prov, :m, 'x', true, 300, 'idle', CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "prov": provider, "m": owner, "o": org}).scalar_one())


def _rows(admin: Any, account_id: str) -> dict[str, tuple[str, str]]:
    """Each row of the mailbox: its id, then its provider id and folder."""
    with admin.connect() as c:
        got = c.execute(text(
            "SELECT id::text AS id, provider_message_id, folder "
            "FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
            {"a": account_id}).fetchall()
    return {r.id: (r.provider_message_id, r.folder) for r in got}


@asynccontextmanager
async def _as_member(m: SimpleNamespace) -> AsyncIterator[None]:
    """Bind the organization and point the shared engine at the app role."""
    token = bind_tenant(m.org)
    try:
        async with tenant_engine_scope(_dsn(m.p)):
            yield
    finally:
        release_tenant(token)


@asynccontextmanager
async def _statements(marker: str) -> AsyncIterator[list[str]]:
    """Each statement sent to Postgres that names ``marker``."""
    from acb_common import db as shared

    eng = shared.get_engine().sync_engine
    seen: list[str] = []

    def _record(_conn, _cur, statement, _params, _ctx, _many):
        if marker in statement:
            seen.append(statement)

    event.listen(eng, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(eng, "before_cursor_execute", _record)


@pytest.fixture()
def mailbox(promoted, app_engine, fake, monkeypatch):  # noqa: F811
    """One Gmail mailbox of one member in org B, and the draft routes with
    the REAL Gmail provider on the fake. ``provider_session`` builds one new
    provider for each request, as the real seam does (E-A5)."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"g3a-{uuid.uuid4().hex[:8]}@em-g3a.test"
    aid = _account(p.admin_engine, org=p.org_b, owner=owner, provider="gmail")
    m = SimpleNamespace(
        p=p, admin=p.admin_engine, org=p.org_b, aid=aid, owner=owner,
        fake=fake, providers=[],
        me=UserContext(email=owner, role=UserRole.EMPLOYEE,
                       organization_id=p.org_b))

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        provider = _provider()
        m.providers.append(provider)
        yield SimpleNamespace(provider=provider, owner_email=owner,
                              account_id=aid, authed=True)

    monkeypatch.setattr(drafting, "provider_session", _provider_session)
    try:
        yield m
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
        _purge(p.admin_engine, [aid])


async def _save(m: SimpleNamespace, **fields: Any) -> dict[str, Any]:
    """One PUT /email/drafts of the member: the real route, the real SQL."""
    req = drafting.DraftUpsertRequest(account_id=m.aid, **fields)
    async with _as_member(m):
        return await drafting.upsert_draft(req, user=m.me)


async def _write_sync_copy(m: SimpleNamespace, mid: str) -> None:
    """The sweep of the Drafts label for one message: the real parse of what
    Gmail holds, and the real upsert with the reclaim flag of Gmail. It reads
    the state of the fake, so it can run inside a fake answer."""
    msg = GmailProvider({"access_token": "x"})._parse_gmail_message(
        m.fake.messages[mid])
    async with core._tenant_session(m.org) as db:
        await upsert_message(db, m.aid, msg,
                             reclaim=GmailProvider.REKEYS_MESSAGE_IDS)


async def _sync(m: SimpleNamespace, mid: str) -> None:
    async with _as_member(m):
        await _write_sync_copy(m, mid)


async def _send(m: SimpleNamespace, local_id: str) -> dict[str, Any]:
    """One POST /email/drafts/send of the member: the real route."""
    async with _as_member(m):
        return await drafting.send_draft_endpoint(
            drafting.DraftSendRequest(account_id=m.aid, draft_id=local_id),
            BackgroundTasks(), user=m.me)


def _sign(m: SimpleNamespace, signature: str) -> None:
    """The signature of the mailbox, so the send signs the draft."""
    with m.admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, signature, "
            "organization_id) VALUES (CAST(:a AS uuid), :s, CAST(:o AS uuid))"),
            {"a": m.aid, "s": signature, "o": m.org})


def _seed_row(admin: Any, *, org: str, account_id: str, pmid: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "folder, from_address, to_addresses, organization_id) VALUES "
            "(CAST(:a AS uuid), :p, 'drafts', '{}'::jsonb, '[]'::jsonb, "
            "CAST(:o AS uuid)) RETURNING id::text"),
            {"a": account_id, "p": pmid, "o": org}).scalar_one())


def _graph_answer(value: dict[str, Any] | None = None) -> Any:
    answer = SimpleNamespace(status_code=200, headers={})
    answer.json = lambda: value or {}
    answer.raise_for_status = lambda: None
    return answer


@_DB_GATE
class TestTheLocalDraftRow:
    """Item 8, O-GM-2 and E-A2 on the real SQL, as ``acb_app_h3rls``."""

    async def test_a_draft_saved_here_is_one_row_after_the_sync(self, mailbox):
        """M4. The save stores the MESSAGE id of the draft, which is the id
        that the sync finds. So the sync writes onto the same row."""
        m = mailbox
        saved = await _save(m, to=[RAVI], subject="Quote", body="v1")
        [(did, mid)] = m.fake.drafts.items()
        assert saved["provider_message_id"] == mid != did

        await _sync(m, mid)

        assert _rows(m.admin, m.aid) == {saved["id"]: (mid, "drafts")}, (
            "one draft has two rows after the sync")

    async def test_an_update_moves_the_row_to_the_new_message_id(self, mailbox):
        """M5. Gmail gives the draft a new message id at each update. The
        save moves the local row to it by its row id, so the row id stays."""
        m = mailbox
        first = await _save(m, to=[RAVI], subject="Quote", body="v1")
        local = first["id"]

        second = await _save(m, draft_id=local, to=[RAVI], subject="Quote",
                             body="v2")

        [(_, new_mid)] = m.fake.drafts.items()
        assert new_mid != first["provider_message_id"]
        assert second["id"] == local
        assert second["provider_message_id"] == new_mid
        assert second["body_text"].startswith("v2")
        assert _rows(m.admin, m.aid) == {local: (new_mid, "drafts")}
        await _sync(m, new_mid)
        assert _rows(m.admin, m.aid) == {local: (new_mid, "drafts")}
        # One provider for each request, so the cache never spans two saves.
        assert len(m.providers) == 2 and m.providers[0] is not m.providers[1]

    async def test_a_sync_copy_of_the_new_id_folds_into_the_local_row(
            self, mailbox):
        """E-A2, M8. A sync commits the new message id as its own row after
        Gmail answers the update and before the save moves the local row.
        The save deletes that copy and moves the local row, in one
        transaction. One row stays, with the row id of the local row."""
        m = mailbox
        first = await _save(m, to=[RAVI], subject="Quote", body="v1")
        local = first["id"]
        rows_at_the_race: list[int] = []

        async def _sync_wins_the_race(new_mid: str) -> None:
            await _write_sync_copy(m, new_mid)
            rows_at_the_race.append(len(_rows(m.admin, m.aid)))

        m.fake.after_update = _sync_wins_the_race
        second = await _save(m, draft_id=local, to=[RAVI], subject="Quote",
                             body="v2")

        [(_, new_mid)] = m.fake.drafts.items()
        assert rows_at_the_race == [2], "the sync copy was not there first"
        assert second["id"] == local
        assert _rows(m.admin, m.aid) == {local: (new_mid, "drafts")}

    async def test_an_update_that_keeps_its_id_moves_nothing(
            self, mailbox, monkeypatch):
        """Outlook returns the same id from ``update_draft``. The save then
        runs no statement of the move, and the upsert updates the row, as
        before EM-G3a."""
        m = mailbox
        provider = SimpleNamespace(
            create_draft=AsyncMock(return_value="AAMk-1"),
            update_draft=AsyncMock(return_value="AAMk-1"))

        @asynccontextmanager
        async def _outlook_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
            yield SimpleNamespace(provider=provider, owner_email=m.owner)

        monkeypatch.setattr(drafting, "provider_session", _outlook_session)
        first = await _save(m, to=[RAVI], subject="Quote", body="v1")
        async with _as_member(m), _statements("AND id <> ") as seen:
            second = await drafting.upsert_draft(
                drafting.DraftUpsertRequest(
                    account_id=m.aid, draft_id=first["id"], to=[RAVI],
                    subject="Quote", body="v2"), user=m.me)

        assert seen == []
        assert second["id"] == first["id"]
        assert second["body_text"].startswith("v2")
        assert _rows(m.admin, m.aid) == {first["id"]: ("AAMk-1", "drafts")}

    async def test_a_discarded_gmail_draft_leaves_no_row(
            self, mailbox, monkeypatch):
        """E-A5. Discard calls DELETE /email/messages/{id}. For a Gmail draft
        the provider calls ``drafts.delete``, and the route deletes the local
        row, because Gmail keeps no copy in Trash. A mail still goes to
        Trash, and its row stays there."""
        m = mailbox
        draft = await _save(m, to=[RAVI], subject="Quote", body="v1")
        [did] = m.fake.drafts
        m.fake.add_message("i-1", thread="t-i", labels=["INBOX"], at=5)
        await _sync(m, "i-1")
        [mail] = [rid for rid, (pmid, _) in _rows(m.admin, m.aid).items()
                  if pmid == "i-1"]
        provider = _provider()

        async def _loader(db: Any, message_id: str, _user: str) -> Any:
            row = (await db.execute(text(
                "SELECT provider_message_id FROM email_messages "
                "WHERE id = :id"), {"id": message_id})).fetchone()
            return provider, row.provider_message_id, m.aid, None

        async def _no_persist(*_a: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(messages_mod, "_provider_for_message", _loader)
        monkeypatch.setattr(messages_mod, "_persist_rotated_creds", _no_persist)
        async with _as_member(m):
            await messages_mod.delete_message(draft["id"], user=m.me)
            await messages_mod.delete_message(mail, user=m.me)

        assert m.fake.deleted_drafts == [did]
        assert m.fake.trashed == ["i-1"]
        assert _rows(m.admin, m.aid) == {mail: ("i-1", "trash")}

    # ── review round 1 ──

    async def test_an_outlook_draft_to_two_people_and_a_cc_sends_to_all_three(
            self, mailbox, monkeypatch):
        """F2, a live Outlook defect. The row kept the first To address only,
        and the signed send PATCHed the draft with it, so the second person
        got nothing. The row now keeps each To, Cc and Bcc address, and the
        PATCH carries all three people. No file goes up again."""
        from email_ingestion.providers.outlook import (
            OutlookProvider,
        )

        m = mailbox
        outlook = OutlookProvider({"access_token": "x", "refresh_token": "y"})
        client = AsyncMock()
        client.post.return_value = _graph_answer({"id": "AAMk-1"})
        client.patch.return_value = _graph_answer({"id": "AAMk-1"})
        outlook._get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

        @asynccontextmanager
        async def _outlook_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
            yield SimpleNamespace(provider=outlook, owner_email=m.owner)

        monkeypatch.setattr(drafting, "provider_session", _outlook_session)
        _sign(m, "Asha, Contoso")
        two = [RAVI, "meera@contoso-em-g3a.test"]
        saved = await _save(m, to=two, cc=["lead@contoso-em-g3a.test"],
                            subject="Quote", body="Hello both")

        await _send(m, saved["id"])

        [patch] = client.patch.await_args_list
        assert patch.args[0] == "/me/messages/AAMk-1"
        sent = patch.kwargs["json"]
        assert [r["emailAddress"]["address"] for r in sent["toRecipients"]] == two
        assert [r["emailAddress"]["address"] for r in sent["ccRecipients"]] == [
            "lead@contoso-em-g3a.test"]
        assert "bccRecipients" not in sent
        assert "Asha, Contoso" in sent["body"]["content"]
        posts = [c.args[0] for c in client.post.await_args_list]
        assert posts == ["/me/messages", "/me/messages/AAMk-1/send"]
        assert _rows(m.admin, m.aid) == {}

    async def test_a_signed_gmail_send_keeps_the_cc_and_the_file(self, mailbox):
        """F1. Gmail replaces the whole draft on the signing update. The
        update carries the Cc of the row and the file of the draft, so the
        mail goes out with both."""
        m = mailbox
        _sign(m, "Asha, Contoso")
        saved = await _save(
            m, to=[RAVI], cc=["lead@contoso-em-g3a.test"], subject="Quote",
            body="See the file.", attachments=[{
                "filename": "quote.pdf", "mime_type": "application/pdf",
                "content_b64": base64.b64encode(b"%PDF-1.7").decode()}])
        [did] = m.fake.drafts

        await _send(m, saved["id"])

        signed = m.fake.saved[-1]["message"]["raw"]
        msg = _decode(signed)
        assert msg["Cc"] == "lead@contoso-em-g3a.test"
        assert msg["To"] == RAVI
        assert _file_parts(signed) == [
            ("quote.pdf", "application/pdf", b"%PDF-1.7")]
        body = next(p for p in msg.walk()
                    if p.get_content_type() == "text/plain")
        assert "Asha, Contoso" in body.get_payload(decode=True).decode()
        assert m.fake.sent_drafts == [did]
        assert _rows(m.admin, m.aid) == {}

    async def test_a_failed_send_leaves_the_row_on_the_live_id(self, mailbox):
        """F3. The signing update gives the draft a new message id. The move
        commits before ``drafts.send``, so a send that fails leaves the row
        on the live id, and the next Send works."""
        m = mailbox
        _sign(m, "Asha, Contoso")
        saved = await _save(m, to=[RAVI], subject="Quote", body="v1")
        [did] = m.fake.drafts
        m.fake.fail[("POST", "/drafts/send")] = 500

        with pytest.raises(httpx.HTTPStatusError):
            await _send(m, saved["id"])

        live = m.fake.drafts[did]
        assert live == m.fake.updated[-1] != saved["provider_message_id"]
        assert _rows(m.admin, m.aid) == {saved["id"]: (live, "drafts")}

        del m.fake.fail[("POST", "/drafts/send")]
        assert await _send(m, saved["id"]) == {"sent": True}
        assert m.fake.sent_drafts == [did]
        assert _rows(m.admin, m.aid) == {}

    async def test_the_move_never_touches_another_mailbox(self, mailbox):
        """F4. The new message id also sits in another mailbox of the same
        organization, and in a mailbox of another organization. The move
        deletes the copy of THIS mailbox only, so both rows stay."""
        m = mailbox
        p = m.p
        other = _account(m.admin, org=m.org, provider="gmail",
                         owner=f"g3a-other-{uuid.uuid4().hex[:8]}@em-g3a.test")
        foreign = _account(m.admin, org=p.org_a, provider="gmail",
                           owner=f"g3a-far-{uuid.uuid4().hex[:8]}@em-g3a.test")
        kept: dict[str, str] = {}

        async def _same_id_elsewhere(new_mid: str) -> None:
            kept[other] = _seed_row(m.admin, org=m.org, account_id=other,
                                    pmid=new_mid)
            kept[foreign] = _seed_row(m.admin, org=p.org_a,
                                      account_id=foreign, pmid=new_mid)

        try:
            first = await _save(m, to=[RAVI], subject="Quote", body="v1")
            m.fake.after_update = _same_id_elsewhere
            await _save(m, draft_id=first["id"], to=[RAVI], subject="Quote",
                        body="v2")

            new_mid = m.fake.updated[-1]
            assert _rows(m.admin, m.aid) == {first["id"]: (new_mid, "drafts")}
            assert _rows(m.admin, other) == {kept[other]: (new_mid, "drafts")}
            assert _rows(m.admin, foreign) == {
                kept[foreign]: (new_mid, "drafts")}
        finally:
            with m.admin.begin() as c:
                c.execute(text(
                    "DELETE FROM email_messages WHERE account_id = ANY("
                    "CAST(:a AS uuid[]))"), {"a": [other, foreign]})
            _purge(m.admin, [other, foreign])
