"""Custom Apps · files — workspace passthrough for the Workshop (edit-gated).

Flat listing + text read/write over an app's workspace. ``clean_app_rel``
refuses traversal, dotfiles and ``.git``, and every open goes through
``acb_skills.safe_open``, which refuses a link at any depth (§7.5 rule B,
WS-43h review P0: a sandbox container writes the app dir). The
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
from acb_skills import safe_open
from fastapi import Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from gateway.routes.apps._common import (
    MAX_SOURCE_FILE_BYTES,
    MAX_WORKSPACE_FILES,
    WORKSPACE_SKIP_DIRS,
    _log,
    _tenant_session,
    clean_app_rel,
    get_app_or_404,
    iter_app_files,
    read_workspace_manifest,
    require_app_author,
    require_app_user,
    router,
    t2_vendor_dir,
    workspace_links,
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
    """Flat file list — skips dot-anything, VCS/build dirs, directories and links."""
    entries: list[AppFileEntry] = []
    if not workspace.is_dir() or workspace.is_symlink():
        return entries

    def keep_dir(_parent: tuple[str, ...], d: str) -> bool:
        return not d.startswith(".") and d not in WORKSPACE_SKIP_DIRS

    for parts in iter_app_files(workspace, keep_dir):
        if parts[-1].startswith("."):
            continue
        rel = "/".join(parts)
        try:
            stat = safe_open.stat_file(workspace, rel)
        except (OSError, ValueError):
            continue
        if stat is None:
            continue
        entries.append(AppFileEntry(
            path=rel,
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(
                stat.st_mtime, tz=UTC,
            ).isoformat(),
        ))
        if len(entries) >= MAX_WORKSPACE_FILES:
            return entries
    return entries


def _unsafe(exc: Exception) -> HTTPException:
    """The answer of a path that the safe opener refused: a link, or no plain file."""
    _log.warning("apps.file_path_refused", error=str(exc)[:160])
    return HTTPException(status_code=400, detail="Not a plain file in the app")


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
    rel = clean_app_rel(path)
    try:
        stat = safe_open.stat_file(workspace, rel)
    except (OSError, ValueError) as exc:
        raise _unsafe(exc) from exc
    if stat is None:
        raise HTTPException(status_code=404, detail="File not found")
    size = stat.st_size
    if size > MAX_SOURCE_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size} bytes). "
                   f"Maximum is {MAX_SOURCE_FILE_BYTES}.",
        )
    try:
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: safe_open.read_bytes(workspace, rel, limit=MAX_SOURCE_FILE_BYTES),
        )
    except (OSError, ValueError) as exc:
        raise _unsafe(exc) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")
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
    rel = clean_app_rel(body.path)

    def _write() -> os.stat_result:
        safe_open.write_bytes(workspace, rel, data)
        written = safe_open.stat_file(workspace, rel)
        if written is None:
            raise FileNotFoundError(rel)
        return written

    try:
        stat = await asyncio.get_event_loop().run_in_executor(None, _write)
    except (OSError, ValueError) as exc:
        raise _unsafe(exc) from exc
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
    rel = clean_app_rel(path)
    if not safe_open.is_file(workspace, rel):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        await asyncio.get_event_loop().run_in_executor(
            None, safe_open.unlink, workspace, rel,
        )
    except (OSError, ValueError) as exc:
        raise _unsafe(exc) from exc
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


def host_build_refusal(workspace: Path) -> str | None:
    """Why the HOST may not build this app, or ``None`` (WS-43h review P0).

    ``node build_t2.mjs`` follows links, so a link in the app dir could put a
    host file in the bundle, or write ``dist/`` into another app's dir. A dir
    that a sandbox container mounts now, or mounted, is refused whole: a
    container could plant a link after this check. Its build runs in the
    sandbox. Any other dir is refused while it holds a link.
    """
    try:
        from orchestrator.sandbox_broker import is_sandbox_dir
    except ImportError:
        is_sandbox_dir = None
    if is_sandbox_dir is not None and is_sandbox_dir(workspace):
        return (
            "This app builds in its sandbox. Ask the App Workshop builder to "
            "build it."
        )
    links = workspace_links(workspace)
    if links:
        shown = ", ".join(links[:5])
        return f"The app holds a link ({shown}). Remove it, then build again."
    return None


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
    refusal = await asyncio.get_event_loop().run_in_executor(
        None, host_build_refusal, workspace,
    )
    if refusal:
        return {"built": False, "error": refusal}

    ok, output = await _run_build_t2(workspace, script)
    _log.info("apps.build_triggered", slug=slug, ok=ok)
    return {"built": ok, **({"output": output} if ok else {"error": output})}
