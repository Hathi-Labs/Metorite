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

    # Containment guard: refuse a path that escapes the workspace (embedded
    # ``..`` etc.) — see write_artifact.resolve_in_workspace. Fail closed.
    from acb_skills.write_artifact import resolve_in_workspace  # noqa: PLC0415
    target = resolve_in_workspace(root, clean)
    if target is None:
        return f"Refused: path '{path}' escapes the workspace."
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
    from acb_skills.write_artifact import resolve_in_workspace  # noqa: PLC0415
    target = resolve_in_workspace(root, clean)
    if target is None:
        return f"Refused: path '{path}' escapes the workspace."

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
