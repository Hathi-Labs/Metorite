"""Tasks · people — the org-knowledge layer (spec §6.1).

GET /tasks/people serves the company's people with roles, skills (org chart +
resume-extracted), capacity/availability, and their ClickUp user id — imported
from agent-project-manager's agent-data via scripts/import_hr_people.py.

This is what makes Clarify capability-aware: the delegation/assignee pickers
and the proposal heuristic see WHO can do WHAT and who has hours free, not
just names.

⚠️ **"Personal phone numbers are never stored or served" is no longer true**,
and the sentence is retired here rather than left standing. WS-28g added the
§3.5 private half of the record on the owner's 2026-08-13 directive; what
replaces the absolute is a *tier* — :data:`PRIVATE_FIELDS` is projected away
unless the caller holds ``admin:members:manage`` or is the subject (D-PC-3),
and the HR importer still populates none of it.

Access (colleague_onboarding.md §4 N4, owner-answered 2026-08-04 — "directory
open, HR fields restricted"): the roster is org data, so the *directory* stays
readable by anyone holding `feature:tasks`, but the HR-sensitive half
(:data:`HR_FIELDS`) is projected away for a caller without
``admin:members:read``, and every write here is gated on
``admin:members:manage``. Both permissions and the two seams live in
``core.py``. ``fetch_people_for_clarify`` is deliberately OUTSIDE that rule —
see its docstring.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, UploadFile
from gateway.routes.tasks.attachments import _safe_name, _storage_dir
from gateway.routes.tasks.core import (
    PEOPLE_STATUSES,
    _tenant_session,
    _uid,
    can_read_hr_fields,
    require_people_write,
    router,
    validate_person_vocabularies,
)
from gateway.routes.tasks.resume_parse import parse_resume
from pydantic import BaseModel
from sqlalchemy import text

_RESUME_MAX_BYTES = 15 * 1024 * 1024  # 15 MB
_RESUME_EXT = {".pdf", ".docx", ".txt", ".md"}


class OrgPersonModel(BaseModel):
    id: str
    name: str
    email: str | None = None
    role: str | None = None
    title: str | None = None
    department: str | None = None
    team: str | None = None
    reports_to: str | None = None
    manager_id: str | None = None
    status: str = "active"
    skills: list[str] = []
    skills_source: dict[str, str] = {}
    domain: str | None = None
    # Résumé-extracted depth (from the CVs, via agent-project-manager's
    # ingest_resumes.py → import_hr_people.py). Served so delegation can weigh
    # seniority/experience, not just skill keywords.
    resume_summary: str | None = None
    years_experience: int | None = None
    capacity_hours_per_week: int | None = None
    current_load_hours_per_week: int | None = None
    available_hours_per_week: int | None = None
    provider_user_id: str | None = None  # ClickUp user id (assignment target)

    # ── The profile (WS-28g / P-3, spec §3) ─────────────────────────────────
    # Grouped by the spec's sections rather than by type, because the section
    # is what decides the read tier and the write class — see
    # `routes/people/fields.py`, which is the authority for both.
    #
    # §3.1 · the self-describing directory half (read: directory)
    preferred_name: str | None = None
    pronouns: str | None = None
    location: str | None = None
    timezone: str | None = None
    working_hours: dict[str, Any] | None = None
    bio: str | None = None
    links: dict[str, Any] | None = None
    languages: list[str] = []
    #: §3.1a — a `data:image/jpeg;base64` URI of the SERVER's 256x256 re-encode
    #: (D-PC-17). Directory tier: a display image is what a directory IS.
    avatar: str | None = None
    avatar_updated_at: str | None = None
    # §3.2 · employment (read: HR)
    employee_id: str | None = None
    employment_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    seniority: str | None = None
    cost_center: str | None = None
    # §3.3/§3.4 · what they want to work on, and their own stated ceiling on
    # parallel work (read: HR — both are planning inputs, not directory facts)
    interests: list[str] = []
    max_concurrent_tasks: int | None = None
    # §3.5 · private (read: `admin:members:manage` or self — NOT
    # `admin:members:read`; D-PC-3)
    phone: str | None = None
    emergency_contact: dict[str, Any] | None = None
    personal_email: str | None = None
    birthday: str | None = None  # MM-DD, never a date of birth (D-PC-9)


#: The HR-sensitive half of a person record — everything the owner's N4
#: answer restricts to `admin:members:read`. The rest of `OrgPersonModel`
#: (name, email, role, title, department, team, reports_to/manager_id,
#: status, domain, provider_user_id and §3.1's self-described directory half)
#: is the basic directory and stays visible to every holder of `feature:tasks`.
#:
#: WS-28g adds the §3.2 employment half plus the two planning inputs to the
#: same tier, and adds a *narrower* tier above it — :data:`PRIVATE_FIELDS`.
HR_FIELDS: tuple[str, ...] = (
    "skills",
    "skills_source",
    "resume_summary",
    "years_experience",
    "capacity_hours_per_week",
    "current_load_hours_per_week",
    "available_hours_per_week",
    "employee_id",
    "employment_type",
    "start_date",
    "end_date",
    "seniority",
    "cost_center",
    "interests",
    "max_concurrent_tasks",
)

#: The private tier (spec §3.5, D-PC-3): self, or an `admin:members:manage`
#: holder, and nobody else. Deliberately keyed to the WRITE grant rather than
#: to `admin:members:read` — a manager seeing skills and capacity is the point
#: of the HR tier, and a manager seeing a colleague's emergency contact is not.
#: The tuple is re-exported by `routes/people/fields.py` so there is one list,
#: not two that agree today.
PRIVATE_FIELDS: tuple[str, ...] = (
    "phone",
    "emergency_contact",
    "personal_email",
    "birthday",
)


def _blank_hr() -> dict[str, Any]:
    """A fresh empty value per HR field — the projected-away form.

    Fresh containers, not a module-level dict: a shared `[]`/`{}` would be
    aliased into every projected model on every request.

    Built from :data:`HR_FIELDS` rather than typed out, so a field added to the
    tuple cannot be forgotten here — the failure mode being that the field
    stays *visible* to a caller the tuple says may not see it, which is the
    silent direction.
    """
    empty: dict[str, Any] = {"skills": [], "skills_source": {}, "interests": []}
    return {name: empty.get(name) for name in HR_FIELDS}


def _blank_private() -> dict[str, Any]:
    """The projected-away form of :data:`PRIVATE_FIELDS` — every one is None."""
    return dict.fromkeys(PRIVATE_FIELDS)


def _jsonb(value: Any) -> dict[str, Any] | None:
    """A ``JSONB`` column as the driver hands it back.

    Raw ``text()`` declares no column type, so asyncpg returns jsonb as a
    **string** and a model field typed ``dict`` would reject it. Same rule and
    same reason as ``routes/projects/core.from_jsonb`` and ``routes/crm/core``
    — per-package, because each package owns its own row mapper.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return value if isinstance(value, dict) else None


def _iso(value: Any) -> str | None:
    """A ``DATE`` column as a wire string. asyncpg hands back ``datetime.date``."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _row_to_person(row: Any, *, include_hr: bool, include_private: bool = False) -> OrgPersonModel:
    """Row → model, with the HR half carried or projected away.

    ``include_hr`` is keyword-only and has **no default**: every call site has
    to state which audience it is serving, so a route added later cannot
    inherit the permissive answer by omission.

    ``include_private`` does have one — ``False`` — and the asymmetry is
    deliberate rather than sloppy. It was added after every existing call site
    was written, and defaulting it to False means each of those keeps its
    behaviour exactly: the narrower tier is opt-in, so a call site that has
    never heard of §3.5 cannot start serving phone numbers by inheriting a
    permissive default. The two routes that mean to serve it pass it
    explicitly.

    The projection is at the SERIALIZATION layer, never in the SQL, for two
    reasons. (1) ``fetch_people_for_clarify`` runs its own query and must keep
    seeing everything — a WHERE/SELECT-level projection would silently degrade
    agent delegation. (2) The response SHAPE is unchanged: restricted fields
    come back null/empty rather than absent, so the frontend mapper and the
    generated TS type read the same object either way.
    """
    person = OrgPersonModel(
        id=str(row.id),
        name=row.name,
        email=row.email,
        role=row.role,
        title=getattr(row, "title", None),
        department=row.department,
        team=row.team,
        reports_to=row.reports_to,
        manager_id=str(row.manager_id) if getattr(row, "manager_id", None) else None,
        status=row.status or "active",
        skills=list(row.skills or []),
        skills_source=dict(getattr(row, "skills_source", None) or {}),
        domain=row.domain,
        resume_summary=row.resume_summary,
        years_experience=row.years_experience,
        capacity_hours_per_week=row.capacity_hours_per_week,
        current_load_hours_per_week=row.current_load_hours_per_week,
        available_hours_per_week=row.available_hours_per_week,
        provider_user_id=row.clickup_user_id,
        # §3.1 — self-described, directory-visible.
        preferred_name=getattr(row, "preferred_name", None),
        pronouns=getattr(row, "pronouns", None),
        location=getattr(row, "location", None),
        timezone=getattr(row, "timezone", None),
        working_hours=_jsonb(getattr(row, "working_hours", None)),
        bio=getattr(row, "bio", None),
        links=_jsonb(getattr(row, "links", None)),
        languages=list(getattr(row, "languages", None) or []),
        avatar=getattr(row, "avatar", None),
        avatar_updated_at=_iso(getattr(row, "avatar_updated_at", None)),
        # §3.2 — employment, HR tier.
        employee_id=getattr(row, "employee_id", None),
        employment_type=getattr(row, "employment_type", None),
        start_date=_iso(getattr(row, "start_date", None)),
        end_date=_iso(getattr(row, "end_date", None)),
        seniority=getattr(row, "seniority", None),
        cost_center=getattr(row, "cost_center", None),
        interests=list(getattr(row, "interests", None) or []),
        max_concurrent_tasks=getattr(row, "max_concurrent_tasks", None),
        # §3.5 — private tier.
        phone=getattr(row, "phone", None),
        emergency_contact=_jsonb(getattr(row, "emergency_contact", None)),
        personal_email=getattr(row, "personal_email", None),
        birthday=getattr(row, "birthday", None),
    )
    # `getattr` with a default throughout, and not `row.x`: the migration
    # applies BEFORE services restart (R6), but the reverse window exists too —
    # a hermetic row double, or a service that came up against a database the
    # ladder has not reached yet, has no such column. A mapper that raises
    # there turns a missing column into a 500 on the directory rather than a
    # blank field.
    projected: dict[str, Any] = {}
    if not include_hr:
        projected.update(_blank_hr())
    if not include_private:
        projected.update(_blank_private())
    return person.model_copy(update=projected) if projected else person


@router.get("/people", response_model=list[OrgPersonModel])
async def list_people(
    q: str = "",
    include_inactive: bool = False,
    user: UserContext = Depends(get_current_user),
):
    """The org's people. `q` filters by name/role/department — and by skill,
    but only for a caller who may see skills: matching on a column that is
    then stripped from the response turns the search box into an oracle for
    the field the projection exists to hide."""
    hr = can_read_hr_fields(user)
    clauses = ["true"] if include_inactive else ["status = 'active'"]
    params: dict[str, Any] = {}
    if q.strip():
        match = "(name ILIKE :q OR role ILIKE :q OR department ILIKE :q"
        if hr:
            match += " OR EXISTS (SELECT 1 FROM unnest(skills) s WHERE s ILIKE :q)"
        clauses.append(match + ")")
        params["q"] = f"%{q.strip()}%"
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT * FROM people WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY department, name"
                ),
                params,
            )
        ).fetchall()
        return [_row_to_person(r, include_hr=hr) for r in rows]


async def fetch_people_for_clarify(db: Any) -> list[dict[str, Any]]:
    """People dicts for the proposal heuristic: name/email/provider id +
    skills + availability + the reporting line (§5, Phase 2). Used by
    ai.clarify_item (org people first; the caller falls back to provider
    members when this is empty).

    ⚠️ **Deliberately outside the N4 read projection.** It takes ``db`` and no
    user because it is never reached through the router: every caller is an
    in-process server-side path (``ai.py``, ``capture_email.py``,
    ``planning.py``). It must keep returning FULL data — this is the
    capability-aware delegation the roster exists for, and narrowing it would
    degrade agent delegation silently rather than protect anybody.

    ``manager_name`` resolves the structured ``manager_id`` FK (a self-join),
    falling back to the free-text ``reports_to`` display name — so the clarify
    LLM can prefer same-team owners or route approvals up the chain."""
    try:
        rows = (
            await db.execute(
                text(
                    """SELECT p.id, p.name, p.email, p.clickup_user_id, p.skills,
                      p.available_hours_per_week, p.capacity_hours_per_week,
                      p.current_load_hours_per_week, p.role, p.title, p.domain,
                      p.years_experience, p.reports_to, p.department, p.team,
                      m.name AS manager_name
                 FROM people p
                 LEFT JOIN people m ON m.id = p.manager_id
                WHERE p.status = 'active'"""
                )
            )
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "email": r.email,
            "provider_user_id": r.clickup_user_id,
            "skills": list(r.skills or []),
            "available_hours_per_week": r.available_hours_per_week,
            "capacity_hours_per_week": r.capacity_hours_per_week,
            "current_load_hours_per_week": r.current_load_hours_per_week,
            "role": r.role,
            "title": getattr(r, "title", None),
            "domain": r.domain,
            "years_experience": r.years_experience,
            "department": r.department,
            "team": r.team,
            "reports_to": (r.manager_name or r.reports_to),
        }
        for r in rows
    ]


# ── Write surface: the app is now the source of truth for HR data ─────────────
# (user decision 2026-07-16). Edits stamp source='manual'/'resume' + updated_by.
#
# Being the source of truth for HR data is exactly why these are ADMIN writes:
# every route below carries `require_people_write()` (`admin:members:manage`)
# as a route dependency — N4, owner-answered 2026-08-04. `POST /people/embed`
# in capability.py is the fourth; count routes, not call sites.


class PersonWrite(BaseModel):
    """Create/update payload. All optional on PATCH; `name` required on create.

    **This model's field set IS the writable surface**, and
    ``routes/people/fields.WRITABLE_FIELDS`` classifies exactly the same names —
    pinned by ``test_people_profile.py``. A field the payload accepts and the
    class map has never heard of is a field with no owner and therefore no gate;
    a field in the map that the payload cannot carry is a permission granted for
    something nobody can do. Both directions are failures, so the test compares
    the two sets rather than one containment.
    """

    name: str | None = None
    email: str | None = None
    role: str | None = None
    title: str | None = None
    department: str | None = None
    team: str | None = None
    reports_to: str | None = None
    manager_id: str | None = None
    status: str | None = None
    skills: list[str] | None = None
    domain: str | None = None
    resume_summary: str | None = None
    years_experience: int | None = None
    capacity_hours_per_week: int | None = None
    current_load_hours_per_week: int | None = None
    clickup_user_id: str | None = None
    # ── The profile (WS-28g / P-3) ──────────────────────────────────────────
    # §3.1 (self class)
    preferred_name: str | None = None
    pronouns: str | None = None
    location: str | None = None
    timezone: str | None = None
    working_hours: dict[str, Any] | None = None
    bio: str | None = None
    links: dict[str, Any] | None = None
    languages: list[str] | None = None
    interests: list[str] | None = None
    max_concurrent_tasks: int | None = None
    # §3.2 (admin class)
    employee_id: str | None = None
    employment_type: str | None = None
    start_date: str | None = None  # ISO 'YYYY-MM-DD'
    end_date: str | None = None
    seniority: str | None = None
    cost_center: str | None = None
    personal_email: str | None = None
    # §3.5 (self class, private read tier)
    phone: str | None = None
    emergency_contact: dict[str, Any] | None = None
    birthday: str | None = None  # 'MM-DD' — D-PC-9


#: Columns the update builder passes straight through: no cast, no shaping.
_PLAIN_UPDATE_COLUMNS: tuple[str, ...] = (
    "name",
    "email",
    "role",
    "title",
    "department",
    "team",
    "reports_to",
    "status",
    "domain",
    "resume_summary",
    "years_experience",
    "clickup_user_id",
    "preferred_name",
    "pronouns",
    "location",
    "timezone",
    "bio",
    "employee_id",
    "employment_type",
    "seniority",
    "cost_center",
    "personal_email",
    "phone",
    "birthday",
    "max_concurrent_tasks",
)

#: JSONB columns. `text()` declares no column type, so asyncpg needs both the
#: cast and a JSON *string* — a bare dict has no codec (the defect
#: `tests/live/live_ws27l.py` exists to pin).
_JSONB_UPDATE_COLUMNS: tuple[str, ...] = (
    "working_hours",
    "links",
    "emergency_contact",
)

#: `TEXT[]` columns. Bound as a Python list — never json-encoded, which would
#: store the literal string '["a"]' in a text array.
_ARRAY_UPDATE_COLUMNS: tuple[str, ...] = ("languages", "interests")

#: `DATE` columns. Parsed into `datetime.date` and bound plainly rather than
#: written as `CAST(:x AS date)` over a string — asyncpg refuses that shape,
#: which is the WS-27k defect recorded in `tests/live/README.md`.
_DATE_UPDATE_COLUMNS: tuple[str, ...] = ("start_date", "end_date")

#: The WS-28g columns — the ones `create_person`'s INSERT (written in 2026-07)
#: does not carry, and which it therefore applies through the shared builder
#: immediately after. Derived from the payload model rather than listed twice,
#: so a column added to `PersonWrite` cannot be silently dropped on create:
#: it is "everything writable that the create INSERT does not name".
_CREATE_INSERT_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "email",
        "role",
        "title",
        "department",
        "team",
        "reports_to",
        "manager_id",
        "status",
        "skills",
        "domain",
        "resume_summary",
        "years_experience",
        "capacity_hours_per_week",
        "current_load_hours_per_week",
        "clickup_user_id",
    }
)

_PROFILE_ONLY_COLUMNS: frozenset[str] = frozenset(PersonWrite.model_fields) - _CREATE_INSERT_COLUMNS


def _available(capacity: int | None, load: int | None) -> int | None:
    """Free hours = capacity - load, floored at 0. None when capacity unknown."""
    if capacity is None:
        return None
    return max(0, capacity - (load or 0))


def _as_date(column: str, value: Any) -> Any:
    """``'2026-08-13'`` → :class:`datetime.date`, or 400 naming the column.

    Bound as a real date rather than written as ``CAST(:x AS date)`` over a
    string: asyncpg refuses that shape, and it refuses it at request time with
    a driver error the caller cannot act on. That exact defect is what
    ``tests/live/live_ws27k.py`` exists to pin, and this is the same fix
    ``routes/projects/core.coerce_write_values`` applies package-side.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"'{column}' is not a valid ISO date (YYYY-MM-DD): {value!r}.",
        ) from exc


def build_person_update(
    fields: dict[str, Any],
    row: Any,
    *,
    actor: str,
) -> tuple[list[str], dict[str, Any]]:
    """``SET`` clauses + binds for a person update. **One builder, two doors.**

    Both write doors run through here: ``PATCH /tasks/people/{id}`` (admin
    only, unchanged since WS-28b-write) and ``PATCH /people/{id}`` (admin *or*
    the subject, WS-28g). They differ in *who may call them and with which
    fields* — which is the field-class authority's job, applied in front of
    this — and not at all in how a column reaches the database. Two builders
    would be two places for a JSONB cast to be forgotten, and the one that
    forgot it would fail at Postgres rather than in a test.

    ``fields`` is already ``exclude_unset``, so *absent* and *set to null* stay
    distinguishable: a PATCH that never mentions ``phone`` must not erase it.
    """
    set_parts: list[str] = ["updated_at = now()", "updated_by = :updated_by"]
    params: dict[str, Any] = {"updated_by": actor}

    for col in _PLAIN_UPDATE_COLUMNS:
        if col in fields:
            set_parts.append(f"{col} = :{col}")
            params[col] = fields[col]
    for col in _JSONB_UPDATE_COLUMNS:
        if col in fields:
            set_parts.append(f"{col} = CAST(:{col} AS JSONB)")
            value = fields[col]
            params[col] = None if value is None else json.dumps(value)
    for col in _ARRAY_UPDATE_COLUMNS:
        if col in fields:
            set_parts.append(f"{col} = :{col}")
            params[col] = [str(v).strip() for v in (fields[col] or []) if str(v).strip()]
    for col in _DATE_UPDATE_COLUMNS:
        if col in fields:
            set_parts.append(f"{col} = :{col}")
            params[col] = _as_date(col, fields[col])

    if "manager_id" in fields:
        set_parts.append("manager_id = CAST(:manager_id AS UUID)")
        params["manager_id"] = fields["manager_id"] or None
    if "skills" in fields:
        skills = [s.strip() for s in (fields["skills"] or []) if s and s.strip()]
        prior = dict(getattr(row, "skills_source", None) or {})
        src = {s: prior.get(s, "manual") for s in skills}
        set_parts.append("skills = :skills")
        set_parts.append("skills_source = CAST(:skills_source AS JSONB)")
        params["skills"] = skills
        params["skills_source"] = json.dumps(src)

    # Recompute free hours whenever capacity or load moves.
    cap = fields.get("capacity_hours_per_week", getattr(row, "capacity_hours_per_week", None))
    load = fields.get(
        "current_load_hours_per_week", getattr(row, "current_load_hours_per_week", None)
    )
    if "capacity_hours_per_week" in fields:
        set_parts.append("capacity_hours_per_week = :capacity")
        params["capacity"] = fields["capacity_hours_per_week"]
    if "current_load_hours_per_week" in fields:
        set_parts.append("current_load_hours_per_week = :load")
        params["load"] = fields["current_load_hours_per_week"]
    if "capacity_hours_per_week" in fields or "current_load_hours_per_week" in fields:
        set_parts.append("available_hours_per_week = :available")
        params["available"] = _available(cap, load)
    return set_parts, params


async def _get_person_row(db: Any, person_id: str) -> Any:
    row = (
        await db.execute(text("SELECT * FROM people WHERE id = :id"), {"id": person_id})
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return row


def _validate_status(status: str | None) -> None:
    """Refuse a status the database will refuse, and say what is allowed.

    Migration 148 gave `people.status` a CHECK. Without this the route
    would hand an illegal value straight to Postgres, which answers with a
    `CheckViolation` — a 500 whose text names a constraint rather than the four
    words the caller may type. The vocabulary is imported (never re-listed), so
    a fifth status cannot be accepted here and rejected there.
    """
    if status is not None and status not in PEOPLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status '{status}'. One of: {list(PEOPLE_STATUSES)}.",
        )


def _clean_email(email: str | None) -> str | None:
    """Trim, and turn a blank into NULL.

    `''` is not NULL to Postgres, so two people saved with an empty address
    would collide under 148's partial unique index — the one case the migration
    had to clean up in the existing data, and it is worth not re-creating it
    one row at a time. A trailing space is the same defect wearing a disguise:
    it makes two identical addresses look distinct to `lower(email)`.
    """
    clean = (email or "").strip()
    return clean or None


async def _email_taken_by(
    db: Any, email: str | None, *, exclude_id: str | None = None
) -> str | None:
    """The name of the person already holding this address, or ``None``.

    Migration 148 put a partial UNIQUE on `lower(email)` so an email→person
    join is unambiguous; migration 209 made it per-organization (H-125), and
    this query carries the same tenant predicate so the two agree. That makes a duplicate address a *database* error —
    an opaque 500 at exactly the moment an admin is typing somebody in. Checked
    here so the answer is a 409 that names the other row, which is the only
    form of this message anyone can act on.

    Case-insensitive on both sides (R10), matching the index's `lower(email)`.
    ``exclude_id`` is what lets a PATCH re-save a person's own address.
    """
    clean = (email or "").strip().lower()
    if not clean:
        return None
    # ⚠️ Tenant-scoped since migration 209 (H-125). The index this check
    # mirrors is per-organization now, so an unscoped lookup would report a
    # 409 naming somebody at ANOTHER customer — a refusal the administrator
    # cannot act on, and a disclosure of a name they should never see.
    sql = ("SELECT name FROM people "
           " WHERE lower(email) = :email "
           "   AND organization_id = CAST(NULLIF("
           "         current_setting('app.tenant_id', true), '') AS uuid)")
    params: dict[str, Any] = {"email": clean}
    if exclude_id:
        sql += " AND id <> CAST(:exclude AS UUID)"
        params["exclude"] = exclude_id
    row = (await db.execute(text(sql + " LIMIT 1"), params)).fetchone()
    return str(row.name) if row is not None else None


@router.post(
    "/people", response_model=OrgPersonModel, status_code=201, dependencies=[require_people_write()]
)
async def create_person(
    body: PersonWrite,
    user: UserContext = Depends(get_current_user),
):
    """Add a person to the org (manual entry). `name` is required. The EMAIL
    is what has to be unique.

    Admin-only (``admin:members:manage``).

    ⚠️ **NOTHING IN THE PRODUCT CALLS THIS, and that is deliberate — do not
    "restore" the missing button.** Owner directive, 2026-09-21: people enter
    the organization in ONE place, which is Organisation, and the directory
    follows from membership by trigger (migration 206). The People app's
    "Add person" control was removed in the same change. An external
    collaborator is an invite with the ``guest`` role, not a row typed here.

    The route is kept rather than deleted because it is the door an importer
    or a re-enabled form would use, and because a working admin endpoint with
    no UI is not the same defect as a second door in the product. Its fate is
    H-140.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    _validate_status(body.status)
    # The vocabularies migration 172 deliberately did NOT put in a CHECK
    # (D-PC-8/P-6) — that is `172_people_profile.sql`, PEOPLE's vocabularies
    # (`employment_type`, `seniority`), NOT the Projects ones in 175, which
    # never touches `people`: enforced in the route against ONE tuple, the same shape
    # `_validate_status` takes for 148's CHECK, so a bad value is a 400 that
    # lists the legal words rather than a 500 naming a constraint.
    validate_person_vocabularies(body.model_dump(exclude_unset=True))
    skills = [s.strip() for s in (body.skills or []) if s and s.strip()]
    skills_source = {s: "manual" for s in skills}
    available = _available(body.capacity_hours_per_week, body.current_load_hours_per_week)
    pid = str(uuid4())
    async with _tenant_session() as db:
        # Duplicate NAMES are allowed, and refusing them was the bug.
        # Migration 148 dropped `UNIQUE(name)` on the argument that two real
        # people share a name and one of them was being locked out of the
        # directory; keeping a route-level 409 would have preserved exactly the
        # behaviour the migration was written to remove. What must be unique is
        # the address, because that is the join key.
        holder = await _email_taken_by(db, body.email)
        if holder:
            raise HTTPException(
                status_code=409, detail=f"{body.email} already belongs to {holder}."
            )
        await db.execute(
            text(
                """INSERT INTO people
               (id, name, email, role, title, department, team, reports_to,
                manager_id, status, skills, skills_source, domain, resume_summary,
                years_experience, capacity_hours_per_week,
                current_load_hours_per_week, available_hours_per_week,
                clickup_user_id, source, updated_by, updated_at)
               VALUES
               (:id, :name, :email, :role, :title, :department, :team, :reports_to,
                CAST(:manager_id AS UUID), :status, :skills,
                CAST(:skills_source AS JSONB), :domain, :resume_summary,
                :years_experience, :capacity, :load, :available,
                :clickup_user_id, 'manual', :updated_by, now())"""
            ),
            {
                "id": pid,
                "name": name,
                "email": _clean_email(body.email),
                "role": body.role,
                "title": body.title,
                "department": body.department,
                "team": body.team,
                "reports_to": body.reports_to,
                "manager_id": body.manager_id,
                "status": body.status or "active",
                "skills": skills,
                "skills_source": json.dumps(skills_source),
                "domain": body.domain,
                "resume_summary": body.resume_summary,
                "years_experience": body.years_experience,
                "capacity": body.capacity_hours_per_week,
                "load": body.current_load_hours_per_week,
                "available": available,
                "clickup_user_id": body.clickup_user_id,
                "updated_by": _uid(user),
            },
        )
        # The INSERT above carries the columns that existed before WS-28g. The
        # profile columns are applied by the shared builder in the SAME
        # transaction rather than by extending the INSERT — one place decides
        # how a JSONB, an array or a date reaches Postgres, and a create that
        # accepted `timezone` in its payload and dropped it on the floor is
        # precisely the silent-discard D-PC-5 refuses.
        extra = {
            k: v
            for k, v in body.model_dump(exclude_unset=True).items()
            if k in _PROFILE_ONLY_COLUMNS
        }
        if extra:
            set_parts, params = build_person_update(
                extra, await _get_person_row(db, pid), actor=_uid(user)
            )
            params["id"] = pid
            await db.execute(
                text(f"UPDATE people SET {', '.join(set_parts)} WHERE id = :id"), params
            )
        if skills:
            # WS-28h / D-PC-6: the child table is the source, the array the
            # projection — so a create that carries skills seeds the table in
            # the SAME transaction, or the very first save would already
            # disagree with it.
            from gateway.person_skills import sync_from_array

            await sync_from_array(db, pid, skills, _uid(user))
        person = await _get_person_row(db, pid)
    # The insert is committed above; the capability re-embed runs AFTER it in
    # its own transaction (best-effort — an embedding hiccup never fails the
    # write that already committed; H2 restructure).
    async with _tenant_session() as db:
        await _reembed_capability(db, pid)
    # include_hr: the route gate already proved this caller is an admin.
    # The route gate already proved this caller holds
    # `admin:members:manage`, which is the private tier's key (D-PC-3).
    return _row_to_person(person, include_hr=True, include_private=True)


@router.patch(
    "/people/{person_id}", response_model=OrgPersonModel, dependencies=[require_people_write()]
)
async def update_person(
    person_id: str,
    body: PersonWrite,
    user: UserContext = Depends(get_current_user),
):
    """Edit a person (title/role/manager/skills/capacity/ClickUp link). Skills
    replace the array; each skill keeps its prior provenance, new ones = manual.

    Admin-only (`admin:members:manage`)."""
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields:
        _validate_status(fields["status"])
    validate_person_vocabularies(fields)
    if "email" in fields:
        fields["email"] = _clean_email(fields["email"])
    async with _tenant_session() as db:
        row = await _get_person_row(db, person_id)
        # `exclude_id` is why this is not the create-path check: re-saving a
        # person without touching their address must not report them as their
        # own duplicate.
        if "email" in fields:
            holder = await _email_taken_by(db, fields["email"], exclude_id=person_id)
            if holder:
                raise HTTPException(
                    status_code=409, detail=f"{fields['email']} already belongs to {holder}."
                )
        set_parts, params = build_person_update(fields, row, actor=_uid(user))
        params["id"] = person_id
        await db.execute(
            text(f"UPDATE people SET {', '.join(set_parts)} WHERE id = :id"), params
        )
        if "skills" in fields:
            # WS-28h / D-PC-6: reconcile the structured table to the flat list
            # in the SAME transaction. Retained skills keep their level/years —
            # a flat save through this older door must never strip what the
            # structured editor set — and the projection re-derives the array,
            # so it cannot be observed disagreeing with the table.
            from gateway.person_skills import sync_from_array

            await sync_from_array(db, person_id, fields["skills"] or [], _uid(user))
        person = await _get_person_row(db, person_id)
    # The edit is committed above; the capability re-embed runs AFTER it in its
    # own transaction (best-effort; H2 restructure).
    async with _tenant_session() as db:
        await _reembed_capability(db, person_id)
    # The route gate already proved this caller holds
    # `admin:members:manage`, which is the private tier's key (D-PC-3).
    return _row_to_person(person, include_hr=True, include_private=True)


class ResumeIngestResult(BaseModel):
    resume_id: str
    added_skills: list[str]
    extracted: dict[str, Any]
    person: OrgPersonModel


@router.post(
    "/people/{person_id}/resume",
    response_model=ResumeIngestResult,
    dependencies=[require_people_write()],
)
async def ingest_resume(
    person_id: str,
    file: UploadFile,
    user: UserContext = Depends(get_current_user),
):
    """Upload a résumé (PDF/DOCX/TXT), parse it, and MERGE the extracted skills +
    profile into the person — 'ingest résumés to automatically update skills'.

    Admin-only (`admin:members:manage`)."""
    fname = _safe_name(file.filename or "resume")
    ext = Path(fname).suffix.lower()
    if ext not in _RESUME_EXT:
        raise HTTPException(
            status_code=400, detail=f"Unsupported résumé type '{ext}'. Use PDF, DOCX or TXT."
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > _RESUME_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Résumé too large (max 15 MB).")

    async with _tenant_session() as db:
        # Existence check only — the merge itself reads the TABLE now (WS-28h),
        # not this row's array.
        await _get_person_row(db, person_id)
        # Vocabulary = every skill the org already knows (broadens keyword hits).
        vocab_rows = (
            await db.execute(text("SELECT DISTINCT unnest(skills) AS s FROM people"))
        ).fetchall()
        known = [r.s for r in vocab_rows if r.s]
        parsed = await parse_resume(content, fname, file.content_type, known)

        # Store the file next to task attachments (owner-checked dir).
        rid = str(uuid4())
        dest = _storage_dir() / f"resume_{rid}{ext}"
        dest.write_bytes(content)

        extracted = {
            "skills": parsed["skills"],
            "credentials": parsed.get("credentials", []),
            "experience_summary": parsed.get("experience_summary"),
            "years_experience": parsed.get("years_experience"),
            "domain": parsed.get("domain"),
        }
        await db.execute(
            text(
                """INSERT INTO people_resumes
               (id, person_id, filename, mime, size_bytes, storage_path,
                parsed_text, extracted, uploaded_by)
               VALUES (:id, :pid, :fn, :mime, :size, :path, :ptext,
                       CAST(:extracted AS JSONB), :by)"""
            ),
            {
                "id": rid,
                "pid": person_id,
                "fn": fname,
                "mime": file.content_type,
                "size": len(content),
                "path": str(dest),
                "ptext": parsed.get("text", "")[:200000],
                "extracted": json.dumps(extracted),
                "by": _uid(user),
            },
        )

        # WS-28h / D-PC-6: the parse writes STRUCTURED rows — skills as
        # evidence='resume' child rows, credentials deduplicated — and the
        # array is re-projected from the table in the same transaction, not
        # merged by hand here. Add-only: a résumé is evidence for what it
        # contains and silent about everything else, so nothing a human put in
        # is removed, and a skill already present keeps its level untouched.
        from gateway.person_skills import merge_from_resume

        added = await merge_from_resume(
            db, person_id, parsed["skills"], parsed.get("credentials", []), _uid(user)
        )
        # Fill summary/years/domain only when currently empty.
        await db.execute(
            text(
                """UPDATE people SET
                 resume_summary = COALESCE(resume_summary, :summary),
                 years_experience = COALESCE(years_experience, :years),
                 domain = COALESCE(domain, :domain),
                 updated_by = :by, updated_at = now()
               WHERE id = :id"""
            ),
            {
                "summary": parsed.get("experience_summary"),
                "years": parsed.get("years_experience"),
                "domain": parsed.get("domain"),
                "by": _uid(user),
                "id": person_id,
            },
        )
        person = await _get_person_row(db, person_id)
    # The résumé + merge are committed above. New skills / résumé depth change
    # the capability text → re-embed, AFTER the commit, in its own transaction
    # (best-effort; H2 restructure).
    async with _tenant_session() as db:
        await _reembed_capability(db, person_id)
    return ResumeIngestResult(
        resume_id=rid,
        added_skills=added,
        extracted=extracted,
        person=_row_to_person(person, include_hr=True, include_private=True),
    )


async def _reembed_capability(db: Any, person_id: str) -> None:
    """Refresh a person's semantic capability vector after an edit (best-effort,
    no-op when semantic matching is off). Isolated + swallowed so an embedding
    hiccup never fails the write that already committed."""
    try:
        from gateway.routes.tasks.capability import embed_person

        await embed_person(db, person_id)
    except Exception:
        pass
