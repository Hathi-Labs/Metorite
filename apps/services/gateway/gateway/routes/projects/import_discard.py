"""Projects · file import — discard one run (WS-41 I-6).

Spec: ``project-docs/specs/project_import.md`` §6.9 "Discard" · decision D80.

A discard removes ONLY the rows one run wrote: the tasks it created, the
comments and activity it added elsewhere, and the nodes it created. It never
touches a reused node or a task an earlier run created, and it cannot put back
a field it changed on one (the earlier value is not kept), so it says how many
such updates stay.

It refuses, and deletes nothing, when somebody built on the run since: a later
import that continued it, a member's edit or comment on one of its tasks, or a
member's new work inside one of its nodes. Deleting then would destroy work
that is not the run's. Every check and every delete runs in ONE transaction,
under the same advisory lock the apply takes (§7.4), so no import starts
between the check and the delete.

The deletes are plain ``DELETE``s, as ``DELETE /nodes/{id}`` does, so migration
168's trigger writes the tombstones and the delta feed sees the tasks go. No
event fires and nobody is notified (§6.10).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

#: How long after it ends a run can be discarded (§6.9).
DISCARD_DAYS = 14
#: How many blocking tasks or nodes the refusal names.
NAME_AT_MOST = 10

#: An import is writing. It may be writing into this run's nodes.
WRITING_SQL = (
    "SELECT id FROM pm_import_runs "
    " WHERE organization_id = CAST(:org AS uuid) AND state = 'applying'"
)


#: When a related run WROTE: from its apply (``progress.started_at``, stamped
#: by START_SQL) to its end. The upload is not the start: a run can sit in
#: planning for days, and member work in that time must still count.
_WROTE_WINDOW = (
    "{col} BETWEEN coalesce(CAST(o.progress->>'started_at' AS timestamptz), o.created_at) "
    "AND coalesce(o.finished_at, 'infinity'::timestamptz)"
)


def _not_by_related_import_on_nodes(column: str) -> str:
    """True unless a RELATED import wrote this node-level row. Only three
    tables can hold one: a later run that continued this run, or that targeted
    one of its nodes, adds statuses, task types and tags there, and its own
    discard leaves them (§6.9). Unrelated runs and every other table get no
    exception, so an upload left open hides nothing."""
    return (
        "NOT EXISTS (SELECT 1 FROM pm_import_runs o "
        " WHERE o.organization_id = CAST(:org AS uuid) AND o.id <> CAST(:run AS uuid) "
        "   AND o.progress ? 'node_ids' "
        "   AND (o.progress->'continued_from' ? CAST(:run AS text) "
        "        OR o.mapping->'target'->>'project_id' = ANY(CAST(:nodes AS text[]))) "
        "   AND " + _WROTE_WINDOW.format(col=column) + ")"
    )


def _not_by_related_import_on_task(column: str) -> str:
    """True unless a RELATED import wrote this watcher: a run that updated the
    task and synced its assignees."""
    return (
        "NOT EXISTS (SELECT 1 FROM pm_import_runs o "
        " WHERE o.organization_id = CAST(:org AS uuid) AND o.id <> CAST(:run AS uuid) "
        "   AND o.progress ? 'node_ids' "
        "   AND (o.id::text = t.origin->>'updated_by_run' "
        "        OR EXISTS (SELECT 1 FROM pm_activities a WHERE a.task_id = t.id "
        "                    AND a.meta->'import'->>'run_id' = o.id::text)) "
        "   AND " + _WROTE_WINDOW.format(col=column) + ")"
    )


#: Rule 1a — a later run that is not discarded continued this run's nodes.
LATER_RUNS_SQL = (
    "SELECT id, created_at FROM pm_import_runs "
    " WHERE organization_id = CAST(:org AS uuid) AND id <> CAST(:run AS uuid) "
    "   AND state IN ('applying', 'done', 'failed') "
    "   AND progress->'continued_from' ? CAST(:run AS text) "
    " ORDER BY created_at DESC LIMIT :n"
)
#: Rule 1b — a later run that is not discarded updated or commented on a task
#: this run made. `updated_by_run` names only the LAST such run, so the rows
#: each run wrote on the task (`meta.import.run_id`) are read too: a later run
#: that was discarded must not hide an earlier one that still stands.
UPDATED_LATER_SQL = (
    "SELECT DISTINCT t.id, t.title FROM pm_tasks t "
    "  JOIN pm_import_runs r ON r.organization_id = t.organization_id "
    "   AND r.id <> CAST(:run AS uuid) AND r.state <> 'discarded' "
    "   AND (r.id::text = t.origin->>'updated_by_run' "
    "        OR EXISTS (SELECT 1 FROM pm_activities a WHERE a.task_id = t.id "
    "                    AND a.meta->'import'->>'run_id' = r.id::text)) "
    " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'run_id' = CAST(:run AS text) "
    " LIMIT :n"
)
#: Rule 2 — a member edited or commented on a task this run made. The edit is
#: measured against the end of the LAST import that wrote the task, so an
#: update by a later run that was itself discarded is not a member's edit.
#:
#: A member's work on a task is not always an edit of the task row. A personal
#: triage (`pm_task_personal`) writes no activity and does not touch
#: `updated_at`, and neither does a watch, an attachment, a link or a board
#: position. Every one of those rows cascades with the task, so each is read.
#: The import writes none of them after it ends, and no personal row at all.
EDITED_SQL = (
    "SELECT t.id, t.title FROM pm_tasks t "
    " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'run_id' = CAST(:run AS text) "
    "   AND (t.updated_at > greatest(CAST(:finished AS timestamptz), "
    "          (SELECT r.finished_at FROM pm_import_runs r "
    "            WHERE r.id::text = t.origin->>'updated_by_run')) "
    "        OR EXISTS (SELECT 1 FROM pm_activities a "
    "                    WHERE a.task_id = t.id AND a.meta->'import' IS NULL) "
    "        OR EXISTS (SELECT 1 FROM pm_task_personal x WHERE x.task_id = t.id) "
    "        OR EXISTS (SELECT 1 FROM pm_task_watchers x WHERE x.task_id = t.id "
    "                    AND x.created_at > CAST(:finished AS timestamptz) AND "
    + _not_by_related_import_on_task("x.created_at")
    + ") "
    "        OR EXISTS (SELECT 1 FROM pm_task_attachments x WHERE x.task_id = t.id) "
    "        OR EXISTS (SELECT 1 FROM pm_task_links x "
    "                    WHERE (x.source_task_id = t.id OR x.target_task_id = t.id) "
    "                      AND x.created_at > CAST(:finished AS timestamptz)) "
    "        OR EXISTS (SELECT 1 FROM pm_view_task_positions x WHERE x.task_id = t.id)) "
    " LIMIT :n"
)
#: Rule 3 — a member added a task or a node inside a node this run made.
FOREIGN_TASKS_SQL = (
    "SELECT id, title FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) "
    "   AND project_id = ANY(CAST(:nodes AS uuid[])) "
    "   AND coalesce(origin->>'run_id', '') <> CAST(:run AS text) "
    " LIMIT :n"
)
FOREIGN_NODES_SQL = (
    "SELECT id, name AS title FROM pm_projects "
    " WHERE organization_id = CAST(:org AS uuid) "
    "   AND parent_project_id = ANY(CAST(:nodes AS uuid[])) "
    "   AND NOT id = ANY(CAST(:nodes AS uuid[])) "
    " LIMIT :n"
)
#: The tables that CASCADE with a node, and what a member made in each. The
#: import writes its rows in them before it ends, so a row made after the run
#: ended is a member's. `pm_project_grants` is here too: a grant an admin added
#: since would vanish with the space. The list is every `ON DELETE CASCADE`
#: foreign key to `pm_projects` but the tasks and nodes, which have their own
#: rules; `live_ws41_discard.py` reads the catalog and fails on a new one.
NODE_TABLES: dict[str, str] = {
    "pm_views": "a saved view",
    "pm_reports": "a saved report",
    "pm_custom_fields": "a custom field",
    "pm_task_statuses": "a status",
    "pm_task_types": "a task type",
    "pm_tags": "a tag",
    "pm_recurrences": "a recurring task",
    "pm_project_watchers": "a watcher",
    "pm_project_grants": "an access grant",
    "pm_activities": "a comment or an edit",
}
#: The node tables an import writes into a node it did not create (§6.9).
IMPORT_WRITES = ("pm_task_statuses", "pm_task_types", "pm_tags")
#: `pm_task_counters` holds no member work: it is the task-number counter.
NODE_TABLES_IGNORED = ("pm_task_counters",)
#: The tables that CASCADE with a task, and what rule 2 does with each. The
#: live test reads the catalog and fails on one that is in neither list.
TASK_TABLES_CHECKED = (
    "pm_activities",
    "pm_task_personal",
    "pm_task_watchers",
    "pm_task_attachments",
    "pm_task_links",
    "pm_view_task_positions",
)
TASK_TABLES_IGNORED: dict[str, str] = {
    "pm_task_assignees": "an assignment by a member writes an activity, which rule 2 reads",
    "pm_notifications": "a notification is a message to a member, not their work",
    "pm_intake": "an intake item a member converted names a task the member made",
}


def _node_work_sql() -> str:
    parts = [
        f"SELECT p.id, p.name || ': ' || '{label}' AS title FROM {table} x "
        "  JOIN pm_projects p ON p.id = x.project_id "
        " WHERE x.project_id = ANY(CAST(:nodes AS uuid[])) "
        "   AND x.created_at > CAST(:finished AS timestamptz) AND "
        + (_not_by_related_import_on_nodes("x.created_at") if table in IMPORT_WRITES else "TRUE")
        + (" AND x.meta->'import' IS NULL" if table == "pm_activities" else "")
        for table, label in NODE_TABLES.items()
    ]
    # A member renamed or moved a node the run made.
    parts.append(
        "SELECT id, name || ': renamed or changed' AS title FROM pm_projects "
        " WHERE id = ANY(CAST(:nodes AS uuid[])) AND updated_at > CAST(:finished AS timestamptz)"
    )
    return "(" + ") UNION ALL (".join(parts) + ") LIMIT :n"


NODE_WORK_SQL = _node_work_sql()
#: Hold every row the discard reads, so no member write lands between the
#: checks and the deletes: an edit or a comment on a task, and any row that
#: hangs off a node, takes a lock these conflict with.
LOCK_TASKS_SQL = (
    "SELECT id FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = CAST(:run AS text) "
    "FOR UPDATE"
)
LOCK_NODES_SQL = (
    "SELECT id FROM pm_projects "
    " WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:nodes AS uuid[])) "
    "FOR UPDATE"
)

COUNT_TASKS_SQL = (
    "SELECT count(*) FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = CAST(:run AS text)"
)
#: Comments and activity this run added to a task or node it did NOT create.
#: Its own tasks' rows go with them through the foreign keys.
DELETE_ACTIVITY_SQL = (
    "DELETE FROM pm_activities a "
    " WHERE a.organization_id = CAST(:org AS uuid) AND a.meta->'import'->>'run_id' = CAST(:run AS text) "
    "   AND NOT EXISTS (SELECT 1 FROM pm_tasks t "
    "                    WHERE t.id = a.task_id AND t.origin->>'run_id' = CAST(:run AS text)) "
    "RETURNING a.type"
)
DELETE_TASKS_SQL = "DELETE FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = CAST(:run AS text)"
#: The nodes, in one statement: the foreign keys cascade to their children in
#: any order, and a node already gone with its parent is simply not found.
DELETE_NODES_SQL = (
    "DELETE FROM pm_projects "
    " WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:nodes AS uuid[])) "
    "RETURNING id, parent_project_id, kind"
)
MARK_DISCARDED_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = 'discarded', updated_at = now(), "
    "       report = coalesce(report, '{}'::jsonb) || jsonb_build_object('discarded', CAST(:counts AS jsonb)) "
    " WHERE id = CAST(:run AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state IN ('done', 'failed') "
    "RETURNING *"
)


class DiscardRefused(Exception):
    """The run cannot be discarded. ``blocking`` names what stops it."""

    def __init__(self, message: str, blocking: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.blocking = blocking or []


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def discard_until(row: Any) -> datetime | None:
    """The last moment a run can be discarded, or None when it cannot be."""
    if row.state not in ("done", "failed") or row.finished_at is None:
        return None
    return row.finished_at + timedelta(days=DISCARD_DAYS)


def discardable(row: Any) -> bool:
    """Offer "Discard this import" now? The server decides, so the browser
    needs no clock of its own."""
    until = discard_until(row)
    return until is not None and datetime.now(UTC) <= until


def _named(rows: list[Any]) -> list[dict[str, str]]:
    return [{"id": str(r.id), "title": str(r.title or "")} for r in rows]


async def discard_written_run(
    db: Any, organization_id: str, row: Any
) -> tuple[Any, dict[str, Any]]:
    """Discard a ``done`` or ``failed`` run inside the caller's transaction.
    Returns the updated run row and the counts. Raises ``DiscardRefused``."""
    from gateway.routes.projects.imports import LOCK_ORG_SQL

    run = str(row.id)
    until = discard_until(row)
    if until is None:
        raise DiscardRefused(f"An import that is {row.state} has nothing to discard.")
    if datetime.now(UTC) > until:
        raise DiscardRefused(
            f"An import can be discarded for {DISCARD_DAYS} days after it ends. This one ended "
            f"on {row.finished_at:%Y-%m-%d}."
        )
    progress = _json(row.progress) or {}
    if "node_ids" in progress and "created_nodes" not in progress:
        raise DiscardRefused(
            "This import ran before discard existed, so it did not record which spaces it "
            "created. Delete its spaces by hand."
        )
    nodes = [str(n) for n in progress.get("created_nodes") or []]
    params = {"org": organization_id, "run": run, "n": NAME_AT_MOST, "nodes": nodes}

    await db.execute(text(LOCK_ORG_SQL), {"org": organization_id})
    if (await db.execute(text(WRITING_SQL), {"org": organization_id})).fetchone() is not None:
        raise DiscardRefused("An import is running. Wait for it to end, then discard.")
    await db.execute(text(LOCK_TASKS_SQL), params)
    if nodes:
        await db.execute(text(LOCK_NODES_SQL), params)

    later = (await db.execute(text(LATER_RUNS_SQL), params)).fetchall()
    if later:
        raise DiscardRefused(
            "A later import continued this one. Discard the later import first.",
            [{"id": str(r.id), "title": f"Import of {r.created_at:%Y-%m-%d %H:%M}"} for r in later],
        )
    updated = (await db.execute(text(UPDATED_LATER_SQL), params)).fetchall()
    if updated:
        raise DiscardRefused(
            "A later import updated tasks this one created. Discard the later import first.",
            _named(updated),
        )
    edited = (
        await db.execute(text(EDITED_SQL), {**params, "finished": row.finished_at})
    ).fetchall()
    if edited:
        raise DiscardRefused(
            "Somebody edited or commented on tasks this import created. A discard would "
            "delete their work.",
            _named(edited),
        )
    if nodes:
        foreign = (await db.execute(text(FOREIGN_TASKS_SQL), params)).fetchall()
        foreign += (await db.execute(text(FOREIGN_NODES_SQL), params)).fetchall()
        foreign += (
            await db.execute(text(NODE_WORK_SQL), {**params, "finished": row.finished_at})
        ).fetchall()
        if foreign:
            raise DiscardRefused(
                "Somebody added work inside the spaces this import created. A discard would "
                "delete it.",
                _named(foreign[:NAME_AT_MOST]),
            )

    tasks = int((await db.execute(text(COUNT_TASKS_SQL), params)).scalar() or 0)
    activity = (await db.execute(text(DELETE_ACTIVITY_SQL), params)).fetchall()
    await db.execute(text(DELETE_TASKS_SQL), params)
    gone = (await db.execute(text(DELETE_NODES_SQL), params)).fetchall() if nodes else []
    report = _json(row.report) or {}
    counts = {
        "nodes": len(gone),
        "spaces": sum(1 for r in gone if r.parent_project_id is None),
        "tasks": tasks,
        # Comments it added to tasks an EARLIER run created. Its own tasks'
        # comments went with them.
        "comments_elsewhere": sum(1 for r in activity if r.type == "comment"),
        # A failed run's report holds only its error; the count is in progress.
        "updates_kept": int(report.get("tasks_updated") or progress.get("updated") or 0),
    }
    saved = (
        await db.execute(
            text(MARK_DISCARDED_SQL),
            {"org": organization_id, "run": run, "counts": json.dumps(counts)},
        )
    ).fetchone()
    return saved, counts
