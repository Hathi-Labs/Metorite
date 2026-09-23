"""Projects · files on a task — WS-27i (spec §11.2 item 1).

    POST   /projects/tasks/{task_id}/attachments        multipart → descriptor
    GET    /projects/tasks/{task_id}/attachments        → what is attached
    GET    /projects/attachments/{id}/{filename}        → the bytes
    DELETE /projects/tasks/{task_id}/attachments/{id}   → detach

**One file store.** The bytes and their metadata go into the existing
``attachments`` registry through the same validation the personal capture
flow uses — imported, not copied, so "is this extension allowed" and "how big
is too big" have one answer. ``pm_task_attachments`` is a thin join.

**The access model is the point, and it is deliberately not the file's.**
``attachments`` is owner-scoped: ``/tasks/attachments/{id}/{name}`` serves
only to the uploader. Correct for a private capture, useless for a shared task.
Here the **join** carries the decision — a file is readable by anyone who can
see a task it is attached to — and the personal route is untouched.

Two consequences worth stating, because both are security properties rather
than conveniences:

1. **There is no attach-by-id endpoint.** Upload and attach are one call. If a
   caller could name an arbitrary ``attachment_id``, they could attach somebody
   else's private capture to a task they own and then read it through the route
   below — a privilege escalation dressed as a feature. The only way a row
   enters this table is by uploading the bytes in the same request.
2. **A personal capture stays unreachable here.** It has no join row, so the
   serve route below cannot find it no matter who asks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from gateway.routes.projects.core import (
    ListResponse,
    _tenant_session,
    actor,
    emit,
    load_visible_task,
    record_activity,
    resolve_visibility,
    router,
)

# Imported, never re-implemented: one answer to "what may be uploaded".
from gateway.routes.tasks.attachments import (
    _BLOCKED_EXT,
    _IMAGE_MIMES,
    _MAX_BYTES,
    _safe_name,
    _storage_dir,
)
from sqlalchemy import text

#: The types served INLINE, so the browser renders them in place.
#:
#: 🔴 **A SAFELIST, and `image/svg+xml` is deliberately absent although the
#: upload calls it an image** (`_IMAGE_MIMES` above includes it, so the
#: descriptor already reports `kind: "image"` for one). An SVG is a DOCUMENT
#: that may carry `<script>`; rendered inline on this origin that script runs
#: with the member's session cookie. It downloads. So does every `text/*`,
#: for the same reason, and so does anything not named here.
#:
#: A new type is added by a person deciding it is safe, never by a pattern
#: that happens to match.
#:
#: Mirrors `workbench/control_plane/src/app/projects/lib/preview.ts`, which
#: decides what the panel OFFERS to preview. A type in one list and not the
#: other is either a download the UI promises to render, or a render the UI
#: never offers.
_INLINE_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "application/pdf",
})

#: How much a SINGLE TASK's attachments may add up to (owner directive,
#: 2026-09-19).
#:
#: ⚠️ **Per task, not per file.** A per-file cap alone lets twenty 9MB files
#: onto one task, and "the attachments that each task can hold" is about the
#: task. It subsumes the shared per-file check, which stays because that rule
#: belongs to the upload path `/tasks` shares.
_TASK_ATTACHMENT_BUDGET = 10 * 1024 * 1024

#: Advisory-lock namespace for the budget check, so the SUM and the INSERT
#: that follows it are one unit. Postgres advisory locks share ONE global
#: space, so the first argument keeps this arm from colliding with any other
#: caller's key. The value is arbitrary and only has to stay put — change it
#: and two releases running side by side stop excluding each other.
_BUDGET_LOCK_NS = 8274

#: Taken before the budget SUM and held to the end of the transaction, so the
#: read and the INSERT that follows it cannot interleave with another upload
#: to the same task.
#:
#: ⚠️ A module constant so a test can run THIS string. Inline, the only fence
#: available was a hermetic fake, and the fake answers an empty result for any
#: statement it does not recognise — it would accept this however it were
#: misspelled. `test_projects_sql_asyncpg.py` runs it on a real Postgres and
#: proves it excludes (R8).
#:
#: ⚠️ **`CAST(:tid AS uuid)::text`, not `:tid`.** `hashtext` hashes TEXT, and
#: Postgres accepts a uuid in several spellings — upper case, mixed case,
#: braced — normalising all of them. Every other statement in this request
#: casts, so two requests spelling one task id differently both succeed while
#: taking DIFFERENT locks, and the race this closes reopens. Casting first
#: makes the lock key the same value the rows are keyed by.
_BUDGET_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(:ns, hashtext(CAST(:tid AS uuid)::text))"
)


def _mb(size: int) -> str:
    """Bytes as a person reads them, for a refusal they can act on.

    A 413 saying `10485760` tells somebody nothing about which file to remove.
    """
    return f"{size / (1024 * 1024):.1f} MB"



def descriptor(row: Any) -> dict[str, Any]:
    """The shape the UI renders — the same field names the capture flow uses."""
    mime = getattr(row, "mime", None) or "application/octet-stream"
    name = getattr(row, "name", "attachment")
    return {
        "attachment_id": str(row.id),
        "kind": "image" if mime in _IMAGE_MIMES else "file",
        "name": name,
        "mime": mime,
        "size": int(getattr(row, "size_bytes", 0) or 0),
        "added_by": getattr(row, "added_by", None),
        "created_at": (
            row.created_at.isoformat() if getattr(row, "created_at", None) else None
        ),
        "url": f"/api/projects/attachments/{row.id}/{name}",
    }


@router.post("/tasks/{task_id}/attachments", status_code=201)
async def attach_file(
    task_id: str,
    file: UploadFile,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload one file onto a task.

    Visibility is checked FIRST — before the bytes are read, let alone written.
    Validating a 15MB upload and then discovering the caller cannot see the task
    would mean an unauthorised caller could still make the server do the work.
    """
    email = actor(user)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)

        name = _safe_name(file.filename or "attachment")
        if Path(name).suffix.lower() in _BLOCKED_EXT:
            raise HTTPException(
                status_code=400, detail=f"File type not allowed: {name}",
            )
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        if len(content) > _MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment too large ({len(content)} bytes; "
                       f"max {_MAX_BYTES}).",
            )

        # ⚠️ The budget is PER TASK, not per file (owner directive,
        # 2026-09-19: "limit the max size of the attachments that each task
        # can hold to 10mb"). A per-file cap alone lets twenty 9MB files onto
        # one task, which is the thing the instruction is about.
        #
        # It subsumes the per-file check above: nothing can exceed the budget
        # on its own. That check is left in place because it belongs to the
        # shared upload rule and also guards `/tasks`, whose budget is its
        # own decision and not this one.
        #
        # Counted from the rows, not from a stored running total: a total
        # column is a second source of truth that drifts the first time a
        # detach does not decrement it.
        #
        # ⚠️ **The lock below is what makes the count mean anything.** Read
        # and write are two statements, so without it the check is a
        # read-then-write race — and the race is the NORMAL case, not an
        # attack. Drop five files on one task and the browser uploads them in
        # parallel. Under READ COMMITTED each transaction reads the same
        # pre-upload SUM, all five pass, and a 10MB task ends up holding 45MB.
        #
        # An advisory lock keyed on the task, rather than `SELECT ... FOR
        # UPDATE` on `pm_tasks`: the row lock would make an upload contend
        # with an unrelated edit to the same task, and this needs to exclude
        # only other uploads. It is held to the end of the transaction, so the
        # SUM and the INSERT below are one unit. Two tasks whose hashes
        # collide serialise needlessly, which costs latency and never
        # correctness.
        await db.execute(
            text(_BUDGET_LOCK_SQL),
            {"ns": _BUDGET_LOCK_NS, "tid": task_id},
        )
        # Aliased and read by NAME rather than through `.scalar()`: the
        # column name is what every fake in the suite can answer, and a
        # single-value read that works on one driver and not on a test
        # double is a fence that cannot run.
        budget_row = (await db.execute(
            text(
                "SELECT COALESCE(SUM(a.size_bytes), 0) AS used "
                "  FROM pm_task_attachments ta "
                "  JOIN attachments a ON a.id = ta.attachment_id "
                " WHERE ta.task_id = CAST(:tid AS uuid)"
            ),
            {"tid": task_id},
        )).fetchone()
        used = int(getattr(budget_row, "used", 0) or 0)
        if used + len(content) > _TASK_ATTACHMENT_BUDGET:
            raise HTTPException(
                status_code=413,
                # ⚠️ Two messages, because "Remove something first" is advice
                # a member cannot act on when removing everything still would
                # not fit this file.
                #
                # The split is on the FILE, not on `used`. Splitting on
                # `used == 0` sent a member down a path that could not work:
                # the per-file cap is 15MB and the budget is 10MB, so a 12MB
                # file on a task holding 1MB read "1.0 MB is already attached,
                # remove something first" — and after removing it the same
                # file was refused again. The property that decides which
                # advice is TRUE is whether the file alone exceeds the budget.
                detail=(
                    (
                        f"This file is {_mb(len(content))} and one task may "
                        f"hold {_mb(_TASK_ATTACHMENT_BUDGET)} in total. "
                        f"Attach a smaller file."
                    )
                    if len(content) > _TASK_ATTACHMENT_BUDGET
                    else (
                        f"This task's attachments would reach "
                        f"{_mb(used + len(content))} and the limit is "
                        f"{_mb(_TASK_ATTACHMENT_BUDGET)}. "
                        f"{_mb(used)} is already attached. Remove something "
                        f"first."
                    )
                ),
            )

        att_id = str(uuid4())
        mime = file.content_type or "application/octet-stream"
        dest = _storage_dir() / f"{att_id}{Path(name).suffix.lower()}"
        dest.write_bytes(content)

        await db.execute(
            text(
                "INSERT INTO attachments "
                "(id, user_id, name, mime, size_bytes, path) "
                "VALUES (CAST(:id AS uuid), :uid, :name, :mime, :size, :path)"
            ),
            {"id": att_id, "uid": email, "name": name, "mime": mime,
             "size": len(content), "path": str(dest)},
        )
        await db.execute(
            text(
                "INSERT INTO pm_task_attachments (task_id, attachment_id, added_by) "
                "VALUES (CAST(:tid AS uuid), CAST(:aid AS uuid), :who) "
                "ON CONFLICT (task_id, attachment_id) DO NOTHING"
            ),
            {"tid": task_id, "aid": att_id, "who": email},
        )
        await record_activity(
            db, activity_type="attachment", created_by=email, task_id=task_id,
            body=f"Attached {name}",
            meta={"attachment_id": att_id, "name": name, "mime": mime,
                  "size": len(content)},
        )
        result = {
            "attachment_id": att_id, "name": name, "mime": mime,
            "size": len(content), "added_by": email,
            "kind": "image" if mime in _IMAGE_MIMES else "file",
            "url": f"/api/projects/attachments/{att_id}/{name}",
        }
        project_id = str(task.project_id)

    await emit("pm.task.updated", {"task_id": task_id, "project_id": project_id,
                                   "attachment_added": att_id})
    return result


@router.get("/tasks/{task_id}/attachments")
async def list_attachments(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> ListResponse:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        rows = (await db.execute(
            text(
                "SELECT a.id, a.name, a.mime, a.size_bytes, "
                "       ta.added_by, ta.created_at "
                "  FROM pm_task_attachments ta "
                "  JOIN attachments a ON a.id = ta.attachment_id "
                " WHERE ta.task_id = CAST(:tid AS uuid) "
                " ORDER BY ta.created_at"
            ),
            {"tid": task_id},
        )).fetchall()
    items = [descriptor(r) for r in rows]
    return ListResponse(rows=items, total=len(items))


@router.get("/attachments/{attachment_id}/{filename}")
async def serve_attachment(
    attachment_id: str,
    filename: str,  # cosmetic — the stored name wins, as in the personal route
    user: UserContext = Depends(get_current_user),
) -> FileResponse:
    """Serve the bytes if the caller can see a task this file is attached to.

    Addressed by attachment rather than by task on purpose: a file may hang off
    more than one task, and requiring the caller to name the *right* one would
    make a legitimate read fail depending on which task they came from.

    404 for "not attached to anything you can see" as well as "no such file"
    (R5). A 403 here would confirm the file exists.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        params: dict[str, Any] = {"aid": attachment_id}
        clauses = ["ta.attachment_id = CAST(:aid AS uuid)"]
        # ⚠️ Unconditional since WS-29b. The `if not vis.unrestricted` that
        # guarded this was correct while the unrestricted clause was the literal
        # `TRUE` — skipping a predicate that filters nothing costs nothing. It
        # is now the TENANT, so skipping it served every organization's files to
        # any `data:org:read` holder.
        clauses.append(vis.project_clause("t.root_project_id"))
        params.update(vis.params)
        row = (await db.execute(
            text(
                "SELECT a.name, a.mime, a.path "
                "  FROM pm_task_attachments ta "
                "  JOIN pm_tasks t ON t.id = ta.task_id "
                "  JOIN attachments a ON a.id = ta.attachment_id "
                " WHERE " + " AND ".join(clauses) + " LIMIT 1"
            ),
            params,
        )).fetchone()
    if row is None or not Path(row.path).is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    mime = (row.mime or "application/octet-stream").split(";")[0].strip().lower()
    inline = mime in _INLINE_MIMES
    return FileResponse(
        row.path,
        media_type=row.mime or "application/octet-stream",
        # ⚠️ `filename=` alone sets `Content-Disposition: attachment`, which
        # forces a DOWNLOAD — a PDF handed to an <iframe> under that header
        # never renders, which is why the panel could only ever link out.
        #
        # A safelisted type is therefore served `inline`; everything else
        # keeps the download. The filename rides along either way, so "Save
        # as" still offers the right one.
        headers={
            "Content-Disposition": (
                f'inline; filename="{row.name}"'
                if inline
                else f'attachment; filename="{row.name}"'
            ),
            # 🔴 Load-bearing, not hygiene. Without it a file stored as
            # `text/plain` whose bytes look like HTML can be content-sniffed
            # into HTML and executed on OUR origin with the member's session
            # attached — the attack the safelist above exists to prevent.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/tasks/{task_id}/attachments/{attachment_id}")
async def detach_file(
    task_id: str, attachment_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Detach a file from a task.

    **The bytes are kept.** Detaching is a statement about this task, and the
    same file may hang off another one; deleting the row from under it would
    turn one person's tidy-up into somebody else's broken link. A file store
    sweep is a separate, deliberate job.

    Detaching something already gone is a no-op, not a 404 — Paca's "lenient
    removes" lesson (research §6), which is what makes a retry after a
    half-failed request safe.
    """
    email = actor(user)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        result = await db.execute(
            text(
                "DELETE FROM pm_task_attachments "
                " WHERE task_id = CAST(:tid AS uuid) "
                "   AND attachment_id = CAST(:aid AS uuid)"
            ),
            {"tid": task_id, "aid": attachment_id},
        )
        removed = int(getattr(result, "rowcount", 0) or 0)
        if removed:
            await record_activity(
                db, activity_type="attachment", created_by=email,
                task_id=task_id, body="Removed an attachment",
                meta={"attachment_id": attachment_id, "removed": True},
            )
    return {"task_id": task_id, "attachment_id": attachment_id, "removed": removed}
