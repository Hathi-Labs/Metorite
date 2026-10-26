"""Discovery at connect: the folder tree of one source (WS-61 KC-1a and KC-3).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 1 and §19, and
``project-docs/specs/knowledge_base.md`` §7.1 and §7.2.

The Google Drive callback (``routes/knowledge/sources.py``) adds
:func:`discover_source` as a ``BackgroundTasks`` job, so it runs after the
redirect has gone to the browser. The job never raises.

**The session rule.** Each step opens its own ``tenant_session(org)`` through
the one seam, and closes it before the next provider call. No session is
open while the adapter calls Google. The one exception is the refresh of a
due token, which ``connections/shared_token.py`` runs under its row lock, so
that every worker shares one token.

**The writes.** Each page of nodes is ONE upsert keyed on
``(organization_id, source_id, ext_id)``, from a JSON array, so a second run
writes the same rows again and adds none. The state goes ``discovering`` to
``ready``, or to ``failed`` with a short code, or to ``needs_reconnect``
when the refresh token is dead. A node that a complete run did not see
again becomes ``gone``.

**The cap (§7.2).** A tree of more than :data:`NODE_CAP` nodes is not stored
flat. The job deletes what the flat read stored, and walks the tree from the
roots instead: the first :data:`LEVELS` levels below each root. A folder on
the last level, or a folder the cap stopped, keeps ``explored = false``, and
a later slice expands it on demand.

**The run lock and the cursor (KC-3).** Discovery takes the run lock of the
source (``kb_sync_runs``, kind ``discover``). It ends a live loop run of the
source, because a consent must never leave the source in ``discovering``
(``runs.py``). A second discovery that finds the lock held returns, and the
first one sets the end state. Each page write also beats the heartbeat, so a
long discovery keeps its lock. Discovery asks the provider for the change
cursor of "now" BEFORE it lists the tree, and stores it in the transaction
that sets ``ready``. So a change made during the listing is read again by
the loop, and none is lost.

**The lock order (KC-3h).** The module doc of ``kb_sync/engine.py`` holds the
one lock order of the source rows. Each write transaction here takes the
source row before the nodes, the cursors and the run row. Most take it with
:func:`lock_source`. The start of a discovery and ``_mark`` take it with
their UPDATE of ``kb_sources``. Discovery keeps its rule for a disconnected
source: a write writes nothing, and the run goes on to its end state.

**The engine reuses two parts.** ``kb_sync/engine.py`` relists an uncapped
source with :func:`_flat` and its own *read* and *write*, and writes each
node with :func:`upsert_rows`. It never calls :func:`_walk`, which DELETES
every node of the source first.

**Metadata only.** The adapter returns :class:`SourceNode` values, which hold
no text. Nothing here reads a document.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import shared_token, store
from gateway.db import tenant_session
from gateway.kb_sources.base import (
    KIND_FOLDER,
    DiscoverCursor,
    DiscoverPage,
    KbSource,
    NeedsReconnect,
    SourceCredential,
    SourceError,
    SourceNode,
)
from gateway.kb_sources.google_drive import auth as google_auth
from gateway.kb_sources.google_drive import client as google_client
from gateway.kb_sync import owner_bump, runs

_log = get_logger("kb_sync.discovery")

#: The owning app of a Knowledge connection in ``integration_connections``.
OWNING_APP = "knowledge"

#: The most nodes one discovery stores (§7.2).
NODE_CAP = 50_000
#: The levels below a root that a capped discovery stores.
LEVELS = 3

STATE_DISCOVERING = "discovering"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_NEEDS_RECONNECT = "needs_reconnect"
STATE_DISCONNECTED = "disconnected"

#: One page write of the tree. The engine passes its own, with its run.
Write = Callable[[list[dict[str, Any]]], Awaitable[None]]
#: One page read of the tree. The engine passes its own, with its retry.
Read = Callable[[DiscoverCursor | None], Awaitable[DiscoverPage]]

_SOURCE_SQL = (
    "SELECT id, provider, owner_email, kind, state FROM kb_sources "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
)

#: The start of a run. ``now()`` is the time of the transaction, so a node
#: that this run writes has ``updated_at`` at or after it.
_START_SQL = (
    "UPDATE kb_sources SET state = 'discovering', last_error = NULL, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected' RETURNING now() AS started_at"
)

#: ⚠️ ``FOR KEY SHARE`` in the guard (review P2). A disconnect holds the source
#: row ``FOR UPDATE`` while it deletes the nodes. The guard waits for that lock
#: and then reads the row again, so it sees ``disconnected`` and the upsert
#: writes nothing. Without the lock it read the old state and left orphan
#: nodes. Fence, R8: ``test_an_upsert_that_waits_on_a_disconnect_writes_no_node``.
#: The engine's change upsert uses this same guard (``engine.py``).
SOURCE_GUARD_SQL = (
    "EXISTS (SELECT 1 FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected' "
    "FOR KEY SHARE OF s)"
)

#: ⚠️ The FIRST statement of each write transaction of the sync (KC-3h, the
#: lock order of ``engine.py``). ``state <> 'disconnected'`` is the guard: a
#: lock that waits on a disconnect reads the row again and finds no live row.
#: ``FOR KEY SHARE`` is for a transaction that writes only the children of
#: the source. It conflicts with the ``FOR UPDATE`` of a disconnect only.
LOCK_SOURCE_SHARE_SQL = (
    "SELECT s.id FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected' "
    "FOR KEY SHARE OF s"
)

#: ``FOR NO KEY UPDATE`` is for a transaction that also writes the source
#: row: a run close, the end of a discovery, and a node upsert, which can
#: stamp ``last_new_at``. A lock that starts as ``FOR KEY SHARE`` and becomes
#: an UPDATE later in the transaction is a deadlock of its own.
LOCK_SOURCE_UPDATE_SQL = (
    "SELECT s.id FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected' "
    "FOR NO KEY UPDATE OF s"
)

#: ``FOR UPDATE`` is for the one transaction that DELETES every node of the
#: source: the clear of a capped discovery (``_walk``). The delete cascades to
#: ``kb_node_access``. Only ``FOR UPDATE`` conflicts with the ``FOR KEY SHARE``
#: of an access page, so the clear waits for the page and the page waits for
#: the clear. ``FOR NO KEY UPDATE`` does not conflict with it (KC-4a, R4).
LOCK_SOURCE_EXCLUSIVE_SQL = (
    "SELECT s.id FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected' "
    "FOR UPDATE OF s"
)

#: The INSERT half of every node upsert, from a JSON array of
#: :func:`node_rows`. ``content_hash`` is the provider's version (KC-3).
NODE_INSERT_SQL = (
    "INSERT INTO kb_source_nodes (organization_id, source_id, ext_id, parent_ext_id, "
    "drive_id, kind, name, mime, size, modified_at, content_hash, web_url, state, "
    "explored, created_at, updated_at) "
    "SELECT CAST(:org AS uuid), CAST(:source AS uuid), r.ext_id, r.parent_ext_id, "
    "r.drive_id, r.kind, r.name, r.mime, r.size, r.modified_at, r.content_hash, "
    "r.web_url, 'live', r.explored, now(), now() "
    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(ext_id text, parent_ext_id text, "
    "drive_id text, kind text, name text, mime text, size bigint, "
    "modified_at timestamptz, content_hash text, web_url text, explored boolean) "
)

#: The columns an upsert sets again on a known node. The id stays, so a
#: rename or a move keeps the node and its children (KC-3).
NODE_SET_SQL = (
    "parent_ext_id = EXCLUDED.parent_ext_id, drive_id = EXCLUDED.drive_id, "
    "kind = EXCLUDED.kind, name = EXCLUDED.name, mime = EXCLUDED.mime, "
    "size = EXCLUDED.size, modified_at = EXCLUDED.modified_at, "
    "content_hash = EXCLUDED.content_hash, web_url = EXCLUDED.web_url, "
    "state = 'live', updated_at = now()"
)

#: ``inserted`` is true for a new node and false for a known one, so a run
#: can stamp ``kb_sources.last_new_at`` (KC-3).
UPSERT_NODES_SQL = (
    NODE_INSERT_SQL + "WHERE " + SOURCE_GUARD_SQL + " "
    "ON CONFLICT (organization_id, source_id, ext_id) DO UPDATE SET "
    + NODE_SET_SQL + ", explored = EXCLUDED.explored "
    "RETURNING (xmax = 0) AS inserted"
)

#: When a run last added a node (KC-3, migration 308). CX-5 reads it.
_LAST_NEW_SQL = (
    "UPDATE kb_sources SET last_new_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected'"
)

_UNEXPLORED_SQL = (
    "UPDATE kb_source_nodes SET explored = false, updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND ext_id = ANY(CAST(:ids AS text[]))"
)

_CLEAR_SQL = (
    "DELETE FROM kb_source_nodes "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid)"
)

#: A complete run marks each node it did not write again as ``gone``.
GONE_SQL = (
    "UPDATE kb_source_nodes SET state = 'gone', updated_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) AND source_id = CAST(:source AS uuid) "
    "AND state = 'live' AND updated_at < CAST(:started AS timestamptz)"
)

_FINISH_SQL = (
    "UPDATE kb_sources SET state = 'ready', last_error = NULL, discovered_at = now(), "
    "node_count = (SELECT count(*) FROM kb_source_nodes n "
    "WHERE n.organization_id = CAST(:org AS uuid) AND n.source_id = CAST(:source AS uuid) "
    "AND n.state = 'live'), updated_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected'"
)

_FAIL_SQL = (
    "UPDATE kb_sources SET state = :state, last_error = :code, updated_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected' RETURNING owner_email"
)


class _Stopped(Exception):
    """The source was disconnected, or lost its connection, during the run."""


def node_rows(nodes: Sequence[SourceNode], *, explored: bool = True,
              unexplored_folders: bool = False) -> list[dict[str, Any]]:
    """The JSON rows of one upsert, one row for each ``ext_id``.

    A page can name one id twice, and one upsert may not change a row twice,
    so the last value of an id wins. *unexplored_folders* marks each folder
    ``explored = false``: the last level of a capped walk.
    """
    rows: dict[str, dict[str, Any]] = {}
    for n in nodes:
        rows[n.ext_id] = {
            "ext_id": n.ext_id,
            "parent_ext_id": n.parent_ext_id,
            "drive_id": n.drive_id,
            "kind": n.kind,
            "name": n.name,
            "mime": n.mime,
            "size": n.size,
            "modified_at": n.modified_at.isoformat() if n.modified_at else None,
            "content_hash": n.content_hash,
            "web_url": n.web_url,
            "explored": not (unexplored_folders and n.kind == KIND_FOLDER) and explored,
        }
    return list(rows.values())


async def upsert_rows(
    db: Any, org: str, source_id: str, rows: list[dict[str, Any]], *,
    sql: str = UPSERT_NODES_SQL, params: dict[str, Any] | None = None,
) -> int:
    """One node upsert in the caller's transaction. Returns how many nodes it
    INSERTED, and stamps ``kb_sources.last_new_at`` when that is one or more."""
    if not rows:
        return 0
    result = await db.execute(text(sql), {
        "org": org, "source": source_id, "rows": json.dumps(rows), **(params or {}),
    })
    inserted = sum(1 for r in result.fetchall() if r.inserted)
    if inserted:
        await db.execute(text(_LAST_NEW_SQL), {"org": org, "source": source_id})
    return inserted


async def lock_source(db: Any, org: str, source_id: str, *, update: bool = False,
                      exclusive: bool = False) -> bool:
    """Lock the source row. It is the FIRST statement of a write transaction.

    The one lock helper of the sync (KC-3h, the lock order of ``engine.py``).
    *update* takes ``FOR NO KEY UPDATE``, for a transaction that writes the
    source row too. *exclusive* takes ``FOR UPDATE``, for the transaction that
    deletes every node (the clear of ``_walk``). Else it takes
    ``FOR KEY SHARE``. Returns False when no live row is left: the source is
    disconnected, or gone.
    """
    sql = (LOCK_SOURCE_EXCLUSIVE_SQL if exclusive
           else LOCK_SOURCE_UPDATE_SQL if update else LOCK_SOURCE_SHARE_SQL)
    row = (await db.execute(text(sql), {"org": org, "source": source_id})).first()
    return row is not None


async def _upsert(org: str, source_id: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with tenant_session(org) as db:
        # An upsert can stamp last_new_at, so the source lock is the strong one.
        await lock_source(db, org, source_id, update=True)
        await upsert_rows(db, org, source_id, rows)


async def _credential(org: str, owner: str, source: KbSource, *,
                      force: bool = False) -> SourceCredential:
    """A fresh token of the member's connection. A gone connection stops the run.

    *force* (KC-3) refreshes the token even when it is not due: the engine's
    one retry after Google refused a token with HTTP 401.
    """
    cred = await shared_token.fresh_credential(
        org,
        provider=source.provider,
        owning_app=OWNING_APP,
        refresh=source.refresh,
        needs_refresh=(lambda _c, _n: True) if force else google_auth.needs_refresh,
        now=google_auth.utcnow,
        scope_kind=store.SCOPE_MEMBER,
        member_email=owner,
    )
    if cred is None:
        raise _Stopped()
    return cred


async def _flat(org: str, source_id: str, owner: str, source: KbSource, *,
                read: Read | None = None, write: Write | None = None) -> bool:
    """Read the whole tree page by page. False when it passed the cap.

    *read* and *write* default to this module's own. The engine passes its
    read, with the 401 retry, and its write, with the heartbeat of its run.
    """
    async def _read(cursor: DiscoverCursor | None) -> DiscoverPage:
        return await source.discover(await _credential(org, owner, source), cursor)

    async def _write(rows: list[dict[str, Any]]) -> None:
        await _upsert(org, source_id, rows)

    reader = read or _read
    writer = write or _write
    total = 0
    cursor: DiscoverCursor | None = None
    while True:
        page = await reader(cursor)
        if total + len(page.nodes) > NODE_CAP:
            return False
        await writer(node_rows(page.nodes))
        total += len(page.nodes)
        if page.next_cursor is None:
            return True
        cursor = page.next_cursor


async def _walk(org: str, source_id: str, owner: str, source: KbSource, *,
                write: Write | None = None) -> None:
    """The first :data:`LEVELS` levels below each root, at most :data:`NODE_CAP`.

    ⚠️ It DELETES every node of the source first. Only discovery calls it.
    """
    async def _write(rows: list[dict[str, Any]]) -> None:
        await _upsert(org, source_id, rows)

    writer = write or _write
    async with tenant_session(org) as db:
        # FOR UPDATE, as a disconnect (KC-4a, R4): the clear cascades to the
        # access rows, and an access page holds the row FOR KEY SHARE.
        await lock_source(db, org, source_id, exclusive=True)
        await db.execute(text(_CLEAR_SQL), {"org": org, "source": source_id})
    total = 0
    queue: list[tuple[str, int]] = []
    cursor: DiscoverCursor | None = DiscoverCursor(phase=google_client.PHASE_ROOT)
    # The roots: the member's own drive and the shared drives.
    while cursor is not None and cursor.phase in (
            google_client.PHASE_ROOT, google_client.PHASE_DRIVES):
        page = await source.discover(await _credential(org, owner, source), cursor)
        await writer(node_rows(page.nodes))
        total += len(page.nodes)
        queue.extend((n.ext_id, 0) for n in page.nodes if n.kind == KIND_FOLDER)
        cursor = page.next_cursor
    stopped: list[str] = []
    while queue:
        folder, depth = queue.pop(0)
        if total >= NODE_CAP:
            stopped.append(folder)
            continue
        child: DiscoverCursor | None = DiscoverCursor(
            phase=google_client.PHASE_CHILDREN, parent_ext_id=folder)
        last = depth + 1 >= LEVELS
        while child is not None:
            page = await source.discover(await _credential(org, owner, source), child)
            room = NODE_CAP - total
            nodes = page.nodes[:room]
            await writer(node_rows(nodes, unexplored_folders=last))
            total += len(nodes)
            if not last:
                queue.extend((n.ext_id, depth + 1) for n in nodes if n.kind == KIND_FOLDER)
            if len(nodes) < len(page.nodes):
                stopped.append(folder)  # the cap cut this folder short
                break
            child = page.next_cursor
    if stopped:
        async with tenant_session(org) as db:
            await lock_source(db, org, source_id)
            await db.execute(text(_UNEXPLORED_SQL), {
                "org": org, "source": source_id, "ids": stopped,
            })


class _Run:
    """The run lock of one discovery: its id, its counts and its page write."""

    def __init__(self, org: str, source_id: str, run_id: str) -> None:
        self.org, self.source_id, self.id = org, source_id, run_id
        self.counts: dict[str, Any] = {"nodes": 0, "inserted": 0}

    async def write(self, rows: list[dict[str, Any]]) -> None:
        """One page of nodes and the heartbeat, in ONE transaction. A run
        that lost its lock raises :class:`runs.RunLost` and writes nothing."""
        if not rows:
            return
        async with tenant_session(self.org) as db:
            # An upsert can stamp last_new_at, so the source lock is the strong one.
            await lock_source(db, self.org, self.source_id, update=True)
            inserted = await upsert_rows(db, self.org, self.source_id, rows)
            self.counts["nodes"] += len(rows)
            self.counts["inserted"] += inserted
            await runs.progress(db, self.org, self.id, self.counts)


async def _mark(org: str, source_id: str, state: str, code: str,
                run: _Run | None) -> None:
    safe = store.safe_error_code(code)[:64]
    async with tenant_session(org) as db:
        marked = (await db.execute(text(_FAIL_SQL), {
            "org": org, "source": source_id, "state": state, "code": safe,
        })).first()
        if run is not None:
            status = runs.STATUS_STOPPED if state == STATE_NEEDS_RECONNECT else runs.STATUS_FAILED
            await runs.finish(db, org, run.id, status, safe, run.counts)
    # KB-9b slice 2: the connector's My Day card. OUTSIDE the session, so the
    # bump never waits while the source row is locked. It never raises.
    if marked is not None:
        await owner_bump.bump_owner(org, marked.owner_email)


async def discover_source(org: str, source_id: str, source: KbSource) -> str:
    """Discover the tree of one source. A background job: it never raises.

    *org* is the organization of the session that ran the callback, never a
    request value. *source* is the adapter, built by the route on the one
    Google client. Returns the state it left, for the tests and the log.
    """
    run: _Run | None = None
    try:
        async with tenant_session(org) as db:
            row = (await db.execute(text(_SOURCE_SQL), {"id": source_id, "org": org})).first()
            if row is None or row.state == STATE_DISCONNECTED:
                return STATE_DISCONNECTED
            owner = str(row.owner_email)
            started = (await db.execute(text(_START_SQL), {"id": source_id, "org": org})).first()
            if started is None:
                return STATE_DISCONNECTED
            lock = await runs.start(db, org, source_id, runs.KIND_DISCOVER, preempt=True)
        run = _Run(org, source_id, lock.id)
        # KC-3: the cursor of "now", BEFORE the listing (the module doc).
        token = await source.changes_start_token(await _credential(org, owner, source))
        if await _flat(org, source_id, owner, source, write=run.write):
            async with tenant_session(org) as db:
                await lock_source(db, org, source_id)
                await db.execute(text(GONE_SQL), {
                    "org": org, "source": source_id,
                    "started": started.started_at,
                })
                await runs.progress(db, org, run.id, run.counts)
        else:
            await _walk(org, source_id, owner, source, write=run.write)
        async with tenant_session(org) as db:
            # KC-3h: the source row before the run row. Before KC-3h this
            # wrote the run row first, so a stale takeover (the source row
            # FOR SHARE, then the run row) could deadlock it.
            await lock_source(db, org, source_id, update=True)
            await runs.progress(db, org, run.id, run.counts)
            await db.execute(text(_FINISH_SQL), {"org": org, "source": source_id})
            # A discovery is a full listing, so it also counts as the relist.
            await runs.write_cursor(db, org, source_id, runs.CURSOR_CHANGES,
                                    {"page_token": token}, runs.CURSOR_OK)
            await runs.write_cursor(db, org, source_id, runs.CURSOR_RECONCILE,
                                    None, runs.CURSOR_OK)
            await runs.finish(db, org, run.id, runs.STATUS_OK, None, run.counts)
        _log.info("knowledge.source_discovered", organization_id=org, source_id=source_id)
        return STATE_READY
    except runs.SyncRunning:
        # Another discovery holds the lock, and it sets the end state.
        _log.info("knowledge.source_discovery_running", organization_id=org,
                  source_id=source_id)
        return STATE_DISCOVERING
    except runs.RunLost:
        # A newer discovery took the lock over, and it sets the end state.
        _log.warning("knowledge.source_discovery_lost", organization_id=org,
                     source_id=source_id)
        return STATE_DISCOVERING
    except _Stopped:
        await _safe_mark(org, source_id, STATE_FAILED, "no_connection", run)
        return STATE_FAILED
    except NeedsReconnect as exc:
        await _safe_mark(org, source_id, STATE_NEEDS_RECONNECT, exc.code or "invalid_grant", run)
        return STATE_NEEDS_RECONNECT
    except SourceError as exc:
        await _safe_mark(org, source_id, STATE_FAILED, exc.code or type(exc).__name__, run)
        return STATE_FAILED
    except Exception as exc:  # a background job must not raise
        await _safe_mark(org, source_id, STATE_FAILED, type(exc).__name__, run)
        return STATE_FAILED


async def _safe_mark(org: str, source_id: str, state: str, code: str,
                     run: _Run | None = None) -> None:
    """Store the end state and close the run. A failure here is logged, and
    never raised."""
    _log.warning("knowledge.source_discovery_failed", organization_id=org,
                 source_id=source_id, state=state, code=store.safe_error_code(code))
    try:
        await _mark(org, source_id, state, code, run)
    except Exception as exc:
        _log.warning("knowledge.source_state_write_failed", organization_id=org,
                     source_id=source_id, error=type(exc).__name__)


__all__ = [
    "GONE_SQL",
    "LEVELS",
    "LOCK_SOURCE_SHARE_SQL",
    "LOCK_SOURCE_UPDATE_SQL",
    "NODE_CAP",
    "NODE_INSERT_SQL",
    "NODE_SET_SQL",
    "OWNING_APP",
    "SOURCE_GUARD_SQL",
    "UPSERT_NODES_SQL",
    "discover_source",
    "lock_source",
    "node_rows",
    "upsert_rows",
]
