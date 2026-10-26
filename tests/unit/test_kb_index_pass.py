"""WS-61 KC-5a — the index pass, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 4, §7.3, §16 and
§22 (KC-5a). No database and no network. The SQL is proved against a real
database in ``test_kc_index_r8.py``. This file proves the flow of
``kb_sync/index.py`` on a fake session and a fake adapter:

* each file is ONE short transaction (the guard, the node re-read, the document,
  the chunks, the heartbeat), and no provider call runs while a session is open;
* the size cap: a node over 25 MB is never fetched (acceptance A4);
* a permanent refusal (a 404, a suffix that nothing reads, a broken file) writes
  a refusal row and no chunk. A transient error writes no row;
* the Google adapter has no fetch yet: the run ends ``stopped`` with the code
  ``fetch_unavailable`` and writes no row;
* the budget: 50 files, 100 MB, 45 s, checked between files;
* a node whose stamp moved is skipped;
* a disconnect ends the run with no write;
* extraction runs in a worker thread, one at a time in the process;
* the flag: with ``KNOWLEDGE_INDEX`` empty the loop counts nothing, runs nothing
  and calls no provider (acceptance A8), and the due rule of the index pass;
* no file name and no word of a document reaches a log.

Mutations this file catches (R7): the size cap is skipped, a refusal row keeps
chunks, the provider call moves inside a session, the stamp check goes, the
semaphore goes, the flag check goes -> each fails here.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.kb_sync.index", reason="gateway not installed")

from acb_common import get_settings
from gateway.kb_sources.base import (
    FetchedFile,
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceError,
)
from gateway.kb_sync import access, discovery, engine, index, runs, scheduler
from gateway.routes.knowledge import core

ORG = "66666666-6666-4666-8666-666666666666"
SOURCE = "88888888-8888-4888-8888-888888888888"
CONNECTION = "99999999-9999-4999-8999-999999999999"
OWNER = "Ana@kc5.test"
SPACE = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
MODIFIED = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)

_NAMES = {
    engine._SOURCE_SQL: "source",
    runs._TAKEOVER_SQL: "takeover",
    runs._INSERT_SQL: "insert_run",
    runs._PROGRESS_SQL: "progress",
    runs._FINISH_SQL: "finish",
    discovery.LOCK_SOURCE_SHARE_SQL: "guard",
    discovery.LOCK_SOURCE_UPDATE_SQL: "lock_source",
    engine._SOURCE_ERROR_SQL: "source_error",
    engine._SOURCE_RECONNECT_SQL: "source_reconnect",
    index._DELETE_STALE_SQL: "delete_stale",
    index._PENDING_SQL: "pending",
    core.INSERT_ME_SPACE_SQL: "insert_me_space",
    core.SELECT_ME_SPACE_SQL: "select_me_space",
    index._NODE_NOW_SQL: "node_now",
    index._UPSERT_DOC_SQL: "upsert_doc",
    index._DELETE_CHUNKS_SQL: "delete_chunks",
    index._UPSERT_CHUNKS_SQL: "upsert_chunks",
}


class _Result:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


def _row(ext: str, *, size: int | None = 40, stamp: str | None = None,
         name: str | None = None) -> Any:
    return SimpleNamespace(
        id=f"node-{ext}", ext_id=ext, parent_ext_id="inc", name=name or f"{ext}.txt",
        mime="text/plain", size=size, modified_at=MODIFIED, web_url=None, drive_id=None,
        content_hash=f"h-{ext}", stamp=stamp or f"h-{ext}")


class World:
    """The fake database and the record of what happened, in order."""

    def __init__(self) -> None:
        self.open = 0
        self.events: list[tuple[str, Any]] = []
        self.pending: list[Any] = []
        self.state = "ready"
        self.disconnected = False
        self.lost = False
        self.moved: set[str] = set()
        self.docs: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, dict[str, Any]] = {}
        self.statuses: list[tuple[str, str | None]] = []
        self.run_kind: str | None = None
        self.plan_params: dict[str, Any] = {}
        self._txn: list[tuple[str, Any]] = []

    @contextlib.asynccontextmanager
    async def session(self, org: str):
        assert org == ORG, "a session bound another org"
        self.open += 1
        self.events.append(("open", None))
        self._txn = []
        try:
            yield _Db(self)
        except BaseException:
            self.open -= 1
            self.events.append(("rollback", None))
            self._txn = []
            raise
        self.open -= 1
        self.events.append(("commit", None))
        for kind, value in self._txn:
            if kind == "doc":
                self.docs[value["node"]] = value
            elif kind == "chunks":
                self.chunks[value["doc"]] = value

    def sql(self) -> list[str]:
        return [v for k, v in self.events if k == "sql"]

    def txns(self) -> list[list[str]]:
        out: list[list[str]] = []
        for kind, value in self.events:
            if kind == "open":
                out.append([])
            elif kind == "sql":
                out[-1].append(value)
            elif kind in ("commit", "rollback"):
                out[-1].append(kind)
        return out

    def fetches(self) -> list[str]:
        return [v for k, v in self.events if k == "fetch"]


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
        return getattr(self, f"_{name}")(p)

    def _source(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(id=SOURCE, provider="google_drive", owner_email=OWNER,
                                        state=self.w.state, connection_id=CONNECTION)])

    def _takeover(self, p: dict[str, Any]) -> _Result:
        return _Result()

    def _insert_run(self, p: dict[str, Any]) -> _Result:
        self.w.run_kind = p["kind"]
        return _Result([SimpleNamespace(id="run-1", started_at=NOW)])

    def _progress(self, p: dict[str, Any]) -> _Result:
        return _Result(rowcount=0 if self.w.lost else 1)

    def _finish(self, p: dict[str, Any]) -> _Result:
        self.w.statuses.append((p["status"], p["error"]))
        return _Result(rowcount=1)

    def _guard(self, p: dict[str, Any]) -> _Result:
        assert p["source"] == SOURCE, "the lock took another source"
        return _Result([] if self.w.disconnected else [SimpleNamespace(id=SOURCE)])

    _lock_source = _guard

    def _source_error(self, p: dict[str, Any]) -> _Result:
        return _Result(rowcount=1)

    _source_reconnect = _source_error

    def _delete_stale(self, p: dict[str, Any]) -> _Result:
        assert p["source"] == SOURCE and p["depth"] == index.DEPTH
        return _Result(rowcount=2)

    def _pending(self, p: dict[str, Any]) -> _Result:
        self.w.plan_params = dict(p)
        return _Result(list(self.w.pending))

    def _insert_me_space(self, p: dict[str, Any]) -> _Result:
        assert p["me"] == OWNER.lower(), "the space owner is not the source owner, lower case"
        return _Result()

    def _select_me_space(self, p: dict[str, Any]) -> _Result:
        return _Result([SimpleNamespace(id=SPACE)])

    def _node_now(self, p: dict[str, Any]) -> _Result:
        ext = str(p["node"]).removeprefix("node-")
        if ext in self.w.moved:
            return _Result([SimpleNamespace(id=p["node"], stamp="moved")])
        row = next(r for r in self.w.pending if r.id == p["node"])
        return _Result([SimpleNamespace(id=p["node"], stamp=row.stamp)])

    def _upsert_doc(self, p: dict[str, Any]) -> _Result:
        self.w._txn.append(("doc", dict(p)))
        return _Result([SimpleNamespace(id=f"doc-{p['node']}")])

    def _delete_chunks(self, p: dict[str, Any]) -> _Result:
        self.w._txn.append(("chunks", {"doc": p["doc"], "keep": list(p["shas"]), "paths": []}))
        return _Result(rowcount=0)

    def _upsert_chunks(self, p: dict[str, Any]) -> _Result:
        self.w._txn.append(("chunks", dict(p)))
        return _Result([SimpleNamespace(content_sha=s, inserted=True) for s in p["shas"]])


class Docs:
    """A fake adapter. It asserts that no session is open during a call."""

    provider = "google_drive"

    def __init__(self, world: World, files: dict[str, Any]) -> None:
        self.w, self.files = world, files

    async def fetch_document(self, cred: Any, node: Any) -> FetchedFile:
        assert self.w.open == 0, "fetch_document ran while a session was open"
        self.w.events.append(("fetch", node.ext_id))
        item = self.files[node.ext_id]
        if isinstance(item, BaseException):
            raise item
        data, suffix = item
        return FetchedFile(data=data, suffix=suffix)

    async def refresh(self, cred: Any) -> Any:
        return cred

    async def list_access(self, cred: Any, cursor: Any, *, budget: int) -> Any:
        raise AssertionError("the index pass read the access listing")

    async def discover(self, cred: Any, cursor: Any = None) -> Any:
        raise AssertionError("the index pass read the tree")

    async def list_changed(self, cred: Any, cursor: Any, folders: Any) -> Any:
        raise AssertionError("the index pass read the change feed")


def _cred() -> SourceCredential:
    return SourceCredential(access_token="at-1", refresh_token="rt-1",
                            expires_at=datetime.now(UTC) + timedelta(hours=1))


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()

    async def fake_cred(org: str, owner: str, source: Any, *, force: bool = False) -> Any:
        assert w.open == 0, "the credential was read inside an index session"
        assert owner == OWNER, "the member did not come from the source row"
        return _cred()

    for module in (index, engine, discovery):
        monkeypatch.setattr(module, "tenant_session", w.session)
    monkeypatch.setattr(discovery, "_credential", fake_cred)
    return w


def _run(adapter: Any, **kw: Any) -> engine.RunOutcome:
    return asyncio.run(index.run_index(ORG, SOURCE, source=adapter, **kw))


def _sc(code: str) -> SourceError:
    exc = SourceError(code)
    exc.code = code
    return exc


# ── The flow of one pass ────────────────────────────────────────────────────

class TestOnePass:
    def test_each_file_is_one_short_transaction_with_the_guard_first(self, world) -> None:
        world.pending = [_row("a"), _row("b")]
        out = _run(Docs(world, {"a": (b"Alpha text", ".txt"), "b": (b"Beta text", ".txt")}))
        assert out.status == runs.STATUS_OK and world.run_kind == runs.KIND_INDEX, out
        txns = world.txns()
        assert txns[0] == ["source", "takeover", "insert_run", "commit"]
        assert txns[1] == ["guard", "delete_stale", "progress", "commit"]
        assert txns[2] == ["guard", "pending", "insert_me_space", "select_me_space",
                           "progress", "commit"]
        file_txn = ["guard", "node_now", "upsert_doc", "delete_chunks", "upsert_chunks",
                    "progress", "commit"]
        assert txns[3:5] == [file_txn, file_txn]
        assert txns[5] == ["finish", "commit"], "the close of a pass took a source lock"
        assert len(txns) == 6

    def test_the_document_holds_the_provider_stamp_and_the_chunks_the_owner_space(
        self, world,
    ) -> None:
        world.pending = [_row("a", stamp="STAMP-1")]
        _run(Docs(world, {"a": (b"Alpha text", ".txt")}))
        doc = world.docs["node-a"]
        assert (doc["sha"], doc["parser"], doc["text"], doc["truncated"]) == (
            "STAMP-1", "txt", "Alpha text", False)
        chunks = world.chunks["doc-node-a"]
        assert chunks["space"] == SPACE and chunks["paths"] == ["a.txt"]
        assert chunks["valid_from"] == MODIFIED.date()
        assert world.plan_params["n"] == index.MAX_FILES == 50

    def test_nothing_pending_makes_no_space_and_no_fetch(self, world) -> None:
        out = _run(Docs(world, {}))
        assert out.status == runs.STATUS_OK and world.fetches() == []
        assert "insert_me_space" not in world.sql()

    def test_a_deck_file_chunks_by_slide(self, world) -> None:
        from tests.unit.test_attachment_formats import _pptx

        world.pending = [_row("d", name="Pitch.pptx")]
        _run(Docs(world, {"d": (_pptx("First", "Second"), ".pptx")}))
        chunks = world.chunks["doc-node-d"]
        assert chunks["paths"] == ["Pitch.pptx > Slide 1", "Pitch.pptx > Slide 2"]
        assert world.docs["node-d"]["parser"] == "pptx"

    def test_no_provider_call_runs_with_a_session_open(self, world) -> None:
        world.pending = [_row("a")]
        # Docs.fetch_document and the fake credential assert it.
        assert _run(Docs(world, {"a": (b"x y", ".txt")})).status == runs.STATUS_OK

    def test_the_pass_reads_only_the_documents(self, world) -> None:
        world.pending = [_row("a")]
        _run(Docs(world, {"a": (b"x y", ".txt")}))  # Docs raises on any other read


# ── The refusals ────────────────────────────────────────────────────────────

class TestPermanentRefusals:
    def _only_a_refusal(self, world: World, ext: str = "a", alone: bool = True) -> None:
        doc = world.docs[f"node-{ext}"]
        assert (doc["parser"], doc["text"], doc["truncated"]) == ("none", "", True), doc
        assert doc["sha"] == f"h-{ext}", "the refusal row keeps the stamp, so it is not pending"
        assert world.chunks[f"doc-node-{ext}"]["keep"] == [], "a refusal row kept a chunk"
        assert not alone or "upsert_chunks" not in world.sql()

    def test_a_node_over_25_mb_is_never_fetched(self, world) -> None:
        world.pending = [_row("a", size=25 * 1024 * 1024 + 1)]
        out = _run(Docs(world, {}))
        assert out.status == runs.STATUS_OK and world.fetches() == []
        self._only_a_refusal(world)
        assert out.counts["refused"] == 1 and index.MAX_FETCH_BYTES == 25 * 1024 * 1024

    def test_a_node_of_exactly_25_mb_is_fetched(self, world) -> None:
        world.pending = [_row("a", size=25 * 1024 * 1024)]
        _run(Docs(world, {"a": (b"small", ".txt")}))
        assert world.fetches() == ["a"] and world.docs["node-a"]["parser"] == "txt"

    @pytest.mark.parametrize("code", ["http_403", "http_404"])
    def test_a_403_or_a_404_on_the_file_is_a_refusal(self, world, code: str) -> None:
        world.pending = [_row("a"), _row("b")]
        out = _run(Docs(world, {"a": _sc(code), "b": (b"fine", ".txt")}))
        assert out.status == runs.STATUS_OK
        self._only_a_refusal(world, alone=False)
        assert world.docs["node-b"]["parser"] == "txt", "one refusal stopped the rest"

    def test_a_suffix_that_nothing_reads_is_a_refusal(self, world) -> None:
        world.pending = [_row("a")]
        _run(Docs(world, {"a": (b"MZ....", ".exe")}))
        self._only_a_refusal(world)

    def test_a_file_that_does_not_parse_is_a_refusal(self, world) -> None:
        world.pending = [_row("a")]
        _run(Docs(world, {"a": (b"this is not a zip", ".docx")}))
        self._only_a_refusal(world)

    def test_a_parser_bug_is_a_refusal_and_not_a_stalled_source(
        self, world, monkeypatch,
    ) -> None:
        def boom(*a: Any) -> Any:
            raise RuntimeError("parser bug")

        monkeypatch.setattr(index, "_read_file", boom)
        world.pending = [_row("a"), _row("b")]
        out = _run(Docs(world, {"a": (b"x", ".txt"), "b": (b"y", ".txt")}))
        assert out.status == runs.STATUS_OK and out.counts["refused"] == 2

    def test_the_wait_for_a_worker_that_is_too_slow_is_a_refusal(
        self, world, monkeypatch,
    ) -> None:
        def slow(*a: Any) -> Any:
            time.sleep(0.4)

        monkeypatch.setattr(index, "_read_file", slow)
        monkeypatch.setattr(index, "EXTRACT_WAIT_SECONDS", 0.05)
        world.pending = [_row("a")]
        assert _run(Docs(world, {"a": (b"x", ".txt")})).status == runs.STATUS_OK
        self._only_a_refusal(world)


class TestTransientErrors:
    def test_a_rate_limit_writes_no_row_and_stops_the_run(self, world) -> None:
        world.pending = [_row("a"), _row("b")]
        out = _run(Docs(world, {"a": (b"kept", ".txt"), "b": RateLimited("slow", tries=4)}))
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "rate_limited")
        assert set(world.docs) == {"node-a"}, "the file after the rate limit left a row"
        assert "source_error" not in world.sql(), "an index error marked the source"

    def test_a_network_error_writes_no_row_and_fails_the_run(self, world) -> None:
        world.pending = [_row("a")]
        out = _run(Docs(world, {"a": _sc("http_500")}))
        assert (out.status, out.error) == (runs.STATUS_FAILED, "http_500")
        assert world.docs == {}

    def test_a_dead_refresh_token_takes_the_reconnect_path(self, world) -> None:
        exc = NeedsReconnect("dead")
        exc.code = "invalid_grant"
        world.pending = [_row("a")]
        out = _run(Docs(world, {"a": exc}))
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "invalid_grant")
        assert "source_reconnect" in world.sql() and world.docs == {}

    def test_the_google_adapter_has_no_fetch_yet(self, world) -> None:
        from gateway.kb_sources.google_drive.client import GoogleDriveSource

        class Real(GoogleDriveSource):
            def __init__(self) -> None:  # no client: the fetch must fail first
                pass

        world.pending = [_row("a")]
        out = _run(Real())
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "fetch_unavailable")
        assert world.docs == {} and "source_error" not in world.sql()
        with pytest.raises(NotImplementedError, match="KC-5b"):
            asyncio.run(Real().fetch_document(_cred(), SimpleNamespace()))

    def test_a_disconnect_ends_the_run_with_no_write(self, world) -> None:
        world.pending = [_row("a")]
        world.disconnected = True
        out = _run(Docs(world, {"a": (b"x", ".txt")}))
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "disconnected")
        assert world.docs == {} and world.fetches() == []
        assert "delete_stale" not in world.sql()

    def test_a_run_that_lost_its_lock_writes_nothing(self, world) -> None:
        world.pending = [_row("a")]
        world.lost = True
        out = _run(Docs(world, {"a": (b"x", ".txt")}))
        assert out.status == "lost" and world.docs == {}

    def test_the_gateway_stopping_ends_the_run_stopped(self, world) -> None:
        world.pending = [_row("a")]
        out = _run(Docs(world, {"a": (b"x", ".txt")}), should_stop=lambda: True)
        assert (out.status, out.error) == (runs.STATUS_STOPPED, "shutdown")
        assert world.fetches() == []

    def test_a_source_that_is_not_ready_runs_nothing(self, world) -> None:
        world.state = "needs_reconnect"
        out = _run(Docs(world, {}))
        assert (out.status, out.error) == ("skipped", "not_ready")
        assert world.sql() == ["source"]


# ── The budget, the stamp and the gate ──────────────────────────────────────

class TestBudget:
    def test_the_bytes_budget_stops_between_files_and_the_run_is_ok(
        self, world, monkeypatch,
    ) -> None:
        monkeypatch.setattr(index, "MAX_BYTES", 10)
        world.pending = [_row("a"), _row("b"), _row("c")]
        out = _run(Docs(world, {k: (b"0123456789ab", ".txt") for k in "abc"}))
        assert out.status == runs.STATUS_OK and out.counts["budget"] is True
        assert world.fetches() == ["a"]

    def test_the_time_budget_stops_between_files(self, world, monkeypatch) -> None:
        clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        monkeypatch.setattr(index, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        world.pending = [_row("a"), _row("b")]
        out = _run(Docs(world, {k: (b"x y", ".txt") for k in "ab"}))
        assert out.counts["budget"] is True and world.fetches() == ["a"]
        assert index.MAX_SECONDS == 45.0 and index.MAX_BYTES == 100 * 1024 * 1024

    def test_a_node_that_moved_is_skipped_and_not_written(self, world) -> None:
        world.pending = [_row("a"), _row("b")]
        world.moved = {"a"}
        out = _run(Docs(world, {k: (b"x y", ".txt") for k in "ab"}))
        assert out.counts["moved"] == 1 and set(world.docs) == {"node-b"}
        assert "upsert_doc" in world.sql()
        skip = next(t for t in world.txns() if "node_now" in t)
        assert skip == ["guard", "node_now", "progress", "commit"]


class TestExtractionGate:
    def test_extraction_runs_in_a_worker_thread_one_at_a_time(self, monkeypatch) -> None:
        seen: list[int] = []
        live = {"now": 0, "max": 0}
        lock = threading.Lock()

        def fake(data: bytes, suffix: str, name: str) -> Any:
            with lock:
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
            seen.append(threading.get_ident())
            time.sleep(0.05)
            with lock:
                live["now"] -= 1
            return "done"

        monkeypatch.setattr(index, "_read_file", fake)

        async def go() -> list[Any]:
            return await asyncio.gather(*(index._extract(b"", ".txt", "n") for _ in range(4)))

        assert asyncio.run(go()) == ["done"] * 4
        assert live["max"] == 1, "two extractions ran at the same time"
        assert threading.get_ident() not in seen, "extraction ran on the event loop thread"

    def test_the_gate_belongs_to_the_running_loop(self) -> None:
        async def gate() -> Any:
            return index._gate()

        first, second = asyncio.run(gate()), asyncio.run(gate())
        assert first is not second, "a dead loop's gate was reused"


class TestLogs:
    def test_no_file_name_and_no_word_of_a_document_reaches_a_log(self, world, capsys) -> None:
        world.pending = [_row("a", name="Secret merger plan.txt"), _row("b", size=10**9)]
        _run(Docs(world, {"a": (b"Project Falcon is the codename", ".txt")}))
        captured = capsys.readouterr()
        logged = captured.out + captured.err
        assert "knowledge.index_ok" in logged
        for word in ("Secret merger", "Falcon", "codename", "h-a"):
            assert word not in logged, word


# ── The loop: the flag, the count and the due rule ──────────────────────────

def _due(*, feed: float | None = 5, read: float | None = 1, pending: int | None = 3,
         finished: float | None = None, status: str | None = None,
         state: str = "ready", running: bool = False) -> runs.DueRow:
    """A due row. Each time is minutes ago. By default the feed ran 5 minutes
    ago and the access pass is fresh, so only the index pass can be due."""
    def ago(m: float | None) -> datetime | None:
        return None if m is None else NOW - timedelta(minutes=m)
    return runs.DueRow(
        source_id=SOURCE, provider="google_drive", state=state, db_now=NOW,
        last_finished=ago(feed), running=running, reconciled_at=ago(1),
        discovered_at=None, access_read_at=ago(read), access_finished=None,
        access_status=None, index_finished=ago(finished), index_status=status,
        index_pending=pending)


class TestTheDueRule:
    def test_the_index_pass_is_due_between_two_feed_reads(self) -> None:
        assert scheduler.due_kind(_due()) == runs.KIND_INDEX

    def test_the_change_feed_comes_before_the_index_pass(self) -> None:
        assert scheduler.due_kind(_due(feed=15)) == runs.KIND_CHANGES
        assert scheduler.due_kind(_due(feed=None)) == runs.KIND_CHANGES

    def test_the_access_pass_comes_before_the_index_pass(self) -> None:
        assert scheduler.due_kind(_due(read=None)) == runs.KIND_ACCESS

    @pytest.mark.parametrize("pending", [None, 0, -1])
    def test_nothing_pending_or_not_counted_is_not_due(self, pending: int | None) -> None:
        assert scheduler.due_kind(_due(pending=pending)) is None

    @pytest.mark.parametrize("state", ["discovering", "failed", "needs_reconnect",
                                       "disconnected"])
    def test_a_source_that_is_not_ready_is_skipped(self, state: str) -> None:
        assert scheduler.due_kind(_due(state=state)) is None

    def test_a_fresh_lock_is_skipped(self) -> None:
        assert scheduler.due_kind(_due(running=True)) is None

    def test_a_run_at_its_budget_goes_on_and_a_failure_waits_15_minutes(self) -> None:
        assert scheduler.due_kind(_due(finished=1, status=runs.STATUS_OK)) == runs.KIND_INDEX
        assert scheduler.due_kind(_due(finished=14, status=runs.STATUS_FAILED)) is None
        assert scheduler.due_kind(_due(finished=14, status=runs.STATUS_STOPPED)) is None
        assert scheduler.due_kind(_due(finished=15, status=runs.STATUS_FAILED)) == (
            runs.KIND_INDEX)
        assert scheduler.due_kind(_due(finished=15, status=runs.STATUS_STOPPED)) == (
            runs.KIND_INDEX)
        assert scheduler.INDEX_RETRY_SECONDS == 900


@pytest.fixture()
def loop(monkeypatch: pytest.MonkeyPatch):
    """The loop with its sessions, its rows, its count and its runs replaced."""
    sessions: list[str] = []
    counted: list[str] = []
    ran: list[tuple[str, str, str]] = []
    rows = {"list": [_due(pending=None)]}

    async def orgs() -> list[str]:
        return [ORG]

    @contextlib.asynccontextmanager
    async def session(org: str):
        sessions.append(org)
        yield SimpleNamespace(org=org)

    async def due_rows(db: Any, org: str) -> list[runs.DueRow]:
        return rows["list"]

    async def pending_count(db: Any, org: str, source_id: str) -> int:
        counted.append(source_id)
        return 3

    async def run_index(org: str, source_id: str, **kw: Any) -> Any:
        ran.append((org, source_id, runs.KIND_INDEX))
        return engine.RunOutcome(runs.STATUS_OK)

    async def prune(db: Any, org: str, keep: int = runs.RUNS_KEPT) -> int:
        return 0

    monkeypatch.setattr(scheduler, "_organization_ids", orgs)
    monkeypatch.setattr(scheduler, "tenant_session", session)
    monkeypatch.setattr(runs, "due_rows", due_rows)
    async def other(org: str, source_id: str, *args: Any, **kw: Any) -> Any:
        ran.append((org, source_id, "other"))
        return engine.RunOutcome(runs.STATUS_OK)

    async def other_kind(org: str, source_id: str, kind: str, **kw: Any) -> Any:
        ran.append((org, source_id, kind))
        return engine.RunOutcome(runs.STATUS_OK)

    monkeypatch.setattr(runs, "prune", prune)
    monkeypatch.setattr(access, "run_access", other)
    monkeypatch.setattr(engine, "run_source", other_kind)
    monkeypatch.setattr(index, "pending_count", pending_count)
    monkeypatch.setattr(index, "run_index", run_index)
    scheduler._stopping.clear()
    yield SimpleNamespace(sessions=sessions, counted=counted, ran=ran, rows=rows)
    scheduler._stopping.clear()


def _flags(monkeypatch: pytest.MonkeyPatch, *, index_orgs: str, sync: str = "*",
           google: str = "*") -> None:
    settings = get_settings()
    for name, value in (("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("knowledge_sources", "*"), ("knowledge_sync", sync),
                        ("knowledge_source_google", google),
                        ("knowledge_index", index_orgs)):
        monkeypatch.setattr(settings, name, value, raising=False)


def _adapter() -> Any:
    return SimpleNamespace(provider="google_drive")


class TestTheFlag:
    def test_with_the_flag_empty_nothing_is_counted_run_or_called(
        self, monkeypatch, loop,
    ) -> None:
        """A8: no index run, no provider call, and no session of the index."""
        _flags(monkeypatch, index_orgs="")
        built: list[int] = []

        def make() -> Any:
            built.append(1)
            return _adapter()

        assert asyncio.run(scheduler.run_tick(make)) == 0
        assert loop.counted == [] and loop.ran == [] and built == []
        # The sync loop's own sessions: the sweep and the due read, as before.
        assert loop.sessions == [ORG, ORG]

    def test_an_org_that_the_flag_names_is_counted_and_runs(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, index_orgs=ORG)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 1
        assert loop.counted == [SOURCE] and loop.ran == [(ORG, SOURCE, runs.KIND_INDEX)]

    def test_an_org_the_flag_does_not_name_is_not_counted(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, index_orgs="77777777-7777-4777-8777-777777777777")
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0
        assert loop.counted == [] and loop.ran == []

    def test_the_provider_flag_gates_the_count_too(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, index_orgs="*", google="")
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0
        assert loop.counted == [] and loop.ran == []

    def test_the_count_runs_only_for_a_source_that_could_run_a_pass(
        self, monkeypatch, loop,
    ) -> None:
        _flags(monkeypatch, index_orgs="*")
        loop.rows["list"] = [
            _due(pending=None, feed=15),          # the feed is due
            _due(pending=None, read=None),        # the access pass is due
            _due(pending=None, state="failed"),   # not ready
            _due(pending=None, running=True),     # locked
            _due(pending=None, finished=3, status=runs.STATUS_FAILED),  # waiting
        ]
        assert asyncio.run(scheduler.run_tick(_adapter)) == 2  # the feed and the access pass
        assert loop.counted == [] and (ORG, SOURCE, runs.KIND_INDEX) not in loop.ran

    def test_a_count_of_zero_runs_nothing(self, monkeypatch, loop) -> None:
        _flags(monkeypatch, index_orgs="*")

        async def zero(db: Any, org: str, source_id: str) -> int:
            loop.counted.append(source_id)
            return 0

        monkeypatch.setattr(index, "pending_count", zero)
        assert asyncio.run(scheduler.run_tick(_adapter)) == 0
        assert loop.counted == [SOURCE] and loop.ran == []


def test_the_pending_rows_replace_into_a_due_row_and_leave_the_old_one() -> None:
    row = _due(pending=None)
    assert dataclasses.replace(row, index_pending=2).index_pending == 2
    assert row.index_pending is None
