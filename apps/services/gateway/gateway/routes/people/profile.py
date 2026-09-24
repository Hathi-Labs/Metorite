"""People Center · the profile and the self-service write (WS-28g).

Spec: ``project-docs/specs/people_center_app.md`` §4, §5.3 · D-PC-1…D-PC-5.

    PATCH /people/{id}              → a class-checked write (admin OR the subject)
    POST  /people/{id}/avatar       → their display image, same rule
    POST  /people/{id}/resume       → the same rule, for the CV
    GET   /people/{id}/editable     → what THIS caller may write on that row

⚠️ **The `/people/me` routes are NOT here** — they live in ``selfservice.py``
on a router with no feature gate (WS-28g-2 / D-PC-15). These three are the
id-bearing doors: they address *any* person, so they stay behind
``feature:people`` and behind the field-class check both.

**Why a second write door exists, when WS-28b deliberately kept this app
read-only.** The original decision was right for what it decided: the writes
already lived at ``/tasks/people`` under ``admin:members:manage``, and adding
verbs here that forwarded nowhere would have minted a hollow path. What changed
is the *audience*. A person editing their own timezone is not an admin, and
routing them through ``feature:tasks`` would hand every colleague the personal
My Tasks app to change their own phone number — the exact reason the People
Center got its own feature slug in the first place.

**There is still only one write IMPLEMENTATION.** This module authorizes and
then calls the tasks package's route functions directly. A route dependency
(``require_people_write()``) is applied by FastAPI's router, not by Python, so
calling the function is calling the body without the admin gate — which is the
point: the gate this door needs is the field-class check, and it runs first,
here, on every path. What must never happen is a *second* SQL builder; that is
why ``build_person_update`` is shared rather than copied (§13.1's lesson —
a shape change has to be walked against every writer, and there is only value
in that if the writers are countable).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Form, HTTPException, UploadFile
from gateway.avatar import AvatarError
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    clear_avatar,
    is_self,
    person_payload,
    router,
    store_avatar,
)
from gateway.routes.people.fields import authorize_write
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text


async def _authorized_row(db: Any, person_id: str, user: Any,
                          names: list[str]) -> Any:
    """Fetch the row and prove this caller may write these fields, or refuse.

    The row is fetched BEFORE the authorization, and it has to be: whether the
    caller is the subject is a property of the row's address, so there is no
    way to answer "may you" without first knowing "whose". A person who does
    not exist is a 404 — answering 403 there would let a caller probe which ids
    exist by reading the refusal.
    """
    row = (await db.execute(
        text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
        {"id": person_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person")
    authorize_write(
        names,
        is_admin=can_manage_people(user),
        is_self=is_self(user, getattr(row, "email", None)),
    )
    return row


@router.patch("/{person_id}")
async def update_profile(
    person_id: str,
    body: tasks_people.PersonWrite,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Edit a person — an admin may change anything, the subject their own half.

    The payload model is the tasks package's ``PersonWrite``: **one payload,
    one class map, one SQL builder**. What differs between the two doors is the
    authorization in front, not the shape of what may be said.

    ``exclude_unset`` is what makes the class check meaningful. A model dump
    that filled in every unset field with ``None`` would present a self-service
    save as an attempt to write ``manager_id`` and ``status`` — refused, for a
    form that never mentioned them.
    """
    names = list(body.model_dump(exclude_unset=True))
    if not names:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    async with _tenant_session() as db:
        await _authorized_row(db, person_id, user, names)
    # Authorized. The write itself is the tasks package's, unchanged and
    # unduplicated — including the status vocabulary check, the duplicate-address
    # 409, the JSONB/date binding and the capability re-embed. The admin route
    # dependency is a ROUTER concern and does not run on a direct call; the gate
    # that matters for this door already ran above.
    person = await tasks_people.update_person(person_id, body, user)
    async with _tenant_session() as db:
        row = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": str(person.id)},
        )).fetchone()
        # Re-projected for THIS caller: `update_person` answers with the admin
        # shape because its own door is admin-only. Handing that straight back
        # would leak the HR and private halves to a self-editor who may write
        # their timezone and, on somebody else's row, may read nothing.
        return await person_payload(db, row, user)


@router.post("/{person_id}/resume")
async def upload_resume(
    person_id: str,
    file: UploadFile,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Upload and parse a CV — the subject's own, or an admin's on their behalf.

    "Their CV … can be edited" was explicit in the owner's directive, and a
    person is the best available source for their own résumé. Authorized as a
    write of ``skills`` (plus the résumé depth it merges), which is the self
    class — so the check is the same one the PATCH runs and not a new rule.

    The parse → merge → re-embed pipeline is the existing one, untouched.
    """
    async with _tenant_session() as db:
        await _authorized_row(
            db, person_id, user,
            ["skills", "resume_summary", "years_experience", "domain"],
        )
    result = await tasks_people.ingest_resume(person_id, file, user)
    async with _tenant_session() as db:
        row = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": person_id},
        )).fetchone()
        return {
            "resume_id": result.resume_id,
            "added_skills": result.added_skills,
            "extracted": result.extracted,
            "person": await person_payload(db, row, user),
        }


# ⚠️ ``GET /{person_id}/editable`` WAS HERE. It is gone (H-144, 2026-09-23).
#
# It answered "which fields may this caller write on this row" — the same
# answer, from the same ``editable_fields`` call, that ``person_payload``
# already embeds on every person read. Nothing called it. `rg -n "editable"` in
# the People client returned only the type of the embedded field, and no test
# exercised the route.
#
# Its docstring argued that a form could ask this "before it draws, so it does
# not have to fetch the whole person". A form that did so then needs a SECOND
# round trip for the values it draws, so the saving was never real.
#
# Deleted, and not deprecated. CLAUDE.md §5 refuses a second way to do an
# existing thing: two routes that answer one question drift, and the one that
# drifts is the one nobody calls. A client that wants the answer reads
# ``editable_fields`` off the person payload, which is where the UI reads it.


@router.post("/{person_id}/avatar")
async def upload_avatar(
    person_id: str,
    file: UploadFile,
    crop_x: float = Form(0.0),
    crop_y: float = Form(0.0),
    crop_size: float = Form(1.0),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Set somebody's display image — theirs, or an admin's on their behalf.

    Authorized as a write of ``avatar``, which is the self class: the same
    question the PATCH asks about a timezone, asked in the same place. The
    normalisation is `core.store_avatar`, shared with the self door — a second
    normaliser would be a second answer to what shape an avatar is.
    """
    async with _tenant_session() as db:
        await _authorized_row(db, person_id, user, ["avatar"])
        try:
            await store_avatar(db, person_id, await file.read(),
                               (crop_x, crop_y, crop_size),
                               getattr(user, "email", None) or "anonymous")
        except AvatarError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": person_id})).fetchone()
        return await person_payload(db, row, user)


@router.delete("/{person_id}/avatar")
async def delete_avatar(
    person_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        await _authorized_row(db, person_id, user, ["avatar"])
        await clear_avatar(db, person_id,
                           getattr(user, "email", None) or "anonymous")
        row = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": person_id})).fetchone()
        return await person_payload(db, row, user)
