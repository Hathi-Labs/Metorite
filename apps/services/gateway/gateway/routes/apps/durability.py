"""Custom Apps · durability — draft mirroring + git checkpoints (Phase 1).

Published apps are already Postgres-durable (``app_versions``); this module
makes the DRAFT workspace durable too. Every eligible workspace text file is
mirrored into ``app_files`` (``infra/postgres/115_app_files.sql``) on file
writes / publish / explicit sync, and a missing-or-gutted workspace is lazily
rehydrated from those rows at the read choke point (``ensure_workspace``).
Per-edit checkpoints ride the workspace's own git repo (the one
``lifecycle._git_init`` scaffolds): best-effort subprocess helpers that never
raise into a route — a failed checkpoint degrades to ``null``, never a 500.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from acb_auth import UserContext
from fastapi import Depends, HTTPException
from gateway.routes.apps._common import (
    MAX_SOURCE_FILE_BYTES,
    MAX_WORKSPACE_FILES,
    WORKSPACE_SKIP_DIRS,
    _field,
    _log,
    _tenant_session,
    _uid,
    app_workspace,
    get_app_or_404,
    manifest_entry_rel,
    parse_db_manifest,
    read_workspace_manifest,
    record_app_audit,
    require_app_user,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

# Same identity + best-effort stance as lifecycle._git_init; checkpoints are
# short-lived local ops so they get a tighter timeout than the scaffold.
_GIT_IDENT = ("-c", "user.name=Metorite", "-c", "user.email=metorite@local")
_GIT_TIMEOUT_S = 10

CHECKPOINT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class RestoreRequest(BaseModel):
    sha: str


# ── Workspace walk (pure — mirrors files.py's listing rules) ─────────────────

def list_workspace_text_files(
    workspace: Path, manifest: dict[str, Any] | None = None,
) -> list[Path]:
    """Every workspace file eligible for the ``app_files`` mirror.

    Same rules as ``files._walk_files`` (skip dot-anything, the shared
    VCS/build dirs, and directories) plus the durability-specific cuts:
    files over ``MAX_SOURCE_FILE_BYTES`` and non-UTF-8-decodable files are
    skipped — only text the files API could serve is mirrored.

    One carve-out: the manifest's resolved ``entry`` file (e.g. a T2 app's
    ``dist/bundle.html``) is always eligible even when it sits under a
    skip-dir — it's the file ``ensure_workspace``/``_workspace_ready`` depend
    on to recover a lost workspace, so it must survive the mirror. Everything
    else under that dir (a stray source map, a vendored ``node_modules``)
    stays excluded.
    """
    out: list[Path] = []
    if not workspace.is_dir():
        return out
    entry_segments = tuple(
        s for s in manifest_entry_rel(
            manifest if manifest is not None else read_workspace_manifest(workspace),
        ).split("/") if s
    )
    entry_dir_segments = entry_segments[:-1]
    for dirpath, dirnames, filenames in os.walk(workspace):
        rel_segments = tuple(
            p for p in Path(dirpath).relative_to(workspace).parts if p != "."
        )
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".")
            and (
                d not in WORKSPACE_SKIP_DIRS
                or (
                    len(rel_segments) < len(entry_dir_segments)
                    and rel_segments == entry_dir_segments[: len(rel_segments)]
                    and d == entry_dir_segments[len(rel_segments)]
                )
            )
        )
        under_skip_dir = any(seg in WORKSPACE_SKIP_DIRS for seg in rel_segments)
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            if under_skip_dir and (*rel_segments, fname) != entry_segments:
                continue
            fpath = Path(dirpath) / fname
            try:
                if not fpath.is_file():
                    continue
                if fpath.stat().st_size > MAX_SOURCE_FILE_BYTES:
                    continue
                fpath.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            out.append(fpath)
            if len(out) >= MAX_WORKSPACE_FILES:
                return out
    return out


def _read_workspace_files(workspace: Path) -> dict[str, tuple[str, str]]:
    """rel path → (content, sha256) for every eligible file (sync, executor)."""
    out: dict[str, tuple[str, str]] = {}
    for fpath in list_workspace_text_files(workspace):
        try:
            content = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(fpath.relative_to(workspace)).replace("\\", "/")
        out[rel] = (content, _sha256(content))
    return out


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Store sync (disk → app_files) ────────────────────────────────────────────

_UPSERT_SQL = text(
    """INSERT INTO app_files (app_id, path, content, sha256)
       VALUES (:app_id, :path, :content, :sha256)
       ON CONFLICT (app_id, path)
       DO UPDATE SET content = EXCLUDED.content,
                     sha256 = EXCLUDED.sha256,
                     updated_at = now()"""
)


async def sync_workspace_to_store(db: Any, row: Any) -> int:
    """Mirror the workspace into ``app_files``: upsert changed files (the sha
    skips untouched ones), delete rows whose path is gone from disk. Returns
    the number of files now in the store. Does NOT commit — every caller
    hands in a ``_tenant_session`` block, whose wrapper commits on clean exit
    (H2: a mid-block commit would end the transaction and drop the GUC)."""
    app_id = str(_field(row, "id"))
    disk = await asyncio.get_event_loop().run_in_executor(
        None, _read_workspace_files, app_workspace(row),
    )
    stored = {
        r.path: r.sha256 for r in (await db.execute(
            text("SELECT path, sha256 FROM app_files WHERE app_id = :app_id"),
            {"app_id": app_id},
        )).fetchall()
    }
    for rel, (content, sha) in disk.items():
        if stored.get(rel) == sha:
            continue
        await db.execute(_UPSERT_SQL, {
            "app_id": app_id, "path": rel, "content": content, "sha256": sha,
        })
    for rel in set(stored) - set(disk):
        await db.execute(
            text("DELETE FROM app_files WHERE app_id = :app_id AND path = :path"),
            {"app_id": app_id, "path": rel},
        )
    return len(disk)


async def sync_workspace_best_effort(row: Any) -> int | None:
    """``sync_workspace_to_store`` on its own session — never raises. The
    scaffold/publish write-throughs ride this so a mirror hiccup can't fail
    the user-visible operation. Awaited only on request paths, so the
    ambient tenant is bound; a call outside one fails closed (and lands in
    this except-all as a logged warning, keeping the best-effort contract)."""
    try:
        async with _tenant_session() as db:
            return await sync_workspace_to_store(db, row)
    except Exception as exc:
        _log.warning(
            "apps.store_sync_failed",
            app_id=str(_field(row, "id")), error=str(exc)[:160],
        )
        return None


async def mirror_app_file(app_id: str, path: str, content: str) -> None:
    """Best-effort single-file upsert (own session, never raises) — the PUT
    write path's cheap write-through; full reconciliation stays with sync."""
    try:
        async with _tenant_session() as db:
            await db.execute(_UPSERT_SQL, {
                "app_id": app_id, "path": path,
                "content": content, "sha256": _sha256(content),
            })
    except Exception as exc:
        _log.warning(
            "apps.file_mirror_failed",
            app_id=app_id, path=path, error=str(exc)[:160],
        )


# ── Rehydrate (app_files → disk) ─────────────────────────────────────────────

def rehydrate_workspace(row: Any, files: list[tuple[str, str]]) -> None:
    """Recreate the workspace dir from store rows (sync, executor).

    Paths came from our own walk, but rows are re-validated anyway: anything
    absolute, dotted, or traversing is skipped. Ends with a baseline git repo
    when none exists (mirroring ``lifecycle._git_init``) so checkpoints work
    immediately on a rehydrated workspace.
    """
    workspace = app_workspace(row)
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, content in files:
        segments = [s for s in (rel or "").replace("\\", "/").split("/") if s]
        if (
            not segments
            or (rel or "").startswith("/")
            or any(s == ".." or s.startswith(".") for s in segments)
        ):
            continue
        target = workspace.joinpath(*segments)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            _log.warning(
                "apps.rehydrate_write_failed", path=rel, error=str(exc)[:120],
            )
    if not (workspace / ".git").is_dir():
        for args in (
            ("init",),
            ("add", "-A"),
            (*_GIT_IDENT, "commit", "-m", "rehydrate from store"),
        ):
            if _git(workspace, *args) is None:
                return


def _workspace_ready(row: Any, workspace: Path) -> bool:
    """A dir with its manifest's entry file present needs no rehydrate."""
    if not workspace.is_dir():
        return False
    manifest = (
        read_workspace_manifest(workspace)
        or parse_db_manifest(_field(row, "manifest"))
    )
    entry = str(manifest.get("entry") or "index.html")
    return (workspace / entry).is_file()


async def ensure_workspace(db: Any, row: Any) -> Path:
    """THE lazy-rehydrate choke point: every read path resolves the workspace
    through here. A present workspace passes straight through; a missing (or
    entry-less) one with ``app_files`` rows is rebuilt from the store first."""
    workspace = app_workspace(row)
    if _workspace_ready(row, workspace):
        return workspace
    rows = (await db.execute(
        text(
            "SELECT path, content FROM app_files "
            "WHERE app_id = :app_id ORDER BY path"
        ),
        {"app_id": str(_field(row, "id"))},
    )).fetchall()
    if not rows:
        return workspace
    _log.info(
        "apps.workspace_rehydrating",
        workspace=str(workspace), files=len(rows),
    )
    await asyncio.get_event_loop().run_in_executor(
        None, rehydrate_workspace, row, [(r.path, r.content) for r in rows],
    )
    return workspace


# ── Git checkpoints (best-effort subprocess — None/[] on any failure) ────────

def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(workspace), capture_output=True,
            text=True, timeout=_GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _head_short_sha(workspace: Path) -> str | None:
    proc = _git(workspace, "rev-parse", "--short", "HEAD")
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def is_checkpoint_sha(sha: str) -> bool:
    """Abbreviated-to-full hex object name — the only ref form restore takes."""
    return bool(CHECKPOINT_SHA_RE.fullmatch((sha or "").strip().lower()))


def git_checkpoint(workspace: Path, label: str) -> str | None:
    """``add -A`` + commit when dirty; the short HEAD sha either way (it IS
    the checkpoint for the current state). None when git is unusable."""
    if not workspace.is_dir():
        return None
    if not (workspace / ".git").is_dir():
        init = _git(workspace, "init")
        if init is None or init.returncode != 0:
            return None
    if _git(workspace, "add", "-A") is None:
        return None
    status = _git(workspace, "status", "--porcelain")
    if status is None:
        return None
    if not status.stdout.strip():
        return _head_short_sha(workspace)
    commit = _git(workspace, *_GIT_IDENT, "commit", "-m", label or "checkpoint")
    if commit is None or commit.returncode != 0:
        return None
    return _head_short_sha(workspace)


def list_checkpoints(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first ``{sha, message, at, files_changed}`` from ``git log``.

    ``%x01`` sentinels the header line so a pathological filename can never
    be mistaken for a commit; the ``--name-only`` lines between headers are
    the per-commit changed-file count.
    """
    proc = _git(
        workspace, "log", f"-{int(limit)}",
        "--pretty=format:%x01%h%x09%cI%x09%s", "--name-only",
    )
    if proc is None or proc.returncode != 0:
        return []
    out: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            sha, at, message = [*line[1:].split("\t", 2), "", ""][:3]
            out.append({
                "sha": sha, "message": message, "at": at, "files_changed": 0,
            })
        elif line.strip() and out:
            out[-1]["files_changed"] += 1
    return out


def restore_checkpoint(workspace: Path, sha: str) -> str | None:
    """ADDITIVE restore: ``git checkout <sha> -- .`` then a "restore: <sha>"
    commit — history only ever grows (files created after *sha* survive; a
    restore is itself undoable by restoring forward). None on a bad/unknown
    sha or any git failure."""
    ref = (sha or "").strip().lower()
    if not is_checkpoint_sha(ref) or not (workspace / ".git").is_dir():
        return None
    checkout = _git(workspace, "checkout", ref, "--", ".")
    if checkout is None or checkout.returncode != 0:
        return None
    if _git(workspace, "add", "-A") is None:
        return None
    status = _git(workspace, "status", "--porcelain")
    if status is None:
        return None
    if not status.stdout.strip():
        return _head_short_sha(workspace)      # restoring the current state
    commit = _git(workspace, *_GIT_IDENT, "commit", "-m", f"restore: {ref}")
    if commit is None or commit.returncode != 0:
        return None
    return _head_short_sha(workspace)


# ── Endpoints (all edit-gated) ───────────────────────────────────────────────

@router.post("/{slug}/sync")
async def sync_app(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> dict[str, Any]:
    """Mirror the draft into ``app_files`` + drop a git checkpoint."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        files = await sync_workspace_to_store(db, row)
    checkpoint = await asyncio.get_event_loop().run_in_executor(
        None, git_checkpoint, app_workspace(row), "checkpoint",
    )
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="storage",
        detail={"op": "sync", "files": files, "checkpoint": checkpoint},
    )
    return {"files": files, "checkpoint": checkpoint}


@router.get("/{slug}/checkpoints")
async def list_app_checkpoints(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> dict[str, list[dict[str, Any]]]:
    """The draft's checkpoint history (empty when git is unusable)."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        workspace = await ensure_workspace(db, row)
    checkpoints = await asyncio.get_event_loop().run_in_executor(
        None, list_checkpoints, workspace,
    )
    return {"checkpoints": checkpoints}


@router.post("/{slug}/restore")
async def restore_app_checkpoint(
    slug: str,
    body: RestoreRequest,
    user: UserContext = Depends(require_app_user),
) -> dict[str, Any]:
    """Additively restore a checkpoint, then re-mirror the restored state."""
    sha = body.sha.strip().lower()
    if not is_checkpoint_sha(sha):
        raise HTTPException(
            status_code=422, detail="sha must be 7-40 hex characters",
        )
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        workspace = await ensure_workspace(db, row)
        new_sha = await asyncio.get_event_loop().run_in_executor(
            None, restore_checkpoint, workspace, sha,
        )
        if new_sha is None:
            raise HTTPException(
                status_code=409, detail="Checkpoint restore failed",
            )
        await sync_workspace_to_store(db, row)
    await record_app_audit(
        app_id=str(row.id), user_email=_uid(user), kind="storage",
        detail={"op": "restore", "from": sha, "checkpoint": new_sha},
    )
    return {"checkpoint": new_sha}
