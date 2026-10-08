"""EM-T9 — a file on an Outlook draft (a LIVE defect).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.10.

``OutlookProvider._attach_files`` sent each file in one POST, never read the
answer, and dropped each error with ``continue``. Graph takes one POST only
for a file under 3 MB. So a larger file failed at Graph, the draft kept no
copy, and the composer then sent the mail with no file. Nothing told the
member.

These fences pin the fix.

* A file under 3,000,000 bytes goes in one POST, and each 2xx passes. Graph
  answers 201, so a check for ``== 200`` is wrong (M8).
* A larger file goes through an upload session, in ranges of 2 MiB (M2).
* The PUTs of the session carry no ``Authorization`` header (M3).
* A session refused for the minimum size falls back to one POST (M4).
* A range that Graph did not expect raises, with no retry.
* A failed file raises ``ProviderAttachmentFailed`` with its name (M1).
* A failed file deletes a NEW draft, and keeps a draft that the member
  already has (M7).
* The token of the upload URL reaches no log record and no traceback (M5,
  M6).
* ``PUT /email/drafts`` answers 502 with the name, and writes no row.
* The signed send of ``drafts/send`` adds no file.

The body fences use an ``AsyncMock`` client, as
``test_email_draft_attachments.py`` does. The fences of the session, the
header and the log run through ``wire`` of ``test_email_provider_401_retry.py``,
so the real auth flow and the real httpx log lines run.

Run::

    uv run pytest tests/unit/test_outlook_attachments.py -v -rs
"""
from __future__ import annotations

import base64
import json
import logging
import re
import traceback
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from email_ingestion.providers import outlook as outlook_mod
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import ProviderAttachmentFailed
from email_ingestion.providers.outlook import OutlookProvider
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import drafting
from gateway.routes.email.transport.send import SendAttachment

# ``wire`` is a fixture, so the import is load-bearing.
from tests.unit.test_email_provider_401_retry import wire  # noqa: F401
from tests.unit.test_gmail_send_and_drafts import (
    _ME,
    _draft_row,
    _route_patches,
    _Rows,
)

PDF = "application/pdf"
SMALL = b"%PDF-1.7 small quote"
BIG = 5_000_000
MIN_SIZE = "ErrorAttachmentSizeShouldNotBeLessThanMinimumSize"
UPLOAD_TOKEN = "eyJ0eXAiOiJKV1Qi.EM-T9-UPLOAD-TOKEN.c2lnbmF0dXJl"
UPLOAD_URL = (
    "https://outlook.office.com/api/v2.0/Users('u-1')/Messages('draft-1')"
    f"/AttachmentSessions('s-1')?authtoken={UPLOAD_TOKEN}")
DETAIL = "The file quote.pdf could not be attached. The mail was not sent."


def _file(name: str = "quote.pdf", content: Any = SMALL) -> dict[str, Any]:
    return {"filename": name, "content": content, "mime_type": PDF}


def _bytes(size: int) -> bytes:
    """*size* bytes that are not all the same, so a range out of order shows."""
    return bytes(i % 251 for i in range(size))


# ── the AsyncMock client (the pattern of test_email_draft_attachments) ─────


def _resp(status: int = 201, body: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status, headers={},
        json=lambda: ({"id": "draft-1"} if body is None else body),
        raise_for_status=lambda: None)


def _client(answers: dict[str, Any] | None = None) -> AsyncMock:
    """A Graph client double. *answers* maps the end of a POST path to an
    answer, or to an exception that the POST raises."""
    answers = answers or {}
    client = AsyncMock()

    async def _post(url: str, **_kw: Any) -> Any:
        for end, answer in answers.items():
            if url.endswith(end):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return _resp(201, {"id": "draft-1"})

    client.post.side_effect = _post
    client.patch.return_value = _resp(200, {})
    client.delete.return_value = _resp(204, {})
    return client


def _provider(client: AsyncMock) -> OutlookProvider:
    p = OutlookProvider({"access_token": "t", "refresh_token": "r"})
    p._http = client
    return p


def _posts(client: AsyncMock, end: str) -> list[dict[str, Any]]:
    """The JSON body of each POST whose path ends with *end*."""
    return [c.kwargs.get("json") for c in client.post.await_args_list
            if c.args[0].endswith(end)]


async def _create(p: OutlookProvider, branch: str, files: list[dict]) -> str:
    if branch == "reply":
        return await p.create_draft(
            to=[], subject="", body_text="b", reply_to_message_id="m-1",
            attachments=files)
    return await p.create_draft(
        to=["a@x.test"], subject="s", body_text="b", attachments=files)


BRANCHES = pytest.mark.parametrize("branch", ["new", "reply"])


# ── the fake Graph and upload host behind ``wire`` ─────────────────────────

_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class _Graph:
    """Graph and the upload host of one session, for ``wire``.

    The upload host answers as Outlook REST does: a 200 with
    ``NextExpectedRanges`` after each range, and a 201 after the last one.
    ``put_answers`` replaces the answers of the PUTs, in order."""

    def __init__(self, *, ranges_key: str = "NextExpectedRanges") -> None:
        self.requests: list[httpx.Request] = []
        self.ranges_key = ranges_key
        self.session: tuple[int, Any] = (201, {
            "uploadUrl": UPLOAD_URL, "nextExpectedRanges": ["0-"],
            "expirationDateTime": "2026-10-05T12:00:00Z"})
        self.put_answers: list[
            int | tuple[int, Any] | Callable[..., Any] | None] = []
        self.received = bytearray()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "outlook.office.com":
            return self._put(request)
        path, method = request.url.path, request.method
        if path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me"})
        if method == "POST" and path.endswith("/createUploadSession"):
            status, body = self.session
            return httpx.Response(status, json=body)
        if method == "POST" and path.endswith("/attachments"):
            return httpx.Response(201, json={"id": "att-1"})
        if method == "POST" and path in ("/v1.0/me/messages",
                                         "/v1.0/me/messages/m-1/createReply"):
            return httpx.Response(201, json={"id": "draft-1"})
        if method in ("PATCH", "DELETE"):
            return httpx.Response(200 if method == "PATCH" else 204)
        return httpx.Response(404, json={"error": {"code": "NoRoute"}})

    def _put(self, request: httpx.Request) -> httpx.Response:
        """One PUT. An entry of ``put_answers`` is a status, a pair of a
        status and a body, a callable, or None for the normal answer."""
        _start, end, total = (int(g) for g in _RANGE.match(
            request.headers["content-range"]).groups())
        self.received += request.content
        answer = self.put_answers.pop(0) if self.put_answers else None
        if callable(answer):
            return answer(request)
        if answer is not None:
            status, body = (answer, {}) if isinstance(answer, int) else answer
            return httpx.Response(status, json=body)
        if end + 1 == total:
            return httpx.Response(201, json={"id": "att-1"})
        return httpx.Response(200, json={
            "ExpirationDateTime": "2026-10-05T12:00:00Z",
            self.ranges_key: [f"{end + 1}-"]})

    def puts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "outlook.office.com"]

    def graph(self, method: str, end: str) -> list[httpx.Request]:
        return [r for r in self.requests
                if r.url.host != "outlook.office.com" and r.method == method
                and r.url.path.endswith(end)]


def _wired_provider() -> OutlookProvider:
    return OutlookProvider({"access_token": "at-1", "refresh_token": "r-1"},
                           app=OAuthApp(client_id="cid", client_secret="secret"))


# ── item 1: a small file ───────────────────────────────────────────────────


@pytest.mark.parametrize("size", [len(SMALL), 2_999_999])
async def test_a_small_file_goes_in_one_post_and_201_passes(size: int) -> None:
    """M8. Graph answers 201 to the POST of a file. A check for ``== 200``
    takes that success for a failure."""
    content = _bytes(size)
    client = _client({"/attachments": _resp(201, {"id": "att-1"})})

    out = await _create(_provider(client), "new", [_file(content=content)])

    assert out == "draft-1"
    posts = _posts(client, "/attachments")
    assert len(posts) == 1
    assert posts[0]["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert posts[0]["name"] == "quote.pdf"
    assert posts[0]["contentType"] == PDF
    assert base64.b64decode(posts[0]["contentBytes"]) == content
    assert _posts(client, "/createUploadSession") == []
    client.delete.assert_not_awaited()


async def test_a_file_of_zero_bytes_goes_in_one_post() -> None:
    """Item 12. A file of 0 bytes is a file. It goes in one POST."""
    client = _client({"/attachments": _resp(201, {"id": "att-1"})})

    await _create(_provider(client), "new", [_file(content=b"")])

    assert [p["contentBytes"] for p in _posts(client, "/attachments")] == [""]


# ── item 2: a large file ───────────────────────────────────────────────────


@pytest.mark.parametrize(("size", "ranges"), [
    (BIG, ["bytes 0-2097151/5000000", "bytes 2097152-4194303/5000000",
           "bytes 4194304-4999999/5000000"]),
    (3_000_000, ["bytes 0-2097151/3000000", "bytes 2097152-2999999/3000000"]),
    # The edges of the range arithmetic (verifier P2-1): an exact multiple of
    # 2 MiB sends no empty third PUT, and a remainder of one byte sends one.
    (4_194_304, ["bytes 0-2097151/4194304", "bytes 2097152-4194303/4194304"]),
    (4_194_305, ["bytes 0-2097151/4194305", "bytes 2097152-4194303/4194305",
                 "bytes 4194304-4194304/4194305"]),
], ids=["5000000", "3000000", "4MiB", "4MiB+1"])
@pytest.mark.parametrize("ranges_key", ["NextExpectedRanges",
                                        "nextExpectedRanges"])
async def test_a_large_file_goes_through_an_upload_session_in_ranges(
        wire, size: int, ranges: list[str], ranges_key: str) -> None:  # noqa: F811
    """M2. A file of 3,000,000 bytes or more opens a session, and its bytes
    go up in ranges of 2 MiB, in order. The upload URL is an Outlook REST
    URL, so the key of the ranges can come in either case."""
    service = wire(_Graph(ranges_key=ranges_key))
    content = _bytes(size)

    out = await _create(_wired_provider(), "new",
                        [_file("big.pdf", content)])

    assert out == "draft-1"
    (session,) = service.graph("POST", "/createUploadSession")
    assert session.url.path == (
        "/v1.0/me/messages/draft-1/attachments/createUploadSession")
    assert json.loads(session.content) == {
        "AttachmentItem": {"attachmentType": "file", "name": "big.pdf",
                           "size": size, "contentType": PDF}}
    puts = service.puts()
    assert [r.method for r in puts] == ["PUT"] * len(ranges)
    assert [r.headers["content-range"] for r in puts] == ranges
    for r in puts:
        assert r.headers["content-type"] == "application/octet-stream"
        assert int(r.headers["content-length"]) == len(r.content)
        assert len(r.content) <= 2 * 1024 * 1024
    assert bytes(service.received) == content
    assert service.graph("POST", "/attachments") == []
    assert service.graph("DELETE", "") == []


async def test_the_upload_puts_carry_no_authorization_header(wire) -> None:  # noqa: F811
    """M3. The upload URL is pre-authenticated. Each PUT goes through a client
    with no auth, so the bearer of the mailbox never reaches that host. The
    Graph requests still carry it, so the auth flow ran."""
    service = wire(_Graph())

    await _create(_wired_provider(), "new", [_file("big.pdf", _bytes(BIG))])

    puts = service.puts()
    assert len(puts) == 3
    for r in puts:
        assert "authorization" not in r.headers, (
            "a PUT to the upload host carried the bearer of the mailbox")
    (session,) = service.graph("POST", "/createUploadSession")
    assert session.headers["authorization"] == "Bearer at-1"


# ── item 3: the minimum size ───────────────────────────────────────────────


async def test_a_session_refused_for_the_minimum_size_falls_back_to_one_post() -> None:
    """M4. "3 MB" can mean 3,000,000 or 3,145,728 bytes. A session that Graph
    refuses with the minimum-size code falls back to one POST."""
    content = _bytes(3_000_000)
    client = _client({
        "/createUploadSession": _resp(400, {"error": {
            "code": MIN_SIZE, "message": "Attachment size is too small."}}),
        "/attachments": _resp(201, {"id": "att-1"}),
    })

    await _create(_provider(client), "new", [_file(content=content)])

    assert len(_posts(client, "/createUploadSession")) == 1
    (post,) = _posts(client, "/attachments")
    assert base64.b64decode(post["contentBytes"]) == content
    client.delete.assert_not_awaited()


# ── item 5: the checks of the loop ─────────────────────────────────────────


@pytest.mark.parametrize(("answers", "put_count"), [
    ([(200, {"NextExpectedRanges": ["0-"]})], 1),
    ([(200, {})], 1),
    ([(201, {"id": "att-1"})], 1),
    ([429], 1),
    ([503], 1),
    ([None, None, (200, {"NextExpectedRanges": ["5000000-"]})], 3),
], ids=["offset-not-next", "no-ranges", "201-before-the-last", "429", "503",
        "200-after-the-last"])
async def test_a_range_that_graph_did_not_expect_raises(
        wire, answers: list[Any], put_count: int) -> None:  # noqa: F811
    """Item 5. A 200 must name the next offset, a 201 must come at the last
    range only, and a 429 or a 5xx raises with no retry. Each failure stops
    the PUTs."""
    service = wire(_Graph())
    service.put_answers = list(answers)

    with pytest.raises(ProviderAttachmentFailed) as caught:
        await _create(_wired_provider(), "new",
                      [_file("big.pdf", _bytes(BIG))])

    assert caught.value.filename == "big.pdf"
    assert len(service.puts()) == put_count, "a PUT was sent again"


# ── items 7 and 12: a failed file raises with its name ─────────────────────


@pytest.mark.parametrize(("answers", "content"), [
    ({"/attachments": _resp(500, {"error": {"code": "ErrorInternal"}})}, SMALL),
    ({"/attachments": _resp(413, {})}, SMALL),
    ({"/attachments": _resp(400, {"error": {"code": "ErrorInvalidRequest"}})},
     SMALL),
    ({"/attachments": httpx.ConnectError("refused")}, SMALL),
    ({"/attachments": _resp(201, {})}, None),
    ({"/createUploadSession": _resp(500, {})}, _bytes(BIG)),
    ({"/createUploadSession": _resp(400, {"error": {
        "code": "ErrorInvalidRequest"}})}, _bytes(BIG)),
    ({"/createUploadSession": _resp(201, {"nextExpectedRanges": ["0-"]})},
     _bytes(BIG)),
    ({"/createUploadSession": httpx.ReadTimeout("slow")}, _bytes(BIG)),
], ids=["post-500", "post-413", "post-400", "post-transport", "no-content",
        "session-500", "session-400", "session-no-url", "session-transport"])
async def test_a_failed_file_raises_with_its_name(
        answers: dict[str, Any], content: Any) -> None:
    """M1. Each failure raises, and the error names the file. Before EM-T9,
    ``continue`` dropped each one, and a mail went out with no file."""
    client = _client(answers)

    with pytest.raises(ProviderAttachmentFailed) as caught:
        await _provider(client).update_draft(
            "draft-1", body_text="hi", attachments=[_file(content=content)])

    assert caught.value.filename == "quote.pdf"
    assert str(caught.value) == "The file quote.pdf could not be attached."
    assert not isinstance(caught.value, httpx.HTTPError)


async def test_each_file_after_a_failed_one_is_not_sent() -> None:
    """The first failed file stops the files after it."""
    client = _client({"/attachments": _resp(500, {})})

    with pytest.raises(ProviderAttachmentFailed):
        await _provider(client).update_draft("draft-1", attachments=[
            _file("a.pdf"), _file("b.pdf")])

    assert [p["name"] for p in _posts(client, "/attachments")] == ["a.pdf"]


# ── items 9 and 10: the delete of a new draft ──────────────────────────────


@BRANCHES
@pytest.mark.parametrize("delete_fails", [False, True],
                         ids=["delete-ok", "delete-fails"])
async def test_a_failed_file_on_a_new_draft_deletes_the_draft(
        branch: str, delete_fails: bool) -> None:
    """Item 9. A failed file deletes the new draft, in both branches of
    ``create_draft``. A failed DELETE is dropped, and the first error raises."""
    client = _client({"/attachments": _resp(500, {})})
    if delete_fails:
        client.delete.side_effect = httpx.ConnectError("gone")

    with pytest.raises(ProviderAttachmentFailed) as caught:
        await _create(_provider(client), branch, [_file()])

    assert caught.value.filename == "quote.pdf"
    client.delete.assert_awaited_once_with("/me/messages/draft-1")


async def test_a_failed_file_on_an_existing_draft_keeps_the_draft() -> None:
    """M7. ``update_draft`` holds a draft that the member already has. A
    failed file raises, and the draft stays. So the delete lives in
    ``create_draft`` only, never in ``_attach_files``."""
    client = _client({"/attachments": _resp(500, {})})

    with pytest.raises(ProviderAttachmentFailed):
        await _provider(client).update_draft(
            "draft-7", body_text="v2", attachments=[_file()])

    client.patch.assert_awaited_once()
    client.delete.assert_not_awaited()


@BRANCHES
async def test_an_empty_draft_id_raises_before_any_file(branch: str) -> None:
    """Item 10. With no id, a file has no draft to go to."""
    client = _client({"/me/messages": _resp(201, {"id": ""}),
                      "/createReply": _resp(201, {})})

    with pytest.raises(ProviderAttachmentFailed) as caught:
        await _create(_provider(client), branch, [_file()])

    assert caught.value.filename == "quote.pdf"
    assert _posts(client, "/attachments") == []
    client.patch.assert_not_awaited()
    client.delete.assert_not_awaited()


@BRANCHES
async def test_a_draft_with_no_file_sends_the_same_requests(branch: str) -> None:
    """A draft with no file keeps the requests of today, byte for byte."""
    client = _client()

    out = await _create(_provider(client), branch, [])

    assert out == "draft-1"
    paths = [c.args[0] for c in client.post.await_args_list]
    assert paths == (["/me/messages/m-1/createReply"] if branch == "reply"
                     else ["/me/messages"])
    assert client.patch.await_count == (1 if branch == "reply" else 0)
    client.delete.assert_not_awaited()


# ── item 6: the token stays out of each log and error (B2) ─────────────────


def _put_times_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout(f"timed out on {request.url}", request=request)


def _marked(target: logging.Logger) -> list[logging.Filter]:
    """The filters of this module on *target*."""
    return [f for f in target.filters if getattr(f, "upload_url_filter", False)]


@pytest.mark.parametrize("failure", [
    [None, 500], [None, _put_times_out], [None, None, 503]],
    ids=["second-put-500", "second-put-timeout", "last-put-503"])
async def test_the_upload_token_never_reaches_a_log_or_a_traceback(
        wire, caplog: pytest.LogCaptureFixture,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch, failure: list[Any]) -> None:
    """M5 and M6. httpx logs each request at INFO with its full URL, and the
    gateway logs at INFO. The token in the query of the upload URL must not
    reach a record, the text of the error, or a traceback.

    litellm puts a redaction filter of its own on the ``httpx`` logger when it
    loads, and this process loads it. So the test keeps only the filter of
    this module on that logger, and the fence proves that filter alone."""
    target = logging.getLogger("httpx")
    monkeypatch.setattr(target, "filters", _marked(target))
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="httpcore")
    service = wire(_Graph())
    service.put_answers = list(failure)

    with pytest.raises(ProviderAttachmentFailed) as caught:
        await _create(_wired_provider(), "new",
                      [_file("big.pdf", _bytes(BIG))])

    exc = caught.value
    rendered = [logging.Formatter("%(name)s %(message)s").format(r)
                for r in caplog.records]
    assert any("AttachmentSessions" in line for line in rendered), (
        "httpx logged no PUT, so this test proves nothing")
    for line in rendered:
        assert UPLOAD_TOKEN not in line, f"a log record holds the token: {line}"
    for text in (str(exc), repr(exc),
                 "".join(traceback.format_exception(exc))):
        assert UPLOAD_TOKEN not in text
        assert "authtoken" not in text
    assert exc.__cause__ is None and exc.__context__ is None


def test_the_log_filter_is_installed_once(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter goes on the ``httpx`` logger when the module loads, and a
    second install adds no second filter. The test works on a copy of the
    list, so it never installs the filter for a later test (M6)."""
    target = logging.getLogger("httpx")
    assert len(_marked(target)) == 1, "the module did not install the filter"
    monkeypatch.setattr(target, "filters", list(target.filters))

    outlook_mod._install_upload_log_filter()
    outlook_mod._install_upload_log_filter()

    assert len(_marked(target)) == 1


# ── item 8: the route ──────────────────────────────────────────────────────


class _ReplyRows(_Rows):
    """``_Rows``, and the parent row of a reply."""

    async def execute(self, stmt: Any, _params: Any = None) -> Any:
        if "FROM email_messages WHERE account_id" in str(stmt):
            self.statements.append(str(stmt))
            row = SimpleNamespace(
                provider_message_id="m-1", thread_id="t-1", subject="Quote",
                from_address={"email": "ravi@em-t9.test"})
            return SimpleNamespace(fetchone=lambda: row)
        return await super().execute(stmt, _params)


@pytest.mark.parametrize("branch", ["new", "reply", "update"])
async def test_a_failed_save_answers_502_and_writes_no_local_row(
        monkeypatch: pytest.MonkeyPatch, branch: str) -> None:
    """Item 8. The composer saves the draft before the send, so a 502 stops
    the send. The detail is a string with the file name, and the route writes
    no row. A new draft is deleted. A draft that the member has stays."""
    client = _client({"/attachments": _resp(500, {})})
    db = _ReplyRows(_draft_row("draft-7"), "")
    _route_patches(monkeypatch, db, _provider(client))
    req = drafting.DraftUpsertRequest(
        account_id="acc-1", to=["ravi@em-t9.test"], body="See the quote.",
        draft_id="local-7" if branch == "update" else None,
        reply_to_message_id="local-1" if branch == "reply" else None,
        attachments=[SendAttachment(filename="quote.pdf", mime_type=PDF,
                                    content_b64=base64.b64encode(SMALL).decode())])

    with pytest.raises(HTTPException) as caught:
        await drafting.upsert_draft(req, user=_ME)

    assert caught.value.status_code == 502
    assert caught.value.detail == DETAIL
    assert drafting.file_not_attached_detail("quote.pdf") == DETAIL
    writes = [s for s in db.statements
              if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == [], "the route wrote a local row for a failed save"
    if branch == "update":
        client.delete.assert_not_awaited()
    else:
        client.delete.assert_awaited_once_with("/me/messages/draft-1")


async def test_the_signed_send_adds_no_file(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """``drafts/send`` signs the draft in place and adds no file. The files
    went up at the save before it (§10.4.10 "The callers")."""
    client = _client()
    p = _provider(client)
    seen: list[dict[str, Any]] = []
    real_update = p.update_draft

    async def _spy(draft_id: str, **kwargs: Any) -> str:
        seen.append(kwargs)
        return await real_update(draft_id, **kwargs)

    p.update_draft = _spy  # type: ignore[method-assign]
    _route_patches(monkeypatch, _Rows(_draft_row("AAMk-1"), "Asha"), p)

    out = await drafting.send_draft_endpoint(
        drafting.DraftSendRequest(account_id="acc-1", draft_id="local-1"),
        BackgroundTasks(), user=_ME)

    assert out == {"sent": True}
    assert len(seen) == 1 and seen[0].get("attachments") is None
    assert _posts(client, "/attachments") == []
    assert _posts(client, "/createUploadSession") == []
    assert [c.args[0] for c in client.post.await_args_list] == [
        "/me/messages/AAMk-1/send"]
