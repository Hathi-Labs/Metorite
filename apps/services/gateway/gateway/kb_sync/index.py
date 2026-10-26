"""The index pass of one Knowledge file source (WS-61 KC-5a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.2 step 4, §7.3, §8.4,
§16, §17 and §22 (KC-5a).

The pass reads the TEXT of the files that a member chose, and writes it to
``kb_documents`` and to the index (``kb_chunks`` with ``source_kind =
'document'``). It is dark: it needs ``KNOWLEDGE_INDEX`` (D4), and with the flag
empty the loop starts no index run, makes no provider call and opens no
session for it.

**What is in scope (D1).** A file is in scope when it is ``live``, of kind
``file``, and any ancestor folder has a row in ``kb_source_includes``. The row
is keyed by the PROVIDER id of the folder, never by a node row, because a capped
discovery deletes every node. The walk goes down from the include rows, is
cycle-safe, and stops at :data:`DEPTH` levels. With no include row nothing is
read. The chunks go to the ``me`` space of the SOURCE OWNER, so only the owner
can read them until a filing plan moves them (KC-6). The space is made when it
is missing (D8).

**The steps** (:func:`run_index`, the run kind ``index``):

(a) ONE session reads the source row ``FOR SHARE`` and takes the run lock.
(b) ONE short transaction deletes the documents of nodes that left scope (a
    gone node, or a folder that lost its include row). The trigger of
    migration 310 takes their chunks.
(c) ONE short transaction reads the next :data:`MAX_FILES` pending files and,
    when there are any, makes the owner's ``me`` space.
(d) For each file, in the order of the read, inside the budget:

    * a node over :data:`MAX_FETCH_BYTES` is never fetched. It gets a refusal
      row;
    * the fetch, through the adapter, with NO session open (``_call`` holds the
      401 retry). The Google adapter raises ``NotImplementedError("KC-5b")``
      until the real fetch ships, and the run then ends ``stopped`` with the
      code ``fetch_unavailable`` and writes no row;
    * ``extract_text`` and the chunker run in ONE worker thread under a
      process-wide semaphore of 1 (:func:`_gate`), with the 20 s deadline of
      ``extract_text``;
    * ONE short transaction: the guard, a re-read of the node (a moved stamp
      skips the file), the upsert of the document and the replace of its
      chunks by sha, so an unchanged chunk keeps its row and its id.

**The stamp (D3).** ``kb_documents.sha`` holds the PROVIDER stamp of the node:
``content_hash``, else ``m:<modified_at>``. It is NOT a hash of the text. A
node with neither is name only and is never read. A file is pending while it
has no document row, or while the sha differs from its stamp. So an unchanged
file costs ZERO fetches and ZERO extractions. :data:`STAMP_SQL` is the one
definition, and the pending read, the re-read and the count all use it.

**The budget.** At most :data:`MAX_FILES` files, :data:`MAX_BYTES` of fetched
bytes and :data:`MAX_SECONDS` of wall time in one run, checked between files. A
run that stops there ends ``ok``. The next tick goes on from the pending read.

**Errors.**

* a permanent refusal for ONE file — ``AttachmentRefused`` (a suffix that
  nothing reads, a cap, a broken file, a deadline), a size over the cap, or an
  HTTP 403 or 404 on that file — writes a document row with ``parser = 'none'``,
  empty text, ``sha`` = the stamp, ``truncated`` true and NO chunks. The file is
  read again only when its stamp changes;
* a transient error — ``RateLimited``, a network ``SourceError`` — writes no
  row and ends the run ``stopped`` or ``failed``. Neither marks the source
  (``kb_sources.last_error``): the change feed owns the health of a source.
  ``NeedsReconnect`` takes the KC-3 path.

**The lock order (KC-3h).** The module doc of ``engine.py`` holds the one order:
the connection row, the ``kb_sources`` row, then the children. This pass never
locks the connection row. Each write transaction takes the source row FIRST with
``discovery.lock_source`` (``FOR KEY SHARE``, because the pass never writes the
source row), then the children in this order: ``kb_source_nodes``,
``kb_documents``, ``kb_chunks``, then the run row (the heartbeat). The
transactions:

* step (a): the source row ``FOR SHARE``, then the run row;
* step (b): the guard, then the DELETE of ``kb_documents`` (it reads the nodes,
  and its trigger deletes ``kb_chunks``), then the heartbeat;
* step (c): the guard, the pending read of the nodes, the ``kb_spaces`` insert
  (no row of the source family), then the heartbeat;
* a file (d): the guard, the node re-read, the ``kb_documents`` upsert (its
  foreign key takes ``FOR KEY SHARE`` on the node), the ``kb_chunks`` delete and
  upsert, then the heartbeat;
* the close: the run row only. An error close is ``engine._SourceRun.close``.

A disconnect holds the source row ``FOR UPDATE``. The guard waits for it, reads
the row again, finds no live row, and the run stops with no write. Fences:
``tests/unit/test_kb_sync_engine.py::TestTheLockOrder`` and, R8,
``tests/unit/test_kc_index_r8.py``.

**No text in a log.** A log line holds the org, the source, the counts and a
short code. Never a file name, a stamp or a word of a document.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from acb_common import get_logger
from acb_skills import attachment_text
from sqlalchemy import text

from gateway.db import tenant_session
from gateway.kb_index.chunk_doc import DocChunks, chunk_document
from gateway.kb_sources.base import (
    KIND_FILE,
    KbSource,
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceError,
    SourceNode,
)
from gateway.kb_sync import discovery, engine, runs

_log = get_logger("kb_sync.index")

#: The most files, bytes and seconds of one run. Checked between files.
MAX_FILES = 50
MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 45.0

#: A node over this size is never fetched. It is the cap of ``extract_text``.
MAX_FETCH_BYTES = attachment_text.MAX_FILE_BYTES

#: The wait for the worker thread: the deadline of ``extract_text`` plus a margin.
EXTRACT_WAIT_SECONDS = attachment_text.DEADLINE_SECONDS + 5.0

#: The levels of the include walk (D1).
DEPTH = 100

#: The ``parser`` of a refused file.
PARSER_NONE = "none"

#: The code of a run that the adapter could not serve (KC-5b builds the fetch).
CODE_FETCH_UNAVAILABLE = "fetch_unavailable"

#: The provider answers that mean "this file, for ever": no access, or gone.
PERMANENT_CODES = frozenset({"http_403", "http_404"})

#: The provider stamp of a node (D3): ``content_hash``, else ``m:<modified_at>``.
#: NULL means a name-only node, which is never read.
STAMP_SQL = (
    "COALESCE(NULLIF(btrim(n.content_hash), ''), CASE WHEN n.modified_at IS NOT NULL "
    "THEN 'm:' || to_char(n.modified_at AT TIME ZONE 'UTC', "
    "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') END)"
)

#: ⚠️ The files in scope (D1). ``inc`` walks DOWN from the include rows through
#: live folders: ``UNION`` plus the depth bound make a cycle end. ``scope`` is
#: each live file whose parent is in ``inc``. A capped discovery deletes the
#: nodes and keeps the include rows, so the walk starts from the rows.
_SCOPE_SQL = (
    "WITH RECURSIVE inc(ext_id, depth) AS ("
    "SELECT i.folder_ext_id, 0 FROM kb_source_includes i "
    "WHERE i.organization_id = CAST(:org AS uuid) AND i.source_id = CAST(:source AS uuid) "
    "UNION "
    "SELECT c.ext_id, inc.depth + 1 FROM kb_source_nodes c "
    "JOIN inc ON c.parent_ext_id = inc.ext_id "
    "WHERE c.organization_id = CAST(:org AS uuid) AND c.source_id = CAST(:source AS uuid) "
    "AND c.kind = 'folder' AND c.state = 'live' AND inc.depth + 1 < CAST(:depth AS int)), "
    "scope AS (SELECT n.id, n.ext_id, n.parent_ext_id, n.name, n.mime, n.size, "
    "n.modified_at, n.web_url, n.drive_id, n.content_hash, " + STAMP_SQL + " AS stamp "
    "FROM kb_source_nodes n "
    "WHERE n.organization_id = CAST(:org AS uuid) AND n.source_id = CAST(:source AS uuid) "
    "AND n.kind = 'file' AND n.state = 'live' "
    "AND n.parent_ext_id IN (SELECT ext_id FROM inc)) "
)

_SOURCE_SQL = engine._SOURCE_SQL

#: The next files to read: in scope, with a stamp, and no document yet or a
#: document of another stamp. The newest first.
_PENDING_SQL = _SCOPE_SQL + (
    "SELECT s.id, s.ext_id, s.parent_ext_id, s.name, s.mime, s.size, s.modified_at, "
    "s.web_url, s.drive_id, s.content_hash, s.stamp FROM scope s "
    "LEFT JOIN kb_documents d ON d.node_id = s.id "
    "AND d.organization_id = CAST(:org AS uuid) "
    "WHERE s.stamp IS NOT NULL AND (d.id IS NULL OR d.sha <> s.stamp) "
    "ORDER BY s.modified_at DESC NULLS LAST, s.id LIMIT CAST(:n AS int)"
)

#: What the loop needs to know: the files to read, plus the documents of nodes
#: that left scope (the pass deletes those).
PENDING_COUNT_SQL = _SCOPE_SQL + (
    "SELECT (SELECT count(*) FROM scope s LEFT JOIN kb_documents d ON d.node_id = s.id "
    "AND d.organization_id = CAST(:org AS uuid) "
    "WHERE s.stamp IS NOT NULL AND (d.id IS NULL OR d.sha <> s.stamp)) "
    "+ (SELECT count(*) FROM kb_documents x JOIN kb_source_nodes y ON y.id = x.node_id "
    "AND y.organization_id = CAST(:org AS uuid) "
    "WHERE x.organization_id = CAST(:org AS uuid) AND y.source_id = CAST(:source AS uuid) "
    "AND y.id NOT IN (SELECT id FROM scope)) AS pending"
)

#: The documents of nodes that left scope. The trigger of migration 310 takes
#: their chunks, so a gone node leaves no chunk.
_DELETE_STALE_SQL = _SCOPE_SQL + (
    "DELETE FROM kb_documents d USING kb_source_nodes n "
    "WHERE d.organization_id = CAST(:org AS uuid) "
    "AND n.organization_id = CAST(:org AS uuid) "
    "AND n.source_id = CAST(:source AS uuid) AND d.node_id = n.id "
    "AND n.id NOT IN (SELECT id FROM scope)"
)

#: The node again, inside the write transaction: still in scope, and its stamp.
_NODE_NOW_SQL = _SCOPE_SQL + (
    "SELECT s.id, s.stamp FROM scope s WHERE s.id = CAST(:node AS uuid)"
)

_UPSERT_DOC_SQL = (
    "INSERT INTO kb_documents (organization_id, node_id, text, sha, extracted_at, parser, "
    "truncated, created_at, updated_at) "
    "VALUES (CAST(:org AS uuid), CAST(:node AS uuid), :text, :sha, now(), :parser, "
    ":truncated, now(), now()) "
    "ON CONFLICT (node_id) DO UPDATE SET text = EXCLUDED.text, sha = EXCLUDED.sha, "
    "extracted_at = now(), parser = EXCLUDED.parser, truncated = EXCLUDED.truncated, "
    "updated_at = now() RETURNING id"
)

#: The chunks of one document whose sha the new text no longer has.
_DELETE_CHUNKS_SQL = (
    "DELETE FROM kb_chunks WHERE organization_id = CAST(:org AS uuid) "
    "AND source_kind = 'document' AND source_id = CAST(:doc AS uuid) "
    "AND NOT (content_sha = ANY(CAST(:shas AS text[])))"
)

#: The chunks of one document, by sha. A kept chunk keeps its row and its id,
#: and takes the new order and the space. ``xmax = 0`` marks a new row.
_UPSERT_CHUNKS_SQL = (
    "INSERT INTO kb_chunks (organization_id, space_id, source_kind, source_id, page_id, "
    "ordinal, heading_path, raw, content_sha, valid_from, metadata) "
    "SELECT CAST(:org AS uuid), CAST(:space AS uuid), 'document', CAST(:doc AS uuid), NULL, "
    "t.ordinal, t.heading_path, t.raw, t.sha, "
    "COALESCE(CAST(:valid_from AS date), current_date), CAST(t.meta AS jsonb) "
    "FROM unnest(CAST(:ordinals AS int[]), CAST(:paths AS text[]), CAST(:raws AS text[]), "
    "CAST(:shas AS text[]), CAST(:metas AS text[])) AS t(ordinal, heading_path, raw, sha, meta) "
    "ON CONFLICT (organization_id, source_kind, source_id, content_sha) "
    "DO UPDATE SET ordinal = EXCLUDED.ordinal, space_id = EXCLUDED.space_id, "
    "metadata = EXCLUDED.metadata "
    "RETURNING content_sha, (xmax = 0) AS inserted"
)


@dataclass(frozen=True)
class _Pending:
    """One file to read, as the pending read gives it."""

    id: str
    ext_id: str
    parent_ext_id: str | None
    name: str
    mime: str | None
    size: int | None
    modified_at: datetime | None
    web_url: str | None
    drive_id: str | None
    content_hash: str | None
    stamp: str

    def node(self) -> SourceNode:
        return SourceNode(
            ext_id=self.ext_id, parent_ext_id=self.parent_ext_id, kind=KIND_FILE,
            name=self.name, mime=self.mime, size=self.size, modified_at=self.modified_at,
            web_url=self.web_url, drive_id=self.drive_id, content_hash=self.content_hash)

    def valid_from(self) -> date | None:
        return self.modified_at.date() if self.modified_at is not None else None


class _FetchUnavailable(Exception):
    """The adapter has no fetch yet (KC-5b). The run ends ``stopped``."""


_gate_state: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _gate() -> asyncio.Semaphore:
    """The process-wide semaphore of 1 that guards extraction.

    One extraction at a time keeps the memory of a parse (a 25 MB file) to
    one file for the whole gateway worker. It is made for the running loop, so
    a test that runs more than one loop never meets a gate of a dead loop.
    """
    global _gate_state
    loop = asyncio.get_running_loop()
    if _gate_state is None or _gate_state[0] is not loop:
        _gate_state = (loop, asyncio.Semaphore(1))
    return _gate_state[1]


def _read_file(data: bytes, suffix: str, name: str) -> tuple[attachment_text.Extracted, DocChunks]:
    """The text and the chunks of one file. Pure, so it runs in a worker thread."""
    got = attachment_text.extract_text(data, suffix)
    return got, chunk_document(name, got.text, got.kind)


async def _extract(data: bytes, suffix: str,
                   name: str) -> tuple[attachment_text.Extracted, DocChunks]:
    """:func:`_read_file` in a worker thread, ONE at a time in the process."""
    async with _gate():
        return await asyncio.wait_for(
            asyncio.to_thread(_read_file, data, suffix, name), timeout=EXTRACT_WAIT_SECONDS)


class _IndexRun(engine._SourceRun):
    """One index pass of one source, after it took the lock.

    It reuses three parts of the engine's run: the 401 retry (``_call``), the
    guard (``_guard``) and the error close (``close``). It never reads the
    change feed and never locks the connection row.
    """

    def __init__(self, org: str, source_id: str, *, source: KbSource,
                 should_stop: Callable[[], bool], owner: str, connection_id: str,
                 lock: runs.Started) -> None:
        super().__init__(org, source_id, runs.KIND_INDEX, source=source,
                         should_stop=should_stop, owner=owner,
                         connection_id=connection_id, lock=lock, token=None, capped=False)
        self.space_id: str | None = None
        self.bytes = 0
        self.counts = {"pending": 0, "files": 0, "refused": 0, "moved": 0, "chunks": 0,
                       "removed": 0, "deleted": 0, "bytes": 0, "budget": False}

    # ── Step (b): the documents that left scope ──────────────────────────

    async def _clean(self) -> None:
        async with tenant_session(self.org) as db:
            await self._guard(db)
            result = await db.execute(text(_DELETE_STALE_SQL), self._params())
            self.counts["deleted"] = int(result.rowcount or 0)
            await runs.progress(db, self.org, self.run_id, self.counts)

    def _params(self) -> dict[str, Any]:
        return {"org": self.org, "source": self.source_id, "depth": DEPTH}

    # ── Step (c): the files to read, and the owner's me space ────────────

    async def _plan(self) -> list[_Pending]:
        from gateway.routes.knowledge import core

        async with tenant_session(self.org) as db:
            await self._guard(db)
            found = (await db.execute(text(_PENDING_SQL),
                                      {**self._params(), "n": MAX_FILES})).fetchall()
            rows = [_Pending(
                id=str(r.id), ext_id=str(r.ext_id), parent_ext_id=r.parent_ext_id,
                name=str(r.name), mime=r.mime, size=r.size, modified_at=r.modified_at,
                web_url=r.web_url, drive_id=r.drive_id, content_hash=r.content_hash,
                stamp=str(r.stamp)) for r in found]
            if rows:
                # D8: the owner's me space, made when it is missing.
                me = {"org": self.org, "slug": core.ME_SLUG, "name": core.ME_NAME,
                      "purpose": core.ME_PURPOSE, "me": self.owner.strip().lower()}
                await db.execute(text(core.INSERT_ME_SPACE_SQL), me)
                space = (await db.execute(text(core.SELECT_ME_SPACE_SQL), me)).first()
                self.space_id = str(space.id)
            self.counts["pending"] = len(rows)
            await runs.progress(db, self.org, self.run_id, self.counts)
        return rows

    # ── Step (d): one file ───────────────────────────────────────────────

    async def _store(self, row: _Pending, *, parser: str, body: str, truncated: bool,
                     doc: DocChunks | None) -> bool:
        """The document row and its chunks, in ONE short transaction. False when
        the node moved (its stamp changed, or it left scope): nothing is written."""
        chunks = doc.chunks if doc is not None else ()
        async with tenant_session(self.org) as db:
            # The lock order: the source row first, then nodes, documents, chunks.
            await self._guard(db)
            now = (await db.execute(text(_NODE_NOW_SQL),
                                    {**self._params(), "node": row.id})).first()
            if now is None or now.stamp != row.stamp:
                self.counts["moved"] += 1
                await runs.progress(db, self.org, self.run_id, self.counts)
                return False
            made = (await db.execute(text(_UPSERT_DOC_SQL), {
                "org": self.org, "node": row.id, "text": body, "sha": row.stamp,
                "parser": parser, "truncated": truncated})).first()
            doc_id = str(made.id)
            shas = [c.content_sha for c in chunks]
            gone = await db.execute(text(_DELETE_CHUNKS_SQL), {
                "org": self.org, "doc": doc_id, "shas": shas})
            self.counts["removed"] += int(gone.rowcount or 0)
            if chunks:
                written = (await db.execute(text(_UPSERT_CHUNKS_SQL), {
                    "org": self.org, "space": self.space_id, "doc": doc_id,
                    "valid_from": row.valid_from(),
                    "ordinals": [c.ordinal for c in chunks],
                    "paths": [c.heading_path for c in chunks],
                    "raws": [c.raw for c in chunks],
                    "shas": shas,
                    "metas": [c.metadata_json() for c in chunks],
                })).fetchall()
                self.counts["chunks"] += sum(1 for w in written if w.inserted)
            await runs.progress(db, self.org, self.run_id, self.counts)
        return True

    async def _refuse(self, row: _Pending, why: str) -> None:
        """A permanent refusal: a row with no text and no chunk, kept until the
        stamp changes."""
        if await self._store(row, parser=PARSER_NONE, body="", truncated=True, doc=None):
            self.counts["refused"] += 1
            _log.info("knowledge.index_file_refused", organization_id=self.org,
                      source_id=self.source_id, why=why)

    async def _file(self, row: _Pending) -> None:
        if row.size is not None and row.size > MAX_FETCH_BYTES:
            await self._refuse(row, "too_large")
            return
        node = row.node()

        async def get(cred: SourceCredential) -> Any:
            return await self.source.fetch_document(cred, node)

        try:
            fetched = await self._call(get)
        except NotImplementedError:
            raise _FetchUnavailable from None
        except (NeedsReconnect, RateLimited):
            raise
        except SourceError as exc:
            if exc.code in PERMANENT_CODES:
                await self._refuse(row, str(exc.code))
                return
            raise
        self.bytes += len(fetched.data)
        self.counts["bytes"] = self.bytes
        try:
            got, doc = await _extract(fetched.data, fetched.suffix, row.name)
        except attachment_text.AttachmentRefused:
            await self._refuse(row, "refused")
            return
        except TimeoutError:
            await self._refuse(row, "deadline")
            return
        except Exception as exc:  # a parser bug must not stall the whole source
            _log.warning("knowledge.index_extract_failed", organization_id=self.org,
                         source_id=self.source_id, error=type(exc).__name__)
            await self._refuse(row, "extract_failed")
            return
        finally:
            fetched = None  # let go of the bytes at once
        wrote = await self._store(row, parser=got.kind[:32], body=got.text,
                                  truncated=bool(got.stopped or doc.truncated), doc=doc)
        if wrote:
            self.counts["files"] += 1

    async def run(self) -> None:
        """Steps (b) to (d)."""
        await self._clean()
        rows = await self._plan()
        began = time.monotonic()
        for row in rows:
            if self.should_stop():
                raise engine._Shutdown()
            if self.bytes >= MAX_BYTES or time.monotonic() - began >= MAX_SECONDS:
                self.counts["budget"] = True
                return
            await self._file(row)

    async def close_pass(self) -> engine.RunOutcome:
        """Close the run ``ok``. The run row only, so no source lock."""
        async with tenant_session(self.org) as db:
            await runs.finish(db, self.org, self.run_id, runs.STATUS_OK, None, self.counts)
        return engine.RunOutcome(runs.STATUS_OK, None, self.counts, self.run_id)


async def pending_count(db: Any, org: str, source_id: str) -> int:
    """The files to read plus the documents to delete, for the due rule of the
    loop. A read only. The caller's session; no provider call."""
    row = (await db.execute(text(PENDING_COUNT_SQL), {
        "org": org, "source": source_id, "depth": DEPTH})).first()
    return int(row.pending or 0) if row is not None else 0


async def _lock(org: str, source_id: str) -> tuple[Any, runs.Started] | None:
    """Step (a), in ONE session. ``None`` when the source is not ``ready``.
    Raises ``SyncRunning``."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_SOURCE_SQL), {"org": org, "source": source_id})).first()
        if row is None or row.state != engine.STATE_READY:
            return None
        lock = await runs.start(db, org, source_id, runs.KIND_INDEX)
    return row, lock


async def run_index(
    org: str, source_id: str, *, source: KbSource,
    should_stop: Callable[[], bool] = lambda: False,
) -> engine.RunOutcome:
    """Run one index pass of one source, up to the budget of a run.

    *org* comes from the loop, which read it from the organization table, never
    from a request. The member of the credential, and the owner of the chunks,
    come from the source row. Raises :class:`runs.SyncRunning` when another run
    holds the lock. Any other failure closes the run and returns, so the loop
    goes on.
    """
    locked = await _lock(org, source_id)
    if locked is None:
        return engine.RunOutcome("skipped", "not_ready")
    row, lock = locked
    run = _IndexRun(org, source_id, source=source, should_stop=should_stop,
                    owner=str(row.owner_email), connection_id=str(row.connection_id),
                    lock=lock)
    try:
        await run.run()
    except runs.RunLost:
        _log.warning("knowledge.index_run_lost", organization_id=org, source_id=source_id)
        return engine.RunOutcome("lost", "run_lost", run.counts, run.run_id)
    except engine._Shutdown:
        return await run.close(runs.STATUS_STOPPED, "shutdown", source_error=False)
    except engine._Disconnected:
        return await run.close(runs.STATUS_STOPPED, "disconnected", source_error=False)
    except discovery._Stopped:
        return await run.close(runs.STATUS_STOPPED, "no_connection", source_error=False)
    except _FetchUnavailable:
        out = await run.close(runs.STATUS_STOPPED, CODE_FETCH_UNAVAILABLE, source_error=False)
        _log.info("knowledge.index_fetch_unavailable", organization_id=org,
                  source_id=source_id)
        return out
    except NeedsReconnect as exc:
        out = await run.close(runs.STATUS_STOPPED, exc.code or "needs_reconnect",
                              reconnect=True)
        _log.warning("knowledge.index_needs_reconnect", organization_id=org,
                     source_id=source_id, code=out.error)
        return out
    except RateLimited:
        out = await run.close(runs.STATUS_STOPPED, "rate_limited", source_error=False)
        _log.info("knowledge.index_rate_limited", organization_id=org, source_id=source_id,
                  files=run.counts.get("files", 0))
        return out
    except SourceError as exc:
        out = await run.close(runs.STATUS_FAILED, exc.code or type(exc).__name__,
                              source_error=False)
        _log.warning("knowledge.index_failed", organization_id=org, source_id=source_id,
                     code=out.error)
        return out
    except Exception as exc:  # a background run must not raise
        out = await run.close(runs.STATUS_FAILED, type(exc).__name__, source_error=False)
        _log.warning("knowledge.index_failed", organization_id=org, source_id=source_id,
                     code=out.error)
        return out
    out = await run.close_pass()
    _log.info("knowledge.index_ok", organization_id=org, source_id=source_id,
              **{k: v for k, v in run.counts.items() if isinstance(v, int | bool)})
    return out


__all__ = [
    "CODE_FETCH_UNAVAILABLE",
    "DEPTH",
    "MAX_BYTES",
    "MAX_FETCH_BYTES",
    "MAX_FILES",
    "MAX_SECONDS",
    "PARSER_NONE",
    "PENDING_COUNT_SQL",
    "PERMANENT_CODES",
    "STAMP_SQL",
    "pending_count",
    "run_index",
]
