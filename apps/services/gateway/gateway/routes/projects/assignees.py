"""Projects · the assignee picker, directory-backed (WS-28e).

Spec: ``project-docs/specs/people_center_app.md`` §6.1 · D-PM-4 · D-PC-12.

    GET /projects/assignees?q=…&due=…   → people + agents a task can go to

**Backed by the directory, not by ``app_user``** — the whole point. Three rules
from §6.1, each of which the old free-text input silently got wrong:

* **Directory-only people are offered.** A contractor with no login can hold a
  task and appear on a board; they simply cannot sign in to see it (D-PC-12).
  Hiding them would make the directory's contractor story unusable. The row
  says "no login" so the assigner knows the task will not notify them.
* **Agents appear in the same picker, under their own heading** — D-PM-4's
  one-vocabulary decision made visible: handing work to an agent is the same
  gesture as handing it to a colleague.
* **Every row carries why this person is or is not a good idea right now** —
  away until the 20th, more committed than contracted, engagement ending
  before the due date. **Shown, never enforced**: the picker warns and still
  lets you assign, because the assigner knows things the record does not.

**The HR tier is projected by the CALLER's grants, here as everywhere.** The
gate on this router is ``feature:projects``; skills, load and contracted hours
additionally need ``admin:members:read`` (§4.2) and come back absent — with
``hr_visible: false`` saying so — for a caller without it. The directory half
(name, title, away, has-login) is what §3.1 already calls directory tier.

Read-only: this module suggests and never assigns (D-PC-13). The assignment
itself stays with the ordinary task write path.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.projects.core import router
from gateway.routes.tasks.core import can_read_hr_fields
from pydantic import BaseModel
from sqlalchemy import text

#: Suggestion caps. A picker is a short list, not a directory browse — the
#: directory app exists for that.
MAX_PEOPLE = 8
MAX_AGENTS = 6
#: "Engagement ends soon" horizon when no due date is passed.
END_DATE_HORIZON_DAYS = 30
TOP_SKILLS = 3


class PickerRow(BaseModel):
    #: The value the task write stores — an email, or ``agent:<name>``.
    assignee: str
    name: str
    kind: str = "person"                       # person | agent
    title: str | None = None
    department: str | None = None
    avatar: str | None = None
    #: False = directory-only (D-PC-12): can hold the task, cannot sign in.
    has_login: bool = True
    away: dict[str, Any] | None = None
    #: HR tier — absent without ``admin:members:read``.
    top_skills: list[str] = []
    load: dict[str, Any] | None = None
    contracted_hours: float | None = None
    #: §6.1's one line: why this is or is not a good idea right now.
    warnings: list[str] = []
    description: str | None = None             # agents only


class PickerResponse(BaseModel):
    people: list[PickerRow]
    agents: list[PickerRow]
    #: False when the capacity half was projected away — "no load shown" and
    #: "no load exists" must not read identically.
    hr_visible: bool


#: How many addresses one label lookup may resolve. A board shows a few dozen
#: assignees at the very most; a caller asking for thousands is not labelling
#: a screen.
MAX_NAME_LOOKUP = 200


class NamesResponse(BaseModel):
    #: lowercased address -> display name. An address the directory does not
    #: know is ABSENT rather than mapped to itself: "no name" and "named after
    #: their own address" are different facts, and only the client knows what
    #: to fall back to.
    names: dict[str, str]


@router.get("/people/names", response_model=NamesResponse)
async def person_names(
    emails: str = "",
    user: UserContext = Depends(get_current_user),
) -> NamesResponse:
    """Resolve assignee addresses to the names people actually read.

    ## Why this is not `/assignees`

    They answer different questions. `/assignees` SUGGESTS who to give work
    to: it is capped at eight, ordered by name, and pays for availability,
    load, skills and warnings per row because an assigner needs them.

    This resolves addresses a caller ALREADY HOLDS — the assignees on the
    cards in front of them — and needs exactly one column. Routing it through
    the picker would mean either computing all that machinery for rows nobody
    is choosing between, or threading a "cheap mode" flag through it. The
    second is how one endpoint becomes two endpoints wearing one name.

    ## Why the client cannot do without it

    `pm_tasks` stores an assignee as an ADDRESS (`tasks.py`: "an email or
    `agent:<name>` — one vocabulary for both"), so a task read carries no
    name. Every Projects surface therefore showed the address's local part,
    which reads as a name often enough that nobody noticed: `priya` looks
    deliberate, `p.sharma` does not. Owner reported it on 2026-09-21.

    ⚠️ **Unknown addresses are simply missing from the map.** They are not an
    error: a task can carry somebody the directory has never had a row for —
    a former colleague, a typo, an address from an import — and a lookup that
    404s on one of fifty would make the whole board unlabellable.
    """
    wanted = [
        part.strip().lower()
        for part in (emails or "").split(",")
        if part.strip() and not part.strip().startswith("agent:")
    ][:MAX_NAME_LOOKUP]
    if not wanted:
        return NamesResponse(names={})

    # Imported here, not at module scope: the picker below does the same, and
    # the reason is the import cycle between this package and `routes.people`.
    from gateway.routes.people.core import _tenant_session

    async with _tenant_session() as db:
        rows = (await db.execute(text(
            "SELECT lower(email) AS email, name FROM people "
            " WHERE lower(email) = ANY(:emails) AND name <> ''"),
            {"emails": wanted})).fetchall()
    return NamesResponse(
        names={str(r.email): str(r.name) for r in rows if r.name},
    )


@router.get("/assignees", response_model=PickerResponse)
async def suggest_assignees(
    q: str = "",
    due: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> PickerResponse:
    """People and agents matching ``q``, with the facts an assigner needs.

    ``due`` (ISO date, optional) sharpens the engagement-end warning: an
    engagement ending *before the task is due* is a different sentence from
    one ending soon.
    """
    from gateway.routes.people.absences import away_today
    from gateway.routes.people.core import _tenant_session, compute_load
    from gateway.work_schedule import (
        contracted_hours_per_week,
        load_policy,
        person_schedule,
    )

    hr = can_read_hr_fields(user)
    needle = (q or "").strip().lower()
    today = date.today()
    due_on: date | None = None
    if due:
        try:
            due_on = date.fromisoformat(due)
        except ValueError:
            due_on = None
    horizon = due_on or (today + timedelta(days=END_DATE_HORIZON_DAYS))

    async with _tenant_session() as db:
        clauses = ["status = 'active'"]
        params: dict[str, Any] = {}
        if needle:
            clauses.append(
                "(name ILIKE :q OR email ILIKE :q OR title ILIKE :q)")
            params["q"] = f"%{needle}%"
        rows = (await db.execute(text(
            "SELECT id, name, email, title, department, avatar, end_date, "
            "       working_hours, skills "
            "  FROM people WHERE " + " AND ".join(clauses) +
            " ORDER BY name LIMIT :cap"),
            {**params, "cap": MAX_PEOPLE})).fetchall()

        away = await away_today(db, [str(r.id) for r in rows])
        policy = await load_policy(db) if hr else None
        logins = set()
        emails = [str(r.email).strip().lower() for r in rows
                  if getattr(r, "email", None)]
        if emails:
            # ⚠️ Scoped to the bound tenant, like `people.core.has_login`.
            # `app_user.email` is GLOBALLY unique (D-MT-1 (a)), so without
            # this the picker draws "has login" for an address that is a
            # member of ANOTHER customer — telling this one that the address
            # exists elsewhere, and hiding the "no login — cannot see the
            # task" warning D-PC-12 exists to show. `NULLIF(…, '')` fails
            # closed.
            found = (await db.execute(text(
                "SELECT lower(email) AS email FROM app_user "
                " WHERE lower(email) = ANY(:emails) "
                "   AND organization_id = CAST(NULLIF("
                "         current_setting('app.tenant_id', true), '') AS uuid)"),
                {"emails": emails})).fetchall()
            logins = {f.email for f in found}

        people: list[PickerRow] = []
        for row in rows:
            email = (getattr(row, "email", None) or "").strip().lower()
            if not email:
                # No address means no assignee value to store: assignment
                # targets are strings, and an empty one assigns work to nobody.
                continue
            warnings: list[str] = []
            away_now = away.get(str(row.id))
            if away_now:
                warnings.append(
                    f"Away ({away_now['kind']}) until {away_now['until']}")
            end = getattr(row, "end_date", None)
            if end is not None and end <= horizon:
                warnings.append(
                    f"Engagement ends {end.isoformat()}"
                    + (" — before this is due" if due_on and end < due_on
                       else ""))

            load = None
            contracted = None
            skills: list[str] = []
            if hr:
                load = await compute_load(db, email)
                contracted = contracted_hours_per_week(
                    person_schedule(policy, row))
                skills = [s for s in (getattr(row, "skills", None) or [])
                          if s][:TOP_SKILLS]
                if (contracted and load
                        and load["estimated_hours"] > contracted):
                    warnings.append(
                        f"{load['estimated_hours']}h committed against a "
                        f"{contracted}h week")

            people.append(PickerRow(
                assignee=email, name=row.name, kind="person",
                title=getattr(row, "title", None),
                department=getattr(row, "department", None),
                avatar=getattr(row, "avatar", None),
                has_login=email in logins,
                away=away_now, top_skills=skills, load=load,
                contracted_hours=contracted, warnings=warnings,
            ))

        agents = await _agent_rows(db, needle)

    return PickerResponse(people=people, agents=agents, hr_visible=hr)


async def _agent_rows(db: Any, needle: str) -> list[PickerRow]:
    """Agents — same picker, own heading (D-PM-4). Best-effort: a database
    without the registry answers "no agents", never a 500 in the picker."""
    try:
        rows = (await db.execute(text(
            "SELECT name, description FROM dynamic_agents "
            " ORDER BY name"))).fetchall()
    except Exception:
        return []
    out: list[PickerRow] = []
    for row in rows:
        name = (row.name or "").strip()
        if not name:
            continue
        if needle and needle not in name.lower() and needle not in (
                row.description or "").lower():
            continue
        out.append(PickerRow(
            assignee=f"agent:{name}", name=name, kind="agent",
            description=(row.description or "").strip()[:160] or None,
            has_login=True, warnings=[],
        ))
        if len(out) >= MAX_AGENTS:
            break
    return out
