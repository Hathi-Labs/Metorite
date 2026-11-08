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
import re
import subprocess
from pathlib import Path
from typing import Any

from acb_auth import UserContext
from acb_common.child_env import child_env
from acb_skills import safe_open
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
    iter_app_files,
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
#: Each checkpoint git runs with no file monitor, no hook, no attributes file
#: of the host and no signing program (§7.5 rule A, WS-43h). A sandbox
#: container writes the app workspace, and an empty read-only mount covers
#: its ``.git``. These flags hold even if a ``.git`` there was ever changed.
_GIT_SAFE = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
    "-c", "core.attributesFile=/dev/null", "-c", "commit.gpgSign=false",
)
#: The only keys that the ``.git/config`` of an app may hold. ``git init``
#: writes these. Any other key, such as ``filter.<x>.clean``,
#: ``diff.<x>.command``, ``include.path`` or ``gpg.program``, can start a
#: program on the host, so host git refuses the repo (WS-43h, review P2).
_TRUSTED_CONFIG_KEYS = frozenset({
    "core.repositoryformatversion", "core.filemode", "core.bare",
    "core.logallrefupdates", "core.symlinks", "core.ignorecase",
    "core.precomposeunicode", "extensions.objectformat", "extensions.refstorage",
})

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
    return [workspace / rel for rel, _text in _eligible_text_files(workspace, manifest)]


def _eligible_text_files(
    workspace: Path, manifest: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """``[(rel, text)]`` of :func:`list_workspace_text_files`, read safely.

    Every listing and every read goes through ``acb_skills.safe_open``
    (§7.5 rule B, WS-43h review P0). A sandbox container writes the app dir,
    so a link there must never reach a host file or another app's dir. A
    link is never listed, and a path with a link at any depth is skipped.
    """
    out: list[tuple[str, str]] = []
    if not workspace.is_dir() or workspace.is_symlink():
        return out
    entry_segments = tuple(
        s for s in manifest_entry_rel(
            manifest if manifest is not None else read_workspace_manifest(workspace),
        ).split("/") if s
    )
    entry_dir_segments = entry_segments[:-1]

    def keep_dir(parent: tuple[str, ...], d: str) -> bool:
        return not d.startswith(".") and (
            d not in WORKSPACE_SKIP_DIRS
            or (
                len(parent) < len(entry_dir_segments)
                and parent == entry_dir_segments[: len(parent)]
                and d == entry_dir_segments[len(parent)]
            )
        )

    for parts in iter_app_files(workspace, keep_dir):
        if parts[-1].startswith("."):
            continue
        under_skip_dir = any(seg in WORKSPACE_SKIP_DIRS for seg in parts[:-1])
        if under_skip_dir and parts != entry_segments:
            continue
        rel = "/".join(parts)
        try:
            raw = safe_open.read_bytes(workspace, rel, limit=MAX_SOURCE_FILE_BYTES)
            if raw is None:
                continue
            text_ = raw.decode("utf-8")
        except (OSError, ValueError):  # a link, too large, or not UTF-8
            continue
        out.append((rel, text_))
        if len(out) >= MAX_WORKSPACE_FILES:
            return out
    return out


def _read_workspace_files(workspace: Path) -> dict[str, tuple[str, str]]:
    """rel path → (content, sha256) for every eligible file (sync, executor)."""
    return {
        rel: (content, _sha256(content))
        for rel, content in _eligible_text_files(workspace)
    }


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
        try:
            # The safe opener refuses a link at any depth (WS-43h review P0).
            safe_open.write_bytes(workspace, "/".join(segments), content.encode("utf-8"))
        except (OSError, ValueError) as exc:
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
    return safe_open.is_file(workspace, entry)


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

def _git_repo_trusted(workspace: Path) -> bool:
    """True when the app's ``.git`` is a real dir whose config host git may read.

    §7.5 rule A (WS-43h, review P2). A container that wrote ``.git/config``
    could name a filter, a diff driver or a signing program, and ``git add``
    would run it on the host. The broker covers ``.git`` before a container
    starts, and this check holds even if a ``.git`` was ever written: each
    key of the config must be one that ``git init`` writes.
    """
    git_dir = workspace / ".git"
    config = git_dir / "config"
    if git_dir.is_symlink() or git_dir.is_junction() or not git_dir.is_dir():
        return False
    if config.is_symlink() or not config.is_file():
        return False
    try:
        listed = subprocess.run(
            ["git", "config", "--file", str(config), "--no-includes", "--list"],
            cwd=str(workspace), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S, check=False,
            env=child_env(extra={
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": str(workspace.parent),
            }),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if listed.returncode != 0:
        return False
    keys = {line.split("=", 1)[0].strip().lower() for line in listed.stdout.splitlines()}
    foreign = sorted(k for k in keys if k and k not in _TRUSTED_CONFIG_KEYS)
    if foreign:
        _log.warning("apps.git_repo_refused", workspace=str(workspace), keys=foreign[:10])
        return False
    return True


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Host git in an app dir, or ``None`` (§7.5 rule A, WS-43h).

    Every command but ``init`` first asks :func:`_git_repo_trusted`. No
    system config, no system attributes, and no search above the app dir.
    """
    if args[:1] != ("init",) and not _git_repo_trusted(workspace):
        return None
    try:
        return subprocess.run(
            ["git", *_GIT_SAFE, *args], cwd=str(workspace), capture_output=True,
            text=True, timeout=_GIT_TIMEOUT_S, check=False,
            env=child_env(extra={
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CEILING_DIRECTORIES": str(workspace.parent),
            }),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def init_app_repo(workspace: Path) -> bool:
    """``git init`` in an app dir that has no ``.git``. True when a real one exists after.

    WS-43h (review P2). The sandbox broker calls it before an ``app_builder``
    container starts, so the read-only cover of ``.git`` always has a
    ``.git`` to cover. ``git init`` reads no file of the work tree.
    """
    git_dir = workspace / ".git"
    if not git_dir.exists() and not git_dir.is_symlink():
        done = _git(workspace, "init", "-q")
        if done is None or done.returncode != 0:
            return False
    return git_dir.is_dir() and not git_dir.is_symlink()


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
