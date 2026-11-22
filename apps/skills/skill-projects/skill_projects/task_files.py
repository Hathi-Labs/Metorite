"""The files of a task, from the chat (WS-IMG IMG-2, ``projects_ai_chat.md`` §30.4).

``attach_to_task`` (``guarded.py``) and ``create_task`` (``writes.py``) share
the checks here, so a file meets the same "no" in either tool, and always
BEFORE the card. This module holds the one reader of a chat file, the one
budget rule, and the one Markdown of an inline image.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from skill_projects.client import data

#: What one task may hold in files, and the types the upload refuses. The
#: route owns both (``attachments.py`` ``_TASK_ATTACHMENT_BUDGET``,
#: ``tasks/attachments.py`` ``_BLOCKED_EXT``). The tools say them BEFORE the
#: card, so a member never approves an upload that the route will refuse.
#: ``test_projects_nudge_attach_tools.py`` pins each value to the route's.
TASK_FILE_BUDGET = 10 * 1024 * 1024
BLOCKED_FILE_TYPES = frozenset({".exe", ".dll", ".so", ".bat", ".cmd", ".sh", ".ps1", ".msi"})

#: The images that draw inline. The same safelist as ``preview.ts``
#: (``PREVIEWABLE_IMAGE_MIMES``): SVG is a document, so it attaches and draws nothing.
INLINE_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
)

#: A file in the thread: its name and its bytes.
ChatFile = tuple[str, bytes]


def mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def size_words(size: int) -> str:
    """A size for the card line: KB under 1 MB, else MB."""
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return mb(size)


def split_names(files: object) -> list[str]:
    """The names of ``files``, in order, with no blank and no repeat."""
    if files is None:
        return []
    if isinstance(files, str):
        files = [files]
    out: list[str] = []
    for item in files if isinstance(files, (list, tuple)) else [files]:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def file_refusal(name: str, content: bytes) -> str:
    """The "no" for one file before the card, or ``""``. Empty, a blocked
    type, or alone over the budget."""
    size = len(content)
    if not size:
        return f"{data(name)} is empty, so there is nothing to attach."
    if Path(name).suffix.lower() in BLOCKED_FILE_TYPES:
        return f"File type not allowed: {data(name)}. Ask the member for another file."
    if size > TASK_FILE_BUDGET:
        return (
            f"{data(name)} is {mb(size)} and one task may hold {mb(TASK_FILE_BUDGET)} "
            "in total. Attach a smaller file."
        )
    return ""


def budget_refusal(files: list[ChatFile], used: int, ref: str) -> str:
    """The "no" when ``files`` on top of the ``used`` bytes pass the budget,
    or ``""``. ``ref`` names the task, or says "The new task"."""
    total = used + sum(len(c) for _n, c in files)
    if total <= TASK_FILE_BUDGET:
        return ""
    names = ", ".join(data(n) for n, _c in files)
    return (
        f"{ref} would hold {mb(total)} with {names}, and the limit is "
        f"{mb(TASK_FILE_BUDGET)}. It holds {mb(used)} now. "
        + ("Detach a file first." if used else "Attach fewer or smaller files.")
    )


async def read_checked(names: list[str]) -> list[ChatFile] | str:
    """Each named chat file, read and checked one by one, or ONE sentence that
    names the file and the reason. Nothing is written here."""
    from acb_skills import attachment_tools

    out: list[ChatFile] = []
    for asked in names:
        got = await attachment_tools.read_attachment_bytes(asked)
        if isinstance(got, str):
            return got
        name, content = got
        if any(name == taken for taken, _c in out):
            continue  # the same file under another spelling of its name
        why = file_refusal(name, content)
        if why:
            return why
        out.append((name, content))
    return out


def mime_of(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def card_line(files: list[ChatFile]) -> str:
    """The value of the card's ``files`` line: each file with its size, then who
    can open them. The client reads ``key: value``, so the key is ``files``."""
    listed = ", ".join(f"{data(n)} ({size_words(len(c))})" for n, c in files)
    who = "it" if len(files) == 1 else "them"
    return f"{listed} · everyone who can open the task can open {who}"


#: The served path up to the attachment's id, and the id (``inlineUploads.ts`` SERVED).
_SERVED = re.compile(
    r"^(.*/attachments/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
    re.IGNORECASE,
)


def image_markdown(row: dict[str, Any]) -> str:
    """``![name](<served url>)`` for an uploaded row that draws inline, as the
    paste of TR-4 writes it (``inlineUploads.ts`` ``imageMarkdown``), or ``""``.
    The URL keeps the route's own path up to the id, and ends in a segment
    with no character that needs an escape."""
    if str(row.get("mime") or "") not in INLINE_IMAGE_MIMES:
        return ""
    found = _SERVED.match(str(row.get("url") or ""))
    if not found:
        return ""
    name = str(row.get("name") or "image")
    segment = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if re.fullmatch(r"\.*", segment):
        segment = "image"
    alt = re.sub(r"[\[\]\\\r\n]", "", name).strip() or "image"
    return f"![{alt}]({found.group(1)}{found.group(2).lower()}/{segment})"
