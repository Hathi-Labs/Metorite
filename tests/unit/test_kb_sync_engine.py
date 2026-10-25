"""WS-61 KC-3 — the Knowledge sync, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 steps 5 and 7 and §22
(KC-3), and ``project-docs/specs/knowledge_base.md`` §7.1.

No database and no network. The SQL is proved against a real database in
``test_kc_sync_r8.py``. This file proves:

* **The Google change feed** (``client.py``) with a fake ``Http``: the
  parameters, the ``fields`` mask with no ``alt=media`` and no export, the
  removal and trash mapping, the last event of a page wins,
  ``newStartPageToken``, a dead cursor (404, 410, a 400 on ``pageToken``) is
  ``CursorExpired``, and a 429 is ``RateLimited``. ``content_hash`` is the
  MD5, else ``v`` and the version.
* **No page token in a log.** The filter of ``kb_sources/http.py`` on the
  ``httpx`` logger.
* **The engine** (``engine.py``) on a fake session: each page is ONE
  transaction that holds the node upsert, the cursor and the heartbeat, and
  no provider call runs while a session is open. A 401 forces one refresh,
  and a second 401 fails the run. The relist never reads text.
* **The loop** (``scheduler.py``): the due rules, the flag, the start and the
  stop, one failure goes on, and ``main.py`` reads no flag.

Mutations this file catches (R7):

* the cursor write leaves the page transaction -> ``TestTheEnginePage``;
* the forced refresh runs twice, or never -> ``TestTheUnauthorizedRetry``;
* a state other than ``ready`` becomes due -> ``TestTheDueRule``;
* the start function stops reading the flag -> ``TestTheSwitch``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.kb_sync.engine", reason="gateway not installed")

from acb_common import get_settings
from gateway.connections import shared_token
from gateway.connections import store as conn_store
from gateway.kb_sources.base import (
    KIND_FILE,
    KIND_FOLDER,
    ChangePage,
    CursorExpired,
    DiscoverPage,
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceError,
    SourceNode,
)
from gateway.kb_sources.google_drive import client as gclient
from gateway.kb_sources.google_drive.client import GoogleDriveSource
from gateway.kb_sources.http import HttpAnswer, redact_page_tokens
from gateway.kb_sync import discovery, engine, runs, scheduler

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps/services/gateway/gateway/main.py"

ORG = "66666666-6666-4666-8666-666666666666"
OTHER = "77777777-7777-4777-8777-777777777777"
SOURCE = "88888888-8888-4888-8888-888888888888"
CONNECTION = "99999999-9999-4999-8999-999999999999"
CLIENT = OAuthClientConfig(client_id="cid", client_secret="secret",
                           redirect_uri="https://app.test/cb")
PAGE_TOKEN = "PT-SECRET-4711"


def _cred() -> SourceCredential:
    return SourceCredential(access_token="at-1", refresh_token="rt-1",
                            expires_at=datetime.now(UTC) + timedelta(hours=1))


# ── The Google change feed, with a fake Http ────────────────────────────────


class FakeHttp:
    """A scripted Google. Each GET pops the next answer and records the call."""

    def __init__(self, answers: list[HttpAnswer]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> HttpAnswer:
        self.calls.append((url, dict(params or {})))
        return self.answers.pop(0)

    async def post(self, url: str, *, form: Any, headers: Any = None) -> HttpAnswer:
        raise AssertionError("the change feed sent a POST")


def _ok(body: dict[str, Any]) -> HttpAnswer:
    return HttpAnswer(status_code=200, body=json.dumps(body).encode())


def _status(code: int, body: dict[str, Any] | None = None) -> HttpAnswer:
    return HttpAnswer(status_code=code, body=json.dumps(body or {}).encode())


async def _no_sleep(_s: float) -> None:
    return None


def _drive(answers: list[HttpAnswer]) -> tuple[GoogleDriveSource, FakeHttp]:
    http = FakeHttp(answers)
    return GoogleDriveSource(CLIENT, http=http, sleep=_no_sleep), http


def _file(fid: str, **kw: Any) -> dict[str, Any]:
    return {"id": fid, "name": kw.pop("name", fid), "mimeType": kw.pop("mime", "application/pdf"),
            "parents": kw.pop("parents", ["root-1"]), **kw}


class TestTheChangeFeed:
    def test_the_start_token_reads_every_drive(self) -> None:
        src, http = _drive([_ok({"startPageToken": "4711"})])
        assert asyncio.run(src.changes_start_token(_cred())) == "4711"
        [(url, params)] = http.calls
        assert url.endswith("/drive/v3/changes/startPageToken")
        assert params == {"supportsAllDrives": "true", "fields": "startPageToken"}

    def test_no_start_token_is_a_source_error(self) -> None:
        src, _ = _drive([_ok({})])
        with pytest.raises(SourceError) as err:
            asyncio.run(src.changes_start_token(_cred()))
        assert err.value.code == "no_start_token"

    def test_the_params_and_the_fields_mask(self) -> None:
        src, http = _drive([_ok({"newStartPageToken": "9", "changes": []})])
        asyncio.run(src.list_changed(_cred(), "8", ("ignored",)))
        [(url, params)] = http.calls
        assert url.endswith("/drive/v3/changes")
        assert params["pageToken"] == "8"
        assert params["supportsAllDrives"] == "true"
        assert params["includeItemsFromAllDrives"] == "true"
        assert params["includeRemoved"] == "true"
        assert params["pageSize"] == str(gclient.PAGE_SIZE)
        assert params["fields"] == gclient.CHANGE_FIELDS
        assert "alt" not in params and "export" not in url
        # Metadata only: no content and no export link in the mask.
        mask = params["fields"].lower()
        for word in ("export", "content", "thumbnail", "alt="):
            assert word not in mask, word
        for word in ("md5checksum", "version", "trashed", "removed", "newstartpagetoken"):
            assert word in mask, word

    def test_discovery_reads_the_hash_fields_too(self) -> None:
        assert "md5Checksum" in gclient.FILE_FIELDS and "version" in gclient.FILE_FIELDS

    def test_a_removal_and_a_trash_are_removals(self) -> None:
        body = {"newStartPageToken": "9", "changes": [
            {"changeType": "file", "fileId": "gone-1", "removed": True},
            {"changeType": "file", "fileId": "trash-1", "removed": False,
             "file": _file("trash-1", trashed=True)},
            {"changeType": "file", "fileId": "live-1", "removed": False,
             "file": _file("live-1", md5Checksum="0" * 32)},
        ]}
        page = gclient.change_page(body)
        assert set(page.removed) == {"gone-1", "trash-1"}
        assert [n.ext_id for n in page.upserts] == ["live-1"]
        assert page.new_start_token == "9" and page.next_token is None

    def test_a_shared_drive_change(self) -> None:
        body = {"nextPageToken": "2", "changes": [
            {"changeType": "drive", "driveId": "sd-1", "removed": True},
            {"changeType": "drive", "driveId": "sd-2", "removed": False,
             "drive": {"id": "sd-2", "name": "Sales EU"}},
            {"changeType": "drive", "driveId": "sd-3", "removed": False, "drive": {"id": "sd-3"}},
        ]}
        page = gclient.change_page(body)
        assert page.removed == ("sd-1",)
        [root] = page.upserts
        assert (root.ext_id, root.kind, root.name, root.drive_id, root.parent_ext_id) == (
            "sd-2", KIND_FOLDER, "Sales EU", "sd-2", None)
        assert page.next_token == "2" and page.new_start_token is None

    def test_the_last_event_of_an_id_wins_in_feed_order(self) -> None:
        body = {"newStartPageToken": "9", "changes": [
            {"changeType": "file", "fileId": "a", "file": _file("a", name="A one")},
            {"changeType": "file", "fileId": "b", "file": _file("b")},
            {"changeType": "file", "fileId": "a", "removed": True},
            {"changeType": "file", "fileId": "c", "removed": True},
            {"changeType": "file", "fileId": "c", "file": _file("c", name="C back")},
            {"changeType": "file", "fileId": "b", "file": _file("b", name="B two")},
        ]}
        page = gclient.change_page(body)
        assert page.removed == ("a",)
        assert [(n.ext_id, n.name) for n in page.upserts] == [("c", "C back"), ("b", "B two")]
        ids = [n.ext_id for n in page.upserts] + list(page.removed)
        assert len(ids) == len(set(ids)), "an id is an upsert or a removal, never both"

    def test_a_malformed_id_is_skipped(self) -> None:
        body = {"newStartPageToken": "9", "changes": [
            {"changeType": "file", "fileId": "bad id;drop", "removed": True},
            {"changeType": "file", "fileId": "ok-1", "file": _file("other-id")},
        ]}
        page = gclient.change_page(body)
        assert page.removed == () and page.upserts == ()

    def test_the_content_hash(self) -> None:
        body = {"newStartPageToken": "9", "changes": [
            {"changeType": "file", "fileId": "pdf", "file": _file("pdf", md5Checksum="A" * 32,
                                                                  version="7")},
            {"changeType": "file", "fileId": "doc", "file": _file(
                "doc", mime="application/vnd.google-apps.document", version="12")},
            {"changeType": "file", "fileId": "dir", "file": _file(
                "dir", mime=gclient.FOLDER_MIME, version="3")},
            {"changeType": "file", "fileId": "odd", "file": _file("odd", md5Checksum="zz",
                                                                  version="x1")},
        ]}
        hashes = {n.ext_id: n.content_hash for n in gclient.change_page(body).upserts}
        assert hashes == {"pdf": "a" * 32, "doc": "v12", "dir": None, "odd": None}

    @pytest.mark.parametrize("answer", [
        _status(404), _status(410),
        _status(400, {"error": {"errors": [{"reason": "invalid", "location": "pageToken"}]}}),
    ])
    def test_a_dead_cursor_is_cursor_expired(self, answer: HttpAnswer) -> None:
        src, _ = _drive([answer])
        with pytest.raises(CursorExpired):
            asyncio.run(src.list_changed(_cred(), "8", ()))

    def test_another_400_is_not_cursor_expired(self) -> None:
        src, _ = _drive([_status(400, {"error": {"errors": [{"location": "fields"}]}})])
        with pytest.raises(SourceError) as err:
            asyncio.run(src.list_changed(_cred(), "8", ()))
        assert not isinstance(err.value, CursorExpired) and err.value.code == "http_400"

    def test_a_404_on_the_start_token_is_not_cursor_expired(self) -> None:
        src, _ = _drive([_status(404)])
        with pytest.raises(SourceError) as err:
            asyncio.run(src.changes_start_token(_cred()))
        assert not isinstance(err.value, CursorExpired)

    def test_no_cursor_is_cursor_expired_with_no_call(self) -> None:
        src, http = _drive([])
        with pytest.raises(CursorExpired):
            asyncio.run(src.list_changed(_cred(), None, ()))
        assert http.calls == []

    def test_a_429_is_rate_limited(self) -> None:
        src, http = _drive([_status(429)] * 4)
        with pytest.raises(RateLimited):
            asyncio.run(src.list_changed(_cred(), "8", ()))
        assert len(http.calls) == 4

    def test_a_401_is_unauthorized(self) -> None:
        src, _ = _drive([_status(401)])
        with pytest.raises(SourceError) as err:
            asyncio.run(src.list_changed(_cred(), "8", ()))
        assert err.value.code == "unauthorized"

    def test_a_truncated_page_is_read_again_smaller(self) -> None:
        big = HttpAnswer(status_code=200, body=b"{}", truncated=True)
        src, http = _drive([big, _ok({"newStartPageToken": "9", "changes": []})])
        asyncio.run(src.list_changed(_cred(), "8", ()))
        assert [p["pageSize"] for _u, p in http.calls] == [
            str(gclient.PAGE_SIZE), str(gclient.PAGE_SIZE // 2)]

    def test_a_page_with_no_cursor_is_a_source_error(self) -> None:
        src, _ = _drive([_ok({"changes": []})])
        with pytest.raises(SourceError) as err:
            asyncio.run(src.list_changed(_cred(), "8", ()))
        assert err.value.code == "no_next_cursor"


# ── No page token in a log ──────────────────────────────────────────────────


class TestNoPageTokenInALog:
    def test_the_filter_redacts_text_and_an_httpx_url(self) -> None:
        import httpx

        url = httpx.URL(f"https://www.googleapis.com/drive/v3/changes?pageToken={PAGE_TOKEN}&x=1")
        out = redact_page_tokens(url)
        assert PAGE_TOKEN not in str(out) and "pageToken=[redacted]&x=1" in str(out)
        assert redact_page_tokens(f"GET /changes?pageToken={PAGE_TOKEN}") == (
            "GET /changes?pageToken=[redacted]")
        assert redact_page_tokens(200) == 200 and redact_page_tokens("no token") == "no token"

    def test_an_httpx_request_line_holds_no_page_token(self, caplog) -> None:
        import httpx

        caplog.set_level(logging.DEBUG, logger="httpx")
        url = httpx.URL(f"https://www.googleapis.com/drive/v3/changes?pageToken={PAGE_TOKEN}")
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s %d %s"',
                                        "GET", url, "HTTP/1.1", 200, "OK")
        lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
        assert lines, "the capture saw nothing, so the check would be vacuous"
        assert all(PAGE_TOKEN not in line for line in lines), lines


# ── The engine, on a fake session ───────────────────────────────────────────


#: Each statement the engine may send, by its exact text.
_NAMES = {
    engine._SOURCE_SQL: "source",
    engine._CAPPED_SQL: "capped",
    engine.UPSERT_CHANGES_SQL: "upsert_changes",
    engine._REMOVE_SQL: "remove",
    engine._EXPLORED_FOLDERS_SQL: "explored_folders",
    engine._GONE_IN_FOLDER_SQL: "gone_folder",
    engine._NOW_SQL: "now",
    engine._SOURCE_OK_SQL: "source_ok",
    engine._SOURCE_ERROR_SQL: "source_error",
    engine._SOURCE_RECONNECT_SQL: "source_reconnect",
    engine._CASCADE_GONE_SQL: "cascade",
    engine._CAP_SQL: "cap",
    runs._PRUNE_SQL: "prune",
    runs._TAKEOVER_SQL: "takeover",
    runs._PREEMPT_SQL: "preempt",
    runs._INSERT_SQL: "insert_run",
    runs._PROGRESS_SQL: "progress",
    runs._FINISH_SQL: "finish",
    runs._CURSOR_SQL: "cursor",
    runs.UPSERT_CURSOR_SQL: "upsert_cursor",
    runs._DUE_SQL: "due",
    discovery.UPSERT_NODES_SQL: "upsert_nodes",
    discovery._LAST_NEW_SQL: "last_new",
    discovery.GONE_SQL: "gone",
    conn_store._MARK_SYNCED_SQL: "mark_synced",
    # KC-3h, the lock order.
    discovery.LOCK_SOURCE_SHARE_SQL: "guard",
    discovery.LOCK_SOURCE_UPDATE_SQL: "lock_source",
    conn_store._LOCK_BY_ID_SQL: "lock_connection",
    # Discovery, for the lock order fence of its transactions.
    discovery._SOURCE_SQL: "disc_source",
    discovery._START_SQL: "disc_start",
    discovery._FINISH_SQL: "disc_finish",
    discovery._FAIL_SQL: "disc_fail",
    discovery._CLEAR_SQL: "clear",
    discovery._UNEXPLORED_SQL: "unexplored",
}


class _Result:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class World:
    """The fake database and the record of what happened, in order."""

    def __init__(self, *, token: str | None = "t0", state: str = "ready",
                 capped: bool = False) -> None:
        self.token, self.state, self.capped = token, state, capped
        self.open = 0
        self.events: list[tuple[str, Any]] = []
        self.stored: list[dict[str, Any]] = []   # the committed cursor states
        self.pending: list[dict[str, Any]] = []  # the cursor writes of the open txn
        self.fail_on: str | None = None
        self.disconnected = False
        self.lost = False
        self.status: list[tuple[str, str | None]] = []
        #: The params of each cascade and each cap statement.
        self.cascades: list[dict[str, Any]] = []
        self.caps: list[dict[str, Any]] = []
        #: The capped relist: the explored folders, and what each sweep took.
        self.explored: list[str] = []
        self.swept: dict[str, list[str]] = {}

    @contextlib.asynccontextmanager
    async def session(self, org: str):
        assert org == ORG, "a session bound another org"
        self.open += 1
        self.events.append(("open", None))
        self.pending = []
        try:
            yield _Db(self)
        except BaseException:
            self.open -= 1
            self.events.append(("rollback", None))
            self.pending = []
            raise
        self.open -= 1
        self.events.append(("commit", None))
        self.stored.extend(self.pending)


class _Db:
    def __init__(self, world: World) -> None:
        self.w = world

    @contextlib.asynccontextmanager
    async def begin_nested(self):
        yield self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        p = params or {}
        name = _NAMES[str(stmt)]
        assert p.get("org", ORG) == ORG, f"{name} named another org"
        self.w.events.append(("sql", name))
        if name == self.w.fail_on:
            raise RuntimeError("injected")
        answer = getattr(self, f"_{name}", None)
        return answer(p) if answer is not None else _Result(rowcount=0)

    def _source(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(id=SOURCE, provider="google_drive",
                                        owner_email="ana@kc3.test", state=self.w.state,
                                        connection_id=CONNECTION)])

    _disc_source = _source

    def _guard(self, p: dict[str, Any]) -> _Result:
        """The source lock. A disconnected source leaves no live row."""
        assert p["source"] == SOURCE, "the lock took another source"
        return _Result([] if self.w.disconnected else [SimpleNamespace(id=SOURCE)])

    _lock_source = _guard

    def _lock_connection(self, p: dict[str, Any]) -> _Result:
        assert p["id"] == CONNECTION, "close_ok locked another connection"
        return _Result([])

    def _disc_start(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(started_at=datetime.now(UTC))])

    def _insert_run(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(id="run-1", started_at=datetime.now(UTC))])

    def _cursor(self, p: dict[str, Any]) -> _Result:
        if p["kind"] != runs.CURSOR_CHANGES or self.w.token is None:
            return _Result([])
        return _Result([SimpleNamespace(kind="changes", cursor_state={"page_token": self.w.token},
                                        last_run_at=None, last_status="ok")])

    def _capped(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(capped=self.w.capped)])

    def _upsert_changes(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(inserted=True) for _ in json.loads(p["rows"])])

    _upsert_nodes = _upsert_changes

    def _remove(self, p: dict[str, Any]) -> _Result:
        return _Result(rowcount=len(p["ids"]))

    def _upsert_cursor(self, p: dict[str, Any]) -> _Result:
        if self.w.disconnected:
            return _Result([])
        self.w.pending.append({"kind": p["kind"], "state": p["state"], "status": p["status"]})
        return _Result([SimpleNamespace(kind=p["kind"])])

    def _progress(self, p: dict[str, Any]) -> _Result:
        return _Result(rowcount=0 if self.w.lost else 1)

    def _finish(self, p: dict[str, Any]) -> _Result:
        self.w.status.append((p["status"], p["error"]))
        return _Result(rowcount=1)

    def _source_ok(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(connection_id=CONNECTION)])

    def _explored_folders(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(ext_id=f) for f in self.w.explored])

    def _gone_folder(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(ext_id=i) for i in self.w.swept.get(p["folder"], [])])

    def _cascade(self, p: dict[str, Any]) -> _Result:
        self.w.cascades.append(dict(p))
        return _Result(rowcount=0)

    def _cap(self, p: dict[str, Any]) -> _Result:
        self.w.caps.append(dict(p))
        return _Result(rowcount=1)

    def _now(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(at=datetime.now(UTC))])


class FakeAdapter:
    """A source that asserts no session is open during each of its calls."""

    provider = "google_drive"

    def __init__(self, world: World, pages: dict[str, Any], *,
                 tree: tuple[SourceNode, ...] = ()) -> None:
        self.w, self.pages, self.tree = world, pages, tree
        self.cursors: list[str | None] = []

    def _enter(self, name: str) -> None:
        assert self.w.open == 0, f"{name} ran while a session was open"
        self.w.events.append(("call", name))

    async def list_changed(self, cred: Any, cursor: str | None, folders: Any) -> ChangePage:
        self._enter("list_changed")
        self.cursors.append(cursor)
        item = self.pages[cursor]
        if isinstance(item, BaseException):
            raise item
        return item

    async def changes_start_token(self, cred: Any) -> str:
        self._enter("start")
        return "s-new"

    async def discover(self, cred: Any, cursor: Any = None) -> DiscoverPage:
        self._enter("discover")
        return DiscoverPage(nodes=self.tree, next_cursor=None)

    async def refresh(self, cred: Any) -> Any:
        self._enter("refresh")
        return cred

    async def fetch_text(self, cred: Any, node: Any) -> str:
        raise AssertionError("KC-3 read the text of a document")

    async def fetch_bytes(self, cred: Any, node: Any) -> bytes:
        raise AssertionError("KC-3 read the bytes of a document")


def _node(ext_id: str, parent: str | None = "root-1", kind: str = KIND_FILE) -> SourceNode:
    return SourceNode(ext_id=ext_id, parent_ext_id=parent, kind=kind, name=ext_id,
                      mime=None, size=None, modified_at=None, web_url=None,
                      content_hash="v1")


def _page(*upserts: SourceNode, removed: tuple[str, ...] = (), nxt: str | None = None,
          start: str | None = None) -> ChangePage:
    return ChangePage(upserts=tuple(upserts), removed=removed, next_token=nxt,
                      new_start_token=start if nxt is None else None)


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch):
    return _bind(monkeypatch, World())


def _bind(monkeypatch: pytest.MonkeyPatch, w: World) -> World:
    """Point the engine and discovery at the fake world *w*."""
    creds: list[bool] = []

    async def fake_cred(org: str, owner: str, source: Any, *, force: bool = False) -> Any:
        assert w.open == 0, "the credential was read inside an engine session"
        assert owner == "ana@kc3.test", "the member did not come from the source row"
        creds.append(force)
        w.events.append(("cred", force))
        return _cred()

    monkeypatch.setattr(engine, "tenant_session", w.session)
    monkeypatch.setattr(discovery, "tenant_session", w.session)
    monkeypatch.setattr(discovery, "_credential", fake_cred)
    w.creds = creds  # type: ignore[attr-defined]
    return w


def _run(kind: str, adapter: Any, **kw: Any) -> engine.RunOutcome:
    return asyncio.run(engine.run_source(ORG, SOURCE, kind, source=adapter, **kw))


def _txns(events: list[tuple[str, Any]]) -> list[list[str]]:
    """The statements of each committed or rolled-back session, in order."""
    out: list[list[str]] = []
    for kind, value in events:
        if kind == "open":
            out.append([])
        elif kind == "sql":
            out[-1].append(value)
        elif kind in ("commit", "rollback"):
            out[-1].append(kind)
    return out


class TestTheEnginePage:
    def test_each_page_is_one_transaction_with_its_cursor_and_heartbeat(self, world) -> None:
        adapter = FakeAdapter(world, {
            "t0": _page(_node("n1"), removed=("old",), nxt="t1"),
            "t1": _page(_node("n2"), start="t2"),
        })
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == runs.STATUS_OK, out
        txns = _txns(world.events)
        pages = [t for t in txns if "upsert_cursor" in t]
        # KC-3h: each page takes the source lock first. An upsert can stamp
        # last_new_at, so the lock is FOR NO KEY UPDATE.
        assert pages == [
            ["lock_source", "upsert_changes", "last_new", "remove", "cascade", "upsert_cursor",
             "progress", "commit"],
            ["lock_source", "upsert_changes", "last_new", "upsert_cursor", "progress", "commit"],
        ], txns
        assert [json.loads(s["state"])["page_token"] for s in world.stored] == ["t1", "t2"]
        assert [s["status"] for s in world.stored] == [runs.CURSOR_PARTIAL, runs.CURSOR_OK]
        assert adapter.cursors == ["t0", "t1"]
        # Step (a) is one session, and step (d) closes the run and marks the connection.
        assert txns[0][:4] == ["source", "takeover", "insert_run", "cursor"]
        # KC-3h (K-22): the connection row, then the source row, then the run.
        assert txns[-1] == ["lock_connection", "lock_source", "finish", "source_ok",
                            "mark_synced", "commit"]

    def test_the_provider_call_of_a_page_waits_for_the_commit_of_the_last(self, world) -> None:
        adapter = FakeAdapter(world, {
            "t0": _page(_node("n1"), nxt="t1"), "t1": _page(start="t2"),
        })
        _run(runs.KIND_CHANGES, adapter)
        seq = [e for e in world.events if e[0] in ("call", "commit", "open")]
        calls = [i for i, e in enumerate(seq) if e == ("call", "list_changed")]
        commits = [i for i, e in enumerate(seq) if e[0] == "commit"]
        assert len(calls) == 2
        assert any(calls[0] < c < calls[1] for c in commits), seq

    def test_a_failed_cursor_write_rolls_the_page_back(self, world) -> None:
        world.fail_on = "upsert_cursor"
        adapter = FakeAdapter(world, {"t0": _page(_node("n1"), start="t1")})
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == runs.STATUS_FAILED and out.error == "RuntimeError"
        page = next(t for t in _txns(world.events) if "upsert_cursor" in t)
        assert page[:2] == ["lock_source", "upsert_changes"] and page[-1] == "rollback", page
        assert world.stored == []

    def test_a_disconnect_stops_the_run_with_no_cursor(self, world) -> None:
        world.disconnected = True
        adapter = FakeAdapter(world, {"t0": _page(_node("n1"), nxt="t1")})
        out = _run(runs.KIND_CHANGES, adapter)
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "disconnected")
        assert adapter.cursors == ["t0"], "the run read on after the disconnect"

    def test_a_lost_lock_writes_nothing_more(self, world) -> None:
        world.lost = True
        adapter = FakeAdapter(world, {"t0": _page(_node("n1"), nxt="t1")})
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == "lost"
        assert world.stored == [] and world.status == [], "a lost run closed a row"

    def test_rate_limited_stops_with_the_cursor_at_the_last_page(self, world) -> None:
        adapter = FakeAdapter(world, {
            "t0": _page(_node("n1"), nxt="t1"),
            "t1": RateLimited("slow down", tries=4),
        })
        out = _run(runs.KIND_CHANGES, adapter)
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "rate_limited")
        assert [json.loads(s["state"])["page_token"] for s in world.stored] == ["t1"]
        assert "source_error" in _txns(world.events)[-1]

    def test_needs_reconnect_sets_the_source_and_stops(self, world) -> None:
        exc = NeedsReconnect("dead")
        exc.code = "invalid_grant"
        adapter = FakeAdapter(world, {"t0": exc})
        out = _run(runs.KIND_CHANGES, adapter)
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "invalid_grant")
        assert _txns(world.events)[-1] == ["lock_source", "finish", "source_reconnect", "commit"]

    def test_another_source_error_fails_and_keeps_the_state(self, world) -> None:
        exc = SourceError("boom")
        exc.code = "http_500"
        adapter = FakeAdapter(world, {"t0": exc})
        out = _run(runs.KIND_CHANGES, adapter)
        assert (out.status, out.error) == (runs.STATUS_FAILED, "http_500")
        assert _txns(world.events)[-1] == ["lock_source", "finish", "source_error", "commit"]

    def test_a_source_that_is_not_ready_takes_no_lock(self, world) -> None:
        world.state = "needs_reconnect"
        adapter = FakeAdapter(world, {})
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == "skipped"
        assert "insert_run" not in [e[1] for e in world.events]
        assert not [e for e in world.events if e[0] == "call"], "a provider call ran"

    def test_an_expired_cursor_takes_a_token_first_then_relists(self, world) -> None:
        adapter = FakeAdapter(world, {"t0": CursorExpired("dead")},
                              tree=(_node("root-1", None, KIND_FOLDER), _node("f1")))
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == runs.STATUS_OK and out.counts["cursor_expired"] is True
        calls = [e[1] for e in world.events if e[0] == "call"]
        assert calls == ["list_changed", "start", "discover"], calls
        stored = [(s["kind"], json.loads(s["state"]) if s["state"] else None) for s in world.stored]
        assert stored == [("changes", {"page_token": "s-new"}), ("reconcile", None)]
        # The new token is stored AFTER the relist and its gone sweep.
        names = [e[1] for e in world.events if e[0] == "sql"]
        assert names.index("gone") < names.index("upsert_cursor")

    def test_no_cursor_takes_a_token_first(self, world) -> None:
        world.token = None
        adapter = FakeAdapter(world, {}, tree=(_node("f1"),))
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == runs.STATUS_OK and out.counts["relist"] is True
        assert [e[1] for e in world.events if e[0] == "call"] == ["start", "discover"]

    def test_the_relist_reads_no_text(self, world) -> None:
        adapter = FakeAdapter(world, {}, tree=(_node("f1"), _node("f2")))
        out = _run(runs.KIND_RECONCILE, adapter)
        assert out.status == runs.STATUS_OK, out  # fetch_text and fetch_bytes raise
        assert [e[1] for e in world.events if e[0] == "call"] == ["discover"]

    def test_a_capped_relist_never_clears_the_source(self, world) -> None:
        world.capped = True
        adapter = FakeAdapter(world, {}, tree=(_node("f1"),))
        out = _run(runs.KIND_RECONCILE, adapter)
        assert out.status == runs.STATUS_OK
        names = [e[1] for e in world.events if e[0] == "sql"]
        assert "explored_folders" in names and "gone" not in names

    def test_a_removal_cascades_in_its_page_and_spares_the_page_upserts(self, world) -> None:
        adapter = FakeAdapter(world, {"t0": _page(_node("keep"), removed=("dir", "f"),
                                                  start="t1")})
        assert _run(runs.KIND_CHANGES, adapter).status == runs.STATUS_OK
        [c] = world.cascades
        assert (c["ids"], c["kept"], c["before"]) == (["dir", "f"], ["keep"], None)
        page = next(t for t in _txns(world.events) if "upsert_cursor" in t)
        assert page.index("remove") < page.index("cascade") < page.index("upsert_cursor")

    def test_a_page_with_no_removal_runs_no_cascade(self, world) -> None:
        _run(runs.KIND_CHANGES, FakeAdapter(world, {"t0": _page(_node("a"), start="t1")}))
        assert world.cascades == []

    def test_a_capped_sweep_cascades_with_the_relist_start(self, world) -> None:
        world.capped = True
        world.explored = ["root-1"]
        world.swept = {"root-1": ["old-dir"]}
        assert _run(runs.KIND_RECONCILE, FakeAdapter(world, {})).status == runs.STATUS_OK
        [c] = world.cascades
        assert c["ids"] == ["old-dir"] and c["kept"] == [] and c["before"] is not None
        sweep = next(t for t in _txns(world.events) if "gone_folder" in t)
        assert sweep[:4] == ["guard", "gone_folder", "cascade", "progress"]

    def test_an_uncapped_relist_past_the_cap_becomes_capped(self, world, monkeypatch) -> None:
        monkeypatch.setattr(discovery, "NODE_CAP", 1)
        adapter = FakeAdapter(world, {}, tree=(_node("a"), _node("b")))
        out = _run(runs.KIND_RECONCILE, adapter)
        assert out.status == runs.STATUS_OK and out.counts["over_cap"] is True
        assert [c["levels"] for c in world.caps] == [discovery.LEVELS]
        names = [e[1] for e in world.events if e[0] == "sql"]
        assert "gone" not in names, "a partial listing marked nodes gone"
        [mark] = [s for s in world.stored if s["kind"] == runs.CURSOR_RECONCILE]
        assert json.loads(mark["state"]) == {"capped": True}

    def test_a_capped_source_keeps_its_mark(self, world) -> None:
        world.capped = True
        _run(runs.KIND_RECONCILE, FakeAdapter(world, {}))
        [mark] = [s for s in world.stored if s["kind"] == runs.CURSOR_RECONCILE]
        assert json.loads(mark["state"]) == {"capped": True}

    def test_the_lock_read_takes_a_share_lock(self) -> None:
        assert engine._SOURCE_SQL.rstrip().endswith("FOR SHARE")

    def test_the_loop_runs_no_discover_kind(self, world) -> None:
        with pytest.raises(ValueError):
            _run(runs.KIND_DISCOVER, FakeAdapter(world, {}))


class TestTheUnauthorizedRetry:
    def _unauthorized(self) -> SourceError:
        exc = SourceError("401")
        exc.code = engine.UNAUTHORIZED
        return exc

    def test_one_401_forces_one_refresh_and_runs_again(self, world) -> None:
        calls = {"n": 0}
        adapter = FakeAdapter(world, {})

        async def list_changed(cred: Any, cursor: Any, folders: Any) -> ChangePage:
            adapter._enter("list_changed")
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._unauthorized()
            return _page(start="t1")

        adapter.list_changed = list_changed  # type: ignore[method-assign]
        out = _run(runs.KIND_CHANGES, adapter)
        assert out.status == runs.STATUS_OK
        assert world.creds == [False, True], world.creds

    def test_a_second_401_fails_the_run(self, world) -> None:
        adapter = FakeAdapter(world, {"t0": self._unauthorized()})
        out = _run(runs.KIND_CHANGES, adapter)
        assert (out.status, out.error) == (runs.STATUS_FAILED, "unauthorized")
        assert world.creds == [False, True], "the run refreshed more than once"
        assert adapter.cursors == ["t0", "t0"]

    def test_the_forced_refresh_is_due_whatever_the_expiry(self, monkeypatch) -> None:
        seen: list[bool] = []

        async def fake_fresh(org: str, **kw: Any) -> Any:
            cred = _cred()  # expires in an hour, so a normal read is not due
            seen.append(kw["needs_refresh"](cred, datetime.now(UTC)))
            assert kw["member_email"] == "ana@kc3.test"
            assert kw["scope_kind"] == conn_store.SCOPE_MEMBER
            return cred

        monkeypatch.setattr(shared_token, "fresh_credential", fake_fresh)
        adapter = FakeAdapter(World(), {})
        asyncio.run(discovery._credential(ORG, "ana@kc3.test", adapter))
        asyncio.run(discovery._credential(ORG, "ana@kc3.test", adapter, force=True))
        assert seen == [False, True]


# ── The lock order (KC-3h) ──────────────────────────────────────────────────
#
# The module doc of ``engine.py`` holds the one lock order: the connection
# row, then the ``kb_sources`` row, then the children. Each lock has, at its
# FIRST touch, the strongest strength that the transaction needs. The R8 half
# (two paths at the same time, no deadlock) is ``test_kc_sync_r8.py``.

#: The connection row: the lock of ``store.lock_by_id``, and its UPDATE.
_CONN = frozenset({"lock_connection", "mark_synced"})
#: The source row at the strength of a share lock. The step (a) read takes
#: ``FOR SHARE``, and the guard ``FOR KEY SHARE``.
_SOURCE_WEAK = frozenset({"source", "guard"})
#: The source row at the strength of an UPDATE.
_SOURCE_STRONG = frozenset({
    "lock_source", "source_ok", "source_error", "source_reconnect", "last_new",
    "disc_start", "disc_finish", "disc_fail",
})
#: A child row (a node, a cursor or a run) that ALSO takes ``FOR KEY SHARE``
#: on the source row inside the statement: the guard of an upsert, or the
#: foreign key check of an INSERT.
_CHILD_AND_SOURCE = frozenset({"upsert_changes", "upsert_nodes", "upsert_cursor",
                               "insert_run"})
#: A child row and nothing else.
_CHILD_ONLY = frozenset({
    "remove", "cascade", "gone", "gone_folder", "cap", "clear", "unexplored", "progress",
    "finish", "takeover", "preempt",
})
#: The reads that lock nothing.
_READS = frozenset({"capped", "cursor", "now", "explored_folders", "disc_source"})
#: The rows of nodes and cursors: a transaction that writes them is a page.
_PAGE_WRITES = frozenset({"upsert_changes", "upsert_nodes", "remove", "cascade", "gone",
                          "gone_folder", "cap", "clear", "unexplored", "upsert_cursor"})
#: The statements that may open a write transaction of the engine.
_ENGINE_FIRST = frozenset({"source", "guard", "lock_source", "lock_connection"})


def lock_order_faults(txn: list[str], *, engine_txn: bool = False) -> list[str]:
    """Each break of the lock order in ONE transaction (its statement names).

    The rules of ``engine.py``: the connection row comes before the source
    row and the children, and the source row comes before each child. When
    the transaction writes the source row, its FIRST touch of the source row
    has the strength of an UPDATE. *engine_txn* adds one rule: a page, or a
    transaction that touches more than one kind of row, starts with a lock.
    An unknown statement is a fault, so a new statement must get a class.
    """
    stmts = [s for s in txn if s not in ("commit", "rollback")]
    known = _CONN | _SOURCE_WEAK | _SOURCE_STRONG | _CHILD_AND_SOURCE | _CHILD_ONLY | _READS
    unknown = sorted(set(stmts) - known)
    if unknown:
        return [f"no lock class for {unknown}"]
    locking = [s for s in stmts if s not in _READS]
    if not locking:
        return []
    source = _SOURCE_WEAK | _SOURCE_STRONG | _CHILD_AND_SOURCE

    def first(group: frozenset[str]) -> int | None:
        return next((i for i, s in enumerate(locking) if s in group), None)

    faults: list[str] = []
    conn, src = first(_CONN), first(source)
    child = first(_CHILD_ONLY | _CHILD_AND_SOURCE)
    if conn is not None and src is not None and conn > src:
        faults.append("the connection row comes after the source row")
    if conn is not None and child is not None and conn > child:
        faults.append("the connection row comes after a child row")
    only = first(_CHILD_ONLY)
    if src is not None and only is not None and only < src:
        faults.append("a child row comes before the source lock")
    if src is not None and locking[src] not in _SOURCE_STRONG and _SOURCE_STRONG & set(locking):
        faults.append("the source lock starts weak and becomes an UPDATE")
    kinds = sum(x is not None for x in (conn, src, child))
    if engine_txn and (kinds > 1 or _PAGE_WRITES & set(locking)) and (
            stmts[0] not in _ENGINE_FIRST):
        faults.append(f"the first statement is {stmts[0]!r}, not a lock")
    return faults


def _scenario(name: str, w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive one path of the engine or of discovery on the fake world *w*."""
    if name == "pages":
        adapter = FakeAdapter(w, {
            "t0": _page(_node("n1"), removed=("old",), nxt="t1"),
            "t1": _page(removed=("n9",), nxt="t2"),  # removals only (K-21)
            "t2": _page(start="t3"),
        })
        assert _run(runs.KIND_CHANGES, adapter).status == runs.STATUS_OK
    elif name == "expired_cursor":
        adapter = FakeAdapter(w, {"t0": CursorExpired("dead")}, tree=(_node("f1"),))
        assert _run(runs.KIND_CHANGES, adapter).status == runs.STATUS_OK
    elif name == "over_cap":
        monkeypatch.setattr(discovery, "NODE_CAP", 1)
        adapter = FakeAdapter(w, {}, tree=(_node("a"), _node("b")))
        assert _run(runs.KIND_RECONCILE, adapter).counts["over_cap"] is True
    elif name == "capped_sweep":
        w.capped, w.explored, w.swept = True, ["root-1"], {"root-1": ["old-dir"]}
        adapter = FakeAdapter(w, {}, tree=(_node("f1"),))
        assert _run(runs.KIND_RECONCILE, adapter).status == runs.STATUS_OK
    elif name in ("needs_reconnect", "source_error"):
        exc: SourceError = NeedsReconnect("dead") if name == "needs_reconnect" else SourceError("x")
        exc.code = "invalid_grant" if name == "needs_reconnect" else "http_500"
        assert _run(runs.KIND_CHANGES, FakeAdapter(w, {"t0": exc})).error == exc.code
    elif name == "disconnected":
        w.disconnected = True
        out = _run(runs.KIND_CHANGES, FakeAdapter(w, {"t0": _page(removed=("x",), nxt="t1")}))
        assert out.error == "disconnected"
    elif name == "discovery":
        adapter = FakeAdapter(w, {}, tree=(_node("f1"),))
        assert asyncio.run(discovery.discover_source(ORG, SOURCE, adapter)) == "ready"
    elif name == "discovery_capped":
        monkeypatch.setattr(discovery, "NODE_CAP", 1)
        adapter = FakeAdapter(w, {}, tree=(_node("d1", "root-1", KIND_FOLDER), _node("f1")))
        assert asyncio.run(discovery.discover_source(ORG, SOURCE, adapter)) == "ready"
    elif name == "discovery_failed":
        adapter = FakeAdapter(w, {})

        async def no_token(cred: Any) -> str:
            raise SourceError("no token")

        adapter.changes_start_token = no_token  # type: ignore[method-assign]
        assert asyncio.run(discovery.discover_source(ORG, SOURCE, adapter)) == "failed"
    else:
        raise AssertionError(name)


_SCENARIOS = ("pages", "expired_cursor", "over_cap", "capped_sweep", "needs_reconnect",
              "source_error", "disconnected", "discovery", "discovery_capped",
              "discovery_failed")


class TestTheLockOrder:
    """KC-3h, the hermetic fence of the lock order (``engine.py``, the module doc)."""

    @pytest.mark.parametrize("name", _SCENARIOS)
    def test_each_transaction_keeps_the_lock_order(self, name: str, monkeypatch) -> None:
        w = _bind(monkeypatch, World())
        _scenario(name, w, monkeypatch)
        engine_txn = not name.startswith("discovery")
        faults = {i: f for i, t in enumerate(_txns(w.events))
                  if (f := lock_order_faults(t, engine_txn=engine_txn))}
        assert faults == {}, (faults, _txns(w.events))

    def test_close_ok_locks_the_connection_before_the_source(self, world, monkeypatch) -> None:
        _scenario("pages", world, monkeypatch)
        [close] = [t for t in _txns(world.events) if "mark_synced" in t]
        assert close.index("lock_connection") < close.index("lock_source") < close.index(
            "finish"), close

    def test_a_removal_only_page_takes_the_guard_first(self, world, monkeypatch) -> None:
        """K-21: the page locks the source row before any node row."""
        _scenario("pages", world, monkeypatch)
        [page] = [t for t in _txns(world.events) if "remove" in t and "upsert_changes" not in t]
        assert page[:2] == ["guard", "remove"], page

    def test_the_scenarios_reach_every_write_transaction(self, monkeypatch) -> None:
        """The fence is not vacuous: the paths reach each kind of write."""
        seen: set[str] = set()
        firsts: set[str] = set()
        for name in _SCENARIOS:
            with pytest.MonkeyPatch.context() as mp:
                w = _bind(mp, World())
                _scenario(name, w, mp)
                for t in _txns(w.events):
                    seen.update(t)
                    firsts.add(t[0])
        writes = _CHILD_ONLY | _CHILD_AND_SOURCE | _SOURCE_STRONG | _CONN
        assert writes <= seen, sorted(writes - seen)
        assert {"guard", "lock_source", "lock_connection", "source"} <= firsts, firsts

    @pytest.mark.parametrize(("txn", "fault"), [
        # The close of KC-3: the run row, the source row, then the connection (K-22).
        (["finish", "source_ok", "mark_synced", "commit"], "after the source row"),
        # The page with removals only of KC-3: the nodes before the source (K-21).
        (["remove", "cascade", "upsert_cursor", "progress", "commit"], "before the source"),
        # A share lock first, then an UPDATE of the source row.
        (["guard", "upsert_changes", "last_new", "upsert_cursor", "progress"], "becomes an UPDATE"),
        # The page of KC-3 with an upsert: the guard of the upsert, then last_new_at.
        (["upsert_changes", "last_new", "upsert_cursor", "progress"], "becomes an UPDATE"),
        # A page with no lock before its first write.
        (["upsert_cursor", "progress"], "not a lock"),
        # The end of a discovery of KC-3: the run row before the source row.
        (["progress", "disc_finish", "upsert_cursor", "finish", "commit"], "before the source"),
        # A statement with no lock class.
        (["lock_source", "new_statement"], "no lock class"),
    ])
    def test_the_check_sees_each_old_order(self, txn: list[str], fault: str) -> None:
        faults = lock_order_faults(txn, engine_txn=True)
        assert any(fault in f for f in faults), faults

    def test_the_check_passes_the_new_order(self) -> None:
        assert lock_order_faults(["lock_connection", "lock_source", "finish", "source_ok",
                                  "mark_synced", "commit"], engine_txn=True) == []
        assert lock_order_faults(["guard", "remove", "cascade", "upsert_cursor", "progress",
                                  "commit"], engine_txn=True) == []


# ── The loop ────────────────────────────────────────────────────────────────


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _due(state: str = "ready", *, running: bool = False, finished: float | None = None,
         reconciled: float | None = 1.0) -> runs.DueRow:
    """A due row. *finished* and *reconciled* are minutes ago, or None."""
    def ago(m: float | None) -> datetime | None:
        return None if m is None else NOW - timedelta(minutes=m)
    return runs.DueRow(source_id=SOURCE, provider="google_drive", state=state, db_now=NOW,
                       last_finished=ago(finished), running=running,
                       reconciled_at=ago(reconciled))


class TestTheDueRule:
    @pytest.mark.parametrize("state", ["discovering", "failed", "needs_reconnect",
                                       "disconnected"])
    def test_a_source_that_is_not_ready_is_skipped(self, state: str) -> None:
        assert scheduler.due_kind(_due(state)) is None

    def test_a_fresh_lock_is_skipped(self) -> None:
        assert scheduler.due_kind(_due(running=True)) is None

    def test_the_15_minute_gap(self) -> None:
        assert scheduler.due_kind(_due(finished=14)) is None
        assert scheduler.due_kind(_due(finished=15)) == runs.KIND_CHANGES
        assert scheduler.due_kind(_due(finished=None)) == runs.KIND_CHANGES

    def test_the_daily_reconcile(self) -> None:
        assert scheduler.due_kind(_due(reconciled=None)) == runs.KIND_RECONCILE
        assert scheduler.due_kind(_due(reconciled=24 * 60 + 1)) == runs.KIND_RECONCILE
        assert scheduler.due_kind(_due(reconciled=23 * 60)) == runs.KIND_CHANGES
        assert scheduler.due_kind(_due(reconciled=None, finished=5)) is None

    def test_the_numbers(self) -> None:
        assert scheduler.TICK_SECONDS == 60
        assert scheduler.INTERVAL_SECONDS == 900
        assert scheduler.CONCURRENCY == 3
        assert scheduler.STOP_GRACE_SECONDS == 30
        assert runs.STALE_AFTER_SECONDS == 900


def _flags(monkeypatch: pytest.MonkeyPatch, sync: str, google: str = "*") -> None:
    settings = get_settings()
    for name, value in (("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_sync", sync),
                        ("knowledge_source_google", google)):
        monkeypatch.setattr(settings, name, value, raising=False)


@pytest.fixture()
def loop(monkeypatch: pytest.MonkeyPatch):
    """The loop with its org list, its sessions and its engine replaced."""
    sessions: list[str] = []
    ran: list[tuple[str, str, str]] = []
    rows: dict[str, list[runs.DueRow]] = {ORG: [_due()], OTHER: [_due()]}
    failing: set[str] = set()
    listed: list[int] = []

    async def orgs() -> list[str]:
        listed.append(1)
        return [ORG, OTHER]

    @contextlib.asynccontextmanager
    async def session(org: str):
        sessions.append(org)
        if org in failing:
            raise RuntimeError("down")
        yield SimpleNamespace(org=org)

    async def due_rows(db: Any, org: str) -> list[runs.DueRow]:
        assert db.org == org
        return rows[org]

    async def run_source(org: str, source_id: str, kind: str, **kw: Any) -> Any:
        ran.append((org, source_id, kind))
        if org in failing:
            raise RuntimeError("one source broke")
        return engine.RunOutcome(runs.STATUS_OK)

    monkeypatch.setattr(scheduler, "_organization_ids", orgs)
    monkeypatch.setattr(scheduler, "tenant_session", session)
    monkeypatch.setattr(runs, "due_rows", due_rows)
    monkeypatch.setattr(engine, "run_source", run_source)
    pruned: list[str] = []

    async def prune(db: Any, org: str, keep: int = runs.RUNS_KEPT) -> int:
        assert db.org == org, "the sweep ran in another org's session"
        pruned.append(org)
        if "prune" in failing:
            raise RuntimeError("the sweep broke")
        return 0

    monkeypatch.setattr(runs, "prune", prune)
    scheduler._stopping.clear()
    yield SimpleNamespace(sessions=sessions, ran=ran, rows=rows, failing=failing, listed=listed,
                          pruned=pruned)
    scheduler._stopping.clear()


def _adapter() -> Any:
    return SimpleNamespace(provider="google_drive")


class TestTheTick:
    def test_an_org_off_the_google_flag_gets_no_google_call(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*", google=OTHER)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 1
        assert loop.ran == [(OTHER, SOURCE, runs.KIND_CHANGES)]
        _flags(monkeypatch, "*", google="")
        loop.ran.clear()
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0 and loop.ran == []

    def test_a_provider_with_no_flag_is_off(self) -> None:
        from gateway.routes.knowledge import flags as kb_flags

        assert kb_flags.knowledge_provider_allowed(ORG, "onedrive") is False

    def test_the_retention_sweep_runs_for_each_named_org_in_its_session(
        self, monkeypatch, loop,
    ) -> None:
        _flags(monkeypatch, OTHER)
        asyncio.run(scheduler.run_tick(_adapter))
        assert loop.pruned == [OTHER]
        assert loop.sessions == [OTHER, OTHER], "the sweep shared the due read's session"

    def test_a_failed_sweep_does_not_stop_the_tick(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")
        loop.failing.add("prune")
        assert asyncio.run(scheduler.run_tick(_adapter)) == 2
        assert loop.pruned == [ORG, OTHER]

    def test_the_due_read_takes_the_newest_run_on_the_index(self) -> None:
        sql = runs._DUE_SQL
        assert "ORDER BY r.started_at DESC LIMIT 1" in sql and "max(" not in sql
        assert runs.RUNS_KEPT == 50

    def test_a_flag_off_org_opens_no_session(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, OTHER)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 1
        assert set(loop.sessions) == {OTHER} and loop.ran == [(OTHER, SOURCE, runs.KIND_CHANGES)]

    def test_no_org_named_lists_nothing_and_opens_nothing(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "")
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0
        assert loop.sessions == [] and loop.listed == []

    def test_one_failing_org_does_not_stop_the_next(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")
        loop.failing.add(ORG)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 1
        assert loop.ran == [(OTHER, SOURCE, runs.KIND_CHANGES)]

    def test_one_failing_source_does_not_stop_the_next(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")

        async def broken(org: str, source_id: str, kind: str, **kw: Any) -> Any:
            loop.ran.append((org, source_id, kind))
            if org == ORG:
                raise RuntimeError("one source broke")
            return engine.RunOutcome(runs.STATUS_OK)

        monkeypatch.setattr(engine, "run_source", broken)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 1
        assert len(loop.ran) == 2

    def test_a_lock_held_elsewhere_is_skipped(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")

        async def held(org: str, source_id: str, kind: str, **kw: Any) -> Any:
            raise runs.SyncRunning("held")

        monkeypatch.setattr(engine, "run_source", held)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0

    def test_no_due_source_builds_no_adapter(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")
        loop.rows[ORG] = [_due("failed")]
        loop.rows[OTHER] = [_due(running=True)]

        def never() -> Any:
            raise AssertionError("an adapter was built for no work")

        assert asyncio.run(scheduler.run_tick(never)) == 0

    def test_an_unset_google_client_skips_the_tick(self, monkeypatch, loop) -> None:
        from fastapi import HTTPException

        _flags(monkeypatch, "*")

        def unset() -> Any:
            raise HTTPException(status_code=503, detail="google_not_configured")

        assert asyncio.run(scheduler.run_tick(unset)) == 0
        assert loop.ran == []

    def test_another_provider_is_skipped(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, "*")
        assert asyncio.run(scheduler.run_tick(
            lambda: SimpleNamespace(provider="onedrive"))) == 0
        assert loop.ran == []

    def test_the_default_adapter_is_the_route_seam(self) -> None:
        import ast
        import inspect

        src = inspect.getsource(scheduler._default_adapter)
        names = {n.name for n in ast.walk(ast.parse(src.strip())) if isinstance(n, ast.alias)}
        assert names == {"google_client", "make_source"}


class TestTheSwitch:
    def test_the_flag_empty_starts_no_task(self, monkeypatch) -> None:
        _flags(monkeypatch, "")

        async def go() -> bool:
            started = await scheduler.start_kb_sync()
            assert not scheduler.loop_running()
            assert not [t for t in asyncio.all_tasks() if t.get_name() == "kb-sync"]
            return started

        assert asyncio.run(go()) is False

    def test_the_flag_set_starts_one_task_and_stop_ends_it(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, ORG)

        async def go() -> None:
            assert await scheduler.start_kb_sync(_adapter) is True
            assert await scheduler.start_kb_sync(_adapter) is True  # idempotent
            assert len([t for t in asyncio.all_tasks() if t.get_name() == "kb-sync"]) == 1
            await asyncio.sleep(0.05)
            await scheduler.stop_kb_sync()
            assert not scheduler.loop_running()

        asyncio.run(go())

    def test_stop_is_safe_when_never_started(self) -> None:
        asyncio.run(scheduler.stop_kb_sync())
        assert not scheduler.loop_running()

    def test_the_start_function_reads_the_flag(self) -> None:
        import ast

        tree = ast.parse(Path(scheduler.__file__).read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_kb_sync")
        called = {c.func.attr for c in ast.walk(start)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        assert "knowledge_sync_named" in called

    def test_main_calls_start_and_stop_and_reads_no_flag(self) -> None:
        """The gate lives inside the start function (``knowledge_base.md`` §7.1)."""
        main_src = MAIN.read_text(encoding="utf-8")
        before, sep, after = main_src.partition("\n    yield\n")
        assert sep, "lifespan yield not found"
        assert "await start_kb_sync()" in before
        assert "await stop_kb_sync()" in after
        assert "knowledge_sync" not in main_src
        # Beside the CRM mirror pair, as the spec places it.
        assert before.index("start_crm_mirror_sync") < before.index("start_kb_sync")
        assert after.index("stop_crm_mirror_sync") < after.index("stop_kb_sync")

    def test_only_the_loop_asks_the_flag_to_start(self) -> None:
        """``flags.py`` defines it, and the scheduler is its one caller."""
        readers = sorted(
            p.name for p in (ROOT / "apps").rglob("*.py")
            if "knowledge_sync_named" in p.read_text(encoding="utf-8", errors="ignore"))
        assert readers == ["flags.py", "scheduler.py"], readers
