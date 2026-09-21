"""People Center · who is away, and when (WS-28k).

Spec: ``project-docs/specs/people_center_app.md`` §5.8 · **D-PC-7**.

    GET    /people/{id}/absences        → theirs (HR tier)
    POST   /people/{id}/absences        → record one (admin, or the subject)
    DELETE /people/{id}/absences/{aid}  → remove one

    GET    /people/me/absences          → your own (UNGATED — selfservice.py)
    POST   /people/me/absences          → record your own
    DELETE /people/me/absences/{aid}    → remove your own

**Availability, not leave management** (D-PC-7). There is no approver, no
status, no balance and no entitlement, and a ticket that adds one has become a
different product — see the migration's header. What is built is the half
assignment needs: who is away, when, and how that changes their week.

**Absences are self-writable.** A person records their own holiday; it is a
statement about themselves, in the same class as their working hours, and
requiring an admin to type it is how the data ends up absent — which makes
every capacity figure that reads it quietly wrong.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    can_read_hr_fields,
    is_self,
    router,
)
from gateway.routes.people.fields import authorize_write
from pydantic import BaseModel
from sqlalchemy import text

#: The three words, mirrored from migration 174's CHECK. ONE tuple, the same
#: shape `PEOPLE_STATUSES` takes for 148's — a fourth kind accepted here and
#: refused by Postgres would be a save that fails at 3am rather than at the
#: route.
ABSENCE_KINDS: tuple[str, ...] = ("away", "holiday", "partial")

#: How far ahead a person read looks. The person page shows what is coming, not
#: a life history; the dashboard asks its own window.
UPCOMING_DAYS = 120


class AbsenceIn(BaseModel):
    starts_on: str
    ends_on: str
    kind: str = "away"
    hours_per_day: float | None = None
    note: str | None = None


def _as_date(name: str, value: Any) -> date:
    """ISO string → ``date``, or a 400 naming the field.

    Parsed rather than cast in SQL: ``CAST(:x AS date)`` over a bound string is
    the shape asyncpg refuses (the WS-27k defect), so the date arrives as a
    real ``date`` object exactly as the profile's `start_date` does.
    """
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' is not a date (YYYY-MM-DD): {value!r}.") from exc


def validate(body: AbsenceIn) -> dict[str, Any]:
    """Refuse what the database would refuse, and say why in words."""
    starts = _as_date("starts_on", body.starts_on)
    ends = _as_date("ends_on", body.ends_on)
    if ends < starts:
        raise HTTPException(
            status_code=400,
            detail="An absence cannot end before it starts. Check the dates.")
    if body.kind not in ABSENCE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown kind '{body.kind}'. One of: {list(ABSENCE_KINDS)}.")
    hours = body.hours_per_day
    if hours is not None and not 0 < float(hours) <= 24:
        raise HTTPException(
            status_code=400,
            detail="hours_per_day must be between 0 and 24.")
    if hours is not None and body.kind != "partial":
        # Silently storing it would leave a number the arithmetic ignores,
        # which is how somebody comes to believe their half-day was recorded.
        raise HTTPException(
            status_code=400,
            detail="hours_per_day only applies to a 'partial' absence.")
    return {"starts_on": starts, "ends_on": ends, "kind": body.kind,
            "hours_per_day": hours, "note": (body.note or "").strip() or None}


async def fetch_absences(db: Any, person_id: str, *,
                         since: date | None = None) -> list[dict[str, Any]]:
    """This person's absences, soonest first.

    ``since`` drops what has already finished. Absent, everything comes back —
    which is what an edit surface needs and what a capacity calculation over a
    past window needs.
    """
    clauses = ["person_id = CAST(:pid AS uuid)"]
    params: dict[str, Any] = {"pid": person_id}
    if since is not None:
        clauses.append("ends_on >= :since")
        params["since"] = since
    rows = (await db.execute(
        text("SELECT id, starts_on, ends_on, kind, hours_per_day, note, "
             "       created_by "
             "  FROM people_absences "
             " WHERE " + " AND ".join(clauses) + " ORDER BY starts_on"),
        params,
    )).fetchall()
    return [
        {"id": str(r.id), "starts_on": r.starts_on.isoformat(),
         "ends_on": r.ends_on.isoformat(), "kind": r.kind,
         "hours_per_day": r.hours_per_day, "note": r.note,
         "created_by": r.created_by}
        for r in rows
    ]


async def rows_for_availability(db: Any, person_id: str) -> list[dict[str, Any]]:
    """The same spans, as ``date`` objects, for :mod:`gateway.work_schedule`.

    A separate shape from :func:`fetch_absences` on purpose: that one is the
    WIRE form (ISO strings) and this one is the ARITHMETIC form. Converting the
    wire form back into dates at each consumer is how one of them ends up
    comparing a string to a date and quietly finding nothing.
    """
    rows = (await db.execute(
        text("SELECT starts_on, ends_on, kind, hours_per_day "
             "  FROM people_absences "
             " WHERE person_id = CAST(:pid AS uuid)"),
        {"pid": person_id},
    )).fetchall()
    return [{"starts_on": r.starts_on, "ends_on": r.ends_on, "kind": r.kind,
             "hours_per_day": r.hours_per_day} for r in rows]


async def create_absence(db: Any, person_id: str, body: AbsenceIn,
                         actor: str) -> dict[str, Any]:
    """Insert one. Shared by both doors — one implementation, two audiences."""
    values = validate(body)
    row = (await db.execute(
        text("INSERT INTO people_absences "
             "  (person_id, starts_on, ends_on, kind, hours_per_day, note, "
             "   created_by) "
             "VALUES (CAST(:pid AS uuid), :starts_on, :ends_on, :kind, "
             "        :hours_per_day, :note, :by) "
             "RETURNING id"),
        {"pid": person_id, "by": actor, **values},
    )).fetchone()
    return {"id": str(row.id), **{k: (v.isoformat() if isinstance(v, date) else v)
                                  for k, v in values.items()}}


async def delete_absence(db: Any, person_id: str, absence_id: str) -> bool:
    """Remove one — **scoped to the person**, so a wrong id deletes nothing.

    ``AND person_id = …`` is not belt-and-braces; it is the control. The id
    alone identifies a row belonging to anybody, and the caller has already
    been authorized against a *person*, not against a span.
    """
    result = await db.execute(
        text("DELETE FROM people_absences "
             " WHERE id = CAST(:aid AS uuid) "
             "   AND person_id = CAST(:pid AS uuid)"),
        {"aid": absence_id, "pid": person_id},
    )
    return bool(getattr(result, "rowcount", 0))


# ── The id-bearing door (gated on `feature:people`) ──────────────────────────

async def _authorized(db: Any, person_id: str, user: Any, *,
                      write: bool) -> Any:
    row = (await db.execute(
        text("SELECT id, email FROM people WHERE id = CAST(:id AS uuid)"),
        {"id": person_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person")
    mine = is_self(user, getattr(row, "email", None))
    if write:
        # The same field-class question the PATCH asks, asked in the same
        # place: an absence is a statement about yourself.
        authorize_write(["working_hours"], is_admin=can_manage_people(user),
                        is_self=mine)
    elif not (can_read_hr_fields(user) or mine):
        # HR tier: when somebody is away is capacity information, and capacity
        # has been behind `admin:members:read` since WS-24 N4.
        raise HTTPException(
            status_code=403,
            detail="Seeing when somebody is away needs admin:members:read.")
    return row


@router.get("/{person_id}/absences")
async def list_absences(
    person_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        await _authorized(db, person_id, user, write=False)
        return {"rows": await fetch_absences(db, person_id)}


@router.post("/{person_id}/absences", status_code=201)
async def add_absence(
    person_id: str, body: AbsenceIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        await _authorized(db, person_id, user, write=True)
        return await create_absence(
            db, person_id, body, getattr(user, "email", None) or "anonymous")


@router.delete("/{person_id}/absences/{absence_id}")
async def remove_absence(
    person_id: str, absence_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        await _authorized(db, person_id, user, write=True)
        if not await delete_absence(db, person_id, absence_id):
            raise HTTPException(status_code=404, detail="No such absence")
    return {"deleted": absence_id}


async def away_today(db: Any, person_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Who, of these people, is away right now — **one query for the page**.

    The directory renders dozens of rows and "do not chase them, they are away"
    is the single most useful thing on one, so it has to be cheap: a per-row
    lookup would be an N+1 on the app's busiest read. One statement, keyed by
    person, and a full absence beats an overlapping partial for the same reason
    :func:`gateway.work_schedule.absent_on` prefers it — answering "partial"
    would put somebody on a picker as available.

    Best-effort: a database one deploy behind migration 174 answers "nobody is
    away" rather than failing the directory.
    """
    if not person_ids:
        return {}
    try:
        rows = (await db.execute(
            text("SELECT person_id, kind, ends_on FROM people_absences "
                 " WHERE person_id = ANY(CAST(:ids AS uuid[])) "
                 "   AND starts_on <= CURRENT_DATE AND ends_on >= CURRENT_DATE "
                 " ORDER BY (kind = 'partial'), ends_on DESC"),
            {"ids": person_ids},
        )).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        # The ORDER BY puts full absences first, so the first row per person
        # wins and `setdefault` is the whole selection rule.
        out.setdefault(str(row.person_id),
                       {"kind": row.kind, "until": row.ends_on.isoformat()})
    return out
