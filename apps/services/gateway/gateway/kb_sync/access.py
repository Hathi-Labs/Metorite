"""The access pass of one Knowledge file source (WS-61 KC-4a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 6, §8.1, §16,
§17 and §22 (KC-4a).

A change of sharing can come with no change of content, so the change feed
does not see it. Every 6 hours (``scheduler.ACCESS_SECONDS``) the loop reads
the readers of each node of a ready source again, as a slim pass: ids and
permissions only. It writes ``kb_node_access``, in the provider's words.
``kb_access/resolve.py`` decides who a row proves. A row is never a grant.

:func:`run_access` runs one pass of one source, as the run kind ``access``:

(a) ONE session reads the source row ``FOR SHARE``, takes the run lock (kind
    ``access``) and reads the access cursor. The member and the connection
    come from the source row, never from a request.
(b) For each read of the adapter: the provider call with NO session open,
    then ONE transaction. The guard comes first. Then the readers of each
    node of the page REPLACE its old rows, and the access cursor and the
    heartbeat follow. A page and its cursor commit together, or neither
    commits.
(c) The pass stops at :data:`ACCESS_CALLS_PER_TICK` provider calls. The
    cursor keeps its place, and the next tick goes on from it. The run ends
    ``ok`` with ``complete`` false, and ``kb_sources.access_read_at`` does
    not move.
(d) The LAST page of the listing also deletes the rows of each node that
    this pass did not read again, clears the cursor, and sets
    ``kb_sources.access_read_at``. So the time moves only when a full pass
    ends.

**The lock order (KC-3h).** The module doc of ``engine.py`` holds it: the
connection row, the ``kb_sources`` row, then the children. ``kb_node_access``
is a child of ``kb_source_nodes``, so it comes after the source row. The pass
never locks the connection row: the refresh of a token runs in
``connections/shared_token.py``, in its own transaction. The transactions:

* step (a): the source row ``FOR SHARE``, then the run row (the foreign key
  of its INSERT takes ``FOR KEY SHARE`` on the source row too) and a cursor
  read;
* a page (b): the guard (``discovery.lock_source``, ``FOR KEY SHARE``), the
  DELETE of the old rows, the INSERT of the new rows (its foreign key takes
  ``FOR KEY SHARE`` on the node rows), the cursor and the heartbeat;
* the last page (d): the guard ``FOR NO KEY UPDATE``, because the
  transaction also writes the source row. Then the rows, the sweep, the
  cursor, the heartbeat and the UPDATE of ``kb_sources``;
* the close of the run: the run row only, so no source lock. An error close
  is ``engine._SourceRun.close``.

A disconnect holds the source row ``FOR UPDATE``. The guard of a page waits
for it, reads the row again, finds no live row, and the run stops with no
write. Fences: ``tests/unit/test_kb_sync_engine.py::TestTheLockOrder`` and,
R8, ``tests/unit/test_kc_access_r8.py``.

**The errors**, as ``engine.py``:

* ``NeedsReconnect`` takes the KC-3 path: ``kb_sources.state`` becomes
  ``needs_reconnect``, and ``shared_token`` has already set the connection
  row. The loop then makes no provider call for the source;
* ``RateLimited`` ends the run ``stopped``, with the cursor at the last
  committed page;
* a dead page token (``CursorExpired``) starts the pass again from the first
  page, in the same run;
* a 401 forces ONE refresh, as in the engine.

**A stale pass.** A pass that began before the last discovery starts again,
because the discovery wrote a new tree. The scheduler also finds a source
due when ``access_read_at`` is older than ``discovered_at``. A pass that
began more than :data:`MAX_PASS_AGE_SECONDS` ago starts again too (KC-4a fix
round 1, R2), so a cursor that no run can finish does not last for ever.

**Dark.** The pass rides ``KNOWLEDGE_SYNC`` (§17), and no flag is new. With
the flag empty, the scheduler starts no task, so no pass runs and nothing
calls the provider.

**No token in a log.** A log line holds the org, the source, the counts and
a short code. The page token sits in ``cursor_state`` only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.db import tenant_session
from gateway.kb_sources.base import (
    AccessCursor,
    AccessPage,
    CursorExpired,
    KbSource,
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceError,
)
from gateway.kb_sources.google_drive import client as google_client
from gateway.kb_sync import discovery, engine, runs

_log = get_logger("kb_sync.access")

#: The most provider calls of one pass of one source in one tick (OQ5).
ACCESS_CALLS_PER_TICK = 50

#: The longest age of an open pass: twice ``scheduler.ACCESS_SECONDS`` (6 h).
#: The scheduler imports this module, so the number sits here, and
#: ``tests/unit/test_kb_sync_engine.py`` checks that it stays that product.
MAX_PASS_AGE_SECONDS = 2 * 6 * 60 * 60

#: The least budget of one adapter read: one listing call, and the
#: ``permissions.list`` pages of one item. Below it, the pass stops for the
#: tick, so each read can finish one item.
MIN_BUDGET = 1 + google_client.MAX_PERMISSION_PAGES

#: ⚠️ ``FOR SHARE``, as ``engine._SOURCE_SQL``: a re-consent and the pass
#: take turns. ``discovered_at`` tells a stale cursor (the module doc).
_SOURCE_SQL = (
    "SELECT id, provider, owner_email, state, connection_id, discovered_at "
    "FROM kb_sources "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "FOR SHARE"
)

#: The old rows of each node of a page. A node the source does not hold
#: matches nothing.
_DELETE_ROWS_SQL = (
    "DELETE FROM kb_node_access a USING kb_source_nodes n "
    "WHERE a.organization_id = CAST(:org AS uuid) "
    "AND n.organization_id = CAST(:org AS uuid) "
    "AND n.source_id = CAST(:source AS uuid) AND a.node_id = n.id "
    "AND n.ext_id = ANY(CAST(:ids AS text[]))"
)

#: The new rows of a page, from a JSON array. The JOIN keeps only the nodes
#: that this source holds, so a file that discovery did not store writes
#: nothing.
_INSERT_ROWS_SQL = (
    "INSERT INTO kb_node_access (organization_id, node_id, principal_kind, principal, "
    "role, synced_at) "
    "SELECT CAST(:org AS uuid), n.id, r.kind, r.principal, r.role, now() "
    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
    "AS r(ext_id text, kind text, principal text, role text) "
    "JOIN kb_source_nodes n ON n.organization_id = CAST(:org AS uuid) "
    "AND n.source_id = CAST(:source AS uuid) AND n.ext_id = r.ext_id "
    "ON CONFLICT (organization_id, node_id, principal_kind, principal) DO UPDATE SET "
    "role = EXCLUDED.role, synced_at = now()"
)

#: The end of a full pass: the rows of each node of the source that the pass
#: did not read again. A node the listing no longer shows keeps no reader.
_SWEEP_SQL = (
    "DELETE FROM kb_node_access a USING kb_source_nodes n "
    "WHERE a.organization_id = CAST(:org AS uuid) "
    "AND n.organization_id = CAST(:org AS uuid) "
    "AND n.source_id = CAST(:source AS uuid) AND a.node_id = n.id "
    "AND a.synced_at < CAST(:started AS timestamptz)"
)

#: The end of a full pass, on the source row.
_ACCESS_READ_SQL = (
    "UPDATE kb_sources SET access_read_at = now() "
    "WHERE id = CAST(:source AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state <> 'disconnected'"
)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def cursor_state(cursor: AccessCursor, pass_started: datetime) -> dict[str, Any]:
    """The JSON of an open pass, for ``kb_sync_cursors.cursor_state``."""
    return {"page_token": cursor.page_token, "after": cursor.after,
            "page_size": cursor.page_size, "pass_started": pass_started.isoformat()}


def open_pass(state: dict[str, Any] | None, discovered_at: datetime | None,
              now: datetime | None = None) -> tuple[AccessCursor, datetime] | None:
    """The cursor and the start of the open pass, or ``None`` to start anew.

    A pass that began before the last discovery is stale (the module doc). So
    is a pass that began more than :data:`MAX_PASS_AGE_SECONDS` before *now*,
    the database time of the run that asks.
    """
    if not state:
        return None
    started = _when(state.get("pass_started"))
    if started is None or (discovered_at is not None and started < discovered_at):
        return None
    if now is not None and (
            started.tzinfo is None or (now - started).total_seconds() > MAX_PASS_AGE_SECONDS):
        return None
    token = state.get("page_token")
    after = state.get("after")
    try:
        size = int(state.get("page_size") or 0)
    except (TypeError, ValueError):
        size = 0
    return AccessCursor(page_token=str(token) if token else None,
                        after=str(after) if after else None, page_size=max(size, 0)), started


def page_rows(page: AccessPage) -> list[dict[str, Any]]:
    """The JSON rows of one page, one row for each node and reader."""
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for node in page.nodes:
        for reader in node.readers:
            rows.setdefault((node.ext_id, reader.kind, reader.principal), {
                "ext_id": node.ext_id, "kind": reader.kind,
                "principal": reader.principal, "role": reader.role[:32],
            })
    return list(rows.values())


class _AccessRun(engine._SourceRun):
    """One access pass of one source, after it took the lock.

    It reuses three parts of the engine's run: the 401 retry (``_call``),
    the guard (``_guard``) and the error close (``close``). It never calls
    the change feed.
    """

    def __init__(self, org: str, source_id: str, *, source: KbSource,
                 should_stop: Callable[[], bool], owner: str, connection_id: str,
                 lock: runs.Started, cursor: AccessCursor | None,
                 pass_started: datetime) -> None:
        super().__init__(org, source_id, runs.KIND_ACCESS, source=source,
                         should_stop=should_stop, owner=owner,
                         connection_id=connection_id, lock=lock, token=None, capped=False)
        self.cursor, self.pass_started = cursor, pass_started
        self.budget = ACCESS_CALLS_PER_TICK
        self.counts = {"pages": 0, "nodes": 0, "listed": 0, "rows": 0, "calls": 0,
                       "complete": False}

    async def _read_access(self, cursor: AccessCursor | None) -> AccessPage:
        """One adapter read, with no session open, inside the budget."""
        budget = self.budget
        forced = self.forced

        async def read(cred: SourceCredential) -> AccessPage:
            return await self.source.list_access(cred, cursor, budget=budget)

        page = await self._call(read)
        used = page.calls + (1 if self.forced and not forced else 0)
        self.budget -= max(used, 1)
        self.counts["calls"] += max(used, 1)
        return page

    async def _apply(self, page: AccessPage) -> None:
        """One page (step b), or the last page (step d), in ONE transaction."""
        nxt = page.next_cursor
        last = nxt is None
        rows = page_rows(page)
        ids = [n.ext_id for n in page.nodes]
        state = cursor_state(nxt, self.pass_started) if nxt is not None else None
        async with tenant_session(self.org) as db:
            # The lock order: the source row first. The last page writes the
            # source row too, so its lock is the strong one.
            await self._guard(db, update=last)
            params = {"org": self.org, "source": self.source_id}
            if ids:
                await db.execute(text(_DELETE_ROWS_SQL), {**params, "ids": ids})
            if rows:
                result = await db.execute(text(_INSERT_ROWS_SQL),
                                          {**params, "rows": json.dumps(rows)})
                self.counts["rows"] += result.rowcount or 0
            if last:
                await db.execute(text(_SWEEP_SQL), {**params, "started": self.pass_started})
            status = runs.CURSOR_OK if last else runs.CURSOR_PARTIAL
            if not await runs.write_cursor(db, self.org, self.source_id, runs.CURSOR_ACCESS,
                                           state, status):
                raise engine._Disconnected()
            if last:
                await db.execute(text(_ACCESS_READ_SQL), params)
            self.counts["pages"] += 1
            self.counts["nodes"] += len(page.nodes)
            self.counts["listed"] += sum(1 for n in page.nodes if n.listed)
            self.counts["complete"] = last
            await runs.progress(db, self.org, self.run_id, self.counts)

    async def run(self) -> None:
        """The pass, read by read, until the listing ends or the budget does."""
        cursor = self.cursor
        while self.budget >= MIN_BUDGET:
            if self.should_stop():
                raise engine._Shutdown()
            try:
                page = await self._read_access(cursor)
            except CursorExpired:
                if cursor is None or cursor.page_token is None:
                    raise
                # A dead page token: the pass starts again from the first page.
                self.counts["cursor_expired"] = True
                cursor = None
                self.pass_started = await self._now()
                continue
            await self._apply(page)
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    async def close_pass(self) -> engine.RunOutcome:
        """Close the run ``ok``. The run row only, so no source lock."""
        async with tenant_session(self.org) as db:
            await runs.finish(db, self.org, self.run_id, runs.STATUS_OK, None, self.counts)
        return engine.RunOutcome(runs.STATUS_OK, None, self.counts, self.run_id)


async def _lock(org: str, source_id: str) -> tuple[Any, runs.Started, AccessCursor | None,
                                                   datetime] | None:
    """Step (a), in ONE session. ``None`` when the source is not ``ready``.
    Raises ``SyncRunning``."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_SOURCE_SQL), {"org": org, "source": source_id})).first()
        if row is None or row.state != engine.STATE_READY:
            return None
        lock = await runs.start(db, org, source_id, runs.KIND_ACCESS)
        stored = await runs.read_cursor(db, org, source_id, runs.CURSOR_ACCESS)
    found = open_pass(stored.state if stored is not None else None, row.discovered_at,
                      lock.started_at)
    if found is None:
        return row, lock, None, lock.started_at
    cursor, started = found
    return row, lock, cursor, started


async def run_access(
    org: str, source_id: str, *, source: KbSource,
    should_stop: Callable[[], bool] = lambda: False,
) -> engine.RunOutcome:
    """Run one access pass of one source, up to the call cap of a tick.

    *org* comes from the loop, which read it from the organization table,
    never from a request. The member of the credential comes from the source
    row. Raises :class:`runs.SyncRunning` when another run holds the lock.
    Any other failure closes the run and returns, so the loop goes on.
    """
    locked = await _lock(org, source_id)
    if locked is None:
        return engine.RunOutcome("skipped", "not_ready")
    row, lock, cursor, started = locked
    run = _AccessRun(org, source_id, source=source, should_stop=should_stop,
                     owner=str(row.owner_email), connection_id=str(row.connection_id),
                     lock=lock, cursor=cursor, pass_started=started)
    try:
        await run.run()
    except runs.RunLost:
        _log.warning("knowledge.access_run_lost", organization_id=org, source_id=source_id)
        return engine.RunOutcome("lost", "run_lost", run.counts, run.run_id)
    except engine._Shutdown:
        return await run.close(runs.STATUS_STOPPED, "shutdown", source_error=False)
    except engine._Disconnected:
        return await run.close(runs.STATUS_STOPPED, "disconnected", source_error=False)
    except discovery._Stopped:
        return await run.close(runs.STATUS_STOPPED, "no_connection", source_error=False)
    except NeedsReconnect as exc:
        out = await run.close(runs.STATUS_STOPPED, exc.code or "needs_reconnect",
                              reconnect=True)
        _log.warning("knowledge.access_needs_reconnect", organization_id=org,
                     source_id=source_id, code=out.error)
        return out
    except RateLimited:
        out = await run.close(runs.STATUS_STOPPED, "rate_limited")
        _log.info("knowledge.access_rate_limited", organization_id=org, source_id=source_id,
                  pages=run.counts.get("pages", 0))
        return out
    except SourceError as exc:
        out = await run.close(runs.STATUS_FAILED, exc.code or type(exc).__name__)
        _log.warning("knowledge.access_failed", organization_id=org, source_id=source_id,
                     code=out.error)
        return out
    except Exception as exc:  # a background run must not raise
        out = await run.close(runs.STATUS_FAILED, type(exc).__name__)
        _log.warning("knowledge.access_failed", organization_id=org, source_id=source_id,
                     code=out.error)
        return out
    out = await run.close_pass()
    _log.info("knowledge.access_ok", organization_id=org, source_id=source_id,
              **{k: v for k, v in run.counts.items() if isinstance(v, int | bool)})
    return out


__all__ = [
    "ACCESS_CALLS_PER_TICK",
    "MAX_PASS_AGE_SECONDS",
    "MIN_BUDGET",
    "cursor_state",
    "open_pass",
    "page_rows",
    "run_access",
]
