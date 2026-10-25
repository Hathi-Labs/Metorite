"""R8 — the Knowledge sync loop on a real database (WS-61 KC-3).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 steps 5 and 7, §16,
§19 and the KC-3 row of §22. ``knowledge_base.md`` §7.1.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (no superuser, no BYPASSRLS), on a database with the whole
ladder, the four generated phases and two organizations. It reuses the
fixtures of ``test_kc_sources_r8.py``: a source is connected through the real
routes, and discovery runs on the fake Google there. The change feed is
scripted as Google's own JSON, and :class:`Feed` counts each provider call.

The nine acceptance items of the KC-3 row:

1. a rename or a move keeps the node id, and a renamed folder keeps its child;
2. a removal and a trash set ``gone`` in one change read, and the reconcile
   marks a vanished node ``gone`` and leaves a seen node live;
3. a revoked token sets ``needs_reconnect`` on the source and on the
   connection, and the next tick makes no provider call;
4. two runs at once make one provider read, and the second raises
   ``SyncRunning`` on the real partial unique index. A stale run is taken
   over, and its next write raises ``RunLost``;
5. ``RateLimited`` on page 2 leaves page 1 and the cursor on page 2, and the
   next run reads page 2;
6. a failure in the cursor write rolls the page back;
7. an expired cursor takes a new token, then relists and compares hashes;
8. org B reads, writes and runs nothing of org A, and both tables FORCE RLS;
9. (hermetic, ``test_kb_sync_engine.py``) the flag empty starts no task.

And the edges of the build: a source with no cursor takes a token first,
discovery stores a token taken before its listing in the transaction that
sets ``ready``, a capped source skips a change inside an unexplored folder,
a disconnect wins the race with a page and with a cursor write, and the
migration replays with no change.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _app_dsn,
    _assert_non_priv,
    _call,
    _ok,
    _principal,
    ns,
)
from tests.unit.test_kc_sources_r8 import FakeGoogle, _connect, _tree, kc  # noqa: F401

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
FOLDER = "application/vnd.google-apps.folder"


def _sync_migration() -> Path:
    """The migration that creates ``kb_sync_runs``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS kb_sync_runs" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


class Feed:
    """The real Google adapter on the fake Google of ``test_kc_sources_r8``
    for the tree, a scripted change feed, and a counter of each call."""

    provider = "google_drive"

    def __init__(self, google: FakeGoogle) -> None:
        from gateway.kb_sources.google_drive import GoogleDriveSource, auth

        self.google = google
        self.inner = GoogleDriveSource(auth.OAuthClientConfig(
            client_id="cid.apps.googleusercontent.com", client_secret="r8-secret",
            redirect_uri="https://app.test/cb"), http=google)
        #: A page token, and the Google JSON of its page or an exception.
        self.pages: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []
        self.starts = 0
        self.refresh_error: BaseException | None = None
        self.gate: asyncio.Event | None = None

    async def refresh(self, cred: Any) -> Any:
        self.calls.append(("refresh", None))
        if self.refresh_error is not None:
            raise self.refresh_error
        return await self.inner.refresh(cred)

    async def discover(self, cred: Any, cursor: Any = None) -> Any:
        self.calls.append(("discover", getattr(cursor, "phase", "root")))
        return await self.inner.discover(cred, cursor)

    async def changes_start_token(self, cred: Any) -> str:
        self.starts += 1
        self.calls.append(("start", None))
        return f"fresh-{self.starts}"

    async def list_changed(self, cred: Any, cursor: Any, folders: Any) -> Any:
        from gateway.kb_sources.google_drive.client import change_page

        self.calls.append(("list_changed", cursor))
        if self.gate is not None:
            await self.gate.wait()
        item = self.pages[cursor]
        if isinstance(item, BaseException):
            raise item
        return change_page(item)

    async def fetch_text(self, cred: Any, node: Any) -> str:
        raise AssertionError("KC-3 read the text of a document")

    async def fetch_bytes(self, cred: Any, node: Any) -> bytes:
        raise AssertionError("KC-3 read the bytes of a document")

    def listed(self) -> list[Any]:
        return [c for verb, c in self.calls if verb == "list_changed"]


def _ch(fid: str, **file: Any) -> dict[str, Any]:
    return {"changeType": "file", "fileId": fid, "removed": False, "file": {"id": fid, **file}}


def _rm(fid: str) -> dict[str, Any]:
    return {"changeType": "file", "fileId": fid, "removed": True}


def _trash(fid: str) -> dict[str, Any]:
    return {"changeType": "file", "fileId": fid, "removed": False,
            "file": {"id": fid, "name": "x", "trashed": True}}


async def _run(p: Any, sid: str, kind: str, feed: Feed, org: str | None = None) -> Any:
    from gateway.kb_sync import engine

    async with tenant_engine_scope(_app_dsn(p)):
        return await engine.run_source(org or p.org_a, sid, kind, source=feed)


async def _source(p: Any, account: str, files: list[dict[str, Any]] | None = None,
                  ) -> tuple[Any, str, Feed]:
    """Connect Drive for a new member of org A. Returns the member, the source
    and the feed."""
    ana = _principal(_address("ana"), p.org_a)
    sid = await _connect(p, ana, email=f"ana-{account.lower()}@kc3-g.test",
                         account_id=account, files=files)
    return ana, sid, Feed(p.fakes[ana.email])


def _nodes(p: Any, sid: str) -> dict[str, Any]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id, ext_id, name, parent_ext_id, state, content_hash, explored "
            "FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)"), {"s": sid}).fetchall()
    return {r.ext_id: r for r in rows}


def _cursor(p: Any, sid: str, kind: str = "changes") -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT cursor_state, last_status, last_run_at FROM kb_sync_cursors "
            "WHERE source_id = CAST(:s AS uuid) AND kind = :k"), {"s": sid, "k": kind}).first()


def _runs(p: Any, sid: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, kind, status, error, counts FROM kb_sync_runs "
            "WHERE source_id = CAST(:s AS uuid) ORDER BY started_at, id"), {"s": sid}).fetchall()


def _src(p: Any, sid: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT s.state, s.last_error, s.last_new_at, s.node_count, s.discovered_at, "
            "ic.status AS conn_status, ic.last_sync_at FROM kb_sources s "
            "JOIN integration_connections ic ON ic.id = s.connection_id "
            "WHERE s.id = CAST(:s AS uuid)"), {"s": sid}).one()


def _token(row: Any) -> str | None:
    state = row.cursor_state if row is not None else None
    if isinstance(state, str):
        state = json.loads(state)
    return (state or {}).get("page_token")


# ── 1. A rename or a move keeps the id ──────────────────────────────────────

async def test_a_rename_keeps_the_id_and_every_child(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    ana, sid, feed = await _source(kc, "S1")
    before = _nodes(kc, sid)
    feed.pages["spt-S1"] = {"newStartPageToken": "spt-S1b", "changes": [
        _ch("fold-S1", name="Agreements", mimeType=FOLDER, parents=["root-S1"])]}
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok", out
    after = _nodes(kc, sid)
    assert after["fold-S1"].id == before["fold-S1"].id
    assert after["fold-S1"].name == "Agreements"
    assert after["file-S1"].id == before["file-S1"].id
    assert (after["file-S1"].parent_ext_id, after["file-S1"].state) == ("fold-S1", "live")
    kids = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=fold-S1")
    assert [n["name"] for n in kids["nodes"]] == ["MSA.pdf"]
    assert _token(_cursor(kc, sid)) == "spt-S1b"
    assert len(after) == len(before), "a rename added or lost a node"


async def test_a_move_updates_the_parent_and_keeps_the_id(kc, app_engine) -> None:  # noqa: F811
    ana, sid, feed = await _source(kc, "S2")
    before = _nodes(kc, sid)
    feed.pages["spt-S2"] = {"newStartPageToken": "spt-S2b", "changes": [
        _ch("file-S2", name="MSA.pdf", mimeType="application/pdf", parents=["sd-S2"],
            driveId="sd-S2")]}
    assert (await _run(kc, sid, "changes", feed)).status == "ok"
    moved = _nodes(kc, sid)["file-S2"]
    assert moved.id == before["file-S2"].id and moved.parent_ext_id == "sd-S2"
    old = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=fold-S2")
    assert old["nodes"] == []
    new = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=sd-S2")
    assert {n["ext_id"] for n in new["nodes"]} == {"deck-S2", "file-S2"}


# ── 2. A removal, a trash and the reconcile set gone ────────────────────────

async def test_a_removal_and_a_trash_set_gone_and_keep_the_row(kc, app_engine) -> None:  # noqa: F811
    ana, sid, feed = await _source(kc, "S3")
    before = _nodes(kc, sid)
    feed.pages["spt-S3"] = {"newStartPageToken": "spt-S3b",
                            "changes": [_rm("deck-S3"), _trash("file-S3")]}
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok" and out.counts["gone"] == 2, out
    after = _nodes(kc, sid)
    for ext in ("deck-S3", "file-S3"):
        assert after[ext].state == "gone" and after[ext].id == before[ext].id
    assert _src(kc, sid).node_count == 4
    shown = await _ok(kc, ana, "GET", f"/knowledge/sources/{sid}/tree?parent=fold-S3")
    assert shown["nodes"] == []


async def test_the_reconcile_marks_a_vanished_node_gone(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    _ana, sid, feed = await _source(kc, "S4")
    before = _nodes(kc, sid)
    reconciled = _cursor(kc, sid, "reconcile").last_run_at
    feed.google.files = [f for f in feed.google.files if f["id"] != "deck-S4"]
    out = await _run(kc, sid, "reconcile", feed)
    assert out.status == "ok", out
    after = _nodes(kc, sid)
    assert after["deck-S4"].state == "gone"
    seen = {k for k, v in after.items() if v.state == "live"}
    assert seen == set(before) - {"deck-S4"}
    assert all(after[k].id == before[k].id for k in before), "the relist changed an id"
    assert [v for v, _ in feed.calls if v == "list_changed"] == []
    assert _cursor(kc, sid, "reconcile").last_run_at > reconciled
    assert _token(_cursor(kc, sid)) == "spt-S4", "the reconcile moved the change cursor"


# ── 3. A revoked token stops the loop for the source ────────────────────────

async def test_a_revoked_token_sets_needs_reconnect_and_the_next_tick_calls_nothing(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings
    from gateway.kb_sources.base import NeedsReconnect, SourceError
    from gateway.kb_sync import scheduler

    monkeypatch.setattr(get_settings(), "knowledge_sync", "*", raising=False)
    scheduler._stopping.clear()
    _ana, sid, feed = await _source(kc, "S5")
    refused = SourceError("Google refused the Drive token")
    refused.code = "unauthorized"
    feed.pages["spt-S5"] = refused
    dead = NeedsReconnect("Google refused the token call (invalid_grant)")
    dead.code = "invalid_grant"
    feed.refresh_error = dead
    async with tenant_engine_scope(_app_dsn(kc)):
        assert await scheduler.run_tick(lambda: feed) == 1
    row = _src(kc, sid)
    assert (row.state, row.conn_status) == ("needs_reconnect", "needs_reconnect")
    assert row.last_error == "invalid_grant"
    assert [v for v, _ in feed.calls] == ["list_changed", "refresh"]
    [*_, last] = _runs(kc, sid)
    assert (last.kind, last.status) == ("changes", "stopped")
    made = len(feed.calls)
    async with tenant_engine_scope(_app_dsn(kc)):
        assert await scheduler.run_tick(lambda: feed) == 0
        assert await scheduler.run_tick(lambda: feed) == 0
    assert len(feed.calls) == made, "a tick called Google for a source that needs a reconnect"


async def test_the_loop_reads_a_ready_source_once_in_15_minutes(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from acb_common import get_settings
    from gateway.kb_sync import scheduler

    monkeypatch.setattr(get_settings(), "knowledge_sync", "*", raising=False)
    scheduler._stopping.clear()
    _ana, sid, feed = await _source(kc, "S5b")
    feed.pages["spt-S5b"] = {"newStartPageToken": "spt-S5c", "changes": []}
    async with tenant_engine_scope(_app_dsn(kc)):
        assert await scheduler.run_tick(lambda: feed) == 1
        assert await scheduler.run_tick(lambda: feed) == 0
    assert feed.listed() == ["spt-S5b"]
    row = _src(kc, sid)
    assert row.last_sync_at is not None and row.last_error is None


# ── 4. The run lock ─────────────────────────────────────────────────────────

async def test_two_runs_at_once_make_one_provider_read(kc, app_engine) -> None:  # noqa: F811
    from gateway.kb_sync import engine, runs

    _assert_non_priv(app_engine)
    _ana, sid, feed = await _source(kc, "S6")
    feed.pages["spt-S6"] = {"newStartPageToken": "spt-S6b", "changes": []}
    feed.gate = asyncio.Event()

    async def release() -> None:
        await asyncio.sleep(1.5)
        assert feed.gate is not None
        feed.gate.set()

    async with tenant_engine_scope(_app_dsn(kc)):
        got = await asyncio.gather(
            engine.run_source(kc.org_a, sid, "changes", source=feed),
            engine.run_source(kc.org_a, sid, "changes", source=feed),
            release(), return_exceptions=True)
    outcomes = got[:2]
    assert sum(isinstance(o, runs.SyncRunning) for o in outcomes) == 1, outcomes
    assert [o.status for o in outcomes if not isinstance(o, BaseException)] == ["ok"]
    assert feed.listed() == ["spt-S6"], "two runs read the feed of one source"
    assert [r.kind for r in _runs(kc, sid)] == ["discover", "changes"]


def test_the_partial_unique_index_allows_one_running_row(kc, app_engine) -> None:  # noqa: F811
    async def go() -> str:
        return (await _source(kc, "S6c"))[1]

    sid = asyncio.run(go())
    insert = text("INSERT INTO kb_sync_runs (organization_id, source_id, kind) "
                  "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'changes')")
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        c.execute(insert, {"o": kc.org_a, "s": sid})
    with pytest.raises(DBAPIError, match="kb_sync_runs_one_running_idx"), \
            app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": kc.org_a})
        c.execute(insert, {"o": kc.org_a, "s": sid})


async def test_a_stale_run_is_taken_over_and_cannot_write(kc, app_engine) -> None:  # noqa: F811
    from gateway.db import tenant_session
    from gateway.kb_sync import runs

    _ana, sid, feed = await _source(kc, "S7")
    feed.pages["spt-S7"] = {"newStartPageToken": "spt-S7b", "changes": []}
    with kc.admin_engine.begin() as c:
        old = str(c.execute(text(
            "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, started_at, "
            "heartbeat_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'changes', 'running', "
            "now() - interval '20 minutes', now() - interval '16 minutes') RETURNING id"),
            {"o": kc.org_a, "s": sid}).scalar())
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok", out
    taken = {str(r.id): r for r in _runs(kc, sid)}[old]
    assert (taken.status, taken.error) == ("failed", "stale")
    async with tenant_engine_scope(_app_dsn(kc)):
        with pytest.raises(runs.RunLost):
            async with tenant_session(kc.org_a) as db:
                await runs.progress(db, kc.org_a, old, {"pages": 9})
    # A heartbeat of five minutes ago is alive: no takeover, and no read.
    with kc.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO kb_sync_runs (organization_id, source_id, kind, status, heartbeat_at) "
            "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'changes', 'running', "
            "now() - interval '5 minutes')"), {"o": kc.org_a, "s": sid})
    reads = len(feed.listed())
    with pytest.raises(runs.SyncRunning):
        await _run(kc, sid, "changes", feed)
    assert len(feed.listed()) == reads


# ── 5. RateLimited keeps the cursor at the last committed page ──────────────

async def test_rate_limited_on_page_two_resumes_at_page_two(kc, app_engine) -> None:  # noqa: F811
    from gateway.kb_sources.base import RateLimited

    _ana, sid, feed = await _source(kc, "S8")
    assert _src(kc, sid).last_new_at is not None, "discovery inserted the tree"
    feed.pages["spt-S8"] = {"nextPageToken": "p2", "changes": [
        _ch("new-S8", name="New.pdf", mimeType="application/pdf", parents=["fold-S8"],
            md5Checksum="a" * 32)]}
    feed.pages["p2"] = RateLimited("Google kept throttling", tries=4)
    out = await _run(kc, sid, "changes", feed)
    assert (out.status, out.error) == ("stopped", "rate_limited"), out
    assert _nodes(kc, sid)["new-S8"].state == "live"
    cur = _cursor(kc, sid)
    assert (_token(cur), cur.last_status) == ("p2", "partial")
    row = _src(kc, sid)
    assert row.last_error == "rate_limited" and row.state == "ready"
    feed.pages["p2"] = {"newStartPageToken": "p3", "changes": [_rm("deck-S8")]}
    again = await _run(kc, sid, "changes", feed)
    assert again.status == "ok", again
    assert feed.listed() == ["spt-S8", "p2", "p2"]
    assert _token(_cursor(kc, sid)) == "p3"
    assert _nodes(kc, sid)["deck-S8"].state == "gone"
    done = _src(kc, sid)
    assert done.last_error is None and done.last_sync_at is not None


# ── 6. A page and its cursor commit together ────────────────────────────────

async def test_a_failure_in_the_cursor_write_rolls_the_page_back(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.kb_sync import runs

    _ana, sid, feed = await _source(kc, "S9")
    feed.pages["spt-S9"] = {"newStartPageToken": "spt-S9b", "changes": [
        _ch("new-S9", name="New.pdf", mimeType="application/pdf", parents=["fold-S9"]),
        _rm("deck-S9")]}

    async def broken(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("the cursor write failed")

    monkeypatch.setattr(runs, "write_cursor", broken)
    out = await _run(kc, sid, "changes", feed)
    assert (out.status, out.error) == ("failed", "RuntimeError"), out
    nodes = _nodes(kc, sid)
    assert "new-S9" not in nodes, "the page committed without its cursor"
    assert nodes["deck-S9"].state == "live", "the removal committed without its cursor"
    assert _token(_cursor(kc, sid)) == "spt-S9"


# ── 7. An expired cursor: a new token, then a slim relist ───────────────────

async def test_an_expired_cursor_takes_a_new_token_then_relists_by_hash(
    kc, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_sources.base import CursorExpired

    files = _tree("S10")
    for f in files:
        if f["id"] == "file-S10":
            f["md5Checksum"] = "a" * 32
        if f["id"] == "orph-S10":
            f["md5Checksum"] = "c" * 32
    _ana, sid, feed = await _source(kc, "S10", files)
    before = _nodes(kc, sid)
    assert before["file-S10"].content_hash == "a" * 32, "discovery stored no hash"
    feed.pages["spt-S10"] = CursorExpired("Google refused the change cursor (410)")
    # At Google: the PDF changed, the deck went, the other file did not.
    for f in feed.google.files:
        if f["id"] == "file-S10":
            f["md5Checksum"] = "b" * 32
    feed.google.files = [f for f in feed.google.files if f["id"] != "deck-S10"]
    sent = len(feed.google.calls)  # the connect POSTed the code exchange
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok" and out.counts["cursor_expired"] is True, out
    verbs = [v for v, _ in feed.calls]
    assert verbs[:3] == ["list_changed", "start", "discover"], verbs
    assert _token(_cursor(kc, sid)) == "fresh-1"
    after = _nodes(kc, sid)
    assert after["file-S10"].id == before["file-S10"].id
    assert after["file-S10"].content_hash == "b" * 32
    assert after["orph-S10"].id == before["orph-S10"].id
    assert after["orph-S10"].content_hash == "c" * 32
    assert after["deck-S10"].state == "gone"
    assert feed.google.calls[sent:], "the relist read nothing"
    assert all(verb == "GET" for verb, _u in feed.google.calls[sent:]), "the relist sent a write"


async def test_a_source_with_no_cursor_takes_a_token_first(kc, app_engine) -> None:  # noqa: F811
    _ana, sid, feed = await _source(kc, "S11")
    with kc.admin_engine.begin() as c:  # a source that KC-1a discovered
        c.execute(text("DELETE FROM kb_sync_cursors WHERE source_id = CAST(:s AS uuid)"),
                  {"s": sid})
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok" and out.counts["relist"] is True, out
    assert feed.calls[0] == ("start", None), feed.calls
    assert feed.listed() == []
    assert _token(_cursor(kc, sid)) == "fresh-1"
    assert _cursor(kc, sid, "reconcile") is not None


# ── Discovery: the token before the listing, stored with ready ──────────────

async def test_discovery_stores_a_token_taken_before_the_listing(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.kb_sync import discovery, runs
    from gateway.routes.knowledge import sources

    _ana, sid, feed = await _source(kc, "S12")
    urls = [u for verb, u in feed.google.calls if verb == "GET"]
    start = next(i for i, u in enumerate(urls) if u.endswith("/changes/startPageToken"))
    listing = next(i for i, u in enumerate(urls) if u.endswith("/files/root"))
    assert start < listing, "discovery took the cursor after it listed the tree"
    assert _token(_cursor(kc, sid)) == "spt-S12"
    assert _cursor(kc, sid, "reconcile") is not None
    [run] = _runs(kc, sid)
    assert (run.kind, run.status) == ("discover", "ok")
    first = _src(kc, sid)
    assert first.state == "ready"

    # The cursor write and `ready` are one transaction: a failed cursor write
    # leaves the source not ready.
    async def broken(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("the cursor write failed")

    monkeypatch.setattr(runs, "write_cursor", broken)
    async with tenant_engine_scope(_app_dsn(kc)):
        state = await discovery.discover_source(
            kc.org_a, sid, sources.make_source(sources.google_client()))
    assert state == "failed"
    row = _src(kc, sid)
    assert row.state == "failed" and row.discovered_at == first.discovered_at
    assert _token(_cursor(kc, sid)) == "spt-S12"


# ── A capped source ─────────────────────────────────────────────────────────

async def test_a_change_inside_an_unexplored_folder_of_a_capped_source_is_skipped(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.kb_sync import discovery

    monkeypatch.setattr(discovery, "NODE_CAP", 5)
    monkeypatch.setattr(discovery, "LEVELS", 1)
    _ana, sid, feed = await _source(kc, "C3")
    before = _nodes(kc, sid)
    assert {k for k, v in before.items() if not v.explored} == {"fold-C3"}
    feed.pages["spt-C3"] = {"newStartPageToken": "spt-C3b", "changes": [
        _ch("inner-C3", name="Inner.pdf", mimeType="application/pdf", parents=["fold-C3"]),
        _ch("top-C3", name="Top.pdf", mimeType="application/pdf", parents=["root-C3"]),
        _ch("fold-C3", name="Renamed", mimeType=FOLDER, parents=["root-C3"]),
        _ch("dir-C3", name="New folder", mimeType=FOLDER, parents=["sd-C3"]),
    ]}
    out = await _run(kc, sid, "changes", feed)
    assert out.status == "ok", out
    after = _nodes(kc, sid)
    assert "inner-C3" not in after, "a change inside an unexplored folder was stored"
    assert after["top-C3"].state == "live"
    assert after["fold-C3"].name == "Renamed" and after["fold-C3"].explored is False
    assert after["dir-C3"].explored is False, "a new folder of a capped source was explored"


async def test_a_capped_reconcile_reads_explored_folders_and_deletes_nothing(
    kc, app_engine, monkeypatch,  # noqa: F811
) -> None:
    from gateway.kb_sync import discovery

    monkeypatch.setattr(discovery, "NODE_CAP", 5)
    monkeypatch.setattr(discovery, "LEVELS", 1)
    _ana, sid, feed = await _source(kc, "C4")
    with kc.admin_engine.begin() as c:  # a node under the unexplored folder
        c.execute(text(
            "INSERT INTO kb_source_nodes (organization_id, source_id, ext_id, parent_ext_id, "
            "kind, name, updated_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'stray-C4', "
            "'fold-C4', 'file', 'Stray', now() - interval '1 day')"), {"o": kc.org_a, "s": sid})
    before = _nodes(kc, sid)
    feed.google.files = [f for f in feed.google.files if f["id"] != "deck-C4"]
    out = await _run(kc, sid, "reconcile", feed)
    assert out.status == "ok", out
    after = _nodes(kc, sid)
    assert set(after) >= set(before), "the capped reconcile deleted a node"
    assert after["deck-C4"].state == "gone", "a read folder kept a vanished child"
    assert after["stray-C4"].state == "live", "an unread folder lost a child"
    assert after["fold-C4"].state == "live" and after["fold-C4"].explored is False
    assert "file-C4" not in after, "the capped reconcile grew the tree"
    phases = [ph for verb, ph in feed.calls if verb == "discover"]
    assert phases and set(phases) == {"children"}, phases


# ── A disconnect wins the race ──────────────────────────────────────────────

def _race(p: Any, app_eng: Any, sid: str, sql: str, params: dict[str, Any]) -> None:
    """Hold the source FOR UPDATE as the disconnect does, run *sql* in a
    thread, and check that it waited for the lock and wrote nothing."""
    done: dict[str, Any] = {}

    def _write() -> None:
        with app_eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_a})
            c.execute(text("SET LOCAL lock_timeout = '20s'"))
            c.execute(text(sql), params)
        done["at"] = time.monotonic()

    with app_eng.connect() as hold, hold.begin():
        hold.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_a})
        hold.execute(text("SELECT 1 FROM kb_sources WHERE id = CAST(:s AS uuid) FOR UPDATE"),
                     {"s": sid})
        hold.execute(text("DELETE FROM kb_source_nodes WHERE source_id = CAST(:s AS uuid)"),
                     {"s": sid})
        hold.execute(text("DELETE FROM kb_sync_cursors WHERE source_id = CAST(:s AS uuid)"),
                     {"s": sid})
        hold.execute(text("UPDATE kb_sources SET state = 'disconnected' "
                          "WHERE id = CAST(:s AS uuid)"), {"s": sid})
        worker = threading.Thread(target=_write)
        worker.start()
        time.sleep(1.5)
        assert "at" not in done, "the write did not wait for the disconnect's row lock"
        released = time.monotonic()
    worker.join(30)
    assert done.get("at", 0) >= released


async def test_a_change_upsert_that_waits_on_a_disconnect_writes_no_node(
    kc, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_sync import engine

    _assert_non_priv(app_engine)
    _ana, sid, _feed = await _source(kc, "R3")
    rows = json.dumps([{"ext_id": "late-R3", "parent_ext_id": None, "drive_id": None,
                        "kind": "file", "name": "Late.pdf", "mime": None, "size": None,
                        "modified_at": None, "content_hash": None, "web_url": None,
                        "explored": True}])
    _race(kc, app_engine, sid, engine.UPSERT_CHANGES_SQL,
          {"org": kc.org_a, "source": sid, "rows": rows, "capped": False})
    assert _nodes(kc, sid) == {}, "a change page left orphan nodes behind a disconnect"


async def test_a_cursor_write_that_waits_on_a_disconnect_writes_nothing(
    kc, app_engine,  # noqa: F811
) -> None:
    from gateway.kb_sync import runs

    _assert_non_priv(app_engine)
    _ana, sid, _feed = await _source(kc, "R4")
    assert _cursor(kc, sid) is not None
    _race(kc, app_engine, sid, runs.UPSERT_CURSOR_SQL,
          {"org": kc.org_a, "source": sid, "kind": "changes",
           "state": json.dumps({"page_token": "late"}), "status": "ok"})
    with kc.admin_engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM kb_sync_cursors "
                              "WHERE source_id = CAST(:s AS uuid)"), {"s": sid}).scalar()
    assert left == 0, "a cursor write outlived the disconnect"


async def test_the_disconnect_deletes_the_cursors_and_keeps_the_runs(
    kc, app_engine,  # noqa: F811
) -> None:
    ana, sid, feed = await _source(kc, "R5")
    feed.pages["spt-R5"] = {"newStartPageToken": "spt-R5b", "changes": []}
    assert (await _run(kc, sid, "changes", feed)).status == "ok"
    await _ok(kc, ana, "DELETE", f"/knowledge/sources/{sid}")
    assert _cursor(kc, sid) is None and _cursor(kc, sid, "reconcile") is None
    assert [r.kind for r in _runs(kc, sid)] == ["discover", "changes"]
    skipped = await _run(kc, sid, "changes", feed)
    assert skipped.status == "skipped", "a disconnected source started a run"


# ── 8. Tenancy: org B reads, writes and runs nothing of org A ───────────────

async def test_org_b_reads_writes_and_runs_nothing_of_org_a(kc, app_engine) -> None:  # noqa: F811
    from gateway.db import tenant_session

    _assert_non_priv(app_engine)
    _ana, sid, feed = await _source(kc, "T1")
    feed.pages["spt-T1"] = {"newStartPageToken": "spt-T1b", "changes": []}
    assert (await _run(kc, sid, "changes", feed)).status == "ok"
    tables = ("kb_sync_cursors", "kb_sync_runs", "kb_source_nodes")

    def counts(org: str) -> dict[str, int]:
        with app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}

    assert counts(kc.org_b) == dict.fromkeys(tables, 0)
    assert all(n > 0 for n in counts(kc.org_a).values()), counts(kc.org_a)
    async with tenant_engine_scope(_app_dsn(kc)), tenant_session(kc.org_b) as db:
        seen = {t: (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
                for t in tables}
        changed = (await db.execute(text(
            "UPDATE kb_sync_runs SET status = 'failed' "
            "WHERE source_id = CAST(:s AS uuid)"), {"s": sid})).rowcount
    assert seen == dict.fromkeys(tables, 0) and changed == 0
    # Org B runs nothing: the source is not visible, so no lock and no call.
    calls = len(feed.calls)
    out = await _run(kc, sid, "changes", feed, org=kc.org_b)
    assert out.status == "skipped" and len(feed.calls) == calls
    assert len(_runs(kc, sid)) == 2
    # Through the routes, org B sees no source of org A.
    boss_b = _principal(_address("boss-b"), kc.org_b, CAPABILITY)
    assert (await _ok(kc, boss_b, "GET", "/knowledge/sources"))["sources"] == []
    assert (await _call(kc, boss_b, "GET", f"/knowledge/sources/{sid}/tree")).status_code == 404


@pytest.mark.parametrize("table", ["kb_sync_runs", "kb_sync_cursors"])
def test_with_check_refuses_a_row_for_another_org(kc, app_engine, table: str) -> None:  # noqa: F811
    async def go() -> str:
        return (await _source(kc, f"T2{table[-4:]}"))[1]

    sid = asyncio.run(go())
    insert = {
        "kb_sync_runs": "INSERT INTO kb_sync_runs (organization_id, source_id, kind) "
                        "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'reconcile')",
        "kb_sync_cursors": "INSERT INTO kb_sync_cursors (organization_id, source_id, kind) "
                           "VALUES (CAST(:o AS uuid), CAST(:s AS uuid), 'changes')",
    }[table]
    # Bound to org B, a row that names org A is refused, and so is the reverse.
    for bound, named in ((kc.org_b, kc.org_a), (kc.org_a, kc.org_b)):
        with pytest.raises(DBAPIError), app_engine.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": bound})
            c.execute(text(insert), {"o": named, "s": sid})


def test_both_new_tables_force_rls(kc, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with kc.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity, "
            "pg_get_userbyid(relowner) AS owner FROM pg_class "
            "WHERE relname IN ('kb_sync_cursors', 'kb_sync_runs')")).fetchall()
        policies = c.execute(text(
            "SELECT tablename, qual, with_check FROM pg_policies "
            "WHERE tablename IN ('kb_sync_cursors', 'kb_sync_runs')")).fetchall()
    assert {r.relname for r in rows} == {"kb_sync_cursors", "kb_sync_runs"}
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows), rows
    assert all(r.owner != _APP_ROLE for r in rows)
    assert {p.tablename for p in policies} == {"kb_sync_cursors", "kb_sync_runs"}
    assert all("app.tenant_id" in p.qual and "app.tenant_id" in p.with_check for p in policies)


def test_the_unbound_session_reads_no_run(kc, app_engine) -> None:  # noqa: F811
    async def go() -> None:
        await _source(kc, "T3")

    asyncio.run(go())
    with app_engine.connect() as c, c.begin():
        assert c.execute(text("SELECT count(*) FROM kb_sync_runs")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM kb_sync_cursors")).scalar() == 0


# ── The migration replays ───────────────────────────────────────────────────

def _shape(c: Any) -> dict[str, Any]:
    tables = ("kb_sync_cursors", "kb_sync_runs", "kb_sources")
    return {
        "columns": sorted(tuple(r) for r in c.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable FROM "
            "information_schema.columns WHERE table_name = ANY(:t)"), {"t": list(tables)})),
        "indexes": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE tablename = ANY(:t)"), {"t": list(tables)})),
        "policies": sorted(tuple(r) for r in c.execute(text(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = ANY(:t)"), {"t": list(tables)})),
        "checks": sorted(tuple(r) for r in c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = ANY(ARRAY['kb_sync_cursors'::regclass, "
            "'kb_sync_runs'::regclass])"))),
    }


def test_the_migration_replays_with_no_change(kc) -> None:  # noqa: F811
    from tests.unit._tenant_ladder import _exec_file

    with kc.admin_engine.connect() as c:
        before = _shape(c)
    assert ("kb_sources", "last_new_at", "timestamp with time zone", "YES") in before["columns"]
    assert any(i[1] == "kb_sync_runs_one_running_idx" and "WHERE (status = 'running'" in i[2]
               for i in before["indexes"]), before["indexes"]
    for _ in range(2):
        with kc.admin_engine.begin() as c:
            _exec_file(c, str(_sync_migration()))
    with kc.admin_engine.connect() as c:
        after = _shape(c)
    assert after == before
