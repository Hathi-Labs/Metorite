"""Projects · vocabulary — the organization's OWN tags, fields and types (WS-42 PS-3).

Spec: ``project-docs/specs/projects_settings.md`` §7 row PS-3, and
``project_management_app.md`` §9.11.2 (D-PM-33).

    GET    /projects/vocabulary
    GET    /projects/vocabulary/{kind}/{row_id}/impact

Until this route, an org-wide row (``project_id IS NULL``, migration 175) was
visible only as one member of a space's union, and no read listed the
organization's rows as a set. So nobody could see what the organization had
minted, and HANDOFF H-4 stayed open. The writes already exist: a rename goes
through ``PATCH /tags|fields|types/{id}``, which D-PM-33 opened for org-wide
rows. This module adds the list and nothing else.

**No project anchor, so the tenant is composed explicitly.** Every other
vocabulary read anchors on ``:root``, a project the caller was shown
(``core.vocabulary_scope``). This one has no project, so it binds the caller's
own ``organization_id`` beside RLS. Two independent fences, the same reason
``vocabulary_scope`` gives.

**The counts are for the people who can rename.** A count spans every task in
the organization, including tasks in spaces the reader cannot open. D-PM-33
shows that number only to ``admin:settings:manage`` (``tag_impact``), so this
read does the same, and a member without it gets the rows with no counts.
"""

from __future__ import annotations

from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    ORG_VOCABULARY_WRITE,
    TypeModel,
    _tenant_session,
    governed_tasks_scope,
    is_org_wide,
    org_vocabularies_enabled,
    require_known_tenant,
    require_org_vocabulary_edit,
    require_row,
    require_same_tenant,
    resolve_visibility,
    router,
    row_to_dict,
)
from gateway.routes.projects.custom_fields import _definition_row
from gateway.routes.projects.tags import _row as _tag_row
from sqlalchemy import text

#: Each statement selects ONLY org-wide rows of the caller's tenant. The count
#: arm is a correlated subquery on the same tenant, and it reads OPEN tasks, as
#: the space's own tag list does (``tags.list_tags``).
ORG_TAGS_SQL = (
    "SELECT g.*, ("
    "  SELECT count(*) FROM pm_tasks t "
    "   WHERE t.organization_id = g.organization_id "
    "     AND t.archived_at IS NULL AND g.name = ANY(t.tags)"
    # A space with its own tag of this name wears ITS tag, not the shared one.
    "     AND t.root_project_id NOT IN (SELECT s.project_id FROM pm_tags s "
    "          WHERE s.project_id IS NOT NULL AND s.organization_id = g.organization_id "
    "            AND lower(s.name) = lower(g.name))"
    ") AS task_count "
    "  FROM pm_tags g "
    " WHERE g.project_id IS NULL AND g.organization_id = CAST(:org AS uuid) "
    " ORDER BY lower(g.name)"
)
ORG_FIELDS_SQL = (
    "SELECT g.*, ("
    "  SELECT count(*) FROM pm_tasks t "
    "   WHERE t.organization_id = g.organization_id "
    "     AND t.archived_at IS NULL AND t.custom_fields ? g.field_key"
    "     AND t.root_project_id NOT IN (SELECT s.project_id FROM pm_custom_fields s "
    "          WHERE s.project_id IS NOT NULL AND s.organization_id = g.organization_id "
    "            AND s.field_key = g.field_key)"
    ") AS task_count "
    "  FROM pm_custom_fields g "
    " WHERE g.project_id IS NULL AND g.organization_id = CAST(:org AS uuid) "
    " ORDER BY g.position, lower(g.name)"
)
ORG_TYPES_SQL = (
    "SELECT g.*, ("
    "  SELECT count(*) FROM pm_tasks t "
    "   WHERE t.organization_id = g.organization_id "
    "     AND t.archived_at IS NULL AND t.type_id = g.id"
    ") AS task_count "
    "  FROM pm_task_types g "
    " WHERE g.project_id IS NULL AND g.organization_id = CAST(:org AS uuid) "
    " ORDER BY lower(g.name)"
)


def _with_count(row: dict[str, Any], count: Any, show: bool) -> dict[str, Any]:
    if show:
        row["task_count"] = int(count or 0)
    else:
        row.pop("task_count", None)
    return row


@router.get("/vocabulary")
async def org_vocabulary(user: UserContext = Depends(get_current_user)) -> dict:
    """The organization's shared tags, custom fields and task types.

    ``can_edit`` says whether the caller may rename them (D-PM-33).
    ``can_create`` says whether minting a new one is released (H-5), and it is
    information only: this slice adds no create.
    """
    can_edit = user is not None and user.has_permission(ORG_VOCABULARY_WRITE)
    out: dict[str, Any] = {
        "tags": [],
        "fields": [],
        "types": [],
        "can_edit": can_edit,
        "can_create": can_edit and org_vocabularies_enabled(),
    }
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        if vis.organization_id is None:
            # No tenant resolved: a caller the directory does not know. Fail
            # closed, as every read with `organization_id = NULL` does.
            return out
        params = {"org": str(vis.organization_id)}
        tags = (await db.execute(text(ORG_TAGS_SQL), params)).fetchall()
        fields = (await db.execute(text(ORG_FIELDS_SQL), params)).fetchall()
        types = (await db.execute(text(ORG_TYPES_SQL), params)).fetchall()
    out["tags"] = [_with_count(_tag_row(r), r.task_count, can_edit) for r in tags]
    out["fields"] = [_with_count(_definition_row(r), r.task_count, can_edit) for r in fields]
    out["types"] = [_with_count(row_to_dict(r, TypeModel), r.task_count, can_edit) for r in types]
    return out


#: H-205 — each kind's table, and the predicate on `pm_tasks` that finds the
#: tasks a delete or merge of one row would change.
IMPACT: dict[str, tuple[str, str, str]] = {
    "tags": ("pm_tags", ":value = ANY(tags)", "name"),
    "fields": ("pm_custom_fields", "custom_fields ? :value", "field_key"),
    "types": ("pm_task_types", "type_id = CAST(:value AS uuid)", "id"),
}


@router.get("/vocabulary/{kind}/{row_id}/impact")
async def vocabulary_impact(
    kind: Literal["tags", "fields", "types"],
    row_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """How many tasks, in how many spaces, a delete or merge of one SHARED row
    would change (H-205), BEFORE it is asked for.

    The same shape as ``GET /tags/{id}/impact`` (D-PM-33), widened to fields
    and types. It counts archived tasks too, because the delete reaches them.
    The scope is ``governed_tasks_scope``, so a space that keeps its own row
    of the same identity is left out of the count, as it is of the write.
    """
    table, predicate, attr = IMPACT[kind]
    async with _tenant_session() as db:
        row = await require_row(db, table, row_id, "Entry")
        # The tenant fence FIRST, so another organization's id answers 404
        # whatever its scope, and no answer says such a row exists.
        vis = await resolve_visibility(db, user)
        require_known_tenant(vis, "entry")
        require_same_tenant(vis, row)
        if not is_org_wide(row):
            raise HTTPException(status_code=409, detail="This entry belongs to one space.")
        require_org_vocabulary_edit(user, row.name)
        where, params = governed_tasks_scope(row, table)
        counted = (
            await db.execute(
                text(
                    "SELECT count(*) AS tasks, count(DISTINCT root_project_id) AS projects "
                    f"  FROM pm_tasks WHERE {where} AND {predicate}"
                ),
                {**params, "value": str(getattr(row, attr))},
            )
        ).fetchone()
    return {
        "name": row.name,
        "tasks": int(counted.tasks or 0),
        "projects": int(counted.projects or 0),
    }
