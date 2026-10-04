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
  result unchanged, and the cycle succeeds. A 410 or a 400 drops that link.
  Any other failure keeps it: a 429, a 5xx, a raise, a 403 or 404 on the
  inbox, and a refused refresh. A 403 or 404 on a user folder skips it. A
  defect outside the guard of each folder leaves the sweep unchanged and
  logs once (review round 1, F3 and F4).
* ``email-delta-link-host``: a stored link, a next link and a delta link
  that do not start with the Graph base URL get no request and no store.
  The link drops, and the log names the folder and no URL (review round 1,
  F1).
* ``email-delta-sync-log``: a shadow cycle writes NULL into the sync log
  row, and the cursor goes into ``last_history_id`` only. A provider with
  its own cursor still writes it (review round 1, F5).
* ``email-delta-normal-cycle``: only a normal incremental cycle sends a
  delta request. A first import and a deep sync send none (review round 1,
  F6).
* ``email-delta-new-mail-counts``: a new message that the fake delta leaves
  out gives ``sweep_only=1``. A message older than ``at`` adds no count. The
  record holds no subject, no address and no link.
* ``email-delta-folder-set``: a new user folder seeds on its first poll. A
  folder that the list no longer returns loses its link. A failed folder
  list keeps each link. A failed ``childFolders`` read keeps the link of
  the nested folder (review round 1, F2).
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
from email_ingestion.providers import outlook
from email_ingestion.providers.app_credentials import MICROSOFT_OAUTH_BASE, OAuthApp
from email_ingestion.providers.base import SyncResult
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
    ``issued`` the last delta link of each path.

    ``children`` maps a user folder to its nested folders, and the list
    reads them through ``childFolders``. A parent in ``child_fail`` answers
    503 to that read. ``token_status`` is the answer of the token endpoint
    to a refresh, and ``None`` gives the 404 of an unknown route."""

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
        self.children: dict[str, dict[str, str]] = {}
        self.child_fail: set[str] = set()
        self.token_status: int | None = None
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

    def add_folder(self, fid: str, name: str, *, parent: str | None = None) -> None:
        if parent is None:
            self.user[fid] = name
        else:
            self.children.setdefault(parent, {})[fid] = name
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
        if url.startswith(f"{MICROSOFT_OAUTH_BASE}/token") and self.token_status:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        path = request.url.path
        if path == "/v1.0/me":
            return httpx.Response(200, json={"id": "me"})
        if path == "/v1.0/me/mailFolders":
            if self.list_fails:
                return httpx.Response(500, json={"error": {"code": "busy"}})
            return httpx.Response(200, json={"value": self._folders(self.user)})
        parts = path.split("/")
        if len(parts) == 6 and parts[3] == "mailFolders" and parts[5] == "childFolders":
            if parts[4] in self.child_fail:
                return httpx.Response(503, json={"error": {"code": "busy"}})
            return httpx.Response(200, json={
                "value": self._folders(self.children.get(parts[4], {}))})
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

    def _folders(self, folders: dict[str, str]) -> list[dict[str, Any]]:
        return [{"id": f, "displayName": n,
                 "childFolderCount": len(self.children.get(f, {}))}
                for f, n in folders.items()]

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


def _row(cursor: str | None, *, initial_sync_done: bool = True) -> SimpleNamespace:
    """The account row of phase (a): an Outlook mailbox that has imported,
    unless ``initial_sync_done`` is False."""
    return SimpleNamespace(
        id="log-1", provider="microsoft", credentials_encrypted="x",
        last_history_id=cursor, sync_interval_secs=300,
        initial_sync_done=initial_sync_done,
        import_since=None, import_reached_at=None, import_count=0,
        last_synced_at=None, created_at=None, db_now=datetime.now(UTC),
        categories=[], user_id="o@x.test")


async def _cycle(monkeypatch, graph: _Graph, cursor: str | None, *,
                 account_id: str = _ACCOUNT, deep: bool | None = None,
                 since: datetime | None = None,
                 initial_sync_done: bool = True,
                 provider: Callable[[], Any] | None = None,
                 provider_name: str = "microsoft") -> SimpleNamespace:
    """One ``_sync_account`` cycle on fake sessions and the real provider.

    It returns the result, the cursor that phase (d) writes, the value that
    phase (d) writes into the sync log, the messages that phase (c)
    upserts, every statement, and the open blocks at each delta request.
    ``deep`` and ``since`` go to ``_sync_account`` as a member act passes
    them."""
    log: list = []
    upserted: list = []
    state = {"open": 0}
    open_at_delta: list[int] = []
    row = _row(cursor, initial_sync_done=initial_sync_done)
    row.provider = provider_name

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
    monkeypatch.setattr(sched, "build_provider",
                        lambda name, creds: (provider or _provider)())
    try:
        res = await sched._sync_account(account_id, organization_id="org-1",
                                        deep=deep, since=since)
    finally:
        graph.handle = real_handle  # type: ignore[method-assign]
        monkeypatch.setattr(httpx, "AsyncClient", real_client)
    written = [p["history_id"] for sql, p in log if "last_history_id = COALESCE(" in sql]
    logged = [p["history_id"] for sql, p in log
              if sql.startswith("UPDATE email_sync_log SET status = 'success'")]
    return SimpleNamespace(result=res, cursor=written[0] if written else "unset",
                           sync_log=logged[0] if logged else "unset",
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


# ── email-delta-sync-log ────────────────────────────────────────────────────


async def test_a_shadow_cycle_writes_no_cursor_into_the_sync_log(monkeypatch) -> None:
    """``email-delta-sync-log``, review round 1 F5. A shadow cycle writes its
    cursor into ``last_history_id`` and NULL into the sync log row, as
    ``off`` does for Outlook. A row for each poll would hold 3 to 17 KB that
    nothing reads. A later ``off`` cycle writes NULL too."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    run = await _cycle(monkeypatch, graph, None)
    assert "error" not in run.result, run.result
    assert set(parse_delta_cursor(run.cursor)) == set(SWEEP_SYSTEM_FOLDERS)
    assert run.sync_log is None, "a shadow cycle wrote its cursor into the sync log"

    _set_mode(monkeypatch, "off")
    off = await _cycle(monkeypatch, graph, run.cursor)
    assert (off.cursor, off.sync_log) == (None, None)


class _CursorProvider:
    """A provider with a cursor of its own, as Gmail and IMAP have."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict[str, Any]:
        return {}

    async def sync_messages(self, **kwargs: Any) -> SyncResult:
        self.calls.append(kwargs)
        return SyncResult(messages_synced=0, messages=[], new_history_id="h-77")


async def test_off_still_writes_the_cursor_of_a_provider_into_the_sync_log(
    monkeypatch,
) -> None:
    """``email-delta-sync-log``: F5 changes the shadow cycle only. A Gmail
    cycle still writes its own cursor into the sync log, with the mode
    ``shadow`` too, because only an Outlook mailbox runs the delta."""
    graph = _Graph()
    made: list[_CursorProvider] = []

    def _make() -> _CursorProvider:
        made.append(_CursorProvider())
        return made[-1]

    for mode in ("off", "shadow"):
        _set_mode(monkeypatch, mode)
        sched._resolve_delta_mode.cache_clear()
        made.clear()
        run = await _cycle(monkeypatch, graph, None, provider=_make,
                           provider_name="gmail")
        assert "error" not in run.result, run.result
        assert (run.cursor, run.sync_log) == ("h-77", "h-77"), mode
        assert made[0].calls[0]["delta_shadow"] is False, mode


# ── email-delta-normal-cycle ────────────────────────────────────────────────


@pytest.mark.parametrize(("deep", "since", "done", "imports", "runs"), [
    (None, None, True, False, True),
    (None, None, False, True, False),
    (True, "floor", True, True, False),
    (True, None, True, True, False),
    (False, None, False, False, False),
], ids=["normal-cycle", "first-import", "member-deep-sync", "manual-full-sync",
        "rerun-during-import"])
async def test_only_a_normal_cycle_sends_a_delta_request(
    monkeypatch, deep, since, done, imports, runs,
) -> None:
    """``email-delta-normal-cycle``, review round 1 F6. A listed mailbox in
    ``shadow`` runs the delta on a normal incremental cycle only. A first
    import, the deep sync of a member act (with or without ``since``) and a
    rerun before the import ends send no delta request. Those cycles hold
    the mailbox lock for a long sweep, and a manual sync has 30 seconds.
    The sweep runs in each of them."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    imported: list[bool] = []

    async def _import(org, account_id, provider, row, *, floor, progress):
        imported.append(progress)
        return 0, None

    monkeypatch.setattr(sched, "_import_in_batches", _import)
    run = await _cycle(monkeypatch, graph, None, deep=deep,
                       since=_floor() if since else None, initial_sync_done=done)
    assert "error" not in run.result, run.result
    assert bool(imported) is imports
    assert graph.sweeps, "the sweep did not run"
    if runs:
        assert len(graph.delta_requests()) == len(SWEEP_SYSTEM_FOLDERS)
        assert set(parse_delta_cursor(run.cursor)) == set(SWEEP_SYSTEM_FOLDERS)
    else:
        assert graph.delta_requests() == [], "a delta ran outside a normal cycle"
        assert run.cursor is None


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
    (503, "503", True),
    (429, "429", True),
    (403, "403", True),
    (404, "404", True),
    (410, "410", False),
    (400, "400", False),
], ids=["raise", "500", "503", "429", "403-inbox", "404-inbox", "410", "400"])
async def test_a_failed_delta_leaves_the_sweep_unchanged(
    wire, answer, status, keeps,
) -> None:
    """``email-delta-failure-isolated``. The inbox link fails. The sweep
    fields equal those of ``off``. The other folders go on.

    * A 410 drops the inbox link. A 400 drops it too (review round 1 F3): a
      stored link is a fixed request, so the 400 comes back at each poll.
    * Any other failure keeps the stored entry as it was (review round 1
      F4c): a raise, a 5xx, a 429, and a 403 or a 404 on the inbox. Each
      mailbox has an inbox, so that 403 or 404 is a failure, not a skip."""
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


async def test_a_refused_refresh_on_a_stored_link_keeps_the_link(wire) -> None:
    """``email-delta-failure-isolated``, review round 1 F3. The inbox link
    gets a 401, and the token endpoint refuses the refresh with a 400
    (EM-T4c). That 400 is a fault of the token, not of the link, so the
    stored inbox entry stays."""
    graph = wire(_Graph())
    _mailbox(graph)
    seeded = await _poll(None)
    stored = parse_delta_cursor(seeded.new_history_id)
    graph.fail["inbox"] = [401]
    graph.token_status = 400
    res = await _poll(seeded.new_history_id)
    assert _report(res)["failed"] == 1
    assert parse_delta_cursor(res.new_history_id)["inbox"] == stored["inbox"]
    assert any(str(r.url).startswith(MICROSOFT_OAUTH_BASE) for r in graph.requests), (
        "the 401 did not reach the token endpoint")


@pytest.mark.parametrize("status", [403, 404])
async def test_a_403_or_404_on_a_user_folder_skips_it(wire, status) -> None:
    """``email-delta-failure-isolated``, review round 1 F4b. A 403 or a 404
    on a USER folder skips it by the one rule of the sweep: no count, no
    link and no failure. The same answer on the inbox is a failure (the
    test above)."""
    graph = wire(_Graph(user={"uf-a": "Projects"}))
    _mailbox(graph)
    graph.add("uf-a", "a-1", _now() - timedelta(hours=1))
    seeded = await _poll(None)
    assert "uf-a" in parse_delta_cursor(seeded.new_history_id)
    graph.fail["uf-a"] = [status]
    res = await _poll(seeded.new_history_id)
    report = _report(res)
    assert (report["failed"], report["statuses"]) == (0, [])
    assert report["folders"] == len(SWEEP_SYSTEM_FOLDERS)
    assert "uf-a" not in parse_delta_cursor(res.new_history_id)


def _boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("a defect in the compare")


@pytest.mark.parametrize("helper", ["dump_delta_cursor", "_new_since"])
async def test_a_defect_in_the_compare_leaves_the_sweep_unchanged(
    wire, monkeypatch, helper,
) -> None:
    """``email-delta-failure-isolated``, review round 1 F4a: the outer guard
    of ``sync_messages``. A helper outside the guard of each folder raises.
    The sweep fields equal those of ``off``, the poll returns no cursor, and
    the record counts each folder as failed with ONE status."""
    graph = wire(_Graph())
    _mailbox(graph)
    seeded = await _poll(None)
    off = await _poll(seeded.new_history_id, shadow=False)
    monkeypatch.setattr(outlook, helper, _boom)
    shadow = await _poll(seeded.new_history_id)
    assert _sweep_fields(shadow) == _sweep_fields(off)
    assert shadow.new_history_id is None
    folders = len(SWEEP_SYSTEM_FOLDERS)
    assert _report(shadow) == {
        "folders": folders, "both": 0, "sweep_only": 0, "delta_only": 0,
        "seeding": 0, "removed": 0, "failed": folders,
        "statuses": ["RuntimeError"]}


async def test_a_defect_in_the_compare_keeps_the_cycle_a_success_and_logs_once(
    monkeypatch, caplog,
) -> None:
    """``email-delta-failure-isolated``, review round 1 F4a, through
    ``_sync_account``. The cycle succeeds and upserts what ``off`` upserts.
    Phase (d) gets no cursor, so it keeps the stored one. The cycle logs
    ``email.delta_shadow_failed`` once."""
    graph = _Graph()
    _mailbox(graph)
    _set_mode(monkeypatch, "shadow")
    seeded = await _cycle(monkeypatch, graph, None)
    _set_mode(monkeypatch, "off")
    off = await _cycle(monkeypatch, graph, seeded.cursor)
    _set_mode(monkeypatch, "shadow")
    monkeypatch.setattr(outlook, "dump_delta_cursor", _boom)
    with caplog.at_level("INFO", logger=sched.logger.name):
        run = await _cycle(monkeypatch, graph, seeded.cursor)
    assert "error" not in run.result, run.result

    def _ids(cycle):
        return sorted((m.provider_message_id, m.folder) for m in cycle.upserted)

    assert _ids(run) == _ids(off)
    assert run.cursor is None and run.sync_log is None
    failed = [r.getMessage() for r in caplog.records
              if "email.delta_shadow_failed" in r.getMessage()]
    assert failed == [f"email.delta_shadow_failed account={_ACCOUNT} "
                      f"folders={len(SWEEP_SYSTEM_FOLDERS)} status=RuntimeError"]


# ── email-delta-link-host ───────────────────────────────────────────────────

#: Stored links that are not Graph links. The second puts Graph in the user
#: info, so the host is the collector. The fourth sends the bearer in clear.
_FOREIGN_LINKS = {
    "other-host": "https://collector.example/v1.0/me/mailFolders('inbox')"
                  "/messages/delta?$deltatoken=x",
    "user-info": "https://graph.microsoft.com@collector.example/v1.0/me"
                 "/mailFolders('inbox')/messages/delta?$deltatoken=x",
    "suffix-host": "https://graph.microsoft.com.collector.example/v1.0/me"
                   "/mailFolders('inbox')/messages/delta?$deltatoken=x",
    "plain-http": "http://graph.microsoft.com/v1.0/me/mailFolders('inbox')"
                  "/messages/delta?$deltatoken=x",
    "relative": "/me/mailFolders/inbox/messages/delta?$deltatoken=x",
}


def _safe(requests: list[httpx.Request]) -> bool:
    """True when each request went to Graph over https."""
    return all(r.url.scheme == "https" and r.url.host == "graph.microsoft.com"
               for r in requests)


@pytest.mark.parametrize("link", list(_FOREIGN_LINKS.values()),
                         ids=list(_FOREIGN_LINKS))
async def test_a_stored_link_that_is_not_a_graph_link_sends_no_request(
    wire, caplog, link,
) -> None:
    """``email-delta-link-host``, review round 1 F1. The cursor is text in
    the database, so its inbox link can name any host. No request goes to
    it, so the bearer stays with Graph. The poll drops the link, the record
    names ``link_refused``, and the log names the folder and no URL. The
    next poll seeds the inbox again."""
    graph = wire(_Graph())
    _mailbox(graph)
    stored = parse_delta_cursor((await _poll(None)).new_history_id)
    stored["inbox"] = {"link": link, "at": _iso(_now())}
    cursor = dump_delta_cursor(stored)
    off = await _poll(cursor, shadow=False)
    before, deltas_before = len(graph.requests), len(graph.deltas)
    with caplog.at_level("WARNING", logger=outlook.logger.name):
        shadow = await _poll(cursor)
    assert _safe(graph.requests[before:]), "a request left Graph"
    assert all(str(r.url) != link for r in graph.requests)
    assert not [p for p, _ in graph.deltas[deltas_before:] if p == "inbox"], (
        "the refused inbox link was called")
    assert _sweep_fields(shadow) == _sweep_fields(off)
    report = _report(shadow)
    assert (report["failed"], report["statuses"]) == (1, ["link_refused"])
    after = parse_delta_cursor(shadow.new_history_id)
    assert "inbox" not in after
    assert after["sent"]["link"] == graph.issued["sentitems"]
    lines = [r.getMessage() for r in caplog.records]
    assert [m for m in lines if "email.delta_link_refused" in m] == [
        "email.delta_link_refused folder=inbox source=stored reason=foreign_host"]
    assert not [m for m in lines if "collector" in m or "deltatoken" in m]

    before = len(graph.deltas)
    again = await _poll(shadow.new_history_id)
    inbox = [r for p, r in graph.deltas[before:] if p == "inbox"]
    assert inbox and inbox[0].url.path == "/v1.0/me/mailFolders/inbox/messages/delta"
    assert "inbox" in parse_delta_cursor(again.new_history_id)


@pytest.mark.parametrize(("field", "source"), [
    ("@odata.nextLink", "next"), ("@odata.deltaLink", "delta")])
async def test_a_link_in_a_graph_answer_that_is_not_a_graph_link_is_refused(
    wire, caplog, field, source,
) -> None:
    """``email-delta-link-host``, review round 1 F1. Each inbox page names a
    next link or a delta link on another host. The round sends no request
    to it and stores no link for the inbox. The other folders go on."""
    graph = wire(_Graph(page_size=2))
    _mailbox(graph, n=5)
    real_page = graph._page

    def _foreign(folder: str, rid: int, k: int) -> httpx.Response:
        resp = real_page(folder, rid, k)
        body = json.loads(resp.content)
        if folder == "inbox" and field in body:
            body[field] = _FOREIGN_LINKS["other-host"]
        return httpx.Response(200, json=body)

    graph._page = _foreign  # type: ignore[method-assign]
    off = await _poll(None, shadow=False)
    with caplog.at_level("WARNING", logger=outlook.logger.name):
        shadow = await _poll(None)
    assert _safe(graph.requests), "a request left Graph"
    assert _sweep_fields(shadow) == _sweep_fields(off)
    report = _report(shadow)
    assert (report["failed"], report["statuses"]) == (1, ["link_refused"])
    after = parse_delta_cursor(shadow.new_history_id)
    assert "inbox" not in after and "sent" in after
    assert [r.getMessage() for r in caplog.records
            if "email.delta_link_refused" in r.getMessage()] == [
        f"email.delta_link_refused folder=inbox source={source} reason=foreign_host"]


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


async def test_a_failed_child_folder_read_keeps_the_link_of_the_nested_folder(
    wire,
) -> None:
    """``email-delta-folder-set``, review round 1 F2. ``list_folders`` skips
    a ``childFolders`` read that fails, so the list comes back short. The
    sweep reads what the list gives, as before. The delta keeps the link of
    the nested folder that the list left out, and the parent goes on."""
    graph = wire(_Graph(user={"uf-a": "Projects"}))
    graph.add_folder("uf-n", "Nested", parent="uf-a")
    _mailbox(graph)
    graph.add("uf-n", "n-1", _now() - timedelta(hours=1))
    first = await _poll(None)
    cursor = parse_delta_cursor(first.new_history_id)
    assert {"uf-a", "uf-n"} <= set(cursor)

    graph.child_fail.add("uf-a")
    assert [f.provider_folder_id for f in await _provider().list_folders()] == [
        "uf-a"], "a failed childFolders read changed the list"
    off = await _poll(first.new_history_id, shadow=False)
    shadow = await _poll(first.new_history_id)
    assert _sweep_fields(shadow) == _sweep_fields(off)
    assert "n-1" not in {m.provider_message_id for m in shadow.messages}
    kept = parse_delta_cursor(shadow.new_history_id)
    assert kept["uf-n"] == cursor["uf-n"], "a short folder list dropped a link"
    assert kept["uf-a"]["link"] == graph.issued["uf-a"] != cursor["uf-a"]["link"]
    assert _report(shadow)["failed"] == 0

    # A folder that the short list DOES give follows the normal rules: a 410
    # drops its link. Only the folders that the list left out keep theirs.
    graph.fail["uf-a"] = [410]
    later = parse_delta_cursor((await _poll(shadow.new_history_id)).new_history_id)
    assert "uf-a" not in later, "a short folder list kept a link that Graph dropped"
    assert later["uf-n"] == cursor["uf-n"]


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
