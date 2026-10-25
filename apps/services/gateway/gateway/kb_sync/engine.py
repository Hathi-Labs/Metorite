"""The sync engine of one Knowledge file source (WS-61 KC-3).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 steps 5 and 7, §16 and
§22 (KC-3), and ``project-docs/specs/knowledge_base.md`` §7.1.

**KC-3 keeps the METADATA tree current.** It reads the provider's change feed
and writes ``kb_source_nodes``. It reads no text and builds no hook for KC-5.
KC-5 pulls: it compares ``kb_source_nodes.content_hash`` with its own document
hash. This module copies the rules of ``crm_sync/engine.py`` and imports none
of its code.

:func:`run_source` runs one source once, as one of two kinds:

* ``changes`` — the change feed from the stored cursor, page by page;
* ``reconcile`` — the daily slim relist, and a ``gone`` sweep.

**The steps.**

(a) ONE session reads the source row ``FOR SHARE``, takes the run lock and
    reads the cursor. The member and the connection id come from the source
    row, never from a request. The share lock orders the loop and a
    re-consent.
(b) For each page: the provider call with NO session open, then ONE
    transaction that locks the source row, applies the page, upserts the
    cursor and beats the heartbeat. A page and its cursor commit together,
    or neither commits.
(c) The last page stores ``new_start_token``, the cursor of the next run.
(d) ONE transaction locks the connection row and the source row, closes the
    run, clears ``kb_sources.last_error`` and calls ``store.mark_synced``.

**The lock order (KC-3h).** This is the ONE lock order of the rows of a
Knowledge source. Each transaction that touches more than one of these rows
takes them in this order:

1. the advisory lock of the consent callback;
2. the connection row (``connections/store.py``);
3. the ``kb_sources`` row;
4. the children: ``kb_sync_runs``, ``kb_sync_cursors`` and
   ``kb_source_nodes``.

Each row lock has, at its FIRST touch, the strongest strength that the
transaction needs. A lock that starts as ``FOR KEY SHARE`` and becomes an
UPDATE later is a deadlock of its own. The transactions:

* **Each write transaction of this module and of** ``discovery.py`` starts
  with ``discovery.lock_source``. It takes ``FOR NO KEY UPDATE`` when the
  transaction writes the source row too: :meth:`_SourceRun.close_ok`,
  :meth:`_SourceRun.close`, the end of a discovery, and a node upsert, which
  can stamp ``last_new_at``. Else it takes ``FOR KEY SHARE``, as a page with
  removals only does. Here, no live row raises :class:`_Disconnected`.
* **close_ok** locks the connection row first (``store.lock_by_id``, with
  the ``connection_id`` of step (a)), then the source row, then the run row.
* **The disconnect** (``routes/knowledge/sources.py``) reads the source row
  with no lock, locks the connection row, then locks the source row
  ``FOR UPDATE`` and reads its state again. ``FOR UPDATE`` stays, because
  the guards rely on ``FOR KEY SHARE`` and ``FOR UPDATE`` in conflict.
* **The consent callback** takes the advisory lock, the connection row
  (``store.save_credential``), the source row ``FOR UPDATE``, then the nodes.
* **The member purge** deletes the connection row (``store.delete``), and
  the foreign keys cascade to the source row and its children, in order.

Fences: R8, the ``test_the_lock_order_*`` tests of
``tests/unit/test_kc_sync_r8.py``, which run two paths at the same time and
prove no deadlock. Hermetic, the statement order of each transaction,
``tests/unit/test_kb_sync_engine.py::TestTheLockOrder``.

**The session rule.** Each step opens its own ``tenant_session(org)``, and no
session is open during a provider call. The one exception is the refresh of
a due token, which ``connections/shared_token.py`` runs under its row lock.
Fence: ``tests/unit/test_kb_sync_tenancy.py``.

**The node writes.** An upsert keeps the row and its id, so a rename or a move
changes the name and ``parent_ext_id`` and keeps every child. A removal sets
``state = 'gone'`` and never deletes the row. A gone folder takes each live
node below it, to a fixed point, in the same transaction, because the feed
reports a trashed folder as ONE event. A disconnect that holds the source row
``FOR UPDATE`` wins: the source lock of the page waits, finds no live row,
and the run stops with no write. The guard of ``discovery.SOURCE_GUARD_SQL``
in each upsert, and the same guard in the cursor write, stay as a second
wall.

**A capped source** (a folder with ``explored = false``) stores a change only
when the node is already known, or its parent is a stored folder with
``explored = true``. A new folder of a capped source starts unexplored. So the
tree of a capped source does not grow past what discovery kept.

**No cursor yet** (each source that KC-1a discovered): the engine takes the
start token FIRST, then relists, then stores the token. A change made during
the relist is read again by the next run.

**The relist** (the daily reconcile, and a dead cursor):

* uncapped: ``discovery._flat`` with this engine's read and write, then the
  ``gone`` sweep of the nodes that the relist did not write again. A source
  that grew past the cap becomes capped with NO delete: each folder deeper
  than the levels of a capped walk becomes ``explored = false``, and the
  reconcile cursor keeps the mark ``capped``. The next relist is capped;
* capped: the children of each stored folder with ``explored = true``. A node
  becomes ``gone`` only when its folder was read in full and the relist did
  not write it again. It never calls ``discovery._walk``, which deletes every
  node of the source.

The upsert writes the provider's ``content_hash``. A node with the same hash
keeps its row and its id, and a changed hash gets the new value. Nothing
fetches text.

**The errors.**

* ``NeedsReconnect`` sets ``kb_sources.state = 'needs_reconnect'`` and the
  code. ``shared_token`` has already set the connection row. The run ends
  ``stopped``, and the loop makes no provider call for the source again.
* ``RateLimited`` ends the run ``stopped``, and the cursor stays at the last
  committed page.
* ``CursorExpired`` takes a new start token first, then relists.
* HTTP 401 (code ``unauthorized``) forces ONE refresh of the token, and the
  call runs again. A dead refresh token is ``NeedsReconnect``. A second 401
  in the run ends it ``failed``.
* Any other ``SourceError`` ends the run ``failed`` with a safe code, and
  ``kb_sources.last_error`` takes the code. The source state does not change.

**No token in a log.** A log line holds the org, the source, the kind, the
counts and a short code. It never holds a page token or a credential.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import store
from gateway.db import tenant_session
from gateway.kb_sources.base import (
    ChangePage,
    CursorExpired,
    DiscoverCursor,
    DiscoverPage,
    KbSource,
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceError,
)
from gateway.kb_sources.google_drive import client as google_client
from gateway.kb_sync import discovery, runs

_log = get_logger("kb_sync.engine")

T = TypeVar("T")

#: The source state the loop reads. Any other state starts no run.
STATE_READY = discovery.STATE_READY

#: The code of a token that Google refused with HTTP 401.
UNAUTHORIZED = "unauthorized"

#: ⚠️ ``FOR SHARE`` (review P2, the consent race). A re-consent sets the row to
#: ``discovering`` and starts a discovery. Without the lock, the loop could
#: read ``ready``, lose the race, and hold the run lock while the discovery
#: gave up, so the source stayed ``discovering`` for ever. With it, the
#: callback's write waits for this transaction, or this read waits for the
#: callback and then reads ``discovering`` and runs nothing. Fence, R8:
#: ``test_kc_sync_r8.py::test_the_loop_waits_for_a_reconsent_and_runs_nothing``.
_SOURCE_SQL = (
    "SELECT id, provider, owner_email, state, connection_id FROM kb_sources "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "FOR SHARE"
)

#: A source is capped when a folder has ``explored = false`` (a capped
#: discovery), or when its reconcile cursor says so (an uncapped source that
#: grew past the cap, :data:`_CAP_SQL`).
_CAPPED_SQL = (
    "SELECT (EXISTS (SELECT 1 FROM kb_source_nodes "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND kind = 'folder' AND NOT explored) "
    "OR EXISTS (SELECT 1 FROM kb_sync_cursors "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND kind = 'reconcile' AND cursor_state ->> 'capped' = 'true')) AS capped"
)

#: An uncapped source that grew past the cap becomes capped, with NO delete
#: (review P2). Each folder deeper than the levels of a capped walk becomes
#: ``explored = false``, as ``discovery._walk`` would leave it. The nodes stay.
_CAP_SQL = (
    "WITH RECURSIVE shallow(ext_id, depth) AS ("
    "SELECT n.ext_id, 0 FROM kb_source_nodes n "
    "WHERE n.organization_id = CAST(:org AS uuid) AND n.source_id = CAST(:source AS uuid) "
    "AND n.state = 'live' AND (n.parent_ext_id IS NULL OR NOT EXISTS ("
    "SELECT 1 FROM kb_source_nodes p WHERE p.organization_id = CAST(:org AS uuid) "
    "AND p.source_id = CAST(:source AS uuid) AND p.ext_id = n.parent_ext_id "
    "AND p.state = 'live')) "
    "UNION "
    "SELECT c.ext_id, s.depth + 1 FROM kb_source_nodes c "
    "JOIN shallow s ON c.parent_ext_id = s.ext_id "
    "WHERE c.organization_id = CAST(:org AS uuid) AND c.source_id = CAST(:source AS uuid) "
    "AND c.state = 'live' AND s.depth + 1 < :levels) "
    "UPDATE kb_source_nodes f SET explored = false, updated_at = now() "
    "WHERE f.organization_id = CAST(:org AS uuid) AND f.source_id = CAST(:source AS uuid) "
    "AND f.kind = 'folder' AND f.explored AND f.ext_id NOT IN (SELECT ext_id FROM shallow)"
)

#: When nodes go ``gone``, each live node below them goes too, to a fixed
#: point (review P2). A trashed folder is ONE event in the change feed, and
#: the capped relist reads live folders only, so without this the children of
#: a gone folder stayed live for ever. *kept* holds the ids that the same
#: page stores, so a change of the page wins. *before* (the relist) spares
#: each node that the relist saw again. The seed must be ``gone`` already.
_CASCADE_GONE_SQL = (
    "WITH RECURSIVE dead(ext_id) AS ("
    "SELECT n.ext_id FROM kb_source_nodes n "
    "WHERE n.organization_id = CAST(:org AS uuid) AND n.source_id = CAST(:source AS uuid) "
    "AND n.state = 'gone' AND n.ext_id = ANY(CAST(:ids AS text[])) "
    "UNION "
    "SELECT c.ext_id FROM kb_source_nodes c JOIN dead d ON c.parent_ext_id = d.ext_id "
    "WHERE c.organization_id = CAST(:org AS uuid) AND c.source_id = CAST(:source AS uuid) "
    "AND c.state = 'live' AND NOT (c.ext_id = ANY(CAST(:kept AS text[]))) "
    "AND (CAST(:before AS timestamptz) IS NULL "
    "OR c.updated_at < CAST(:before AS timestamptz))) "
    "UPDATE kb_source_nodes g SET state = 'gone', updated_at = now() FROM dead d "
    "WHERE g.organization_id = CAST(:org AS uuid) AND g.source_id = CAST(:source AS uuid) "
    "AND g.ext_id = d.ext_id AND g.state = 'live'"
)

#: The upsert of the change feed. It differs from the discovery upsert in two
#: ways: a capped source stores a change only when the node is known or its
#: parent is an explored folder, and a known node keeps its ``explored``.
UPSERT_CHANGES_SQL = (
    discovery.NODE_INSERT_SQL + "WHERE " + discovery.SOURCE_GUARD_SQL + " "
    "AND (NOT CAST(:capped AS boolean) "
    "OR EXISTS (SELECT 1 FROM kb_source_nodes x "
    "WHERE x.organization_id = CAST(:org AS uuid) AND x.source_id = CAST(:source AS uuid) "
    "AND x.ext_id = r.ext_id) "
    "OR EXISTS (SELECT 1 FROM kb_source_nodes p "
    "WHERE p.organization_id = CAST(:org AS uuid) AND p.source_id = CAST(:source AS uuid) "
    "AND p.ext_id = r.parent_ext_id AND p.kind = 'folder' AND p.explored "
    "AND p.state = 'live')) "
    "ON CONFLICT (organization_id, source_id, ext_id) DO UPDATE SET "
    + discovery.NODE_SET_SQL + " "
    "RETURNING (xmax = 0) AS inserted"
)

#: A removal: the node stays as a row, with ``state = 'gone'``.
_REMOVE_SQL = (
    "UPDATE kb_source_nodes SET state = 'gone', updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND ext_id = ANY(CAST(:ids AS text[])) AND state = 'live'"
)

_EXPLORED_FOLDERS_SQL = (
    "SELECT ext_id FROM kb_source_nodes "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND kind = 'folder' AND explored AND state = 'live' ORDER BY ext_id"
)

#: The ``gone`` sweep of ONE folder that the capped relist read in full.
_GONE_IN_FOLDER_SQL = (
    "UPDATE kb_source_nodes SET state = 'gone', updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND parent_ext_id = :folder AND state = 'live' "
    "AND updated_at < CAST(:started AS timestamptz) RETURNING ext_id"
)

_NOW_SQL = "SELECT now() AS at"

_SOURCE_OK_SQL = (
    "UPDATE kb_sources SET last_error = NULL, node_count = (SELECT count(*) "
    "FROM kb_source_nodes n WHERE n.organization_id = CAST(:org AS uuid) "
    "AND n.source_id = CAST(:source AS uuid) AND n.state = 'live'), updated_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected' RETURNING connection_id"
)

_SOURCE_ERROR_SQL = (
    "UPDATE kb_sources SET last_error = :code, updated_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected'"
)

_SOURCE_RECONNECT_SQL = (
    "UPDATE kb_sources SET state = 'needs_reconnect', last_error = :code, "
    "updated_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected'"
)


class _Disconnected(Exception):
    """The source guard wrote nothing: a disconnect won the source row."""


class _Shutdown(Exception):
    """The gateway is stopping. The cursor stays at the last committed page."""


@dataclass
class RunOutcome:
    """What one call of :func:`run_source` did. Codes and counts only."""

    status: str
    error: str | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None


def _safe(code: object) -> str:
    return store.safe_error_code(str(code or ""))[:64]


class _SourceRun:
    """One run of one source, after it took the lock."""

    def __init__(
        self, org: str, source_id: str, kind: str, *, source: KbSource,
        should_stop: Callable[[], bool], owner: str, connection_id: str,
        lock: runs.Started, token: str | None, capped: bool,
    ) -> None:
        self.org, self.source_id, self.kind = org, source_id, kind
        self.source, self.should_stop = source, should_stop
        self.owner, self.connection_id = owner, connection_id
        self.run_id, self.started_at = lock.id, lock.started_at
        self.token, self.capped = token, capped
        self.forced = False
        self.counts: dict[str, Any] = {
            "pages": 0, "upserts": 0, "removed": 0, "inserted": 0, "gone": 0,
        }

    # ── The provider, with no session open ───────────────────────────────

    async def _call(self, fn: Callable[[SourceCredential], Awaitable[T]]) -> T:
        """One provider call. A 401 forces ONE refresh in the run, then the
        call runs again. A second 401 raises."""
        cred = await discovery._credential(self.org, self.owner, self.source)
        try:
            return await fn(cred)
        except SourceError as exc:
            if exc.code != UNAUTHORIZED or self.forced:
                raise
        self.forced = True
        cred = await discovery._credential(self.org, self.owner, self.source, force=True)
        return await fn(cred)

    async def _discover(self, cursor: DiscoverCursor | None) -> DiscoverPage:
        return await self._call(lambda cred: self.source.discover(cred, cursor))

    # ── The writes, each in one transaction with the heartbeat ───────────

    async def _guard(self, db: Any, *, update: bool = False) -> None:
        """The FIRST statement of each write transaction: the source row
        lock of the lock order (the module doc). *update* is for a
        transaction that writes the source row too. No live row raises
        :class:`_Disconnected`, before any write."""
        if not await discovery.lock_source(db, self.org, self.source_id, update=update):
            raise _Disconnected()

    async def _write_nodes(self, rows: list[dict[str, Any]]) -> None:
        """One page of a relist: the nodes and the heartbeat, in ONE
        transaction. A capped relist uses the change upsert, so a new folder
        starts unexplored."""
        async with tenant_session(self.org) as db:
            # An upsert can stamp last_new_at, so the source lock is the strong one.
            await self._guard(db, update=bool(rows))
            inserted = await discovery.upsert_rows(
                db, self.org, self.source_id, rows,
                sql=UPSERT_CHANGES_SQL if self.capped else discovery.UPSERT_NODES_SQL,
                params={"capped": True} if self.capped else None,
            )
            self.counts["upserts"] += len(rows)
            self.counts["inserted"] += inserted
            await runs.progress(db, self.org, self.run_id, self.counts)

    async def _cascade(self, db: Any, ids: list[str], *, kept: list[str],
                       before: datetime | None) -> None:
        """Each live node below the gone *ids*, gone too, in the caller's
        transaction (:data:`_CASCADE_GONE_SQL`)."""
        if not ids:
            return
        result = await db.execute(text(_CASCADE_GONE_SQL), {
            "org": self.org, "source": self.source_id, "ids": ids, "kept": kept,
            "before": before,
        })
        self.counts["gone"] += result.rowcount or 0

    async def _apply_page(self, page: ChangePage, cursor: str) -> None:
        """One page of the change feed, its cursor and the heartbeat, in ONE
        transaction (the module doc, step b)."""
        rows = discovery.node_rows(page.upserts, unexplored_folders=self.capped)
        async with tenant_session(self.org) as db:
            # K-21: the source lock comes first, before any node lock. A page
            # with removals only writes children only, so FOR KEY SHARE. An
            # upsert can stamp last_new_at, so FOR NO KEY UPDATE.
            await self._guard(db, update=bool(rows))
            inserted = await discovery.upsert_rows(
                db, self.org, self.source_id, rows,
                sql=UPSERT_CHANGES_SQL, params={"capped": self.capped},
            )
            if page.removed:
                result = await db.execute(text(_REMOVE_SQL), {
                    "org": self.org, "source": self.source_id, "ids": list(page.removed),
                })
                self.counts["gone"] += result.rowcount or 0
                # A gone folder takes each live node below it, in this page.
                await self._cascade(db, list(page.removed),
                                    kept=[n.ext_id for n in page.upserts], before=None)
            status = runs.CURSOR_PARTIAL if page.next_token else runs.CURSOR_OK
            wrote = await runs.write_cursor(db, self.org, self.source_id,
                                            runs.CURSOR_CHANGES, {"page_token": cursor}, status)
            if not wrote:
                raise _Disconnected()
            self.counts["pages"] += 1
            self.counts["upserts"] += len(rows)
            self.counts["removed"] += len(page.removed)
            self.counts["inserted"] += inserted
            await runs.progress(db, self.org, self.run_id, self.counts)

    # ── The kinds ────────────────────────────────────────────────────────

    async def changes(self) -> None:
        if self.token is None:
            await self.bootstrap()
            return
        token = self.token
        while True:
            if self.should_stop():
                raise _Shutdown()
            async def read(cred: SourceCredential, t: str = token) -> ChangePage:
                return await self.source.list_changed(cred, t, ())

            try:
                page = await self._call(read)
            except CursorExpired:
                self.counts["cursor_expired"] = True
                await self.bootstrap()
                return
            nxt = page.next_token or page.new_start_token
            if nxt is None:  # an adapter that broke the ChangePage contract
                exc = SourceError("the change page has no next cursor")
                exc.code = "no_next_cursor"
                raise exc
            await self._apply_page(page, nxt)
            if page.next_token is None:
                return
            token = page.next_token

    async def reconcile(self) -> None:
        if self.token is None:
            await self.bootstrap()
            return
        await self.relist()
        await self._store_cursors(None)

    async def bootstrap(self) -> None:
        """No cursor, or a dead one: the start token FIRST, then the relist,
        then the token (the module doc)."""
        token = await self._call(self.source.changes_start_token)
        self.counts["relist"] = True
        await self.relist()
        await self._store_cursors(token)

    async def _store_cursors(self, token: str | None) -> None:
        """The reconcile cursor, and the new change cursor when *token* is
        given, with the heartbeat, in ONE transaction."""
        async with tenant_session(self.org) as db:
            await self._guard(db)
            if token is not None:
                wrote = await runs.write_cursor(db, self.org, self.source_id,
                                                runs.CURSOR_CHANGES, {"page_token": token},
                                                runs.CURSOR_OK)
                if not wrote:
                    raise _Disconnected()
            # A capped source keeps the mark, so the next relist is capped too.
            mark = {"capped": True} if self.capped else None
            if not await runs.write_cursor(db, self.org, self.source_id,
                                           runs.CURSOR_RECONCILE, mark, runs.CURSOR_OK):
                raise _Disconnected()
            await runs.progress(db, self.org, self.run_id, self.counts)

    async def _now(self) -> datetime:
        async with tenant_session(self.org) as db:
            at: datetime = (await db.execute(text(_NOW_SQL))).first().at
        return at

    async def relist(self) -> None:
        """The slim relist of the module doc. Metadata only."""
        started = await self._now()
        if not self.capped:
            full = await discovery._flat(self.org, self.source_id, self.owner, self.source,
                                         read=self._discover, write=self._write_nodes)
            if not full:
                # Past the cap: the listing is not whole, so nothing is gone
                # now. The source becomes capped with no delete, and the next
                # relist takes the capped path (review P2).
                self.counts["over_cap"] = True
                async with tenant_session(self.org) as db:
                    await self._guard(db)
                    result = await db.execute(text(_CAP_SQL), {
                        "org": self.org, "source": self.source_id,
                        "levels": discovery.LEVELS,
                    })
                    self.counts["unexplored"] = result.rowcount or 0
                    await runs.progress(db, self.org, self.run_id, self.counts)
                self.capped = True
                return
            async with tenant_session(self.org) as db:
                await self._guard(db)
                result = await db.execute(text(discovery.GONE_SQL), {
                    "org": self.org, "source": self.source_id, "started": started,
                })
                self.counts["gone"] += result.rowcount or 0
                await runs.progress(db, self.org, self.run_id, self.counts)
            return
        async with tenant_session(self.org) as db:
            folders = [str(r.ext_id) for r in (await db.execute(text(_EXPLORED_FOLDERS_SQL), {
                "org": self.org, "source": self.source_id,
            })).fetchall()]
        for folder in folders:
            if self.should_stop():
                raise _Shutdown()
            child: DiscoverCursor | None = DiscoverCursor(
                phase=google_client.PHASE_CHILDREN, parent_ext_id=folder)
            while child is not None:
                page = await self._discover(child)
                await self._write_nodes(discovery.node_rows(page.nodes, unexplored_folders=True))
                child = page.next_cursor
            # The folder was read in full: what it did not list again is gone.
            # A gone child folder takes each node below it that this relist
            # did not see again.
            async with tenant_session(self.org) as db:
                await self._guard(db)
                gone = [str(r.ext_id) for r in (await db.execute(text(_GONE_IN_FOLDER_SQL), {
                    "org": self.org, "source": self.source_id, "folder": folder,
                    "started": started,
                })).fetchall()]
                self.counts["gone"] += len(gone)
                await self._cascade(db, gone, kept=[], before=started)
                await runs.progress(db, self.org, self.run_id, self.counts)

    # ── The end ──────────────────────────────────────────────────────────

    async def close_ok(self) -> RunOutcome:
        """Step (d): close the run, clear the error, and mark the connection.

        K-22, the lock order of the module doc: the connection row, then the
        source row, then the run row. Each lock is at the strength of the
        UPDATE that follows. The consent callback and the disconnect take the
        connection row first too, so neither waits in a cycle with this one.
        """
        async with tenant_session(self.org) as db:
            await store.lock_by_id(db, self.org, self.connection_id)
            await discovery.lock_source(db, self.org, self.source_id, update=True)
            owned = await runs.finish(db, self.org, self.run_id, runs.STATUS_OK, None,
                                      self.counts)
            if owned:
                row = (await db.execute(text(_SOURCE_OK_SQL), {
                    "org": self.org, "source": self.source_id,
                })).first()
                if row is not None:
                    # The row that the first lock took: connection_id never changes.
                    await store.mark_synced(db, self.org, self.connection_id)
        return RunOutcome(runs.STATUS_OK, None, self.counts, self.run_id)

    async def close(self, status: str, code: str, *, reconnect: bool = False,
                    source_error: bool = True) -> RunOutcome:
        """Close the run with a safe code. The source keeps its state, except
        for ``needs_reconnect``. A failure to write is logged, never raised."""
        safe = _safe(code)
        writes_source = reconnect or source_error
        try:
            async with tenant_session(self.org) as db:
                if writes_source:
                    # The lock order: the source row before the run row.
                    await discovery.lock_source(db, self.org, self.source_id, update=True)
                owned = await runs.finish(db, self.org, self.run_id, status, safe, self.counts)
                if owned and writes_source:
                    await db.execute(
                        text(_SOURCE_RECONNECT_SQL if reconnect else _SOURCE_ERROR_SQL),
                        {"org": self.org, "source": self.source_id, "code": safe})
        except Exception as exc:
            _log.warning("knowledge.sync_close_failed", organization_id=self.org,
                         source_id=self.source_id, error=type(exc).__name__)
        return RunOutcome(status, safe, self.counts, self.run_id)


async def _lock(org: str, source_id: str, kind: str) -> tuple[Any, runs.Started, str | None,
                                                               bool] | None:
    """Step (a), in ONE session: the source row, the lock, the cursor and the
    cap. ``None`` when the source is not ``ready``. Raises ``SyncRunning``."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_SOURCE_SQL), {"org": org, "source": source_id})).first()
        if row is None or row.state != STATE_READY:
            return None
        lock = await runs.start(db, org, source_id, kind)
        cursor = await runs.read_cursor(db, org, source_id, runs.CURSOR_CHANGES)
        capped = bool((await db.execute(text(_CAPPED_SQL), {
            "org": org, "source": source_id,
        })).first().capped)
    return row, lock, cursor.page_token if cursor is not None else None, capped


async def run_source(
    org: str, source_id: str, kind: str, *, source: KbSource,
    should_stop: Callable[[], bool] = lambda: False,
) -> RunOutcome:
    """Run one source once. *kind* is ``changes`` or ``reconcile``.

    *org* comes from the loop, which read it from the organization table,
    never from a request. The member of the credential comes from the source
    row. Raises :class:`runs.SyncRunning` when another run holds the lock.
    Any other failure closes the run and returns, so the loop goes on.
    """
    if kind not in (runs.KIND_CHANGES, runs.KIND_RECONCILE):
        raise ValueError(f"the loop runs no {kind!r} kind")
    locked = await _lock(org, source_id, kind)
    if locked is None:
        return RunOutcome("skipped", "not_ready")
    row, lock, token, capped = locked
    run = _SourceRun(org, source_id, kind, source=source, should_stop=should_stop,
                     owner=str(row.owner_email), connection_id=str(row.connection_id),
                     lock=lock, token=token, capped=capped)
    try:
        if kind == runs.KIND_CHANGES:
            await run.changes()
        else:
            await run.reconcile()
    except runs.RunLost:
        _log.warning("knowledge.sync_run_lost", organization_id=org, source_id=source_id,
                     kind=kind)
        return RunOutcome("lost", "run_lost", run.counts, run.run_id)
    except _Shutdown:
        return await run.close(runs.STATUS_STOPPED, "shutdown", source_error=False)
    except _Disconnected:
        return await run.close(runs.STATUS_STOPPED, "disconnected", source_error=False)
    except discovery._Stopped:
        return await run.close(runs.STATUS_STOPPED, "no_connection", source_error=False)
    except NeedsReconnect as exc:
        out = await run.close(runs.STATUS_STOPPED, exc.code or "needs_reconnect",
                              reconnect=True)
        _log.warning("knowledge.sync_needs_reconnect", organization_id=org,
                     source_id=source_id, code=out.error)
        return out
    except RateLimited:
        out = await run.close(runs.STATUS_STOPPED, "rate_limited")
        _log.info("knowledge.sync_rate_limited", organization_id=org, source_id=source_id,
                  pages=run.counts.get("pages", 0))
        return out
    except SourceError as exc:
        out = await run.close(runs.STATUS_FAILED, exc.code or type(exc).__name__)
        _log.warning("knowledge.sync_failed", organization_id=org, source_id=source_id,
                     kind=kind, code=out.error)
        return out
    except Exception as exc:  # a background run must not raise
        out = await run.close(runs.STATUS_FAILED, type(exc).__name__)
        _log.warning("knowledge.sync_failed", organization_id=org, source_id=source_id,
                     kind=kind, code=out.error)
        return out
    out = await run.close_ok()
    _log.info("knowledge.sync_ok", organization_id=org, source_id=source_id, kind=kind,
              **{k: v for k, v in run.counts.items() if isinstance(v, int | bool)})
    return out


__all__ = [
    "UPSERT_CHANGES_SQL",
    "RunOutcome",
    "run_source",
]
