"""People Center · your own row, and only your own (WS-28g-2).

Spec: ``project-docs/specs/people_center_app.md`` §4.5 · **D-PC-15**.

    GET    /people/me           → the caller's own row, or WHY there isn't one
    PATCH  /people/me           → their own record, class-checked
    POST   /people/me/resume    → their own CV
    POST   /people/me/avatar    → their own display image
    DELETE /people/me/avatar    → remove it
    GET    /people/me/absences  → when they are away
    POST   /people/me/absences  → record a span
    DELETE /people/me/absences/{absence_id} → remove one

**This router carries no feature gate, and that is the ticket.** WS-28g put the
self surface on the directory's router, which is gated on ``feature:people`` —
and ``feature:people`` is ``is_default false``. The consequence was that an
ordinary colleague could not open their own profile: the one surface whose
entire purpose is "every person maintains their own record" was reachable only
by people who had been granted the org directory. The rule now is
**the directory is gated; your own row is not** (D-PC-15), and it is the same
argument ``/access`` already won — gating the page that explains a missing
permission hides it from exactly the person who needs it.

**The security property is structural, not a check.** The person is **never
taken from the request** — every route here resolves it from the authenticated
identity through :func:`_my_row`, and no path carries a person id. That is a
stronger guarantee than "we validate that the id is yours", because there is no
id to validate and therefore nothing a later refactor can forget.

⚠️ **WS-28k refined the fence rather than working around it.** The absence
routes need to address a *span* (``/me/absences/{absence_id}``), and the
original fence forbade any path parameter at all — which would have forced an
awkward URL to satisfy a proxy for the real invariant. The invariant is *"no
ungated route takes a PERSON from the request"*, so that is what
``test_org_access_enforcement.UNGATED_ROUTERS`` now asserts, plus the stronger
half it was standing in for: **every ungated endpoint resolves the person
through the self predicate**. The absence id is additionally scoped in SQL
(``AND person_id = …``), so a span belonging to somebody else is a no-op rather
than a deletion.

Everything about *other* people — the directory, the person page, the org
chart, search — stays on ``core.router`` behind ``feature:people``, unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from gateway.avatar import AvatarError
from gateway.routes.people.absences import (
    AbsenceIn,
    create_absence,
    delete_absence,
    fetch_absences,
)
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    clear_avatar,
    find_self_row,
    person_payload,
    store_avatar,
)
from gateway.routes.people.fields import authorize_write

# The request models only — the handlers defer the store imports, and skills.py
# does not import this module, so there is no cycle to mind.
from gateway.routes.people.skills import CredentialsWrite, SkillsWrite
from gateway.routes.tasks import people as tasks_people
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.people.self")

#: No ``dependencies=[require_feature_router("people")]`` — deliberately, and
#: the omission is the feature. Registered in
#: ``test_org_access_enforcement.UNGATED_ROUTERS`` with its reason, because a
#: router missing from that file is *unchecked* rather than *deliberately
#: open*, and the two must never look the same to the next reader.
router = APIRouter(prefix="/people", tags=["people"])


class MeResponse(BaseModel):
    """The three honest answers to "which row is mine" (§5.3).

    Collapsing them would be the defect. A 404 cannot distinguish *"the
    directory has no row for your address"* — an admin has to add one — from
    *"you are signed in without an address"*, which is an identity problem and
    is not fixed anywhere in this app. And returning an empty person object for
    either would render a form that silently saves nothing.
    """
    state: Literal["resolved", "no_directory_row", "no_identity"]
    #: The address the lookup used, echoed so the person can see WHICH address
    #: failed to match — usually the whole explanation (work vs personal).
    email: str | None = None
    person: dict[str, Any] | None = None
    #: What a caller can do about it, in the product's own words. Carried by the
    #: response rather than written into the page, because the page cannot know
    #: whether an admin exists to ask.
    detail: str | None = None


@router.get("/me", response_model=MeResponse)
async def get_me(user: UserContext = Depends(get_current_user)) -> MeResponse:
    """The caller's own person record (§5.3).

    ⚠️ **Route order matters and is not obvious.** ``/people/{person_id}`` on
    the gated router would happily match the literal string ``me`` and then fail
    casting it to a UUID — or, worse for this ticket, refuse an ungranted member
    with a 403 from the directory's gate. FastAPI matches in registration order
    across the whole app, so ``main.py`` includes THIS router first, and
    ``test_people_profile.py`` asserts that ordering rather than trusting it.
    """
    email = (user.email or "").strip() or None
    if not email:
        return MeResponse(
            state="no_identity", email=None,
            detail="You are signed in without an email address, so no directory "
                   "row can be matched to you.",
        )
    async with _tenant_session() as db:
        row = await find_self_row(db, user)
        if row is None:
            return MeResponse(
                state="no_directory_row", email=email,
                detail=f"No person in the directory carries {email}. An "
                       "administrator can add you, or correct the address on "
                       "your existing row.",
            )
        return MeResponse(state="resolved", email=email,
                          person=await person_payload(db, row, user))


async def _my_row(db: Any, user: Any) -> Any:
    """The caller's own row, or a 404 that says which address failed to match.

    404 rather than 403: there is nothing being refused here. The caller is
    entitled to their own record; the product simply does not have one for the
    address they signed in with, which is a different sentence and a different
    fix.
    """
    row = await find_self_row(db, user)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No person in the directory carries {user.email or 'your address'}. "
                "An administrator can add you."
            ),
        )
    return row


@router.patch("/me")
async def update_me(
    body: tasks_people.PersonWrite,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Edit your own record — the self class, plus the admin class if you hold it.

    Both flags are passed to :func:`authorize_write`, so an admin editing their
    OWN row through this door can still change their title: admin is a superset
    of self, never a disjoint set (§4.3). Without that, the ungated door would
    be the *narrower* one for the very people who hold the grant, and they would
    have to go find the other URL to fix their own department.
    """
    names = list(body.model_dump(exclude_unset=True))
    if not names:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        person_id = str(row.id)
        authorize_write(names, is_admin=can_manage_people(user), is_self=True)
    person = await tasks_people.update_person(person_id, body, user)
    async with _tenant_session() as db:
        saved = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": str(person.id)},
        )).fetchone()
        # Re-projected for this caller. `update_person` answers in the admin
        # shape because its own door is admin-only; handing that straight back
        # would be an ungated endpoint returning an admin projection.
        return await person_payload(db, saved, user)


@router.post("/me/resume")
async def upload_my_resume(
    file: UploadFile,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Upload your own CV — *"their CV … can be edited"*, as the directive put it.

    Authorized as a write of ``skills`` and the résumé depth it merges, which is
    the self class — the same check the PATCH runs, not a new rule. The parse →
    merge → re-embed pipeline is the existing one, untouched.
    """
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        person_id = str(row.id)
        authorize_write(
            ["skills", "resume_summary", "years_experience", "domain"],
            is_admin=can_manage_people(user), is_self=True,
        )
    result = await tasks_people.ingest_resume(person_id, file, user)
    async with _tenant_session() as db:
        saved = (await db.execute(
            text("SELECT * FROM people WHERE id = CAST(:id AS uuid)"),
            {"id": person_id},
        )).fetchone()
        return {
            "resume_id": result.resume_id,
            "added_skills": result.added_skills,
            "extracted": result.extracted,
            "person": await person_payload(db, saved, user),
        }


@router.post("/me/avatar")
async def upload_my_avatar(
    file: UploadFile,
    crop_x: float = Form(0.0),
    crop_y: float = Form(0.0),
    crop_size: float = Form(1.0),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Set your own display image (§3.1a).

    The crop rectangle is **fractional** — ``(x, y, side)`` in ``[0, 1]`` of the
    source — so the client works in whatever it displays and never needs the
    image's DPI. A 1000x400 pixel image opens as a 750x300 *point* page, and a
    pixel rectangle from a browser would silently crop the wrong region; that
    was measured, not guessed.

    The three form fields default to a full-frame crop, so a caller that sends
    only a file gets a centre crop rather than an error. **The server squares it
    either way** (D-PC-17): the cropper is a courtesy, the square is a
    guarantee.
    """
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["avatar"], is_admin=can_manage_people(user),
                        is_self=True)
        try:
            await store_avatar(db, str(row.id), await file.read(),
                               (crop_x, crop_y, crop_size),
                               getattr(user, "email", None) or "anonymous")
        except AvatarError as exc:
            # The refusal is a sentence about the file the person just chose,
            # because they are the one who has to choose a different one.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        saved = await _my_row(db, user)
        return await person_payload(db, saved, user)


@router.delete("/me/avatar")
async def delete_my_avatar(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Remove your display image; the directory falls back to your initials."""
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["avatar"], is_admin=can_manage_people(user),
                        is_self=True)
        await clear_avatar(db, str(row.id),
                           getattr(user, "email", None) or "anonymous")
        return await person_payload(db, await _my_row(db, user), user)


@router.get("/me/absences")
async def my_absences(user: UserContext = Depends(get_current_user)) -> dict:
    """When you are away. Your own row, so no HR grant is involved."""
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        return {"rows": await fetch_absences(db, str(row.id))}


@router.post("/me/absences", status_code=201)
async def add_my_absence(
    body: AbsenceIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Record a span you are away.

    Self-writable, and that is the point: requiring an admin to type it is how
    the data ends up absent, which makes every capacity figure that reads it
    quietly wrong. Authorized as a write of ``working_hours`` — the same
    question, in the same place, as changing when you work.
    """
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["working_hours"], is_admin=can_manage_people(user),
                        is_self=True)
        return await create_absence(db, str(row.id), body,
                                    getattr(user, "email", None) or "anonymous")


@router.delete("/me/absences/{absence_id}")
async def remove_my_absence(
    absence_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Remove one of yours.

    The id names a SPAN, never a person — the person still comes from the
    identity — and the delete is scoped ``AND person_id = <you>``, so an id
    belonging to a colleague deletes nothing and answers 404.
    """
    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["working_hours"], is_admin=can_manage_people(user),
                        is_self=True)
        if not await delete_absence(db, str(row.id), absence_id):
            raise HTTPException(status_code=404, detail="No such absence")
    return {"deleted": absence_id}


# ── WS-28h: your own structured skills & credentials ─────────────────────────
#
# The same two-door shape as absences: one implementation (skills.py → the
# `gateway.person_skills` leaf, which owns the D-PC-6 projection), and this
# door resolves the person through the self predicate with no id in the path.

@router.get("/me/work")
async def get_my_work(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """My own open tasks — the same panel a colleague's page already showed.

    **The asymmetry this closes.** `/people/{id}/work` has rendered a
    colleague's open work since WS-28b. My own profile showed none, so I
    could see what everybody else was holding and not what I was. Found by
    an end-to-end review, filed as H-146.

    ⚠️ **It exists here rather than being called from the page**, because
    `/people/{id}/work` is on the GATED router and this page is not
    (D-PC-15). Pointing an ungated surface at a gated endpoint would 403 for
    a `guest`, who can still reach their own profile — the exact defect
    WS-28g-2 was opened to fix, reintroduced through a fetch.

    The route takes no person: `_my_row` resolves it from the authenticated
    identity, which is the invariant `UNGATED_ROUTERS` asserts.

    Scoping is unchanged and is still the VIEWER's — which here is also the
    subject, so it answers "the work I hold in projects I can open". That is
    the honest answer: a task in a project I cannot see is not mine to act
    on from here.
    """
    from gateway.routes.people.directory import get_person_work

    async with _tenant_session() as db:
        row = await _my_row(db, user)
    return (await get_person_work(str(row.id), user=user)).model_dump()


@router.get("/me/skills")
async def get_my_skills(
    user: UserContext = Depends(get_current_user),
) -> dict:
    from gateway.routes.people.skills import capability_payload

    async with _tenant_session() as db:
        row = await _my_row(db, user)
        return await capability_payload(db, str(row.id))


@router.put("/me/skills")
async def put_my_skills(
    body: SkillsWrite, user: UserContext = Depends(get_current_user),
) -> dict:
    """Your own skills, structured — the owner's original ask ("their CV, their
    skill sets, etc. can be edited and seen over here"), now with the level,
    years and recency the assignment questions actually turn on (§3.3).

    Authorized as a write of ``skills`` — the same field class, the same
    authority, as the flat list this table now projects."""
    from gateway.person_skills import replace_skills
    from gateway.routes.people.skills import capability_payload

    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["skills"], is_admin=can_manage_people(user),
                        is_self=True)
        await replace_skills(db, str(row.id),
                             [r.model_dump() for r in body.rows],
                             getattr(user, "email", None) or "anonymous")
        return await capability_payload(db, str(row.id))


@router.put("/me/credentials")
async def put_my_credentials(
    body: CredentialsWrite, user: UserContext = Depends(get_current_user),
) -> dict:
    from gateway.person_skills import replace_credentials
    from gateway.routes.people.skills import capability_payload

    async with _tenant_session() as db:
        row = await _my_row(db, user)
        authorize_write(["skills"], is_admin=can_manage_people(user),
                        is_self=True)
        await replace_credentials(db, str(row.id),
                                  [r.model_dump() for r in body.rows],
                                  getattr(user, "email", None) or "anonymous")
        return await capability_payload(db, str(row.id))
