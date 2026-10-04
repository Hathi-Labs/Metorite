"""Agent workspace blob store (Part 2) — durable, authoritative file storage.

The three MAF agent folders — ``agent-data/`` (the agent's memory + accumulated
knowledge, an extension of its system prompt), ``inputs/`` (user uploads), and
``outputs/`` (everything the agent generates) — are backed here in Postgres. The
store is the SOURCE OF TRUTH; the on-disk workspace is a rehydratable cache. This
is the same model Mem0 uses (Postgres authoritative, disk disposable), so a wiped
volume or a migrated box restores an agent's files from the store.

Two tables (see infra/postgres/71_agent_blob_store.sql):
  agent_blob         — current content of every live file, keyed (agent, path).
  agent_file_history — append-only log of every UNIQUE version (by sha256) an
                       agent created/modified, so every version is trackable and
                       directly retrievable.

Design notes:
  • Keyed by (agent_name, workspace-relative POSIX path). agent_name is the only
    tenant key, so this is portable to a second tenant deployment's MAF agents unchanged.
  • Only the three visible folders are stored (agent-data/inputs/outputs); other
    workspace files (source, .git, caches) are NOT — they come from the agent
    repo, not from accumulated state.
  • Graceful degradation: if the DB is unavailable, every function is a no-op /
    returns empty, so agents keep working off the local disk cache.
  • The underlying acb_graph session is sync; the public API is async (wrapped in
    asyncio.to_thread), matching mem0_client.
  • Every read and write binds a tenant (WS-27bm S15, ``projects_ai_chat.md``
    §21). ``agent_blob`` and ``agent_file_history`` are FORCE RLS in production,
    so an unbound write was refused and the store held no row. A caller may pass
    ``organization_id``. Otherwise :func:`_caller_tenant` reads the tenant bound
    on the caller's frame, BEFORE the worker-thread hop. With no tenant the sync
    helper raises ``TenantUnbound``, and the public function logs and returns
    its empty answer. Nothing falls back to an unbound session.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from dataclasses import dataclass

from acb_common import get_logger

_log = get_logger("acb_memory.blob_store")

# The three visible workspace folders that are backed by the store. Mirrors
# acb_skills' _VISIBLE_DIRS / the gateway's _VISIBLE_WORKSPACE_DIRS.
STORE_FOLDERS = ("agent-data", "inputs", "outputs")

# Sentinel sha for delete history rows (so a delete never dedupe-collides with a
# prior content version at the same path).
_DELETE_SHA = "0" * 64


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def folder_of(path: str) -> str | None:
    """Return the visible folder a workspace-relative path belongs to, or None.

    "agent-data/x.md" → "agent-data"; "outputs/a/b.txt" → "outputs";
    "config.json" → None (not a stored folder).
    """
    clean = path.replace("\\", "/").lstrip("/")
    first = clean.split("/", 1)[0]
    return first if first in STORE_FOLDERS else None


def is_stored_path(path: str) -> bool:
    """True when *path* lives under one of the three backed folders."""
    return folder_of(path) is not None


@dataclass
class BlobMeta:
    agent_name: str
    path: str
    folder: str
    sha256: str
    size: int
    mime_type: str


def _caller_tenant(organization_id: str | None) -> str | None:
    """The tenant for one blob call: the explicit one, else the bound one.

    The bound one is the async seam's tenant (``acb_common.db.current_tenant``).
    A gateway request binds it from the authenticated identity, and a run binds
    it at run start. Call this on the event-loop frame, because a ContextVar
    does not cross ``asyncio.to_thread``. It never reads a tenant from request
    input. ``None`` means no tenant, and the sync helper then refuses.
    """
    if organization_id:
        return organization_id
    try:
        from acb_common.db import current_tenant
    except ImportError:
        return None
    bound = current_tenant()
    return str(bound) if bound else None


# ---------------------------------------------------------------------------
# Internal sync core (runs in a thread; each helper opens its own session)
# ---------------------------------------------------------------------------


def _sync_put(
    agent_name: str,
    path: str,
    data: bytes,
    mime_type: str,
    *,
    action: str,
    run_id: str | None,
    session_id: str | None,
    actor: str,
    instance: str = "",
    organization_id: str | None = None,
) -> BlobMeta | None:
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    folder = folder_of(path)
    if folder is None:
        return None
    sha = _sha256(data)
    size = len(data)
    with tenant_session(organization_id) as s:
        # Upsert current content.
        s.execute(
            text(
                "INSERT INTO agent_blob "
                "(agent_name, instance, path, folder, content, sha256, size, "
                " mime_type, updated_at) "
                "VALUES (:a, :i, :p, :f, :c, :sha, :sz, :m, now()) "
                "ON CONFLICT (agent_name, instance, path) DO UPDATE SET "
                "content = EXCLUDED.content, sha256 = EXCLUDED.sha256, "
                "size = EXCLUDED.size, mime_type = EXCLUDED.mime_type, "
                "updated_at = now()"
            ),
            {"a": agent_name, "i": instance, "p": path, "f": folder, "c": data,
             "sha": sha, "sz": size, "m": mime_type},
        )
        # Append a version-history row (deduped per instance, so two people
        # writing byte-identical content each keep their own provenance).
        s.execute(
            text(
                "INSERT INTO agent_file_history "
                "(agent_name, instance, path, folder, sha256, size, mime_type, "
                " action, run_id, session_id, actor) "
                "VALUES (:a, :i, :p, :f, :sha, :sz, :m, :act, :rid, :sid, :actor) "
                "ON CONFLICT (agent_name, instance, path, sha256, action) DO NOTHING"
            ),
            {"a": agent_name, "i": instance, "p": path, "f": folder, "sha": sha,
             "sz": size, "m": mime_type, "act": action, "rid": run_id,
             "sid": session_id, "actor": actor},
        )
        s.commit()
    return BlobMeta(agent_name, path, folder, sha, size, mime_type)


def _sync_get(
    agent_name: str, path: str, instance: str = "",
    organization_id: str | None = None,
) -> bytes | None:
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with tenant_session(organization_id) as s:
        row = s.execute(
            text(
                "SELECT content FROM agent_blob "
                "WHERE agent_name = :a AND instance = :i AND path = :p"
            ),
            {"a": agent_name, "i": instance, "p": path},
        ).fetchone()
    if row is None:
        return None
    content = row[0]
    return bytes(content) if content is not None else None


def _sync_list(
    agent_name: str, prefix: str | None, instance: str = "",
    organization_id: str | None = None,
) -> list[BlobMeta]:
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    sql = (
        "SELECT path, folder, sha256, size, mime_type FROM agent_blob "
        "WHERE agent_name = :a AND instance = :i"
    )
    params: dict = {"a": agent_name, "i": instance}
    if prefix:
        sql += " AND path LIKE :pfx"
        params["pfx"] = prefix.rstrip("/") + "/%"
    sql += " ORDER BY path"
    with tenant_session(organization_id) as s:
        rows = s.execute(text(sql), params).fetchall()
    return [
        BlobMeta(agent_name, r[0], r[1], r[2], int(r[3]), r[4]) for r in rows
    ]


def _sync_delete(
    agent_name: str, path: str, *, run_id: str | None, session_id: str | None,
    actor: str, instance: str = "", organization_id: str | None = None,
) -> None:
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    folder = folder_of(path)
    if folder is None:
        return
    with tenant_session(organization_id) as s:
        s.execute(
            text(
                "DELETE FROM agent_blob "
                "WHERE agent_name = :a AND instance = :i AND path = :p"
            ),
            {"a": agent_name, "i": instance, "p": path},
        )
        s.execute(
            text(
                "INSERT INTO agent_file_history "
                "(agent_name, instance, path, folder, sha256, size, mime_type, "
                " action, run_id, session_id, actor) "
                "VALUES (:a, :i, :p, :f, :sha, 0, '', 'delete', :rid, :sid, :actor) "
                "ON CONFLICT (agent_name, instance, path, sha256, action) DO NOTHING"
            ),
            {"a": agent_name, "i": instance, "p": path, "f": folder,
             "sha": _DELETE_SHA, "rid": run_id, "sid": session_id, "actor": actor},
        )
        s.commit()


def _sync_history(
    agent_name: str, path: str | None, limit: int, instance: str = "",
    organization_id: str | None = None, session_id: str | None = None,
) -> list[dict]:
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    sql = (
        "SELECT path, folder, sha256, size, mime_type, action, run_id, "
        "session_id, actor, created_at FROM agent_file_history "
        "WHERE agent_name = :a AND instance = :i"
    )
    params: dict = {"a": agent_name, "i": instance}
    if path:
        sql += " AND path = :p"
        params["p"] = path
    if session_id:
        sql += " AND session_id = :sid"
        params["sid"] = session_id
    sql += " ORDER BY created_at DESC LIMIT :lim"
    params["lim"] = max(1, min(limit, 1000))
    with tenant_session(organization_id) as s:
        rows = s.execute(text(sql), params).fetchall()
    return [
        {
            "path": r[0], "folder": r[1], "sha256": r[2], "size": int(r[3]),
            "mime_type": r[4], "action": r[5], "run_id": r[6],
            "session_id": r[7], "actor": r[8], "created_at": str(r[9]),
        }
        for r in rows
    ]


_IN_KEPT_TREE = "(left(path, 7) = 'inputs/' OR left(path, 8) = 'outputs/')"


def _sync_session_paths(
    session_id: str, instance: str, organization_id: str | None,
) -> list[tuple[str, str]]:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        rows = s.execute(
            text(
                "SELECT DISTINCT agent_name, path FROM agent_file_history "
                "WHERE session_id = :sid AND instance IN (:i, '') "
                f"AND action <> 'delete' AND {_IN_KEPT_TREE}"
            ),
            {"sid": session_id, "i": instance},
        ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def _sync_purge(
    instance: str, organization_id: str | None, *,
    prefixes: tuple[str, ...], paths: tuple[tuple[str, str], ...],
    session_id: str | None,
) -> int:
    from acb_graph import tenant_session
    from sqlalchemy import text

    gone = 0
    with tenant_session(organization_id) as s:
        for prefix in prefixes:
            # left() and not LIKE: a slug may hold '_', which LIKE reads as
            # any one character.
            for table in ("agent_blob", "agent_file_history"):
                gone += s.execute(
                    text(
                        f"DELETE FROM {table} WHERE instance IN (:i, '') "
                        "AND left(path, length(:p)) = :p"
                    ),
                    {"i": instance, "p": prefix},
                ).rowcount or 0
        for agent, path in paths:
            for table in ("agent_blob", "agent_file_history"):
                gone += s.execute(
                    text(
                        f"DELETE FROM {table} WHERE agent_name = :a "
                        "AND instance IN (:i, '') AND path = :p"
                    ),
                    {"a": agent, "i": instance, "p": path},
                ).rowcount or 0
        if session_id:
            gone += s.execute(
                text(
                    "DELETE FROM agent_file_history WHERE session_id = :sid "
                    f"AND instance IN (:i, '') AND {_IN_KEPT_TREE}"
                ),
                {"sid": session_id, "i": instance},
            ).rowcount or 0
        s.commit()
    return gone


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def put_file(
    agent_name: str,
    path: str,
    data: bytes,
    *,
    mime_type: str = "application/octet-stream",
    action: str = "modify",
    run_id: str | None = None,
    session_id: str | None = None,
    actor: str = "agent",
    instance: str = "",
    organization_id: str | None = None,
) -> BlobMeta | None:
    """Write-through: store *data* at (agent, instance, path) + a history version.

    No-op (returns None) for paths outside the three backed folders, or on any DB
    error (graceful — the disk cache still holds the file).

        instance:  Partition key — ``""`` (shared, and every pre-migration row),
                   ``"u:<email>"`` for a personal agent, ``"t:<team>"`` for a
                   team one. Defaults to ``""`` so a caller that doesn't know
                   about instances sees exactly what it saw before migration 136.
    """
    if not agent_name or not is_stored_path(path):
        return None
    try:
        return await asyncio.to_thread(
            _sync_put, agent_name, path, data, mime_type,
            action=action, run_id=run_id, session_id=session_id, actor=actor,
            instance=instance, organization_id=_caller_tenant(organization_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("blob_store.put_failed", agent=agent_name, path=path, error=str(exc)[:200])
        return None


async def get_file(
    agent_name: str, path: str, *, instance: str = "",
    organization_id: str | None = None,
) -> bytes | None:
    """Fault-in read: return stored bytes for (agent, instance, path), or None."""
    if not agent_name or not is_stored_path(path):
        return None
    try:
        return await asyncio.to_thread(
            _sync_get, agent_name, path, instance,
            _caller_tenant(organization_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("blob_store.get_failed", agent=agent_name, path=path, error=str(exc)[:120])
        return None


async def list_files(
    agent_name: str, prefix: str | None = None, *, instance: str = "",
    organization_id: str | None = None,
) -> list[BlobMeta]:
    """All stored files for one agent instance (optionally under *prefix*)."""
    if not agent_name:
        return []
    try:
        return await asyncio.to_thread(
            _sync_list, agent_name, prefix, instance,
            _caller_tenant(organization_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("blob_store.list_failed", agent=agent_name, error=str(exc)[:120])
        return []


async def delete_file(
    agent_name: str,
    path: str,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    actor: str = "agent",
    instance: str = "",
    organization_id: str | None = None,
) -> None:
    """Write-through delete: drop the current blob + record a delete version."""
    if not agent_name or not is_stored_path(path):
        return
    try:
        await asyncio.to_thread(
            _sync_delete, agent_name, path,
            run_id=run_id, session_id=session_id, actor=actor,
            instance=instance, organization_id=_caller_tenant(organization_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("blob_store.delete_failed", agent=agent_name, path=path, error=str(exc)[:200])


async def file_history(
    agent_name: str, path: str | None = None, limit: int = 200,
    *, instance: str = "", organization_id: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Version history for one agent instance, newest first.

    *session_id* keeps only the rows that one chat session wrote. The
    gateway's session routes ask for them, to tell which loose file of a
    shared agent's tenant dir a session wrote (H-227).
    """
    if not agent_name:
        return []
    try:
        return await asyncio.to_thread(
            _sync_history, agent_name, path, limit, instance,
            _caller_tenant(organization_id), session_id,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("blob_store.history_failed", agent=agent_name, error=str(exc)[:120])
        return []


async def session_paths(
    session_id: str, *, instance: str, organization_id: str | None = None,
) -> list[tuple[str, str]]:
    """``(agent, path)`` of each ``inputs/`` or ``outputs/`` path that one chat
    session wrote, under *instance* or the older ``''`` key.

    The gateway reads it when a member deletes a chat (H-227), to find the
    loose files that the chat began. ``[]`` on any error.
    """
    if not session_id:
        return []
    try:
        return await asyncio.to_thread(
            _sync_session_paths, session_id, instance, _caller_tenant(organization_id),
        )
    except Exception as exc:
        _log.warning("blob_store.session_paths_failed", error=str(exc)[:200])
        return []


async def purge_files(
    *, instance: str, organization_id: str | None = None,
    prefixes: tuple[str, ...] = (), paths: tuple[tuple[str, str], ...] = (),
    session_id: str | None = None,
) -> int:
    """Delete stored files AND their history, for a deleted chat (H-227).

    In one tenant, under *instance* and the older ``''`` key:

    * every row under each of *prefixes* (a thread's own folders), of any agent;
    * every row at each ``(agent, path)`` of *paths* (the loose files the chat
      began);
    * every history row of *session_id* under ``inputs/`` or ``outputs/``.

    Unlike :func:`delete_file` it keeps no delete row: a chat id can come back
    from a client, and a row that names it would hand the next owner of the
    id its files. Returns the rows deleted. Raises on a database error, so
    the caller can log that the purge did not finish.
    """
    return await asyncio.to_thread(
        _sync_purge, instance, _caller_tenant(organization_id),
        prefixes=tuple(prefixes), paths=tuple(paths), session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Rehydrate — restore an agent's workspace folders from the store on load
# ---------------------------------------------------------------------------


class _SafeDisk:
    """The rehydrate's reads and writes, through the safe opener (WS-43d).

    ``maf_coding_engine.md`` §7.5 rule B. A sandbox container can write a
    link into the dir it mounts, and the next rehydrate would then write the
    blob through it, onto a host file. ``acb_skills.safe_open`` refuses a
    link at any depth.

    ⚠️ A deliberate lazy import of ``acb_skills`` from this lower package, at
    the call site and never at module top: the opener lives in
    ``acb_skills`` (spec §7.5), and the rehydrate is the one writer here.
    Without ``acb_skills`` installed there is no sandbox either, so the
    writes fall back to the plain path calls of before.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            from acb_skills import safe_open
        except ImportError:  # no acb_skills means no sandbox on this box
            safe_open = None
        self._safe = safe_open

    def read(self, rel: str) -> bytes | None:
        try:
            if self._safe is not None:
                return self._safe.read_bytes(self.root, rel)
            path = self.root / rel
            return path.read_bytes() if path.is_file() else None
        except (OSError, ValueError):
            return None

    def write(self, rel: str, data: bytes) -> bool:
        try:
            if self._safe is not None:
                self._safe.write_bytes(self.root, rel, data)
            else:
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        except (OSError, ValueError) as exc:
            _log.warning("blob_store.rehydrate_refused", path=rel, error=str(exc)[:200])
            return False
        return True


async def rehydrate_workspace(
    agent_name: str, workspace_root: str, *, instance: str = "",
    organization_id: str | None = None,
    legacy_instance: str | None = None,
) -> int:
    """Restore agent-data/inputs/outputs from the store into *workspace_root*.

    Called when an agent loads so a wiped/migrated volume comes back from the
    authoritative store. Store is authoritative: a stored file is written to disk
    if missing OR if the disk content differs (by sha). Files only on disk (not in
    the store) are left alone — they'll be captured on their next write-through.

    *instance* selects WHICH partition is restored (``""`` = shared, today's
    behaviour). This is the seam that matters most for a personal agent: the
    on-disk workspace is a single directory, so restoring the wrong instance
    would put one person's notes in front of another. Callers that pass a
    non-empty instance must give each instance its own workspace root.

    *legacy_instance* (H-201 part 3, ``projects_ai_chat.md`` §21.15) also
    restores the rows of an older partition, for a path that *instance* does
    not hold. A shared agent's tenant dir (``o:<org>``) passes ``""``. Its rows
    from before the tenant dir existed carry ``instance=''``, and each row
    carries the tenant of the run that wrote it (S15). The read is in one
    tenant, so only this tenant's older rows come back. A row of *instance*
    always wins over a legacy row at the same path.

    Returns the number of files restored/updated. Never raises.
    """
    if not agent_name or not workspace_root:
        return 0
    try:
        org = _caller_tenant(organization_id)
        metas = await list_files(
            agent_name, instance=instance, organization_id=org,
        )
        plan: list[tuple[BlobMeta, str]] = [(m, instance) for m in metas]
        if legacy_instance is not None and legacy_instance != instance:
            held = {m.path for m in metas}
            older = await list_files(
                agent_name, instance=legacy_instance, organization_id=org,
            )
            plan += [(m, legacy_instance) for m in older if m.path not in held]
        if not plan:
            return 0
        root = Path(workspace_root)
        disk = _SafeDisk(root)
        restored = 0
        for meta, from_instance in plan:
            # Skip if disk already has this exact version.
            current = await asyncio.to_thread(disk.read, meta.path)
            if current is not None and _sha256(current) == meta.sha256:
                continue
            data = await get_file(
                agent_name, meta.path, instance=from_instance,
                organization_id=org,
            )
            if data is None:
                continue
            if await asyncio.to_thread(disk.write, meta.path, data):
                restored += 1
        if restored:
            _log.info(
                "blob_store.rehydrated",
                agent=agent_name, instance=instance or "shared", files=restored,
            )
        return restored
    except Exception as exc:  # noqa: BLE001
        _log.warning("blob_store.rehydrate_failed", agent=agent_name, error=str(exc)[:200])
        return 0
