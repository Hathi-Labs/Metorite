"""WS-17 EM-G4b — the Gmail history cursor and its recovery.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.5.2, items 1 to 7
and 11, the audit rules E-B1 to E-B3, and the known limit EM-G4a-f2 of
§12.3.5.1.

* **The seed (item 1, E-B3).** With no cursor, ``users.getProfile`` gives the
  seed FIRST. The sweep then runs and returns the seed. Before an import,
  ``_sync_cycle`` reads the seed, so the sweep after the import reads each
  change made during the import.
* **Every page (item 2).** The history read follows ``nextPageToken`` to the
  last page. The new cursor is the ``historyId`` of the last answer.
* **The label events (item 3).** ``labelsAdded`` and ``labelsRemoved`` fetch
  the message in full, so an archive, a read mark and a user label made in
  Gmail reach the row.
* **A stale cursor (items 5 and 6, E-B1).** A 404 seeds again and sweeps all
  mail back to the catch-up watermark. A sweep that stops short returns no
  cursor, so the stale one stays. After ``CATCH_UP_MAX_MISSES`` short loop
  cycles the scheduler writes the fresh seed and logs
  ``email.gmail_history_gap_abandoned``.
* **A failed fetch (EM-G4a-f2).** A 5xx or a transport error keeps the old
  position for that cycle. After ``GMAIL_FETCH_HOLD_CYCLES`` cycles in a row
  for one message, the cursor passes it and records it. A 404 is a message
  that is gone, and it never holds the cursor.
* **A deleted draft (E-B2).** A ``[DELETED]`` marker deletes a row in
  ``drafts`` and moves any other row to TRASH.

These tests use ``httpx.MockTransport``, because an ``AsyncMock`` client skips
the auth flow of the client seam. The provider makes no ``authenticate``
request, so each request that the fake sees is a call of the sync.

**R8.** ``TestTheCursorOnARealDatabase`` drives ``_sync_account`` end to end
against the phase-4-promoted catalog of ``test_h3_rls_promotion_rehearsal``,
as the role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS).

Run::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_gmail_history_cursor.py -v -rs
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import email_ingestion.providers.gmail as gmail_mod
import email_ingestion.scheduler as sched
import httpx
import pytest
from acb_common.db import clear_tenant, release_tenant
from acb_common.settings import get_settings

# Import it before the ``fake`` fixture swaps ``httpx.AsyncClient``:
# ``openai``, which it imports, subclasses that class at import.
from acb_llm import key_store
from email_ingestion.persist import upsert_message
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import Attachment, EmailMessage, SyncResult
from email_ingestion.providers.gmail import (
    GMAIL_FETCH_HOLD_CYCLES,
    GMAIL_RESET_SWEEP_NAME,
    GmailProvider,
    GmailRateLimited,
)
from email_ingestion.providers.imap import IMAPProvider
from email_ingestion.providers.outlook import OutlookProvider
from gateway.routes.email import core
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _purge,
    _Store,
)

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

API = "/gmail/v1/users/me"
LOGGER = "email_ingestion.providers.gmail"
SCHED_LOGGER = "email_ingestion.scheduler"
ORG = "00000000-0000-0000-0000-0000000000b4"

#: One answer of the fake: it takes the request and gives the response.
Answer = Callable[[httpx.Request], httpx.Response]


# ── the fake Gmail ──────────────────────────────────────────────────────────


class _Gmail:
    """One fake Gmail API.

    Each request goes into ``seen`` as (method, path, params), in order. The
    path drops the prefix ``/gmail/v1/users/me``. ``profile`` and ``history``
    give their answers in order, and the last one repeats. ``listing``
    answers ``messages.list``. ``messages`` gives each message id its
    answers, in order, and the last one repeats. A message with no answer
    gets a 404."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, dict[str, list[str]]]] = []
        self.profile: list[Answer] = [_ok({"historyId": "500"})]
        self.history: list[Answer] = [_ok({"historyId": "500"})]
        self.listing: Answer = _ok({})
        self.messages: dict[str, list[Answer]] = {}
        self.user_labels: list[tuple[str, str]] = []

    def message(self, mid: str, *answers: Answer) -> None:
        self.messages[mid] = list(answers)

    def requests(self, path: str) -> list[dict[str, list[str]]]:
        """The params of each request to *path*, in order."""
        return [params for _, p, params in self.seen if p == path]

    def first(self, path: str) -> int:
        """The index in ``seen`` of the first request to *path*."""
        return next(i for i, (_, p, _) in enumerate(self.seen) if p == path)

    @staticmethod
    def _next(answers: list[Answer], request: httpx.Request) -> httpx.Response:
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return answer(request)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(API)
        params: dict[str, list[str]] = {}
        for key, value in request.url.params.multi_items():
            params.setdefault(key, []).append(value)
        self.seen.append((request.method, path, params))
        if path == "/profile":
            return self._next(self.profile, request)
        if path == "/history":
            return self._next(self.history, request)
        if path == "/labels":
            return httpx.Response(200, json={"labels": [
                {"id": lid, "name": name, "type": "user"}
                for lid, name in self.user_labels]})
        if path == "/messages":
            return self.listing(request)
        if path.startswith("/messages/"):
            answers = self.messages.get(path.rsplit("/", 1)[-1])
            if answers:
                return self._next(answers, request)
        return httpx.Response(404, json={"error": {"code": 404}})


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


def _message(mid: str, *labels: str, hours_ago: float = 1.0) -> dict[str, Any]:
    """A message in the shape of ``users.messages.get`` with ``format=full``.
    With no label it is archived mail."""
    data = base64.urlsafe_b64encode(b"hi").decode().rstrip("=")
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {
        "id": mid, "threadId": f"t-{mid}", "labelIds": list(labels),
        "snippet": "", "internalDate": str(int(at.timestamp() * 1000)),
        "payload": {"mimeType": "text/plain", "body": {"data": data},
                    "headers": [{"name": "Subject", "value": f"About {mid}"},
                                {"name": "From", "value": "Asha <asha@example.org>"},
                                {"name": "Message-ID", "value": f"<{mid}@example.org>"}]},
    }


def _pages(*pages: dict[str, Any]) -> Answer:
    """The pages of one history read. Each page but the last carries the
    ``nextPageToken`` of the next one: p2, p3 and so on."""
    def _answer(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        index = int(token[1:]) - 1 if token else 0
        body = dict(pages[index])
        if index + 1 < len(pages):
            body["nextPageToken"] = f"p{index + 2}"
        return httpx.Response(200, json=body)
    return _answer


def _added(*ids: str) -> dict[str, Any]:
    return {"messagesAdded": [{"message": {"id": i}} for i in ids]}


def _deleted(*ids: str) -> dict[str, Any]:
    return {"messagesDeleted": [{"message": {"id": i}} for i in ids]}


def _labels_event(event: str, mid: str, *labels: str) -> dict[str, Any]:
    return {event: [{"message": {"id": mid}, "labelIds": list(labels)}]}


def _by_label(**ids: list[str]) -> Answer:
    """``messages.list`` that answers each ``labelIds`` with its ids. A list
    with no ``labelIds`` is the list of all mail, as Gmail gives it: each id
    of each label once. The import of EM-G5a sends that list."""
    def _answer(request: httpx.Request) -> httpx.Response:
        label = request.url.params.get("labelIds")
        found = (ids.get(label, []) if label
                 else list(dict.fromkeys(i for group in ids.values() for i in group)))
        return httpx.Response(200, json={"messages": [{"id": i} for i in found]})
    return _answer


class _Provider(GmailProvider):
    """A Gmail provider whose ``authenticate`` sends no request. So each
    request in ``seen`` is a call of the sync, in order."""

    async def authenticate(self) -> bool:
        return True


def _provider() -> _Provider:
    return _Provider({"access_token": "at-1", "refresh_token": "r-1"},
                     app=OAuthApp(client_id="cid", client_secret="secret"))


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _Gmail:
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


def _ids(result: SyncResult) -> list[str]:
    return [m.provider_message_id for m in result.messages]


def _by_id(result: SyncResult) -> dict[str, EmailMessage]:
    return {m.provider_message_id: m for m in result.messages}


# ── item 1: the seed ────────────────────────────────────────────────────────


async def test_a_sweep_with_no_cursor_seeds_from_get_profile_first(
        fake: _Gmail) -> None:
    """M1. The first request is ``users.getProfile``. The sweep runs after
    it, and the result carries the seed as the new cursor."""
    fake.profile = [_ok({"historyId": "500"})]
    fake.listing = _by_label(INBOX=["m1"])
    fake.message("m1", _ok(_message("m1", "INBOX")))

    result = await _provider().sync_messages()

    assert fake.seen[0][:2] == ("GET", "/profile")
    assert fake.first("/profile") < fake.first("/messages")
    assert result.new_history_id == "500"
    assert _ids(result) == ["m1"]
    assert fake.requests("/history") == []


async def test_a_failed_seed_still_sweeps_and_returns_no_cursor(
        fake: _Gmail, caplog: pytest.LogCaptureFixture) -> None:
    """A failed seed never stops new mail (owner answer Q2). The sweep
    lands the mail, and the next cycle seeds again."""
    fake.profile = [_refuse(500, "backendError")]
    fake.listing = _by_label(INBOX=["m1"])
    fake.message("m1", _ok(_message("m1", "INBOX")))

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await _provider().sync_messages()

    assert _ids(result) == ["m1"]
    assert result.new_history_id is None
    assert any(r.getMessage() == "gmail.seed_failed error=HTTPStatusError status=500"
               for r in caplog.records)


async def test_a_rate_limited_seed_fails_the_sync(fake: _Gmail) -> None:
    """A rate limit is no failed seed. It fails the sync (EM-G4a item 9)."""
    fake.profile = [_refuse(429, "rateLimitExceeded")]

    with pytest.raises(GmailRateLimited):
        await _provider().sync_messages()


@pytest.mark.parametrize("raw", [
    "garbage", '{"history_id": "x1"}', "{not json",
    '{"v":1,"folders":{}}', '{"v": 1, "held_cycles": 2}',
    "١٢", '{"v": 1, "history_id": "١٢"}', "[1, 2]",
], ids=["text", "json_id_not_digits", "broken_json", "outlook_shape",
        "json_with_no_history_id", "arabic_indic_digits",
        "json_arabic_indic_digits", "json_list"])
async def test_text_that_is_no_cursor_seeds_again(fake: _Gmail, raw: str) -> None:
    """Text that is neither a history id nor the JSON form is no cursor. The
    sync seeds again and sends nothing to ``history.list``. A JSON object
    with no ``history_id`` is no cursor (review round 1 F4). A history id
    is ASCII digits only, so "١٢" is no cursor (review round 1 F5)."""
    fake.profile = [_ok({"historyId": "777"})]

    result = await _provider().sync_messages(history_id=raw)

    assert result.new_history_id == "777"
    assert fake.requests("/history") == []


# ── item 2: every page, and the cursor of the last answer ───────────────────


async def test_history_reads_every_page(fake: _Gmail) -> None:
    """M2. The history read follows ``nextPageToken`` to the last page, and
    each page puts its messages into the fetch set. With a cursor,
    ``catch_up`` is unused (item 7): no ``messages.list`` runs."""
    fake.history = [_pages({"historyId": "110", "history": [_added("m1")]},
                           {"historyId": "120", "history": [_added("m2")]},
                           {"historyId": "130", "history": [_added("m3")]})]
    for mid in ("m1", "m2", "m3"):
        fake.message(mid, _ok(_message(mid, "INBOX")))

    result = await _provider().sync_messages(
        history_id="100", catch_up=datetime.now(UTC) - timedelta(days=30),
        since=datetime.now(UTC) - timedelta(days=60))

    assert [p.get("pageToken") for p in fake.requests("/history")] == [
        None, ["p2"], ["p3"]]
    assert all(p["startHistoryId"] == ["100"] for p in fake.requests("/history"))
    assert _ids(result) == ["m1", "m2", "m3"]
    assert result.messages_synced == 3
    assert fake.requests("/messages") == []
    assert fake.requests("/profile") == []


async def test_the_new_cursor_is_the_id_of_the_last_answer(fake: _Gmail) -> None:
    """M6. The cursor is the ``historyId`` of the LAST page. The id of the
    first page would read the changes of the later pages again."""
    fake.history = [_pages({"historyId": "110", "history": []},
                           {"historyId": "120", "history": []},
                           {"historyId": "130", "history": []})]

    result = await _provider().sync_messages(history_id="100")

    assert result.new_history_id == "130"


# ── item 3: the label events ────────────────────────────────────────────────


async def test_inbox_removed_in_gmail_files_the_row_as_archive(
        fake: _Gmail) -> None:
    """M3. An archive in Gmail removes INBOX. The event fetches the message
    in full, and the parse files it as ``archive``."""
    fake.history = [_ok({"historyId": "120", "history": [
        _labels_event("labelsRemoved", "m1", "INBOX")]})]
    fake.message("m1", _ok(_message("m1")))

    result = await _provider().sync_messages(history_id="100")

    assert _by_id(result)["m1"].folder == "archive"
    assert result.new_history_id == "120"


async def test_unread_removed_in_gmail_marks_the_row_read(fake: _Gmail) -> None:
    """M3. A read mark in Gmail removes UNREAD. A star adds STARRED."""
    fake.history = [_ok({"historyId": "120", "history": [
        _labels_event("labelsRemoved", "m1", "UNREAD"),
        _labels_event("labelsAdded", "m1", "STARRED")]})]
    fake.message("m1", _ok(_message("m1", "INBOX", "STARRED")))

    result = await _provider().sync_messages(history_id="100")

    msg = _by_id(result)["m1"]
    assert msg.is_read is True and msg.is_starred is True
    assert fake.requests("/messages/m1") == [{"format": ["full"]}], (
        "two events of one message fetched it twice, or not in full")


async def test_a_user_label_added_in_gmail_reaches_categories(
        fake: _Gmail) -> None:
    """M3. A user label added in Gmail goes to ``categories``."""
    fake.user_labels = [("Label_7", "Clients")]
    fake.history = [_ok({"historyId": "120", "history": [
        _labels_event("labelsAdded", "m1", "Label_7")]})]
    fake.message("m1", _ok(_message("m1", "INBOX", "Label_7")))

    result = await _provider().sync_messages(history_id="100")

    assert _by_id(result)["m1"].categories == ["Clients"]


async def test_a_deleted_message_is_a_marker_and_is_not_fetched(
        fake: _Gmail) -> None:
    """Item 4. ``messagesDeleted`` gives a ``[DELETED]`` marker. A message
    added and deleted in one read has nothing to fetch."""
    fake.history = [_ok({"historyId": "120", "history": [
        _added("m1", "m2"), _deleted("m1")]})]
    fake.message("m2", _ok(_message("m2", "INBOX")))

    result = await _provider().sync_messages(history_id="100")

    assert [(m.provider_message_id, m.subject) for m in result.messages] == [
        ("m1", "[DELETED]"), ("m2", "About m2")]
    assert fake.requests("/messages/m1") == []
    assert result.messages_synced == 2
    assert result.new_history_id == "120"


async def test_a_rate_limit_on_a_later_history_page_fails_the_sync(
        fake: _Gmail) -> None:
    """A rate limit on any history page fails the sync, so phase (d) never
    moves the cursor (EM-G4a item 9)."""
    first = _pages({"historyId": "110", "history": []},
                   {"historyId": "120", "history": []})

    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken"):
            return _refuse(429, "rateLimitExceeded")(request)
        return first(request)

    fake.history = [_answer]

    with pytest.raises(GmailRateLimited):
        await _provider().sync_messages(history_id="100")


# ── items 5 and 6, E-B1: a stale cursor ─────────────────────────────────────


async def test_a_stale_cursor_reseeds_and_sweeps_back_to_the_watermark(
        fake: _Gmail) -> None:
    """M4. A 404 from ``history.list`` is no failure. The provider seeds
    FIRST, then lists all mail with ``after:`` the watermark in epoch
    seconds, ``includeSpamTrash`` and no ``labelIds``. A complete sweep
    returns the seed as the new cursor."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_ok({"historyId": "900"})]
    fake.listing = _pages({"messages": [{"id": "m1"}]},
                          {"messages": [{"id": "m2"}]})
    fake.message("m1", _ok(_message("m1", "INBOX")))
    fake.message("m2", _ok(_message("m2")))
    watermark = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    floor = datetime(2026, 6, 1, tzinfo=UTC)

    result = await _provider().sync_messages(
        history_id="100", catch_up=watermark, since=floor)

    assert fake.first("/history") < fake.first("/profile") < fake.first("/messages")
    [first, second] = fake.requests("/messages")
    assert first == {"maxResults": ["100"], "includeSpamTrash": ["true"],
                     "q": [f"after:{int(watermark.timestamp())}"]}
    assert second["pageToken"] == ["p2"]
    assert _ids(result) == ["m1", "m2"]
    assert result.new_history_id == "900"
    assert result.reseed_history_id == "900"
    assert result.catch_up_incomplete is False


@pytest.mark.parametrize(("catch_up", "since", "after"), [
    (None, datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)),
    (datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC),
     datetime(2026, 6, 1, tzinfo=UTC)),
    (None, None, None),
], ids=["no_watermark_uses_the_floor", "the_floor_binds", "neither"])
async def test_the_reset_sweep_reads_back_to_the_later_of_watermark_and_floor(
        fake: _Gmail, catch_up: datetime | None, since: datetime | None,
        after: datetime | None) -> None:
    """Item 6. With no watermark, the sweep uses the floor. The floor binds
    each sync (EM-T6a), so an older watermark gives way to it."""
    fake.history = [_refuse(404, "notFound")]

    await _provider().sync_messages(history_id="100", catch_up=catch_up,
                                    since=since)

    [params] = fake.requests("/messages")
    assert params.get("q") == (None if after is None
                               else [f"after:{int(after.timestamp())}"])
    assert "labelIds" not in params


async def test_a_short_reset_sweep_keeps_the_stale_cursor(fake: _Gmail) -> None:
    """M7 (E-B1). The sweep reads ``DEEP_SYNC_MAX_PAGES`` pages at most. At
    the cap it returns no new cursor, so the stale one stays. It keeps what
    it read, and it marks the catch-up short."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_ok({"historyId": "900"})]

    def _endless(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        n = int(token[1:]) if token else 1
        return httpx.Response(200, json={"messages": [], "nextPageToken": f"p{n + 1}"})

    fake.listing = _endless

    result = await _provider().sync_messages(history_id="100")

    assert len(fake.requests("/messages")) == GmailProvider.DEEP_SYNC_MAX_PAGES
    assert result.new_history_id is None
    assert result.reseed_history_id == "900"
    assert result.catch_up_incomplete is True
    assert result.catch_up_folders == [GMAIL_RESET_SWEEP_NAME]


@pytest.mark.parametrize("failure", ["page", "fetch", "seed"])
async def test_a_reset_sweep_with_a_failure_is_short(
        fake: _Gmail, failure: str) -> None:
    """E-B1. A page that fails, a fetch that a later cycle can fix, and a
    failed seed each make the sweep short. The mail that it read lands."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_refuse(500) if failure == "seed"
                    else _ok({"historyId": "900"})]
    page_two = _refuse(500) if failure == "page" else _ok({"messages": [{"id": "m2"}]})

    def _listing(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken"):
            return page_two(request)
        return httpx.Response(200, json={"messages": [{"id": "m1"}],
                                         "nextPageToken": "p2"})

    fake.listing = _listing
    fake.message("m1", _ok(_message("m1", "INBOX")))
    fake.message("m2", _refuse(503) if failure == "fetch"
                 else _ok(_message("m2", "INBOX")))

    result = await _provider().sync_messages(history_id="100")

    assert "m1" in _ids(result)
    assert result.new_history_id is None
    assert result.catch_up_incomplete is True


# ── EM-G4a-f2: a failed fetch on the history path ───────────────────────────


def _one_change(fake: _Gmail, last_id: str = "120") -> None:
    fake.history = [_ok({"historyId": last_id, "history": [_added("m1", "m2")]})]
    fake.message("m1", _ok(_message("m1", "INBOX")))


async def test_a_5xx_fetch_keeps_the_old_cursor(fake: _Gmail) -> None:
    """M21. A fetch that fails with a 5xx keeps the OLD position, so the
    next cycle reads the message again. The other message still syncs, and
    the failed fetch leaves its record."""
    _one_change(fake)
    fake.message("m2", _refuse(503, "backendError"))

    result = await _provider().sync_messages(history_id="100")

    assert _ids(result) == ["m1"]
    assert json.loads(result.new_history_id) == {
        "v": 1, "history_id": "100", "held_cycles": 1}
    assert result.errors == [
        "gmail.fetch_failed id=m2 error=HTTPStatusError status=503"]


async def test_a_transport_error_holds_the_cursor(fake: _Gmail) -> None:
    """A transport error is a failure that a later cycle can fix, as a 5xx
    is."""
    _one_change(fake)
    fake.message("m2", _transport_error)

    result = await _provider().sync_messages(history_id="100")

    assert json.loads(result.new_history_id)["held_cycles"] == 1


async def test_a_held_cursor_holds_no_message_id(fake: _Gmail) -> None:
    """M24 (review round 1 F7). 150 fetches fail in one cycle, and the held
    cursor keeps its three keys. It stores no message id, so its size never
    grows."""
    ids = [f"x{i}" for i in range(150)]
    fake.history = [_ok({"historyId": "120", "history": [_added(*ids)]})]
    for mid in ids:
        fake.message(mid, _refuse(503))

    result = await _provider().sync_messages(history_id="100")

    assert json.loads(result.new_history_id) == {
        "v": 1, "history_id": "100", "held_cycles": 1}
    assert len(result.new_history_id) < 64
    assert len(result.errors) == 150


def _no_id(_request: httpx.Request) -> httpx.Response:
    """A body that fails the parse."""
    raw = _message("m2", "INBOX")
    del raw["id"]
    return httpx.Response(200, json=raw)


@pytest.mark.parametrize("answer", [
    _refuse(404, "notFound"), _refuse(400, "invalidArgument"), _no_id,
], ids=["gone", "refused", "parse"])
async def test_a_failure_that_stays_does_not_hold_the_cursor(
        fake: _Gmail, answer: Answer) -> None:
    """A 404 means that the message is gone, and a 4xx or a parse error
    stays the same. Each leaves its record, and the cursor moves on."""
    _one_change(fake)
    fake.message("m2", answer)

    result = await _provider().sync_messages(history_id="100")

    assert result.new_history_id == "120"
    assert len(result.errors) == 1 and result.errors[0].startswith(
        "gmail.fetch_failed id=m2 ")


async def test_a_message_that_fails_three_cycles_is_passed_and_recorded(
        fake: _Gmail, caplog: pytest.LogCaptureFixture) -> None:
    """M22. The same message fails ``GMAIL_FETCH_HOLD_CYCLES`` cycles in a
    row. The first cycles hold the cursor. The last one passes the message,
    logs it and records it, so one bad message cannot stall the mailbox."""
    _one_change(fake)
    fake.message("m2", _refuse(500, "backendError"))
    cursor = "100"
    seen: list[Any] = []

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(GMAIL_FETCH_HOLD_CYCLES):
            result = await _provider().sync_messages(history_id=cursor)
            cursor = result.new_history_id
            seen.append(cursor)

    assert [json.loads(c)["held_cycles"] for c in seen[:-1]] == list(
        range(1, GMAIL_FETCH_HOLD_CYCLES))
    assert seen[-1] == "120"
    assert result.errors == [
        "gmail.fetch_failed id=m2 error=HTTPStatusError status=500",
        f"gmail.fetch_abandoned id=m2 held_cycles={GMAIL_FETCH_HOLD_CYCLES}"]
    logged = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("gmail.fetch_abandoned")]
    assert logged == [
        f"gmail.fetch_abandoned message_id=m2 held_cycles={GMAIL_FETCH_HOLD_CYCLES}"]
    for line in [*result.errors, *logged]:
        assert "@" not in line and "About" not in line


async def test_a_new_failure_each_cycle_cannot_hold_the_cursor_longer(
        fake: _Gmail) -> None:
    """M23 (review round 1 F1). m2 fails on each cycle, and a NEW message
    fails on each cycle too. The count belongs to the cursor, so the cursor
    moves on at cycle ``GMAIL_FETCH_HOLD_CYCLES`` and records both messages
    that still fail. A count for each message held the cursor with no end."""
    fake.history = [
        _ok({"historyId": "120", "history": [_added("m2", f"n{k}")]})
        for k in range(1, GMAIL_FETCH_HOLD_CYCLES + 1)]
    fake.message("m2", _refuse(500))
    for k in range(1, GMAIL_FETCH_HOLD_CYCLES + 1):
        fake.message(f"n{k}", _refuse(503))
    cursor = "100"
    cursors: list[Any] = []

    for _ in range(GMAIL_FETCH_HOLD_CYCLES):
        result = await _provider().sync_messages(history_id=cursor)
        cursor = result.new_history_id
        cursors.append(cursor)

    assert [json.loads(c)["held_cycles"] for c in cursors[:-1]] == [1, 2]
    assert cursors[-1] == "120"
    last = f"n{GMAIL_FETCH_HOLD_CYCLES}"
    assert [e for e in result.errors if e.startswith("gmail.fetch_abandoned")] == [
        f"gmail.fetch_abandoned id=m2 held_cycles={GMAIL_FETCH_HOLD_CYCLES}",
        f"gmail.fetch_abandoned id={last} held_cycles={GMAIL_FETCH_HOLD_CYCLES}"]


async def test_a_message_that_recovers_drops_out_of_the_count(
        fake: _Gmail) -> None:
    """A message that succeeds on the next cycle drops out of the count, and
    the cursor moves on."""
    _one_change(fake)
    fake.message("m2", _refuse(500), _ok(_message("m2", "INBOX")))

    held = await _provider().sync_messages(history_id="100")
    again = await _provider().sync_messages(history_id=held.new_history_id)

    assert json.loads(held.new_history_id)["held_cycles"] == 1
    assert again.new_history_id == "120"
    assert sorted(_ids(again)) == ["m1", "m2"]
    assert again.errors == []
    assert fake.requests("/history")[1]["startHistoryId"] == ["100"]


# ── the scheduler, hermetic ─────────────────────────────────────────────────


def _flat(stmt: Any) -> str:
    return " ".join(str(stmt).split())


class _Res:
    def __init__(self, row: Any) -> None:
        self.row = row

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> list[Any]:
        return []

    def scalar(self) -> Any:
        return None


def _row(**over: Any) -> SimpleNamespace:
    now = datetime.now(UTC)
    base = dict(id="acc-1", provider="gmail", credentials_encrypted="x",
                last_history_id=None, sync_interval_secs=300,
                initial_sync_done=True, import_since=now - timedelta(days=30),
                import_reached_at=None, import_count=None,
                last_synced_at=now - timedelta(days=10), created_at=now,
                db_now=now, categories=[])
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture()
def cycle(monkeypatch: pytest.MonkeyPatch, fake: _Gmail) -> SimpleNamespace:
    """Run ``_sync_account`` on fake sessions with a real Gmail provider on
    the fake. ``log`` holds each statement with its params."""
    log: list[tuple[str, dict[str, Any]]] = []
    upserts: list[EmailMessage] = []

    async def _upsert(db: Any, account_id: str, msg: EmailMessage, *,
                      reclaim: bool = False) -> None:
        upserts.append(msg)

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "upsert_message", _upsert)
    monkeypatch.setattr(sched, "run_label_learn_hook", _noop)
    monkeypatch.setattr(sched, "_catch_up_misses", {})
    monkeypatch.setattr(sched, "_catch_up_abandoned", set())

    async def _run(row: SimpleNamespace, **kw: Any) -> dict[str, Any]:
        @asynccontextmanager
        async def _ts(org: Any = None):
            class _Db:
                async def execute(self, stmt: Any, params: Any = None,
                                  *_a: Any, **_k: Any) -> _Res:
                    log.append((_flat(stmt), dict(params or {})))
                    return _Res(row)
            yield _Db()

        provider = _provider()
        monkeypatch.setattr(sched, "build_provider", lambda name, creds: provider)
        monkeypatch.setattr(sched, "tenant_session", _ts)
        return await sched._sync_account("acc-1", organization_id=ORG, **kw)

    def _phase_d() -> list[dict[str, Any]]:
        return [p for sql, p in log if "last_history_id = COALESCE" in sql]

    return SimpleNamespace(run=_run, log=log, upserts=upserts, phase_d=_phase_d)


async def test_the_first_import_seeds_before_it_imports(
        fake: _Gmail, cycle: SimpleNamespace) -> None:
    """M19 (E-B3). With no cursor, the seed comes BEFORE the import. The
    sweep of the same cycle reads the history from the seed, so an archive
    made in Gmail during the import reaches the row."""
    fake.profile = [_ok({"historyId": "500"})]
    fake.listing = _by_label(INBOX=["m1"])
    fake.message("m1", _ok(_message("m1", "INBOX")), _ok(_message("m1")))
    fake.history = [_ok({"historyId": "510", "history": [
        _labels_event("labelsRemoved", "m1", "INBOX")]})]

    res = await cycle.run(_row(initial_sync_done=False, last_synced_at=None))

    assert "error" not in res, res
    assert fake.first("/profile") < fake.first("/messages"), (
        "the seed came after the import")
    assert [p["startHistoryId"] for p in fake.requests("/history")] == [["500"]]
    assert [(m.provider_message_id, m.folder) for m in cycle.upserts] == [
        ("m1", "inbox"), ("m1", "archive")]
    assert [p["history_id"] for p in cycle.phase_d()] == ["510"]
    assert res["history_id"] == "510"


async def test_a_failed_seed_before_the_import_still_imports(
        fake: _Gmail, cycle: SimpleNamespace,
        caplog: pytest.LogCaptureFixture) -> None:
    """A failed seed never stops new mail (owner answer Q2). The import and
    the sweep run, and the cycle stores no cursor."""
    fake.profile = [_refuse(500)]
    fake.listing = _by_label(INBOX=["m1"])
    fake.message("m1", _ok(_message("m1", "INBOX")))

    with caplog.at_level(logging.WARNING, logger=SCHED_LOGGER):
        res = await cycle.run(_row(initial_sync_done=False, last_synced_at=None))

    assert "error" not in res, res
    assert "m1" in [m.provider_message_id for m in cycle.upserts]
    assert [p["history_id"] for p in cycle.phase_d()] == [None]
    assert any(r.getMessage() == "sync.seed_failed account=acc-1 error=HTTPStatusError"
               for r in caplog.records)


async def test_a_resync_clears_the_cursor_and_seeds_a_new_one(
        fake: _Gmail, cycle: SimpleNamespace) -> None:
    """Item 11. A Resync clears the cursor in phase (a). Item 1 then seeds
    a new one before the import, and the sweep reads from the new seed, not
    from the old cursor."""
    fake.profile = [_ok({"historyId": "800"})]
    fake.history = [_ok({"historyId": "805"})]

    res = await cycle.run(_row(last_history_id="100"), deep=True,
                          reset_cursor=True)

    assert "error" not in res, res
    assert any("SET last_history_id = NULL" in sql for sql, _ in cycle.log)
    assert [p["startHistoryId"] for p in fake.requests("/history")] == [["800"]]
    assert [p["history_id"] for p in cycle.phase_d()] == ["805"]


@pytest.mark.parametrize("provider", [
    OutlookProvider({"access_token": "at"}),
    IMAPProvider({"host": "imap.example.org", "username": "u", "password": "p"}),
], ids=["outlook", "imap"])
async def test_a_provider_with_no_cursor_gets_no_seed(provider: Any) -> None:
    """E-B3. Outlook and IMAP keep the default of the base class, so the
    value that the import passes on does not change."""
    assert await sched._seed_before_import("acc-1", provider, None) is None
    assert await sched._seed_before_import("acc-1", provider, "") == ""
    assert await sched._seed_before_import("acc-1", provider, "7:9") == "7:9"


async def test_a_complete_reset_sweep_writes_the_seed(
        fake: _Gmail, cycle: SimpleNamespace,
        caplog: pytest.LogCaptureFixture) -> None:
    """Items 5 and 11. A stale cursor seeds again, the sweep reads to the
    end, and phase (d) writes the seed. The log names the mailbox."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_ok({"historyId": "900"})]
    fake.listing = _ok({"messages": [{"id": "m1"}]})
    fake.message("m1", _ok(_message("m1", "INBOX")))

    with caplog.at_level(logging.WARNING, logger=SCHED_LOGGER):
        res = await cycle.run(_row(last_history_id="100"))

    assert "error" not in res, res
    [phase_d] = cycle.phase_d()
    assert phase_d["history_id"] == "900"
    assert phase_d["keep_watermark"] is False
    assert any(r.getMessage()
               == "gmail.history_reset account=acc-1 complete=True seeded=True"
               for r in caplog.records)


async def test_a_spent_rate_limit_on_the_seed_fails_the_cycle_before_the_import(
        fake: _Gmail, cycle: SimpleNamespace) -> None:
    """M25 (review round 1 F2). A 429 on each of the 3 tries of the seed is
    no failed seed. The cycle fails before the import sends a request, and
    phase (d) writes nothing (EM-G4a item 9)."""
    fake.profile = [_refuse(429, "rateLimitExceeded")]
    fake.listing = _by_label(INBOX=["m1"])

    res = await cycle.run(_row(initial_sync_done=False, last_synced_at=None))

    assert "error" in res, res
    assert [path for _, path, _ in fake.seen] == ["/profile"] * 3
    assert cycle.phase_d() == []
    assert any("sync_status = 'error'" in sql for sql, _ in cycle.log)


async def test_a_failed_reseed_logs_the_mailbox_and_backs_the_loop_off(
        fake: _Gmail, cycle: SimpleNamespace,
        caplog: pytest.LogCaptureFixture) -> None:
    """M26 (review round 1 F6). The mailbox holds a sticky abandon, and the
    seed of the reset fails. The scheduler logs the reset and the failed
    reseed with the mailbox id. The cycle counts as a soft failure, so the
    loop backs off and does not sweep at each interval."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_refuse(500)]
    fake.listing = _ok({"messages": []})
    sched._catch_up_abandoned.add("acc-1")

    with caplog.at_level(logging.WARNING, logger=SCHED_LOGGER):
        res = await cycle.run(_row(last_history_id="100"), from_loop=True)

    assert res.get("catch_up_incomplete") is True, res
    assert [w["history_id"] for w in cycle.phase_d()] == [None]
    lines = [r.getMessage() for r in caplog.records]
    assert "gmail.history_reset account=acc-1 complete=False seeded=False" in lines
    assert "gmail.reseed_failed account=acc-1" in lines


def _log_writes(log: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """The ``provider_history_id`` of each sync-log write of phase (d)."""
    return [p["history_id"] for sql, p in log
            if "provider_history_id = :history_id" in sql]


async def test_the_sync_log_keeps_no_held_cursor(
        fake: _Gmail, cycle: SimpleNamespace) -> None:
    """M27 (review round 1 F7). A held cycle writes the JSON cursor into the
    account row and NULL into the sync log, as the Outlook delta does. The
    next cycle moves on, and the sync log gets the plain history id."""
    fake.history = [_ok({"historyId": "120", "history": [_added("m1")]})]
    fake.message("m1", _refuse(503), _ok(_message("m1", "INBOX")))

    await cycle.run(_row(last_history_id="100"))
    held = cycle.phase_d()[0]["history_id"]
    await cycle.run(_row(last_history_id=held))

    assert json.loads(held)["held_cycles"] == 1
    assert [w["history_id"] for w in cycle.phase_d()] == [held, "120"]
    assert _log_writes(cycle.log) == [None, "120"]


async def test_six_short_loop_cycles_abandon_the_gap_and_write_the_fresh_seed(
        fake: _Gmail, cycle: SimpleNamespace,
        caplog: pytest.LogCaptureFixture) -> None:
    """M20 (E-B1, the open point of the audit, decided 2026-10-05). Each loop
    cycle gets the 404 and a sweep that stops at the page cap. The first
    five keep the stale cursor and the watermark. The sixth abandons the
    gap through the rule of the Outlook catch-up (``CATCH_UP_MAX_MISSES``):
    phase (d) writes the fresh seed and moves the watermark on."""
    fake.history = [_refuse(404, "notFound")]
    fake.profile = [_ok({"historyId": "900"})]

    def _endless(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        n = int(token[1:]) if token else 1
        return httpx.Response(200, json={"messages": [], "nextPageToken": f"p{n + 1}"})

    fake.listing = _endless
    row = _row(last_history_id="100")

    with caplog.at_level(logging.WARNING, logger=SCHED_LOGGER):
        results = [await cycle.run(row, from_loop=True)
                   for _ in range(sched.CATCH_UP_MAX_MISSES)]

    writes = cycle.phase_d()
    assert [w["history_id"] for w in writes] == [None] * 5 + ["900"]
    assert [w["keep_watermark"] for w in writes] == [True] * 5 + [False]
    assert all(r.get("catch_up_incomplete") for r in results[:5])
    assert results[5].get("catch_up_abandoned") is True
    assert f"The catch-up of {GMAIL_RESET_SWEEP_NAME} stopped after" in (
        writes[5]["sync_note"])
    abandoned = [r.getMessage() for r in caplog.records
                 if r.getMessage().startswith("email.gmail_history_gap_abandoned")]
    assert abandoned == [
        f"email.gmail_history_gap_abandoned account=acc-1 "
        f"misses={sched.CATCH_UP_MAX_MISSES}"]


async def test_a_short_cycle_of_a_member_act_does_not_count(
        fake: _Gmail, cycle: SimpleNamespace) -> None:
    """Only a loop cycle counts toward the abandon, as for Outlook. Six
    manual syncs keep the stale cursor each time."""
    fake.history = [_refuse(404, "notFound")]
    fake.listing = _ok({"messages": [], "nextPageToken": "p2"})
    row = _row(last_history_id="100")

    for _ in range(sched.CATCH_UP_MAX_MISSES):
        await cycle.run(row)

    assert [w["history_id"] for w in cycle.phase_d()] == [None] * 6


# ── R8: the cursor on a real database ───────────────────────────────────────


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _gmail_account(admin: Any, org: str, provider: str = "gmail") -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, "
            "sync_status, organization_id) "
            "VALUES (:u, :p, :m, 'x', true, 300, 'idle', CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": f"g4b-{uuid.uuid4().hex[:8]}@em-g4b.test", "p": provider,
             "m": f"box-{uuid.uuid4().hex[:8]}@em-g4b.test",
             "o": org}).scalar_one())


def _sync_log_cursors(admin: Any, account_id: str) -> list[str | None]:
    """``provider_history_id`` of each sync-log row, oldest first."""
    with admin.connect() as c:
        return list(c.execute(text(
            "SELECT provider_history_id FROM email_sync_log "
            "WHERE account_id = CAST(:a AS uuid) ORDER BY started_at"),
            {"a": account_id}).scalars().all())


def _row_state(admin: Any, account_id: str, pmid: str) -> tuple[str, str, int] | None:
    """The id and the folder of one stored message, and its attachment count."""
    with admin.connect() as c:
        row = c.execute(text(
            "SELECT m.id::text AS id, m.folder, "
            "(SELECT count(*) FROM email_attachments a WHERE a.message_id = m.id) AS n "
            "FROM email_messages m WHERE m.account_id = CAST(:a AS uuid) "
            "AND m.provider_message_id = :p"), {"a": account_id, "p": pmid}).first()
    return None if row is None else (row.id, row.folder, int(row.n))


def _account_state(admin: Any, account_id: str) -> dict[str, Any]:
    with admin.connect() as c:
        return dict(c.execute(text(
            "SELECT last_history_id, sync_status, initial_sync_done "
            "FROM email_accounts WHERE id = CAST(:a AS uuid)"),
            {"a": account_id}).mappings().one())


def _stored_rows(admin: Any, account_id: str) -> dict[str, tuple[str, bool]]:
    with admin.connect() as c:
        rows = c.execute(text(
            "SELECT provider_message_id, folder, is_read FROM email_messages "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": account_id}).all()
    return {r.provider_message_id: (r.folder, r.is_read) for r in rows}


def _drop(admin: Any, account_id: str) -> None:
    with admin.begin() as c:
        c.execute(text("DELETE FROM email_messages WHERE account_id = "
                       "CAST(:a AS uuid)"), {"a": account_id})
    _purge(admin, [account_id])


@pytest.fixture()
def real_cycle(monkeypatch: pytest.MonkeyPatch, fake: _Gmail) -> Callable[..., Any]:
    """``_sync_account`` on the real database, with a real Gmail provider on
    the fake."""
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: _provider())
    monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", False)
    monkeypatch.setattr(sched, "_catch_up_misses", {})
    monkeypatch.setattr(sched, "_catch_up_abandoned", set())

    async def _run(dsn: str, account_id: str, org: str) -> dict[str, Any]:
        async with tenant_engine_scope(dsn):
            return await sched._sync_account(account_id, organization_id=org)

    return _run


@_DB_GATE
class TestTheCursorOnARealDatabase:
    """``gmail-history-cursor-write`` (R8, spec §12.3.5.2)."""

    async def test_the_cursor_reaches_last_history_id_and_the_next_cycle_reads_it(
        self, promoted, app_engine, fake, real_cycle,  # noqa: F811
    ):
        """Five cycles of one new Gmail mailbox, through the real SQL of
        phases (a) to (d).

        1. The first import seeds BEFORE it imports, and the sweep reads the
           history from the seed. ``last_history_id`` holds the seed.
        2. The next cycle reads two history pages from the stored cursor. An
           archive and a read mark made in Gmail reach the row, and the
           cursor is the id of the last answer.
        3. A 5xx on one fetch keeps the old position in the stored cursor.
        4. A rate limit fails the cycle, and the stored cursor stays.
        5. The message syncs, and the cursor moves on."""
        _assert_non_priv(app_engine)
        p = promoted
        account = _gmail_account(p.admin_engine, p.org_b)
        cleared = clear_tenant()
        try:
            # 1. The first import.
            fake.profile = [_ok({"historyId": "500"})]
            fake.listing = _by_label(INBOX=["m1"])
            fake.message("m1", _ok(_message("m1", "INBOX", "UNREAD")))
            fake.history = [_ok({"historyId": "500"})]
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            state = _account_state(p.admin_engine, account)
            assert state["last_history_id"] == "500"
            assert state["initial_sync_done"] is True
            assert fake.first("/profile") < fake.first("/messages")
            assert _stored_rows(p.admin_engine, account) == {"m1": ("inbox", False)}

            # 2. Two pages from the stored cursor.
            fake.history = [_pages(
                {"historyId": "510", "history": [
                    _labels_event("labelsRemoved", "m1", "INBOX", "UNREAD")]},
                {"historyId": "520", "history": [_added("m2")]})]
            fake.message("m1", _ok(_message("m1")))
            fake.message("m2", _ok(_message("m2", "INBOX")))
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            assert fake.requests("/history")[-2]["startHistoryId"] == ["500"]
            assert _account_state(p.admin_engine, account)["last_history_id"] == "520"
            assert _stored_rows(p.admin_engine, account) == {
                "m1": ("archive", True), "m2": ("inbox", True)}

            # 3. A 5xx keeps the old position.
            fake.history = [_ok({"historyId": "530", "history": [_added("m3")]})]
            fake.message("m3", _refuse(503, "backendError"))
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            held = _account_state(p.admin_engine, account)["last_history_id"]
            assert json.loads(held) == {"v": 1, "history_id": "520",
                                        "held_cycles": 1}

            # 4. A rate limit fails the cycle, and the cursor stays.
            fake.history = [_refuse(429, "rateLimitExceeded")]
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" in res, res
            state = _account_state(p.admin_engine, account)
            assert state["last_history_id"] == held
            assert state["sync_status"] == "error"

            # 5. The message syncs, and the cursor moves on.
            fake.history = [_ok({"historyId": "540", "history": [_added("m3")]})]
            fake.message("m3", _ok(_message("m3", "INBOX")))
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            assert fake.requests("/history")[-1]["startHistoryId"] == ["520"]
            assert _account_state(p.admin_engine, account)["last_history_id"] == "540"
            assert "m3" in _stored_rows(p.admin_engine, account)
            # The sync log keeps no held cursor (review round 1 F7). The
            # failed cycle 4 wrote no cursor into its log row.
            assert _sync_log_cursors(p.admin_engine, account) == [
                "500", "520", None, None, "540"]
        finally:
            release_tenant(cleared)
            _drop(p.admin_engine, account)

    async def test_a_deleted_draft_leaves_no_row_in_trash(
        self, promoted, app_engine, fake, real_cycle,  # noqa: F811
    ):
        """M18 (E-B2). An edit of a draft in Gmail web gives the draft a new
        message id and deletes the old one. The ``[DELETED]`` marker of the
        old id deletes its row in ``drafts``, and nothing goes to Trash. A
        deleted message in any other folder still moves to TRASH."""
        _assert_non_priv(app_engine)
        p = promoted
        account = _gmail_account(p.admin_engine, p.org_b)
        cleared = clear_tenant()
        try:
            fake.profile = [_ok({"historyId": "700"})]
            fake.listing = _by_label(INBOX=["i1"], DRAFT=["d1"])
            fake.message("i1", _ok(_message("i1", "INBOX")))
            fake.message("d1", _ok(_message("d1", "DRAFT")))
            fake.history = [_ok({"historyId": "700"})]
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            assert _stored_rows(p.admin_engine, account) == {
                "i1": ("inbox", True), "d1": ("drafts", True)}

            fake.history = [_ok({"historyId": "710", "history": [
                _added("d2"), _deleted("d1"), _deleted("i1")]})]
            fake.message("d2", _ok(_message("d2", "DRAFT")))
            res = await real_cycle(_dsn(p), account, p.org_b)
            assert "error" not in res, res
            assert _stored_rows(p.admin_engine, account) == {
                "i1": ("TRASH", True), "d2": ("drafts", True)}, (
                "a deleted draft left a row, or a deleted mail left its folder")
        finally:
            release_tenant(cleared)
            _drop(p.admin_engine, account)

    async def test_a_real_subject_of_deleted_keeps_its_draft_row(
        self, promoted, app_engine,  # noqa: F811
    ):
        """M28 (review round 1 F3). An Outlook draft and an IMAP draft whose
        REAL subject is "[DELETED]" keep their rows and their attachments, as
        on main: each moves to TRASH. Only a Gmail marker, which sets
        ``deletion_marker``, deletes a draft row."""
        _assert_non_priv(app_engine)
        p = promoted
        boxes = {kind: _gmail_account(p.admin_engine, p.org_b, provider=kind)
                 for kind in ("microsoft", "imap", "gmail")}
        folders = {"microsoft": "drafts", "imap": "Drafts", "gmail": "drafts"}
        floor = datetime(2000, 1, 1, tzinfo=UTC)
        cleared = clear_tenant()
        try:
            async with tenant_engine_scope(_dsn(p)):
                async with core._tenant_session(p.org_b) as db:
                    for kind, account in boxes.items():
                        await upsert_message(db, account, _draft(
                            f"{kind}-d1", folders[kind]), reclaim=False)
                before = {kind: _row_state(p.admin_engine, account, f"{kind}-d1")
                          for kind, account in boxes.items()}
                async with core._tenant_session(p.org_b) as db:
                    for kind in ("microsoft", "imap"):
                        real = _draft(f"{kind}-d1", folders[kind],
                                      subject="[DELETED]")
                        await sched._write_messages(
                            db, boxes[kind], [real], floor, reclaim=False)
                    await sched._write_messages(
                        db, boxes["gmail"], [gmail_mod._deleted_marker("gmail-d1")],
                        floor, reclaim=False)
            for kind in ("microsoft", "imap"):
                row_id, folder, attachments = before[kind]
                assert (folder, attachments) == (folders[kind], 1), kind
                assert _row_state(p.admin_engine, boxes[kind], f"{kind}-d1") == (
                    row_id, "TRASH", 1), f"the {kind} draft lost its row"
            assert before["gmail"] is not None
            assert _row_state(p.admin_engine, boxes["gmail"], "gmail-d1") is None
        finally:
            release_tenant(cleared)
            for account in boxes.values():
                _drop(p.admin_engine, account)


def _draft(pmid: str, folder: str, *, subject: str = "Quote") -> EmailMessage:
    """A draft with one attachment, in the shape that each provider gives."""
    return EmailMessage(
        provider_message_id=pmid, thread_id=f"t-{pmid}", folder=folder,
        subject=subject, body_text="Please send it.", snippet="Please send it.",
        has_attachments=True,
        attachments=[Attachment(id=f"a-{pmid}", filename="quote.pdf",
                                mime_type="application/pdf", size_bytes=10,
                                provider_attachment_id=f"att-{pmid}")],
        received_at=datetime.now(UTC) - timedelta(minutes=5))
