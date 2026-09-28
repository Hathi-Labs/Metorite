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
    organization_id: str | None = None,
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
) -> list[dict]:
    """Version history for one agent instance, newest first."""
    if not agent_name:
        return []
    try:
        return await asyncio.to_thread(
            _sync_history, agent_name, path, limit, instance,
            _caller_tenant(organization_id),
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("blob_store.history_failed", agent=agent_name, error=str(exc)[:120])
        return []


# ---------------------------------------------------------------------------
# Rehydrate — restore an agent's workspace folders from the store on load
# ---------------------------------------------------------------------------


async def rehydrate_workspace(
    agent_name: str, workspace_root: str, *, instance: str = "",
    organization_id: str | None = None,
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

    Returns the number of files restored/updated. Never raises.
    """
    from pathlib import Path  # noqa: PLC0415

    if not agent_name or not workspace_root:
        return 0
    try:
        org = _caller_tenant(organization_id)
        metas = await list_files(
            agent_name, instance=instance, organization_id=org,
        )
        if not metas:
            return 0
        root = Path(workspace_root)
        restored = 0
        for meta in metas:
            dest = root / meta.path
            # Skip if disk already has this exact version.
            if dest.exists():
                try:
                    if _sha256(dest.read_bytes()) == meta.sha256:
                        continue
                except Exception:  # noqa: BLE001
                    pass
            data = await get_file(
                agent_name, meta.path, instance=instance, organization_id=org,
            )
            if data is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
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
