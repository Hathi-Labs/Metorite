"""WS-17 EM-G4a — the rate limits of Gmail, and the record of a failed fetch.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.5.1, items 8 to 10.

``GmailBearer`` (``providers/gmail.py``) is the bearer of the Gmail client.
``_get_client`` sets it, so each Gmail call gets one rule:

* A 429, or a 403 whose reason is ``rateLimitExceeded`` or
  ``userRateLimitExceeded``, waits for ``Retry-After`` or a back-off. Then
  the same request goes again. After 3 tries the flow raises
  ``GmailRateLimited``.
* A plain 403 and a 5xx go back to the caller at once.
* One wait is 30 seconds at most. The waits of one client add up to 60
  seconds at most, so a sync cycle cannot hang on rate limits.
* A send never goes out twice. Only a GET, a PUT, a DELETE or a POST that
  sets a state goes again.
* A 401 still refreshes once.

A fetch that fails for another reason leaves a record: ``gmail.fetch_failed``
with the message id, and with no subject and no address. The other messages
still parse. A fetch whose tries are spent raises, so the sync fails, the
loop backs off and the cursor stays.

These tests use ``httpx.MockTransport``, because an ``AsyncMock`` client
skips the auth flow. Each test drives the real ``_get_client``, and the
``authenticate`` probe and the token refresh reach the same fake. The tests
replace ``gmail._wait``, so no test sleeps.

**Hermetic.** No database.

Run::

    uv run pytest tests/unit/test_gmail_rate_limits.py -v -rs
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import email_ingestion.providers.gmail as gmail_mod
import httpx
import pytest
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.base import RefreshingBearer
from email_ingestion.providers.gmail import (
    GMAIL_MAX_TRIES,
    GMAIL_MAX_WAIT_SECS,
    GMAIL_WAIT_BUDGET_SECS,
    GmailBearer,
    GmailProvider,
    GmailRateLimited,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "/gmail/v1/users/me"
#: The upload URI of the three writes (EM-G3c-1 item 3).
UPLOAD = "/upload" + API
LOGGER = "email_ingestion.providers.gmail"

#: One answer of the fake: it takes the request and gives the response.
Answer = Callable[[httpx.Request], httpx.Response]


# ── the fake Gmail ──────────────────────────────────────────────────────────


class _Gmail:
    """One fake Gmail API and its OAuth token endpoint.

    ``on`` gives a path its answers, in order. The last answer repeats. The
    access token ``at-1`` works until a test drops it from ``valid``. A
    refresh mints ``at-<n>``, and each older token stops working.

    A write on the upload URI (EM-G3c-1) has the same path under
    ``/upload``. The fake reads it as that path, so one script of ``on``
    answers both URIs. ``uploads`` records the path of each upload.
    """

    def __init__(self) -> None:
        self.scripts: dict[tuple[str, str], list[Answer]] = {}
        #: (method, path, bearer) for each API request, the probe left out.
        self.seen: list[tuple[str, str, str]] = []
        #: (method, path) of each request on the upload URI (EM-G3c-1).
        self.uploads: list[tuple[str, str]] = []
        self.valid = {"at-1"}
        self.refreshes = 0
        self.n = 1
        #: Each wait that ``gmail._wait`` was asked for, in seconds.
        self.waits: list[float] = []

    def on(self, method: str, path: str, *answers: Answer) -> None:
        self.scripts[(method, API + path)] = list(answers)

    def calls(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.seen if (m, p) == (method, API + path))

    def bearers(self, method: str, path: str) -> list[str]:
        return [b for m, p, b in self.seen if (m, p) == (method, API + path)]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.refreshes += 1
            self.n += 1
            self.valid = {f"at-{self.n}"}
            return httpx.Response(200, json={"access_token": f"at-{self.n}",
                                             "refresh_token": f"r-{self.n}"})
        if request.url.path == API + "/profile":
            # The probe of ``authenticate``. It never refreshes here.
            return httpx.Response(200, json={"historyId": "1"})
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        path = request.url.path
        if path.startswith(UPLOAD):
            path = API + path.removeprefix(UPLOAD)
            self.uploads.append((request.method, path))
        self.seen.append((request.method, path, bearer))
        if bearer not in self.valid:
            return httpx.Response(401, json={"error": {"code": 401}})
        answers = self.scripts.get((request.method, path))
        if not answers:
            return httpx.Response(404, json={"error": {"code": 404}})
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return answer(request)


def _ok(body: dict[str, Any] | None = None) -> Answer:
    return lambda _request: httpx.Response(200, json=body or {})


def _refuse(code: int, reason: str | None = None, *,
            retry_after: str | None = None) -> Answer:
    """An error in the shape of the Gmail API."""
    errors = [{"domain": "usageLimits", "reason": reason}] if reason else []
    body = {"error": {"code": code, "message": "refused", "errors": errors}}
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return lambda _request: httpx.Response(code, json=body, headers=headers)


def _rate_limited(retry_after: str | None = "0") -> Answer:
    return _refuse(429, "rateLimitExceeded", retry_after=retry_after)


def _message(mid: str, subject: str = "Hello",
             sender: str = "Asha <asha@example.org>") -> dict[str, Any]:
    """A message in the shape of ``users.messages.get`` with ``format=full``."""
    data = base64.urlsafe_b64encode(b"hi").decode().rstrip("=")
    return {
        "id": mid, "threadId": f"t-{mid}", "labelIds": ["INBOX"],
        "snippet": "", "internalDate": "1790000000000",
        "payload": {"mimeType": "text/plain", "body": {"data": data},
                    "headers": [{"name": "Subject", "value": subject},
                                {"name": "From", "value": sender}]},
    }


def _labels(*user: tuple[str, str]) -> Answer:
    return _ok({"labels": [{"id": lid, "name": name, "type": "user"}
                           for lid, name in user]})


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> _Gmail:
    """Send each ``httpx.AsyncClient`` that the provider builds to one fake.

    The provider client, the ``authenticate`` probe and the token refresh
    each build their own client, so all three reach the fake. ``_wait``
    records each wait and does not sleep."""
    service = _Gmail()
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


def _provider() -> GmailProvider:
    return GmailProvider({"access_token": "at-1", "refresh_token": "r-1"},
                         app=OAuthApp(client_id="cid", client_secret="secret"))


# ── the seam ────────────────────────────────────────────────────────────────


async def test_get_client_puts_the_helper_on_each_call(fake: _Gmail) -> None:
    """R7 fence ``gmail-rate-limit-at-the-seam`` (M15). The helper is the
    bearer of the client, so no call site can miss it. It is still a
    ``RefreshingBearer``, so the fence of EM-T4c holds too."""
    p = _provider()
    client = await p._get_client()

    assert isinstance(client.auth, GmailBearer)
    assert isinstance(client.auth, RefreshingBearer)
    assert client.auth._provider is p
    assert "authorization" not in client.headers


# ── item 8: what is a rate limit ────────────────────────────────────────────


@pytest.mark.parametrize("form", ["seconds", "http-date"])
async def test_a_429_waits_for_retry_after_and_succeeds(
        fake: _Gmail, form: str) -> None:
    """M5 and M16. The wait is what ``Retry-After`` asks, in either form.
    The HTTP date is computed in the body, so a slow run cannot put it in
    the past."""
    if form == "seconds":
        retry_after, low, high = "2", 2.0, 2.0
    else:
        when = datetime.now(UTC) + timedelta(seconds=20)
        retry_after, low, high = format_datetime(when, usegmt=True), 5.0, 20.0
    fake.on("GET", "/labels", _rate_limited(retry_after),
            _labels(("Label_1", "Work")))

    folders = await _provider().list_folders()

    assert [f.name for f in folders] == ["Work"]
    assert fake.calls("GET", "/labels") == 2
    assert len(fake.waits) == 1
    assert low <= fake.waits[0] <= high


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
async def test_a_403_with_a_rate_limit_reason_retries(
        fake: _Gmail, reason: str) -> None:
    """With no ``Retry-After``, the first back-off is 1 second and a part
    of a second at random."""
    fake.on("GET", "/labels", _refuse(403, reason), _labels(("Label_1", "Work")))

    folders = await _provider().list_folders()

    assert [f.name for f in folders] == ["Work"]
    assert fake.calls("GET", "/labels") == 2
    assert len(fake.waits) == 1
    assert 1.0 <= fake.waits[0] < 2.0


@pytest.mark.parametrize("answer", [
    _refuse(403, "insufficientPermissions"),
    _refuse(403, "dailyLimitExceeded"),
    _refuse(403, "domainPolicy"),
    _refuse(403),
    lambda _request: httpx.Response(403, text="<html>Forbidden</html>"),
], ids=["scope", "daily-limit", "policy", "no-reason", "not-json"])
async def test_a_plain_403_is_not_retried(fake: _Gmail, answer: Answer) -> None:
    """M8. A refusal that a second try cannot help goes back at once, as a
    plain HTTP error and not as a rate limit."""
    fake.on("GET", "/labels", answer, _labels(("Label_1", "Work")))

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _provider().list_folders()

    assert not isinstance(caught.value, GmailRateLimited)
    assert caught.value.response.status_code == 403
    assert fake.calls("GET", "/labels") == 1
    assert fake.waits == []


@pytest.mark.parametrize("code", [500, 503])
async def test_a_5xx_is_not_retried(fake: _Gmail, code: int) -> None:
    """Items 8 to 10 name the 429 and two reasons of a 403 only. A 5xx goes
    back to the caller, as it did before EM-G4a (§12.3.5.1)."""
    fake.on("GET", "/labels", _refuse(code, "backendError", retry_after="1"),
            _labels(("Label_1", "Work")))

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await _provider().list_folders()

    assert not isinstance(caught.value, GmailRateLimited)
    assert fake.calls("GET", "/labels") == 1
    assert fake.waits == []


# ── item 9: the bounds ──────────────────────────────────────────────────────


async def test_the_tries_are_bounded_and_end_in_a_typed_error(fake: _Gmail) -> None:
    """M9. Three tries, two waits, then ``GmailRateLimited``. Its message
    names the method and the path, and no query."""
    fake.on("GET", "/messages", _rate_limited("1"))

    with pytest.raises(GmailRateLimited) as caught:
        await _provider().list_messages(folder="INBOX", query="after:2026/01/01")

    assert GMAIL_MAX_TRIES == 3
    assert caught.value.tries == GMAIL_MAX_TRIES
    assert caught.value.response.status_code == 429
    assert fake.calls("GET", "/messages") == GMAIL_MAX_TRIES
    assert fake.waits == [1.0, 1.0]
    assert "after:" not in str(caught.value)


async def test_a_retry_after_past_the_cap_ends_the_tries(fake: _Gmail) -> None:
    """M17. A shorter wait than ``Retry-After`` asks gets one more refusal,
    so a wait past the cap ends the tries at once."""
    asked = GMAIL_MAX_WAIT_SECS + 1
    fake.on("GET", "/labels", _rate_limited(str(int(asked))),
            _labels(("Label_1", "Work")))

    with pytest.raises(GmailRateLimited) as caught:
        await _provider().list_folders()

    assert caught.value.tries == 1
    assert fake.calls("GET", "/labels") == 1
    assert fake.waits == []


async def test_the_total_wait_of_a_client_is_bounded(fake: _Gmail) -> None:
    """M10. The waits of one client, so of one sync cycle, add up to
    ``GMAIL_WAIT_BUDGET_SECS`` at most. Past the budget, a rate limit
    raises at the first refusal."""
    fake.on("GET", "/labels", _rate_limited("25"))
    p = _provider()

    for _ in range(4):
        with pytest.raises(GmailRateLimited):
            await p.list_folders()

    assert fake.waits == [25.0, 25.0]
    assert sum(fake.waits) <= GMAIL_WAIT_BUDGET_SECS == 60.0
    assert all(w <= GMAIL_MAX_WAIT_SECS == 30.0 for w in fake.waits)
    # Three tries for the first call, then one for each later call.
    assert fake.calls("GET", "/labels") == GMAIL_MAX_TRIES + 3
    client = await p._get_client()
    assert client.auth.waited == 50.0


# ── a send never goes out twice ─────────────────────────────────────────────


async def _send(p: GmailProvider) -> Any:
    return await p.send_message(to=["asha@example.org"], subject="s", body_text="b")


async def _create_draft(p: GmailProvider) -> Any:
    return await p.create_draft(to=["asha@example.org"], subject="s", body_text="b")


async def _send_draft(p: GmailProvider) -> Any:
    # EM-G3a item 7: send_draft takes the MESSAGE id of the draft, and finds
    # the draft id ``r-draft-1`` through ``drafts.list`` (E-A4).
    return await p.send_draft("m-draft-1")


@pytest.mark.parametrize("act, path", [
    (_send, "/messages/send"),
    (_create_draft, "/drafts"),
    (_send_draft, "/drafts/send"),
], ids=["messages.send", "drafts.create", "drafts.send"])
async def test_a_send_is_not_retried_after_the_request_was_sent(
        fake: _Gmail, act: Callable[[GmailProvider], Any], path: str) -> None:
    """M11. Each answer proves that the request reached Google. A second
    try could send the mail twice, so the 429 goes back to the caller."""
    fake.on("GET", "/drafts", _ok({"drafts": [
        {"id": "r-draft-1", "message": {"id": "m-draft-1"}}]}))
    fake.on("POST", path, _rate_limited("0"), _ok({"id": "m-sent"}))

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await act(_provider())

    assert not isinstance(caught.value, GmailRateLimited)
    assert caught.value.response.status_code == 429
    assert fake.calls("POST", path) == 1
    assert fake.waits == []


@pytest.mark.parametrize("act, path", [
    (lambda p: p.modify_message("m1", add_labels=["STARRED"]), "/messages/m1/modify"),
    (lambda p: p.trash_message("m1"), "/messages/m1/trash"),
    (lambda p: p.bulk_apply(["m1", "m2"], "read"), "/messages/batchModify"),
], ids=["modify", "trash", "batchModify"])
async def test_a_post_that_sets_a_state_retries(
        fake: _Gmail, act: Callable[[GmailProvider], Any], path: str) -> None:
    """A POST that sets a state changes nothing on a second try, so it gets
    the rule of item 8."""
    fake.on("POST", path, _rate_limited("0"), _ok({}))

    await act(_provider())

    assert fake.calls("POST", path) == 2
    assert fake.waits == [0.0]


# ── a 401 still refreshes once ──────────────────────────────────────────────


@pytest.mark.parametrize("order", ["429-then-401", "401-then-429"])
async def test_the_401_refresh_still_runs_once(fake: _Gmail, order: str) -> None:
    """Each try runs the flow of ``RefreshingBearer``. A rate limit and a
    401 in one call cost one refresh, in either order."""
    def _expire_then_refuse(_request: httpx.Request) -> httpx.Response:
        fake.valid.discard("at-1")
        return httpx.Response(429, headers={"Retry-After": "0"})

    if order == "429-then-401":
        fake.on("GET", "/labels", _expire_then_refuse, _labels(("Label_1", "Work")))
        expected = ["at-1", "at-1", "at-2"]
    else:
        fake.valid = set()
        fake.on("GET", "/labels", _rate_limited("0"), _labels(("Label_1", "Work")))
        expected = ["at-1", "at-2", "at-2"]
    p = _provider()

    folders = await p.list_folders()

    assert [f.name for f in folders] == ["Work"]
    assert fake.bearers("GET", "/labels") == expected
    assert fake.refreshes == 1
    assert fake.waits == [0.0]
    assert p.export_credentials()["access_token"] == "at-2"


# ── item 10: the record of a failed fetch ───────────────────────────────────


def _no_id(_request: httpx.Request) -> httpx.Response:
    """A body that fails the parse. It carries a subject and an address, and
    the record must name neither."""
    raw = _message("m2", subject="Secret plan", sender="Spy <spy@example.org>")
    del raw["id"]
    return httpx.Response(200, json=raw)


@pytest.mark.parametrize("answer, error, status", [
    (_refuse(500, "backendError"), "HTTPStatusError", "500"),
    (_refuse(404, "notFound"), "HTTPStatusError", "404"),
    (_no_id, "KeyError", "None"),
], ids=["500", "404", "parse"])
async def test_a_failed_fetch_is_recorded_and_the_other_messages_parse(
        fake: _Gmail, caplog: pytest.LogCaptureFixture,
        answer: Answer, error: str, status: str) -> None:
    """M12. The failed fetch leaves a record with the message id, the class
    of the error and the status. The record names no subject and no
    address. The messages before it and after it still parse."""
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/messages", _ok({"messages": [{"id": "m1"}, {"id": "m2"},
                                                  {"id": "m3"}]}))
    fake.on("GET", "/messages/m1", _ok(_message("m1")))
    fake.on("GET", "/messages/m2", answer)
    fake.on("GET", "/messages/m3", _ok(_message("m3")))
    p = _provider()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        messages, _ = await p.list_messages(folder="INBOX")

    assert [m.provider_message_id for m in messages] == ["m1", "m3"]
    assert p.fetch_failures == [
        f"gmail.fetch_failed id=m2 error={error} status={status}"]
    logged = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("gmail.fetch_failed")]
    assert logged == [
        f"gmail.fetch_failed message_id=m2 error={error} status={status}"]
    for text in [*p.fetch_failures, *logged]:
        assert "Secret" not in text and "@" not in text


async def test_a_failed_fetch_on_the_history_path_goes_into_errors(
        fake: _Gmail) -> None:
    """Item 10 on the history path: the record goes into
    ``SyncResult.errors``, and the other message still syncs."""
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/history", _ok({
        "historyId": "120",
        "history": [{"messagesAdded": [{"message": {"id": "m1"}},
                                       {"message": {"id": "m2"}}]}]}))
    fake.on("GET", "/messages/m1", _ok(_message("m1")))
    fake.on("GET", "/messages/m2", _refuse(500, "backendError"))

    result = await _provider().sync_messages(history_id="100")

    assert [m.provider_message_id for m in result.messages] == ["m1"]
    assert result.errors == [
        "gmail.fetch_failed id=m2 error=HTTPStatusError status=500"]


async def test_the_sweep_puts_the_fetch_records_of_its_call_into_errors(
        fake: _Gmail) -> None:
    """The sweep with no cursor and the deep sync return the records of
    their own call in ``SyncResult.errors``. A later call starts empty."""
    def _inbox_only(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("labelIds") == "INBOX":
            return httpx.Response(200, json={"messages": [{"id": "m1"},
                                                          {"id": "m2"}]})
        return httpx.Response(200, json={})

    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/messages", _inbox_only)
    fake.on("GET", "/messages/m1", _ok(_message("m1")))
    fake.on("GET", "/messages/m2", _refuse(500, "backendError"),
            _ok(_message("m2")))
    p = _provider()

    first = await p.sync_messages()
    second = await p.sync_messages(deep=True)

    record = "gmail.fetch_failed id=m2 error=HTTPStatusError status=500"
    assert [m.provider_message_id for m in first.messages] == ["m1"]
    assert first.errors == [record]
    assert sorted(m.provider_message_id for m in second.messages) == ["m1", "m2"]
    assert second.errors == []
    assert p.fetch_failures == [record]


async def test_a_rate_limited_fetch_fails_the_page(fake: _Gmail) -> None:
    """M13. A fetch whose tries are spent is no skipped fetch. It raises,
    so the sync fails and the next cycle reads the message."""
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/messages", _ok({"messages": [{"id": "m1"}, {"id": "m2"}]}))
    fake.on("GET", "/messages/m1", _ok(_message("m1")))
    fake.on("GET", "/messages/m2", _rate_limited("0"))
    p = _provider()

    with pytest.raises(GmailRateLimited):
        await p.list_messages(folder="INBOX")

    assert fake.calls("GET", "/messages/m2") == GMAIL_MAX_TRIES
    assert p.fetch_failures == []


def _rate_limit_one_label(label: str) -> Answer:
    """A list that Gmail refuses for *label* only."""
    refuse = _rate_limited("0")

    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("labelIds") == label:
            return refuse(request)
        return httpx.Response(200, json={})
    return _answer


def _sweep_user_label(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _labels(("Label_1", "Work")))
    fake.on("GET", "/messages", _rate_limit_one_label("Label_1"))
    return {}


def _sweep_system_label(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/messages", _rate_limit_one_label("SENT"))
    return {}


def _sweep_label_list(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _rate_limited("0"))
    fake.on("GET", "/messages", _ok({}))
    return {}


def _deep_user_label(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _labels(("Label_1", "Work")))
    fake.on("GET", "/messages", _rate_limit_one_label("Label_1"))
    return {"deep": True}


def _deep_system_label(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/messages", _rate_limit_one_label("TRASH"))
    return {"deep": True}


def _deep_label_list(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _rate_limited("0"))
    fake.on("GET", "/messages", _ok({}))
    return {"deep": True}


def _history_fetch(fake: _Gmail) -> dict[str, Any]:
    fake.on("GET", "/labels", _labels())
    fake.on("GET", "/history", _ok({
        "historyId": "120",
        "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]}))
    fake.on("GET", "/messages/m1", _rate_limited("0"))
    return {"history_id": "100"}


@pytest.mark.parametrize("arrange", [
    _sweep_user_label, _sweep_system_label, _sweep_label_list,
    _deep_user_label, _deep_system_label, _deep_label_list, _history_fetch,
], ids=lambda f: f.__name__.lstrip("_"))
async def test_a_rate_limit_past_the_tries_fails_the_sync(
        fake: _Gmail, arrange: Callable[[_Gmail], dict[str, Any]]) -> None:
    """M14 (item 9). Each loop of ``sync_messages`` skips a label that
    fails, but a rate limit must fail the sync. The scheduler then counts a
    failed sync, backs off, and phase (d) never moves the cursor."""
    kwargs = arrange(fake)

    with pytest.raises(GmailRateLimited):
        await _provider().sync_messages(**kwargs)
