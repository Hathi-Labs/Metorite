"""People Center · the company's working week (WS-28p).

Spec: ``project-docs/specs/people_center_app.md`` §3.4a, §5.11 · D-PC-16, D-PC-18.

    GET /people/schedule   → the org policy + its defaults (any feature holder)
    PUT /people/schedule   → edit it (admin:members:manage)

The *model* is ``gateway.work_schedule`` — pure, shared, and outside both route
packages so the calendar can read it without closing an import cycle. This
module is only the surface and the store.

**Why editing it is admin-gated.** It is the definition of the working week for
everybody: changing it moves every contracted-hours figure and every load bar
in the product at once. That is why the PUT answers with the impact — see
:class:`PolicyImpact`.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.people.core import (
    _tenant_session,
    can_manage_people,
    router,
)
from gateway.routes.tasks.core import require_people_write
from gateway.work_schedule import (
    DEFAULT_POLICY,
    POLICY_KEY,
    ScheduleError,
    contracted_hours_per_week,
    effective_schedule,
    load_policy,
    person_schedule,
    validate_policy,
)
from pydantic import BaseModel
from sqlalchemy import text


class PolicyResponse(BaseModel):
    policy: dict[str, Any]
    #: What an org that has never edited it gets — sent so the settings page can
    #: offer "reset" without hard-coding a second copy of the defaults.
    defaults: dict[str, Any]
    #: The whole-company week implied by the policy, for the summary line.
    contracted_hours_per_week: float
    can_manage: bool
    updated_by: str | None = None
    updated_at: str | None = None


class PolicyImpact(BaseModel):
    """Who this change actually moves, and by how much.

    Returned by the dry run, and the reason it exists: a settings page that
    silently re-baselines every load bar in the organisation is a settings page
    nobody trusts twice. The count is people whose derived hours change — not
    the roster size, which would be true and useless.
    """
    changed: int
    unchanged: int
    #: Up to ten examples, so the number is checkable rather than asserted.
    examples: list[dict[str, Any]]
    hours_before: float
    hours_after: float


@router.get("/schedule", response_model=PolicyResponse)
async def get_policy(user: UserContext = Depends(get_current_user)) -> PolicyResponse:
    """The company's working week.

    Readable by any ``feature:people`` holder rather than by admins only: "when
    does this company work" is directory-level information — the same tier as a
    colleague's working hours — and a person cannot understand their own
    effective schedule without seeing the layer underneath it.
    """
    async with _tenant_session() as db:
        policy = await load_policy(db)
        meta = (await db.execute(
            text("SELECT updated_by, updated_at FROM org_settings "
                 "WHERE key = :key"),
            {"key": POLICY_KEY},
        )).fetchone()
    return PolicyResponse(
        policy=policy,
        defaults=dict(DEFAULT_POLICY),
        contracted_hours_per_week=contracted_hours_per_week(
            effective_schedule(policy, None)),
        can_manage=can_manage_people(user),
        updated_by=getattr(meta, "updated_by", None) if meta else None,
        updated_at=(meta.updated_at.isoformat()
                    if meta is not None and getattr(meta, "updated_at", None)
                    else None),
    )


class PolicyWrite(BaseModel):
    policy: dict[str, Any]
    #: When true, compute and return the impact and **write nothing**. The
    #: settings page calls this first, shows what will move, and only then
    #: saves — the same propose-then-apply shape the ClickUp stage repair uses,
    #: and for the same reason: a change nobody could preview is a change
    #: somebody undoes by hand afterwards.
    dry_run: bool = False


class PolicySaved(BaseModel):
    policy: dict[str, Any]
    impact: PolicyImpact
    saved: bool


@router.put("/schedule", response_model=PolicySaved,
            dependencies=[require_people_write()])
async def put_policy(
    body: PolicyWrite, user: UserContext = Depends(get_current_user),
) -> PolicySaved:
    """Replace the org policy — or, with ``dry_run``, say what it would move.

    Validated strictly and refused with the sentence, not a field code: the
    person typing it is the person who has to fix it.
    """
    try:
        policy = validate_policy(body.policy)
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with _tenant_session() as db:
        current = await load_policy(db)
        rows = (await db.execute(text(
            "SELECT id, name, working_hours FROM people "
            "WHERE status <> 'alumni'"))).fetchall()
        impact = _impact(current, policy, rows)
        if not body.dry_run:
            await db.execute(
                text(
                    "INSERT INTO org_settings (key, value, updated_by, updated_at) "
                    "VALUES (:key, CAST(:value AS JSONB), :by, now()) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, "
                    "    updated_by = EXCLUDED.updated_by, "
                    "    updated_at = now()"
                ),
                {"key": POLICY_KEY, "value": json.dumps(policy),
                 "by": getattr(user, "email", None) or "anonymous"},
            )
    return PolicySaved(policy=policy, impact=impact, saved=not body.dry_run)


def _impact(before: dict[str, Any], after: dict[str, Any],
            rows: list[Any]) -> PolicyImpact:
    """Whose derived week changes between two policies.

    Pure over the rows it is handed, so the arithmetic is testable without a
    database — and it has to be right, because it is the number an admin reads
    before agreeing to move everybody's capacity at once.
    """
    changed: list[dict[str, Any]] = []
    unchanged = 0
    for row in rows:
        was = contracted_hours_per_week(person_schedule(before, row))
        now = contracted_hours_per_week(person_schedule(after, row))
        if was == now:
            unchanged += 1
        else:
            changed.append({"id": str(row.id), "name": row.name,
                            "before": was, "after": now})
    return PolicyImpact(
        changed=len(changed),
        unchanged=unchanged,
        examples=changed[:10],
        hours_before=contracted_hours_per_week(effective_schedule(before, None)),
        hours_after=contracted_hours_per_week(effective_schedule(after, None)),
    )
