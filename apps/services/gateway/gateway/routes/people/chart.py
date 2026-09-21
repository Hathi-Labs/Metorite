"""People Center · the org chart (WS-28c).

Spec: ``project-docs/specs/people_center_app.md`` §5.4 · D-PC-14.

    GET /people/chart   → every current person as a flat node list

The chart is ``people.manager_id`` — a self-FK — so this endpoint returns
the FLAT list and the client builds the tree, with the cycle guard where the
recursion is (a manager loop must degrade to a labelled root, not a hang).

Three decisions worth stating:

* **Directory tier.** Name, title, department and who-reports-to-whom are the
  directory's own fields (§4.2 tier D); nothing HR travels on a node. Group
  membership rides along because the Center overlay is the point of §5.4 —
  "who is actually in Operations" — and groups are what actually scope the
  Centers (`department_centers.md`), while ``department`` is free text. The
  overlay exists to SHOW where those two disagree, not to smooth it over.
* **Alumni are not on the chart.** A chart is the org as it stands. A person
  whose manager is an alumni row therefore surfaces as a ROOT — the same
  defect §5.10's ``manager_alumni`` list names, made visible rather than
  patched around.
* **No write here.** Re-parenting goes through the ordinary
  ``PATCH /people/{id}`` (admin class, §4.3) — the chart page calls the same
  door the person editor does, and ``can_manage`` only tells the UI whether
  to offer the drag.
"""

from __future__ import annotations

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.people.core import _tenant_session, router
from gateway.routes.tasks.core import can_manage_people
from pydantic import BaseModel
from sqlalchemy import text


class ChartNode(BaseModel):
    id: str
    name: str
    title: str | None = None
    department: str | None = None
    team: str | None = None
    avatar: str | None = None
    email: str | None = None
    status: str | None = None
    manager_id: str | None = None
    #: Slugs of the ``org_group`` rows this person belongs to (joined through
    #: ``app_user`` on lowered email) — the overlay's facts, never a colour.
    groups: list[str] = []


class ChartGroup(BaseModel):
    slug: str
    display_name: str


class ChartResponse(BaseModel):
    nodes: list[ChartNode]
    #: The org's groups, so the client can draw a legend and spot a
    #: department string that names a group nobody put the person in.
    groups: list[ChartGroup]
    can_manage: bool


@router.get("/chart", response_model=ChartResponse)
async def get_chart(
    user: UserContext = Depends(get_current_user),
) -> ChartResponse:
    async with _tenant_session() as db:
        rows = (await db.execute(text(
            # `status IS NULL` stays ON the chart: NULL <> 'alumni' is NULL in
            # SQL, which would silently drop the row §5.10 lists as a
            # bad-status defect — a person must not vanish with their status.
            "SELECT id, name, title, department, team, avatar, email, "
            "       status, manager_id "
            "  FROM people "
            " WHERE status IS NULL OR status <> 'alumni' "
            " ORDER BY lower(name)"))).fetchall()
        # ⚠️ `org_group` is in the tenancy ratchet's EXEMPT list (it carries
        # organization_id since 138) so NO RLS policy covers it — the tenant
        # predicate must be explicit here, exactly as `routes/admin/groups.py`
        # and `rooms.py` do, or this legend lists every customer's groups.
        # `NULLIF(current_setting(…), '')` fails CLOSED when the GUC is
        # unbound: NULL matches no row. (Found by the adversarial review.)
        groups = (await db.execute(text(
            "SELECT id, slug, display_name FROM org_group "
            " WHERE organization_id = "
            "       CAST(NULLIF(current_setting('app.tenant_id', true), '') "
            "            AS uuid) "
            " ORDER BY slug"))).fetchall()
        membership: dict[str, list[str]] = {}
        if groups:
            # org_group_member and app_user ARE policied, but the same
            # explicit predicate keeps this correct on a cluster without
            # FORCE RLS (the scratch DB) rather than safe by accident.
            members = (await db.execute(text(
                "SELECT g.slug, lower(btrim(u.email)) AS email "
                "  FROM org_group_member m "
                "  JOIN org_group g ON g.id = m.group_id "
                "  JOIN app_user u ON u.id = m.user_id "
                " WHERE g.organization_id = "
                "       CAST(NULLIF(current_setting('app.tenant_id', true), "
                "                   '') AS uuid)"))).fetchall()
            for m in members:
                if m.email:
                    membership.setdefault(m.email, []).append(m.slug)

    current_ids = {str(r.id) for r in rows}
    nodes = []
    for r in rows:
        email = (r.email or "").strip().lower()
        nodes.append(ChartNode(
            id=str(r.id), name=r.name, title=r.title,
            department=r.department, team=r.team, avatar=r.avatar,
            email=r.email, status=r.status,
            # A manager off the chart (alumni, deleted) is no manager: the
            # person surfaces as a root, which is §5.4's "not an error state
            # to hide" — and the same fact §5.10 lists as `manager_alumni`.
            manager_id=(str(r.manager_id)
                        if r.manager_id and str(r.manager_id) in current_ids
                        else None),
            groups=sorted(membership.get(email, [])),
        ))
    return ChartResponse(
        nodes=nodes,
        groups=[ChartGroup(slug=g.slug, display_name=g.display_name)
                for g in groups],
        can_manage=can_manage_people(user),
    )
