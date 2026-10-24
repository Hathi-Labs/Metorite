"""Discovery at connect: the folder tree of one source (WS-61 KC-1a).

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

**No run lock and no scheduler.** KC-3 adds both. Two jobs for one source at
one time each write the same idempotent upserts.

**Metadata only.** The adapter returns :class:`SourceNode` values, which hold
no text. Nothing here reads a document.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import shared_token, store
from gateway.db import tenant_session
from gateway.kb_sources.base import (
    KIND_FOLDER,
    DiscoverCursor,
    KbSource,
    NeedsReconnect,
    SourceCredential,
    SourceError,
    SourceNode,
)
from gateway.kb_sources.google_drive import auth as google_auth
from gateway.kb_sources.google_drive import client as google_client

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

UPSERT_NODES_SQL = (
    "INSERT INTO kb_source_nodes (organization_id, source_id, ext_id, parent_ext_id, "
    "drive_id, kind, name, mime, size, modified_at, web_url, state, explored, "
    "created_at, updated_at) "
    "SELECT CAST(:org AS uuid), CAST(:source AS uuid), r.ext_id, r.parent_ext_id, "
    "r.drive_id, r.kind, r.name, r.mime, r.size, r.modified_at, r.web_url, 'live', "
    "r.explored, now(), now() "
    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r(ext_id text, parent_ext_id text, "
    "drive_id text, kind text, name text, mime text, size bigint, "
    "modified_at timestamptz, web_url text, explored boolean) "
    "WHERE EXISTS (SELECT 1 FROM kb_sources s WHERE s.id = CAST(:source AS uuid) "
    "AND s.organization_id = CAST(:org AS uuid) AND s.state <> 'disconnected') "
    "ON CONFLICT (organization_id, source_id, ext_id) DO UPDATE SET "
    "parent_ext_id = EXCLUDED.parent_ext_id, drive_id = EXCLUDED.drive_id, "
    "kind = EXCLUDED.kind, name = EXCLUDED.name, mime = EXCLUDED.mime, "
    "size = EXCLUDED.size, modified_at = EXCLUDED.modified_at, "
    "web_url = EXCLUDED.web_url, state = 'live', explored = EXCLUDED.explored, "
    "updated_at = now()"
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
_GONE_SQL = (
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
    "AND state <> 'disconnected'"
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
            "web_url": n.web_url,
            "explored": not (unexplored_folders and n.kind == KIND_FOLDER) and explored,
        }
    return list(rows.values())


async def _upsert(org: str, source_id: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with tenant_session(org) as db:
        await db.execute(text(UPSERT_NODES_SQL), {
            "org": org, "source": source_id, "rows": json.dumps(rows),
        })


async def _credential(org: str, owner: str, source: KbSource) -> SourceCredential:
    """A fresh token of the member's connection. A gone connection stops the run."""
    cred = await shared_token.fresh_credential(
        org,
        provider=source.provider,
        owning_app=OWNING_APP,
        refresh=source.refresh,
        needs_refresh=google_auth.needs_refresh,
        now=google_auth.utcnow,
        scope_kind=store.SCOPE_MEMBER,
        member_email=owner,
    )
    if cred is None:
        raise _Stopped()
    return cred


async def _flat(org: str, source_id: str, owner: str, source: KbSource) -> bool:
    """Read the whole tree page by page. False when it passed the cap."""
    total = 0
    cursor: DiscoverCursor | None = None
    while True:
        page = await source.discover(await _credential(org, owner, source), cursor)
        if total + len(page.nodes) > NODE_CAP:
            return False
        await _upsert(org, source_id, node_rows(page.nodes))
        total += len(page.nodes)
        if page.next_cursor is None:
            return True
        cursor = page.next_cursor


async def _walk(org: str, source_id: str, owner: str, source: KbSource) -> None:
    """The first :data:`LEVELS` levels below each root, at most :data:`NODE_CAP`."""
    async with tenant_session(org) as db:
        await db.execute(text(_CLEAR_SQL), {"org": org, "source": source_id})
    total = 0
    queue: list[tuple[str, int]] = []
    cursor: DiscoverCursor | None = DiscoverCursor(phase=google_client.PHASE_ROOT)
    # The roots: the member's own drive and the shared drives.
    while cursor is not None and cursor.phase in (
            google_client.PHASE_ROOT, google_client.PHASE_DRIVES):
        page = await source.discover(await _credential(org, owner, source), cursor)
        await _upsert(org, source_id, node_rows(page.nodes))
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
            await _upsert(org, source_id, node_rows(nodes, unexplored_folders=last))
            total += len(nodes)
            if not last:
                queue.extend((n.ext_id, depth + 1) for n in nodes if n.kind == KIND_FOLDER)
            if len(nodes) < len(page.nodes):
                stopped.append(folder)  # the cap cut this folder short
                break
            child = page.next_cursor
    if stopped:
        async with tenant_session(org) as db:
            await db.execute(text(_UNEXPLORED_SQL), {
                "org": org, "source": source_id, "ids": stopped,
            })


async def _mark(org: str, source_id: str, state: str, code: str) -> None:
    async with tenant_session(org) as db:
        await db.execute(text(_FAIL_SQL), {
            "org": org, "source": source_id, "state": state,
            "code": store.safe_error_code(code)[:64],
        })


async def discover_source(org: str, source_id: str, source: KbSource) -> str:
    """Discover the tree of one source. A background job: it never raises.

    *org* is the organization of the session that ran the callback, never a
    request value. *source* is the adapter, built by the route on the one
    Google client. Returns the state it left, for the tests and the log.
    """
    try:
        async with tenant_session(org) as db:
            row = (await db.execute(text(_SOURCE_SQL), {"id": source_id, "org": org})).first()
            if row is None or row.state == STATE_DISCONNECTED:
                return STATE_DISCONNECTED
            owner = str(row.owner_email)
            started = (await db.execute(text(_START_SQL), {"id": source_id, "org": org})).first()
        if started is None:
            return STATE_DISCONNECTED
        if await _flat(org, source_id, owner, source):
            async with tenant_session(org) as db:
                await db.execute(text(_GONE_SQL), {
                    "org": org, "source": source_id,
                    "started": started.started_at,
                })
        else:
            await _walk(org, source_id, owner, source)
        async with tenant_session(org) as db:
            await db.execute(text(_FINISH_SQL), {"org": org, "source": source_id})
        _log.info("knowledge.source_discovered", organization_id=org, source_id=source_id)
        return STATE_READY
    except _Stopped:
        await _safe_mark(org, source_id, STATE_FAILED, "no_connection")
        return STATE_FAILED
    except NeedsReconnect as exc:
        await _safe_mark(org, source_id, STATE_NEEDS_RECONNECT, exc.code or "invalid_grant")
        return STATE_NEEDS_RECONNECT
    except SourceError as exc:
        await _safe_mark(org, source_id, STATE_FAILED, exc.code or type(exc).__name__)
        return STATE_FAILED
    except Exception as exc:  # a background job must not raise
        await _safe_mark(org, source_id, STATE_FAILED, type(exc).__name__)
        return STATE_FAILED


async def _safe_mark(org: str, source_id: str, state: str, code: str) -> None:
    """Store the end state. A failure here is logged, and never raised."""
    _log.warning("knowledge.source_discovery_failed", organization_id=org,
                 source_id=source_id, state=state, code=store.safe_error_code(code))
    try:
        await _mark(org, source_id, state, code)
    except Exception as exc:
        _log.warning("knowledge.source_state_write_failed", organization_id=org,
                     source_id=source_id, error=type(exc).__name__)


__all__ = [
    "LEVELS",
    "NODE_CAP",
    "OWNING_APP",
    "UPSERT_NODES_SQL",
    "discover_source",
    "node_rows",
]
