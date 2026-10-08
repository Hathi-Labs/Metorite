"""Custom Apps · files — workspace passthrough for the Workshop (edit-gated).

Flat listing + text read/write over an app's workspace, containment-guarded by
``resolve_app_file`` (traversal, dotfiles, ``.git``, symlink escapes). The
Workshop's code view and the preview pane's draft fetch ride these; published
bundles are served from ``app_versions`` instead (see ``publish.py``). Reads
resolve the workspace through ``durability.ensure_workspace`` (lazy rehydrate
from ``app_files``) and writes mirror the file back into that store.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acb_auth import UserContext
from acb_common import get_settings
from acb_common.child_env import child_env
from fastapi import Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from gateway.routes.apps._common import (
    MAX_SOURCE_FILE_BYTES,
    MAX_WORKSPACE_FILES,
    WORKSPACE_SKIP_DIRS,
    _log,
    _tenant_session,
    get_app_or_404,
    read_workspace_manifest,
    require_app_author,
    require_app_user,
    resolve_app_file,
    router,
    t2_vendor_dir,
)
from gateway.routes.apps.durability import ensure_workspace, mirror_app_file
from pydantic import BaseModel

# Hard cap on a build_t2.mjs run — a local esbuild bundle against an
# already-vendored dep cache is a few seconds; generous headroom for a
# loaded box without leaving a runaway process to hang the request forever.
BUILD_TIMEOUT_SECONDS = 60.0


class AppFileEntry(BaseModel):
    path: str
    size: int
    modified_at: str


class AppFileWrite(BaseModel):
    path: str
    content: str


def _walk_files(workspace: Path) -> list[AppFileEntry]:
    """Flat file list — skips dot-anything, VCS/build dirs, and directories."""
    entries: list[AppFileEntry] = []
    if not workspace.is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in WORKSPACE_SKIP_DIRS
        )
        rel_dir = Path(dirpath).relative_to(workspace)
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            fpath = Path(dirpath) / fname
            try:
                stat = fpath.stat()
            except OSError:
                continue
            entries.append(AppFileEntry(
                path=str(rel_dir / fname).replace("\\", "/"),
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC,
                ).isoformat(),
            ))
            if len(entries) >= MAX_WORKSPACE_FILES:
                return entries
    return entries


async def _edit_workspace(slug: str, user: UserContext) -> tuple[Any, Path]:
    """Edit-gated row + workspace path — rehydrated from ``app_files`` first
    when the on-disk draft is missing (the durability choke point)."""
    async with _tenant_session() as db:
        row, _grants = await get_app_or_404(db, slug, user, edit=True)
        workspace = await ensure_workspace(db, row)
    return row, workspace


@router.get("/{slug}/files", response_model=list[AppFileEntry])
async def list_app_files(
    slug: str,
    user: UserContext = Depends(require_app_user),
) -> list[AppFileEntry]:
    _row, workspace = await _edit_workspace(slug, user)
    return await asyncio.get_event_loop().run_in_executor(
        None, _walk_files, workspace,
    )


@router.get("/{slug}/files/content")
async def read_app_file(
    slug: str,
    path: str = Query(..., description="Relative path within the workspace"),
    user: UserContext = Depends(require_app_user),
) -> PlainTextResponse:
    _row, workspace = await _edit_workspace(slug, user)
    file_path = resolve_app_file(workspace, path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    size = file_path.stat().st_size
    if size > MAX_SOURCE_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size} bytes). "
                   f"Maximum is {MAX_SOURCE_FILE_BYTES}.",
        )
    data = await asyncio.get_event_loop().run_in_executor(
        None, file_path.read_bytes,
    )
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415, detail="Not a text file",
        ) from exc
    return PlainTextResponse(content)


@router.put("/{slug}/files/content", response_model=AppFileEntry)
async def write_app_file(
    slug: str,
    body: AppFileWrite,
    user: UserContext = Depends(require_app_author),
) -> AppFileEntry:
    data = body.content.encode("utf-8")
    if len(data) > MAX_SOURCE_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Content too large ({len(data)} bytes). "
                   f"Maximum is {MAX_SOURCE_FILE_BYTES}.",
        )
    row, workspace = await _edit_workspace(slug, user)
    file_path = resolve_app_file(workspace, body.path)

    def _write() -> os.stat_result:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        return file_path.stat()

    stat = await asyncio.get_event_loop().run_in_executor(None, _write)
    rel = str(file_path.relative_to(workspace.resolve())).replace("\\", "/")
    # Durability write-through: mirror just this file into app_files
    # (best-effort — a full reconcile rides POST /{slug}/sync and publish).
    await mirror_app_file(str(row.id), rel, body.content)
    _log.info("apps.file_written", slug=slug, path=rel, size=stat.st_size)
    return AppFileEntry(
        path=rel,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(
            stat.st_mtime, tz=UTC,
        ).isoformat(),
    )


@router.delete("/{slug}/files/content")
async def delete_app_file(
    slug: str,
    path: str = Query(..., description="Relative path within the workspace"),
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Delete a file from an app's workspace (the Advanced/IDE view).

    Disk-only — mirrors ``write_app_file``'s stance that the ``app_files``
    store's full reconciliation (dropping the now-gone row, a checkpoint)
    rides the caller's follow-up ``POST /{slug}/sync``, not this endpoint.
    """
    _row, workspace = await _edit_workspace(slug, user)
    file_path = resolve_app_file(workspace, path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    await asyncio.get_event_loop().run_in_executor(None, file_path.unlink)
    rel = str(file_path.relative_to(workspace.resolve())).replace("\\", "/")
    _log.info("apps.file_deleted", slug=slug, path=rel)
    return {"path": rel, "deleted": True}


def _is_buildable_manifest(manifest: dict[str, Any] | None) -> bool:
    """True for a T2 (build-based) app — resolved from ``entry``, the same
    signal every other backend read path uses (never the ``tier`` display
    field — see ``default_manifest``'s docstring)."""
    entry = str((manifest or {}).get("entry") or "index.html")
    return entry.startswith("dist/")


def _agent_app_builder_dir() -> Path | None:
    """The on-disk clone of the ``agent-app-builder`` agent, which owns
    ``build/build_t2.mjs`` — the SAME script the app-builder's own Copilot
    session invokes mid-chat (agent-app-builder/agents.py's
    ``__T2_BUILD_SCRIPT__`` substitution). Reuses the loader's own
    clone-cache resolution so this never drifts from where the agent
    actually finds it."""
    from gateway.routes.workspace import _agent_workspace_dir
    return _agent_workspace_dir("agent-app-builder")


async def _run_build_t2(workspace: Path, script: Path) -> tuple[bool, str]:
    """Run ``node build_t2.mjs`` against *workspace*. Returns (ok, output) —
    ``output`` is stdout on success, stderr on failure. Never raises: a
    timeout or a missing ``node`` binary is reported the same as any other
    build failure."""
    # build_t2.mjs reads these two names (lines 75-77) and nothing else of the
    # gateway env (WS-49 BH-1).
    env = child_env(extra={
        "CUSTOM_APPS_T2_VENDOR_DIR": str(t2_vendor_dir()),
        "AGENTS_CLONE_DIR": get_settings().agents_clone_dir or None,
    })
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(script),
            cwd=str(workspace), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=BUILD_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # timeout, missing node, etc.
        return False, str(exc)
    if proc.returncode != 0:
        return False, err.decode(errors="replace")[-2000:]
    return True, out.decode(errors="replace")[-2000:]


@router.post("/{slug}/build")
async def build_app(
    slug: str,
    user: UserContext = Depends(require_app_author),
) -> dict[str, Any]:
    """Rebuild a T2 (React) app's ``dist/bundle.html`` from ``src/*`` — the
    Advanced/IDE view's save-triggers-rebuild step for apps whose entry is a
    build artifact, not a hand-edited file. A no-op for T1 apps (nothing to
    build); the caller still gets a clean ``{"built": false}`` rather than an
    error, since "not buildable" isn't a failure.

    Build failures return 200 with ``{"built": false, "error": ...}`` —
    an expected, actionable outcome for the editor to show inline, not a
    server error.
    """
    _row, workspace = await _edit_workspace(slug, user)
    manifest = read_workspace_manifest(workspace)
    if not _is_buildable_manifest(manifest):
        return {"built": False, "reason": "not a build-based (T2) app"}

    app_builder_dir = await asyncio.get_event_loop().run_in_executor(
        None, _agent_app_builder_dir,
    )
    if app_builder_dir is None:
        return {
            "built": False,
            "error": "T2 build tooling unavailable (agent-app-builder not cloned)",
        }
    script = app_builder_dir / "build" / "build_t2.mjs"
    if not script.is_file():
        return {"built": False, "error": f"build script missing: {script}"}

    ok, output = await _run_build_t2(workspace, script)
    _log.info("apps.build_triggered", slug=slug, ok=ok)
    return {"built": ok, **({"output": output} if ok else {"error": output})}
