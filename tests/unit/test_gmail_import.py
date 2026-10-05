"""WS-17 EM-G5a — the Gmail import: one list of all mail, newest first.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.6.1, items 1 to 5
and the rules of the build E-G5-3 to E-G5-8.

* **One list (items 1 and 2).** ``GmailProvider.import_batches`` pages one
  ``messages.list`` with ``includeSpamTrash=true`` and no ``labelIds``. So
  archived mail with no label comes too (GM-8). Each page is one batch,
  newest first.
* **The resume (item 3, E-G5-3).** ``before:`` is the whole second of
  ``until`` plus 1 second, so a resume reads that second again.
* **The fetch (item 4, E-G5-4, E-G5-5).** Each page fetches in parallel,
  ``GMAIL_IMPORT_FETCHES`` at once, and each id once. A fetch that a later
  cycle can fix fails the import. Each page gathers its fetches before it
  yields, so the storage limit stops the import with no fetch in flight.
* **The estimate (item 5).** ``on_estimate`` gets the ``resultSizeEstimate``
  of the first answer, once, before the first fetch.
* **The cap (E-G5-6), and no deep sync (E-G5-8).**
* **The confirm of the reconcile (WS-17 EM-G5b, §12.3.6.2).**
  ``message_gone`` reads by the provider id with ``format=minimal``. Only a
  404 with the reason ``notFound`` is gone (items 6 and 10). A spent rate
  limit stops ``_confirm_gone`` (item 11). No Gmail sync result sets
  ``full_snapshot`` (item 8).

The fake Gmail answers on ``httpx.MockTransport``, so each request goes
through the real ``_get_client`` and its ``GmailBearer``. The R8 cases of
EM-G5a and EM-G5b are in ``tests/unit/test_email_import_batches.py``
(``TestTheImportOnARealDatabase``), and they use the fake of this file.

Run::

    uv run pytest tests/unit/test_gmail_import.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import base64
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import email_ingestion.providers.gmail as gmail_mod
import email_ingestion.scheduler as sched
import httpx
import pytest
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import EmailMessage
from email_ingestion.providers.gmail import (
    GMAIL_IMPORT_FETCHES,
    GmailProvider,
    GmailRateLimited,
)

API = "/gmail/v1/users/me"
LOGGER = "email_ingestion.providers.gmail"
T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
FLOOR = T0 - timedelta(days=30)

#: One answer of the fake: it takes the request and gives the response.
Answer = Callable[[httpx.Request], httpx.Response]


# ── the fake Gmail ──────────────────────────────────────────────────────────


def _ms(at: datetime) -> int:
    return round(at.timestamp() * 1000)


def _raw(mid: str, at: datetime, *labels: str) -> dict[str, Any]:
    """A message in the shape of ``users.messages.get`` with ``format=full``.
    With no label it is archived mail."""
    data = base64.urlsafe_b64encode(b"hi").decode().rstrip("=")
    return {
        "id": mid, "threadId": f"t-{mid}", "labelIds": list(labels),
        "snippet": "", "internalDate": str(_ms(at)),
        "payload": {"mimeType": "text/plain", "body": {"data": data},
                    "headers": [{"name": "Subject", "value": f"About {mid}"},
                                {"name": "From", "value": "Asha <asha@example.org>"},
                                {"name": "Message-ID", "value": f"<{mid}@example.org>"}]},
    }


def _ok(body: dict[str, Any]) -> Answer:
    return lambda _request: httpx.Response(200, json=body)


def _refuse(code: int, reason: str | None = None) -> Answer:
    """An error in the shape of the Gmail API."""
    errors = [{"domain": "global", "reason": reason}] if reason else []
    body = {"error": {"code": code, "message": "refused", "errors": errors}}
    headers = {"Retry-After": "0"} if code == 429 else {}
    return lambda _request: httpx.Response(code, json=body, headers=headers)


def _transport_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("the connection dropped", request=request)


#: The answer of Gmail to a read of a deleted message, as Google sends it.
_NOT_FOUND_BODY = {"error": {
    "code": 404, "message": "Requested entity was not found.",
    "errors": [{"message": "Requested entity was not found.",
                "domain": "global", "reason": "notFound"}],
    "status": "NOT_FOUND"}}


def _gone(_request: httpx.Request) -> httpx.Response:
    """A deleted message: a 404 with the reason ``notFound`` (EM-G5b C2)."""
    return httpx.Response(404, json=_NOT_FOUND_BODY)


class _Gmail:
    """One fake Gmail mailbox.

    ``messages.list`` honours ``labelIds``, ``includeSpamTrash``, the
    ``after:`` and ``before:`` terms of ``q``, ``maxResults`` and
    ``pageToken``. It lists the newest mail first, as Gmail does. It reads
    ``after:`` as "at or after" and ``before:`` as "before". Gmail does not
    write down the edge, and no test depends on the ``after:`` edge.

    * ``estimate`` is the ``resultSizeEstimate`` of each answer. ``"count"``
      gives the count of the matches, and ``"absent"`` leaves it out.
    * ``overlap`` starts each page after the first that many ids early, as
      a list does when new mail arrives during the import.
    * ``ignore_query`` lists all mail, whatever the query says.
    * ``endless`` gives a ``nextPageToken`` on each page.
    * ``reverse_pages`` lists each page oldest first.

    Each fetch counts in ``fetched`` and in ``in_flight``. A fetch yields to
    the loop ``hold`` times while it is in flight, so the fetches of one page
    overlap. ``slow`` adds more turns for one message id, so the fetches of a
    page end at different times, as they do at Gmail. With ``release`` set,
    a fetch of a message with no own answer waits for it. ``answers`` gives
    a message id its own answers, in order, and the last one repeats.

    The reconcile (WS-17 EM-G5b):

    * A fetch of an id that the mailbox does not hold answers 404 with the
      reason ``notFound``, as Gmail does for a deleted message.
    * A fetch with ``format=minimal`` is a confirm. ``confirms`` keeps its
      id and its query, and it answers the minimal shape of Gmail.
    * ``unlisted`` holds ids that the list leaves out and a fetch still
      answers, as for a message that the import missed.
    * ``stale_history`` makes ``history.list`` answer 404, a stale cursor."""

    def __init__(self) -> None:
        self.mail: dict[str, dict[str, Any]] = {}
        self.lists: list[dict[str, list[str]]] = []
        self.fetched: dict[str, int] = {}
        self.answers: dict[str, list[Answer]] = {}
        self.estimate: int | str = "count"
        self.overlap = 0
        self.ignore_query = False
        self.endless = False
        self.reverse_pages = False
        self.hold = 3
        self.slow: dict[str, int] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.cancelled: list[str] = []
        self.release: asyncio.Event | None = None
        self.history_id = "500"
        self.confirms: list[tuple[str, list[tuple[str, str]]]] = []
        self.unlisted: set[str] = set()
        self.stale_history = False

    def add(self, mid: str, at: datetime, *labels: str) -> None:
        self.mail[mid] = _raw(mid, at, *labels)

    def _matches(self, params: dict[str, list[str]]) -> list[dict[str, Any]]:
        wanted = params.get("labelIds") or []
        spam_trash = params.get("includeSpamTrash") == ["true"]
        query = " ".join(params.get("q", []))
        after = re.search(r"after:(\d+)", query)
        before = re.search(r"before:(\d+)", query)
        found = []
        for raw in self.mail.values():
            labels = set(raw["labelIds"])
            at = int(raw["internalDate"])
            if raw["id"] in self.unlisted:
                continue
            if not set(wanted) <= labels:
                continue
            if not spam_trash and labels & {"SPAM", "TRASH"}:
                continue
            if not self.ignore_query and after and at < int(after.group(1)) * 1000:
                continue
            if not self.ignore_query and before and at >= int(before.group(1)) * 1000:
                continue
            found.append(raw)
        found.sort(key=lambda r: (int(r["internalDate"]), r["id"]), reverse=True)
        return found

    def _list(self, params: dict[str, list[str]]) -> httpx.Response:
        self.lists.append(params)
        found = self._matches(params)
        size = int(params.get("maxResults", ["100"])[0])
        token = params.get("pageToken", [""])[0]
        page = int(token[1:]) if token else 0
        start = max(page * size - (self.overlap if page else 0), 0)
        chunk = found[start:start + size]
        if self.reverse_pages:
            chunk = chunk[::-1]
        body: dict[str, Any] = {}
        if self.estimate == "count":
            body["resultSizeEstimate"] = len(found)
        elif self.estimate != "absent":
            body["resultSizeEstimate"] = self.estimate
        if chunk:
            body["messages"] = [{"id": r["id"], "threadId": r["threadId"]}
                                for r in chunk]
        if self.endless or start + size < len(found):
            body["nextPageToken"] = f"p{page + 1}"
        return httpx.Response(200, json=body)

    async def _fetch(self, mid: str, request: httpx.Request) -> httpx.Response:
        self.fetched[mid] = self.fetched.get(mid, 0) + 1
        minimal = request.url.params.get("format") == "minimal"
        if minimal:
            self.confirms.append((mid, sorted(request.url.params.multi_items())))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            for _ in range(self.hold + self.slow.get(mid, 0)):
                await asyncio.sleep(0)
            answers = self.answers.get(mid)
            if answers:
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                return answer(request)
            if self.release is not None:
                await self.release.wait()
            raw = self.mail.get(mid)
            if raw is None:
                return _gone(request)
            if minimal:
                return httpx.Response(200, json={
                    key: raw[key] for key in ("id", "threadId", "labelIds",
                                              "internalDate")})
            return httpx.Response(200, json=raw)
        except asyncio.CancelledError:
            self.cancelled.append(mid)
            raise
        finally:
            self.in_flight -= 1

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(API)
        params: dict[str, list[str]] = {}
        for key, value in request.url.params.multi_items():
            params.setdefault(key, []).append(value)
        if path == "/messages":
            return self._list(params)
        if path.startswith("/messages/"):
            return await self._fetch(path.rsplit("/", 1)[-1], request)
        if path == "/labels":
            return httpx.Response(200, json={"labels": []})
        if path == "/history" and self.stale_history:
            return _gone(request)
        if path in ("/profile", "/history"):
            return httpx.Response(200, json={"historyId": self.history_id})
        return httpx.Response(404, json={"error": {"code": 404}})


class _Provider(GmailProvider):
    """A Gmail provider whose ``authenticate`` sends no request."""

    async def authenticate(self) -> bool:
        return True


def _provider() -> _Provider:
    return _Provider({"access_token": "at-1", "refresh_token": "r-1"},
                     app=OAuthApp(client_id="cid", client_secret="secret"))


@pytest.fixture()
def gmail(monkeypatch: pytest.MonkeyPatch) -> _Gmail:
    """Send each ``httpx.AsyncClient`` that the provider builds to one fake.
    ``_wait`` does not sleep."""
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


async def _collect(batches: AsyncIterator[list[EmailMessage]]) -> list[list[EmailMessage]]:
    return [batch async for batch in batches]


def _ids(batches: list[list[EmailMessage]]) -> list[list[str]]:
    return [[m.provider_message_id for m in batch] for batch in batches]


def _q(since: datetime, until: datetime | None = None) -> list[str]:
    """The ``q`` that the import sends, in epoch seconds (E-G5-3)."""
    terms = [f"after:{int(since.timestamp())}"]
    if until is not None:
        terms.append(f"before:{int(until.timestamp()) + 1}")
    return [" ".join(terms)]


def _open_fetches() -> list[asyncio.Task[Any]]:
    """Each task of the provider that has not ended."""
    return [t for t in asyncio.all_tasks()
            if not t.done() and getattr(t.get_coro(), "__qualname__", "")
            .startswith("GmailProvider.")]


# ── items 1 and 2: one list of all mail ─────────────────────────────────────


@pytest.mark.parametrize("reverse_pages", [False, True], ids=["newest_first", "page_reversed"])
async def test_the_import_lists_all_mail_once_newest_first(
        gmail: _Gmail, reverse_pages: bool) -> None:
    """Items 1 and 2, M8. One list reads the Inbox, Sent, a draft, spam,
    trash and archived mail, each once and newest first across the pages.
    Each list sends ``includeSpamTrash=true`` and no ``labelIds``. Each batch
    is sorted again, also when a page comes oldest first."""
    labels = [("INBOX",), ("SENT",), ("DRAFT",), ("SPAM",), ("TRASH",), (),
              ("Label_7",), ("INBOX", "UNREAD")]
    for n, label in enumerate(labels):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), *label)
    gmail.reverse_pages = reverse_pages

    batches = await _collect(_provider().import_batches(since=FLOOR, size=3))

    assert _ids(batches) == [["m0", "m1", "m2"], ["m3", "m4", "m5"], ["m6", "m7"]]
    assert [m.folder for batch in batches for m in batch] == [
        "inbox", "sent", "drafts", "junk", "trash", "archive", "archive", "inbox"]
    assert len(gmail.lists) == 3
    for params in gmail.lists:
        assert "labelIds" not in params
        assert params["includeSpamTrash"] == ["true"]
        assert params["maxResults"] == ["3"]
        assert params["q"] == _q(FLOOR)
    assert gmail.fetched == {f"m{n}": 1 for n in range(8)}


async def test_the_import_reads_archived_mail_with_no_label(gmail: _Gmail) -> None:
    """GM-8, M1. A message with no label at all is archived mail. No label
    list reaches it, so only the list of all mail imports it."""
    gmail.add("inbox", T0, "INBOX")
    gmail.add("archived", T0 - timedelta(hours=1))
    gmail.add("labelled", T0 - timedelta(hours=2), "Label_7")

    batches = await _collect(_provider().import_batches(since=FLOOR))

    by_id = {m.provider_message_id: m for batch in batches for m in batch}
    assert set(by_id) == {"inbox", "archived", "labelled"}
    assert by_id["archived"].folder == "archive"


# ── item 4: one fetch for each id ───────────────────────────────────────────


async def test_a_message_with_two_labels_is_fetched_once(gmail: _Gmail) -> None:
    """Item 4. The list of all mail gives a message with two user labels
    once, so the import fetches it once."""
    gmail.add("two", T0, "INBOX", "Label_1", "Label_2")
    gmail.add("one", T0 - timedelta(hours=1), "Label_1")

    batches = await _collect(_provider().import_batches(since=FLOOR))

    assert _ids(batches) == [["two", "one"]]
    assert gmail.fetched == {"two": 1, "one": 1}


async def test_an_id_listed_again_at_a_page_edge_is_fetched_once(gmail: _Gmail) -> None:
    """Item 4. New mail during the import moves the page edge, so the list
    gives the last id of a page again. The import fetches it once, and no
    batch holds it twice."""
    for n in range(5):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.overlap = 1

    batches = await _collect(_provider().import_batches(since=FLOOR, size=2))

    assert _ids(batches) == [["m0", "m1"], ["m2"], ["m3", "m4"]]
    assert gmail.fetched == {f"m{n}": 1 for n in range(5)}


async def test_the_import_fetches_in_parallel_at_most_ten_at_once(gmail: _Gmail) -> None:
    """Item 4. One page of 30 ids runs 10 fetches at once, never more."""
    for n in range(30):
        gmail.add(f"m{n:02d}", T0 - timedelta(minutes=n), "INBOX")

    batches = await _collect(_provider().import_batches(since=FLOOR, size=30))

    assert sum(len(batch) for batch in batches) == 30
    assert GMAIL_IMPORT_FETCHES == 10
    assert gmail.max_in_flight == GMAIL_IMPORT_FETCHES
    assert sum(gmail.fetched.values()) == 30


# ── item 5: the estimate ────────────────────────────────────────────────────


@pytest.mark.parametrize(("estimate", "expected"), [(4321, 4321), ("absent", None)],
                         ids=["given", "absent"])
async def test_the_estimate_comes_from_result_size_estimate(
        gmail: _Gmail, estimate: int | str, expected: int | None) -> None:
    """Item 5, M2. ``on_estimate`` gets the ``resultSizeEstimate`` of the
    first answer, once, before the first fetch. An answer with no estimate
    gives None."""
    for n in range(5):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.estimate = estimate
    calls: list[tuple[int | None, int]] = []

    async def _on_estimate(value: int | None) -> None:
        calls.append((value, sum(gmail.fetched.values())))

    batches = await _collect(_provider().import_batches(
        since=FLOOR, size=2, on_estimate=_on_estimate))

    assert sum(len(batch) for batch in batches) == 5
    assert len(gmail.lists) == 3
    assert calls == [(expected, 0)]


# ── item 3 and E-G5-3: the resume ───────────────────────────────────────────


async def test_a_resume_passes_before(gmail: _Gmail) -> None:
    """Item 3 (EM-T6b, D-EM-13). A resume sends ``before:`` the second after
    the point that it reached. A message of that second that is newer than
    ``until`` drops, and the message at ``until`` comes again, as in the
    base."""
    until = datetime(2026, 9, 1, 11, 0, 5, 200000, tzinfo=UTC)
    gmail.add("later", until + timedelta(seconds=2), "INBOX")
    gmail.add("same_second_newer", until + timedelta(milliseconds=500), "INBOX")
    gmail.add("at_until", until, "INBOX")
    gmail.add("older", until - timedelta(hours=1), "INBOX")

    batches = await _collect(_provider().import_batches(since=FLOOR, until=until))

    [params] = gmail.lists
    assert params["q"] == _q(FLOOR, until)
    assert params["q"] == [f"after:{int(FLOOR.timestamp())} before:1788260406"]
    assert _ids(batches) == [["at_until", "older"]]


async def test_a_resume_reads_the_second_it_reached_again(gmail: _Gmail) -> None:
    """E-G5-3, M5. The import reached a message at 11:00:05.600. A message
    at 11:00:05.200 of the same second is older, and the import did not
    write it. The bound of the resume is the whole second plus 1 second, so
    the resume reads that message."""
    until = datetime(2026, 9, 1, 11, 0, 5, 600000, tzinfo=UTC)
    gmail.add("reached", until, "INBOX")
    gmail.add("same_second", until - timedelta(milliseconds=400), "INBOX")
    gmail.add("older", until - timedelta(minutes=5), "INBOX")

    batches = await _collect(_provider().import_batches(since=FLOOR, until=until))

    assert _ids(batches) == [["reached", "same_second", "older"]]


async def test_no_message_older_than_the_floor(gmail: _Gmail) -> None:
    """Item 1. The list sends ``after:`` the floor. A message older than the
    floor drops also when the list gives it. With no floor the list sends no
    query."""
    gmail.add("new", T0, "INBOX")
    gmail.add("old", FLOOR - timedelta(seconds=1), "INBOX")
    gmail.ignore_query = True

    batches = await _collect(_provider().import_batches(since=FLOOR))

    assert gmail.lists[0]["q"] == _q(FLOOR)
    assert _ids(batches) == [["new"]]

    gmail.lists.clear()
    everything = await _collect(_provider().import_batches(since=None))
    assert "q" not in gmail.lists[0]
    assert _ids(everything) == [["new", "old"]]


# ── E-G5-4: a failed fetch ──────────────────────────────────────────────────


@pytest.mark.parametrize("answer", [_refuse(503, "backendError"), _transport_error],
                         ids=["5xx", "transport_error"])
async def test_a_transient_fetch_failure_fails_the_import(
        gmail: _Gmail, answer: Answer, caplog: pytest.LogCaptureFixture) -> None:
    """E-G5-4, M6. A 5xx or a transport error on one fetch fails the import
    before its page yields. The resume then reads that message again. The
    log names the message id, and no fetch stays in flight."""
    for n in range(4):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.answers["m3"] = [answer]
    yielded: list[list[str]] = []

    with caplog.at_level(logging.WARNING, logger=LOGGER), \
            pytest.raises(httpx.HTTPError):
        async for batch in _provider().import_batches(since=FLOOR, size=2):
            yielded.append([m.provider_message_id for m in batch])

    assert yielded == [["m0", "m1"]]
    assert gmail.in_flight == 0
    assert any(r.getMessage().startswith("gmail.fetch_failed message_id=m3 ")
               for r in caplog.records)


@pytest.mark.parametrize("answer", [
    _refuse(404, "notFound"), _refuse(400, "invalidArgument"), _ok({"threadId": "t"}),
], ids=["404", "400", "parse_error"])
async def test_a_fetch_failure_that_stays_is_recorded_and_the_batch_goes_on(
        gmail: _Gmail, answer: Answer) -> None:
    """E-G5-4. A 404, another 4xx or a parse error stays the same on each
    cycle. The fetch leaves its record (EM-G4a item 10), and the batch goes
    on with the other messages."""
    for n in range(3):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.answers["m1"] = [answer]
    provider = _provider()

    batches = await _collect(provider.import_batches(since=FLOOR))

    assert _ids(batches) == [["m0", "m2"]]
    assert len(provider.fetch_failures) == 1
    assert provider.fetch_failures[0].startswith("gmail.fetch_failed id=m1 ")


async def test_a_rate_limit_cancels_the_other_fetches_and_raises(gmail: _Gmail) -> None:
    """E-G5-5 (EM-G4a item 9). A fetch whose tries are spent raises. The
    other fetches of the page are cancelled, and the import raises with no
    fetch in flight."""
    for n in range(5):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.answers["m2"] = [_refuse(429, "rateLimitExceeded")]
    gmail.release = asyncio.Event()

    with pytest.raises(GmailRateLimited):
        await _collect(_provider().import_batches(since=FLOOR))

    assert sorted(gmail.cancelled) == ["m0", "m1", "m3", "m4"]
    assert gmail.fetched["m2"] == 3
    assert gmail.in_flight == 0
    assert _open_fetches() == []


async def test_the_storage_limit_stops_the_import_with_no_fetch_in_flight(
        gmail: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-G5-5, M7. ``scheduler._run_import`` stops after the batch that
    reaches the storage limit, and ``aclosing`` closes the import. The
    fetches of the page end at different times. The batch is written with
    no fetch in flight. After the stop, no task of the provider is open, and
    the import read no next page."""
    for n in range(25):
        gmail.add(f"m{n:02d}", T0 - timedelta(minutes=n), "INBOX")
    gmail.slow = {f"m{n:02d}": 5 * (n % 10) for n in range(25)}
    writes: list[tuple[list[str], int, int]] = []

    @asynccontextmanager
    async def _session(org: Any = None) -> Any:
        yield SimpleNamespace()

    async def _write(db: Any, account_id: str, batch: list[EmailMessage],
                     floor: datetime, **_kw: Any) -> tuple[list[Any], list[Any]]:
        writes.append(([m.provider_message_id for m in batch], gmail.in_flight,
                       len(_open_fetches())))
        return list(batch), []

    async def _meter(db: Any, account_id: str) -> int:
        return 10 ** 12

    monkeypatch.setattr(sched, "tenant_session", _session)
    monkeypatch.setattr(sched, "_write_messages", _write)
    monkeypatch.setattr(sched.storage, "measure_stored_bytes", _meter)
    monkeypatch.setattr(sched, "IMPORT_BATCH_SIZE", 10)
    tally: dict[str, Any] = {"written": 0, "limit": False}

    await sched._run_import("org-1", "acc-1", _provider(), SimpleNamespace(),
                            floor=FLOOR, progress=False, tally=tally)

    assert tally == {"written": 10, "limit": True}
    assert writes == [([f"m{n:02d}" for n in range(10)], 0, 0)], (
        "a fetch was in flight when the batch was written")
    assert len(gmail.lists) == 1, "the import read a page after the limit"
    assert sum(gmail.fetched.values()) == 10
    assert gmail.in_flight == 0
    assert _open_fetches() == []


# ── E-G5-6: the page cap ────────────────────────────────────────────────────


def test_the_import_cap_is_far_above_the_deep_sweep() -> None:
    """E-G5-6. One list reads all mail, so its cap is far above the 50
    pages of the deep sweep, as for Outlook."""
    assert GmailProvider.IMPORT_MAX_PAGES >= 25 * GmailProvider.DEEP_SYNC_MAX_PAGES


async def test_the_import_stops_at_the_page_cap_and_logs_it(
        gmail: _Gmail, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """E-G5-6. A list that never ends stops at the cap, and the log says
    ``gmail.import_capped``. The provider records the cap for the scheduler
    (EM-G5b item 12), and the next import that ends starts with no record."""
    for n in range(10):
        gmail.add(f"m{n}", T0 - timedelta(hours=n), "INBOX")
    gmail.endless = True
    monkeypatch.setattr(GmailProvider, "IMPORT_MAX_PAGES", 3)
    provider = _provider()
    assert provider.import_capped is False

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        batches = await _collect(provider.import_batches(since=FLOOR, size=2))

    assert len(gmail.lists) == 3
    assert _ids(batches) == [["m0", "m1"], ["m2", "m3"], ["m4", "m5"]]
    assert [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("gmail.import_capped")] == [
        "gmail.import_capped pages=3 messages=6"]
    assert provider.import_capped is True

    gmail.endless = False
    await _collect(provider.import_batches(since=FLOOR, size=20))
    assert provider.import_capped is False, "an import that ended kept the cap"


# ── E-G5-8: no deep sync ────────────────────────────────────────────────────


async def test_the_import_never_calls_the_deep_sync(
        gmail: _Gmail, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-G5-8. Gmail overrides ``import_batches``. It never calls
    ``sync_messages(deep=True)`` or ``_deep_sweep``, which stay for a direct
    call and keep their own fences."""
    gmail.add("m0", T0, "INBOX")
    provider = _provider()
    calls: list[str] = []

    async def _refused(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("deep")
        raise AssertionError("the import called the deep sync")

    monkeypatch.setattr(provider, "sync_messages", _refused)
    monkeypatch.setattr(provider, "_deep_sweep", _refused)

    batches = await _collect(provider.import_batches(since=FLOOR))

    assert "import_batches" in vars(GmailProvider)
    assert _ids(batches) == [["m0"]]
    assert calls == []


# ── EM-G5b: the confirm of the reconcile (spec §12.3.6.2) ───────────────────

#: A Gmail message id in the form that Gmail gives.
_GMAIL_ID = "18c2f0a1b2c3d4e5"


def _bare_404(_request: httpx.Request) -> httpx.Response:
    """A 404 with no reason, as a proxy or a wrong path can give."""
    return httpx.Response(404, json={"error": {"code": 404}})


def _empty_404(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text="")


def _status_only_404(_request: httpx.Request) -> httpx.Response:
    """A 404 that gives ``NOT_FOUND`` as its status and no reason."""
    return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})


@pytest.mark.parametrize(("held", "answer", "outcome"), [
    (None, None, True),
    ("INBOX", None, False),
    ("TRASH", None, False),
    ("SPAM", None, False),
    (None, _bare_404, httpx.HTTPStatusError),
    (None, _empty_404, httpx.HTTPStatusError),
    (None, _status_only_404, httpx.HTTPStatusError),
    (None, _refuse(400, "invalidArgument"), httpx.HTTPStatusError),
    (None, _refuse(403, "forbidden"), httpx.HTTPStatusError),
    (None, _refuse(429, "rateLimitExceeded"), GmailRateLimited),
    (None, _refuse(500, "backendError"), httpx.HTTPStatusError),
    (None, _transport_error, httpx.TransportError),
], ids=["404_not_found", "200_inbox", "200_trash", "200_spam", "bare_404",
        "empty_404", "status_only_404", "400", "403", "spent_429", "500",
        "transport_error"])
async def test_the_gmail_confirm_reads_by_the_provider_id(
        gmail: _Gmail, held: str | None, answer: Answer | None,
        outcome: bool | type[Exception]) -> None:
    """EM-G5b items 6 and 10 (C1, C2), M11. ``message_gone`` reads
    ``messages/{id}`` with ``format=minimal`` only, and the path holds the
    provider id. Only a 404 whose body gives the reason ``notFound`` is
    gone. A 200 keeps the row, also for a message in ``TRASH`` or ``SPAM``.
    Each other answer raises, and that includes a bare 404 and a 404 that
    gives only the status ``NOT_FOUND``. A spent rate limit raises
    ``GmailRateLimited`` after the tries of the client seam (EM-G4a)."""
    if held is not None:
        gmail.add(_GMAIL_ID, T0, held)
    if answer is not None:
        gmail.answers[_GMAIL_ID] = [answer]
    provider = _provider()

    if isinstance(outcome, bool):
        assert await provider.message_gone(_GMAIL_ID) is outcome
    else:
        with pytest.raises(outcome):
            await provider.message_gone(_GMAIL_ID)

    tries = 3 if outcome is GmailRateLimited else 1
    assert gmail.confirms == [(_GMAIL_ID, [("format", "minimal")])] * tries


@pytest.mark.parametrize("bad", ["", "a/b", "../labels/x", "a?b", "a#b", "a%2Fb",
                                 "<m@example.org>", "a b"])
async def test_the_gmail_confirm_refuses_an_id_that_is_not_a_gmail_id(
        gmail: _Gmail, bad: str) -> None:
    """EM-G5b item 10. An id that a path cannot hold as it is raises
    ``ValueError``, and no request goes out. So a stored id can never send
    the confirm to another resource whose 404 would trash a row."""
    with pytest.raises(ValueError):
        await _provider().message_gone(bad)
    assert gmail.confirms == []
    assert gmail.fetched == {}


async def test_a_spent_rate_limit_stops_the_confirm(
        gmail: _Gmail, caplog: pytest.LogCaptureFixture) -> None:
    """EM-G5b item 11 (C4), M13. ``scheduler._confirm_gone`` stops at the
    first spent rate limit. The row that a ``notFound`` confirmed before it
    goes to trash. A failed lookup before it lets the confirm go on. Each
    row after it gets no lookup, so it keeps its folder."""
    gmail.answers["g-flaky"] = [_refuse(500, "backendError")]
    gmail.answers["g-limited"] = [_refuse(429, "rateLimitExceeded")]
    candidates = [(1, "g-gone", "<gone@example.org>"),
                  (2, "g-flaky", "<flaky@example.org>"),
                  (3, "g-limited", "<limited@example.org>"),
                  (4, "g-after-1", "<after-1@example.org>"),
                  (5, "g-after-2", "<after-2@example.org>")]

    with caplog.at_level(logging.INFO, logger="email_ingestion.scheduler"):
        gone = await sched._confirm_gone(_provider(), candidates,
                                         account_id="acc-1")

    assert gone == [1]
    assert [mid for mid, _ in gmail.confirms] == [
        "g-gone", "g-flaky", "g-limited", "g-limited", "g-limited"]
    messages = [r.getMessage() for r in caplog.records]
    assert "sync.import_reconcile_lookup_failed error=HTTPStatusError" in messages
    assert ("sync.import_reconcile_rate_limited account=acc-1 gone=1 "
            "unconfirmed=3") in messages


async def test_no_gmail_sync_result_sets_full_snapshot(gmail: _Gmail) -> None:
    """EM-G5b item 8. The recurring reconcile (``reconcile_full_snapshot``)
    has no confirm, so it stays for Outlook only. Each sync path of Gmail
    returns ``full_snapshot`` False: no cursor, the history, a stale cursor
    and the deep sweep. No line of ``gmail.py`` sets the field."""
    gmail.add("m0", T0, "INBOX")
    gmail.add("m1", T0 - timedelta(hours=1))
    results = {
        "no_cursor": await _provider().sync_messages(history_id=None, since=FLOOR),
        "history": await _provider().sync_messages(history_id="400", since=FLOOR),
        "deep": await _provider().sync_messages(deep=True, since=FLOOR),
    }
    gmail.stale_history = True
    results["stale_cursor"] = await _provider().sync_messages(
        history_id="400", since=FLOOR)

    assert results["stale_cursor"].cursor_reset is True
    assert {name: r.full_snapshot for name, r in results.items()} == dict.fromkeys(
        results, False)

    tree = ast.parse(Path(gmail_mod.__file__).read_text(encoding="utf-8"))
    sets = [node.lineno for node in ast.walk(tree)
            if (isinstance(node, ast.keyword) and node.arg == "full_snapshot")
            or (isinstance(node, ast.Attribute) and node.attr == "full_snapshot"
                and isinstance(node.ctx, ast.Store))
            or (isinstance(node, ast.Constant) and node.value == "full_snapshot")]
    assert sets == [], f"gmail.py sets full_snapshot at the lines {sets}"
