"""Transport · Metorite files: a file the member can already see, attached to
a chat email (H-392, owner decision of 2026-10-24).

``email_app_master_plan.md`` §21.10. A chat may attach three kinds of
Metorite file, each named by a reference (``acb_skills.held_card``):

* ``artifact:<agent>:<path>`` — a file of the member's OWN workspace of that
  agent, as the Artifacts app lists it. The bytes come through
  ``send.load_artifact_attachments``, the one reader of a workspace file for
  a send (``workspace._member_agent_workspace``, H-201).
* ``knowledge:<page id>`` — a Knowledge page that the member can read, as
  its Markdown. The page comes through ``knowledge.core.load_page``, the
  visibility read of the Knowledge routes, behind the same two gates
  (``KNOWLEDGE_ENABLED`` for the organization, and ``feature:knowledge``).
  No PDF export route exists, so the file is the Markdown.
* ``task-file:<attachment id>`` — a file on a task that the member can see.
  The row comes through ``projects.attachments.visible_task_attachment``, the
  one read that the serve route of the Projects app uses, behind
  ``feature:projects``.

So no new access path exists: each file resolves as the member, through the
read that the app itself uses, and a file the member cannot see is a 404.
The bytes of one send are capped as a held send caps them
(``automation.held_card.MAX_FILES_BYTES``). Each file carries the SHA-256 of
its bytes, so a send acts only when the file is still the file that the
card named (409 else). Fence: ``tests/unit/test_email_metorite_files.py``.
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.email.core import router
from pydantic import BaseModel, ConfigDict, Field

#: The most files of one send.
MAX_METORITE_FILES = 20
#: The answer for a file that the member cannot see, or that does not exist.
#: One answer for both, so a 404 does not tell that a file exists.
FILE_NOT_VISIBLE = "A named Metorite file is not a file that you can see, so nothing was sent."
#: The answer for a file that changed after its card.
FILE_CHANGED = "A named Metorite file changed after the card showed it, so nothing was sent."

#: The source of each kind, as a card names it.
SOURCE_TEXT = {"artifact": "Artifacts", "knowledge": "Knowledge", "task-file": "a task"}


@dataclass(frozen=True)
class MetoriteFile:
    """One resolved file: what the card shows, and what the send attaches."""

    ref: str
    source: str
    name: str
    size: int
    mime: str
    content: bytes
    digest: str

    def shown(self) -> dict[str, Any]:
        """What a card shows. No bytes."""
        return {"ref": self.ref, "source": self.source, "name": self.name,
                "size": self.size, "digest": self.digest}


def _max_bytes() -> int:
    from gateway.routes.email.automation.held_card import MAX_FILES_BYTES

    return MAX_FILES_BYTES


def _page_file_name(title: Any) -> str:
    """A file name for a page: its title, with no path sign and no control."""
    text = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", " ", str(title or "")).strip()
    return f"{' '.join(text.split())[:120] or 'page'}.md"


def _not_visible() -> HTTPException:
    return HTTPException(status_code=404, detail=FILE_NOT_VISIBLE)


async def _artifact(user: UserContext, spec: str, *, held: bool) -> tuple[str, bytes, str]:
    from acb_skills.held_card import HELD_WORKSPACE_AGENT, HELD_WORKSPACE_REFUSED
    from gateway.routes.email.transport.send import (
        ArtifactAttachment,
        load_artifact_attachments,
    )

    agent, _, path = spec.partition(":")
    if held and agent.strip().lower() == HELD_WORKSPACE_AGENT:
        raise HTTPException(status_code=422, detail=HELD_WORKSPACE_REFUSED)
    [got] = load_artifact_attachments(
        [ArtifactAttachment(path=path, agent=agent)], user.email)
    return str(got["filename"]), bytes(got["content"]), str(got["mime_type"])


async def _knowledge(user: UserContext, page_id: str) -> tuple[str, bytes, str]:
    from gateway.routes.knowledge import core as kb
    from gateway.routes.knowledge.flags import knowledge_org_allowed

    if not (knowledge_org_allowed(user.organization_id)
            and user.has_permission("feature:knowledge")):
        raise _not_visible()
    async with kb._tenant_session() as db:
        page = await kb.load_page(db, user, page_id)
    if page is None:
        raise _not_visible()
    return (_page_file_name(page.title), str(page.body_md or "").encode("utf-8"),
            "text/markdown")


async def _task_file(user: UserContext, attachment_id: str) -> tuple[str, bytes, str]:
    from gateway.routes.projects import core as pm
    from gateway.routes.projects.attachments import visible_task_attachment

    if not user.has_permission("feature:projects"):
        raise _not_visible()
    async with pm._tenant_session() as db:
        row = await visible_task_attachment(db, user, attachment_id)
    if row is None or not Path(row.path).is_file():
        raise _not_visible()
    if int(getattr(row, "size_bytes", 0) or 0) > _max_bytes():
        raise HTTPException(status_code=422, detail="A task file is too large to send.")
    name = str(row.name or "file")
    mime = str(row.mime or "") or (mimetypes.guess_type(name)[0] or "application/octet-stream")
    return name, Path(row.path).read_bytes(), mime


async def resolve_metorite_files(
    user: UserContext, refs: list[str], *, held: bool = False,
) -> list[MetoriteFile]:
    """THE resolver of a Metorite file of a send, as *user*.

    Each ref must be one of the three forms. A ref of another form is a 422,
    and a file the member cannot see is a 404. *held* refuses a file of the
    held run's own workspace (``held_card.HELD_WORKSPACE_AGENT``). The bytes
    of all the files together are capped."""
    from acb_skills.held_card import parse_file_ref

    if len(refs) > MAX_METORITE_FILES:
        raise HTTPException(status_code=422,
                            detail=f"Name at most {MAX_METORITE_FILES} files at a time.")
    out: list[MetoriteFile] = []
    total = 0
    seen: set[str] = set()
    for ref in refs:
        try:
            source, ident = parse_file_ref(ref)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if source == "attachment":
            raise HTTPException(status_code=422, detail=(
                "A file of a mail goes on a forward or a held send, not here."))
        key = f"{source}:{ident}"
        if key in seen:
            continue
        seen.add(key)
        if source == "artifact":
            name, content, mime = await _artifact(user, ident, held=held)
        elif source == "knowledge":
            name, content, mime = await _knowledge(user, ident)
        else:
            name, content, mime = await _task_file(user, ident)
        total += len(content)
        if total > _max_bytes():
            raise HTTPException(status_code=422, detail=(
                f"The files are larger than {_max_bytes() // (1024 * 1024)} MB together, "
                "so nothing was sent."))
        out.append(MetoriteFile(ref=key, source=source, name=name, size=len(content),
                                mime=mime, content=content,
                                digest=hashlib.sha256(content).hexdigest()))
    return out


def check_digests(files: list[MetoriteFile], expect: dict[str, str]) -> None:
    """409 unless each file has the digest that its card showed."""
    have = {f.ref: f.digest for f in files}
    if set(have) != set(expect) or any(have[k] != expect[k] for k in expect):
        raise HTTPException(status_code=409, detail=FILE_CHANGED)


class MetoriteFilesRequest(BaseModel):
    """The files to show on a send card. Nothing is sent."""

    model_config = ConfigDict(extra="forbid")

    refs: list[str] = Field(max_length=MAX_METORITE_FILES)


@router.post("/metorite-files/resolve")
async def resolve_metorite_files_route(
    req: MetoriteFilesRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The name, the size and the digest of each named file, as the member
    sees it. A read: it sends nothing and returns no bytes."""
    files = await resolve_metorite_files(user, list(req.refs))
    return {"files": [f.shown() for f in files]}
