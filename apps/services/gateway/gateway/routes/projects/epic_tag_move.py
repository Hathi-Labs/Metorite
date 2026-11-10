"""SP-7 — move the tasks tagged "Epic" to the Epic type, once (Metorite task 2290).

Spec: ``project-docs/specs/project_management_app.md`` §13, slice SP-7.

A tag "Epic" and the Epic type both exist. A top-level task that has the tag and
no epic type stays invisible to the roll-ups, the epic page and the critical
path, which read the type. This module gives each such task the epic type of its
root project. The tag stays as a plain tag.

**Rules.**

* A task that has a parent is skipped and listed (the Epic-root rule,
  ``core.assert_epic_has_no_parent``).
* A task whose root project has no epic type is skipped and listed.
* The tag match ignores case, as the tag registry does.
* Each change goes through ``core.record_field_change`` as ``system:sp7-epic-tag``,
  so the timeline shows it as an automated field change.
* A second run finds nothing to move. A dry run rolls back.

Run it through ``scripts/sp7_epic_tag_move.py``. Every read and write goes
through ``tenant_session(org)``, so FORCE RLS stamps and checks the tenant.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

ACTOR = "system:sp7-epic-tag"

#: The top-level tasks that carry the tag and lack an epic type, with the epic
#: type of their root project. ``epic_type_id`` is NULL when the root has none.
_CANDIDATES_SQL = text("""
SELECT t.id, t.task_number, t.title, t.parent_task_id, t.type_id,
       t.root_project_id,
       (SELECT ty.id FROM pm_task_types ty
         WHERE ty.project_id = t.root_project_id AND ty.is_epic
         ORDER BY ty.is_system DESC, (ty.name = 'Epic') DESC, ty.created_at, ty.id
         LIMIT 1) AS epic_type_id
  FROM pm_tasks t
  LEFT JOIN pm_task_types cur ON cur.id = t.type_id
 WHERE t.organization_id = CAST(:org AS uuid)
   AND EXISTS (SELECT 1 FROM unnest(t.tags) g WHERE lower(g) = 'epic')
   AND COALESCE(cur.is_epic, false) = false
 ORDER BY t.root_project_id, t.task_number
""")


async def move_org(org: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Move the tagged tasks of *org*. Returns counts and the skipped tasks.

    Keys: ``found``, ``moved``, ``skipped`` (a list of dicts with ``task_number``,
    ``title``, ``reason``) and ``moved_numbers``. A dry run counts ``moved`` as
    the tasks that a real run would move, and writes nothing.
    """
    from gateway.db import tenant_session
    from gateway.routes.projects.core import record_field_change

    out: dict[str, Any] = {"found": 0, "moved": 0, "skipped": [], "moved_numbers": []}
    async with tenant_session(org) as db:
        rows = (await db.execute(_CANDIDATES_SQL, {"org": org})).fetchall()
        out["found"] = len(rows)
        for r in rows:
            if r.parent_task_id is not None:
                out["skipped"].append({"task_number": r.task_number, "title": r.title,
                                       "reason": "has a parent"})
                continue
            if r.epic_type_id is None:
                out["skipped"].append({"task_number": r.task_number, "title": r.title,
                                       "reason": "its project has no epic type"})
                continue
            out["moved"] += 1
            out["moved_numbers"].append(r.task_number)
            if dry_run:
                continue
            await db.execute(
                text("UPDATE pm_tasks SET type_id = CAST(:ty AS uuid), updated_at = now() "
                     "WHERE id = CAST(:id AS uuid)"),
                {"ty": str(r.epic_type_id), "id": str(r.id)},
            )
            await record_field_change(
                db, created_by=ACTOR, task_id=str(r.id), automation=True,
                changes=[{"field": "type_id",
                          "old": str(r.type_id) if r.type_id else None,
                          "new": str(r.epic_type_id)}],
            )
        if dry_run:
            await db.rollback()
    return out
