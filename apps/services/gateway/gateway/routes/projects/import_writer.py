"""The file importer's writer — lands a confirmed run in ``pm_*``.

Spec: ``project-docs/specs/project_import.md`` §6, §7.3 · decision **D80**
(amends D52.2) · board **WS-41** slice I-3.

D80's second condition is that an import adds a CALLER, not a write path. So
every row goes through the helpers the Projects routes use: ``insert_row``,
``_seed_root``, ``next_task_number``, ``apply_task_tags``, ``insert_assignees``,
``ensure_watchers`` and ``record_activity``. ``tests/unit/test_pm_task_insert_sites.py``
fails if a ``pm_tasks`` row is written any other way.

**The job.** ``start`` runs :func:`apply_run` as an ``asyncio`` task. It binds
``tenant_session(organization_id)`` itself, from the RUN ROW, for every batch
(R5 (e)). Batch 0 writes every node and stores the node map in ``progress``.
Each later batch writes :data:`BATCH` tasks in one transaction and bumps
``heartbeat_at``. A gateway restart leaves the run ``applying`` with a stale
heartbeat, and ``POST …/apply`` resumes it from ``progress.cursor``. A resume is
safe, because each batch first skips every task that already exists (§6.9),
and the unique index on the import origin (migration 219) is the backstop.

**Quiet** (§6.10). The routes emit ``pm.task.created`` and write notifications
AFTER calling these helpers. The writer calls the helpers and nothing else, so
no workflow runs, no automation fires and nobody is pinged. The only watchers
are the mapped assignees, through ``ensure_watchers``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections import defaultdict
from typing import Any

from gateway.routes.projects.core import (
    _log,
    _tenant_session,
    insert_assignees,
    insert_row,
    next_task_number,
    record_activity,
    resolve_visibility_for,
)
from gateway.routes.projects.importer.bundle import ImportBundle, Task
from gateway.routes.projects.importer.layout import (
    SOURCE_LABEL,
    STAGE_COLOR,
    build_nodes,
    completed_estimate,
    description,
    due_instant,
    is_closed,
    order_tasks,
    origin,
    project_statuses,
)
from gateway.routes.projects.importer.plan import (
    ImportMapping,
    build_plan,
    resolve_people,
    resolve_statuses,
)
from gateway.routes.projects.tags import apply_task_tags
from gateway.routes.projects.tree import _seed_root
from gateway.routes.projects.watchers import ensure_watchers
from sqlalchemy import text

#: Tasks per transaction. Small enough that a batch commits in a few seconds,
#: so the heartbeat stays fresh and a crash loses little.
BATCH = 200
#: A run whose heartbeat is older than this is not being written by anybody.
STALE_AFTER = dt.timedelta(minutes=2)

_RUNNING: set[asyncio.Task[None]] = set()

#: SQLSTATEs that mean "try this transaction again": a deadlock, a
#: serialization failure, a lock that timed out. A deploy applies migrations
#: while an import may be running, and a migration's lock can deadlock a batch.
#: Each batch is one transaction that skips what exists, so a retry is safe.
TRANSIENT_SQLSTATES = frozenset({"40P01", "40001", "55P03"})
RETRIES = 4


def is_transient(err: BaseException) -> bool:
    """True when ``err``, or the driver error under it, carries a transient
    SQLSTATE. SQLAlchemy wraps asyncpg twice, so walk the chain."""
    seen: BaseException | None = err
    for _ in range(5):
        if seen is None:
            return False
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if code in TRANSIENT_SQLSTATES:
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__
    return False


async def _retrying(label: str, run_id: str, attempt_fn: Any) -> Any:
    """Run ``attempt_fn`` (one whole transaction) until it commits, retrying a
    transient error with a short, growing wait."""
    for attempt in range(1, RETRIES + 1):
        try:
            return await attempt_fn()
        except Exception as err:
            if attempt == RETRIES or not is_transient(err):
                raise
            _log.warning("projects.import.retry", run_id=run_id, step=label, attempt=attempt)
            await asyncio.sleep(0.5 * attempt)
    raise AssertionError("unreachable")


# ── the SQL (module constants, so the live test runs these exact strings) ───

PROGRESS_SQL = (
    "UPDATE pm_import_runs "
    "   SET progress = CAST(:progress AS jsonb), heartbeat_at = now(), updated_at = now() "
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state = 'applying' "
    "RETURNING id"
)
FINISH_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = :state, report = CAST(:report AS jsonb), progress = CAST(:progress AS jsonb), "
    "       heartbeat_at = now(), updated_at = now(), finished_at = now() "
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state = 'applying' "
    "RETURNING id"
)
#: A failure keeps `progress`, so the report can say how far the run got.
FAIL_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = 'failed', report = CAST(:report AS jsonb), "
    "       updated_at = now(), finished_at = now() "
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state = 'applying' "
    "RETURNING id"
)
#: The tasks of this batch that already exist in this organization: written by
#: an earlier run of this file, or by the pre-D52 importer (§11 Q-7).
EXISTING_IN_BATCH_SQL = (
    "SELECT origin->>'external_id' AS ref, id, root_project_id FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND origin->>'kind' = 'import' "
    "   AND origin->>'source' = :source AND origin->>'external_id' = ANY(:refs) "
    "UNION ALL "
    "SELECT clickup_id AS ref, id, root_project_id FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND :source = 'clickup' "
    "   AND clickup_id = ANY(:refs)"
)
TYPES_SQL = "SELECT id, name FROM pm_task_types WHERE project_id = CAST(:root AS uuid)"


class ImportRefused(Exception):
    """The run cannot apply as confirmed, for example because a mapped member
    left the organization after the dry run."""


def start(organization_id: str, run_id: str) -> None:
    """Run the writer in the background. The task is kept in :data:`_RUNNING`
    so the event loop does not drop it half way."""
    task = asyncio.create_task(_guarded(organization_id, run_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def _guarded(organization_id: str, run_id: str) -> None:
    try:
        await apply_run(organization_id, run_id)
    except Exception as err:
        _log.warning("projects.import.failed", run_id=run_id, error=type(err).__name__)
        await fail_run(organization_id, run_id, err)


# ── the run ─────────────────────────────────────────────────────────────────


async def apply_run(organization_id: str, run_id: str, *, stop_after: int | None = None) -> None:
    """Write the run. ``stop_after`` ends the job after that many task
    batches, which is how the live test simulates a crash and a resume."""
    from gateway.routes.projects import imports  # the route module imports this one

    async with _tenant_session(organization_id) as db:
        row = await imports._load_run(db, run_id, organization_id)
    if row.state != "applying":
        return
    admin = str(row.created_by)
    mapping = ImportMapping.model_validate(imports._json(row.mapping) or {})
    bundle = await imports._parse(
        row.source, imports._read_files(organization_id, run_id, imports._json(row.files))
    )
    progress: dict[str, Any] = dict(imports._json(row.progress) or {})

    # Re-check the plan against the database as it is NOW. A member mapped at
    # the dry run may have left since, and a target space may have moved.
    async def read_facts() -> dict[str, Any]:
        async with _tenant_session(organization_id) as db:
            vis = await resolve_visibility_for(db, admin)
            return await imports._facts(db, bundle, mapping, vis, organization_id)

    facts = await _retrying("facts", run_id, read_facts)
    plan = build_plan(bundle, mapping, **facts)
    if not plan["ready"]:
        raise ImportRefused("; ".join(plan["errors"]))
    people = resolve_people(bundle, mapping, facts["directory"])
    final = resolve_statuses(bundle, mapping)

    if not progress.get("nodes"):
        progress = await _retrying(
            "nodes",
            run_id,
            lambda: _write_nodes(organization_id, run_id, bundle, mapping, admin, final),
        )

    tasks = order_tasks(bundle)
    comments = defaultdict(list)
    for comment in bundle.comments:
        comments[comment.task_ref].append(comment)
    names = {p.ref: p.display_name for p in bundle.people}

    batches = 0
    while progress.get("cursor", 0) < len(tasks):
        if stop_after is not None and batches >= stop_after:
            return
        cursor = int(progress.get("cursor", 0))
        chunk = tasks[cursor : cursor + BATCH]

        async def write_batch(
            base: dict[str, Any] = progress, chunk: list[Task] = chunk, cursor: int = cursor
        ) -> dict[str, Any]:
            # A COPY: a retried transaction must not count the failed attempt.
            nxt = dict(base)
            async with _tenant_session(organization_id) as db:
                counts = await _write_tasks(
                    db,
                    organization_id,
                    run_id,
                    bundle,
                    chunk,
                    nxt,
                    people,
                    names,
                    final,
                    comments,
                    admin,
                )
                for key, value in counts.items():
                    nxt[key] = int(nxt.get(key, 0)) + value
                nxt["cursor"] = cursor + len(chunk)
                if await _save_progress(db, organization_id, run_id, nxt) is None:
                    # The run left `applying` under us. Stop; the batch rolls back.
                    raise ImportRefused("the run is no longer applying")
            return nxt

        progress = await _retrying(f"batch {cursor // BATCH + 1}", run_id, write_batch)
        batches += 1

    await _retrying(
        "finish", run_id, lambda: _finish(organization_id, run_id, bundle, plan, progress, admin)
    )


async def _write_nodes(
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    mapping: ImportMapping,
    admin: str,
    final: dict[str, Any],
) -> dict[str, Any]:
    """Batch 0 — every space, folder and project, with each project's own
    status set, in ONE transaction with the progress row. A crash before the
    commit leaves nothing; after it, the node map is on the run."""
    specs, home = build_nodes(bundle, mapping.target)
    statuses, done_added = project_statuses(bundle, final)
    node_ids: dict[str, str] = {}
    root_of: dict[str, str] = {}
    status_ids: dict[str, list[list[str]]] = {}
    counts = {"spaces": 0, "folders": 0, "projects": 0}
    label = SOURCE_LABEL.get(bundle.source, bundle.source)

    async with _tenant_session(organization_id) as db:
        for spec in specs:
            if spec.existing_id:
                node_ids[spec.ref] = spec.existing_id
                root_of[spec.ref] = spec.existing_id
                continue
            parent = node_ids.get(spec.parent_ref) if spec.parent_ref else None
            row = await insert_row(
                db,
                "pm_projects",
                {
                    "name": spec.name[:200],
                    "organization_id": organization_id,
                    "parent_project_id": parent,
                    "kind": "folder" if spec.kind == "folder" else "project",
                    # A space and a project own a status set; a folder never holds
                    # tasks, so it inherits (migration 196).
                    "owns_statuses": spec.kind != "folder",
                    "source": "import",
                    "created_by": admin,
                },
            )
            node_id = str(row.id)
            node_ids[spec.ref] = node_id
            root_of[spec.ref] = root_of[spec.parent_ref] if spec.parent_ref else node_id
            if spec.kind == "space":
                counts["spaces"] += 1
                await _seed_root(db, node_id, admin)
                await insert_row(
                    db,
                    "pm_project_grants",
                    {
                        "project_id": node_id,
                        "subject": mapping.grant,
                        "created_by": admin,
                    },
                )
                await record_activity(
                    db,
                    activity_type="system",
                    created_by=admin,
                    project_id=node_id,
                    body=f"Space '{spec.name}' imported from {label}",
                    meta={"import": {"run_id": run_id, "source": bundle.source}},
                )
            elif spec.kind == "folder":
                counts["folders"] += 1
            else:
                counts["projects"] += 1
                ids = []
                for position, (name, category) in enumerate(statuses.get(spec.ref, []), start=1):
                    status = await insert_row(
                        db,
                        "pm_task_statuses",
                        {
                            "project_id": node_id,
                            "name": name,
                            "color": STAGE_COLOR[category],
                            "position": position * 10,
                            "category": category,
                            "is_default": position == 1,
                        },
                    )
                    ids.append([name.lower(), str(status.id)])
                status_ids[spec.ref] = ids
        progress = {
            "nodes": {ref: node_ids[node] for ref, node in home.items()},
            "roots": {ref: root_of[node] for ref, node in home.items()},
            "statuses": status_ids,
            "created": counts,
            "done_status_added": done_added,
            "cursor": 0,
        }
        if await _save_progress(db, organization_id, run_id, progress) is None:
            raise ImportRefused("the run is no longer applying")
    return progress


async def _write_tasks(
    db: Any,
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    chunk: list[Task],
    progress: dict[str, Any],
    people: dict[str, str | None],
    names: dict[str, str],
    final: dict[str, Any],
    comments: dict[str, list[Any]],
    admin: str,
) -> dict[str, int]:
    """One batch, in the caller's transaction."""
    now = dt.datetime.now(dt.UTC)
    source = bundle.source
    system_actor = f"system:import:{source}"
    refs = [t.ref for t in chunk] + [t.parent_ref for t in chunk if t.parent_ref]
    known = {
        str(r.ref): (str(r.id), str(r.root_project_id))
        for r in (
            await db.execute(
                text(EXISTING_IN_BATCH_SQL),
                {"org": organization_id, "source": source, "refs": refs},
            )
        ).fetchall()
    }
    types = await _types_by_root(db, set(progress["roots"].values()))
    counts = {"written": 0, "skipped": 0, "comments": 0, "detached": 0, "completed_estimated": 0}

    for task in chunk:
        if task.ref in known:
            counts["skipped"] += 1
            continue
        project_id = progress["nodes"][task.container_ref]
        root = progress["roots"][task.container_ref]
        status_id = _status_for(task, progress["statuses"][task.container_ref], final)

        parent_id = None
        if task.parent_ref:
            parent = known.get(task.parent_ref)
            # A parent in another tree (the old importer's rows) is not a
            # parent here: the task lands at the top and the report counts it.
            if parent is not None and parent[1] == root:
                parent_id = parent[0]
            else:
                counts["detached"] += 1

        members = [m for r in task.assignee_refs if (m := people.get(r))]
        unassigned = [names.get(r, r) for r in task.assignee_refs if not people.get(r)]
        closed = is_closed(task, final)
        completed_at = (
            completed_estimate(task, comments.get(task.ref, []), bundle.utc_offset, now)
            if closed
            else None
        )
        counts["completed_estimated"] += 1 if closed else 0

        values: dict[str, Any] = {
            "project_id": project_id,
            "root_project_id": root,
            "status_id": status_id,
            "parent_task_id": parent_id,
            "type_id": await _type_id(db, types, root, task.task_type, admin),
            "title": task.title[:500],
            "description": description(task, unassigned, source),
            "estimate_mins": task.estimate_mins,
            "start_date": task.start_date,
            "due_at": due_instant(task, bundle.utc_offset),
            "completed_at": completed_at,
            "created_by": system_actor,
            "source": "import",
            "origin": origin(
                task,
                source,
                run_id,
                completed_at_estimated=True if closed else None,
                assignee_names=unassigned,
            ),
            "task_number": await next_task_number(db, root),
        }
        if task.created_at is not None:
            values["created_at"] = task.created_at
        if task.importance is not None:
            values["importance"] = task.importance
        if task.tags:
            values["tags"] = await apply_task_tags(db, root, task.tags, by=admin)

        row = await insert_row(db, "pm_tasks", values)
        task_id = str(row.id)
        known[task.ref] = (task_id, root)
        counts["written"] += 1
        if members:
            await insert_assignees(db, task_id, members, by=admin)
            await ensure_watchers(db, task_id, members, by=admin)
        for comment in comments.get(task.ref, []):
            author = people.get(comment.author_ref or "") if comment.author_ref else None
            await record_activity(
                db,
                activity_type="comment",
                created_by=author or system_actor,
                task_id=task_id,
                body=comment.body_md,
                meta={
                    "import": {
                        "run_id": run_id,
                        "author": names.get(comment.author_ref or "", comment.author_ref),
                    }
                },
                created_at=comment.created_at,
            )
            counts["comments"] += 1
    return counts


def _status_for(task: Task, statuses: list[list[str]], final: dict[str, Any]) -> str:
    """The exact status id (D79 rule 1): the task's mapped name in its own
    project's set, else the set's first status."""
    if task.status_name is not None:
        wanted = final[task.status_name][0].lower()
        for name, status_id in statuses:
            if name == wanted:
                return status_id
    return statuses[0][1]


async def _types_by_root(db: Any, roots: set[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for root in roots:
        rows = (await db.execute(text(TYPES_SQL), {"root": root})).fetchall()
        out[root] = {str(r.name).lower(): str(r.id) for r in rows}
    return out


async def _type_id(
    db: Any, types: dict[str, dict[str, str]], root: str, name: str | None, admin: str
) -> str | None:
    """§6.5 — the source type by name onto the root's types, created when
    missing. No name takes the root's default type (``None`` here)."""
    if not name:
        return None
    key = name.strip().lower()
    known = types.setdefault(root, {})
    if key not in known:
        row = await insert_row(
            db,
            "pm_task_types",
            {
                "project_id": root,
                "name": name.strip()[:60],
                "is_default": False,
                "is_system": False,
                "is_epic": False,
            },
        )
        known[key] = str(row.id)
    return known[key]


async def _save_progress(
    db: Any, organization_id: str, run_id: str, progress: dict[str, Any]
) -> Any:
    return (
        await db.execute(
            text(PROGRESS_SQL),
            {"id": run_id, "org": organization_id, "progress": json.dumps(progress)},
        )
    ).fetchone()


async def _finish(
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    plan: dict[str, Any],
    progress: dict[str, Any],
    admin: str,
) -> None:
    from gateway.routes.projects import imports

    report = {
        "created": progress.get("created", {}),
        "tasks_written": progress.get("written", 0),
        "tasks_skipped": progress.get("skipped", 0),
        "subtasks_detached": progress.get("detached", 0),
        "comments_written": progress.get("comments", 0),
        "completed_at_estimated": progress.get("completed_estimated", 0),
        "done_status_added": progress.get("done_status_added", 0),
        "people_unassigned": sum(1 for p in plan["people"] if p["member"] is None),
        "warnings": plan["warnings"],
        "losses": plan["losses"],
        "space_ids": sorted(set(progress.get("roots", {}).values())),
    }
    label = SOURCE_LABEL.get(bundle.source, bundle.source)
    async with _tenant_session(organization_id) as db:
        for root in report["space_ids"]:
            await record_activity(
                db,
                activity_type="system",
                created_by=admin,
                project_id=root,
                body=f"Imported {report['tasks_written']} tasks from {label}",
                meta={"import": {"run_id": run_id, "source": bundle.source}},
            )
        await db.execute(
            text(FINISH_SQL),
            {
                "id": run_id,
                "org": organization_id,
                "state": "done",
                "report": json.dumps(report),
                "progress": json.dumps(progress),
            },
        )
    imports._discard_files(organization_id, run_id)
    _log.info("projects.import.done", run_id=run_id, tasks=report["tasks_written"])


async def fail_run(organization_id: str, run_id: str, err: BaseException) -> None:
    """Mark the run failed and drop its files. The rows already written stay:
    a new upload of the same file skips them and writes the rest."""
    from gateway.routes.projects import imports

    detail = str(err) if isinstance(err, ImportRefused) else type(err).__name__
    try:
        async with _tenant_session(organization_id) as db:
            await db.execute(
                text(FAIL_SQL),
                {
                    "id": run_id,
                    "org": organization_id,
                    "report": json.dumps({"error": detail[:500]}),
                },
            )
    finally:
        imports._discard_files(organization_id, run_id)
