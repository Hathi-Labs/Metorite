"""Tasks · attachments — context files/photos/links on captures.

GTD capture keeps context WITH the item: a whiteboard photo, a spec PDF, a
URL. Files are stored server-side (``GTD_ATTACHMENTS_DIR``, default
``data/gtd_attachments`` under the gateway CWD) with an owner-checked row in
``attachments``; items reference them in ``gtd_items.attachments`` JSONB
({kind: 'file'|'image'|'link', name, url, attachment_id?, mime?, size?}).
Links are JSONB-only — no upload involved.

  POST /tasks/attachments                  multipart upload → descriptor
  GET  /tasks/attachments/{id}/{filename}  serve (owner-checked)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from gateway.routes.tasks.core import _tenant_session, _uid, router
from sqlalchemy import text

_MAX_BYTES = 15 * 1024 * 1024  # 15 MB per attachment
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp",
                "image/svg+xml")
_BLOCKED_EXT = {".exe", ".dll", ".so", ".bat", ".cmd", ".sh", ".ps1", ".msi"}


def _storage_dir() -> Path:
    d = Path(os.environ.get("GTD_ATTACHMENTS_DIR", "data/gtd_attachments"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(raw: str) -> str:
    name = Path(raw or "attachment").name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120] or "attachment"


@router.post("/attachments", status_code=201)
async def upload_attachment(
    file: UploadFile,
    user: UserContext = Depends(get_current_user),
):
    """Upload ONE file; returns the descriptor the capture flow embeds in
    the item's ``attachments`` list."""
    name = _safe_name(file.filename or "attachment")
    if Path(name).suffix.lower() in _BLOCKED_EXT:
        raise HTTPException(status_code=400,
                            detail=f"File type not allowed: {name}")
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Attachment too large ({len(content)} bytes; "
                   f"max {_MAX_BYTES}).")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    att_id = str(uuid4())
    mime = file.content_type or "application/octet-stream"
    dest = _storage_dir() / f"{att_id}{Path(name).suffix.lower()}"
    dest.write_bytes(content)

    async with _tenant_session() as db:
        await db.execute(text(
            """INSERT INTO attachments
               (id, user_id, name, mime, size_bytes, path)
               VALUES (:id, :uid, :name, :mime, :size, :path)"""),
            {"id": att_id, "uid": _uid(user), "name": name, "mime": mime,
             "size": len(content), "path": str(dest)})

    return {
        "attachment_id": att_id,
        "kind": "image" if mime in _IMAGE_MIMES else "file",
        "name": name,
        "mime": mime,
        "size": len(content),
        "url": f"/api/tasks/attachments/{att_id}/{name}",
    }


@router.get("/attachments/{attachment_id}/{filename}")
async def serve_attachment(
    attachment_id: str,
    filename: str,  # cosmetic — the row's stored name wins
    user: UserContext = Depends(get_current_user),
):
    async with _tenant_session() as db:
        row = (await db.execute(text(
            """SELECT name, mime, path FROM attachments
               WHERE id = :id AND user_id = :uid"""),
            {"id": attachment_id, "uid": _uid(user)})).fetchone()
    if row is None or not Path(row.path).is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(row.path, media_type=row.mime or "application/octet-stream",
                        filename=row.name)
