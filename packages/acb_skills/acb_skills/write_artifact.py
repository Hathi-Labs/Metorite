"""write_artifact — agent tool for writing files to a session workspace.

Auto-injected into every agent alongside ``web_search`` and ``call_agent``.

The tool:
1. Defaults to the ``outputs/`` directory when no visible workspace dir
   (``inputs/``, ``outputs/``, ``agent-data/``) is specified in the path.
2. Writes the file under ``{workspace_root}/{path}`` (creating parent dirs).
3. Computes a SHA-256 hash of the content.
4. Emits an AG-UI ``CUSTOM`` event ``artifact_created`` / ``artifact_updated``
   so the Control Plane sidebar updates in real time.
5. PATCHes the gateway to register the workspace root on the session.
6. Returns a ``download_url`` the agent SHOULD embed in its text response.

In a shared agent's tenant dir (store key ``o:<org>``), every member of the
organization opens the same dir. So a document of one chat goes to that
chat's own folder, ``outputs/<thread slug>/``, and an ``inputs/`` path to
``inputs/<thread slug>/``. The card links there, and the session routes
serve it only to a session of that thread (H-227, D12).

Usage by agents:
    result = await write_artifact("summary.md", "# Sales Summary\\n...")
    # File lands in outputs/summary.md (auto-prefixed)
    # Agent outputs: [📄 Download summary.md]({download_url})
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import mimetypes
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

# ── The run's artifact context (H-201, projects_ai_chat.md §21.16) ──────────
#
# The workspace, the tenant partition, the session and the gateway of the run
# that executes on THIS frame. It was one process-global dict. The gateway is
# one process, so when two runs overlapped the later setup overwrote the
# earlier, and run A wrote its document into run B's tenant dir and posted the
# event to run B's session.
#
# Now it is a ContextVar that holds an IMMUTABLE mapping. Every asyncio task
# copies the context at its creation, so each run, and each task a run spawns,
# sees only its own value. A change makes a new mapping and never edits the
# old one, so a child cannot change what its parent sees.
#
# Keys the executor binds: session_id, agent_name, run_id, workspace_root,
# instance, member, integrations, integration_warnings, gateway_url,
# gateway_token, permission_check_root for a sandboxed Copilot session, and
# the two D85 flags: shell_tools_withheld (the injected shell tools are
# withheld, a cover may lift it) and host_shell_refused (the Copilot CLI's
# own shell on the host is refused, read by
# permission_policy.guard_shared_agent_shell).
#
# A frame with no run context reads an EMPTY mapping. Every reader then fails
# closed: it writes nothing and emits to no session. No reader may fall back
# to a value of another run. Fence: tests/unit/test_h201_run_context.py.
_RUN_ARTIFACT_CONTEXT: contextvars.ContextVar[Mapping[str, Any] | None] = (
    contextvars.ContextVar("acb_run_artifact_context", default=None)
)
_NO_RUN: Mapping[str, Any] = MappingProxyType({})


def artifact_context() -> Mapping[str, Any]:
    """The artifact context of the run on this frame. THE one reader.

    Returns an empty, read-only mapping when no run is bound.
    """
    ctx = _RUN_ARTIFACT_CONTEXT.get()
    return _NO_RUN if ctx is None else ctx


def bind_artifact_context(**values: Any) -> None:
    """Set a NEW artifact context for the run on this frame.

    It replaces the context this frame had, and keeps no key of it. The run
    boundary calls this. Call it inside :func:`artifact_context_scope`, so the
    earlier value comes back when the scope ends.
    """
    _RUN_ARTIFACT_CONTEXT.set(MappingProxyType(dict(values)))


def derive_artifact_context(**changes: Any) -> None:
    """Set a copy of this frame's context with *changes* applied.

    A ``None`` value removes the key. A sub-agent uses this to get its OWN
    context from its parent's. The parent's mapping does not change.
    """
    merged = dict(artifact_context())
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    _RUN_ARTIFACT_CONTEXT.set(MappingProxyType(merged))


@contextlib.contextmanager
def artifact_context_scope() -> Iterator[None]:
    """Restore this frame's artifact context when the block ends.

    Every bind or derive inside the block ends with it. The reset uses the
    ContextVar token, so it restores the exact earlier value.
    """
    token = _RUN_ARTIFACT_CONTEXT.set(_RUN_ARTIFACT_CONTEXT.get())
    try:
        yield
    finally:
        reset_artifact_context(token)


def enter_artifact_context() -> contextvars.Token:
    """Open a scope that an async generator can close in its ``finally``.

    :func:`artifact_context_scope` for a frame that cannot hold a ``with``
    block across its yields. Pass the token to :func:`reset_artifact_context`.
    """
    return _RUN_ARTIFACT_CONTEXT.set(_RUN_ARTIFACT_CONTEXT.get())


def reset_artifact_context(token: contextvars.Token) -> None:
    """Close a scope. Never raises.

    A generator that another task closes runs its ``finally`` in a different
    context, and the token then does not apply. The context of that frame is
    cleared instead, so no value of the run stays behind.
    """
    try:
        _RUN_ARTIFACT_CONTEXT.reset(token)
    except (ValueError, RuntimeError):
        _RUN_ARTIFACT_CONTEXT.set(None)


# ── The chat that asked: where a delegated run delivers its documents ───────
#
# A sub-agent (call_agent, call_agents_parallel, call_agent_background,
# delegate_to_agent) works in its OWN working dir: email-assistant is personal,
# so it works in the u:<member> dir of the member. Its card shows in the chat
# that called it, and the card links to that chat's session. The session
# routes serve that link from the CHAT's working dir and thread folder, so a
# document in the sub-agent's dir answered 404 (2026-10-05, Welmont brief).
#
# So the run boundary binds ``deliver_to``: the working dir, the store key,
# the agent and the session of the chat run. ``write_artifact`` and
# ``share_artifact`` put a document there, in that chat's own thread folder.
# The value comes from the parent's bound context only, never from the model
# or a tool argument (R5). Fence: tests/unit/test_delegated_artifact_card.py.

#: The context key of a delegated run's delivery target.
DELIVER_TO = "deliver_to"
_TARGET_KEYS = ("workspace_root", "session_id", "agent_name", "instance")


def delegation_target(parent: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Where a run that *parent* delegates to delivers its documents, or ``None``.

    *parent* is the bound artifact context of the run that delegates, read
    on the frame of the delegation, before the sub-run binds its own. The
    target of a delegated parent passes on, so a grandchild delivers to the
    chat at the top. A batch run (no chat) is no target: its sub-run keeps
    its own working dir, as before. A parent with no working dir, session
    or agent is no target either.
    """
    inherited = parent.get(DELIVER_TO)
    if isinstance(inherited, Mapping):
        return inherited
    if parent.get("batch_thread") is True:
        return None
    values = {key: str(parent.get(key) or "") for key in _TARGET_KEYS}
    if not (values["workspace_root"] and values["session_id"] and values["agent_name"]):
        return None
    return MappingProxyType(values)


def _destination(ctx: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """The context that names where a document goes, and True for a delegated run."""
    target = ctx.get(DELIVER_TO)
    if isinstance(target, Mapping) and target.get("workspace_root") and target.get("session_id"):
        return target, True
    return ctx, False

# Visible workspace dirs — files written outside these are hidden in the UI.
_VISIBLE_DIRS = frozenset({"inputs", "outputs", "agent-data"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_agent_name() -> str:
    """Best-effort agent name for the current run (blob-store key).

    Prefers the explicit context value the executor sets; falls back to the
    workspace_root basename ({agents_clone_dir}/repos/<agent_name>). A tenant
    state dir ({agents_clone_dir}/state/<agent_name>/<slug>) puts the SLUG in
    the basename — keying blobs by slug would silently shard one agent's
    store — so the agent name is its parent there.
    """
    name = artifact_context().get("agent_name")
    if name:
        return str(name)
    root = artifact_context().get("workspace_root")
    if not root:
        return ""
    p = Path(root)
    if p.parent.parent.name == "state":
        return p.parent.name
    return p.name


async def mirror_to_blob_store(
    rel_path: str,
    data: bytes,
    *,
    mime_type: str = "application/octet-stream",
    action: str = "modify",
    actor: str = "agent",
    target: Mapping[str, Any] | None = None,
) -> None:
    """Write-through a workspace file into the authoritative blob store.

    Files under agent-data/, inputs/, outputs/ are mirrored to Postgres (source
    of truth; the disk workspace is a cache) and a version-history row recorded.
    No-op for other paths, when the store isn't available, or on any error — the
    on-disk file is already written, so this never blocks the agent.

    *target* is a delegated run's delivery target (:func:`delegation_target`).
    The row then carries the agent, the store key and the session of the chat
    whose working dir holds the file, so the fault-in of that chat finds it.
    """
    try:
        from acb_memory import is_stored_path, put_file
    except ImportError:
        return
    if not is_stored_path(rel_path):
        return
    ctx = artifact_context()
    if target is not None:
        agent_name = str(target.get("agent_name") or "")
        session_id = target.get("session_id")
        instance = str(target.get("instance") or "")
    else:
        agent_name = _current_agent_name()
        session_id = ctx.get("session_id")
        # The run's tenant partition, set by the executor alongside
        # workspace_root — disk and store must carry the SAME key, or a
        # personal agent's files rehydrate into the wrong person's run.
        instance = ctx.get("instance", "")
    if not agent_name:
        return
    await put_file(
        agent_name,
        rel_path.replace("\\", "/"),
        data,
        mime_type=mime_type,
        action=action,
        run_id=ctx.get("run_id"),
        session_id=session_id,
        actor=actor,
        instance=instance,
    )


async def mirror_delete_from_blob_store(rel_path: str, *, actor: str = "agent") -> None:
    """Write-through a workspace delete into the blob store.

    The delete seam is ``acb_memory.blob_store.delete_file``. A tenant dir
    (``o:<org>``) also deletes the tenant's older row at the same path
    (``instance=''``), as the gateway's delete route does, or the next
    rehydrate would bring the file back from that row. A no-op for a path
    outside the three kept folders, with no run bound, or on any error.
    """
    try:
        from acb_memory import delete_file, is_stored_path

        from acb_skills.agent_paths import is_tenant_instance
    except ImportError:
        return
    rel = rel_path.replace("\\", "/")
    if not is_stored_path(rel):
        return
    agent_name = _current_agent_name()
    if not agent_name:
        return
    ctx = artifact_context()
    instance = str(ctx.get("instance") or "")
    keys = [instance, ""] if is_tenant_instance(instance) else [instance]
    for key in keys:
        await delete_file(
            agent_name, rel,
            run_id=ctx.get("run_id"), session_id=ctx.get("session_id"),
            actor=actor, instance=key,
        )


def announce_artifact(rel_path: str, data: bytes) -> str | None:
    """Show a file the run wrote as an artifact card in the chat.

    The same ``artifact_created`` event that :func:`write_artifact` emits,
    through the same :func:`_notify`. The sandbox tools call it for a file
    that a command or a file tool wrote under the thread's output folder.
    Returns the download link, or ``None`` when no run is bound.
    """
    import asyncio

    ctx = artifact_context()
    workspace_root = ctx.get("workspace_root")
    session_id = ctx.get("session_id")
    if not workspace_root or not session_id:
        return None
    rel = rel_path.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    mime, _ = mimetypes.guess_type(name)
    artifact = {
        "path": rel,
        "name": name,
        "size": len(data),
        "sha256": _sha256(data),
        "mime_type": mime or "application/octet-stream",
        "modified_at": datetime.now(tz=UTC).isoformat(),
        "is_dir": False,
    }
    asyncio.ensure_future(_notify(
        session_id=session_id,
        workspace_root=workspace_root,
        artifact=artifact,
        gateway_url=ctx.get("gateway_url", "http://127.0.0.1:8000"),
        gateway_token=ctx.get("gateway_token", "sk-local-dev-change-me"),
    ))
    return f"/api/agent/workspace/{session_id}/file?path={rel}"


def _normalise_path(path: str) -> str:
    """Strip leading slashes/dots and ensure the path lives in a visible dir.

    If *path* doesn't start with ``inputs/``, ``outputs/``, or ``agent-data/``,
    it is automatically prefixed with ``outputs/`` so the file appears in the
    Files Viewer sidebar.

    NOTE: this only strips a LEADING ``/.`` — it does not neutralise an EMBEDDED
    ``..`` (e.g. ``outputs/../../etc/x``). Containment is enforced separately by
    :func:`resolve_in_workspace`; every tool that turns a caller path into a
    filesystem path MUST route it through that guard.
    """
    clean = path.replace("\\", "/").lstrip("/.")
    # Already in a visible dir — use as-is.
    for d in _VISIBLE_DIRS:
        if clean == d or clean.startswith(d + "/"):
            return clean
    # Default: write to outputs/
    return f"outputs/{clean}"


#: The history actor of a document that a batch run wrote (H-227). The
#: gateway lets every member of the organization read such a loose file, and
#: nobody change it (``workspace._batch_readable``). No request can set it.
BATCH_ACTOR = "batch"


def _is_batch_run(ctx: Mapping[str, Any]) -> bool:
    """True for a batch run in a shared agent's tenant dir (the H-227 decision).

    A run with no chat: a workflow node, ``/agent/run`` or ``/agent/run/async``
    with no thread, or a task that a member assigns to an agent. Its document
    belongs to the organization, as it did before H-227, so it goes to the
    flat folder and every member may read it. ``batch_thread`` comes only
    from the executor, which sets it when it mints the thread itself and no
    parent run is bound. A thread id that a client sends never sets it, also
    one shaped ``<agent>:<run id>``.
    """
    from acb_skills.agent_paths import is_tenant_instance

    return ctx.get("batch_thread") is True and is_tenant_instance(ctx.get("instance"))


def _chat_document_path(clean_path: str, path: str, ctx: Mapping[str, Any]) -> str | dict:
    """The path of a chat run's document (:func:`_thread_scoped`), or the error
    to answer."""
    scoped = _thread_scoped(clean_path, ctx)
    if scoped is None:
        return {"error": "This chat has no thread folder, so nothing was written."}
    if scoped != clean_path and len(scoped.split("/")) < 3:  # the thread folder itself
        return {"error": f"Path '{path}' names no file, so nothing was written."}
    return scoped


async def _path_has_history(rel: str, ctx: Mapping[str, Any]) -> bool:
    """True when the blob history holds a write at *rel* in this run's store
    key or its older ``''`` key. A file that the store still holds may be
    missing on disk (the fault-in state), so the disk alone cannot say."""
    try:
        from acb_memory import file_history
    except ImportError:
        return False
    agent = _current_agent_name()
    if not agent:
        return False
    for key in (str(ctx.get("instance") or ""), ""):
        rows = await file_history(agent, rel, 20, instance=key)
        if any(r.get("action") != "delete" for r in rows):
            return True
    return False


async def _take_batch_name(
    root: Path, rel: str, data: bytes, ctx: Mapping[str, Any],
) -> str:
    """Write *data* at *rel*, or at ``name (n).ext``, and return the path taken.

    The H-227 batch rule, fix round 2 of PR #616. A name is free only when the
    history holds no write there (:func:`_path_has_history`), and
    ``safe_open.write_bytes(..., exclusive=True)`` takes it in one call. So a
    member's file that is missing on disk but kept in the store, and a second
    batch run that races for the same name, never lose their bytes. Raises
    ``FileExistsError`` after 1,000 names.
    """
    import asyncio
    from pathlib import PurePosixPath

    from acb_skills import safe_open

    p = PurePosixPath(rel)
    for counter in range(1000):
        name = p.name if counter == 0 else f"{p.stem} ({counter}){p.suffix}"
        candidate = str(p.with_name(name))
        if await _path_has_history(candidate, ctx):
            continue
        try:
            await asyncio.to_thread(
                safe_open.write_bytes, root, candidate, data, exclusive=True,
            )
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(rel)


#: The one folder of the chat that called it where a delegated run may
#: write. Its uploads (``inputs/``) read as the member's own files, and its
#: ``agent-data/`` is the memory of the chat's agent (PR #656, fix round 1).
_DELIVERY_HEAD = "outputs"


def _delivery_path(
    dest: Mapping[str, Any], path: str, *, member: object,
) -> tuple[Path, str] | dict:
    """The root and the thread-folder path of a document a delegated run
    delivers to *dest*, or the error to answer.

    The same rules as a document of the chat itself: containment
    (:func:`resolve_in_workspace`), the thread folder of the chat
    (:func:`_chat_document_path`) and :func:`agent_paths.refused_write`.

    A delegated run writes only in ``outputs/`` of the chat. The rule reads
    the RESOLVED, thread-scoped path, never the input string, so
    ``outputs/../inputs/x`` cannot reach an upload (fix round 1). A ``..``
    or ``.`` part, a NUL and a drive are refused before any resolve.
    """
    from acb_skills import safe_open
    from acb_skills.agent_paths import refused_write

    raw = str(path or "").replace("\\", "/")
    if chr(0) in raw or any(part == ".." for part in raw.split("/")):
        return {"error": f"Path '{path}' was refused: it may not hold '..'."}
    clean = _normalise_path(raw)
    try:
        safe_open.split_rel(clean)
    except safe_open.UnsafePath as exc:
        return {"error": f"Path '{path}' was refused: {exc}"}
    if any(":" in part for part in clean.split("/")):
        return {"error": f"Path '{path}' was refused: a name may not hold ':'."}
    root_r = Path(str(dest["workspace_root"])).resolve()
    target = resolve_in_workspace(root_r, clean)
    if target is None:
        return {"error": f"Path '{path}' escapes the workspace and was refused."}
    scoped = _chat_document_path(target.relative_to(root_r).as_posix(), path, dest)
    if isinstance(scoped, dict):
        return scoped
    head = scoped.split("/", 1)[0]
    if head != _DELIVERY_HEAD or "/" not in scoped:
        what = "that chat's uploads" if head == "inputs" else "outside its outputs/"
        return {"error": (
            f"Path '{path}' was refused: an agent that another chat called "
            f"cannot write {what}. Write to outputs/."
        )}
    reason = refused_write(
        root_r, scoped, member=member, thread_id=dest.get("session_id"),
    )
    if reason:
        return {"error": f"Path '{path}' was refused: {reason}."}
    return root_r, scoped


def _write_delivered(
    root: Path, rel: str, data: bytes, *, overwrite: bool, reuse_same: bool,
) -> tuple[str, bool, bool]:
    """Write *data* at *rel* beneath *root* with the safe opener.

    Returns ``(path taken, existed, changed)``. The working dir of the chat
    may be mounted in its sandbox container, so every open goes through
    :mod:`acb_skills.safe_open`, and a link at any depth fails the write.
    Without *overwrite*, an existing file keeps its bytes, and the document
    takes ``name (n).ext`` with one exclusive create. With *reuse_same*, a
    file that already holds these exact bytes is taken as it is.
    """
    from pathlib import PurePosixPath

    from acb_skills import safe_open

    if overwrite:
        existed = safe_open.is_file(root, rel)
        safe_open.write_bytes(root, rel, data)
        return rel, existed, True
    p = PurePosixPath(rel)
    for counter in range(1000):
        name = p.name if counter == 0 else f"{p.stem} ({counter}){p.suffix}"
        candidate = str(p.with_name(name))
        if reuse_same and _holds_same(root, candidate, data):
            return candidate, True, False
        try:
            safe_open.write_bytes(root, candidate, data, exclusive=True)
        except FileExistsError:
            continue
        return candidate, False, True
    raise FileExistsError(rel)


def _holds_same(root: Path, rel: str, data: bytes) -> bool:
    """True when ``root/rel`` holds exactly *data*. The size comes first, so a
    large file of the chat with the same name is never read (fix round 1)."""
    from acb_skills import safe_open

    st = safe_open.stat_file(root, rel)
    if st is None or st.st_size != len(data):
        return False
    return safe_open.read_bytes(root, rel, limit=len(data)) == data


@contextlib.asynccontextmanager
async def _chat_dir_guard(root: Path) -> AsyncIterator[bool]:
    """The broker's dir lock of the chat's working dir, while a delegated run
    writes there (fix round 1). Yields True when a container on the dir is
    over its quota. With no broker installed, there is no container."""
    try:
        from orchestrator.sandbox_broker import delivery_guard
    except ImportError:
        yield False
        return
    async with delivery_guard(root) as over_quota:
        yield over_quota


async def _deliver(
    ctx: Mapping[str, Any], dest: Mapping[str, Any], path: str, data: bytes,
    *, overwrite: bool, reuse_same: bool = False,
) -> dict:
    """Write a delegated run's document into the chat that called it.

    Returns ``{"path", "size", "sha256", "download_url"}``, or ``{"error"}``.
    The blob row, the card and the link all name the chat's working dir and
    session (*dest*), so the card opens in that chat.
    """
    import asyncio

    from acb_skills import safe_open

    placed = _delivery_path(dest, path, member=ctx.get("member"))
    if isinstance(placed, dict):
        return placed
    root, rel = placed
    try:
        # The same lock as the chat's own file tools and every container on
        # its dir, and the same quota rule (TenantFileStore.write).
        async with _chat_dir_guard(root) as over_quota:
            if over_quota:
                return {"error": (
                    "The working dir of the chat is over its quota, so nothing "
                    "was written."
                )}
            rel, existed, changed = await asyncio.to_thread(
                _write_delivered, root, rel, data,
                overwrite=overwrite, reuse_same=reuse_same,
            )
    except safe_open.UnsafePath:
        return {"error": f"Path '{path}' was refused: it has a link in it."}
    except (FileExistsError, IsADirectoryError, NotADirectoryError):
        return {"error": f"No free name near '{path}', so nothing was written."}
    except OSError as exc:
        return {"error": f"Path '{path}' could not be written: {exc.strerror or exc}."}
    mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if changed:
        asyncio.ensure_future(mirror_to_blob_store(
            rel, data, mime_type=mime,
            action="modify" if existed else "create", target=dest,
        ))
    session_id = str(dest["session_id"])
    asyncio.ensure_future(_notify(
        session_id=session_id,
        workspace_root=str(dest["workspace_root"]),
        artifact={
            "path": rel, "name": rel.rsplit("/", 1)[-1], "size": len(data),
            "sha256": _sha256(data), "mime_type": mime,
            "modified_at": datetime.now(tz=UTC).isoformat(), "is_dir": False,
        },
        gateway_url=ctx.get("gateway_url", "http://127.0.0.1:8000"),
        gateway_token=ctx.get("gateway_token", "sk-local-dev-change-me"),
    ))
    return {
        "path": rel, "size": len(data), "sha256": _sha256(data), "mime_type": mime,
        "download_url": f"/api/agent/workspace/{session_id}/file?path={rel}",
    }


#: Told to a delegated run, so it hands the link on and does not look for
#: the file in its own working dir.
_DELIVERED_NOTE = (
    "This file is in the chat that called you, not in your own workspace. "
    "Give that chat the download_url."
)


def _thread_scoped(rel: str, ctx: Mapping[str, Any]) -> str | None:
    """*rel* in the thread's own folder, for a run in a shared agent's tenant dir.

    H-227 (D12). The run's store key ``o:<org>`` marks the tenant dir, and
    the run's session id names the thread. Both come from the run's
    artifact context, never from the model. ``inputs/x`` and ``outputs/x`` go
    to ``inputs/<thread slug>/x`` and ``outputs/<thread slug>/x``
    (``agent_paths.thread_scoped_rel``, over the one slug rule). Any other
    workspace and any other folder keep *rel*. ``None`` means a tenant dir
    with no thread folder, and the caller writes and shows nothing.
    """
    from acb_skills.agent_paths import is_tenant_instance, thread_scoped_rel

    if not is_tenant_instance(ctx.get("instance")):
        return rel
    try:
        return thread_scoped_rel(rel, ctx.get("session_id"))
    except ValueError:
        return None


def resolve_in_workspace(root: str | Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, returning it ONLY if it stays inside the root.

    The single path-containment guard for every workspace read/write tool
    (``write_artifact``, ``save_note``, ``recall_notes``, …). Returns ``None`` on
    any traversal escape — an embedded ``..`` that climbs out, or an absolute
    path that resolves outside the workspace — so callers fail closed instead of
    reading/writing arbitrary files. Symlinks are resolved on both sides so a
    symlinked escape is caught too.
    """
    root_r = Path(root).resolve()
    # strict=False (the default): non-existent leaves still resolve lexically,
    # so a not-yet-created target is contained-checked correctly.
    target = (root_r / rel).resolve()
    try:
        target.relative_to(root_r)
    except ValueError:
        return None
    return target


async def write_artifact(
    path: str,
    content: str | bytes,
    *,
    encoding: str | None = "utf-8",
    overwrite: bool = False,
) -> dict:
    """Write a file to the agent's workspace and surface it in the UI file browser.

    Call this any time you generate a document, report, script, spreadsheet,
    PDF, image, or any other file that the operator should be able to view or
    download from the Control Plane.

    Files are automatically placed in ``outputs/`` unless you specify
    ``inputs/`` (user-provided files) or ``agent-data/`` (reference data).
    In a chat of a shared agent, ``outputs/`` and ``inputs/`` are that chat's
    own folders, so the returned ``path`` shows where the file went.

    After calling this, **embed the returned ``download_url`` in your text
    response** so the operator can click to download.

    Args:
        path:     Relative file path, e.g. ``"summary.md"`` or
                  ``"reports/q2_summary.md"``.  If the path does not start
                  with ``inputs/``, ``outputs/``, or ``agent-data/``, it is
                  automatically placed in ``outputs/``.
                  Parent directories are created automatically.
        content:  File content — either a ``str`` (written with *encoding*)
                  or ``bytes`` (written as-is; set *encoding* to ``None``).
        encoding: Text encoding for ``str`` content.  Default ``"utf-8"``.
                  Pass ``None`` when *content* is already ``bytes``.
        overwrite: By default (``False``) an existing file is **never**
                  clobbered — the new file is written to a uniquified name
                  (``report (1).md``) so originals/user uploads are preserved.
                  Set ``True`` to deliberately replace the file in place.

    Returns:
        ``{"path": str, "size": int, "sha256": str, "download_url": str}``

        ``path``/``download_url`` reflect the file *actually* written (which may
        be a uniquified name if a file already existed and ``overwrite`` is off).
        *download_url* is a relative URL suitable for a clickable markdown
        link, e.g. ``/api/agent/workspace/{session}/file?path=outputs/x.md``.
    """
    import asyncio

    ctx = artifact_context()
    workspace_root = ctx.get("workspace_root")
    session_id = ctx.get("session_id")
    gateway_url = ctx.get("gateway_url", "http://127.0.0.1:8000")
    gateway_token = ctx.get("gateway_token", "sk-local-dev-change-me")

    if not workspace_root:
        # H-201: fail closed. A frame with no run context writes nothing and
        # emits nothing. The temp-dir fallback that was here put the file
        # outside every tenant dir, keyed by a session id it could not know.
        return {"error": "No workspace is configured for this run, so nothing was written."}

    # A delegated run delivers its document to the chat that called it, so
    # the card opens there (``delegation_target``). ``agent-data/`` is the
    # sub-agent's own memory: it stays in its own working dir, with no card,
    # because no chat can open that dir.
    dest, delegated = _destination(ctx)
    if delegated and _normalise_path(path).split("/", 1)[0] != "agent-data":
        return await _write_delegated(ctx, dest, path, content, encoding, overwrite)
    show = not delegated

    root = Path(workspace_root)
    root_r = root.resolve()
    # Normalise path and auto-prefix with outputs/ if needed
    clean_path = _normalise_path(path)
    # Containment guard: refuse any path that escapes the workspace (embedded
    # ``..`` or an absolute path resolving outside root). Fail closed.
    target = resolve_in_workspace(root, clean_path)
    if target is None:
        return {"error": f"Path '{path}' escapes the workspace and was refused."}
    clean_path = target.relative_to(root_r).as_posix()
    # H-227 (D12): a shared agent's tenant dir is one folder for every member
    # of the organization. So a document of this chat goes to the chat's own
    # folder, outputs/<thread slug>/ (inputs/<thread slug>/ for inputs/), and
    # its card links there. Only a session of this thread reads it. A batch
    # run (no chat) writes the flat folder, and its document belongs to the
    # organization (`_is_batch_run`).
    batch = _is_batch_run(ctx)
    if not batch:
        scoped = _chat_document_path(clean_path, path, ctx)
        if isinstance(scoped, dict):
            return scoped
        if scoped != clean_path:
            clean_path, target = scoped, root_r / scoped
    # WS-43d (review P1, fix round 1): never another chat's output folder,
    # another member's skill folder, or the skill author marker.
    from acb_skills.agent_paths import refused_write

    reason = refused_write(
        root_r, clean_path, member=ctx.get("member"), thread_id=session_id,
    )
    if reason:
        return {"error": f"Path '{path}' was refused: {reason}."}

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    # Non-destructive by default: never clobber an existing file (a user upload
    # in inputs/, or a previously generated artifact). Uniquify to "name (1).ext"
    # — the same collision policy the upload endpoint uses. Pass overwrite=True to
    # deliberately replace the file in place.
    if not batch and target.exists() and not overwrite:
        stem, ext = target.stem, target.suffix
        counter = 1
        while target.exists():
            target = target.parent / f"{stem} ({counter}){ext}"
            counter += 1
        clean_path = target.relative_to(root_r).as_posix()

    # Write file
    if isinstance(content, str):
        data = content.encode(encoding or "utf-8")
    else:
        data = bytes(content)

    if batch:
        # A batch run never replaces a file in a tenant dir: that file may
        # belong to a member's chat (H-227). The name is free only when the
        # disk AND the history hold nothing there, and one exclusive create
        # takes it (fix round 2).
        try:
            clean_path = await _take_batch_name(root_r, clean_path, data, ctx)
        except FileExistsError:
            return {"error": f"No free name near '{path}', so nothing was written."}
        target = root_r / clean_path
        _existed = False
    else:
        _existed = target.exists()
        target.write_bytes(data)
    digest = _sha256(data)
    size = len(data)

    mime, _ = mimetypes.guess_type(target.name)
    mime = mime or "application/octet-stream"

    # Write-through to the authoritative blob store (agent-data/inputs/outputs).
    # Fire-and-forget: the disk file is already written, so a store outage never
    # blocks the agent.
    import asyncio as _asyncio
    _asyncio.ensure_future(mirror_to_blob_store(
        clean_path, data, mime_type=mime,
        action="modify" if (_existed and overwrite) else "create",
        # H-227: "batch" marks a document of a run with no chat. Only the
        # server sets it, and the session routes let every member read it.
        actor=BATCH_ACTOR if batch else "agent",
    ))

    # Build download URL (relative path — works from the frontend chat UI).
    download_url = (
        f"/api/agent/workspace/{session_id}/file"
        f"?path={clean_path}"
    ) if session_id and show else None

    # Build artifact entry
    artifact = {
        "path": clean_path,
        "name": target.name,
        "size": size,
        "sha256": digest,
        "mime_type": mime,
        "modified_at": datetime.now(tz=UTC).isoformat(),
        "is_dir": False,
    }

    # Fire-and-forget: emit AG-UI CUSTOM event into the active SSE stream
    # (via _active_run_queue context var set by the executor) and also
    # register the workspace path on the session via the gateway.
    if show:
        asyncio.ensure_future(_notify(
            session_id=session_id,
            workspace_root=workspace_root,
            artifact=artifact,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
        ))

    result: dict = {"path": clean_path, "size": size, "sha256": digest}
    if download_url:
        result["download_url"] = download_url
    result.update(_lint_fields(target.suffix, content))
    return result


async def _write_delegated(
    ctx: Mapping[str, Any], dest: Mapping[str, Any], path: str,
    content: str | bytes, encoding: str | None, overwrite: bool,
) -> dict:
    """:func:`write_artifact` of a delegated run: the document goes to the
    chat that called it (:func:`_deliver`)."""
    data = content.encode(encoding or "utf-8") if isinstance(content, str) else bytes(content)
    out = await _deliver(ctx, dest, path, data, overwrite=overwrite)
    if "error" not in out:
        out.pop("mime_type", None)
        out["note"] = _DELIVERED_NOTE
        out.update(_lint_fields(Path(out["path"]).suffix, content))
    return out


def _lint_fields(suffix: str, content: str | bytes) -> dict:
    """Advisory lint for an HTML or React document, as result fields.

    The sandbox fails silently (a CDN link is just blocked, a typo'd cc- class
    just renders unstyled), so surface those mistakes while the agent can
    still fix them. Never blocks the write. Empty when there is nothing to say.
    """
    suffix = suffix.lower()
    if suffix not in {".html", ".htm", ".jsx", ".tsx"} or not isinstance(content, str):
        return {}
    from acb_skills.artifact_lint import (
        lint_artifact_html,
        lint_artifact_source,
    )

    # JSX is not HTML — running the document linter on it yields only noise.
    # But the CSP failure is shared, and it is silent, so React artifacts get
    # the narrow remote-asset check.
    warnings = (
        lint_artifact_html(content, full_page=True)
        if suffix in {".html", ".htm"}
        else lint_artifact_source(content)
    )
    if not warnings:
        return {}
    return {
        "warnings": warnings,
        "warning_note": (
            "The artifact was saved, but these issues will degrade how it "
            "renders. Fix them and write the file again with overwrite=True."
        ),
    }


def _share_target(path: str, root: Path, ctx: Mapping[str, Any]) -> Path | str:
    """The existing file or dir that *path* names in *root*, or why there is none.

    The target must stay inside the workspace root. H-227 (D12): in a shared
    agent's tenant dir, ``inputs/`` and ``outputs/`` name this chat's own
    folders, as they do for :func:`write_artifact`.
    """
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return "A file or directory path is required."
    candidate = Path(raw)
    target = (candidate if candidate.is_absolute() else root / raw).resolve()
    if not target.is_relative_to(root):
        return f"Path '{path}' is outside the workspace."
    scoped = _thread_scoped(target.relative_to(root).as_posix(), ctx)
    if scoped is None:
        return "This chat has no thread folder, so nothing was shared."
    target = (root / scoped).resolve()
    if not target.is_relative_to(root):
        return f"Path '{path}' is outside the workspace."
    if not target.exists():
        return f"File not found: {path}"
    return target


def _shareable(p: Path, root: Path, not_this_chats: Callable[[str], bool]) -> Path | None:
    """The real path of *p*, when a directory share may show it, else ``None``.

    PR #616 review, P3: the check reads the RESOLVED path, the one that the
    card shows, and never the name of a link. A link is skipped outright. So
    a link that a run plants in its own folder never shows the name or the
    size of a file of another chat.
    """
    if p.is_symlink() or not p.is_file():
        return None
    try:
        real = p.resolve()
        rel = real.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if not_this_chats(rel):
        return None
    return real


def _not_this_chats(ctx: Mapping[str, Any]) -> Callable[[str], bool]:
    """A check: True for a working-dir path that this run may not show.

    WS-43d (review P1, fix round 1): another chat's folder. H-227: in a
    shared agent's tenant dir, also a loose file, one that lies in
    ``inputs/`` or ``outputs/`` but in no thread folder.
    """
    from acb_skills.agent_paths import (
        instance_slug,
        is_loose_rel,
        is_other_thread_rel,
        is_tenant_instance,
    )

    session_id = ctx.get("session_id")
    own = instance_slug(str(session_id)) if session_id else None
    tenant_dir = is_tenant_instance(ctx.get("instance"))

    def check(rel: str) -> bool:
        return is_other_thread_rel(rel, own) or (tenant_dir and is_loose_rel(rel))

    return check


async def share_artifact(path: str) -> dict:
    """Surface a file you ALREADY created as a downloadable, previewable card in
    the chat — and get back a download link.

    Use this whenever you produced a file with your own tools (shell, editor,
    Write, a script you ran) instead of ``write_artifact``.  Do NOT re-create or
    re-read the file's contents — just point this tool at the path you already
    wrote and it will appear in the chat with a Download button and an inline
    preview, with zero extra effort on your part.  You do not need to construct
    any URL by hand; the returned ``download_url`` is the canonical link.

    Pass a single file, or a directory to share every file inside it.

    Args:
        path: File or directory path relative to your workspace (e.g.
              ``"outputs/report.pdf"``, ``"q2_summary.xlsx"``, or ``"outputs"``
              to share the whole folder).  Absolute paths inside the workspace
              are also accepted.

    Returns:
        ``{"artifacts": [{"path","name","size","mime_type","download_url"}, ...],
        "download_url": <first file's link>}``.  On error,
        ``{"error": str, "artifacts": []}``.
    """
    import asyncio

    ctx = artifact_context()
    workspace_root = ctx.get("workspace_root")
    session_id = ctx.get("session_id")
    gateway_url = ctx.get("gateway_url", "http://127.0.0.1:8000")
    gateway_token = ctx.get("gateway_token", "sk-local-dev-change-me")

    if not workspace_root:
        return {"error": "No workspace is configured for this run.", "artifacts": []}

    root = Path(workspace_root).resolve()
    target = _share_target(path, root, ctx)
    if isinstance(target, str):
        return {"error": target, "artifacts": []}
    not_this_chats = _not_this_chats(ctx)
    if not_this_chats(target.relative_to(root).as_posix()):
        return {"error": f"Path '{path}' belongs to another chat.", "artifacts": []}

    # Collect the file(s) to share (a directory shares everything within it).
    files: list[Path] = []
    if target.is_dir():
        for p in sorted(target.rglob("*")):
            shared = _shareable(p, root, not_this_chats)
            if shared is not None:
                files.append(shared)
                if len(files) >= 50:
                    break
    else:
        files = [target]
    if not files:
        return {"error": f"No files found at: {path}", "artifacts": []}

    # A delegated run shares a copy in the chat that called it, so the card
    # opens there (``delegation_target``). The file stays in its own dir too.
    dest, delegated = _destination(ctx)
    if delegated:
        return await _share_delivered(ctx, dest, root, files)

    artifacts: list[dict] = []
    for f in files:
        rel = f.resolve().relative_to(root).as_posix()
        size = f.stat().st_size
        mime, _ = mimetypes.guess_type(f.name)
        mime = mime or "application/octet-stream"
        download_url = (
            f"/api/agent/workspace/{session_id}/file?path={rel}"
            if session_id else None
        )
        art = {
            "path": rel,
            "name": f.name,
            "size": size,
            "mime_type": mime,
            "modified_at": datetime.now(tz=UTC).isoformat(),
            "is_dir": False,
        }
        # Same CUSTOM event write_artifact emits → renders the ArtifactCard.
        asyncio.ensure_future(_notify(
            session_id=session_id,
            workspace_root=workspace_root,
            artifact=art,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
        ))
        entry: dict = {"path": rel, "name": f.name, "size": size, "mime_type": mime}
        if download_url:
            entry["download_url"] = download_url
        artifacts.append(entry)

    result: dict = {"artifacts": artifacts}
    if artifacts and artifacts[0].get("download_url"):
        result["download_url"] = artifacts[0]["download_url"]
    return result


def _delivered_rel(rel: str) -> str | None:
    """The ``outputs/`` path that a delegated share gives a file of its own
    dir, or ``None`` when the file may not leave that dir.

    Only a file in the sub-agent's own ``outputs/`` may go (fix round 1). Its
    ``agent-data/`` is its memory, and in a room every member of the chat can
    read what the chat holds. A name that starts with a dot is refused too. A
    file keeps its path below ``outputs/``, less the sub-agent's own thread
    folder.
    """
    from acb_skills.agent_paths import is_thread_slug

    parts = [p for p in rel.split("/") if p]
    if len(parts) < 2 or parts[0] != "outputs" or any(p.startswith(".") for p in parts):
        return None
    rest = parts[1:]
    if len(rest) > 1 and is_thread_slug(rest[0]):
        rest = rest[1:]
    return "/".join(["outputs", *rest])


async def _share_delivered(
    ctx: Mapping[str, Any], dest: Mapping[str, Any], root: Path, files: list[Path],
) -> dict:
    """Copy each of *files* (in the delegated run's own dir *root*) into the
    chat that called it, and show each copy as a card there.

    A copy that already holds the same bytes is shown again, never copied
    twice. A file of the chat with the same name and other bytes keeps them,
    and the copy takes ``name (n).ext``.
    """
    import asyncio

    from acb_skills import safe_open

    artifacts: list[dict] = []
    errors: list[str] = []
    for f in files:
        own = f.resolve().relative_to(root).as_posix()
        to = _delivered_rel(own)
        if to is None:
            errors.append(
                f"{own}: an agent that another chat called shares only the files "
                "of its own outputs/"
            )
            continue
        try:
            data = await asyncio.to_thread(safe_open.read_bytes, root, own)
        except (safe_open.UnsafePath, OSError):
            data = None
        if data is None:
            errors.append(f"{own}: not a regular file")
            continue
        out = await _deliver(
            ctx, dest, to, data, overwrite=False, reuse_same=True,
        )
        if "error" in out:
            errors.append(f"{own}: {out['error']}")
            continue
        artifacts.append({
            "path": out["path"], "name": out["path"].rsplit("/", 1)[-1],
            "size": out["size"], "mime_type": out["mime_type"],
            "download_url": out["download_url"],
        })
    result: dict = {"artifacts": artifacts}
    if artifacts:
        result["download_url"] = artifacts[0]["download_url"]
        result["note"] = _DELIVERED_NOTE
    if errors:
        result["error"] = "; ".join(errors[:5])
    return result


async def _notify(
    *,
    session_id: str | None,
    workspace_root: str,
    artifact: dict,
    gateway_url: str,
    gateway_token: str,
) -> None:
    """Background task: push CUSTOM SSE event into the active run queue AND
    register workspace on the gateway.  Non-fatal — all errors are swallowed.
    """
    # 1. Push AG-UI CUSTOM event into the active executor SSE queue so the
    #    frontend receives it immediately as part of the existing chat stream.
    #    resolve_run_queue falls back to the plain _RUN_QUEUES registry, keyed
    #    by THIS run's session_id. It never guesses another run's queue.
    try:
        from orchestrator.executor import resolve_run_queue
        queue = resolve_run_queue(session_id)
        if queue is not None:
            _artifact_data = {
                "path": artifact["path"],
                "sha256": artifact.get("sha256"),
                "size": artifact.get("size"),
                "mime_type": artifact.get("mime_type"),
            }
            await queue.put({
                "type": "CUSTOM",
                "name": "artifact_created",
                "value": _artifact_data,
            })
    except Exception:
        pass

    if not session_id:
        return

    # 2. Also POST to gateway events endpoint so any other SSE subscribers
    #    (future browser tabs, monitoring) receive it.
    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=5) as client:
            # Register workspace root on the session (idempotent PATCH)
            await client.patch(
                f"{gateway_url}/agent/workspace/{session_id}",
                json={"workspace_path": workspace_root},
                headers=headers,
            )
            # Emit to gateway subscriber queues
            await client.post(
                f"{gateway_url}/agent/workspace/{session_id}/events",
                json={
                    "name": "artifact_created",
                    "path": artifact["path"],
                    "sha256": artifact.get("sha256"),
                    "size": artifact.get("size"),
                },
                headers=headers,
            )
    except Exception:
        pass  # Non-fatal — the sidebar can always refresh manually


# ── Generative UI ───────────────────────────────────────────────────────────

#: The node kinds the renderer draws: ``KNOWN_TYPES`` in
#: ``GenerativeUINode.tsx``. ``emit_generative_ui`` refuses any other kind
#: (``genui_refusal``), so the model can retry. Until 2026-10-07 this set
#: existed and nothing read it, and a ``tree`` node reached a member as
#: "unsupported UI element: tree". Fence: ``test_genui_catalog_lockstep.py``.
GENUI_NODE_TYPES: frozenset[str] = frozenset({
    "card", "stack", "row", "heading", "text", "markdown", "badge",
    "divider", "keyValue", "table", "list", "code", "link", "button", "callout",
    "template", "html", "react", "icon",
})

#: The template names the renderer draws: ``TEMPLATE_CATALOG`` in
#: ``genUITemplates.tsx``. Same fence.
GENUI_TEMPLATES: frozenset[str] = frozenset({
    "weatherCard", "statDashboard", "barChart", "sparkTrend", "comparison",
    "progressTracker", "recipeCard", "flightStatus", "trainStatus", "formCard",
    "optionPicker", "timeline", "taskBoard", "dataGrid", "reportCard",
    "planCard",
})

#: The renderer drops a node deeper than this (``Node``'s depth guard).
_GENUI_MAX_DEPTH = 20


def genui_refusal(spec: dict) -> str | None:
    """Why the renderer cannot draw ``spec``, or ``None`` when it can.

    The check costs no schema tokens: ``ui`` is one JSON string, so the
    schema cannot hold an enum, and the docstring has a ceiling in
    ``test_tool_schema_diet.py``. So the refusal names the allowed kinds,
    and the model reads them in the tool result and emits again.

    It walks the tree the way the renderer does: ``root`` or ``view`` when
    present, then ``children``. A child that is not an object, or a node
    below the depth guard, draws nothing and is not refused.
    """
    root = spec.get("root")
    if root is None:
        root = spec.get("view")
    if root is None:
        root = spec

    def walk(node: object, where: str, depth: int) -> str | None:
        if depth > _GENUI_MAX_DEPTH or not isinstance(node, dict):
            return None
        kind = node.get("type")
        # A list or an object is unhashable, and the set lookup would raise.
        if not isinstance(kind, str) or kind not in GENUI_NODE_TYPES:
            return (
                f"{where}: the renderer has no {kind!r} type. Use one of: "
                f"{', '.join(sorted(GENUI_NODE_TYPES))}. For a hierarchy, use "
                "a markdown node with a nested list."
            )
        if kind == "template":
            props = node.get("props")
            name = props.get("name") if isinstance(props, dict) else None
            if not isinstance(name, str) or name not in GENUI_TEMPLATES:
                return (
                    f"{where}: the renderer has no {name!r} template. Use one "
                    f"of: {', '.join(sorted(GENUI_TEMPLATES))}."
                )
        kids = node.get("children")
        if isinstance(kids, list):
            for i, kid in enumerate(kids):
                found = walk(kid, f"{where}.children[{i}]", depth + 1)
                if found:
                    return found
        return None

    if not isinstance(root, dict):
        return "ui: the root must be a JSON object (a component node)."
    return walk(root, "ui", 0)


#: The data shape of each template, the copy that the model reads. Each value
#: is the ``data`` string of ``TEMPLATE_CATALOG`` in ``genUITemplates.tsx``,
#: which is the source of truth. ``emit_generative_ui`` returns these on
#: demand (:func:`genui_guide`), so they cost no schema tokens. Until
#: 2026-10-09 they sat in the tool's docstring, in every model request of
#: every run. Fence: ``test_genui_catalog_lockstep.py`` holds this map equal
#: to the catalog, name for name and shape for shape.
GENUI_TEMPLATE_SHAPES: Mapping[str, str] = MappingProxyType({
    "weatherCard": "{ location, tempC|tempF, condition('sunny'|'cloudy'|'rain'|'snow'|'storm'), highC?, lowC?, humidity?, wind?, forecast?:[{day,condition,high,low}] }",
    "statDashboard": "{ title?, stats:[{ label, value, unit?, delta?:number, icon?(Lucide name e.g. 'trending-up') }] }",
    "barChart": "{ title?, unit?, bars:[{ label, value, tone?('primary'|'success'|'warning'|'danger') }] }",
    "sparkTrend": "{ label, value, unit?, delta?:number, series:number[] }",
    "comparison": "{ title?, options:[{ name, recommended?:bool, rows:[{ label, value }] }] }",
    "progressTracker": "{ title?, steps:[{ label, state('done'|'active'|'pending') }] }",
    "recipeCard": "{ title, description?, servings?, prepMinutes?, cookMinutes?, calories?, ingredients:[{item,amount?}], steps:[string], tags?:[string], tip? }",
    "flightStatus": "{ airline?, flightNo, status('scheduled'|'boarding'|'departed'|'in-air'|'landed'|'delayed'|'cancelled'), from:{code,city?,time?,terminal?,gate?}, to:{code,city?,time?,terminal?,gate?}, progressPct?, durationMin?, date?, note? }",
    "trainStatus": "{ operator?, trainNo?, line?, status('scheduled'|'boarding'|'departed'|'arrived'|'delayed'|'cancelled'), from:{station,time?,platform?}, to:{station,time?,platform?}, stops?:[{station,time?,state?('done'|'active'|'pending')}], delayMin?, note? }",
    "formCard": "{ title?, description?, submitLabel?, fields:[{ name, label, type('text'|'number'|'select'|'slider'|'toggle'|'checkbox'|'date'|'textarea'), placeholder?, value?, required?, options?:[string], min?, max?, step?, unit?, hint? }] }",
    "optionPicker": "{ title?, description?, multi?:bool, options:[{ id, label, description?, icon?, badge?, recommended?:bool }] }",
    "timeline": "{ title?, taskId?, total?, rows:[{ id?, at, type, actor, body?, field?, before?, after?, via? }] }",
    "taskBoard": "{ title?, total?, columns:[{ id?, name, category?, tasks:[{ id, number?, title, assignees?:[string], due?, due_at?, importance?, leveraged?:bool, done?:bool }] }] }",
    "dataGrid": "{ title?, columns:[string], rows:[{ id?, cells:[string|number] }], openBase? }",
    "reportCard": "{ title, period?, reportId?, stats?:[{label,value,unit?,icon?}], tables?:[{ title, columns:[string], rows:[{cells:[string|number]}] }] }",
    "planCard": "{ title?, description?, submitLabel?, project:{ name, parent?, description? }, tasks:[{ key, title, owner, effort_mins, start?, due, after?:[key], important?:bool, leveraged?:bool, impact?, urgency?, effort?, priority?, fit?, hours?, marks?:[string], warnings?:[string] }], capacity?, warnings?:[string], risks?:[string] }",
})

#: The templates that collect the member's input. Each one pairs with
#: ``"hitl": true``.
_GENUI_ASKS = frozenset({"formCard", "optionPicker", "planCard"})

#: The rules of the two code modes, returned on demand (:func:`genui_guide`).
#: Moved from the tool's docstring on 2026-10-09, word for word.
GENUI_MODE_GUIDES: Mapping[str, str] = MappingProxyType({
    "react": """REACT COMPONENT — a real React component for anything genuinely
INTERACTIVE or stateful: multi-step forms, filterable/sortable tables,
calculators, live-editable dashboards, small tools. Shape:
{"type":"react","props":{"code":"<your component source>"}}.

Write ordinary modern React and DEFAULT-EXPORT the component
(export default function Dashboard() { … }).

• Hooks all work (useState/useEffect/useMemo/useReducer/useRef/context).
• JSX and TypeScript syntax are both fine — it is compiled for you.
• PREFER the prebuilt components: import { Report, Stat, Bars } from
  "@cc/ui". If you hold load_artifact_kit, call load_artifact_kit() for the
  list and load_artifact_kit("Stat,Bars") for their props.
• You may import ONLY from @cc/ui, react, and react-dom/client. There is NO
  network in the sandbox, so no npm packages, no CDNs, no icon libraries.
  Inline any helpers and seed the data in the file.
• Anything the kit doesn't cover: fall back to the same cc-* classes and
  --cc-* tokens as custom HTML.
• Talk back to the agent with window.ccSubmit("Label", value) (send a value
  the user set) or window.ccAction("message") (fire a fixed follow-up). Both
  are available from first mount.
• Optional props.height (px); omit to auto-size.

If it compiles but the build fails, the tool result carries the compiler
errors — fix them and emit again.""",
    "html": """CUSTOM HTML — the escape hatch for bespoke animation/layout or genuinely
interactive controls no template, tree, or React component covers. Shape:
{"type":"html","props":{"code":"<div>…</div>"}}. Your HTML/CSS/JS runs in an
ISOLATED sandbox (its own opaque origin): it cannot reach the app, cookies,
or the network, so inline everything — NO external CDNs, fonts, or images
(use data: URIs). Optional props.height (px); omit to auto-size.

DESIGN — follow the Metorite look. The frame pre-defines CSS variables from
the app's real design tokens; USE THEM instead of hard-coding colors:
  --cc-primary (blue) · --cc-accent (warm orange) · --cc-fg · --cc-muted
  · --cc-card · --cc-secondary · --cc-border · --cc-success · --cc-warning
  · --cc-danger · --cc-radius (0.75rem) · --cc-ease (motion curve).
Native <button>, <input>, <select>, <textarea> and input[type=range] are
already styled on-brand (add class cc-primary to a button for the filled
blue variant; cc-card for a panel). Prefer rem spacing, rounded corners
(var(--cc-radius)), and subtle transitions (0.2s var(--cc-ease)).

REPORT DESIGN KIT — for a substantial DOCUMENT (analysis, plan, comparison,
briefing) prefer writing it to an .html file with write_artifact so it opens
full-page in the side panel, wrapped in <div class="cc-report">. If you hold
load_design_system, call load_design_system("blocks") for the pre-styled
block reference.

INTERACTIVITY — two channels back to the agent:
• data-cc-action="<message>" on a clickable element (or ccAction("…") in
  script) fires a FIXED follow-up message — like a button.
• data-cc-submit="<label>" on a button harvests every named control (input,
  select, textarea) in its enclosing <form> or [data-cc-form] and submits
  their VALUES back as the user's next message. Or call
  ccSubmit("Temperature", 22) / ccSubmit({temp:22,unit:"C"}) directly. Use
  this whenever the user SETS a value, so the agent receives what they
  chose.""",
})


def genui_catalog(name: str | None = None) -> str:
    """The data shape of template *name*, or of each template.

    One line per template: ``name — shape``. A template that collects the
    member's input says that it pairs with ``"hitl": true``. A name that is
    not a template returns every line.
    """
    names = [name] if name in GENUI_TEMPLATE_SHAPES else list(GENUI_TEMPLATE_SHAPES)
    lines = []
    for n in names:
        tail = " — pair it with top-level \"hitl\": true." if n in _GENUI_ASKS else ""
        lines.append(f"• {n} — {GENUI_TEMPLATE_SHAPES[n]}{tail}")
    return "\n".join(lines)


def _empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def genui_guide(spec: dict) -> dict | None:
    """The guide that *spec* asks for, or ``None`` when it is a card to draw.

    The model reads the catalog on demand, not from the tool's schema. Three
    shapes of the ROOT node ask, and nothing is drawn for any of them:

    * a template with an empty or unknown name: the shape of each template;
    * a known template with no ``data`` key, or ``data: null``: its shape.
      An empty ``{}`` is data and draws as it always did;
    * a ``react`` or ``html`` node with no ``code``: the rules of that mode.

    The answer is ``ok: False``, so the model never reads it as a drawn card.
    A child node is never a request, and :func:`genui_refusal` judges it.
    """
    root = spec.get("root")
    if root is None:
        root = spec.get("view")
    if root is None:
        root = spec
    if not isinstance(root, dict):
        return None
    kind = root.get("type")
    props = root.get("props") if isinstance(root.get("props"), dict) else {}
    if kind == "template":
        name = props.get("name")
        if _empty(name) or (isinstance(name, str) and name not in GENUI_TEMPLATES):
            return {
                "ok": False,
                "drawn": False,
                "error": (
                    f"Nothing was drawn: the renderer has no {name!r} template. "
                    f"Use one of: {', '.join(sorted(GENUI_TEMPLATES))}. Send "
                    "its data in props.data, in the shape that `templates` gives."
                ),
                "templates": genui_catalog(),
            }
        if isinstance(name, str) and props.get("data") is None:
            return {
                "ok": False,
                "drawn": False,
                "error": (
                    f"Nothing was drawn. Here is the data shape of {name}. "
                    "Call again with props.data in this shape."
                ),
                "shape": genui_catalog(name),
            }
        return None
    # A list or an object is unhashable, and genui_refusal answers it.
    # The renderer draws ``props.code``, or ``props.html`` for an html node
    # (``GenerativeUINode.tsx``), so either one is code.
    code = props.get("code")
    if code is None and kind == "html":
        code = props.get("html")
    if isinstance(kind, str) and kind in GENUI_MODE_GUIDES and _empty(code):
        return {
            "ok": False,
            "drawn": False,
            "error": (
                f"Nothing was drawn. Here are the rules of the {kind} mode. "
                "Call again with props.code."
            ),
            "guide": GENUI_MODE_GUIDES[kind],
        }
    return None


def _warn_fields(warnings: list[str]) -> dict:
    """Lint warnings as result fields — empty dict when the markup is clean."""
    if not warnings:
        return {}
    return {
        "warnings": warnings,
        "warning_note": (
            "The card was rendered, but these issues will degrade how it looks. "
            "Fix them and emit it again."
        ),
    }


async def emit_generative_ui(ui: str) -> dict:
    """Draw a card in the chat: a template, a component tree, React or HTML.

    Use it when the answer is numbers, a status, a comparison, steps, or a
    choice the member makes. Draw at most ONE answer card, after your text.
    A list of fewer than six items stays a Markdown list. Never draw the
    result of a read again as a card. A one-line fact or a long narrative
    stays text. A card stays in the transcript, so never call it temporary.

    ``ui`` is one JSON object. Two top-level keys are optional.
    ``"surface":"panel"`` opens a big card in the side panel.
    ``"hitl":true`` pauses the run until the member submits, and returns
    their values as this call's result. Use it with formCard, optionPicker
    and planCard. Without it, a click arrives as a new chat message.

    Four modes, in this order of preference:

    1. TEMPLATE — ``{"type":"template","props":{"name":<t>,"data":{...}}}``.
       You give the data, and the design is fixed. Names: weatherCard,
       statDashboard, barChart, sparkTrend, comparison, progressTracker,
       recipeCard, flightStatus, trainStatus, formCard, optionPicker,
       timeline, taskBoard, dataGrid, reportCard, planCard. To get the data
       shape of a template, call with its name and no ``data``. Nothing is
       drawn. An empty or unknown name returns the shape of each template.

    2. COMPONENT TREE — ``{"type":<kind>,"props":{...},"children":[...]}``.
       Kinds: card{title?} · stack · row · heading{text} · text{text,muted?}
       · markdown{text} · badge{text,tone?} · divider ·
       callout{title?,text?,tone?} · keyValue{pairs:[{key,value}]} ·
       table{columns:[string],rows:[[cell,...]]} · list{items,ordered?} ·
       code{text} · link{href,text?} · button{label,action,tone?} ·
       icon{name} (a Lucide name). A button's ``action`` comes back as the
       member's next message.

    3. REACT COMPONENT — ``{"type":"react","props":{"code":...}}``, for an
       interactive or stateful tool.

    4. CUSTOM HTML — ``{"type":"html","props":{"code":...}}``, only when
       nothing above fits.

    For mode 3 or 4, call first with no ``code`` to get its rules. Nothing is
    drawn. Returns ``{"ok": true}`` when the card is drawn. Also say in text
    what the card shows. A ``type`` not named above is refused.
    """
    import json

    try:
        spec = json.loads(ui) if isinstance(ui, str) else ui
    except (json.JSONDecodeError, TypeError) as exc:
        return {"ok": False, "error": f"ui must be valid JSON: {exc}"}
    if not isinstance(spec, dict):
        return {"ok": False, "error": "ui must be a JSON object (a component node)"}
    # The catalog on demand: a template with no data, an unknown or empty
    # template name, or a code mode with no code asks for its guide. Nothing
    # is parked or pushed for it.
    guide = genui_guide(spec)
    if guide is not None:
        return guide
    # Refuse a kind the renderer cannot draw BEFORE anything is parked or
    # pushed, so a refused HITL card never waits for an answer.
    refusal = genui_refusal(spec)
    if refusal:
        return {"ok": False, "error": refusal}

    # Advisory lint for the custom-HTML tier — the sandbox swallows these errors
    # silently, so report them back with the emit result. Inline cards are not
    # expected to carry the cc-report wrapper (that is for full-page documents).
    _ui_warnings: list[str] = []
    if spec.get("type") == "html":
        _code = (spec.get("props") or {}).get("code")
        if isinstance(_code, str):
            from acb_skills.artifact_lint import lint_artifact_html

            _ui_warnings = lint_artifact_html(_code, full_page=False)

    # ── HITL blocking mode (generative_ui_2 Phase 1) ──────────────────────
    # ``"hitl": true`` parks THIS tool call on the same Future machinery as
    # ask_questions: the UI's submit/action resolves it via
    # /agent/respond-input and the run resumes in the SAME turn with the
    # user's values as this tool's result. Without it, genUI submits arrive
    # as a NEW chat message (non-blocking).
    _blocking = bool(spec.pop("hitl", False))
    _request_id: str | None = None
    _fut = None
    if _blocking:
        try:
            import asyncio as _asyncio
            import uuid as _uuid

            from orchestrator.executor import _pending_user_input
            _request_id = _uuid.uuid4().hex
            _fut = _asyncio.get_running_loop().create_future()
            _pending_user_input[_request_id] = _fut
            spec["request_id"] = _request_id
        except Exception:
            _blocking, _request_id, _fut = False, None, None

    # Push the CUSTOM event into the active run's SSE queue. resolve_run_queue
    # tries the ContextVar (native-MAF) first, then the plain _RUN_QUEUES
    # registry keyed by the session id — the latter is what makes this work for
    # GitHub-Copilot-SDK agents, whose tool callables run in a JSON-RPC read
    # thread with a fresh context where the ContextVar is invisible.
    try:
        from orchestrator.executor import resolve_run_queue
        session_id = artifact_context().get("session_id")
        queue = resolve_run_queue(session_id)
        if queue is None:
            if _request_id is not None:
                from orchestrator.executor import _pending_user_input
                _pending_user_input.pop(_request_id, None)
            return {"ok": False, "error": "no active run stream to render into"}
        await queue.put({
            "type": "CUSTOM",
            "name": "generative_ui",
            "value": spec,
        })
        if not _blocking or _fut is None:
            return {"ok": True, **_warn_fields(_ui_warnings)}
        # Park until the user interacts (heartbeats the relay so the run
        # stays visibly alive — same wait as every other HITL surface).
        try:
            from orchestrator.executor import (
                _pending_user_input,
                wait_user_future,
            )
            try:
                _result = await wait_user_future(_fut, 3600)
            finally:
                _pending_user_input.pop(_request_id, None)
            return {
                "ok": True,
                "response": _result.get("answer", ""),
                **_warn_fields(_ui_warnings),
            }
        except Exception:
            return {
                "ok": True,
                "response": None,
                "note": "user did not respond to the UI",
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "no active run stream to render into"}
