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

(a) ONE session takes the run lock and reads the source row and the cursor.
    The member comes from the source row, never from a request.
(b) For each page: the provider call with NO session open, then ONE
    transaction that applies the page, upserts the cursor and beats the
    heartbeat. A page and its cursor commit together, or neither commits.
(c) The last page stores ``new_start_token``, the cursor of the next run.
(d) ONE transaction closes the run, clears ``kb_sources.last_error`` and
    calls ``store.mark_synced``.

**The session rule.** Each step opens its own ``tenant_session(org)``, and no
session is open during a provider call. The one exception is the refresh of
a due token, which ``connections/shared_token.py`` runs under its row lock.
Fence: ``tests/unit/test_kb_sync_tenancy.py``.

**The node writes.** An upsert keeps the row and its id, so a rename or a move
changes the name and ``parent_ext_id`` and keeps every child. A removal sets
``state = 'gone'`` and never deletes the row. Each write has the guard of
``discovery.SOURCE_GUARD_SQL``, and the cursor write has the same guard. So a
disconnect that holds the source row ``FOR UPDATE`` wins, and the page and the
cursor write nothing.

**A capped source** (a folder with ``explored = false``) stores a change only
when the node is already known, or its parent is a stored folder with
``explored = true``. A new folder of a capped source starts unexplored. So the
tree of a capped source does not grow past what discovery kept.

**No cursor yet** (each source that KC-1a discovered): the engine takes the
start token FIRST, then relists, then stores the token. A change made during
the relist is read again by the next run.

**The relist** (the daily reconcile, and a dead cursor):

* uncapped: ``discovery._flat`` with this engine's read and write, then the
  ``gone`` sweep of the nodes that the relist did not write again;
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

_SOURCE_SQL = (
    "SELECT id, provider, owner_email, state, connection_id FROM kb_sources "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid)"
)

#: Any folder with ``explored = false`` makes a source capped.
_CAPPED_SQL = (
    "SELECT EXISTS (SELECT 1 FROM kb_source_nodes "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND kind = 'folder' AND NOT explored) AS capped"
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
    "AND updated_at < CAST(:started AS timestamptz)"
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

    async def _write_nodes(self, rows: list[dict[str, Any]]) -> None:
        """One page of a relist: the nodes and the heartbeat, in ONE
        transaction. A capped relist uses the change upsert, so a new folder
        starts unexplored."""
        async with tenant_session(self.org) as db:
            inserted = await discovery.upsert_rows(
                db, self.org, self.source_id, rows,
                sql=UPSERT_CHANGES_SQL if self.capped else discovery.UPSERT_NODES_SQL,
                params={"capped": True} if self.capped else None,
            )
            self.counts["upserts"] += len(rows)
            self.counts["inserted"] += inserted
            await runs.progress(db, self.org, self.run_id, self.counts)

    async def _apply_page(self, page: ChangePage, cursor: str) -> None:
        """One page of the change feed, its cursor and the heartbeat, in ONE
        transaction (the module doc, step b)."""
        rows = discovery.node_rows(page.upserts, unexplored_folders=self.capped)
        async with tenant_session(self.org) as db:
            inserted = await discovery.upsert_rows(
                db, self.org, self.source_id, rows,
                sql=UPSERT_CHANGES_SQL, params={"capped": self.capped},
            )
            if page.removed:
                result = await db.execute(text(_REMOVE_SQL), {
                    "org": self.org, "source": self.source_id, "ids": list(page.removed),
                })
                self.counts["gone"] += result.rowcount or 0
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
            if token is not None:
                wrote = await runs.write_cursor(db, self.org, self.source_id,
                                                runs.CURSOR_CHANGES, {"page_token": token},
                                                runs.CURSOR_OK)
                if not wrote:
                    raise _Disconnected()
            if not await runs.write_cursor(db, self.org, self.source_id,
                                           runs.CURSOR_RECONCILE, None, runs.CURSOR_OK):
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
                # Past the cap: the listing is not whole, so nothing is gone.
                self.counts["over_cap"] = True
                return
            async with tenant_session(self.org) as db:
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
            async with tenant_session(self.org) as db:
                result = await db.execute(text(_GONE_IN_FOLDER_SQL), {
                    "org": self.org, "source": self.source_id, "folder": folder,
                    "started": started,
                })
                self.counts["gone"] += result.rowcount or 0
                await runs.progress(db, self.org, self.run_id, self.counts)

    # ── The end ──────────────────────────────────────────────────────────

    async def close_ok(self) -> RunOutcome:
        """Step (d): close the run, clear the error, and mark the connection."""
        async with tenant_session(self.org) as db:
            owned = await runs.finish(db, self.org, self.run_id, runs.STATUS_OK, None,
                                      self.counts)
            if owned:
                row = (await db.execute(text(_SOURCE_OK_SQL), {
                    "org": self.org, "source": self.source_id,
                })).first()
                if row is not None:
                    await store.mark_synced(db, self.org, str(row.connection_id))
        return RunOutcome(runs.STATUS_OK, None, self.counts, self.run_id)

    async def close(self, status: str, code: str, *, reconnect: bool = False,
                    source_error: bool = True) -> RunOutcome:
        """Close the run with a safe code. The source keeps its state, except
        for ``needs_reconnect``. A failure to write is logged, never raised."""
        safe = _safe(code)
        try:
            async with tenant_session(self.org) as db:
                owned = await runs.finish(db, self.org, self.run_id, status, safe, self.counts)
                if owned and (reconnect or source_error):
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
