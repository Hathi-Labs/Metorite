"""People Center · the router, the gate, and the seams it borrows.

Spec: ``project-docs/specs/people_center_app.md`` §3, §6 · ticket WS-28b.

**Two permissions, and they answer different questions.**
``feature:people`` decides whether the directory is reachable at all;
``admin:members:read`` decides whether the HR-sensitive half of a person record
comes back filled in or nulled. The second one is **not defined here** — it is
imported from ``routes/tasks/core`` where WS-24 N4 put it. A second definition
of "may this caller see skills" is a second answer waiting to drift from the
first, and the projection is a boundary.
"""

from __future__ import annotations

from typing import Any

from acb_auth import require_feature_router
from acb_common import get_logger
from fastapi import APIRouter

# The shared seam (BO-10 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased here for the same reason every
# converted package aliases it: submodules import it from this module BY NAME,
# which is the seam the hermetic tests patch per module. The tenant comes from
# the request context — bound centrally in `_with_resolved_access` — so no
# call site passes one (H2). A call outside a bound request raises
# `TenantUnbound` rather than defaulting: fail closed, never "the usual org".
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from gateway.routes.people.fields import editable_fields
from gateway.routes.tasks.core import (
    PEOPLE_STATUSES,
    can_manage_people,
    can_read_hr_fields,
)
from gateway.routes.tasks.people import _row_to_person
from gateway.work_schedule import (
    absent_on,
    capacity_disagreement,
    contracted_hours_per_week,
    load_policy,
    person_schedule,
    working_hours_between,
)
from sqlalchemy import text

_log = get_logger("gateway.people")

router = APIRouter(
    prefix="/people", tags=["people"],
    dependencies=[require_feature_router("people")],
)


#: Statuses the directory understands — the SAME tuple the write routes
#: validate against, not a copy of it. Listed so a bad filter value is an empty
#: result rather than an error, and so the UI can render the pill set (and the
#: editor's status select) without a second round trip.
STATUSES: tuple[str, ...] = PEOPLE_STATUSES


def is_self(user: Any, person_email: str | None) -> bool:
    """Is the caller the person this row is about? (spec §2, **D-PC-1**)

    The self predicate, computed in exactly ONE place. It is not a permission —
    a new slug is nobody's grant until an admin creates it — it is a row match
    on the same lowercased address ``has_login`` joins on, seen from the other
    side. The badge the person page draws and the right to edit that row are
    therefore the same fact, and cannot disagree.

    **It is safe only because migration 148 made ``lower(email)`` partially
    unique.** Before it, two rows could carry one address and "edit yourself"
    could have edited somebody else. Do not weaken that index.

    Fails closed on every missing half: no caller address, no row address, or an
    unresolved principal all answer False. A directory-only person has no self
    by construction (D-PC-12) — there is no caller to match.
    """
    mine = (getattr(user, "email", None) or "").strip().lower()
    theirs = (person_email or "").strip().lower()
    return bool(mine and theirs and mine == theirs)


async def find_self_row(db: Any, user: Any) -> Any:
    """The caller's own ``gtd_people`` row, or ``None``.

    The self predicate run as a query rather than over rows already in hand —
    used by ``/people/me`` and by the directory's ``self_person_id``. Resolved
    independently of whatever list is being rendered, because "which row is me"
    must not depend on whether my row survived the caller's search filter.

    At most one row can match: 148's partial unique index on ``lower(email)``
    is what makes that a guarantee rather than a hope, and ``LIMIT 1`` here is
    belt-and-braces against a database where the index was never applied.
    """
    mine = (getattr(user, "email", None) or "").strip().lower()
    if not mine:
        return None
    return (await db.execute(
        text("SELECT * FROM gtd_people WHERE lower(email) = :email LIMIT 1"),
        {"email": mine},
    )).fetchone()


async def has_login(db: Any, email: str | None) -> bool:
    """Does this person have an ``app_user`` row — i.e. can they sign in?

    The visible half of §2's two-store split. Case-insensitive on both sides
    (R10), and a person with no address is directory-only by construction
    rather than by lookup.
    """
    if not (email or "").strip():
        return False
    # ⚠️ **Scoped to the bound tenant.** `app_user.email` is GLOBALLY unique
    # (D-MT-1 (a)), so an unscoped existence check answers "is this address a
    # member anywhere in the deployment" — which is both the wrong question
    # and a disclosure: it tells one customer that an address belongs to
    # another. The question this badge asks is "can they sign in HERE".
    # `NULLIF(…, '')` fails CLOSED: an unbound GUC matches no row.
    row = (await db.execute(
        text("SELECT 1 FROM app_user "
             " WHERE lower(email) = :email "
             "   AND organization_id = CAST(NULLIF("
             "         current_setting('app.tenant_id', true), '') AS uuid) "
             " LIMIT 1"),
        {"email": email.strip().lower()},
    )).fetchone()
    return row is not None


# ── The shared read seam ────────────────────────────────────────────────────
#
# `person_payload` and its two helpers live HERE, in the leaf module, and not
# beside the route that first needed them, for a reason that is easy to trip
# over: `directory.py` registers `/people/{person_id}`, and FastAPI matches in
# REGISTRATION order, so `/people/me` has to be registered first or the literal
# string "me" is matched as a person id and fails casting to a UUID. That means
# `profile.py` must be imported before `directory.py` — which it cannot be if
# it imports anything FROM `directory.py`, because the import would drag the
# routes in first anyway. A leaf module both can read breaks the knot.
#
# `directory.py` re-exports all three, so every existing caller and test keeps
# its import path.

#: A working week, used to turn estimate minutes into the load bar's hours.
#: A constant rather than a setting: the bar is a rough signal, and a tunable
#: nobody tunes is a config surface pretending to be a feature.
MINUTES_PER_HOUR = 60


async def person_payload(db: Any, row: Any, user: Any) -> dict:
    """One person row → the wire shape, with every tier decided here.

    **The one assembler**, used by ``GET /people/{id}`` and by
    ``GET /people/me`` alike. `/people/me` is not a second page with a second
    layout — it is this page resolved through the self predicate — so a field
    added here cannot be missing there (spec §5.3).

    Three access questions, and they are three (§4):

    * ``hr`` — the WS-24 N4 projection, **now with a second door**: the grant
      ``admin:members:read``, *or* being the subject. Without the second door,
      "edit your own skills" would be a form whose current values you cannot
      read.
    * ``private`` — §3.5, keyed to ``admin:members:manage`` or self (D-PC-3),
      deliberately NOT to the read grant a manager may hold.
    * ``editable_fields`` — what this caller may WRITE, from the one field-class
      authority. The UI renders from this and never from its own copy (D-PC-4).
    """
    mine = is_self(user, getattr(row, "email", None))
    admin = can_manage_people(user)
    hr = can_read_hr_fields(user) or mine
    person = _row_to_person(row, include_hr=hr,
                            include_private=admin or mine).model_dump()
    person["has_login"] = await has_login(db, getattr(row, "email", None))
    # An address moved aside by migration 148 because another row claimed it.
    # Surfaced rather than hidden: it means a human still has to decide which
    # row is the real person.
    person["email_conflict"] = getattr(row, "email_conflict", None)
    person["manager"] = await _manager_name(db, getattr(row, "manager_id", None))
    person["load"] = (
        await compute_load(db, getattr(row, "email", None)) if hr else None
    )
    # WS-28p — the schedule layering, resolved in ONE place (D-PC-16).
    #
    # `schedule` is DIRECTORY tier: when a colleague works is what stops a
    # 9am call being rude, and §3.1 already put `working_hours` there.
    # `contracted_hours_per_week` is HR tier: it is a capacity figure, and
    # capacity has been behind `admin:members:read` since WS-24 N4.
    policy = await load_policy(db)
    schedule = person_schedule(policy, row)
    person["schedule"] = schedule
    person["contracted_hours_per_week"] = (
        contracted_hours_per_week(schedule) if hr else None)
    # The typed column is NOT rewritten (R6 — the importer writes it), and the
    # disagreement is surfaced rather than resolved: quietly preferring one is
    # how two numbers start disagreeing where nobody can see them (D-PC-18).
    person["capacity_conflict"] = (
        capacity_disagreement(
            schedule, getattr(row, "capacity_hours_per_week", None))
        if hr else None)
    # WS-28k — availability. `away` is DIRECTORY tier: "do not expect a reply
    # this week" is the same kind of fact as "they work Mon-Fri", and it is the
    # one thing a colleague most needs before they chase somebody. The SPANS
    # themselves are HR tier — when and why somebody is off is capacity
    # information, and a reason is nobody else's business.
    availability = await person_availability(db, str(row.id), schedule, hr=hr)
    person.update(availability)
    # WS-28h — the structured capability half (§3.3): level, years, recency,
    # evidence, and the credential history. HR tier like the flat skills they
    # project into. Best-effort for the same reason the absences are: a
    # database one deploy behind migration 176 answers "none recorded" rather
    # than failing the person page.
    person["skills_detail"] = []
    person["credentials"] = []
    if hr:
        try:
            from gateway.person_skills import fetch_credentials, fetch_skills
            person["skills_detail"] = await fetch_skills(db, str(row.id))
            person["credentials"] = await fetch_credentials(db, str(row.id))
        except Exception:
            pass
    person["hr_visible"] = hr
    # Independent of `hr_visible` on purpose. The two permissions are separate
    # grants, and an admin who may edit a record but not read its HR half is a
    # real principal — the editor has to open with the skills strip restricted
    # and the save button present.
    person["can_manage"] = admin
    person["is_self"] = mine
    person["editable_fields"] = editable_fields(is_admin=admin, is_self=mine)
    return person


async def _manager_name(db: Any, manager_id: Any) -> str | None:
    if not manager_id:
        return None
    row = (await db.execute(
        text("SELECT name FROM gtd_people WHERE id = CAST(:id AS uuid)"),
        {"id": str(manager_id)},
    )).fetchone()
    return str(row.name) if row is not None else None


async def compute_load(db: Any, email: str | None) -> dict[str, Any]:
    """Load from OPEN ASSIGNED TASKS, not from the typed column (§5.2).

    ``gtd_people.current_load_hours_per_week`` is a number somebody typed once;
    it is stale the moment anyone assigns anything. This counts what the person
    is actually holding.

    The honest part is ``unestimated``: a task with no estimate contributes
    nothing to the hours, so a bar built only from the sum would show somebody
    with thirty un-estimated tasks as completely free. The count travels with
    the number so the UI can say "plus 30 with no estimate" instead of lying.

    ⚠️ **The tenant predicate is EXPLICIT, and its absence was a leak.** An
    assignee is an email string, and the same address can be a member of two
    organizations — a contractor working for two customers is the ordinary
    case this product is built for. Without the fence this counted every open
    task in the DEPLOYMENT assigned to that address.

    That is worse than a wrong number. `compute_load` feeds the directory,
    capability search and the Projects **assignee picker**, so another
    customer's workload was deciding whether this customer's colleague looked
    overloaded. `pm_tasks.organization_id` is on the numbered ladder
    (migration 161), so the column is there whether or not the generated
    tenancy phase has been promoted (H-104). `NULLIF(…, '')` fails CLOSED.
    """
    if not (email or "").strip():
        return {"open_tasks": 0, "estimated_hours": 0.0, "unestimated": 0}
    row = (await db.execute(
        text(
            """SELECT count(*)                                   AS open_tasks,
                      COALESCE(sum(t.estimate_mins), 0)           AS mins,
                      count(*) FILTER (WHERE t.estimate_mins IS NULL)
                                                                  AS unestimated
                 FROM pm_tasks t
                 JOIN pm_task_statuses s ON s.id = t.status_id
                 JOIN pm_task_assignees a ON a.task_id = t.id
                WHERE lower(a.assignee) = :who
                  AND t.organization_id = CAST(NULLIF(
                        current_setting('app.tenant_id', true), '') AS uuid)
                  AND t.archived_at IS NULL
                  AND s.category NOT IN ('done', 'cancelled')"""
        ),
        {"who": email.strip().lower()},
    )).fetchone()
    mins = int(getattr(row, "mins", 0) or 0)
    return {
        "open_tasks": int(getattr(row, "open_tasks", 0) or 0),
        "estimated_hours": round(mins / MINUTES_PER_HOUR, 1),
        "unestimated": int(getattr(row, "unestimated", 0) or 0),
    }


# ── The avatar write, shared by both doors ──────────────────────────────────

async def store_avatar(db: Any, person_id: str, data: bytes,
                       crop: tuple[float, float, float] | None,
                       actor: str) -> str:
    """Normalise the upload and store it. Returns the data URI.

    One implementation, two doors — `/people/me/avatar` (ungated, self) and
    `/people/{id}/avatar` (gated, admin). They differ in who may call them,
    which is the field-class authority's job and runs in front of this; they do
    not differ in what reaches the column, because a second normaliser would be
    a second answer to "what shape is an avatar".

    `avatar_updated_at` is stamped here rather than left to `updated_at`: once
    anything else on the row moves, `updated_at` stops being able to answer
    "when did this person last change their picture", which is what the client
    caches against.
    """
    from gateway.avatar import normalise, to_data_uri

    uri = to_data_uri(normalise(data, crop=crop))
    await db.execute(
        text("UPDATE gtd_people SET avatar = :avatar, avatar_updated_at = now(), "
             "updated_by = :by, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"avatar": uri, "by": actor, "id": person_id},
    )
    return uri


async def clear_avatar(db: Any, person_id: str, actor: str) -> None:
    """Remove it. `avatar_updated_at` is stamped, not cleared — "they took their
    picture down just now" is a change the client still has to notice."""
    await db.execute(
        text("UPDATE gtd_people SET avatar = NULL, avatar_updated_at = now(), "
             "updated_by = :by, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"by": actor, "id": person_id},
    )


async def person_availability(db: Any, person_id: str, schedule: dict,
                              *, hr: bool) -> dict:
    """Away-now, the upcoming spans, and the hours left this week (§5.8).

    Best-effort: a database without migration 174 answers "not away" rather
    than failing the person page. A directory that 500s because a table is one
    deploy behind is worse than one that shows a colleague as present.

    ``hours_available_this_week`` is the figure "at risk" is built on — the
    contracted week minus what absences take out of it — and it is HR tier
    because it is a capacity number.
    """
    from datetime import date, timedelta

    from gateway.routes.people.absences import fetch_absences, rows_for_availability

    today = date.today()
    try:
        spans = await rows_for_availability(db, person_id)
    except Exception:
        return {"away": None, "absences": [], "hours_available_this_week": None}

    away = absent_on(today, spans)
    out: dict[str, Any] = {
        # Directory tier: the FACT, and its shape — never the note.
        "away": ({"kind": away["kind"],
                  "until": away["ends_on"].isoformat()} if away else None),
        "absences": [],
        "hours_available_this_week": None,
    }
    if hr:
        # Monday of this week through Sunday, so "this week" means the same
        # thing to everybody looking at the same dashboard.
        monday = today - timedelta(days=today.isoweekday() - 1)
        out["hours_available_this_week"] = working_hours_between(
            schedule, today, monday + timedelta(days=6), spans)
        out["absences"] = await fetch_absences(db, person_id, since=today)
    return out
