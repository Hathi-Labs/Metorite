"""WS-17 EM-T4d — the Graph delta of Outlook, in shadow first.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, the part
"EM-T4d".

Delta stopped new mail once (2026-06-23), and nobody found the cause. So the
full sweep stays the one writer of each poll. In ``shadow`` a listed mailbox
ALSO reads the Graph delta of each swept folder, after the sweep. It compares
the NEW mail of the two reads and stores the links in ``last_history_id``.

Each test uses the real ``OutlookProvider``. Its client, the
``authenticate`` probe and the token refresh all reach ONE fake Graph on
``httpx.MockTransport``. No test calls real Graph.

R7 fences named here:

* ``email-delta-off-no-call``: with ``off``, no ``/messages/delta``
  request, and ``new_history_id=None``. An unknown value resolves to ``off``.
* ``email-delta-on-refused``: ``on`` resolves to ``shadow`` and logs
  ``email.delta_mode_refused``.
* ``email-delta-account-scope``: with ``shadow`` and an empty account list,
  no mailbox sends a delta. Only a listed account sends one.
* ``email-delta-links-whole``: a round of three pages stores the last
  ``@odata.deltaLink`` of each folder, byte for byte. The next poll calls it
  as it is, with ``Prefer: odata.maxpagesize=100`` and no ``$top``.
* ``email-delta-bad-cursor``: a bare token, text that is not JSON, JSON with
  another version, and NULL each give a full sweep, a seed round and no error.
* ``email-delta-sweep-only``: ``off`` and ``shadow`` give equal
  ``messages``, ``full_snapshot``, ``catch_up_incomplete`` and
  ``catch_up_folders``. An ``@removed`` item writes no TRASH row.
* ``email-delta-failure-isolated``: a raise, a 410 or a 500 leaves the sweep
  result unchanged, and the cycle succeeds. A 410 drops that link, and a 500
  keeps it.
* ``email-delta-new-mail-counts``: a new message that the fake delta leaves
  out gives ``sweep_only=1``. A message older than ``at`` adds no count. The
  record holds no subject, no address and no link.
* ``email-delta-folder-set``: a new user folder seeds on its first poll. A
  folder that the list no longer returns loses its link. A failed folder
  list keeps each link.
* ``email-delta-floor``: the first request of a seed round filters
  ``receivedDateTime ge <floor>``.
* ``email-delta-page-cap``: a folder at 20 pages stores its nextLink and
  continues at the next poll.

The R8 case (a JSON cursor lands in the row of org B only, and a later
``off`` cycle keeps it) lives in ``test_email_scheduler_tenancy.py``.

Run::

    uv run pytest tests/unit/test_outlook_delta_shadow.py -v -rs
"""
from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.settings import get_settings

# Imported here, before any test runs ``wire``: ``acb_llm`` imports a client
# that subclasses ``httpx.AsyncClient``, and ``wire`` replaces that class.
from acb_llm import key_store
from email_ingestion.providers.app_credentials import OAuthApp
from email_ingestion.providers.outlook import (
    _MESSAGE_SELECT,
    SWEEP_SYSTEM_FOLDERS,
    OutlookProvider,
    dump_delta_cursor,
    parse_delta_cursor,
)

_GRAPH = "https://graph.microsoft.com/v1.0"
_FMT = "%Y-%m-%dT%H:%M:%SZ"
#: The Graph path of each sweep key of a system folder.
_SYSTEM = {"inbox": "inbox", "sent": "sentitems", "drafts": "drafts",
           "archive": "archive", "junk": "junkemail", "trash": "deleteditems"}
_PATH_KEY = {v: k for k, v in _SYSTEM.items()}
_SENDER = "sender@mail.test"
_ACCOUNT = "acc-delta-1"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_FMT)


def _since(flt: str | None) -> datetime | None:
    """The ``ge`` bound of a ``$filter``, or None."""
    for part in (flt or "").split(" and "):
        words = part.split()
        if len(words) == 3 and words[1] == "ge":
            return datetime.strptime(words[2], _FMT).replace(tzinfo=UTC)
    return None


# ── a Graph mail API in memory ──────────────────────────────────────────────


class _Graph:
    """The folders of one mailbox, the sweep reads and the delta rounds.

    ``mail`` maps a Graph path (a well-known name or a folder id) to its
    messages, ``{id: receivedDateTime}``. A seed round returns each message
    above the ``$filter`` floor. Each round ends with a delta link that
    holds a snapshot, and a later call of that link returns the messages
    added since the snapshot and an ``@removed`` item for each one gone.
    ``page_size`` splits a round into pages joined by next links.

    ``hide`` names ids that the delta leaves out, as the defect of
    2026-06-23 did. ``fail`` maps a path to the answers of its next delta
    requests: a status, or ``"raise"`` for a transport error.
    ``on_round`` runs at the start of each delta round, so a test can add
    mail between the sweep and the delta.

    ``deltas`` holds ``(path, request)`` for each delta request, and
    ``issued`` the last delta link of each path."""

    def __init__(self, *, user: dict[str, str] | None = None,
                 page_size: int = 100, missing: tuple[str, ...] = ()) -> None:
        self.user = dict(user or {})
        self.mail: dict[str, dict[str, datetime]] = {
            p: {} for p in _SYSTEM.values() if p not in missing}
        for fid in self.user:
            self.mail[fid] = {}
        self.page_size = page_size
        self.hide: set[str] = set()
        self.fail: dict[str, list[int | str]] = {}
        self.list_fails = False
        self.on_round: Callable[[str], None] | None = None
        self.requests: list[httpx.Request] = []
        self.deltas: list[tuple[str, httpx.Request]] = []
        self.sweeps: list[tuple[str, httpx.Request]] = []
        self.issued: dict[str, str] = {}
        self._links: dict[str, tuple] = {}
        self._rounds: dict[int, tuple] = {}

    # The mailbox.

    def add(self, path: str, mid: str, at: datetime) -> None:
        self.mail[path][mid] = at

    def drop(self, path: str, mid: str) -> None:
        self.mail[path].pop(mid)

    def add_folder(self, fid: str, name: str) -> None:
        self.user[fid] = name
        self.mail[fid] = {}

    def remove_folder(self, fid: str) -> None:
        self.user.pop(fid)
        self.mail.pop(fid)

    def delta_requests(self, path: str | None = None) -> list[httpx.Request]:
        return [r for p, r in self.deltas if path is None or p == path]

    # The wire.

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self._links:
            return self._follow(request, url)
        path = request.url.path
        if path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me"})
        if path == "/v1.0/me/mailFolders":
            if self.list_fails:
                return httpx.Response(500, json={"error": {"code": "busy"}})
            return httpx.Response(200, json={"value": [
                {"id": f, "displayName": n, "childFolderCount": 0}
                for f, n in self.user.items()]})
        parts = path.split("/")
        if len(parts) in (6, 7) and parts[3] == "mailFolders" and parts[5] == "messages":
            folder = parts[4]
            is_delta = len(parts) == 7 and parts[6] == "delta"
            if is_delta:
                self.deltas.append((folder, request))
            if folder not in self.mail:
                return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
            if is_delta:
                return self._failure(folder, request) or self._start(
                    folder, _since(request.url.params.get("$filter")), {})
            return self._sweep(folder, request)
        return httpx.Response(404, json={"error": {"code": "NoRoute"}})

    def _message(self, mid: str, at: datetime) -> dict[str, Any]:
        return {"id": mid, "receivedDateTime": _iso(at),
                "subject": f"subject of {mid}", "conversationId": f"c-{mid}",
                "bodyPreview": "a preview",
                "from": {"emailAddress": {"name": "Sender", "address": _SENDER}}}

    def _sweep(self, folder: str, request: httpx.Request) -> httpx.Response:
        self.sweeps.append((folder, request))
        params = request.url.params
        floor = _since(params.get("$filter"))
        top = int(params.get("$top", "100"))
        rows = sorted(((m, t) for m, t in self.mail[folder].items()
                       if floor is None or t >= floor),
                      key=lambda row: row[1], reverse=True)[:top]
        return httpx.Response(200, json={
            "value": [self._message(m, t) for m, t in rows]})

    def _failure(self, folder: str, request: httpx.Request) -> httpx.Response | None:
        queue = self.fail.get(folder) or []
        if not queue:
            return None
        answer = queue.pop(0)
        if answer == "raise":
            raise httpx.ConnectError("graph unreachable", request=request)
        return httpx.Response(int(answer), json={"error": {"code": "SyncStateNotFound"}})

    def _follow(self, request: httpx.Request, url: str) -> httpx.Response:
        kind, folder, *rest = self._links[url]
        self.deltas.append((folder, request))
        failed = self._failure(folder, request)
        if failed is not None:
            return failed
        if kind == "next":
            return self._page(folder, *rest)
        floor, snapshot = rest
        return self._start(folder, floor, snapshot)

    def _start(self, folder: str, floor: datetime | None,
               base: dict[str, datetime]) -> httpx.Response:
        if self.on_round is not None:
            self.on_round(folder)
        current = {m: t for m, t in self.mail[folder].items()
                   if floor is None or t >= floor}
        added = sorted((t, m) for m, t in current.items()
                       if m not in base and m not in self.hide)
        items = [self._message(m, t) for t, m in added]
        items += [{"id": m, "@removed": {"reason": "deleted"}}
                  for m in sorted(base) if m not in current]
        pages = [items[i:i + self.page_size]
                 for i in range(0, len(items), self.page_size)] or [[]]
        rid = len(self._rounds) + 1
        self._rounds[rid] = (pages, floor, current)
        return self._page(folder, rid, 0)

    def _page(self, folder: str, rid: int, k: int) -> httpx.Response:
        pages, floor, snapshot = self._rounds[rid]
        body: dict[str, Any] = {"value": pages[k]}
        base = f"{_GRAPH}/me/mailFolders('{folder}')/messages/delta"
        if k + 1 < len(pages):
            link = f"{base}?$skiptoken=r{rid}p{k + 1}.Aa_-%3D"
            self._links[link] = ("next", folder, rid, k + 1)
            body["@odata.nextLink"] = link
        else:
            link = f"{base}?$deltatoken=d{rid}.Zz_-%3D%3D"
            self._links[link] = ("delta", folder, floor, dict(snapshot))
            body["@odata.deltaLink"] = link
            self.issued[folder] = link
        return httpx.Response(200, json=body)


@pytest.fixture()
def wire(monkeypatch):
    """Send each ``httpx.AsyncClient`` that a provider builds to *graph*."""
    real = httpx.AsyncClient

    def _wire(graph: _Graph) -> _Graph:
        transport = httpx.MockTransport(graph.handle)

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return graph

    return _wire


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Each test starts with the delta ``off``, no listed account, and a
    fresh cache of the mode, so a refusal logs again."""
    settings = get_settings()
    monkeypatch.setattr(settings, "email_outlook_delta", "off", raising=False)
    monkeypatch.setattr(settings, "email_outlook_delta_accounts", "", raising=False)
    monkeypatch.setattr(settings, "email_semantic_search_enabled", False,
                        raising=False)
    sched._resolve_delta_mode.cache_clear()
    sched._delta_accounts.cache_clear()
    sched._catch_up_misses.clear()
    sched._catch_up_abandoned.clear()
    yield
    sched._resolve_delta_mode.cache_clear()
    sched._delta_accounts.cache_clear()


def _set_mode(monkeypatch, mode: str, accounts: str = _ACCOUNT) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "email_outlook_delta", mode, raising=False)
    monkeypatch.setattr(settings, "email_outlook_delta_accounts", accounts,
                        raising=False)


def _provider() -> OutlookProvider:
    return OutlookProvider({"access_token": "at-1", "refresh_token": "r-1"},
                           app=OAuthApp(client_id="cid", client_secret="secret"))


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _floor() -> datetime:
    return _now() - timedelta(days=30)


def _mailbox(graph: _Graph, n: int = 3) -> None:
    """*n* messages of the last hours in the inbox, and one in Sent."""
    for i in range(n):
        graph.add("inbox", f"in-{i}", _now() - timedelta(hours=i + 1))
    graph.add("sentitems", "sent-0", _now() - timedelta(hours=2))


async def _poll(cursor: str | None, *, shadow: bool = True,
                floor: datetime | None = None, deep: bool = False):
    """One poll with a NEW provider, as each scheduler cycle builds one."""
    return await _provider().sync_messages(
        history_id=cursor, max_results=100, deep=deep,
        since=floor or _floor(), catch_up=None, delta_shadow=shadow)


def _sweep_fields(result) -> tuple:
    """The four sweep fields of a result, with each message as a tuple."""
    return (sorted((m.provider_message_id, m.folder, m.subject) for m in result.messages),
            result.full_snapshot, result.catch_up_incomplete,
            list(result.catch_up_folders))


def _report(result) -> dict[str, Any]:
    r = result.delta_report
    return {"folders": r.folders, "both": r.both, "sweep_only": r.sweep_only,
            "delta_only": r.delta_only, "seeding": r.seeding,
            "removed": r.removed, "failed": r.failed, "statuses": list(r.statuses)}


# ── the cursor ──────────────────────────────────────────────────────────────


def test_the_cursor_round_trips_and_refuses_every_other_shape() -> None:
    """``email-delta-bad-cursor``, the parse half (items 5 and 6)."""
    entry = {"link": f"{_GRAPH}/me/mailFolders('inbox')/messages/delta?$deltatoken=x",
             "at": "2026-10-04T10:00:00+00:00"}
    raw = dump_delta_cursor({"inbox": entry})
    assert json.loads(raw) == {"v": 1, "folders": {"inbox": entry}}
    assert parse_delta_cursor(raw) == {"inbox": entry}
    for bad in (None, "", "AAMkAGI2bare-token", f"{_GRAPH}/x?$deltatoken=abc",
                "{not json", "[1, 2]", "7", '"text"',
                json.dumps({"v": 2, "folders": {"inbox": entry}}),
                json.dumps({"v": True, "folders": {"inbox": entry}}),
                json.dumps({"v": "1", "folders": {"inbox": entry}}),
                json.dumps({"v": 1, "folders": []})):
        assert parse_delta_cursor(bad) == {}, bad
    # One bad entry drops that folder only. An ``at`` that is not text means
    # the round has not ended.
    mixed = json.dumps({"v": 1, "folders": {
        "inbox": entry, "sent": {"link": ""}, "drafts": "x",
        "junk": {"link": "https://l", "at": 5}}})
    assert parse_delta_cursor(mixed) == {
        "inbox": entry, "junk": {"link": "https://l", "at": None}}


# ── email-delta-off-no-call ─────────────────────────────────────────────────


async def test_off_sends_no_delta_request_and_returns_no_cursor(wire) -> None:
    """``email-delta-off-no-call``, the provider half. A stored cursor is
    never read, and nothing replaces it."""
    graph = wire(_Graph())
    _mailbox(graph)
    stored = dump_delta_cursor({"inbox": {
        "link": f"{_GRAPH}/me/mailFolders('inbox')/messages/delta?$deltatoken=old",
        "at": _iso(_now())}})
    res = await _poll(stored, shadow=False)
    assert graph.delta_requests() == []
    assert all("delta" not in str(r.url) for r in graph.requests)
    assert res.new_history_id is None
    assert res.delta_report is None
    assert len(res.messages) == 4 and res.full_snapshot is True


@pytest.mark.parametrize("value", ["off", "", "bogus", "shadowy", "enforce"])
def test_an_unknown_value_resolves_to_off(monkeypatch, value) -> None:
    """``email-delta-off-no-call``: only ``shadow`` (and the refused ``on``)
    runs a delta. Every other value is ``off``, also for a listed mailbox."""
    _set_mode(monkeypatch, value)
    assert sched.outlook_delta_mode(_ACCOUNT) == "off"


# ── email-delta-on-refused ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["on", "ON", " On "])
def test_on_resolves_to_shadow_and_logs_the_refusal(monkeypatch, caplog, value) -> None:
    """``email-delta-on-refused``. EM-T4d has no ``on`` path: the provider
    takes a boolean, so ``on`` cannot even reach it."""
    _set_mode(monkeypatch, value)
    with caplog.at_level("WARNING", logger=sched.logger.name):
        assert sched.outlook_delta_mode(_ACCOUNT) == "shadow"
    refused = [r.getMessage() for r in caplog.records
               if "email.delta_mode_refused" in r.getMessage()]
    assert refused == ["email.delta_mode_refused mode=on resolved=shadow"]


def test_shadow_resolves_to_shadow_for_a_listed_account_only(monkeypatch) -> None:
    """``email-delta-account-scope``, the resolver half. The list compares
    ids in lower case, and an empty list names nobody."""
    _set_mode(monkeypatch, "shadow", accounts="")
    assert sched.outlook_delta_mode(_ACCOUNT) == "off"
    sched._delta_accounts.cache_clear()
    _set_mode(monkeypatch, "shadow", accounts=f" other , {_ACCOUNT.upper()} ")
    assert sched.outlook_delta_mode(_ACCOUNT) == "shadow"
    assert sched.outlook_delta_mode("acc-not-listed") == "off"


# ── the scheduler, with no database ─────────────────────────────────────────


class _Res:
    def __init__(self, row: Any) -> None:
        self._row = row
        self.rowcount = 0

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list:
        return []

    def scalar(self) -> Any:
        return None


class _Db:
    def __init__(self, row: Any, log: list) -> None:
        self.row, self.log = row, log

    async def execute(self, stmt: Any, params: Any = None, *_a: Any, **_k: Any) -> _Res:
        self.log.append((" ".join(str(stmt).split()), dict(params or {})))
        return _Res(self.row)


class _Store:
    def decrypt(self, raw: str) -> str:
        return json.dumps({"access_token": "at-1", "refresh_token": "r-1"})

    def encrypt(self, raw: str) -> str:
        return f"enc:{raw}"


def _row(cursor: str | None) -> SimpleNamespace:
    """The account row of phase (a): an Outlook mailbox that has imported."""
    return SimpleNamespace(
        id="log-1", provider="microsoft", credentials_encrypted="x",
        last_history_id=cursor, sync_interval_secs=300, initial_sync_done=True,
        import_since=None, import_reached_at=None, import_count=0,
        last_synced_at=None, created_at=None, db_now=datetime.now(UTC),
        categories=[], user_id="o@x.test")


async def _cycle(monkeypatch, graph: _Graph, cursor: str | None, *,
                 account_id: str = _ACCOUNT) -> SimpleNamespace:
    """One ``_sync_account`` cycle on fake sessions and the real provider.

    It returns the result, the cursor that phase (d) writes, the messages
    that phase (c) upserts, every statement, and the open blocks at each
    delta request."""
    log: list = []
    upserted: list = []
    state = {"open": 0}
    open_at_delta: list[int] = []
    row = _row(cursor)

    @asynccontextmanager
    async def _ts(org=None):
        state["open"] += 1
        try:
            yield _Db(row, log)
        finally:
            state["open"] -= 1

    async def _upsert(db, aid, msg):
        upserted.append(msg)

    async def _no_hook(*_a, **_k):
        return None

    real_handle = graph.handle

    async def _watched(request: httpx.Request) -> httpx.Response:
        # The open blocks at the moment of each delta request.
        before = len(graph.deltas)
        try:
            return await real_handle(request)
        finally:
            if len(graph.deltas) > before:
                open_at_delta.append(state["open"])

    graph.handle = _watched  # type: ignore[method-assign]
    wire_transport = httpx.MockTransport(_watched)
    real_client = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = wire_transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    monkeypatch.setattr(sched, "tenant_session", _ts)
    monkeypatch.setattr(sched, "upsert_message", _upsert)
    monkeypatch.setattr(sched, "run_label_learn_hook", _no_hook)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "build_provider", lambda name, creds: _provider())
    try:
        res = await sched._sync_account(account_id, organization_id="org-1")
    finally:
        graph.handle = real_handle  # type: ignore[method-assign]
        monkeypatch.setattr(httpx, "AsyncClient", real_client)
    written = [p["history_id"] for sql, p in log if "last_history_id = COALESCE(" in sql]
    return SimpleNamespace(result=res, cursor=written[0] if written else "unset",
                           upserted=upserted, log=log, open_at_delta=open_at_delta)


async def test_the_scheduler_with_off_sends_no_delta_and_keeps_the_cursor(
    monkeypatch,
) -> None:
    """``email-delta-off-no-call`` through ``_sync_account``. A listed
    mailbox with the mode ``off`` sends no delta request, and phase (d)
    hands ``None`` to its ``COALESCE``, so the stored cursor stays."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "off")
    run = await _cycle(monkeypatch, graph, '{"v": 1, "folders": {}}')
    assert "error" not in run.result, run.result
    assert graph.delta_requests() == []
    assert run.cursor is None
    assert len(run.upserted) == 4


async def test_an_empty_account_list_sends_no_delta_from_any_mailbox(
    monkeypatch,
) -> None:
    """``email-delta-account-scope``: ``shadow`` with an empty list runs no
    delta. Only the listed mailbox sends one."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow", accounts="")
    run = await _cycle(monkeypatch, graph, None)
    assert "error" not in run.result and graph.delta_requests() == []
    assert run.cursor is None

    sched._delta_accounts.cache_clear()
    _set_mode(monkeypatch, "shadow", accounts="acc-delta-2")
    run = await _cycle(monkeypatch, graph, None, account_id=_ACCOUNT)
    assert graph.delta_requests() == [], "a mailbox that the list does not name ran"
    run = await _cycle(monkeypatch, graph, None, account_id="acc-delta-2")
    assert "error" not in run.result, run.result
    assert {p for p, _ in graph.deltas} == set(_SYSTEM.values())
    assert parse_delta_cursor(run.cursor).keys() == set(SWEEP_SYSTEM_FOLDERS)


async def test_the_delta_runs_with_no_session_open(monkeypatch) -> None:
    """R5 (EM-T4a-1). The delta runs inside ``sync_messages``, in phase
    (b), so no ``tenant_session`` block is open during any delta request."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    run = await _cycle(monkeypatch, graph, None)
    assert "error" not in run.result, run.result
    assert len(run.open_at_delta) == len(SWEEP_SYSTEM_FOLDERS)
    assert set(run.open_at_delta) == {0}, "a session was open during a delta request"


# ── email-delta-links-whole ─────────────────────────────────────────────────


async def test_a_round_of_three_pages_stores_each_delta_link_whole(wire) -> None:
    """``email-delta-links-whole``. The inbox round takes three pages. The
    cursor holds the last delta link of each folder, byte for byte, and
    the next poll calls it as it is: the ``Prefer`` page size, no ``$top``
    and no ``IdType`` preference."""
    graph = wire(_Graph(page_size=2))
    _mailbox(graph, n=5)
    first = await _poll(None)
    inbox = graph.delta_requests("inbox")
    assert len(inbox) == 3, "the round did not follow the next links"
    cursor = parse_delta_cursor(first.new_history_id)
    assert set(cursor) == set(SWEEP_SYSTEM_FOLDERS)
    for key, path in _SYSTEM.items():
        assert cursor[key]["link"] == graph.issued[path], key
        assert cursor[key]["at"] is not None
    assert "$deltatoken=" in cursor["inbox"]["link"]

    before = len(graph.deltas)
    second = await _poll(first.new_history_id)
    calls = graph.deltas[before:]
    assert [str(r.url) for p, r in calls if p == "inbox"][:1] == [cursor["inbox"]["link"]]
    for path, request in graph.deltas:
        assert request.headers.get("prefer") == "odata.maxpagesize=100", path
        assert "$top" not in request.url.params, path
        assert "idtype" not in request.headers.get("prefer", "").lower()
    assert second.delta_report.failed == 0


async def test_the_delta_sends_the_select_of_the_sweep(wire) -> None:
    """Item 4: the two reads ask for the same properties, so their ids
    compare."""
    graph = wire(_Graph())
    _mailbox(graph)
    await _poll(None)
    sweep_select = {r.url.params.get("$select") for _, r in graph.sweeps}
    delta_select = {r.url.params.get("$select") for _, r in graph.deltas}
    assert sweep_select == delta_select == {_MESSAGE_SELECT}


# ── email-delta-floor ───────────────────────────────────────────────────────


async def test_a_seed_round_filters_on_the_floor(wire) -> None:
    """``email-delta-floor``. The first request of each seed round sends
    ``receivedDateTime ge <floor>``. A message below the floor never
    reaches the delta."""
    graph = wire(_Graph())
    _mailbox(graph)
    floor = _now() - timedelta(days=10)
    graph.add("inbox", "too-old", floor - timedelta(days=1))
    res = await _poll(None, floor=floor)
    for path, request in graph.deltas:
        assert request.url.params.get("$filter") == f"receivedDateTime ge {_iso(floor)}", path
    assert res.delta_report.seeding == len(SWEEP_SYSTEM_FOLDERS)
    assert "too-old" not in {m.provider_message_id for m in res.messages}


# ── email-delta-bad-cursor ──────────────────────────────────────────────────


@pytest.mark.parametrize("cursor", [
    None,
    "AAMkAGI2THVSAAA=",
    f"{_GRAPH}/me/mailFolders/inbox/messages/delta?$deltatoken=abc",
    "{not json",
    json.dumps({"v": 2, "folders": {"inbox": {"link": "https://graph.test/x", "at": None}}}),
], ids=["null", "bare-token", "bare-link", "not-json", "version-2"])
async def test_a_bad_cursor_gives_a_full_sweep_and_a_seed_round(wire, cursor) -> None:
    """``email-delta-bad-cursor``. The sweep result equals the result of
    ``off``. Every folder seeds through the delta path, and the poll
    returns a valid cursor of version 1."""
    graph = wire(_Graph())
    _mailbox(graph)
    off = await _poll(cursor, shadow=False)
    shadow = await _poll(cursor)
    assert _sweep_fields(shadow) == _sweep_fields(off)
    report = _report(shadow)
    assert report["seeding"] == report["folders"] == len(SWEEP_SYSTEM_FOLDERS)
    assert report["failed"] == 0
    for path, request in graph.deltas:
        assert request.url.path == f"/v1.0/me/mailFolders/{path}/messages/delta"
    assert set(parse_delta_cursor(shadow.new_history_id)) == set(SWEEP_SYSTEM_FOLDERS)


# ── email-delta-sweep-only ──────────────────────────────────────────────────


async def test_shadow_writes_exactly_what_off_writes(wire) -> None:
    """``email-delta-sweep-only``, the provider half. With a stored cursor,
    a new message and a gone message, the four sweep fields of ``shadow``
    equal those of ``off``. The ``@removed`` item becomes no ``[DELETED]``
    marker."""
    graph = wire(_Graph())
    _mailbox(graph)
    seeded = await _poll(None)
    graph.add("inbox", "new-1", _now() + timedelta(minutes=1))
    graph.drop("inbox", "in-0")
    off = await _poll(seeded.new_history_id, shadow=False)
    shadow = await _poll(seeded.new_history_id)
    assert _sweep_fields(shadow) == _sweep_fields(off)
    assert shadow.delta_report.removed == 1
    assert all(m.subject != "[DELETED]" for m in shadow.messages)
    assert all(m.folder != "TRASH" for m in shadow.messages)


async def test_a_removed_item_writes_no_trash_row(monkeypatch) -> None:
    """``email-delta-sweep-only``, the scheduler half: the cycle in
    ``shadow`` upserts what the cycle in ``off`` upserts, and runs no
    ``SET folder = 'TRASH'`` statement for the ``@removed`` item."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    seeded = await _cycle(monkeypatch, graph, None)
    graph.drop("inbox", "in-0")
    graph.add("inbox", "new-1", _now() + timedelta(minutes=1))

    _set_mode(monkeypatch, "off")
    sched._resolve_delta_mode.cache_clear()
    off = await _cycle(monkeypatch, graph, seeded.cursor)
    _set_mode(monkeypatch, "shadow")
    sched._resolve_delta_mode.cache_clear()
    shadow = await _cycle(monkeypatch, graph, seeded.cursor)
    assert "error" not in shadow.result, shadow.result

    def _ids(run):
        return sorted((m.provider_message_id, m.folder) for m in run.upserted)

    assert _ids(shadow) == _ids(off)
    assert not [sql for sql, _ in shadow.log if "SET folder = 'TRASH'" in sql]
    assert parse_delta_cursor(shadow.cursor)["inbox"]["link"] == graph.issued["inbox"]


# ── email-delta-failure-isolated ────────────────────────────────────────────


@pytest.mark.parametrize(("answer", "status", "keeps"), [
    ("raise", "ConnectError", True),
    (500, "500", True),
    (410, "410", False),
], ids=["raise", "500", "410"])
async def test_a_failed_delta_leaves_the_sweep_unchanged(
    wire, answer, status, keeps,
) -> None:
    """``email-delta-failure-isolated``. The inbox link fails. The sweep
    fields equal those of ``off``. A 410 drops the inbox link, and a raise
    or a 500 keeps the stored entry as it was. The other folders go on."""
    graph = wire(_Graph())
    _mailbox(graph)
    seeded = await _poll(None)
    stored = parse_delta_cursor(seeded.new_history_id)
    graph.add("inbox", "new-1", _now() + timedelta(minutes=1))
    off = await _poll(seeded.new_history_id, shadow=False)
    graph.fail["inbox"] = [answer]
    shadow = await _poll(seeded.new_history_id)
    assert _sweep_fields(shadow) == _sweep_fields(off)
    report = _report(shadow)
    assert (report["failed"], report["statuses"]) == (1, [status])
    after = parse_delta_cursor(shadow.new_history_id)
    if keeps:
        assert after["inbox"] == stored["inbox"]
    else:
        assert "inbox" not in after
    assert after["sent"]["link"] == graph.issued["sentitems"]
    assert after["sent"] != stored["sent"], "the other folders did not go on"


async def test_a_failed_delta_keeps_the_cycle_a_success(monkeypatch, caplog) -> None:
    """``email-delta-failure-isolated`` through ``_sync_account``: the
    cycle succeeds, writes the sweep, and logs
    ``email.delta_shadow_failed`` with the folder count and the status."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    seeded = await _cycle(monkeypatch, graph, None)
    graph.fail["inbox"] = [500]
    graph.fail["sentitems"] = ["raise"]
    with caplog.at_level("INFO", logger=sched.logger.name):
        run = await _cycle(monkeypatch, graph, seeded.cursor)
    assert "error" not in run.result, run.result
    assert len(run.upserted) == 4
    failed = [r.getMessage() for r in caplog.records
              if "email.delta_shadow_failed" in r.getMessage()]
    assert failed == [f"email.delta_shadow_failed account={_ACCOUNT} folders=2 "
                      "status=500,ConnectError"]


async def test_a_mailbox_with_no_archive_logs_no_failure(wire) -> None:
    """A 404 on Archive skips the folder by the one rule of the sweep
    (``_skips_folder``). It adds no count, no link and no failure. A 404 on
    the inbox is a failure."""
    graph = wire(_Graph(missing=("archive",)))
    _mailbox(graph)
    res = await _poll(None)
    report = _report(res)
    assert report["failed"] == 0
    assert report["folders"] == len(SWEEP_SYSTEM_FOLDERS) - 1
    assert "archive" not in parse_delta_cursor(res.new_history_id)


# ── email-delta-new-mail-counts ─────────────────────────────────────────────


async def test_the_record_counts_new_mail_only(wire) -> None:
    """``email-delta-new-mail-counts``. After the seed round:

    * a new message that the delta leaves out is ``sweep_only``;
    * a new message that both reads see is ``both``;
    * a message that arrives between the sweep and the delta is
      ``delta_only``, so the order of the two reads can never invent a
      ``sweep_only``;
    * a message older than ``at`` that moves into the folder adds no count.
    """
    graph = wire(_Graph())
    _mailbox(graph)
    seeded = await _poll(None)
    assert _report(seeded)["seeding"] == len(SWEEP_SYSTEM_FOLDERS)
    assert _report(seeded)["both"] == _report(seeded)["sweep_only"] == 0

    soon = _now() + timedelta(minutes=1)
    graph.add("inbox", "missed", soon)
    graph.hide.add("missed")
    graph.add("inbox", "seen", soon)
    graph.add("inbox", "moved-in-old", _now() - timedelta(days=2))

    def _late(folder: str) -> None:
        if folder == "inbox" and "late" not in graph.mail["inbox"]:
            graph.add("inbox", "late", soon + timedelta(seconds=30))

    graph.on_round = _late
    res = await _poll(seeded.new_history_id)
    report = _report(res)
    assert (report["both"], report["sweep_only"], report["delta_only"]) == (1, 1, 1)
    assert report["seeding"] == 0 and report["failed"] == 0
    assert report["folders"] == len(SWEEP_SYSTEM_FOLDERS)


async def test_the_record_holds_no_subject_no_address_and_no_link(
    monkeypatch, caplog,
) -> None:
    """``email-delta-new-mail-counts``, the log half: one
    ``email.delta_shadow`` line for each poll, with counts only."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    seeded = await _cycle(monkeypatch, graph, None)
    graph.add("inbox", "missed", _now() + timedelta(minutes=1))
    graph.hide.add("missed")
    with caplog.at_level("INFO", logger=sched.logger.name):
        await _cycle(monkeypatch, graph, seeded.cursor)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("email.delta_shadow ")]
    assert lines == [
        f"email.delta_shadow account={_ACCOUNT} folders=6 both=0 sweep_only=1 "
        "delta_only=0 seeding=0 removed=0"]
    for text in lines:
        for leak in ("subject", _SENDER, "http", "deltatoken", "missed"):
            assert leak not in text.lower(), leak


# ── email-delta-folder-set ──────────────────────────────────────────────────


async def test_the_folder_set_follows_the_sweep(wire) -> None:
    """``email-delta-folder-set``: a new user folder seeds on its first
    poll, a folder that the list no longer returns loses its link, and a
    failed folder list keeps each link."""
    graph = wire(_Graph(user={"uf-a": "Projects"}))
    _mailbox(graph)
    graph.add("uf-a", "a-1", _now() - timedelta(hours=1))
    first = await _poll(None)
    assert set(parse_delta_cursor(first.new_history_id)) == {
        *SWEEP_SYSTEM_FOLDERS, "uf-a"}

    graph.add_folder("uf-b", "Receipts")
    before = len(graph.deltas)
    second = await _poll(first.new_history_id)
    new = [(p, r) for p, r in graph.deltas[before:] if p == "uf-b"]
    assert len(new) == 1
    assert new[0][1].url.path == "/v1.0/me/mailFolders/uf-b/messages/delta"
    assert _report(second)["seeding"] == 1, "only the new folder seeds"
    assert "uf-b" in parse_delta_cursor(second.new_history_id)

    graph.remove_folder("uf-a")
    third = await _poll(second.new_history_id)
    cursor = parse_delta_cursor(third.new_history_id)
    assert "uf-a" not in cursor and "uf-b" in cursor

    graph.list_fails = True
    before = len(graph.deltas)
    fourth = await _poll(third.new_history_id)
    kept = parse_delta_cursor(fourth.new_history_id)
    assert kept["uf-b"] == cursor["uf-b"], "a failed folder list dropped a link"
    assert [p for p, _ in graph.deltas[before:]] == list(_SYSTEM.values())


# ── email-delta-page-cap ────────────────────────────────────────────────────


async def test_a_folder_at_the_page_cap_goes_on_at_the_next_poll(wire) -> None:
    """``email-delta-page-cap``. 25 inbox messages in pages of 1: the first
    poll reads 20 pages and stores the next link with no ``at``. The second
    poll calls that link as it is, reads 5 more pages and ends the round.
    The folder seeds until a round ends, and counts on the third poll."""
    assert OutlookProvider.DELTA_MAX_PAGES == 20
    graph = wire(_Graph(page_size=1))
    for i in range(25):
        graph.add("inbox", f"in-{i:02d}", _now() - timedelta(hours=i + 1))
    first = await _poll(None)
    assert len(graph.delta_requests("inbox")) == 20
    entry = parse_delta_cursor(first.new_history_id)["inbox"]
    assert entry["at"] is None and "$skiptoken=" in entry["link"]

    before = len(graph.deltas)
    second = await _poll(first.new_history_id)
    inbox = [r for p, r in graph.deltas[before:] if p == "inbox"]
    assert str(inbox[0].url) == entry["link"]
    assert len(inbox) == 5
    ended = parse_delta_cursor(second.new_history_id)["inbox"]
    assert ended["link"] == graph.issued["inbox"] and ended["at"] is not None
    assert _report(second)["seeding"] == 1, "the inbox counted before its round ended"

    graph.add("inbox", "new-1", _now() + timedelta(minutes=1))
    third = await _poll(second.new_history_id)
    assert _report(third)["seeding"] == 0
    assert _report(third)["both"] == 1


# ── the deep sync ───────────────────────────────────────────────────────────


async def test_a_deep_sync_runs_no_delta(wire) -> None:
    """Non-goal: no change to the deep sync. A deep call with
    ``delta_shadow`` sends no delta request."""
    graph = wire(_Graph())
    _mailbox(graph)
    res = await _poll(None, deep=True)
    assert graph.delta_requests() == []
    assert res.new_history_id is None and res.delta_report is None
