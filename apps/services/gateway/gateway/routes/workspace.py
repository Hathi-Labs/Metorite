"""Agent workspace file-browser API (ST-AV-01).

Endpoints
---------
GET  /agent/workspace/{session_id}
    Returns a JSON tree of files in the session's workspace directory.
    Response: { "session_id": str, "root": str, "files": [FileEntry] }

GET  /agent/workspace/{session_id}/file?path=<rel_path>
    Returns the raw file bytes (streamed).  50 MB cap.

GET  /agent/artifacts?agent=<name>&category=<inputs|outputs|agent-data>
    Global artifact browser — lists all files from all agent workspaces
    across the three visible directories.  Supports filtering by agent
    name and category (folder).  Response: { "artifacts": [ArtifactEntry] }
    Use Content-Disposition: inline for browser display.

DELETE /agent/workspace/{session_id}/file?path=<rel_path>
    Delete a file from the workspace.

POST /agent/workspace/{session_id}/upload
    Upload one or more files (multipart/form-data).  Files land in .tmp/
    under the workspace root.  Returns the list of created FileEntry objects.

PATCH /agent/workspace/{session_id}
    Set or update the workspace_path for a session (called by write_artifact tool).

POST /agent/workspace/{session_id}/events
    Push an artifact_created / artifact_updated event from a tool call.
    Stored in a per-session in-memory queue consumed by the SSE endpoint.

GET /agent/workspace/{session_id}/events
    SSE stream: emits artifact_created / artifact_updated events pushed via POST.
    Consumed by the Next.js proxy to forward them into the existing chat SSE stream.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from collections import defaultdict
from pathlib import Path
from typing import Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

_log = get_logger("gateway.workspace")

router = APIRouter(prefix="/agent", tags=["workspace"])

_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard cap

# In-memory queues: session_id → list of asyncio.Queue
# Each SSE subscriber gets its own queue so multiple browser tabs work.
_artifact_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FileEntry(BaseModel):
    path: str           # relative to workspace root
    name: str
    size: int           # bytes
    modified_at: str    # ISO-8601
    mime_type: str
    is_dir: bool = False


class WorkspaceTree(BaseModel):
    session_id: str
    root: str           # absolute workspace root path (for display only)
    files: list[FileEntry]


class WorkspacePatchRequest(BaseModel):
    workspace_path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blob_key_for_workspace(workspace: Path) -> tuple[str, str]:
    """The blob-store ``(agent_name, instance)`` key for a workspace root.

    A shared workspace lives at ``{agents_clone_dir}/repos/<agent_name>`` (the
    basename IS the agent name); a tenant workspace at
    ``{agents_clone_dir}/state/<agent_name>/<slug>`` carries its instance in
    the ``.cc-instance`` marker. One mapping, shared with the agent-side
    write-through (``acb_skills.agent_paths.workspace_blob_key``), so a file
    edited in the file manager lands in the SAME partition the run that
    created it used.
    """
    try:
        from acb_skills.agent_paths import workspace_blob_key

        return workspace_blob_key(workspace)
    except Exception:
        return workspace.name, ""


async def _mirror_gateway_write(
    workspace: Path,
    rel_path: str,
    data: bytes,
    *,
    action: str,
    session_id: str | None,
) -> None:
    """Write-through a gateway file write into the authoritative blob store.

    Mirrors app-side writes (PUT save, upload) to Postgres so a file edited in
    the file manager is as durable as one the agent wrote. No-op for paths
    outside agent-data/inputs/outputs or when the store is unavailable.
    """
    try:
        from acb_memory import is_stored_path, put_file
    except ImportError:
        return
    rel = rel_path.replace("\\", "/")
    if not is_stored_path(rel):
        return
    import mimetypes as _mt

    mime = _mt.guess_type(rel)[0] or "application/octet-stream"
    agent, instance = _blob_key_for_workspace(workspace)
    try:
        await put_file(
            agent, rel, data,
            mime_type=mime, action=action, session_id=session_id, actor="user",
            instance=instance,
        )
    except Exception as exc:
        _log.warning("workspace.blob_mirror_failed", path=rel, error=str(exc)[:200])


async def _faultin_from_store(workspace: Path, rel_path: str) -> bool:
    """Restore a file missing from the disk cache from the authoritative store.

    Returns True and writes the file to disk if the store had it, else False.
    Only applies to the three backed folders; a no-op otherwise.
    """
    rel = rel_path.replace("\\", "/").lstrip("/")
    try:
        from acb_memory import get_file, is_stored_path
    except ImportError:
        return False
    if not is_stored_path(rel):
        return False
    agent, instance = _blob_key_for_workspace(workspace)
    data = await get_file(agent, rel, instance=instance)
    if data is None:
        return False
    dest = _safe_resolve(workspace, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    _log.info("workspace.faulted_in", agent=agent, path=rel)
    return True


async def _mirror_gateway_delete(
    workspace: Path, rel_path: str, *, session_id: str | None
) -> None:
    """Write-through a gateway file delete into the blob store."""
    try:
        from acb_memory import delete_file, is_stored_path
    except ImportError:
        return
    rel = rel_path.replace("\\", "/")
    if not is_stored_path(rel):
        return
    agent, instance = _blob_key_for_workspace(workspace)
    try:
        await delete_file(
            agent, rel,
            session_id=session_id, actor="user", instance=instance,
        )
    except Exception as exc:
        _log.warning("workspace.blob_delete_mirror_failed", path=rel, error=str(exc)[:200])


def _get_workspace_path(
    session_id: str, user_email: str | None = None,
) -> Path | None:
    """Look up workspace_path from Postgres for this session.

    Fallback chain:
    1. Explicit ``workspace_path`` column (set by write_artifact or PATCH
       endpoint) — for an instanced agent this already points at the tenant
       state dir the run used, so no user is needed to resolve it.
    2. Agent workspace derived from the session's ``agent_name``, resolved
       for the viewing member (``user_email``) so a personal agent's session
       browser opens the viewer's own partition even before its first run.
    """
    try:
        from acb_graph import get_session as _db_session
        from sqlalchemy import text

        with _db_session() as s:
            row = s.execute(
                text("SELECT workspace_path, agent_name FROM chat_session WHERE id = :id"),
                {"id": session_id},
            ).fetchone()

        if row is None:
            return None

        # ── 1. Explicit workspace_path ──────────────────────────────────────
        if row.workspace_path:
            return Path(row.workspace_path)

        # ── 2. Derive from agent clone directory ────────────────────────────
        agent_name: str = row.agent_name or ""
        if not agent_name or agent_name in ("orchestrator", "default"):
            return None

        return _resolve_agent_workspace(agent_name, user_email)

    except Exception as exc:
        _log.warning("workspace.db_lookup_failed", session_id=session_id, error=str(exc))
    return None


def _agent_instance_for(agent_name: str, user_email: str | None) -> str:
    """The tenant partition a viewer resolves to for *agent_name*.

    Reads the agent's ``sharing`` declaration from its clone's ``config.json``
    (the same file the executor resolves at run time) and keys it by the
    viewing member — so the file manager, the artifact viewer and the email
    attachment flow open the SAME directory the member's runs write to.

    ``''`` for every shared agent, for anonymous/internal callers, and on any
    read/parse failure — i.e. exactly today's behaviour unless the agent
    explicitly declared otherwise. Never raises.
    """
    if not user_email or "@" not in user_email:
        return ""
    try:
        import json

        from acb_skills.manifest import AgentManifest

        code_dir = _agent_clone_dir(agent_name)
        if code_dir is None:
            return ""
        cfg_path = code_dir / "config.json"
        if not cfg_path.is_file():
            return ""
        cfg = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
        return AgentManifest.from_config(cfg, name=agent_name).instance_key(
            user_email,
        )
    except Exception:
        return ""


def _agent_clone_dir(agent_name: str) -> Path | None:
    """The agent's clone-cache checkout, or ``None`` if it has never run.

    Tries the bare agent name first, then the ``agent-`` prefixed variant,
    since older clones may use either convention.  Each name is looked up
    under BOTH the configured ``agents_clone_dir`` and the legacy
    ``/tmp/acb_agents`` default — older clones (created before the clone root
    moved under ``$HOME``) still live in ``/tmp`` until the agent next runs,
    and we must still surface their files.
    """
    from acb_common import get_settings

    settings = get_settings()
    configured = getattr(
        settings, "agents_clone_dir", str(Path.home() / ".acb" / "agents")
    )
    # Search the configured clone root first, then the legacy /tmp default so
    # clones stranded there before the relocation are still found.
    clone_roots: list[Path] = [Path(configured) / "repos"]
    legacy = Path("/tmp/acb_agents") / "repos"
    if legacy not in clone_roots:
        clone_roots.append(legacy)

    names = [agent_name]
    if agent_name.startswith("agent-"):
        names.append(agent_name[len("agent-"):])
    else:
        names.append(f"agent-{agent_name}")

    for clone_root in clone_roots:
        for name in names:
            candidate = clone_root / name
            if candidate.is_dir():
                return candidate
    return None


def _agent_workspace_dir(
    agent_name: str, user_email: str | None = None,
) -> Path | None:
    """Return the on-disk workspace directory for an agent.

    This MUST mirror ``loader.load_agent`` + the executor's directory
    resolution: a shared agent runs from ``{agents_clone_dir}/repos/{agent}``
    (``clone_as=agent_name``) — whether sourced from a GitHub repo or a local
    ``local_path``.  The registry's ``local_path`` is only a *load-time source
    pointer*: the loader copies it into the clone-cache and runs from there,
    so the agent and all its artefacts (``outputs/``, ``inputs/``,
    ``agent-data/``) live in the cache, NOT at ``local_path``.  Using
    ``local_path`` as the workspace points the file browsers at the
    (artefact-free) monorepo source — which is exactly why generated files
    were invisible in the UI.

    ``user_email`` makes the resolution tenant-aware: a ``personal``/``team``
    agent resolves to that member's state directory
    (:func:`acb_skills.agent_paths.agent_state_dir`) — the directory their
    runs actually write to — created on first resolution so every caller's
    ``is_dir()`` contract holds.  Callers WITHOUT a user (startup sweeps,
    dep-status checks) keep the clone dir, byte-identically, as do all
    shared agents.  Returns ``None`` when no clone exists yet (the agent has
    never run, so it has no artefacts).
    """
    code_dir = _agent_clone_dir(agent_name)
    if code_dir is None:
        return None
    instance = _agent_instance_for(agent_name, user_email)
    if instance:
        from acb_skills.agent_paths import ensure_state_dir

        return ensure_state_dir(code_dir.name, instance)
    return code_dir


def _canonical_workspace_dir(agent_name: str) -> Path:
    """The path the loader WOULD use for *agent_name* — whether or not it
    exists on disk yet.

    Equals ``{agents_clone_dir}/repos/{agent_name}`` (the loader always clones
    with ``clone_as=agent_name``).  Used so a registered-but-never-run agent
    still appears in the artifacts viewer with empty folders, instead of
    vanishing entirely just because it has no clone yet.
    """
    from acb_common import get_settings

    settings = get_settings()
    configured = getattr(
        settings, "agents_clone_dir", str(Path.home() / ".acb" / "agents")
    )
    return Path(configured) / "repos" / agent_name


def _resolve_agent_workspace(
    agent_name: str, user_email: str | None = None,
) -> Path | None:
    """Return the workspace directory for a named agent.

    Resolves to the directory the loader actually runs the agent from — the
    clone cache, or the viewing member's tenant state dir for an instanced
    agent (see :func:`_agent_workspace_dir` for both rules, and for why the
    registry ``local_path`` is never used here).
    """
    try:
        return _agent_workspace_dir(agent_name, user_email)
    except Exception as exc:
        _log.warning(
            "workspace.agent_resolve_failed",
            agent=agent_name,
            error=str(exc),
        )
    return None


def _safe_resolve(root: Path, rel: str) -> Path:
    """Resolve a relative path under root, raising 400 on traversal attempts."""
    # Normalise separators and strip leading slashes/dots
    clean = rel.replace("\\", "/").lstrip("/.")
    resolved = (root / clean).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    return resolved


# The three "special" workspace directories.  Agents are encouraged to write
# deliverables to outputs/ (uploads land in inputs/, reference data in
# agent-data/), and these are always created up-front so they show in the UI.
# NOTE: the Files Viewer is NOT limited to these — GitHub Copilot SDK agents
# create/edit files directly in the working-directory root (reports, scripts,
# code), so the viewer surfaces the whole working tree (minus the excludes
# below).  These three are kept only for up-front creation and categorisation.
_VISIBLE_WORKSPACE_DIRS = frozenset({"inputs", "outputs", "agent-data"})

# Directories excluded from traversal: VCS, dependency, build, and cache noise.
# Pruned wherever they appear so a whole-repo clone (e.g. the dev agent that
# clones the entire monorepo) doesn't flood the UI with node_modules/.git/etc.
_EXCLUDED_DIRS = frozenset({
    "__pycache__", "node_modules", ".git", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".venv", "venv", ".next", ".turbo", ".cache", "dist",
    "build", ".idea", ".vscode", "coverage", ".nyc_output", "test-results",
    "playwright-report", ".pnpm-store", ".gradle", ".terraform",
    "site-packages", ".egg-info",
})

# File suffixes never surfaced (compiled junk + key/cert material).
_EXCLUDED_FILE_SUFFIXES = (".pid", ".pem", ".key", ".crt", ".pyc", ".pyo")

# Substrings that mark a file as secret/credential material to hide from the UI.
_SECRET_FILE_MARKERS = ("token_cache", "credential", "secret", "id_rsa", "id_ed25519")

# Hard cap on files returned per workspace walk — bounds huge monorepo clones.
_MAX_TREE_FILES = 4000


def _is_hidden_or_secret_file(name: str) -> bool:
    """True if *name* is a dotfile, secret/credential, or compiled-junk file.

    Dotfiles (``.env``, ``.zoho_token_cache.json``, ``.git-credentials`` …)
    are hidden wholesale — this is the primary guard against leaking secrets
    that live in an agent's working-directory root into the file browser.
    """
    if name.startswith("."):
        return True
    if name.endswith(_EXCLUDED_FILE_SUFFIXES):
        return True
    low = name.lower()
    return any(m in low for m in _SECRET_FILE_MARKERS)


def _ensure_workspace_dirs(root: Path) -> None:
    """Create inputs/, outputs/, and agent-data/ if they don't exist yet."""
    for d in _VISIBLE_WORKSPACE_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)


def _walk_tree(root: Path) -> list[FileEntry]:
    """Walk the agent's whole working tree and return a flat list of files.

    Unlike the old behaviour (which only traversed ``inputs/``, ``outputs/``,
    and ``agent-data/``), this surfaces every file the agent created or edited
    — GitHub Copilot SDK agents write reports, scripts, and code directly in
    the working-directory root, so restricting to the three special dirs hid
    all of their output.  ``_EXCLUDED_DIRS`` (VCS/deps/build/cache) and
    :func:`_is_hidden_or_secret_file` (dotfiles, keys, credential caches) keep
    the listing useful and prevent secret leakage.  Capped at
    ``_MAX_TREE_FILES``.
    """
    entries: list[FileEntry] = []
    try:
        _ensure_workspace_dirs(root)

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune VCS / dependency / build / cache directories everywhere.
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and d not in _EXCLUDED_DIRS
            )
            dp = Path(dirpath)
            rel_dir = dp.relative_to(root)

            for fname in sorted(filenames):
                if _is_hidden_or_secret_file(fname):
                    continue
                fpath = dp / fname
                try:
                    stat = fpath.stat()
                    rel_path = str((rel_dir / fname)).replace("\\", "/")
                    mime, _ = mimetypes.guess_type(fname)
                    entries.append(FileEntry(
                        path=rel_path,
                        name=fname,
                        size=stat.st_size,
                        modified_at=__import__("datetime").datetime.fromtimestamp(
                            stat.st_mtime, tz=__import__("datetime").timezone.utc
                        ).isoformat(),
                        mime_type=mime or "application/octet-stream",
                        is_dir=False,
                    ))
                    if len(entries) >= _MAX_TREE_FILES:
                        _log.warning(
                            "workspace.walk_capped",
                            root=str(root), cap=_MAX_TREE_FILES,
                        )
                        return entries
                except OSError:
                    continue
    except Exception as exc:
        _log.warning("workspace.walk_failed", root=str(root), error=str(exc))
    return entries


def _is_visible_workspace_path(rel_path: str) -> bool:
    """Check whether *rel_path* is within one of the visible workspace dirs."""
    clean = rel_path.replace("\\", "/").lstrip("/")
    return any(
        clean == d or clean.startswith(d + "/")
        for d in _VISIBLE_WORKSPACE_DIRS
    )


def _is_blocked_path(rel_path: str) -> bool:
    """True if *rel_path* points into excluded/secret territory.

    The file browser now lists the whole working tree, so the raw-file
    endpoints must independently refuse anything the walkers hide — otherwise
    a crafted ``?path=.env`` would still stream a secret that never appeared
    in any listing.  Blocks excluded/dot directories anywhere in the path and
    secret/dot/junk basenames.
    """
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return True
    for seg in parts[:-1]:
        if seg.startswith(".") or seg in _EXCLUDED_DIRS:
            return True
    return _is_hidden_or_secret_file(parts[-1])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/workspace/{session_id}", response_model=WorkspaceTree)
async def get_workspace_tree(
    session_id: str,
    _user: UserContext = Depends(get_current_user),
) -> WorkspaceTree:
    """Return the file tree for a session's workspace directory.

    If the session has no explicit workspace_path set, the agent's clone
    directory is used automatically (derived from the session's agent_name).
    """
    import asyncio
    loop = asyncio.get_event_loop()
    workspace = await loop.run_in_executor(None, _get_workspace_path, session_id, _user.email)
    if workspace is None or not workspace.exists():
        return WorkspaceTree(session_id=session_id, root="", files=[])

    files = await loop.run_in_executor(None, _walk_tree, workspace)
    return WorkspaceTree(session_id=session_id, root=str(workspace), files=files)


@router.get("/workspace/{session_id}/file", response_model=None)
async def get_workspace_file(
    session_id: str,
    path: str = Query(..., description="Relative path within the workspace"),
    format_: Literal["pdf"] | None = Query(
        None,
        alias="format",
        description="`pdf` converts a Markdown or HTML file and sends it as a download",
    ),
    _user: UserContext = Depends(get_current_user),
) -> StreamingResponse | Response:
    """Stream a single file from the session workspace.

    ``?format=pdf`` (WS-27bm S8) converts a Markdown or HTML file through the
    one seam, ``gateway.pdf_render``, AFTER the same workspace, blocked-path
    and containment checks as a raw read. Any other file type is a 415.
    """
    import asyncio
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(status_code=404, detail="Workspace not found for session")

    if _is_blocked_path(path):
        raise HTTPException(status_code=404, detail="File not found")
    file_path = _safe_resolve(workspace, path)
    if not file_path.exists() or not file_path.is_file():
        # Fault-in: the store is authoritative, so a file missing from the disk
        # cache may still live in the blob store (e.g. after a volume wipe before
        # the agent has re-run). Restore it on demand, then serve.
        restored = await _faultin_from_store(workspace, path)
        if not restored:
            raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    if format_ == "pdf":
        # The member is the authenticated caller, never request input.
        return await _file_as_pdf(file_path, file_size, member=_user.email or "")
    if file_size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file_size} bytes). Maximum is {_MAX_FILE_BYTES} bytes.",
        )

    mime, _ = mimetypes.guess_type(file_path.name)
    media_type = mime or "application/octet-stream"

    def _iter_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.name}"',
            "Content-Length": str(file_size),
        },
    )


async def _file_as_pdf(file_path: Path, file_size: int, *, member: str) -> Response:
    """A workspace document as a PDF download (WS-27bm S8, spec §14).

    The type check comes before the size check and before any read, so a
    refused type costs nothing. The read runs in a thread, and the layout
    runs in a child process (``pdf_render.render_pdf``).
    """
    from gateway.pdf_render import (
        MAX_SOURCE_BYTES,
        SOURCE_KINDS,
        PdfRenderError,
        attachment_disposition,
        pdf_filename,
        render_pdf,
    )

    kind = SOURCE_KINDS.get(file_path.suffix.lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="Only a Markdown or HTML file can be downloaded as a PDF.",
        )
    if file_size > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large for a PDF ({file_size} bytes). Maximum is {MAX_SOURCE_BYTES} bytes.",
        )
    source = await asyncio.to_thread(
        file_path.read_text, encoding="utf-8", errors="replace"
    )
    try:
        # Out of process, with a timeout: a MuPDF crash or hang is a refusal
        # here, never the gateway's death (fix round 1).
        pdf = await render_pdf(kind, source, member=member)
    except PdfRenderError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": attachment_disposition(pdf_filename(file_path.name))},
    )


@router.patch("/workspace/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_workspace_path(
    session_id: str,
    body: WorkspacePatchRequest,
    _user: UserContext = Depends(get_current_user),
) -> None:
    """Record the workspace_path for a session (called by write_artifact tool)."""
    try:
        from acb_graph import get_session as _db_session
        from sqlalchemy import text

        def _write():
            with _db_session() as s:
                s.execute(
                    text(
                        "UPDATE chat_session SET workspace_path = :path "
                        "WHERE id = :id"
                    ),
                    {"path": body.workspace_path, "id": session_id},
                )
                s.commit()

        await __import__("asyncio").get_event_loop().run_in_executor(None, _write)
    except Exception as exc:
        _log.warning("workspace.patch_failed", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to update workspace path") from exc


# ---------------------------------------------------------------------------
# Artifact event push (called by write_artifact tool) + SSE stream
# ---------------------------------------------------------------------------

class ArtifactEvent(BaseModel):
    name: str  # "artifact_created" | "artifact_updated"
    path: str
    sha256: str | None = None
    size: int | None = None


@router.post("/workspace/{session_id}/events", status_code=status.HTTP_204_NO_CONTENT)
async def push_artifact_event(
    session_id: str,
    event: ArtifactEvent,
    _user: UserContext = Depends(get_current_user),
) -> None:
    """Receive an artifact event from the write_artifact tool and fan it out
    to any subscribed SSE consumers for this session."""
    payload = event.model_dump()
    for q in list(_artifact_subscribers.get(session_id, [])):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
    _log.info(
        "workspace.artifact_event",
        session_id=session_id,
        name=event.name,
        path=event.path,
    )


@router.get("/workspace/{session_id}/events")
async def stream_artifact_events(
    session_id: str,
    _user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """SSE stream — yields artifact_created / artifact_updated events pushed
    by the write_artifact tool for this session.

    The Next.js api/agent/chat route (or a dedicated proxy) can subscribe
    here and forward custom events into the existing chat SSE stream.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _artifact_subscribers[session_id].append(q)

    async def _generate():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps({'type': 'custom', 'name': payload['name'], 'data': payload})}\n\n"
                except asyncio.TimeoutError:
                    # heartbeat keep-alive
                    yield ": keep-alive\n\n"
        finally:
            try:
                _artifact_subscribers[session_id].remove(q)
            except ValueError:
                pass
            if not _artifact_subscribers[session_id]:
                del _artifact_subscribers[session_id]

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# POST /workspace/{session_id}/upload  — user file upload
# ---------------------------------------------------------------------------

from fastapi import UploadFile

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file
_ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".json", ".yaml", ".yml", ".xml", ".html", ".css", ".js", ".ts",
    ".py", ".sh", ".ps1", ".toml", ".ini", ".cfg",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp3", ".wav", ".mp4", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".log", ".sql", ".db", ".sqlite",
}


@router.post("/workspace/{session_id}/upload")
async def upload_files(
    session_id: str,
    files: list[UploadFile],
    _user: UserContext = Depends(get_current_user),
) -> list[FileEntry]:
    """Upload one or more files into the session workspace .tmp/ directory.

    Files are stored under ``{workspace_root}/.tmp/`` and are automatically
    tracked by Git.  The agent receives a system message with the list of
    uploaded files and their paths so it can reference them.
    """
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email
    )
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail="No workspace found for this session. "
                   "Start a chat with an agent first.",
        )

    # Upload to inputs/ (visible workspace directory) — not .tmp/
    upload_dir = workspace / "inputs"
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot create inputs directory: {exc}",
        ) from exc

    uploaded: list[FileEntry] = []
    for f in files:
        # Validate filename
        safe_name = Path(f.filename or "untitled").name
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. "
                       f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
            )

        # Read into memory (capped at _MAX_UPLOAD_BYTES)
        content = await f.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{safe_name}' too large "
                       f"({len(content)} bytes). Max is {_MAX_UPLOAD_BYTES}.",
            )

        # Avoid overwrites: append (1), (2), etc.
        dest = upload_dir / safe_name
        counter = 1
        stem, ext2 = Path(safe_name).stem, Path(safe_name).suffix
        while dest.exists():
            dest = upload_dir / f"{stem} ({counter}){ext2}"
            counter += 1

        # Write
        dest.write_bytes(content)

        # Build response entry
        stat = dest.stat()
        mime, _ = mimetypes.guess_type(safe_name)
        rel_path = str(dest.relative_to(workspace)).replace("\\", "/")

        # Write-through: a user upload (inputs/) is durable state too.
        await _mirror_gateway_write(
            workspace, rel_path, content, action="create", session_id=session_id,
        )

        uploaded.append(FileEntry(
            path=rel_path,
            name=dest.name,
            size=stat.st_size,
            modified_at=__import__("datetime").datetime.fromtimestamp(
                stat.st_mtime, tz=__import__("datetime").timezone.utc
            ).isoformat(),
            mime_type=mime or "application/octet-stream",
            is_dir=False,
        ))

        _log.info(
            "workspace.file_uploaded",
            session_id=session_id,
            path=rel_path,
            size=stat.st_size,
        )

    return uploaded


@router.post("/artifacts/upload")
async def upload_artifact(
    files: list[UploadFile],
    agent: str = Query(...),
    category: str = Query("agent-data"),  # agent-data | inputs | outputs
    _user: UserContext = Depends(get_current_user),
) -> list[FileEntry]:
    """Upload file(s) directly into an AGENT's workspace folder (by agent name,
    not chat session). Used by the email rule editor to add draft attachments —
    files land in the uploader's workspace for the agent (their tenant state
    dir for an instanced agent, else ``repos/{agent}``) and can then be picked
    via ``GET /agent/artifacts?agent=…&category=…`` — which resolves the SAME
    directory for the same member."""
    cat = category if category in ("agent-data", "inputs", "outputs") else "agent-data"
    workspace = _agent_workspace_dir(agent, _user.email) or _canonical_workspace_dir(
        agent,
    )
    upload_dir = workspace / cat
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Cannot create {cat} directory: {exc}",
        ) from exc

    uploaded: list[FileEntry] = []
    for f in files:
        safe_name = Path(f.filename or "untitled").name
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {ext}. "
                       f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
            )
        content = await f.read()
        if len(content) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{safe_name}' too large "
                       f"({len(content)} bytes). Max is {_MAX_UPLOAD_BYTES}.",
            )
        dest = upload_dir / safe_name
        counter = 1
        stem, ext2 = Path(safe_name).stem, Path(safe_name).suffix
        while dest.exists():
            dest = upload_dir / f"{stem} ({counter}){ext2}"
            counter += 1
        dest.write_bytes(content)
        stat = dest.stat()
        mime, _ = mimetypes.guess_type(safe_name)
        rel_path = str(dest.relative_to(workspace)).replace("\\", "/")
        # Write-through to the authoritative blob store.
        await _mirror_gateway_write(
            workspace, rel_path, content, action="create", session_id=None,
        )
        uploaded.append(FileEntry(
            path=rel_path, name=dest.name, size=stat.st_size,
            modified_at=__import__("datetime").datetime.fromtimestamp(
                stat.st_mtime, tz=__import__("datetime").timezone.utc
            ).isoformat(),
            mime_type=mime or "application/octet-stream", is_dir=False,
        ))
        _log.info("workspace.artifact_uploaded", agent=agent,
                  path=rel_path, size=stat.st_size)
    return uploaded


# ---------------------------------------------------------------------------
# DELETE /workspace/{session_id}/file  — remove a file
# ---------------------------------------------------------------------------

class DeleteResponse(BaseModel):
    deleted: bool
    path: str


@router.delete("/workspace/{session_id}/file", response_model=DeleteResponse)
async def delete_workspace_file(
    session_id: str,
    path: str = Query(..., description="Relative path within the workspace"),
    _user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    """Delete a file from the session workspace."""
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail="Workspace not found for session"
        )

    file_path = _safe_resolve(workspace, path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if file_path.is_dir():
        raise HTTPException(
            status_code=400, detail="Cannot delete directories"
        )

    # Only allow deletion of files within the visible workspace dirs.
    rel = str(file_path.relative_to(workspace)).replace("\\", "/")
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Deletion is restricted to inputs/, outputs/, and agent-data/.",
        )

    file_path.unlink()
    # Write-through the delete to the authoritative store (records a delete
    # version in history).
    rel_del = str(file_path.relative_to(workspace)).replace("\\", "/")
    await _mirror_gateway_delete(workspace, rel_del, session_id=session_id)
    _log.info(
        "workspace.file_deleted",
        session_id=session_id,
        path=path,
    )
    return DeleteResponse(deleted=True, path=path)


# ---------------------------------------------------------------------------
# POST /workspace/{session_id}/promote — move an inputs/ file to agent-data/
# ---------------------------------------------------------------------------
# A user upload lands in inputs/. Promoting it to agent-data/ makes it durable,
# behaviour-shaping knowledge (agent-data is an extension of the system prompt).


class PromoteRequest(BaseModel):
    path: str
    """inputs/ path to promote, e.g. "inputs/spec.pdf"."""
    dest: str | None = None
    """Optional agent-data/ destination; defaults to agent-data/<basename>."""


@router.post("/workspace/{session_id}/promote")
async def promote_input_to_agent_data(
    session_id: str, body: PromoteRequest,
    _user: UserContext = Depends(get_current_user),
) -> FileEntry:
    """Move an inputs/ file into agent-data/ (permanent, prompt-shaping storage)."""
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(status_code=404, detail="workspace not found")

    src_rel = body.path.replace("\\", "/").lstrip("/")
    if not src_rel.startswith("inputs/"):
        raise HTTPException(status_code=400, detail="only inputs/ files can be promoted")
    src = _safe_resolve(workspace, src_rel)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="source file not found")

    dest_rel = (body.dest or f"agent-data/{src.name}").replace("\\", "/").lstrip("/")
    if not dest_rel.startswith("agent-data/"):
        raise HTTPException(status_code=400, detail="destination must be under agent-data/")
    dest = _safe_resolve(workspace, dest_rel)
    dest.parent.mkdir(parents=True, exist_ok=True)

    data = src.read_bytes()
    dest.write_bytes(data)
    src.unlink()

    mime, _ = mimetypes.guess_type(dest.name)
    mime = mime or "application/octet-stream"
    # Store: record the new agent-data version (promote) + the inputs delete.
    await _mirror_gateway_write(
        workspace, dest_rel, data, action="promote", session_id=session_id,
    )
    await _mirror_gateway_delete(workspace, src_rel, session_id=session_id)

    stat = dest.stat()
    _log.info("workspace.promoted", session_id=session_id, src=src_rel, dest=dest_rel)
    return FileEntry(
        path=dest_rel,
        name=dest.name,
        size=stat.st_size,
        modified_at=__import__("datetime").datetime.fromtimestamp(
            stat.st_mtime, tz=__import__("datetime").timezone.utc
        ).isoformat(),
        mime_type=mime,
        is_dir=False,
    )


# ---------------------------------------------------------------------------
# GET /workspace/{session_id}/history — version history of tracked files
# ---------------------------------------------------------------------------


@router.get("/workspace/{session_id}/history")
async def get_workspace_history(
    session_id: str, path: str | None = None, limit: int = 200,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Version history for this agent's files (all, or one *path*), newest first.

    Every unique version an agent created/modified over time is a row, so a user
    can track and directly access the full history of any file.
    """
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email
    )
    if workspace is None:
        return {"history": []}
    try:
        from acb_memory import file_history
    except ImportError:
        return {"history": []}
    agent, instance = _blob_key_for_workspace(workspace)
    rows = await file_history(agent, path, limit, instance=instance)
    return {"history": rows}


# ---------------------------------------------------------------------------
# PUT /workspace/{session_id}/file  — edit/write a file in place
# ---------------------------------------------------------------------------

class WriteFileRequest(BaseModel):
    content: str
    """New file content.  Written as UTF-8 text for text/code files;
    base64-decodable payloads are written as binary bytes."""
    encoding: str = "utf-8"
    """'utf-8' (default — text content), 'base64' (binary content)."""


@router.put("/workspace/{session_id}/file")
async def write_workspace_file(
    session_id: str,
    body: WriteFileRequest,
    path: str = Query(..., description="Relative path within the workspace"),
    _user: UserContext = Depends(get_current_user),
) -> FileEntry:
    """Overwrite or create a file in the workspace.  Directories are
    created automatically.  Safe — resolves against workspace root only.

    Accepts text (encoding='utf-8') and binary (encoding='base64') content.
    Returns the updated FileEntry with fresh stat metadata.
    """
    import base64

    loop = asyncio.get_event_loop()
    workspace = await loop.run_in_executor(None, _get_workspace_path, session_id, _user.email)
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail="Workspace not found for session"
        )

    file_path = _safe_resolve(workspace, path)
    # Only allow writes within the visible workspace dirs (inputs/, outputs/,
    # agent-data/).  The agent itself can write anywhere, but the frontend
    # user is restricted to the three visible folders.
    if not file_path.is_relative_to(workspace):
        raise HTTPException(
            status_code=400, detail="Path escapes workspace root"
        )
    rel = str(file_path.relative_to(workspace)).replace("\\", "/")
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Writes are restricted to inputs/, outputs/, and agent-data/.",
        )

    # Create parent directories if needed
    _existed = file_path.exists()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write content
    if body.encoding == "base64":
        data = base64.b64decode(body.content)
        file_path.write_bytes(data)
    else:
        data = body.content.encode("utf-8")
        file_path.write_text(body.content, encoding="utf-8")

    # Build response
    stat = file_path.stat()
    mime, _ = mimetypes.guess_type(file_path.name)
    rel_path = str(file_path.relative_to(workspace)).replace("\\", "/")

    # Write-through to the authoritative blob store.
    await _mirror_gateway_write(
        workspace, rel_path, data,
        action="modify" if _existed else "create", session_id=session_id,
    )

    _log.info(
        "workspace.file_written",
        session_id=session_id,
        path=rel_path,
        size=stat.st_size,
    )

    return FileEntry(
        path=rel_path,
        name=file_path.name,
        size=stat.st_size,
        modified_at=__import__("datetime").datetime.fromtimestamp(
            stat.st_mtime, tz=__import__("datetime").timezone.utc
        ).isoformat(),
        mime_type=mime or "application/octet-stream",
        is_dir=False,
    )


# ---------------------------------------------------------------------------
# Global artifact browser — lists files from ALL agent workspaces
# ---------------------------------------------------------------------------

class ArtifactEntry(BaseModel):
    agent_name: str
    path: str           # relative to workspace root
    name: str
    size: int
    modified_at: str
    mime_type: str
    category: str       # "inputs" | "outputs" | "agent-data"
    is_dir: bool = False


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactEntry]


def _discover_agent_workspaces(
    user_email: str | None = None,
) -> dict[str, Path]:
    """Return a dict of {agent_name: workspace_path} for all *live* agents.

    Collects the names of every live agent from the registries, then resolves
    each via :func:`_agent_workspace_dir` for the viewing member — the same
    path the loader runs the agent from and writes artefacts to (the member's
    own tenant dir for an instanced agent).  The
    registry ``local_path`` is deliberately ignored as a workspace (it is only
    a load-time source pointer; see :func:`_agent_workspace_dir`).

    Name sources:
    1. Static agent registry (``_AGENT_REGISTRY``) — all entries are live.
    2. Dynamic agent registry (Postgres-backed) — ``status == 'live'`` only.
    3. Agents.json file (legacy fallback) — all listed names.
    """
    names: set[str] = set()

    # ── 1. Static registry (in-code _AGENT_REGISTRY) ──────────────────────
    try:
        from gateway.routes.agent import _AGENT_REGISTRY
        for entry in _AGENT_REGISTRY:
            name = entry.get("name")
            if name:
                names.add(name)
    except Exception:
        pass

    # ── 2. Dynamic registry (Postgres-backed) — live agents only ──────────
    try:
        from gateway.routes.agent import \
            _load_dynamic_agents
        for entry in _load_dynamic_agents():
            name = entry.get("name")
            if name and entry.get("status", "live") == "live":
                names.add(name)
    except Exception:
        pass

    # ── 3. Agents.json file (legacy fallback) ─────────────────────────────
    try:
        import json as _json
        agents_file = Path(__file__).resolve()
        for _ in range(8):
            agents_file = agents_file.parent
            if (agents_file / "pyproject.toml").exists():
                agents_file = agents_file / "agents.json"
                break
        if agents_file.exists() and agents_file.name == "agents.json":
            entries = _json.loads(agents_file.read_text(encoding="utf-8"))
            for entry in entries:
                name = entry.get("name")
                if name:
                    names.add(name)
    except Exception:
        pass

    # ── Resolve each name to its workspace.  Prefer an existing clone; fall
    # back to the canonical path so a registered-but-never-run agent (no clone
    # on disk yet) STILL appears in the artifacts viewer, instead of silently
    # vanishing.  _walk_agent_artifacts surfaces the three empty folders for
    # such agents; real files appear once the agent runs and gets cloned.
    workspaces: dict[str, Path] = {}
    for name in names:
        try:
            ws = _agent_workspace_dir(name, user_email) or _canonical_workspace_dir(
                name,
            )
        except Exception:
            continue
        workspaces[name] = ws

    return workspaces


def _category_for(rel_path: str) -> str:
    """Label an entry by its top-level segment: one of the three special dirs,
    or ``"workspace"`` for everything in the working-directory root/tree."""
    first = rel_path.replace("\\", "/").split("/", 1)[0]
    return first if first in _VISIBLE_WORKSPACE_DIRS else "workspace"


def _walk_agent_artifacts(
    agent_name: str,
    workspace: Path,
    category_filter: str | None = None,
) -> list[ArtifactEntry]:
    """Walk an agent's whole working tree and return ArtifactEntry objects
    (files + directories).

    Surfaces every file the agent created or edited — not just ``inputs/``,
    ``outputs/``, ``agent-data/`` — because GitHub Copilot SDK agents write
    reports, scripts, and code directly into the working-directory root.
    ``_EXCLUDED_DIRS`` and :func:`_is_hidden_or_secret_file` strip VCS/build
    noise and secrets; the listing is capped at ``_MAX_TREE_FILES``.  When
    *category_filter* names a special dir, only that subtree is walked.
    """
    import datetime as _dt

    entries: list[ArtifactEntry] = []
    emitted_dirs: set[str] = set()

    # Always surface the three special folders FIRST so every agent renders in
    # the viewer — even one with no clone on disk yet (never run / wiped).
    # Real mtime when the folder exists, else epoch (synthetic placeholder).
    synth_cats = (
        [category_filter]
        if category_filter and category_filter in _VISIBLE_WORKSPACE_DIRS
        else sorted(_VISIBLE_WORKSPACE_DIRS)
    )
    for cat in synth_cats:
        cat_dir = workspace / cat
        try:
            mtime = cat_dir.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append(ArtifactEntry(
            agent_name=agent_name,
            path=cat,
            name=cat,
            size=0,
            modified_at=_dt.datetime.fromtimestamp(
                mtime, tz=_dt.timezone.utc,
            ).isoformat(),
            mime_type="inode/directory",
            category=cat,
            is_dir=True,
        ))
        emitted_dirs.add(cat)

    if category_filter and category_filter in _VISIBLE_WORKSPACE_DIRS:
        start = workspace / category_filter
        if not start.is_dir():
            return entries
    else:
        start = workspace

    if not start.exists():
        return entries  # no clone yet — the synthetic folders above are all we have

    file_count = 0
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in _EXCLUDED_DIRS
        )
        dp = Path(dirpath)
        try:
            rel_dir = dp.relative_to(workspace)
        except ValueError:
            continue

        # Directory entries (the root itself is skipped — only its children).
        for dname in dirnames:
            dpath = dp / dname
            try:
                dstat = dpath.stat()
            except OSError:
                continue
            rel_dpath = str((rel_dir / dname)).replace("\\", "/")
            if rel_dpath in emitted_dirs:
                continue  # already emitted as a synthetic special folder
            emitted_dirs.add(rel_dpath)
            entries.append(ArtifactEntry(
                agent_name=agent_name,
                path=rel_dpath,
                name=dname,
                size=0,
                modified_at=_dt.datetime.fromtimestamp(
                    dstat.st_mtime, tz=_dt.timezone.utc,
                ).isoformat(),
                mime_type="inode/directory",
                category=_category_for(rel_dpath),
                is_dir=True,
            ))

        # File entries.
        for fname in sorted(filenames):
            if _is_hidden_or_secret_file(fname):
                continue
            fpath = dp / fname
            try:
                stat = fpath.stat()
            except OSError:
                continue
            rel_path = str((rel_dir / fname)).replace("\\", "/")
            mime, _ = mimetypes.guess_type(fname)
            entries.append(ArtifactEntry(
                agent_name=agent_name,
                path=rel_path,
                name=fname,
                size=stat.st_size,
                modified_at=_dt.datetime.fromtimestamp(
                    stat.st_mtime, tz=_dt.timezone.utc,
                ).isoformat(),
                mime_type=mime or "application/octet-stream",
                category=_category_for(rel_path),
                is_dir=False,
            ))
            file_count += 1
            if file_count >= _MAX_TREE_FILES:
                _log.warning(
                    "workspace.artifacts_walk_capped",
                    agent=agent_name, cap=_MAX_TREE_FILES,
                )
                return entries

    return entries


@router.get("/artifacts", response_model=ArtifactListResponse)
async def get_artifacts(
    agent: str | None = Query(None, description="Filter by agent name"),
    category: str | None = Query(
        None, description="Filter: 'inputs', 'outputs', or 'agent-data'"
    ),
    _user: UserContext = Depends(get_current_user),
) -> ArtifactListResponse:
    """Global artifact browser — lists files from ALL agent workspaces.

    Returns every file across ``inputs/``, ``outputs/``, and ``agent-data/``
    for every known agent.  Supports optional filtering by agent name and
    category.
    """
    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()

    # Validate category filter early
    if category and category not in _VISIBLE_WORKSPACE_DIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: "
                   f"{', '.join(sorted(_VISIBLE_WORKSPACE_DIRS))}.",
        )

    workspaces = await loop.run_in_executor(
        None, _discover_agent_workspaces, _user.email
    )

    # Filter by agent if requested
    if agent:
        ws = workspaces.get(agent)
        if ws is None:
            return ArtifactListResponse(artifacts=[])
        workspaces = {agent: ws}

    all_artifacts: list[ArtifactEntry] = []
    for ag_name, ws_path in sorted(workspaces.items()):
        batch = await loop.run_in_executor(
            None,
            _walk_agent_artifacts,
            ag_name,
            ws_path,
            category,
        )
        all_artifacts.extend(batch)

    return ArtifactListResponse(artifacts=all_artifacts)


@router.get("/artifacts/file")
async def get_artifact_file(
    agent: str = Query(..., description="Agent name"),
    path: str = Query(..., description="Relative path within the workspace"),
    _user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a single file from any agent's workspace (global artifact view)."""
    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()
    workspaces = await loop.run_in_executor(
        None, _discover_agent_workspaces, _user.email
    )
    workspace = workspaces.get(agent)
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail=f"Agent workspace not found: {agent}"
        )

    if _is_blocked_path(path):
        raise HTTPException(status_code=404, detail="File not found")
    file_path = _safe_resolve(workspace, path)
    if not file_path.exists() or not file_path.is_file():
        # Fault-in: the store is authoritative, so a file missing from the disk
        # cache may still live in the blob store (e.g. after a volume wipe before
        # the agent has re-run). Restore it on demand, then serve.
        restored = await _faultin_from_store(workspace, path)
        if not restored:
            raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    if file_size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file_size} bytes). "
                   f"Maximum is {_MAX_FILE_BYTES} bytes.",
        )

    mime, _ = mimetypes.guess_type(file_path.name)
    media_type = mime or "application/octet-stream"

    def _iter_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.name}"',
            "Content-Length": str(file_size),
        },
    )


@router.put("/artifacts/file")
async def write_artifact_file(
    agent: str = Query(..., description="Agent name"),
    path: str = Query(..., description="Relative path within the workspace"),
    body: WriteFileRequest = ...,
    _user: UserContext = Depends(get_current_user),
) -> ArtifactEntry:
    """Overwrite a file in any agent's workspace (global artifact view).

    Uses the same agent-workspace discovery as GET /artifacts/file.
    Accepts text (encoding='utf-8') and binary (encoding='base64') content.
    Returns the updated ArtifactEntry with fresh stat metadata.
    """
    import asyncio as _asyncio
    import base64
    loop = _asyncio.get_event_loop()

    workspaces = await loop.run_in_executor(
        None, _discover_agent_workspaces, _user.email
    )
    workspace = workspaces.get(agent)
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail=f"Agent workspace not found: {agent}"
        )

    file_path = _safe_resolve(workspace, path)
    # Restrict writes to visible workspace dirs
    rel = str(file_path.relative_to(workspace)).replace("\\", "/")
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Writes are restricted to inputs/, outputs/, and agent-data/.",
        )

    # Create parent directories if needed
    _existed = file_path.exists()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write content
    if body.encoding == "base64":
        data = base64.b64decode(body.content)
        file_path.write_bytes(data)
    else:
        data = body.content.encode("utf-8")
        file_path.write_text(body.content, encoding="utf-8")

    # Build response
    stat = file_path.stat()
    mime, _ = mimetypes.guess_type(file_path.name)
    rel_path = str(file_path.relative_to(workspace)).replace("\\", "/")

    # Write-through to the authoritative blob store (agent = the explicit target).
    await _mirror_gateway_write(
        workspace, rel_path, data,
        action="modify" if _existed else "create", session_id=None,
    )

    # Determine category from path
    cat = "agent-data"
    for c in _VISIBLE_WORKSPACE_DIRS:
        if rel_path.startswith(c + "/") or rel_path == c:
            cat = c
            break

    _log.info(
        "workspace.artifact_file_written",
        agent=agent,
        path=rel_path,
        size=stat.st_size,
    )

    return ArtifactEntry(
        agent_name=agent,
        path=rel_path,
        name=file_path.name,
        size=stat.st_size,
        modified_at=__import__("datetime").datetime.fromtimestamp(
            stat.st_mtime, tz=__import__("datetime").timezone.utc
        ).isoformat(),
        mime_type=mime or "application/octet-stream",
        category=cat,
        is_dir=False,
    )
