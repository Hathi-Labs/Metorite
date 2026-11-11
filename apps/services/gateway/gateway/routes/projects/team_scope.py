"""Projects · the team scope (WS-39 T-1, Metorite task 2532).

Spec: ``project-docs/specs/project_management_app.md`` §14.9 (the five rules
of teams) and ``navigation_shell.md`` §4.2a (the one scope value).

A team is a group, ``group:<slug>`` (D12). There is no team object and no team
table (D87, D49). The teams of a member are the groups that the member is in.
A grant of a project to a group in ``pm_project_grants`` puts that project,
and each project beneath it, in the view of that team (rule 1). So the grant
of a space covers its whole subtree.

**The one value.** A read takes ``team``: the slug of one team of the caller,
or the word ``all`` (:data:`ALL_TEAMS`). ``all`` is "All my teams", which is
every space that the member may open (T-2), so it narrows nothing.

* The one subject grammar (``core.validate_grant_subject``) checks the slug,
  as ``group:<slug>``. A value that it refuses is 422. A value with a
  character that cannot print is 422 too. No second grammar.
* A well-formed slug that names no group of the caller is 404, with the same
  body as a slug that names no group at all. So the answer says nothing about
  the groups of other members. The slug reaches the database only when it
  equals a group that the directory gave for the caller.
* While ``TEAMS_ORGS`` does not hold the org, any ``team`` value is 404, and
  ``GET /projects/teams`` is 404 (:func:`require_teams`).

**A scope never grants (rule 5).** :meth:`TeamScope.project_clause` can only
narrow. Each read ANDs it beside its own visibility clause and never uses it
alone, so a team view holds no row that the caller could not already read.

**The team reach.** :data:`_TEAM_PROJECTS_SQL` is the closure of the grants of
one group, in the tenant of the caller. It leaves out a sealed node (D63) and a
personal node (D-OP-15), on both arms, as ``core._VISIBLE_PROJECTS_SQL`` does.
A team view holds team spaces only, so no personal node is part of one.

The tenant and the caller come from the session. ``vis_org`` is the tenant of
``resolve_visibility``, and the groups come from ``core._MY_GROUPS_SQL`` and
``core.org_groups``, imported and never copied (R5).

Readers: ``GET /projects/teams`` (``teams.py``), and the ``team`` filter of
``GET /projects/tasks``, ``/projects/tasks/stats``, ``/projects/tree``,
``/projects/nodes`` and ``/projects/search``.

Fence: ``tests/unit/test_teams_read_model_r8.py`` (R8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    _MY_GROUPS_SQL,
    Visibility,
    org_groups,
    validate_grant_subject,
)
from sqlalchemy import text

#: The word of "All my teams" (``navigation_shell.md`` §4.2a). It narrows
#: nothing: "All my teams" is every space that the member may open.
ALL_TEAMS = "all"

#: The 404 body of a slug that names no team of the caller. A group of another
#: member answers the same words, so the answer is no oracle (R5).
TEAM_NOT_FOUND = "Team not found"

#: The 422 words of a value that is not a team slug.
TEAM_SHAPE = (
    "team takes the slug of one of your teams, such as team-a, "
    f"or the word {ALL_TEAMS}."
)

#: The projects that the grants of ONE group reach: each project with a grant
#: of ``:team_subject``, and each project beneath it.
#:
#: ⚠️ ``g.organization_id = :vis_org`` binds the GRANT row to the tenant, as in
#: ``core._VISIBLE_PROJECTS_SQL``. Another organization can hold a group with
#: the same slug, and its grants must reach nothing here. The recursive step
#: repeats the tenant, so the closure never leaves it.
#:
#: The seal and the personal line bind both arms, so the walk stops at a sealed
#: node and at a personal node, and never descends through one. ``UNION`` ends
#: the walk on a cycle.
_TEAM_PROJECTS_SQL = """
WITH RECURSIVE team_reach AS (
    SELECT g.project_id AS id
    FROM pm_project_grants g
    JOIN pm_projects gp ON gp.id = g.project_id
    WHERE g.organization_id = CAST(:vis_org AS uuid)
      AND g.subject = :team_subject
      AND gp.organization_id = CAST(:vis_org AS uuid)
      AND gp.sealed_at IS NULL
      AND gp.personal_owner IS NULL
    UNION
    SELECT p.id
    FROM pm_projects p
    JOIN team_reach r ON p.parent_project_id = r.id
    WHERE p.organization_id = CAST(:vis_org AS uuid)
      AND p.sealed_at IS NULL
      AND p.personal_owner IS NULL
)
SELECT id FROM team_reach
"""


# ── The flag ────────────────────────────────────────────────────────────────


def teams_allowed(organization_id: str | None) -> bool:
    """True when ``TEAMS_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session, never from request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload needs no restart
    (``docs/flag_reload.md``). THE one reader of the flag.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "teams_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


def _dark() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


async def require_teams(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``TEAMS_ORGS`` does not hold the caller's org."""
    if not teams_allowed(getattr(user, "organization_id", None)):
        raise _dark()


# ── The scope ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TeamScope:
    """One team of the caller: a group of the caller's organization that the
    caller is in. Only :func:`member_teams` makes one from the directory."""

    slug: str
    name: str

    @property
    def subject(self) -> str:
        """The grant subject of the team, the form ``pm_project_grants`` holds."""
        return f"group:{self.slug}"

    @property
    def params(self) -> dict[str, Any]:
        """The bind of :meth:`project_clause`. ``vis_org`` comes from the
        caller's ``Visibility.params``."""
        return {"team_subject": self.subject}

    def project_clause(self, column: str = "id") -> str:
        """A predicate that keeps *column* (a project id) inside the team reach.

        ⚠️ AND it beside the read's own visibility clause. Alone it is not a
        visibility rule: it says what a team holds, not what a caller may read.
        """
        return f"{column} IN ({_TEAM_PROJECTS_SQL})"


def parse_team(value: str) -> str | None:
    """The grant subject that *value* names, or ``None`` for ``all``.

    Pure. A value that the one subject grammar refuses, or that holds a
    character that cannot print, is 422 (:data:`TEAM_SHAPE`).
    """
    raw = str(value).strip()
    if raw.lower() == ALL_TEAMS:
        return None
    if not raw or not raw.isprintable():
        raise HTTPException(status_code=422, detail=TEAM_SHAPE)
    try:
        return validate_grant_subject(f"group:{raw}")
    except HTTPException:
        raise HTTPException(status_code=422, detail=TEAM_SHAPE) from None


def check_team_value(user: Any, team: str | None) -> str | None:
    """The flag, then the shape, with no database read.

    ``None`` when *team* is absent or ``all``. Else the folded subject that it
    names. 404 while the flag is off, and 422 for a value of the wrong shape.
    """
    if team is None:
        return None
    if not teams_allowed(getattr(user, "organization_id", None)):
        raise _dark()
    return parse_team(team)


async def member_teams(db: Any, vis: Visibility) -> list[TeamScope]:
    """Each team of the caller, in the order of the group slugs.

    The groups of the caller are ``vis.groups``. A ``data:org:read`` holder has
    none there, because ``resolve_visibility`` skips that read for them, so it
    runs ``core._MY_GROUPS_SQL`` here. ``core.org_groups`` names the
    organization, so a group of another tenant never shows (R5).
    """
    if vis.unrestricted:
        rows = (await db.execute(text(_MY_GROUPS_SQL), {"email": vis.email})).fetchall()
        subjects = {str(r.subject).lower() for r in rows if getattr(r, "subject", None)}
    else:
        subjects = {str(s).lower() for s in vis.groups}
    if not subjects:
        return []
    return [
        TeamScope(slug=g["slug"], name=g["display_name"])
        for g in await org_groups(db, vis.organization_id)
        if f"group:{g['slug']}".lower() in subjects
    ]


async def resolve_team_scope(
    db: Any, user: Any, vis: Visibility, team: str | None,
) -> TeamScope | None:
    """The team that *team* names, or ``None`` when the read is not narrowed.

    ``None`` for an absent value and for ``all``. 404 while the flag is off,
    422 for a value of the wrong shape (:func:`check_team_value`), and 404
    (:data:`TEAM_NOT_FOUND`) for a slug that names no team of the caller.
    """
    subject = check_team_value(user, team)
    if subject is None:
        return None
    for scope in await member_teams(db, vis):
        if scope.subject.lower() == subject:
            return scope
    raise HTTPException(status_code=404, detail=TEAM_NOT_FOUND)
