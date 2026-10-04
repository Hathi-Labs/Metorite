"""Repo-scoped memory tools — agents persist notes across sessions.

Provides ``save_note`` and ``recall_notes``, inspired by VS Code Copilot's
3-tier memory system (/memories/user, /memories/session, /memories/repo).
These tools let agents maintain a durable, file-based working memory within
the agent workspace that survives session resets and context compaction.

Design
------
- ``save_note(path, fact)`` — Append a dated bullet to a markdown notes file
  under ``agent-data/``.  Creates the file if it doesn't exist.
- ``recall_notes(path, query?)`` — Read back a notes file, optionally
  filtering lines that match a query string.
- Notes files are plain markdown, human-readable, and visible in the
  Control Plane Files sidebar.
- The canonical working-memory file is ``agent-data/NOTES.md`` — agents are
  instructed to read it at session start.

Usage by agents::

    await save_note("NOTES.md", "Closed ABC Corp deal at ₹50L")
    await save_note("leads.md", "New lead: XYZ Ltd, contact Priya")
    history = await recall_notes("NOTES.md")
    leads = await recall_notes("leads.md", "XYZ")
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _get_agent_dir() -> str:
    """Resolve the agent's workspace root."""
    try:
        from acb_skills.write_artifact import artifact_context  # noqa: PLC0415
        return str(artifact_context().get("workspace_root") or "")
    except Exception:  # noqa: BLE001
        return ""


def _notes_target(root: Path, clean: str, path: str) -> tuple[Path, str] | str:
    """The file that a notes path names, and its working-dir path, or a refusal.

    Containment first (``write_artifact.resolve_in_workspace``). Then, in a
    shared agent's tenant dir (the run's store key is ``o:<org>``), H-227
    applies as it does to ``write_artifact`` and ``share_artifact``:

    * ``inputs/x`` and ``outputs/x`` name this chat's own folders
      (``write_artifact._thread_scoped``), so ``outputs/x`` lands in, and
      reads from, ``outputs/<thread slug>/x``;
    * a loose file (in ``inputs/`` or ``outputs/`` but in no thread folder)
      and the folder of another chat are refused. Before this rule,
      ``save_note`` on a colleague's loose file appended to it, and the
      history row it wrote under this session took the file over (PR #616
      review, P1).

    ``agent-data/`` is not a thread folder, so ``agent-data/NOTES.md`` stays
    one file for the whole tenant dir, as before. That file is shared by every
    member of the organization (HANDOFF H-237).
    """
    from acb_skills.agent_paths import (  # noqa: PLC0415
        THREAD_HEADS,
        instance_slug,
        is_loose_rel,
        is_other_thread_rel,
        is_tenant_instance,
    )
    from acb_skills.write_artifact import (  # noqa: PLC0415
        _thread_scoped,
        artifact_context,
        resolve_in_workspace,
    )

    target = resolve_in_workspace(root, clean)
    if target is None:
        return f"Refused: path '{path}' escapes the workspace."
    root_r = root.resolve()
    rel = target.relative_to(root_r).as_posix()
    ctx = artifact_context()
    if not is_tenant_instance(ctx.get("instance")):
        return target, rel
    scoped = _thread_scoped(rel, ctx)
    if scoped is None:
        return "Refused: this chat has no thread folder."
    session_id = ctx.get("session_id")
    own = instance_slug(str(session_id)) if session_id else None
    if is_loose_rel(scoped) or is_other_thread_rel(scoped, own):
        return f"Refused: path '{path}' belongs to another chat."
    parts = scoped.split("/")
    if parts[0] in THREAD_HEADS and len(parts) < 3:
        return f"Refused: path '{path}' names no file."
    return root_r / scoped, scoped


async def save_note(path: str, fact: str) -> str:
    """Append a dated fact to a notes file in the agent workspace.

    The file is created under ``agent-data/`` if *path* does not already
    start with ``agent-data/``, ``inputs/``, or ``outputs/``.  Each fact
    is prefixed with an ISO-8601 date and written as a bullet point.

    Use this to persist important facts, decisions, and discoveries across
    sessions.  The ``NOTES.md`` file is your canonical working memory —
    read it at the start of every session.

    Args:
        path: Relative path to the notes file, e.g. ``"NOTES.md"`` or
              ``"leads.md"``.  Defaults to ``agent-data/`` if no visible
              workspace prefix is present.
        fact: The fact to record.  Keep it concise — one line per fact.

    Returns:
        ``"Saved to agent-data/NOTES.md"`` or similar confirmation.
    """
    root_s = _get_agent_dir()
    if not root_s:
        # H-201 (§21.16): no run context, so no workspace. Fail closed.
        return "No workspace is configured for this run, so nothing was saved."
    root = Path(root_s)

    # Normalise path — ensure it lands in a visible workspace dir.
    clean = path.replace("\\", "/").lstrip("/.")
    _visible = frozenset({"inputs", "outputs", "agent-data"})
    in_visible = any(
        clean == d or clean.startswith(d + "/") for d in _visible
    )
    if not in_visible:
        clean = f"agent-data/{clean}"

    # Containment guard, and the thread rule of H-227 (``_notes_target``).
    # Fail closed.
    placed = _notes_target(root, clean, path)
    if isinstance(placed, str):
        return placed
    target, clean = placed
    # WS-43d (review P1, fix round 1): never another chat's output folder,
    # another member's skill folder, or the skill author marker.
    from acb_skills.agent_paths import refused_write  # noqa: PLC0415
    from acb_skills.write_artifact import artifact_context  # noqa: PLC0415

    _ctx = artifact_context()
    _why = refused_write(
        Path(root).resolve(), clean,
        member=_ctx.get("member"), thread_id=_ctx.get("session_id"),
    )
    if _why:
        return f"Refused: path '{path}': {_why}."
    target.parent.mkdir(parents=True, exist_ok=True)

    # Build the dated bullet.
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"- {ts}: {fact.strip()}\n"

    existed = target.exists()
    existing = target.read_text(encoding="utf-8") if existed else ""
    new_text = existing + line
    target.write_text(new_text, encoding="utf-8")

    # Write-through to the authoritative blob store — NOTES.md and other
    # agent-data/ files are the agent's durable memory (an extension of its
    # system prompt), so they must survive a wiped volume like Mem0 does.
    # save_note emits no artifact event, so mirror here at the write itself.
    import asyncio as _asyncio  # noqa: PLC0415

    from acb_skills.write_artifact import mirror_to_blob_store  # noqa: PLC0415
    _asyncio.ensure_future(mirror_to_blob_store(
        clean, new_text.encode("utf-8"), mime_type="text/markdown",
        action="modify" if existed else "create",
    ))

    return f"Saved to {clean}"


async def recall_notes(path: str, query: str = "") -> str:
    """Read back a notes file, optionally filtered by a search query.

    Args:
        path: Relative path to the notes file (e.g. ``"NOTES.md"``).
        query: Optional case-insensitive filter string.  Only lines
               containing *query* are returned.  Empty = return all lines.

    Returns:
        The file contents (or filtered lines), or ``"(empty)"`` if the
        file doesn't exist or has no matching lines.

    Example::

        all_notes = await recall_notes("NOTES.md")
        abc_notes = await recall_notes("leads.md", "ABC Corp")
    """
    root_s = _get_agent_dir()
    if not root_s:
        # H-201 (§21.16): no run context, so no workspace. Fail closed.
        return "No workspace is configured for this run, so nothing was read."
    root = Path(root_s)
    clean = path.replace("\\", "/").lstrip("/.")
    # Apply the SAME visible-dir prefixing as save_note so the documented
    # round-trip works: recall_notes("NOTES.md") reads the agent-data/NOTES.md
    # that save_note("NOTES.md", …) wrote (previously it looked at root/NOTES.md
    # and never found it).
    _visible = frozenset({"inputs", "outputs", "agent-data"})
    if not any(clean == d or clean.startswith(d + "/") for d in _visible):
        clean = f"agent-data/{clean}"
    # Containment guard: recall_notes is a file-READ primitive — an embedded
    # ``..`` would let an agent read arbitrary files outside the workspace.
    # H-227: in a shared agent's tenant dir it reads no loose file and no
    # folder of another chat (``_notes_target``).
    placed = _notes_target(root, clean, path)
    if isinstance(placed, str):
        return placed
    target, clean = placed

    if not target.exists():
        return f"{clean}: (file not found)"

    content = target.read_text(encoding="utf-8")
    if not query.strip():
        return content if content.strip() else f"{clean}: (empty)"

    q = query.strip().lower()
    filtered = [line for line in content.splitlines() if q in line.lower()]
    if not filtered:
        return f"{clean}: no lines matching {query!r}"
    return "\n".join(filtered)
