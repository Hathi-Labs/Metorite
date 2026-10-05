"""WS-17 EM-G3c-1 — the size of a Gmail mail with files (EM-G3a-f8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.3b, items 1 to 9.

Three Gmail writes sent the whole mail as base64url text in the JSON field
``raw``, on the plain URI. Google documents no size limit for that request.
Each write also has an upload URI, and these fences pin its use.

* A mail with a file, or a built mail over 1 MiB, goes to the upload URI as
  ``multipart/related`` with ``uploadType=multipart`` (items 2 to 4). Any
  other mail keeps the plain URI and ``raw``.
* ``update_draft`` decides on the files after the read-back of the draft,
  never on ``attachments`` (item 2).
* Part 1 is the metadata, ``{"threadId": T}`` for ``messages.send`` and
  ``{"message": {"threadId": T}}`` for a draft write. Part 2 is the raw mail
  (item 4).
* A mail over 36,700,160 bytes raises ``GmailMailTooLarge`` before its write.
  A 413 of Google raises it too (items 5 and 6).
* The three routes answer 413 with "This mail is too large to send." (item 7).
* The upload POST of a send and of a new draft gets one try. The PUT of an
  update keeps its retry on a 429 (item 8).

The fences drive the REAL ``GmailProvider`` through the real ``_get_client``
on ``httpx.MockTransport``. ``gmail`` is the stateful fake of
``test_gmail_send_and_drafts.py``, which parses each upload (item 9).
``limited`` is the scripted fake of ``test_gmail_rate_limits.py``.

Mutations, each run once with this file (as built, the spec table):

* M1 sends a draft with a file to the plain URI. The first fence fails.
* M2 moves the limit check after the write. The limit fence fails.
* M3 maps the 413 to a 500. The route fence fails.
* M5 routes on ``attachments``. The read-back fence fails.
* M6 makes an upload send repeatable. The one-try fence fails.

**Hermetic.** No database.

Run::

    uv run pytest tests/unit/test_gmail_mail_size.py -v -rs
"""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from email import message_from_bytes
from types import SimpleNamespace
from typing import Any

import email_ingestion.providers.gmail as gmail_mod
import httpx
import pytest
from email_ingestion.providers.base import ProviderMailTooLarge
from email_ingestion.providers.gmail import (
    GMAIL_MAIL_MAX_BYTES,
    GMAIL_PLAIN_MAX_BYTES,
    GMAIL_UPLOAD_BASE,
    GmailMailTooLarge,
    GmailProvider,
    GmailRateLimited,
    _build_gmail_mail,
    _repeatable,
)
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import drafting
from gateway.routes.email.transport import send as send_mod
from gateway.routes.email.transport.send import SendAttachment, SendEmailRequest

from tests.unit import test_gmail_rate_limits as limits_mod

# ``fake`` is a fixture, so the import is load-bearing.
from tests.unit.test_gmail_send_and_drafts import (  # noqa: F401
    _ME,
    RAVI,
    _decode,
    _draft_row,
    _file_parts,
    _Gmail,
    _provider,
    _route_patches,
    _Rows,
    _thread,
    fake,
)

PDF = "application/pdf"
QUOTE = b"%PDF-1.7 the quote"
DETAIL = "This mail is too large to send."


def _file(content: bytes = QUOTE) -> dict[str, Any]:
    return {"filename": "quote.pdf", "content": content, "mime_type": PDF}


@pytest.fixture()
def gmail(fake: _Gmail) -> _Gmail:  # noqa: F811
    """The stateful fake Gmail of EM-G3a, with the upload parse of item 9."""
    return fake


@pytest.fixture()
def limited(monkeypatch: pytest.MonkeyPatch) -> limits_mod._Gmail:
    """The scripted fake Gmail of EM-G4a. ``on`` answers both URIs, and
    ``_wait`` records each wait and does not sleep."""
    service = limits_mod._Gmail()
    transport = httpx.MockTransport(service.handle)
    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    async def _no_sleep(seconds: float) -> None:
        service.waits.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(gmail_mod, "_wait", _no_sleep)
    return service


def _writes(gmail: _Gmail) -> list[tuple[str, str]]:
    """Each write request that reached the fake, on either URI."""
    return [(m, p) for m, p, _ in gmail.seen
            if m in ("POST", "PUT")
            and (p in ("/messages/send", "/drafts") or p.startswith("/drafts/r-"))]


def _raw_draft_with_a_file() -> bytes:
    """A draft that a member made in Gmail web, with ``quote.pdf``."""
    return _build_gmail_mail(to=[RAVI], subject="Quote", body_text="v1",
                             body_html=None, attachments=[_file()])


# ── items 2 to 4: the upload URI ────────────────────────────────────────────


@pytest.mark.parametrize("reply", [False, True], ids=["new", "reply"])
async def test_a_draft_with_a_file_goes_to_the_upload_uri(
        gmail: _Gmail, reply: bool) -> None:
    """M1. ``drafts.create`` with a file goes to the upload URI of Google,
    with ``uploadType=multipart``. The request goes through the client of
    ``_get_client``, so it carries the bearer. The draft holds the file."""
    kwargs: dict[str, Any] = {}
    if reply:
        _thread(gmail)
        kwargs = {"reply_to_message_id": "p-2", "thread_id": "t-1"}

    mid = await _provider().create_draft(
        to=[RAVI], subject="Quote", body_text="See the file.",
        attachments=[_file()], **kwargs)

    [upload] = gmail.uploads
    assert (upload.method, upload.path) == ("POST", "/drafts")
    assert GMAIL_UPLOAD_BASE == "https://gmail.googleapis.com/upload/gmail/v1"
    assert str(upload.url) == (
        "https://gmail.googleapis.com/upload/gmail/v1/users/me/drafts"
        "?uploadType=multipart")
    assert upload.bearer == "Bearer at-1"
    assert _file_parts(gmail.saved[-1]["message"]["raw"]) == [
        ("quote.pdf", PDF, QUOTE)]
    assert mid in gmail.drafts.values()


@pytest.mark.parametrize("act", ["send", "create", "update"])
async def test_a_draft_with_no_file_keeps_the_plain_uri(
        gmail: _Gmail, act: str) -> None:
    """Item 2. A small mail with no file keeps the plain URI and ``raw``, for
    each of the three writes, as before EM-G3c."""
    gmail.add_draft("r-7", "m-7", thread="t-7")
    p = _provider()

    if act == "send":
        await p.send_message(to=[RAVI], subject="Hi", body_text="Hello")
        body = gmail.sent[-1]
    elif act == "create":
        await p.create_draft(to=[RAVI], subject="Hi", body_text="Hello")
        body = gmail.saved[-1]["message"]
    else:
        await p.update_draft("m-7", to=[RAVI], body_text="Hello")
        body = gmail.saved[-1]["message"]

    assert gmail.uploads == []
    assert _decode(body["raw"]).get_payload(decode=True) == b"Hello"
    [write] = [q for m, _p, q in gmail.seen if m in ("POST", "PUT")]
    assert "uploadType" not in write


@pytest.mark.parametrize("act", ["send", "create", "update"])
@pytest.mark.parametrize("size, uploads", [(700_000, False), (800_000, True)],
                         ids=["under-1-mib", "over-1-mib"])
async def test_a_large_mail_with_no_file_goes_to_the_upload_uri(
        gmail: _Gmail, act: str, size: int, uploads: bool) -> None:
    """Item 2. A built mail over 1 MiB goes to the upload URI, also with no
    file. A mail under it keeps the plain URI. The text of the body arrives
    whole on either URI."""
    gmail.add_draft("r-7", "m-7", thread="t-7")
    text = "y" * size
    p = _provider()

    if act == "send":
        await p.send_message(to=[RAVI], subject="Long", body_text=text)
        raw = gmail.sent[-1]["raw"]
    elif act == "create":
        await p.create_draft(to=[RAVI], subject="Long", body_text=text)
        raw = gmail.saved[-1]["message"]["raw"]
    else:
        await p.update_draft("m-7", to=[RAVI], body_text=text)
        raw = gmail.saved[-1]["message"]["raw"]

    assert GMAIL_PLAIN_MAX_BYTES == 1_048_576
    assert bool(gmail.uploads) is uploads
    if uploads:
        assert len(gmail.uploads[0].mail) > GMAIL_PLAIN_MAX_BYTES
    assert _decode(raw).get_payload(decode=True).decode() == text


async def test_a_send_with_a_file_goes_to_the_upload_uri(gmail: _Gmail) -> None:
    """Items 2 and 3. ``messages.send`` with a file goes to its upload URI.
    The reply keeps its thread, and the mail holds the file."""
    _thread(gmail)

    sent_id = await _provider().send_message(
        to=[RAVI], subject="Re: Quote", body_text="Here it is.",
        reply_to_message_id="p-2", thread_id="t-1", attachments=[_file()])

    [upload] = gmail.uploads
    assert (upload.method, upload.path) == ("POST", "/messages/send")
    assert str(upload.url) == (
        "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"
        "?uploadType=multipart")
    assert upload.bearer == "Bearer at-1"
    assert sent_id.startswith("s-")
    assert gmail.sent[-1]["threadId"] == "t-1"
    assert _file_parts(gmail.sent[-1]["raw"]) == [("quote.pdf", PDF, QUOTE)]
    assert _decode(gmail.sent[-1]["raw"])["In-Reply-To"] == "<p2@contoso.test>"


async def test_an_update_that_reads_back_a_file_goes_to_the_upload_uri(
        gmail: _Gmail) -> None:
    """Item 2, M5. An autosave adds no file. The draft holds one, so the
    update reads it back, and the files after that read decide the URI."""
    gmail.add_raw_draft("r-f", "m-f", _raw_draft_with_a_file(), thread="t-f")

    await _provider().update_draft("m-f", to=[RAVI], body_text="v2")

    [upload] = gmail.uploads
    assert (upload.method, upload.path) == ("PUT", "/drafts/r-f")
    assert str(upload.url) == (
        "https://gmail.googleapis.com/upload/gmail/v1/users/me/drafts/r-f"
        "?uploadType=multipart")
    assert _file_parts(gmail.saved[-1]["message"]["raw"]) == [
        ("quote.pdf", PDF, QUOTE)]


# ── item 4: the parts ───────────────────────────────────────────────────────


async def _write(p: GmailProvider, act: str, *, thread: bool,
                 files: list[dict[str, Any]] | None, draft_id: str) -> str:
    """One write of ``act``, with or without a thread and a file."""
    thread_id = "t-1" if thread else None
    if act == "send":
        return await p.send_message(to=[RAVI], subject="Re: Quote",
                                    body_text="x", thread_id=thread_id,
                                    attachments=files)
    if act == "create":
        return await p.create_draft(to=[RAVI], subject="Re: Quote",
                                    body_text="x", thread_id=thread_id,
                                    attachments=files)
    return await p.update_draft(draft_id, to=[RAVI], body_text="x",
                                thread_id=thread_id, attachments=files)


@pytest.mark.parametrize("act, thread, meta", [
    ("send", True, {"threadId": "t-1"}),
    ("send", False, {}),
    ("create", True, {"message": {"threadId": "t-1"}}),
    ("create", False, {"message": {}}),
    ("update", True, {"message": {"threadId": "t-1"}}),
    ("update", False, {"message": {}}),
], ids=["send-thread", "send-no-thread", "create-thread", "create-no-thread",
        "update-thread", "update-no-thread"])
async def test_the_metadata_of_each_method_has_its_own_shape(
        gmail: _Gmail, act: str, thread: bool, meta: dict[str, Any]) -> None:
    """Item 4, C18 (a). Part 1 holds the metadata of the method, and it
    omits ``threadId`` exactly where the JSON body of the plain URI omits
    it. Part 2 holds the raw mail as ``message/rfc822``, not base64url. The
    fake refuses an upload whose parts have another type."""
    _thread(gmail)
    p = _provider()

    plain_id = await _write(p, act, thread=thread, files=None, draft_id="m-90")
    await _write(p, act, thread=thread, files=[_file()], draft_id=plain_id)

    [upload] = gmail.uploads
    assert upload.meta == meta
    plain = gmail.sent[0] if act == "send" else gmail.saved[0]
    if act == "send":
        assert {k: v for k, v in plain.items() if k != "raw"} == meta
    else:
        assert {k: v for k, v in plain["message"].items() if k != "raw"} == (
            meta["message"])
    assert upload.mail.startswith(b"Content-Type: multipart/mixed")
    assert f"To: {RAVI}".encode() in upload.mail
    mail = message_from_bytes(upload.mail)
    assert [part.get_filename() for part in mail.walk()
            if part.get_filename()] == ["quote.pdf"]


async def test_a_reply_draft_with_a_file_keeps_its_thread_after_an_update(
        gmail: _Gmail) -> None:
    """Item 4, C18 (a). A reply draft with a file stays in its conversation
    after an update on the upload URI. The update sends
    ``message.threadId`` again and sets ``In-Reply-To`` again."""
    _thread(gmail)
    p = _provider()
    mid = await p.create_draft(
        to=[RAVI], subject="Re: Quote", body_text="v1",
        reply_to_message_id="p-2", thread_id="t-1", attachments=[_file()])

    new_mid = await p.update_draft(mid, to=[RAVI], subject="Re: Quote",
                                   body_text="v2", thread_id="t-1")

    assert [(u.method, u.meta) for u in gmail.uploads] == [
        ("POST", {"message": {"threadId": "t-1"}}),
        ("PUT", {"message": {"threadId": "t-1"}})]
    assert gmail.messages[new_mid]["threadId"] == "t-1"
    rebuilt = gmail.saved[-1]["message"]["raw"]
    assert _decode(rebuilt)["In-Reply-To"] == "<p2@contoso.test>"
    assert _file_parts(rebuilt) == [("quote.pdf", PDF, QUOTE)]


# ── items 5 and 6: the limit and the error ──────────────────────────────────


@pytest.mark.parametrize("act, limit", [
    ("send", 4_000), ("create", 4_000), ("update", 4_000),
    ("send", None),
], ids=["send", "create", "update", "send-at-the-real-limit"])
async def test_a_mail_over_the_limit_raises_before_the_write_request(
        gmail: _Gmail, monkeypatch: pytest.MonkeyPatch, act: str,
        limit: int | None) -> None:
    """Items 5 and 6, M2. A built mail over the limit raises
    ``GmailMailTooLarge`` and no write request goes out. An update still
    reads the draft first. The error is no HTTP error, and its text holds
    no URL. One case builds a mail over the real limit of 36,700,160
    bytes."""
    assert GMAIL_MAIL_MAX_BYTES == 36_700_160
    if limit is not None:
        monkeypatch.setattr(gmail_mod, "GMAIL_MAIL_MAX_BYTES", limit)
        content = b"x" * 5_000
    else:
        content = b"\x00" * 27_600_000
    gmail.add_draft("r-7", "m-7", thread="t-7")
    p = _provider()

    with pytest.raises(GmailMailTooLarge) as caught:
        if act == "send":
            await p.send_message(to=[RAVI], subject="Big", body_text="x",
                                 attachments=[_file(content)])
        elif act == "create":
            await p.create_draft(to=[RAVI], subject="Big", body_text="x",
                                 attachments=[_file(content)])
        else:
            await p.update_draft("m-7", to=[RAVI], body_text="x",
                                 attachments=[_file(content)])

    error = caught.value
    assert isinstance(error, ProviderMailTooLarge)
    assert not isinstance(error, httpx.HTTPError)
    assert error.limit == (limit or 36_700_160) < error.size
    assert "http" not in str(error) and "googleapis" not in str(error)
    assert _writes(gmail) == [] and gmail.uploads == []
    assert gmail.sent == [] and gmail.saved == []
    if act == "update":
        reads = [(m, path) for m, path, _ in gmail.seen if m == "GET"]
        assert ("GET", "/drafts") in reads and ("GET", "/messages/m-7") in reads


def _html_413(_request: httpx.Request) -> httpx.Response:
    """A 413 with an HTML body, as a front end of Google can answer."""
    return httpx.Response(413, text="<html><b>413.</b> Too large.</html>",
                          headers={"Content-Type": "text/html"})


def _json_413(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(413, json={"error": {"code": 413}})


def _drafts_of(limited: limits_mod._Gmail) -> None:
    """``drafts.list`` and the raw draft ``m-1``, with a file in it."""
    raw = base64.urlsafe_b64encode(_raw_draft_with_a_file()).decode()
    limited.on("GET", "/drafts", limits_mod._ok({"drafts": [
        {"id": "r-1", "message": {"id": "m-1"}}]}))
    limited.on("GET", "/messages/m-1", limits_mod._ok({"id": "m-1", "raw": raw}))


@pytest.mark.parametrize("act, method, path, upload, answer", [
    ("send", "POST", "/messages/send", True, _html_413),
    ("send", "POST", "/messages/send", False, _json_413),
    ("create", "POST", "/drafts", True, _json_413),
    ("create", "POST", "/drafts", False, _html_413),
    ("update", "PUT", "/drafts/r-1", True, _html_413),
], ids=["send-upload-html", "send-plain-json", "create-upload-json",
        "create-plain-html", "update-upload-html"])
async def test_a_413_from_google_raises_mail_too_large(
        limited: limits_mod._Gmail, act: str, method: str, path: str,
        upload: bool, answer: Callable[[httpx.Request], httpx.Response]) -> None:
    """Item 6. A 413 raises ``GmailMailTooLarge`` on either URI. The write
    reads only the status code, so an HTML body raises it too. A 413 is no
    rate limit, so the write goes once. The text holds no URL."""
    _drafts_of(limited)
    limited.on(method, path, answer)
    files = [_file()] if upload else None
    p = limits_mod._provider()

    with pytest.raises(GmailMailTooLarge) as caught:
        if act == "send":
            await p.send_message(to=[RAVI], subject="s", body_text="b",
                                 attachments=files)
        elif act == "create":
            await p.create_draft(to=[RAVI], subject="s", body_text="b",
                                 attachments=files)
        else:
            await p.update_draft("m-1", to=[RAVI], body_text="b")

    assert not isinstance(caught.value, httpx.HTTPError)
    assert "googleapis" not in str(caught.value)
    assert "upload" not in str(caught.value)
    assert limited.calls(method, path) == 1
    assert bool(limited.uploads) is upload


# ── item 8: one try for a send ──────────────────────────────────────────────


@pytest.mark.parametrize("method, path, repeatable", [
    ("POST", "/messages/send", False),
    ("POST", "/drafts", False),
    ("PUT", "/drafts/r-1", True),
], ids=["messages.send", "drafts.create", "drafts.update"])
async def test_an_upload_send_gets_one_try_on_a_429(
        limited: limits_mod._Gmail, method: str, path: str,
        repeatable: bool) -> None:
    """Item 8, M6. ``_repeatable`` reads the path. The upload paths of
    ``messages.send`` and ``drafts.create`` end in no action that sets a
    state, so a 429 goes back to the caller after one try, and the mail
    goes out once. The PUT of ``drafts.update`` keeps its retry."""
    request = httpx.Request(method, f"{GMAIL_UPLOAD_BASE}/users/me{path}",
                            params={"uploadType": "multipart"})
    assert _repeatable(request) is repeatable
    _drafts_of(limited)
    answer = {"id": "r-1", "message": {"id": "m-2"}}
    limited.on(method, path, limits_mod._rate_limited("0"),
               limits_mod._ok(answer))
    p = limits_mod._provider()
    act: dict[str, Callable[[], Awaitable[Any]]] = {
        "/messages/send": lambda: p.send_message(
            to=[RAVI], subject="s", body_text="b", attachments=[_file()]),
        "/drafts": lambda: p.create_draft(
            to=[RAVI], subject="s", body_text="b", attachments=[_file()]),
        "/drafts/r-1": lambda: p.update_draft("m-1", to=[RAVI], body_text="b"),
    }

    if repeatable:
        assert await act[path]() == "m-2"
        assert limited.calls(method, path) == 2
        assert limited.waits == [0.0]
    else:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await act[path]()
        assert not isinstance(caught.value, GmailRateLimited)
        assert caught.value.response.status_code == 429
        assert limited.calls(method, path) == 1
        assert limited.waits == []
    assert set(limited.uploads) == {(method, limits_mod.API + path)}


# ── item 7: the routes ──────────────────────────────────────────────────────


class _ReplyRows(_Rows):
    """``_Rows``, and the parent row of a reply."""

    async def execute(self, stmt: Any, _params: Any = None) -> Any:
        if "FROM email_messages WHERE account_id" in str(stmt):
            self.statements.append(str(stmt))
            row = SimpleNamespace(
                provider_message_id="p-2", thread_id="t-1", subject="Quote",
                from_address={"email": RAVI})
            return SimpleNamespace(fetchone=lambda: row)
        return await super().execute(stmt, _params)


def _local_writes(db: _Rows) -> list[str]:
    return [s for s in db.statements
            if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


@pytest.mark.parametrize("route", [
    "save-new", "save-reply", "save-update", "signed-send", "send",
])
async def test_each_route_answers_413_with_the_text(
        gmail: _Gmail, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    """Item 7, M3. ``PUT /email/drafts`` (each of its three branches), the
    signed send of ``POST /email/drafts/send`` and ``POST /email/send``
    answer 413 with a string detail. No write reaches Gmail, and the route
    writes no local row."""
    monkeypatch.setattr(gmail_mod, "GMAIL_MAIL_MAX_BYTES", 200)
    _thread(gmail)
    db = _ReplyRows(_draft_row("m-90"), "Asha" if route == "signed-send" else "")
    _route_patches(monkeypatch, db, _provider())
    pdf = SendAttachment(filename="quote.pdf", mime_type=PDF,
                         content_b64=base64.b64encode(QUOTE).decode())

    with pytest.raises(HTTPException) as caught:
        if route.startswith("save-"):
            await drafting.upsert_draft(drafting.DraftUpsertRequest(
                account_id="acc-1", to=[RAVI], body="See the quote.",
                draft_id="local-7" if route == "save-update" else None,
                reply_to_message_id=(
                    "local-1" if route == "save-reply" else None),
                attachments=[pdf]), user=_ME)
        elif route == "signed-send":
            await drafting.send_draft_endpoint(
                drafting.DraftSendRequest(account_id="acc-1",
                                          draft_id="local-7"),
                BackgroundTasks(), user=_ME)
        else:
            await _send_route(monkeypatch, db)

    assert caught.value.status_code == 413
    assert caught.value.detail == DETAIL == drafting.MAIL_TOO_LARGE_DETAIL
    assert isinstance(caught.value.__cause__, GmailMailTooLarge)
    assert _writes(gmail) == [] and gmail.sent_drafts == []
    assert _local_writes(db) == [], "the route wrote a local row"


async def _send_route(monkeypatch: pytest.MonkeyPatch, db: _Rows) -> Any:
    """One ``POST /email/send`` of the member, with a file."""
    @asynccontextmanager
    async def _session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield db

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_provider(), owner_email=_ME.email)

    monkeypatch.setattr(send_mod, "_tenant_session", _session)
    monkeypatch.setattr(send_mod, "provider_session", _provider_session)
    return await send_mod.send_email(SendEmailRequest(
        account_id="acc-1", to=[RAVI], subject="Quote", body_text="Hello",
        attachments=[SendAttachment(
            filename="quote.pdf", mime_type=PDF,
            content_b64=base64.b64encode(QUOTE).decode())]),
        BackgroundTasks(), user=_ME)
