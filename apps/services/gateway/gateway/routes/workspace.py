"""Agent workspace file-browser API (ST-AV-01).

Endpoints
---------
GET  /agent/workspace/{session_id}
    Returns a JSON tree of files in the session's workspace directory.
    Response: { "session_id": str, "root": str, "files": [FileEntry] }

GET  /agent/workspace/{session_id}/file?path=<rel_path>
    Returns the raw file bytes (streamed).  50 MB cap.

GET  /agent/artifacts?agent=<name>&category=<inputs|outputs|agent-data>
    Artifact browser — lists the files of the CALLER'S OWN agent
    workspaces (a ``personal`` agent's state dir). A shared agent's clone is
    one folder for every tenant, so it is never served (H-201 part 2).
    Supports filtering by agent name and category (folder).
    Response: { "artifacts": [ArtifactEntry] }
    Use Content-Disposition: inline for browser display.

DELETE /agent/workspace/{session_id}/file?path=<rel_path>
    Delete a file from the workspace.

POST /agent/workspace/{session_id}/upload
    Upload one or more files (multipart/form-data).  Files land in the
    session's attachment folder: ``inputs/<thread slug>/`` in a shared agent's
    tenant dir, else ``inputs/`` (H-229).  Returns the created FileEntry objects.

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
import stat as _stat_mod
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

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
    organization_id: str | None = None,
) -> None:
    """Write-through a gateway file write into the authoritative blob store.

    Mirrors app-side writes (PUT save, upload) to Postgres so a file edited in
    the file manager is as durable as one the agent wrote. No-op for paths
    outside agent-data/inputs/outputs or when the store is unavailable.

    *organization_id* is the caller's tenant, from the authenticated identity
    (H-201 part 2). ``None`` falls back to the tenant the request bound.
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
            instance=instance, organization_id=organization_id,
        )
    except Exception as exc:
        _log.warning("workspace.blob_mirror_failed", path=rel, error=str(exc)[:200])


async def _faultin_from_store(
    workspace: Path, rel_path: str, organization_id: str | None = None,
    *, accept: Any = None,
) -> bool:
    """Restore a file missing from the disk cache from the authoritative store.

    Returns True and writes the file to disk if the store had it, else False.
    Only applies to the three backed folders; a no-op otherwise.
    *organization_id* is the caller's tenant (see :func:`_mirror_gateway_write`).

    *accept* is an async check on the stored bytes, made BEFORE anything is
    written (H-227). A loose file of a shared agent's tenant dir restores
    only for a session that the blob history shows wrote those bytes
    (:func:`_session_owns_loose`). A refused file is never written to disk.

    H-201 part 3 (``projects_ai_chat.md`` §21.15):

    * It never writes into a shared clone. That is a backstop, because no
      session root lies in ``repos/`` any more.
    * A tenant dir (``o:<org>``) restores only for a caller of that tenant.
    * A tenant dir that misses its own key reads this tenant's older row
      (``instance=''``). That row is how a document that the Projects chat
      linked before the tenant dir existed still opens, for its own tenant
      only. The read is in the caller's tenant, so another org's row is
      never seen.
    """
    rel = rel_path.replace("\\", "/").lstrip("/")
    try:
        from acb_memory import get_file, is_stored_path
        from acb_skills.agent_paths import is_tenant_instance, tenant_instance
    except ImportError:
        return False
    if not is_stored_path(rel):
        return False
    if _is_shared_clone(workspace):
        return False
    agent, instance = _blob_key_for_workspace(workspace)
    tenant_dir = is_tenant_instance(instance)
    if tenant_dir and (
        not organization_id or instance != tenant_instance(organization_id)
    ):
        return False
    data = await get_file(agent, rel, instance=instance, organization_id=organization_id)
    if data is None and tenant_dir:
        data = await get_file(agent, rel, instance="", organization_id=organization_id)
    if data is None:
        return False
    if accept is not None and not await accept(data):
        return False
    from acb_skills import safe_open

    # WS-43d (§7.5 rule B): the blob goes back into the dir through the safe
    # opener, so a link that a container planted cannot point the write at a
    # host file.
    try:
        await asyncio.to_thread(
            safe_open.write_bytes, workspace, _open_rel(workspace, rel), data,
        )
    except (safe_open.UnsafePath, OSError) as exc:
        _log.warning("workspace.faultin_refused", agent=agent, path=rel, error=str(exc)[:200])
        return False
    _log.info("workspace.faulted_in", agent=agent, path=rel)
    return True


async def _mirror_gateway_delete(
    workspace: Path, rel_path: str, *, session_id: str | None,
    organization_id: str | None = None,
) -> None:
    """Write-through a gateway file delete into the blob store.

    H-201 part 3: a delete in a tenant dir (``o:<org>``) also deletes this
    tenant's older row at the same path (``instance=''``). Otherwise the next
    rehydrate would bring the file back from that row.
    """
    try:
        from acb_memory import delete_file, is_stored_path
        from acb_skills.agent_paths import is_tenant_instance
    except ImportError:
        return
    rel = rel_path.replace("\\", "/")
    if not is_stored_path(rel):
        return
    agent, instance = _blob_key_for_workspace(workspace)
    keys = [instance, ""] if is_tenant_instance(instance) else [instance]
    for key in keys:
        try:
            await delete_file(
                agent, rel,
                session_id=session_id, actor="user", instance=key,
                organization_id=organization_id,
            )
        except Exception as exc:
            _log.warning("workspace.blob_delete_mirror_failed", path=rel, error=str(exc)[:200])


def _is_under(path: Path, root: Path, *, strictly: bool = False) -> bool:
    """True when *path* is *root* or lies below it. Both are resolved."""
    try:
        if not path.is_relative_to(root):
            return False
    except ValueError:
        return False
    return path != root if strictly else True


def _app_workspace_for(
    resolved: Path, user_email: str | None, organization_id: str | None,
    *, write: bool,
) -> bool:
    """True when *resolved* is inside the workspace of an app of this tenant.

    The ``apps`` read runs in the caller's tenant, so an app of another
    tenant is not found. A write also needs edit rights on that app, the
    same rule the app routes use (``_common.can_edit``).
    """
    from acb_auth import UserContext as _UC
    from acb_auth.roles import UserRole
    from acb_graph import tenant_session
    from gateway.routes.apps._common import can_edit
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        apps = s.execute(text(
            "SELECT id, owner_email, workspace_path FROM apps "
            "WHERE workspace_path IS NOT NULL")).fetchall()
        for app in apps:
            try:
                ws = Path(str(app.workspace_path)).resolve(strict=True)
            except OSError:
                continue
            if not _is_under(resolved, ws):
                continue
            if not write:
                return True
            grants = [(g.subject, g.role) for g in s.execute(
                text("SELECT subject, role FROM app_grants WHERE app_id = :i"),
                {"i": app.id}).fetchall()]
            member = _UC(email=user_email, role=UserRole.EMPLOYEE,
                         organization_id=organization_id)
            if can_edit(app, member, grants):
                return True
    return False


def _allowed_workspace(
    raw: str, *, session_id: str, user_email: str | None,
    organization_id: str | None, write: bool,
) -> Path | None:
    """The resolved workspace root, or ``None`` when *raw* is not allowed.

    H-201 fix round 1 (P0). ``chat_session.workspace_path`` is the root that
    every file route trusts, and ``_safe_resolve`` checks containment only
    against it. So a PATCH of ``/`` made the whole disk readable. The path is
    resolved with every symlink followed, and it must then lie under a root
    that the server derives:

    * **Write (the PATCH).** The browser is the only member writer. It binds
      the Workshop session to the workspace of an app that the member may
      edit, in the member's tenant. Nothing else is allowed.
    * **Read.** A writer (``write_artifact``) stores the run's own
      directory. So a read also takes the caller's own state directory for
      that agent, the caller's tenant dir for that agent, or this session's
      scratch directory. Anything else counts as absent.

    H-201 part 3 (``projects_ai_chat.md`` §21.15): a read no longer takes a
    directory below an agent clone root, ``repos/``. A shared agent runs
    each tenant in its own tenant dir, so a stored clone path of an older
    session counts as absent. Step 2 of :func:`_get_workspace_path` then gives
    the caller's tenant dir.
    """
    import tempfile

    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_dir():
        return None

    from gateway.routes.apps._common import apps_root

    if _is_under(resolved, apps_root().resolve(), strictly=True):
        return resolved if _app_workspace_for(
            resolved, user_email, organization_id, write=write) else None
    if write:
        return None

    from acb_skills.agent_paths import (
        agent_state_dir,
        is_valid_agent_name,
        state_root,
        tenant_instance,
    )

    try:
        state = state_root().resolve()
    except OSError:
        state = None
    if state is not None and _is_under(resolved, state, strictly=True):
        agent = resolved.relative_to(state).parts[0]
        if not is_valid_agent_name(agent):
            return None
        instance = _agent_instance_for(agent, user_email)
        if instance:
            own = agent_state_dir(agent, instance).resolve()
            if _is_under(resolved, own):
                return resolved
        if organization_id:
            tenant = agent_state_dir(agent, tenant_instance(organization_id)).resolve()
            if _is_under(resolved, tenant):
                return resolved
        return None

    scratch = (Path(tempfile.gettempdir()) / "acb_artifacts" / session_id).resolve()
    if _is_under(resolved, scratch):
        return resolved
    return None


def _is_shared_clone(workspace: Path) -> bool:
    """True when *workspace* lies under an agent clone root, ``repos/``.

    That is a shared agent's one clone, which holds its code for every
    tenant. H-201 part 2, P0-A: no member may change it through a session
    route. H-201 part 3 moved each run into a tenant dir, and no session
    root lies in ``repos/`` any more. This check stays as a backstop.
    """
    from acb_common import get_settings

    settings = get_settings()
    clone_root = Path(getattr(
        settings, "agents_clone_dir", str(Path.home() / ".acb" / "agents")))
    try:
        resolved = workspace.resolve()
    except OSError:
        return True
    for repos in (clone_root / "repos", Path("/tmp/acb_agents") / "repos"):
        try:
            if _is_under(resolved, repos.resolve()):
                return True
        except OSError:
            continue
    return False


def _refuse_shared_clone_write(workspace: Path) -> None:
    """403 when a session route would change a shared agent's clone."""
    if _is_shared_clone(workspace):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A shared agent's workspace is read-only.",
        )


def _get_workspace_path(
    session_id: str, user_email: str | None, organization_id: str | None,
) -> Path | None:
    """Look up workspace_path from Postgres for this session.

    H-201 (``projects_ai_chat.md`` §21.13). ``chat_session`` is FORCE RLS in
    production, so the read opens ``acb_graph.tenant_session`` for the
    caller's tenant. The route passes ``user.organization_id``, which the
    server resolves from the authenticated identity, never from the request.
    With no tenant the read raises ``TenantUnbound`` and the app handler
    answers 403. Nothing falls back to an unbound session.

    Fallback chain:
    1. Explicit ``workspace_path`` column (set by write_artifact or PATCH
       endpoint) — for an instanced agent this already points at the tenant
       state dir the run used, so no user is needed to resolve it.
    2. Agent workspace derived from the session's ``agent_name``, resolved
       for the viewer (H-201 part 3, ``projects_ai_chat.md`` §21.15):

       * a ``personal`` agent gives the viewer's own state dir
         (:func:`_member_agent_workspace`);
       * a ``shared`` agent gives the viewer's tenant dir, keyed by
         *organization_id* (:func:`_tenant_agent_workspace`). It is never
         the clone;
       * a ``team`` agent keeps the older rule.
    """
    from acb_graph import tenant_session
    from gateway.db import TenantUnbound

    try:
        from sqlalchemy import text

        with tenant_session(organization_id) as s:
            row = s.execute(
                text("SELECT workspace_path, agent_name FROM chat_session WHERE id = :id"),
                {"id": session_id},
            ).fetchone()

        if row is None:
            return None

        # ── 1. Explicit workspace_path ──────────────────────────────────────
        # H-201 fix round 1 (P0): a stored value is attacker input, because
        # an earlier PATCH or a legacy writer put it there. A value that
        # fails the allowed-root check counts as absent, so the chain goes on
        # to step 2 and a warning is logged.
        if row.workspace_path:
            allowed = _allowed_workspace(
                row.workspace_path, session_id=session_id,
                user_email=user_email, organization_id=organization_id,
                write=False,
            )
            if allowed is not None:
                return allowed
            _log.warning(
                "workspace.stored_path_rejected", session_id=session_id,
                workspace_path=str(row.workspace_path)[:200],
            )

        # ── 2. Derive from agent clone directory ────────────────────────────
        agent_name: str = row.agent_name or ""
        if not agent_name or agent_name in ("orchestrator", "default"):
            return None

        # H-201 part 3: a personal agent gives the viewer's own folder, and a
        # shared agent the viewer's tenant dir. Neither is ever the clone.
        instance = _agent_instance_for(agent_name, user_email)
        if not instance:
            return _tenant_agent_workspace(agent_name, organization_id)
        if user_email and instance == f"u:{user_email}":
            return _member_agent_workspace(agent_name, user_email)

        # A team agent. H-201 fix round 2 (P0): the derived root passes the
        # same read check as a stored one. ``agent_name`` came from the
        # session row, which a member wrote, and ``_agent_clone_dir`` already
        # refuses a bad name.
        derived = _resolve_agent_workspace(agent_name, user_email)
        if derived is None:
            return None
        return _allowed_workspace(
            str(derived), session_id=session_id, user_email=user_email,
            organization_id=organization_id, write=False,
        )

    except TenantUnbound:
        raise
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

    H-201 fix round 2 (P0): *agent_name* is data. It comes from a query, a
    request body or a ``chat_session`` row. A name such as ``../../..``
    joined onto ``repos/`` escaped to ``/``. A name that fails the one rule
    (``agent_paths.is_valid_agent_name``) has no clone dir. A candidate
    must also resolve strictly below its ``repos/`` root.
    """
    from acb_common import get_settings
    from acb_skills.agent_paths import is_valid_agent_name

    if not is_valid_agent_name(agent_name):
        _log.warning("workspace.agent_name_rejected", agent=str(agent_name)[:80])
        return None
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
            if candidate.is_dir() and _is_under(
                candidate.resolve(), clone_root.resolve(), strictly=True,
            ):
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
    from acb_skills.agent_paths import require_agent_name

    # H-201 fix round 2: raises InvalidAgentName for a name that is not one
    # safe path segment.
    return Path(configured) / "repos" / require_agent_name(agent_name)


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


def _member_agent_workspace(
    agent_name: str, user_email: str | None,
) -> Path | None:
    """The caller's OWN workspace for *agent_name*, or ``None``.

    H-201 part 2 (``projects_ai_chat.md`` §21.14). This is the only root the
    four global artifact routes (``/agent/artifacts``, ``.../file`` GET and
    PUT, ``.../upload``) may use.

    * A ``personal`` agent gives the member a private state directory, keyed
      ``u:<email>``. That directory holds only this member's files, so it is
      the root.
    * A ``shared`` agent runs every tenant in ONE clone, ``repos/<agent>``.
      Its ``inputs/``, ``outputs/`` and ``agent-data/`` can hold the run output
      of another org. So a shared agent has no root here, for every member.
    * A ``team`` agent is keyed ``t:<team>`` by the team name alone, so two
      orgs with one team name share one folder. It has no root here either.
    * An agent with no clone has no readable ``config.json``. Nothing can
      prove that it is instanced, so it has no root. The old fallback to the
      canonical ``repos/<agent>`` path was a shared folder.

    The result must also lie strictly below ``state_root()``, so a link or a
    bad name cannot move it into ``repos/`` or out of the tree. Never raises.
    """
    if not user_email or "@" not in user_email:
        return None
    from acb_skills.agent_paths import (
        agent_state_dir,
        ensure_state_dir,
        is_valid_agent_name,
        state_root,
    )

    if not is_valid_agent_name(agent_name):
        return None
    try:
        code_dir = _agent_clone_dir(agent_name)
        if code_dir is None:
            return None
        if _agent_instance_for(agent_name, user_email) != f"u:{user_email}":
            return None
        instance = f"u:{user_email}"
        root = state_root().resolve()
        # Check BEFORE ensure_state_dir: it makes the folder and writes the
        # marker, so a link at state/<agent> must be refused first.
        planned = agent_state_dir(code_dir.name, instance).resolve()
        if not _is_under(planned, root, strictly=True):
            return None
        ws = ensure_state_dir(code_dir.name, instance)
        if not _is_under(ws.resolve(), root, strictly=True):
            return None
    except Exception as exc:
        _log.warning(
            "workspace.member_workspace_failed", agent=agent_name, error=str(exc)[:200],
        )
        return None
    return ws


def _tenant_agent_workspace(
    agent_name: str, organization_id: str | None,
) -> Path | None:
    """The caller's TENANT dir of a shared agent, or ``None``.

    H-201 part 3 (``projects_ai_chat.md`` §21.15). A shared agent's run works
    in ``state/<agent>/<slug of o:<org>>``, one folder per tenant, and never
    in the clone. This is the same folder, keyed by the caller's
    *organization_id*. The route passes ``user.organization_id``, which the
    server resolves from the authenticated identity, never from the request.

    * No tenant gives no folder.
    * An agent with no clone gives no folder, as it has never run here.
    * The folder must lie strictly below ``state_root()`` with every link
      followed, and the check comes BEFORE ``ensure_state_dir``. So a link
      planted at ``state/<agent>`` makes no folder and no marker in a clone.

    Never raises.
    """
    if not organization_id:
        return None
    from acb_skills.agent_paths import (
        agent_state_dir,
        ensure_state_dir,
        is_valid_agent_name,
        state_root,
        tenant_instance,
    )

    if not is_valid_agent_name(agent_name):
        return None
    try:
        code_dir = _agent_clone_dir(agent_name)
        if code_dir is None:
            return None
        key = tenant_instance(organization_id)
        root = state_root().resolve()
        planned = agent_state_dir(code_dir.name, key).resolve()
        if not _is_under(planned, root, strictly=True):
            return None
        ws = ensure_state_dir(code_dir.name, key)
        if not _is_under(ws.resolve(), root, strictly=True):
            return None
    except Exception as exc:
        _log.warning(
            "workspace.tenant_workspace_failed", agent=agent_name, error=str(exc)[:200],
        )
        return None
    return ws


def _safe_resolve(root: Path, rel: str) -> Path:
    """Resolve a relative path under root, raising 400 on traversal attempts."""
    # Normalise separators and strip leading slashes/dots
    clean = rel.replace("\\", "/").lstrip("/.")
    resolved = (root / clean).resolve()
    # H-201 fix round 1: a path check, not a string prefix. "/srv/app2" starts
    # with "/srv/app", and the old check let it through.
    if not _is_under(resolved, root.resolve()):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    return resolved


# ---------------------------------------------------------------------------
# The safe opener (WS-43d, maf_coding_engine.md §7.5 rule B)
# ---------------------------------------------------------------------------
# A sandbox container can write anything in the dir it mounts: a link, or a
# part that turns into a link while the host reads it. So every route that
# reads or writes a workspace file by path opens it with
# ``acb_skills.safe_open``. ``_safe_resolve`` still answers 400 for an escape,
# and the opener then refuses a link at any depth, which reads as absent.


def _open_rel(root: Path, rel: str) -> str:
    """*rel* as the safe opener takes it, after the containment check."""
    from acb_skills import safe_open

    _safe_resolve(root, rel)
    clean = rel.replace("\\", "/").lstrip("/.")
    try:
        return "/".join(safe_open.split_rel(clean))
    except safe_open.UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Path traversal not allowed") from exc


def _safe_stat(root: Path, rel: str) -> os.stat_result | None:
    """The stat of a regular file reached with no link, or ``None``."""
    from acb_skills import safe_open

    try:
        return safe_open.stat_file(root, rel)
    except (safe_open.UnsafePath, OSError):
        return None


def _safe_chunks(root: Path, rel: str):
    """Yield the bytes of ``root/rel``, opened with the safe opener."""
    from acb_skills import safe_open

    fh = safe_open.open_read(root, rel)
    if fh is None:
        return
    with fh:
        while chunk := fh.read(65536):
            yield chunk


def _safe_write(root: Path, rel: str, data: bytes) -> None:
    """Write ``root/rel`` with the safe opener. A link answers 400."""
    from acb_skills import safe_open

    try:
        safe_open.write_bytes(root, rel, data)
    except safe_open.UnsafePath as exc:
        raise HTTPException(status_code=400, detail="That path is not a plain file.") from exc


def _safe_upload(root: Path, folder: str, name: str, data: bytes) -> str:
    """Write an upload under *folder* with a free name. Returns its rel path.

    The exclusive create picks the name in the same call that writes, so two
    uploads of one name never overwrite each other.
    """
    from acb_skills import safe_open

    stem, ext = Path(name).stem, Path(name).suffix
    candidate, counter = name, 1
    while True:
        rel = f"{folder}/{candidate}"
        try:
            safe_open.write_bytes(root, rel, data, exclusive=True)
            return rel
        except FileExistsError:
            candidate = f"{stem} ({counter}){ext}"
            counter += 1
        except safe_open.UnsafePath as exc:
            raise HTTPException(status_code=400, detail="That folder is not a plain dir.") from exc


# ---------------------------------------------------------------------------
# The thread's own folders (WS-43d and H-227, maf_coding_engine.md §16.3, D12)
# ---------------------------------------------------------------------------
# Every session of a shared agent in one organization opens the same tenant
# dir. A run writes its outputs to its thread's own folder,
# ``outputs/<thread slug>/``, and an upload lands in ``inputs/<thread slug>/``.
# So for a session of a shared agent, the routes list and serve, under
# ``outputs/`` and ``inputs/``, only the folders of THAT session's thread.
# The folder of another thread is hidden and answers 404. The room check stays.
#
# H-227: a LOOSE file, one in ``inputs/`` or ``outputs/`` but in no thread
# folder, comes from before the thread folders: an S8 document or an upload.
# It is listed and served only to a session that the blob history shows
# wrote those exact bytes (:func:`_session_owns_loose`). For any other
# session it is absent.


def _own_thread_slug(
    workspace: Path, session_id: str, organization_id: str | None,
) -> str | None:
    """This session's thread slug in a shared agent's tenant dir, else ``None``.

    ``None`` means the rule does not apply: a personal agent's own dir or an
    app workspace holds no other member's thread.

    ⚠️ The tenant dir is told from its PATH and the caller's own tenant, and
    never from its ``.cc-instance`` marker. A container writes the dir it
    mounts, so a marker it rewrote would switch this rule off.
    """
    if not organization_id:
        return None
    from acb_skills.agent_paths import instance_slug, state_root, tenant_instance

    try:
        ws = workspace.resolve()
        root = state_root().resolve()
    except (OSError, RuntimeError):
        return None
    if ws.parent.parent != root or ws.name != instance_slug(tenant_instance(organization_id)):
        return None
    return instance_slug(str(session_id or ""))


def _is_other_thread_path(rel: str, own_slug: str | None) -> bool:
    """True when *rel* lies in the output or upload folder of a thread that is
    not *own_slug*.

    ``None`` (no tenant dir) never matches. The rule is
    ``agent_paths.is_other_thread_rel``, the one rule of every writer.
    """
    if own_slug is None:
        return False
    from acb_skills.agent_paths import is_other_thread_rel

    return is_other_thread_rel(rel, own_slug)


def _is_loose_path(rel: str, own_slug: str | None) -> bool:
    """True when *rel* is a loose file of a shared agent's tenant dir (H-227).

    ``None`` (no tenant dir) never matches: a personal agent's own dir holds
    only its member's files, so its flat ``inputs/`` and ``outputs/`` stay.
    """
    if own_slug is None:
        return False
    from acb_skills.agent_paths import is_loose_rel

    return is_loose_rel(rel)


def _session_wrote(rows: list[dict], session_id: str, sha256: str) -> bool:
    """THE history rule of a loose file (H-227): this session wrote these bytes.

    A row must name this session and this sha256, and it must write bytes,
    so a ``delete`` row proves nothing. An upload (``user``), an edit in the
    file manager (``user``) and a document of the run (``agent``) all
    count, because each one is the session's own write. ``read_attachment``
    keeps its narrower form of the rule, an upload only (H-229).
    """
    return any(
        r.get("session_id") == session_id and r.get("sha256") == sha256
        and r.get("action") != "delete"
        for r in rows
    )


async def _session_history(
    workspace: Path, organization_id: str | None, session_id: str,
    path: str | None = None,
) -> list[dict]:
    """The rows of a tenant dir's blob history that *session_id* wrote.

    Call it only when :func:`_own_thread_slug` is not ``None``, so the
    workspace IS the caller's tenant dir. So the store key comes from the
    path and the caller's tenant, never from the ``.cc-instance`` marker.
    It reads the tenant key ``o:<org>`` and this tenant's older rows
    (``instance=''``), from before the tenant dir existed (§21.15). The read
    is in the caller's tenant. A missing store or an error gives ``[]``, so
    the rule fails closed.
    """
    try:
        from acb_memory import file_history
    except ImportError:
        return []
    from acb_skills.agent_paths import tenant_instance

    if not session_id or not organization_id:
        return []
    agent = workspace.resolve().parent.name
    rows: list[dict] = []
    for key in (tenant_instance(organization_id), ""):
        rows += await file_history(
            agent, path, 1000, instance=key,
            organization_id=organization_id, session_id=session_id,
        )
    return rows


async def _session_owns_loose(
    workspace: Path, rel: str, data: bytes, session_id: str,
    organization_id: str | None,
) -> bool:
    """True when the history shows that *session_id* wrote *data* at *rel*."""
    import hashlib

    rows = await _session_history(workspace, organization_id, session_id, rel)
    return _session_wrote(rows, session_id, hashlib.sha256(data).hexdigest())


def _safe_sha256(root: Path, rel: str) -> str | None:
    """The sha256 of the regular file ``root/rel``, read with the safe opener."""
    import hashlib

    from acb_skills import safe_open

    try:
        fh = safe_open.open_read(root, rel)
    except (safe_open.UnsafePath, OSError):
        return None
    if fh is None:
        return None
    digest = hashlib.sha256()
    with fh:
        while chunk := fh.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


async def _session_owns_disk_file(
    workspace: Path, rel: str, session_id: str, organization_id: str | None,
) -> bool:
    """:func:`_session_owns_loose` for the file on disk. An absent file is not owned."""
    sha = await asyncio.to_thread(_safe_sha256, workspace, rel)
    if sha is None:
        return False
    rows = await _session_history(workspace, organization_id, session_id, rel)
    return _session_wrote(rows, session_id, sha)


async def _keep_owned_loose(
    workspace: Path, files: list[FileEntry], session_id: str,
    organization_id: str | None,
) -> list[FileEntry]:
    """*files* without the loose files that this session did not write (H-227).

    One history read per store key, for this session's rows only. A loose
    file stays when its bytes on disk match a row of this session.
    """
    from acb_skills.agent_paths import is_loose_rel

    loose = [f for f in files if is_loose_rel(f.path)]
    if not loose:
        return files
    by_path: dict[str, list[dict]] = defaultdict(list)
    for r in await _session_history(workspace, organization_id, session_id):
        by_path[str(r.get("path") or "")].append(r)
    kept: set[str] = set()
    for f in loose:
        if f.path not in by_path:
            continue  # this session wrote nothing at that path, so no read
        sha = await asyncio.to_thread(_safe_sha256, workspace, f.path)
        if sha is not None and _session_wrote(by_path[f.path], session_id, sha):
            kept.add(f.path)
    return [f for f in files if not is_loose_rel(f.path) or f.path in kept]


async def _apply_write_rules(
    workspace: Path, rel: str, user: UserContext, session_id: str, *, claim: bool,
) -> None:
    """The write rules of ``agent_paths.refused_write``, and the skill author.

    WS-43d (review P1, fix round 1). The skill author marker is reserved
    (400), and a skill folder that another member made is theirs (403). In a
    shared agent's tenant dir, the first member who writes into a skill folder
    becomes its author, and the marker goes to the blob store too. A sandboxed
    run loads, and runs the scripts of, only its own member's skills.
    """
    from acb_skills.agent_paths import SKILL_AUTHOR_MARKER, claim_skill, refused_write

    if rel.rsplit("/", 1)[-1] == SKILL_AUTHOR_MARKER:
        raise HTTPException(status_code=400, detail="That file name is reserved.")
    reason = await asyncio.to_thread(
        refused_write, workspace.resolve(), rel, member=user.email, thread_id=session_id,
    )
    if reason and "member" in reason:
        raise HTTPException(status_code=403, detail="That skill belongs to another member.")
    if reason:
        raise HTTPException(status_code=404, detail="File not found")
    if not claim or _own_thread_slug(workspace, session_id, user.organization_id) is None:
        return
    try:
        claimed = await asyncio.to_thread(claim_skill, workspace.resolve(), rel, user.email)
    except ValueError:
        return
    if claimed is not None:
        await _mirror_gateway_write(
            workspace, claimed[0], claimed[1], action="create", session_id=session_id,
            organization_id=user.organization_id,
        )


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
                    # WS-43d (§7.5 rule B): a link is never listed, so the
                    # tree never shows the size of a host file.
                    if fpath.is_symlink():
                        continue
                    stat = fpath.lstat()
                    # A FIFO, a socket or a device is never a workspace file.
                    if not _stat_mod.S_ISREG(stat.st_mode):
                        continue
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


async def _room_for(session_id: str, user: UserContext) -> Any:
    """This caller's place in the session's room, in the caller's tenant.

    H-201 fix round 1 (P1). The workspace belongs to the session, so the
    session's room decides who may reach it. This is S15's
    ``resolve_room_access``, with its rules for an unsaved id, an id of
    another tenant and a failed lookup.
    """
    from gateway.rooms import resolve_room_access

    return await asyncio.to_thread(
        resolve_room_access, session_id, user.email or "",
        organization_id=user.organization_id,
    )


async def _refuse_unless_room(
    session_id: str, user: UserContext, *, send: bool,
) -> None:
    """A read needs ``can_read`` and a change needs ``can_send``.

    A read that is refused answers 404, the same as a session with no
    workspace. A change that is refused answers 403, as a chat save does.
    """
    room = await _room_for(session_id, user)
    if send:
        if not room.can_send:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=room.denied("change its files"),
            )
    elif not room.can_read:
        raise HTTPException(status_code=404, detail="Workspace not found for session")


def _is_service_caller(user: UserContext) -> bool:
    """The internal-token caller with no member, which holds every right."""
    from acb_auth.roles import UserRole

    return user.role is UserRole.AGENT and user.has_permission("*")


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
    # H-201 fix round 1 (P1): a room you cannot read gets the same empty
    # tree as a session with no workspace, as chat gives [].
    if not (await _room_for(session_id, _user)).can_read:
        return WorkspaceTree(session_id=session_id, root="", files=[])
    import asyncio
    loop = asyncio.get_event_loop()
    workspace = await loop.run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None or not workspace.exists():
        return WorkspaceTree(session_id=session_id, root="", files=[])

    files = await loop.run_in_executor(None, _walk_tree, workspace)
    # WS-43d (§16.3) and H-227: another thread's output and upload folders
    # are not listed, and a loose file only for the session that wrote it.
    own = _own_thread_slug(workspace, session_id, _user.organization_id)
    files = [f for f in files if not _is_other_thread_path(f.path, own)]
    if own is not None:
        files = await _keep_owned_loose(workspace, files, session_id, _user.organization_id)
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
    await _refuse_unless_room(session_id, _user, send=False)
    import asyncio
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(status_code=404, detail="Workspace not found for session")

    if _is_blocked_path(path):
        raise HTTPException(status_code=404, detail="File not found")
    rel = _open_rel(workspace, path)
    # WS-43d (§16.3): another thread's output folder answers as absent, and
    # BEFORE the fault-in, so the store cannot restore it either.
    org = _user.organization_id
    own = _own_thread_slug(workspace, session_id, org)
    if _is_other_thread_path(rel, own):
        raise HTTPException(status_code=404, detail="File not found")
    # H-227: a loose file (an S8 document or an upload from before the thread
    # folders) opens only for the session that wrote it. That keeps its old
    # link working for that thread, and for no other session.
    loose = _is_loose_path(rel, own)
    st = await asyncio.to_thread(_safe_stat, workspace, rel)
    if st is None:
        # Fault-in: the store is authoritative, so a file missing from the disk
        # cache may still live in the blob store (e.g. after a volume wipe before
        # the agent has re-run). Restore it on demand, then serve.
        async def _owned(data: bytes) -> bool:
            return await _session_owns_loose(workspace, rel, data, session_id, org)

        restored = await _faultin_from_store(
            workspace, path, organization_id=org, accept=_owned if loose else None,
        )
        st = await asyncio.to_thread(_safe_stat, workspace, rel) if restored else None
        if st is None:
            raise HTTPException(status_code=404, detail="File not found")
    elif loose and not await _session_owns_disk_file(workspace, rel, session_id, org):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = st.st_size
    name = rel.rsplit("/", 1)[-1]
    if format_ == "pdf":
        # The member is the authenticated caller, never request input.
        return await _file_as_pdf(
            workspace, rel, name, file_size, member=_user.email or "",
        )
    if file_size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file_size} bytes). Maximum is {_MAX_FILE_BYTES} bytes.",
        )

    mime, _ = mimetypes.guess_type(name)
    media_type = mime or "application/octet-stream"

    return StreamingResponse(
        _safe_chunks(workspace, rel),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            "Content-Length": str(file_size),
        },
    )


async def _file_as_pdf(
    workspace: Path, rel: str, name: str, file_size: int, *, member: str,
) -> Response:
    """A workspace document as a PDF download (WS-27bm S8, spec §14).

    The type check comes before the size check and before any read, so a
    refused type costs nothing. The read runs in a thread, and the layout
    runs in a child process (``pdf_render.render_pdf``).
    """
    from gateway.db import current_tenant
    from gateway.pdf_render import (
        MAX_SOURCE_BYTES,
        SOURCE_KINDS,
        PdfRenderError,
        attachment_disposition,
        pdf_filename,
        render_pdf,
    )

    from acb_skills import safe_open

    kind = SOURCE_KINDS.get(Path(name).suffix.lower())
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
    try:
        raw = await asyncio.to_thread(
            safe_open.read_bytes, workspace, rel, limit=MAX_SOURCE_BYTES,
        )
    except (safe_open.UnsafePath, OSError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if raw is None:
        raise HTTPException(status_code=404, detail="File not found")
    source = raw.decode("utf-8", errors="replace")
    try:
        # Out of process, with a timeout: a MuPDF crash or hang is a refusal
        # here, never the gateway's death (fix round 1).
        pdf = await render_pdf(kind, source, member=member, org=current_tenant())
    except PdfRenderError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": attachment_disposition(pdf_filename(name))},
    )


@router.patch("/workspace/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_workspace_path(
    session_id: str,
    body: WorkspacePatchRequest,
    _user: UserContext = Depends(get_current_user),
) -> None:
    """Record the workspace_path for a session.

    H-201: the UPDATE runs in the caller's tenant. Under FORCE RLS an unbound
    UPDATE matches no row and changes nothing. With no tenant the write
    raises ``TenantUnbound``, and the app handler answers it.

    H-201 fix round 1 (P0): the path must resolve, with symlinks followed,
    inside the workspace of an app that the caller may edit in their tenant
    (:func:`_allowed_workspace`). Anything else gets 422, and no row changes.
    The stored value is the resolved path.
    """
    await _refuse_unless_room(session_id, _user, send=True)
    import asyncio

    from acb_graph import tenant_session
    from gateway.db import TenantUnbound

    organization_id = _user.organization_id
    allowed = await asyncio.to_thread(
        _allowed_workspace, body.workspace_path, session_id=session_id,
        user_email=_user.email, organization_id=organization_id, write=True,
    )
    if allowed is None:
        _log.warning("workspace.patch_path_rejected", session_id=session_id,
                     workspace_path=body.workspace_path[:200])
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That workspace path is not an app workspace you may edit.",
        )
    try:
        from sqlalchemy import text

        def _write() -> None:
            # tenant_session commits when the block ends.
            with tenant_session(organization_id) as s:
                s.execute(
                    text(
                        "UPDATE chat_session SET workspace_path = :path "
                        "WHERE id = :id"
                    ),
                    {"path": str(allowed), "id": session_id},
                )

        await asyncio.to_thread(_write)
    except TenantUnbound:
        raise
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
    # The write_artifact tool posts here with the internal token and no
    # member. That caller already holds every right, so it skips the room.
    if not _is_service_caller(_user):
        await _refuse_unless_room(session_id, _user, send=True)
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
    await _refuse_unless_room(session_id, _user, send=False)
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
    """Upload one or more files into the session's attachment folder.

    The folder is ``acb_skills.agent_paths.upload_dir_rel`` (H-229). A shared
    agent's tenant dir is one folder for every member of the organization, so
    there a file lands in ``inputs/<thread slug>/``, the folder of this
    session's thread. ``read_attachment`` reads it only in a run of that
    thread (D12). Since H-227 the session file routes, the sandbox file store
    and the container also keep to the session's own thread folder. Any other
    workspace keeps ``inputs/``. The browser tells the agent the names and the
    paths in the next message.
    """
    from acb_skills.agent_paths import upload_dir_rel

    await _refuse_unless_room(session_id, _user, send=True)
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail="No workspace found for this session. "
                   "Start a chat with an agent first.",
        )
    _refuse_shared_clone_write(workspace)

    # The session id is the thread id, and the room check above admitted the
    # caller to it. The rule is shared with the tool that reads the folder.
    # The tenant dir is told from its PATH and the caller's own tenant, as
    # `_own_thread_slug` tells it, never from the `.cc-instance` marker that
    # a container could rewrite.
    from acb_skills.agent_paths import tenant_instance

    org = _user.organization_id
    tenant_key = (
        tenant_instance(org)
        if _own_thread_slug(workspace, session_id, org) is not None else ""
    )
    try:
        upload_rel = upload_dir_rel(tenant_key, session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="This chat cannot take an upload: its id is not a plain thread id.",
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

        # Avoid overwrites: append (1), (2), etc. WS-43d (§7.5 rule B): the
        # name is taken and written in one exclusive create, with no link.
        rel_path = await asyncio.to_thread(
            _safe_upload, workspace, upload_rel, safe_name, content,
        )
        dest_name = rel_path.rsplit("/", 1)[-1]

        # Build response entry
        stat = await asyncio.to_thread(_safe_stat, workspace, rel_path)
        if stat is None:
            raise HTTPException(status_code=500, detail="The upload was not stored.")
        mime, _ = mimetypes.guess_type(safe_name)

        # Write-through: a user upload (inputs/) is durable state too.
        await _mirror_gateway_write(
            workspace, rel_path, content, action="create", session_id=session_id,
            organization_id=_user.organization_id,
        )

        uploaded.append(FileEntry(
            path=rel_path,
            name=dest_name,
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
    files land in the uploader's own state dir for a ``personal`` agent, and
    can then be picked via ``GET /agent/artifacts?agent=…&category=…`` — which
    resolves the SAME directory for the same member. H-201 part 2: a shared
    or team agent, or one with no clone, answers 404
    (:func:`_member_agent_workspace`)."""
    from acb_skills.agent_paths import is_valid_agent_name

    # H-201 fix round 2 (P0): the agent name is a query string, and it is
    # joined onto repos/. A name that is not one safe segment gets 422.
    if not is_valid_agent_name(agent):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Not a valid agent name.",
        )
    cat = category if category in ("agent-data", "inputs", "outputs") else "agent-data"
    # H-201 part 2: only the caller's own instanced folder. A shared agent's
    # clone, a team folder and the canonical fallback are never a target.
    workspace = _member_agent_workspace(agent, _user.email)
    if workspace is None:
        raise HTTPException(
            status_code=404, detail=f"Agent workspace not found: {agent}",
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
        # WS-43d (§7.5 rule B): one exclusive create, with no link followed.
        rel_path = await asyncio.to_thread(_safe_upload, workspace, cat, safe_name, content)
        stat = await asyncio.to_thread(_safe_stat, workspace, rel_path)
        if stat is None:
            raise HTTPException(status_code=500, detail="The upload was not stored.")
        mime, _ = mimetypes.guess_type(safe_name)
        # Write-through to the authoritative blob store.
        await _mirror_gateway_write(
            workspace, rel_path, content, action="create", session_id=None,
            organization_id=_user.organization_id,
        )
        uploaded.append(FileEntry(
            path=rel_path, name=rel_path.rsplit("/", 1)[-1], size=stat.st_size,
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
    await _refuse_unless_room(session_id, _user, send=True)
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail="Workspace not found for session"
        )
    _refuse_shared_clone_write(workspace)

    from acb_skills import safe_open

    rel = _open_rel(workspace, path)
    # WS-43d (§16.3) and H-227: another thread's folders answer as absent,
    # and so does a loose file that this session did not write.
    own = _own_thread_slug(workspace, session_id, _user.organization_id)
    if _is_other_thread_path(rel, own):
        raise HTTPException(status_code=404, detail="File not found")
    if _is_loose_path(rel, own) and not await _session_owns_disk_file(
        workspace, rel, session_id, _user.organization_id,
    ):
        raise HTTPException(status_code=404, detail="File not found")
    # Only allow deletion of files within the visible workspace dirs.
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Deletion is restricted to inputs/, outputs/, and agent-data/.",
        )
    await _apply_write_rules(workspace, rel, _user, session_id, claim=False)
    try:
        # WS-43d (§7.5 rule B): no part of the path is followed as a link.
        deleted = await asyncio.to_thread(safe_open.unlink, workspace, rel)
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="Cannot delete directories") from exc
    except safe_open.UnsafePath as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    # Write-through the delete to the authoritative store (records a delete
    # version in history).
    await _mirror_gateway_delete(
        workspace, rel, session_id=session_id,
        organization_id=_user.organization_id,
    )
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
    await _refuse_unless_room(session_id, _user, send=True)
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(status_code=404, detail="workspace not found")
    _refuse_shared_clone_write(workspace)

    src_rel = body.path.replace("\\", "/").lstrip("/")
    if not src_rel.startswith("inputs/"):
        raise HTTPException(status_code=400, detail="only inputs/ files can be promoted")
    from acb_skills import safe_open

    # WS-43d (§7.5 rule B): the read, the write and the unlink all go
    # through the safe opener. A link reads as absent.
    src_rel = _open_rel(workspace, src_rel)
    # H-227: another thread's upload, and a loose file that this session did
    # not write, are absent here too. Else a member could move a colleague's
    # upload into the shared agent-data/.
    own = _own_thread_slug(workspace, session_id, _user.organization_id)
    if _is_other_thread_path(src_rel, own):
        raise HTTPException(status_code=404, detail="source file not found")
    try:
        data = await asyncio.to_thread(safe_open.read_bytes, workspace, src_rel)
    except (safe_open.UnsafePath, OSError):
        data = None
    if data is None or (
        _is_loose_path(src_rel, own) and not await _session_owns_loose(
            workspace, src_rel, data, session_id, _user.organization_id,
        )
    ):
        raise HTTPException(status_code=404, detail="source file not found")
    src_name = src_rel.rsplit("/", 1)[-1]

    dest_rel = (body.dest or f"agent-data/{src_name}").replace("\\", "/").lstrip("/")
    if not dest_rel.startswith("agent-data/"):
        raise HTTPException(status_code=400, detail="destination must be under agent-data/")
    dest_rel = _open_rel(workspace, dest_rel)
    await _apply_write_rules(workspace, dest_rel, _user, session_id, claim=True)
    await asyncio.to_thread(_safe_write, workspace, dest_rel, data)
    await asyncio.to_thread(safe_open.unlink, workspace, src_rel)
    dest_name = dest_rel.rsplit("/", 1)[-1]

    mime, _ = mimetypes.guess_type(dest_name)
    mime = mime or "application/octet-stream"
    # Store: record the new agent-data version (promote) + the inputs delete.
    await _mirror_gateway_write(
        workspace, dest_rel, data, action="promote", session_id=session_id,
        organization_id=_user.organization_id,
    )
    await _mirror_gateway_delete(
        workspace, src_rel, session_id=session_id,
        organization_id=_user.organization_id,
    )

    stat = await asyncio.to_thread(_safe_stat, workspace, dest_rel)
    if stat is None:
        raise HTTPException(status_code=500, detail="The promoted file was not stored.")
    _log.info("workspace.promoted", session_id=session_id, src=src_rel, dest=dest_rel)
    return FileEntry(
        path=dest_rel,
        name=dest_name,
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
    # H-201 fix round 1 (P1): the same empty answer as no workspace.
    if not (await _room_for(session_id, _user)).can_read:
        return {"history": []}
    workspace = await asyncio.get_event_loop().run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None:
        return {"history": []}
    try:
        from acb_memory import file_history
    except ImportError:
        return {"history": []}
    agent, instance = _blob_key_for_workspace(workspace)
    own = _own_thread_slug(workspace, session_id, _user.organization_id)
    if path and _is_other_thread_path(path, own):
        return {"history": []}
    rows = await file_history(
        agent, path, limit, instance=instance,
        organization_id=_user.organization_id,
    )
    # WS-43d (§16.3) and H-227: the files of another thread's folders are not
    # listed, not even by name. A row of a loose file shows only when this
    # session wrote that row, so another session's versions stay hidden.
    rows = [
        r for r in rows
        if not _is_other_thread_path(str(r.get("path") or ""), own)
        and (
            not _is_loose_path(str(r.get("path") or ""), own)
            or r.get("session_id") == session_id
        )
    ]
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
    await _refuse_unless_room(session_id, _user, send=True)
    import base64

    loop = asyncio.get_event_loop()
    workspace = await loop.run_in_executor(
        None, _get_workspace_path, session_id, _user.email,
        _user.organization_id,
    )
    if workspace is None or not workspace.exists():
        raise HTTPException(
            status_code=404, detail="Workspace not found for session"
        )
    _refuse_shared_clone_write(workspace)

    rel = _open_rel(workspace, path)
    # Only allow writes within the visible workspace dirs (inputs/, outputs/,
    # agent-data/).  The agent itself can write anywhere, but the frontend
    # user is restricted to the three visible folders.
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Writes are restricted to inputs/, outputs/, and agent-data/.",
        )
    # WS-43d (§16.3) and H-227: another thread's folders answer as absent.
    # A loose file can change only from the session that wrote it. A new
    # loose path gets the same answer, so the answer tells no one which loose
    # file exists. A new file goes in the thread's own folder.
    own = _own_thread_slug(workspace, session_id, _user.organization_id)
    if _is_other_thread_path(rel, own):
        raise HTTPException(status_code=404, detail="File not found")
    if _is_loose_path(rel, own) and not await _session_owns_disk_file(
        workspace, rel, session_id, _user.organization_id,
    ):
        raise HTTPException(status_code=404, detail="File not found")
    await _apply_write_rules(workspace, rel, _user, session_id, claim=True)

    _existed = await asyncio.to_thread(_safe_stat, workspace, rel) is not None
    data = (
        base64.b64decode(body.content) if body.encoding == "base64"
        else body.content.encode("utf-8")
    )
    # WS-43d (§7.5 rule B): parent dirs and the file, with no link followed.
    await asyncio.to_thread(_safe_write, workspace, rel, data)

    # Build response
    stat = await asyncio.to_thread(_safe_stat, workspace, rel)
    if stat is None:
        raise HTTPException(status_code=500, detail="The file was not stored.")
    file_name = rel.rsplit("/", 1)[-1]
    mime, _ = mimetypes.guess_type(file_name)
    rel_path = rel

    # Write-through to the authoritative blob store.
    await _mirror_gateway_write(
        workspace, rel_path, data,
        action="modify" if _existed else "create", session_id=session_id,
        organization_id=_user.organization_id,
    )

    _log.info(
        "workspace.file_written",
        session_id=session_id,
        path=rel_path,
        size=stat.st_size,
    )

    return FileEntry(
        path=rel_path,
        name=file_name,
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
    """Return {agent_name: workspace_path} for the caller's OWN workspaces.

    Collects the names of every live agent from the registries, then resolves
    each through :func:`_member_agent_workspace` for the viewing member. That
    is the member's private state dir of a ``personal`` agent, the directory
    their runs write to.

    H-201 part 2 (``projects_ai_chat.md`` §21.14). A shared agent, a team
    agent and an agent with no clone have NO entry. Their folder is one
    folder for every tenant, so it is never listed, read or written here. No
    caller (``None`` or no ``@``) gets an empty dict.

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

    # ── Resolve each name to the caller's own workspace. H-201 part 2: the
    # old fallback to the canonical repos/<agent> path is gone. That path is
    # the shared clone, one folder for every tenant.
    workspaces: dict[str, Path] = {}
    for name in names:
        ws = _member_agent_workspace(name, user_email)  # never raises
        if ws is not None:
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
    """Artifact browser — lists the files of the caller's own workspaces.

    Returns every file of each workspace that
    :func:`_discover_agent_workspaces` gives the caller: the state dir of
    each ``personal`` agent. A shared agent is absent (H-201 part 2).
    Supports optional filtering by agent name and category.
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
    """Stream a single file from the caller's own workspace for *agent*.

    A shared agent, or an agent the caller has no workspace for, is 404
    (H-201 part 2, :func:`_member_agent_workspace`).
    """
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
    rel = _open_rel(workspace, path)
    st = await _asyncio.to_thread(_safe_stat, workspace, rel)
    if st is None:
        # Fault-in: the store is authoritative, so a file missing from the disk
        # cache may still live in the blob store (e.g. after a volume wipe before
        # the agent has re-run). Restore it on demand, then serve.
        restored = await _faultin_from_store(
            workspace, path, organization_id=_user.organization_id,
        )
        st = await _asyncio.to_thread(_safe_stat, workspace, rel) if restored else None
        if st is None:
            raise HTTPException(status_code=404, detail="File not found")

    file_size = st.st_size
    if file_size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file_size} bytes). "
                   f"Maximum is {_MAX_FILE_BYTES} bytes.",
        )

    name = rel.rsplit("/", 1)[-1]
    mime, _ = mimetypes.guess_type(name)
    media_type = mime or "application/octet-stream"

    return StreamingResponse(
        _safe_chunks(workspace, rel),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
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
    """Overwrite a file in the caller's own workspace for *agent*.

    Uses the same discovery as GET /artifacts/file, so a shared agent is 404
    (H-201 part 2).
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

    rel = _open_rel(workspace, path)
    # Restrict writes to visible workspace dirs
    if not _is_visible_workspace_path(rel):
        raise HTTPException(
            status_code=400,
            detail="Writes are restricted to inputs/, outputs/, and agent-data/.",
        )

    _existed = await _asyncio.to_thread(_safe_stat, workspace, rel) is not None
    data = (
        base64.b64decode(body.content) if body.encoding == "base64"
        else body.content.encode("utf-8")
    )
    # WS-43d (§7.5 rule B): parent dirs and the file, with no link followed.
    await _asyncio.to_thread(_safe_write, workspace, rel, data)

    # Build response
    stat = await _asyncio.to_thread(_safe_stat, workspace, rel)
    if stat is None:
        raise HTTPException(status_code=500, detail="The file was not stored.")
    file_name = rel.rsplit("/", 1)[-1]
    mime, _ = mimetypes.guess_type(file_name)
    rel_path = rel

    # Write-through to the authoritative blob store (agent = the explicit target).
    await _mirror_gateway_write(
        workspace, rel_path, data,
        action="modify" if _existed else "create", session_id=None,
        organization_id=_user.organization_id,
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
        name=file_name,
        size=stat.st_size,
        modified_at=__import__("datetime").datetime.fromtimestamp(
            stat.st_mtime, tz=__import__("datetime").timezone.utc
        ).isoformat(),
        mime_type=mime or "application/octet-stream",
        category=cat,
        is_dir=False,
    )
