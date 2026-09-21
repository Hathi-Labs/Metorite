"""People Center · the Center landing rollup (WS-28l).

Spec: ``project-docs/specs/people_center_app.md`` §5.9 · D-PC-14, D-PC-20.

    GET /people/overview   → the one-screen answer the Center opens on

What ``centers.ts`` lists as *"People dashboard"*, narrowed to what exists:
headcount by department and status, who is away this week, the load spread,
the data-quality counts, and the org's unmanaged roots.

**A projection of §5.7 and §5.10, not new arithmetic** — stated as a
mechanism, the same way ``workload.rollup`` reads serialized rows:

* the load half IS :func:`~gateway.routes.people.dashboard.get_dashboard`'s
  own ``departments``/``org`` rollup, passed through verbatim (including its
  ``away`` names — "who is away this week" is already on it);
* the quality half IS :func:`~gateway.routes.people.quality.collect`'s counts
  and its ``no_manager`` list, passed through verbatim.

A dashboard that computes its own version of a number the app already renders
is how two numbers start disagreeing — so the ONE statement this module runs
itself is the headcount by department and status, which no other surface
computes (the workload dashboard excludes alumni by design; a headcount that
did would say the company never loses anybody). The fence: this module's only
``SELECT`` reads ``people``, and its only aggregate is that GROUP BY.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.people.core import _tenant_session, can_read_hr_fields, router
from gateway.routes.people.dashboard import get_dashboard
from gateway.routes.people.quality import collect
from pydantic import BaseModel
from sqlalchemy import text


class HeadcountRow(BaseModel):
    department: str
    status: str
    count: int


class OverviewResponse(BaseModel):
    headcount: list[HeadcountRow]
    total_people: int
    #: §5.7.3's rollup, verbatim — never recomputed here.
    departments: list[dict[str, Any]]
    org: dict[str, Any]
    #: §5.10's pre-cap totals, verbatim — the panel behind them is /people/quality.
    quality_counts: dict[str, int]
    #: The org chart's roots (§5.9 "unmanaged roots") — §5.10's ``no_manager``
    #: list, capped exactly as §5.10 caps it. The COUNT is
    #: ``quality_counts["no_manager"]`` — the page must number from that,
    #: never from ``len(roots)``, or the two figures disagree past 50 rows.
    roots: list[dict[str, Any]]
    partial: bool
    work_visible: bool


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    user: UserContext = Depends(get_current_user),
) -> OverviewResponse:
    """Gated exactly like the two surfaces it projects (§4.2's oracle rule):
    load, quarantines and org structure for everybody at once."""
    if not can_read_hr_fields(user):
        raise HTTPException(
            status_code=403,
            detail="The People Center landing needs admin:members:read.")

    board = await get_dashboard(user)

    async with _tenant_session() as db:
        q = await collect(db, user)
        counted = (await db.execute(text(
            "SELECT COALESCE(NULLIF(btrim(department), ''), 'Unassigned') "
            "         AS department, "
            # NULL status is a data defect §5.10 lists, so headcount shows it
            # as its own bucket rather than silently promoting it to active —
            # the two numbers sit on one screen and must tell one story.
            "       COALESCE(status, '(none)') AS status, "
            "       count(*) AS count "
            "  FROM people "
            " GROUP BY 1, 2 ORDER BY 1, 2"))).fetchall()

    headcount = [HeadcountRow(department=r.department, status=r.status,
                              count=r.count) for r in counted]
    return OverviewResponse(
        headcount=headcount,
        total_people=sum(r.count for r in headcount),
        departments=board.departments,
        org=board.org,
        quality_counts=q.counts,
        roots=[p.model_dump() for p in q.quality.no_manager],
        partial=board.partial,
        work_visible=board.work_visible,
    )
