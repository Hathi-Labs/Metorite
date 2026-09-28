"""The file importer's writer — lands a confirmed run in ``pm_*``.

Spec: ``project-docs/specs/project_import.md`` §6, §7.3 · decision **D80**
(amends D52.2) · board **WS-41** slice I-3.

D80's second condition is that an import adds a CALLER, not a write path. So
every row goes through the helpers the Projects routes use: ``insert_row``,
``_seed_root``, ``reserve_task_numbers``, ``apply_task_tags``,
``insert_assignees``, ``ensure_watchers`` and ``record_activity``.
``tests/unit/test_pm_task_insert_sites.py`` fails if a ``pm_tasks`` row is
written any other way.

**The job.** ``start`` runs :func:`apply_run` as an ``asyncio`` task. It binds
``tenant_session(organization_id)`` itself, from the RUN ROW, for every
transaction (R5 (e)).

1. Batch 0 writes the node tree and each project's status set, in ONE
   transaction with the progress row. A node an earlier run of the same
   source already wrote for the same target is REUSED, not created again, so
   a re-run creates nothing and a re-upload after a failure continues in the
   same tree (§6.9).
2. Each later batch first reads which of its tasks exist, reserves task
   numbers in a short transaction of its own, then writes :data:`BATCH` tasks
   in one transaction and saves ``progress.cursor``.
3. A ticker bumps ``heartbeat_at`` every :data:`HEARTBEAT_EVERY`, so a slow
   batch never looks dead.

**One writer.** ``POST …/apply`` writes a LEASE id into ``progress.lease``.
Every write the job makes is guarded by it. A resume after a restart takes a
new lease, so a writer that is somehow still alive loses its next write and
stops, and it never marks the run failed or deletes the upload.

**Quiet** (§6.10). The routes emit ``pm.task.created`` and write notifications
AFTER calling the shared helpers. The writer calls the helpers and nothing
else. ``test_import_writer.py`` fails if this module calls ``emit`` or
``notify``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
from collections import Counter, defaultdict
from typing import Any

from fastapi import HTTPException
from gateway.routes.projects.core import (
    ACTOR_VIA,
    _log,
    _tenant_session,
    insert_assignees,
    insert_row,
    record_activity,
    reserve_task_numbers,
    resolve_visibility_for,
    status_owner_id,
    update_row,
    vocabulary_scope,
)
from gateway.routes.projects.importer.bundle import ImportBundle, Task
from gateway.routes.projects.importer.layout import (
    SOURCE_LABEL,
    STAGE_COLOR,
    STAGE_ORDER,
    build_nodes,
    comment_key,
    completed_estimate,
    description,
    due_instant,
    merge_fields,
    order_tasks,
    origin,
    project_statuses,
    snapshot,
    source_values,
)
from gateway.routes.projects.importer.plan import (
    ImportMapping,
    build_plan,
    resolve_people,
    resolve_statuses,
)
from gateway.routes.projects.tags import (
    MAX_TAG,
    MAX_TAGS_PER_PROJECT,
    MAX_TAGS_PER_TASK,
    apply_task_tags,
    load_registry,
    normalise_tag,
)
from gateway.routes.projects.tree import _seed_root
from gateway.routes.projects.watchers import ensure_watchers
from sqlalchemy import text

#: Tasks per transaction. Small enough that a batch commits in seconds.
BATCH = 200
#: How often the ticker proves the writer is alive. The resume threshold in
#: ``imports.START_SQL`` and ``imports.BUSY_SQL`` is 120 s, four ticks.
HEARTBEAT_EVERY = 30.0

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

#: Every write the job makes names its lease (see the module docstring).
_MINE = (
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "   AND state = 'applying' AND progress->>'lease' = :lease "
)
PROGRESS_SQL = (
    "UPDATE pm_import_runs "
    "   SET progress = CAST(:progress AS jsonb), heartbeat_at = now(), updated_at = now() "
    + _MINE
    + "RETURNING id"
)
HEARTBEAT_SQL = "UPDATE pm_import_runs SET heartbeat_at = now() " + _MINE + "RETURNING id"
FINISH_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = 'done', report = CAST(:report AS jsonb), progress = CAST(:progress AS jsonb), "
    "       heartbeat_at = now(), updated_at = now(), finished_at = now() " + _MINE + "RETURNING id"
)
#: A failure keeps `progress`, so the report can say how far the run got.
FAIL_SQL = (
    "UPDATE pm_import_runs "
    "   SET state = 'failed', report = CAST(:report AS jsonb), "
    "       updated_at = now(), finished_at = now() " + _MINE + "RETURNING id"
)
#: The tasks of this batch that already exist in this organization: written by
#: an earlier run of this file, or by the pre-D52 importer (§11 Q-7).
EXISTING_IN_BATCH_SQL = (
    "SELECT origin->>'external_id' AS ref, id, root_project_id, 'import' AS kind FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND origin->>'kind' = 'import' "
    "   AND origin->>'source' = :source AND origin->>'external_id' = ANY(:refs) "
    "UNION ALL "
    "SELECT clickup_id AS ref, id, root_project_id, 'legacy' AS kind FROM pm_tasks "
    " WHERE organization_id = CAST(:org AS uuid) AND :source = 'clickup' "
    "   AND clickup_id = ANY(:refs)"
)
#: The state of the imported tasks a batch may update (I-3b, §6.9).
EXISTING_ROWS_SQL = (
    "SELECT t.id, t.project_id, t.root_project_id, t.title, t.description, t.status_id, "
    "       t.due_at, t.start_date, t.importance, t.estimate_mins, t.tags, t.completed_at, "
    "       t.origin, (p.personal_owner IS NOT NULL) AS in_personal "
    "  FROM pm_tasks t JOIN pm_projects p ON p.id = t.project_id "
    " WHERE t.organization_id = CAST(:org AS uuid) AND t.id = ANY(CAST(:ids AS uuid[]))"
)
EXISTING_ASSIGNEES_SQL = (
    "SELECT task_id, assignee FROM pm_task_assignees WHERE task_id = ANY(CAST(:ids AS uuid[]))"
)
#: The source comments already imported, by key, so a re-import adds each once.
EXISTING_COMMENT_KEYS_SQL = (
    "SELECT task_id, meta->'import'->>'comment_key' AS comment_key, created_at, body "
    "  FROM pm_activities "
    " WHERE task_id = ANY(CAST(:ids AS uuid[])) AND type = 'comment' AND meta ? 'import'"
)
REMOVE_ASSIGNEES_SQL = (
    "DELETE FROM pm_task_assignees  WHERE task_id = CAST(:tid AS uuid) AND assignee = ANY(:who)"
)
#: The node maps of earlier runs of the same source in this organization,
#: newest first. Batch 0 reuses a node only when this run CONTINUES one of
#: them (see :func:`_earlier_nodes`).
EARLIER_NODES_SQL = (
    "SELECT id, files, mapping->'target' AS target, mapping->>'grant' AS grant_subject, "
    "       progress->'node_ids' AS node_ids, progress->'statuses' AS statuses "
    "  FROM pm_import_runs "
    " WHERE organization_id = CAST(:org AS uuid) AND source = :source "
    "   AND id <> CAST(:id AS uuid) AND progress ? 'node_ids' "
    " ORDER BY created_at DESC"
)
#: The runs that already wrote a task of THIS file: the proof that this run
#: continues them rather than importing a different workspace.
CONTINUED_RUNS_SQL = (
    "SELECT DISTINCT r.run_id FROM pm_tasks t, "
    "       LATERAL (VALUES (t.origin->>'run_id'), (t.origin->>'updated_by_run')) AS r(run_id) "
    " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'kind' = 'import' "
    "   AND t.origin->>'source' = :source AND t.origin->>'external_id' = ANY(:refs) "
    "   AND r.run_id IS NOT NULL"
)
#: A node is reused only where it still stands: alive, not archived, and
#: under the same parent (a move re-stamps the roots, so the old map is stale).
LIVE_NODES_SQL = (
    "SELECT id, parent_project_id FROM pm_projects "
    " WHERE organization_id = CAST(:org AS uuid) AND id = ANY(CAST(:ids AS uuid[])) "
    "   AND archived_at IS NULL"
)
STATUSES_OF_SQL = (
    "SELECT id, name, category, position FROM pm_task_statuses "
    " WHERE project_id = CAST(:project AS uuid) ORDER BY position"
)
TYPES_SQL = (
    "SELECT id, name, coalesce(is_epic, false) AS is_epic FROM pm_task_types "
    " WHERE project_id = CAST(:root AS uuid)"
)
GROUP_EXISTS_SQL = (
    "SELECT 1 FROM org_group WHERE organization_id = CAST(:org AS uuid) AND slug = :slug"
)


class ImportRefused(Exception):
    """The run cannot apply as confirmed. The message is the admin's reason."""


class LeaseLost(Exception):
    """Another writer took this run over. Stop, and touch nothing."""


def start(organization_id: str, run_id: str, lease: str) -> None:
    """Run the writer in the background. The task is kept in :data:`_RUNNING`
    so the event loop does not drop it half way."""
    task = asyncio.create_task(_guarded(organization_id, run_id, lease))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def _guarded(organization_id: str, run_id: str, lease: str) -> None:
    # The job inherits the request's context. The admin's `X-Actor-Via`
    # header must not stamp two thousand imported rows.
    ACTOR_VIA.set("")
    ticker = asyncio.create_task(_tick(organization_id, run_id, lease))
    try:
        await apply_run(organization_id, run_id, lease)
    except LeaseLost:
        _log.warning("projects.import.lease_lost", run_id=run_id)
    except Exception as err:
        _log.warning("projects.import.failed", run_id=run_id, error=type(err).__name__)
        await fail_run(organization_id, run_id, lease, err)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


async def _tick(organization_id: str, run_id: str, lease: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_EVERY)
        try:
            async with _tenant_session(organization_id) as db:
                alive = (
                    await db.execute(
                        text(HEARTBEAT_SQL),
                        {"id": run_id, "org": organization_id, "lease": lease},
                    )
                ).fetchone()
        except Exception:  # a missed tick is not a failure
            continue
        if alive is None:
            return


# ── the run ─────────────────────────────────────────────────────────────────


async def apply_run(
    organization_id: str, run_id: str, lease: str, *, stop_after: int | None = None
) -> None:
    """Write the run. ``stop_after`` ends the job after that many task
    batches, which is how the live test simulates a crash and a resume."""
    from gateway.routes.projects import imports  # the route module imports this one

    async with _tenant_session(organization_id) as db:
        row = await imports._load_run(db, run_id, organization_id)
    progress: dict[str, Any] = dict(imports._json(row.progress) or {})
    if row.state != "applying" or progress.get("lease") != lease:
        raise LeaseLost()
    admin = str(row.created_by)
    mapping = ImportMapping.model_validate(imports._json(row.mapping) or {})
    bundle = await imports._parse(
        row.source, imports._read_files(organization_id, run_id, imports._json(row.files))
    )

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
        hashes = [str(f.get("sha256")) for f in (imports._json(row.files) or [])]
        progress = await _retrying(
            "nodes",
            run_id,
            lambda: _write_nodes(
                organization_id, run_id, lease, bundle, mapping, admin, final, progress, hashes
            ),
        )

    tasks = order_tasks(bundle)
    comments: dict[str, list[Any]] = defaultdict(list)
    for comment in bundle.comments:
        comments[comment.task_ref].append(comment)
    names = {p.ref: p.display_name for p in bundle.people}

    batches = 0
    while progress.get("cursor", 0) < len(tasks):
        if stop_after is not None and batches >= stop_after:
            return
        cursor = int(progress.get("cursor", 0))
        chunk = tasks[cursor : cursor + BATCH]
        numbers = await _retrying(
            f"numbers {cursor // BATCH + 1}",
            run_id,
            lambda chunk=chunk, base=progress: _reserve_numbers(
                organization_id, bundle.source, chunk, base
            ),
        )

        async def write_batch(
            base: dict[str, Any] = progress,
            chunk: list[Task] = chunk,
            cursor: int = cursor,
            numbers: dict[str, list[int]] = numbers,
        ) -> dict[str, Any]:
            # COPIES: a retried transaction must not count the failed attempt,
            # and must hand out the same reserved numbers again.
            nxt = dict(base)
            pool = {root: list(block) for root, block in numbers.items()}
            async with _tenant_session(organization_id) as db:
                counts, by_root = await _write_tasks(
                    db,
                    organization_id,
                    run_id,
                    bundle,
                    chunk,
                    nxt,
                    pool,
                    people,
                    names,
                    final,
                    comments,
                    admin,
                )
                for key, value in counts.items():
                    nxt[key] = int(nxt.get(key, 0)) + value
                written = dict(nxt.get("written_by_root", {}))
                for root, n in by_root.items():
                    written[root] = int(written.get(root, 0)) + n
                nxt["written_by_root"] = written
                nxt["cursor"] = cursor + len(chunk)
                await _save_progress(db, organization_id, run_id, lease, nxt)
            return nxt

        progress = await _retrying(f"batch {cursor // BATCH + 1}", run_id, write_batch)
        batches += 1

    await _retrying(
        "finish",
        run_id,
        lambda: _finish(organization_id, run_id, lease, bundle, plan, progress, admin),
    )


async def _write_nodes(
    organization_id: str,
    run_id: str,
    lease: str,
    bundle: ImportBundle,
    mapping: ImportMapping,
    admin: str,
    final: dict[str, Any],
    base: dict[str, Any],
    file_hashes: list[str],
) -> dict[str, Any]:
    """Batch 0 — the node tree and each project's status set, in ONE
    transaction with the progress row. A node an earlier run of THIS export
    wrote, for the same target and grant, is reused (§6.9)."""
    specs, home = build_nodes(bundle, mapping.target)
    statuses, done_refs = project_statuses(bundle, final)
    # Only a project this run CREATES gains the Done. A reused one got it from
    # the run that made it; a lane added there shows in `lanes_added`.
    done_added = 0
    node_ids: dict[str, str] = {}
    root_of: dict[str, str] = {}
    status_ids: dict[str, list[list[str]]] = {}
    counts = {"spaces": 0, "folders": 0, "projects": 0, "reused": 0}
    lanes_added: list[str] = []
    label = SOURCE_LABEL.get(bundle.source, bundle.source)

    async with _tenant_session(organization_id) as db:
        reusable, continued, earlier_status = await _earlier_nodes(
            db, organization_id, run_id, bundle, mapping, file_hashes
        )
        grant_checked = False
        for spec in specs:
            if spec.existing_id:
                node_ids[spec.ref] = spec.existing_id
                root_of[spec.ref] = spec.existing_id
                continue
            parent = node_ids.get(spec.parent_ref) if spec.parent_ref else None
            candidate = reusable.get(spec.ref)
            # Reuse only a node that still sits under the parent this run just
            # resolved. Its root then follows from the parent, never from an
            # old map, so a node moved since is created again instead.
            if candidate is not None and candidate[1] == parent:
                node_ids[spec.ref] = candidate[0]
                root_of[spec.ref] = root_of[spec.parent_ref] if spec.parent_ref else candidate[0]
                counts["reused"] += 1
                if spec.kind == "project":
                    status_ids[spec.ref] = await _reuse_statuses(
                        db,
                        node_ids[spec.ref],
                        statuses.get(spec.ref, []),
                        earlier_status.get(spec.ref),
                        added=lanes_added,
                    )
                continue
            if spec.kind == "space" and not grant_checked:
                await _check_grant(db, organization_id, mapping.grant)
                grant_checked = True
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
                done_added += spec.ref in done_refs
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
                    ids.append([name.lower(), str(status.id), category])
                status_ids[spec.ref] = ids
        progress = {
            **base,
            "nodes": {ref: node_ids[node] for ref, node in home.items()},
            "roots": {ref: root_of[node] for ref, node in home.items()},
            "node_ids": node_ids,
            "statuses": status_ids,
            "created": counts,
            "continued_from": sorted(continued),
            "done_status_added": done_added,
            "lanes_added": len(lanes_added),
            "cursor": 0,
        }
        await _save_progress(db, organization_id, run_id, lease, progress)
    return progress


async def continues_earlier(
    db: Any,
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    mapping: ImportMapping,
    file_hashes: list[str],
) -> bool:
    """Will this run go into an earlier import's tree? The plan asks, so the
    wizard can say so before any write (I-4). It is the writer's OWN rule,
    read-only: a second copy of the rule is how the note would start to lie."""
    nodes, _continued, _statuses = await _earlier_nodes(
        db, organization_id, run_id, bundle, mapping, file_hashes
    )
    return bool(nodes)


async def _earlier_nodes(
    db: Any,
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    mapping: ImportMapping,
    file_hashes: list[str],
) -> tuple[dict[str, tuple[str, str | None]], set[str], dict[str, dict[str, str]]]:
    """``spec ref → (node id, its parent now)`` for the nodes this run may
    reuse, the ids of the runs it continues, and ``project ref → {status name
    → status id}`` as those runs recorded it. The last lets a status a member
    RENAMED since keep its tasks, instead of coming back as a new lane.

    A run CONTINUES an earlier one only when all of these hold:

    * it is the same export: the same file hashes, or the earlier run already
      wrote a task of this file. Space and Folder refs are NAMES, so a
      different workspace with a Space called "Team Space" must never land
      in an earlier import's "Team Space";
    * the target is the same: a new space, or the same existing space;
    * the grant and the admin's space name are the same, so a reused space
      never ignores what the admin confirmed this time.
    """
    target = mapping.target
    wanted = (
        target.kind,
        target.project_id if target.kind == "existing" else None,
        (target.name or "").strip(),
        mapping.grant,
    )
    wrote_this_file = {
        str(r.run_id)
        for r in (
            await db.execute(
                text(CONTINUED_RUNS_SQL),
                {
                    "org": organization_id,
                    "source": bundle.source,
                    "refs": [t.ref for t in bundle.tasks],
                },
            )
        ).fetchall()
        if r.run_id
    }
    found: dict[str, str] = {}
    continued: set[str] = set()
    earlier_status: dict[str, dict[str, str]] = {}
    for row in (
        await db.execute(
            text(EARLIER_NODES_SQL),
            {"org": organization_id, "source": bundle.source, "id": run_id},
        )
    ).fetchall():
        hashes = sorted(str(f.get("sha256")) for f in (_json(row.files) or []))
        if hashes != sorted(file_hashes) and str(row.id) not in wrote_this_file:
            continue
        earlier = _json(row.target) or {}
        kind = earlier.get("kind", "new_space")
        seen = (
            kind,
            earlier.get("project_id") if kind == "existing" else None,
            (earlier.get("name") or "").strip(),
            row.grant_subject or "org",
        )
        if seen != wanted:
            continue
        continued.add(str(row.id))
        for ref, node in (_json(row.node_ids) or {}).items():
            found.setdefault(ref, str(node))
        for ref, entries in (_json(row.statuses) or {}).items():
            names = earlier_status.setdefault(ref, {})
            for entry in entries:
                names.setdefault(str(entry[0]), str(entry[1]))
    if not found:
        return {}, continued, earlier_status
    alive = {
        str(r.id): (str(r.parent_project_id) if r.parent_project_id else None)
        for r in (
            await db.execute(
                text(LIVE_NODES_SQL),
                {"org": organization_id, "ids": sorted(set(found.values()))},
            )
        ).fetchall()
    }
    reusable = {ref: (node, alive[node]) for ref, node in found.items() if node in alive}
    return reusable, continued, earlier_status


async def _reuse_statuses(
    db: Any,
    project_id: str,
    wanted: list[tuple[str, Any]],
    earlier: dict[str, str] | None = None,
    added: list[str] | None = None,
) -> list[list[str]]:
    """The status set a reused project uses, plus any name this run needs that
    it lacks. The set is the one its status OWNER holds (migration 196), which
    is the project itself unless somebody set it to inherit.

    A name the set already holds keeps ITS stage. The task then takes that
    stage, and so does its completion date, so the lane and the date agree.
    A new name goes in after the last status of its own stage or an earlier one."""
    owner = await status_owner_id(db, project_id)
    rows = (await db.execute(text(STATUSES_OF_SQL), {"project": owner})).fetchall()
    have = [[str(r.name).lower(), str(r.id), str(r.category)] for r in rows]
    positions = [(STAGE_ORDER.get(str(r.category), 2), int(r.position or 0)) for r in rows]
    by_id = {status_id: (n, c) for n, status_id, c in have}
    for name, category in sorted(wanted, key=lambda nc: STAGE_ORDER[nc[1]]):
        if name.lower() in {n for n, _, _ in have}:
            continue
        # A member renamed the lane an earlier run made for this name: keep
        # using that lane, under the source's name, with its own stage.
        renamed = (earlier or {}).get(name.lower())
        if renamed in by_id:
            have.append([name.lower(), renamed, by_id[renamed][1]])
            continue
        stage = STAGE_ORDER[category]
        position = max((pos for st, pos in positions if st <= stage), default=0) + 1
        status = await insert_row(
            db,
            "pm_task_statuses",
            {
                "project_id": owner,
                "name": name,
                "color": STAGE_COLOR[category],
                "position": position,
                "category": category,
                "is_default": False,
            },
        )
        have.append([name.lower(), str(status.id), category])
        positions.append((stage, position))
        if added is not None:
            # A lane in a set this run did not create: the report names it.
            added.append(name)
    return have


async def _check_grant(db: Any, organization_id: str, grant: str) -> None:
    """A group grant is checked again here: the mapping can change between
    the apply route's check and this write."""
    if not grant.startswith("group:"):
        return
    found = (
        await db.execute(
            text(GROUP_EXISTS_SQL),
            {"org": organization_id, "slug": grant.split(":", 1)[1]},
        )
    ).fetchone()
    if found is None:
        raise ImportRefused(f"There is no group {grant!r} in this organization.")


async def _reserve_numbers(
    organization_id: str, source: str, chunk: list[Task], progress: dict[str, Any]
) -> dict[str, list[int]]:
    """A short transaction of its own: read which tasks of the chunk exist,
    and reserve one number per task still to write, per space. A skipped task
    burns no number, so a re-run leaves the counters untouched."""
    refs = [t.ref for t in chunk]
    async with _tenant_session(organization_id) as db:
        known = {
            str(r.ref)
            for r in (
                await db.execute(
                    text(EXISTING_IN_BATCH_SQL),
                    {"org": organization_id, "source": source, "refs": refs},
                )
            ).fetchall()
        }
        need = Counter(progress["roots"][t.container_ref] for t in chunk if t.ref not in known)
        out: dict[str, list[int]] = {}
        for root, count in need.items():
            first = await reserve_task_numbers(db, root, count)
            out[root] = list(range(first, first + count))
    return out


async def _write_tasks(
    db: Any,
    organization_id: str,
    run_id: str,
    bundle: ImportBundle,
    chunk: list[Task],
    progress: dict[str, Any],
    numbers: dict[str, list[int]],
    people: dict[str, str | None],
    names: dict[str, str],
    final: dict[str, Any],
    comments: dict[str, list[Any]],
    admin: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """One batch, in the caller's transaction. A task new to this
    organization is inserted. A task an earlier import wrote is UPDATED by
    the three-way rule (§6.9, owner decision 2026-09-28). A task the pre-D52
    importer wrote is skipped: it holds no snapshot to merge against."""
    source = bundle.source
    refs = [t.ref for t in chunk] + [t.parent_ref for t in chunk if t.parent_ref]
    found = (
        await db.execute(
            text(EXISTING_IN_BATCH_SQL),
            {"org": organization_id, "source": source, "refs": refs},
        )
    ).fetchall()
    known = {str(r.ref): (str(r.id), str(r.root_project_id)) for r in found}
    kinds = {str(r.ref): str(r.kind) for r in found}
    existing = await _existing_state(
        db, organization_id, [known[t.ref][0] for t in chunk if kinds.get(t.ref) == "import"]
    )
    types = await _types_by_root(db, set(progress["roots"].values()))
    counts: Counter[str] = Counter(
        {
            "written": 0,
            "skipped": 0,
            "updated": 0,
            "unchanged": 0,
            "fields_updated": 0,
            "conflicts": 0,
            "moved": 0,
            "comments": 0,
            "detached": 0,
            "completed_estimated": 0,
            "tags_dropped": 0,
            "epic_demoted": 0,
        }
    )
    by_root: Counter[str] = Counter()

    for task in chunk:
        kind = kinds.get(task.ref)
        if kind == "legacy":
            counts["skipped"] += 1
            continue
        incoming, extras = await _incoming(
            db,
            task,
            bundle,
            progress,
            known,
            types,
            people,
            names,
            final,
            comments,
            admin,
            counts,
            for_update=kind == "import",
        )
        if kind == "import":
            task_id = known[task.ref][0]
            await _update_task(
                db,
                task_id,
                task,
                incoming,
                extras,
                existing,
                comments,
                bundle,
                run_id,
                admin,
                counts,
                people,
                names,
            )
            continue
        root = progress["roots"][task.container_ref]
        values = {
            **{k: v for k, v in incoming.items() if k != "assignees"},
            "project_id": progress["nodes"][task.container_ref],
            "root_project_id": root,
            "parent_task_id": extras["parent_id"],
            "type_id": extras["type_id"],
            "completed_at": extras["completed_at"],
            "created_by": f"system:import:{source}",
            "source": "import",
            "origin": {
                **extras["origin"],
                "run_id": run_id,
                "import_values": snapshot(incoming),
                "import_source": snapshot(extras["source"]),
            },
            "task_number": await _next_number(db, numbers, root),
        }
        if task.created_at is not None:
            values["created_at"] = task.created_at
        if values.get("importance") is None:
            values.pop("importance", None)
        if not values.get("tags"):
            values.pop("tags", None)
        row = await insert_row(db, "pm_tasks", values)
        task_id = str(row.id)
        known[task.ref] = (task_id, root)
        counts["written"] += 1
        counts["completed_estimated"] += 1 if extras["closed"] else 0
        by_root[root] += 1
        members = incoming["assignees"]
        if members:
            await insert_assignees(db, task_id, members, by=admin)
            await ensure_watchers(db, task_id, members, by=admin)
        counts["comments"] += await _add_comments(
            db, task_id, task, comments, set(), bundle, run_id, people, names
        )
    return dict(counts), dict(by_root)


async def _incoming(
    db: Any,
    task: Task,
    bundle: ImportBundle,
    progress: dict[str, Any],
    known: dict[str, tuple[str, str]],
    types: dict[str, dict[str, tuple[str, bool]]],
    people: dict[str, str | None],
    names: dict[str, str],
    final: dict[str, Any],
    comments: dict[str, list[Any]],
    admin: str,
    counts: Counter[str],
    *,
    for_update: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the source says this task is now: the UPDATABLE fields, and the
    structure an insert needs as well. One place, so an insert and an update
    can never read the file two ways.

    ``for_update`` touches nothing: an update never re-parents or re-types a
    task, never registers a tag it may not write, and never counts a
    detach, a demotion or a dropped tag (§6.9). The caller registers the tags
    only when the merge writes them."""
    if for_update:
        counts = Counter()
    now = dt.datetime.now(dt.UTC)
    source = bundle.source
    root = progress["roots"][task.container_ref]
    status_id, stage = _status_for(task, progress["statuses"][task.container_ref], final)

    parent_id = None
    if task.parent_ref:
        parent = known.get(task.parent_ref)
        # A parent in another tree (the old importer's rows) is not a
        # parent here: the task lands at the top and the report counts it.
        if parent is not None and parent[1] == root:
            parent_id = parent[0]
        else:
            counts["detached"] += 1

    type_id, epic = (None, False) if for_update else await _type_id(db, types, root, task.task_type)
    if epic and parent_id:
        # §3.4: an Epic has no parent. Keep the parent, drop the type.
        type_id = None
        counts["epic_demoted"] += 1

    members = [m for r in task.assignee_refs if (m := people.get(r))]
    unassigned = [names.get(r, r) for r in task.assignee_refs if not people.get(r)]
    # The stage of the status the task LANDS in, not of the mapping: in a
    # reused set, an existing name keeps its own stage.
    closed = stage in ("done", "cancelled")
    tags: list[str] = []
    fitted: list[str] = []
    if task.tags:
        fitted, dropped = await _fit_tags(db, root, task.tags)
        counts["tags_dropped"] += dropped
        if fitted and not for_update:
            tags = await apply_task_tags(db, root, fitted, by=admin)
        elif fitted:
            # The registry's spelling, WITHOUT registering: the merge may keep
            # the member's tags, and then nothing enters the registry.
            registry = await load_registry(db, root)
            tags = [registry.get(t.lower(), t) for t in fitted]
    incoming = {
        "title": task.title[:500],
        "description": description(task, unassigned, source),
        "status_id": status_id,
        "due_at": due_instant(task, bundle.utc_offset),
        "start_date": task.start_date,
        "importance": task.importance,
        "estimate_mins": task.estimate_mins,
        "tags": tags,
        "assignees": sorted(set(members)),
    }
    extras = {
        "parent_id": parent_id,
        "type_id": type_id,
        "closed": closed,
        "completed_at": (
            completed_estimate(task, comments.get(task.ref, []), bundle.utc_offset, now)
            if closed
            else None
        ),
        "origin": origin(
            task,
            source,
            "",
            completed_at_estimated=True if closed else None,
            assignee_names=unassigned,
        ),
        "project_id": progress["nodes"][task.container_ref],
        "root": root,
        "fitted_tags": fitted,
        "source": source_values(task, source, bundle.utc_offset),
    }
    return incoming, extras


async def _existing_state(
    db: Any, organization_id: str, ids: list[str]
) -> dict[str, dict[str, Any]]:
    """The current state of the imported tasks a batch may update."""
    if not ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in (
        await db.execute(text(EXISTING_ROWS_SQL), {"org": organization_id, "ids": ids})
    ).fetchall():
        out[str(r.id)] = {
            "row": r,
            "assignees": [],
            "comment_keys": set(),
            "origin": _json(r.origin) or {},
        }
    for r in (await db.execute(text(EXISTING_ASSIGNEES_SQL), {"ids": ids})).fetchall():
        if str(r.task_id) in out:
            out[str(r.task_id)]["assignees"].append(str(r.assignee))
    for r in (await db.execute(text(EXISTING_COMMENT_KEYS_SQL), {"ids": ids})).fetchall():
        if str(r.task_id) in out:
            # A comment written before comment keys existed (I-3) is matched
            # on its date and text, so a re-run never writes it twice.
            keys = out[str(r.task_id)]["comment_keys"]
            if r.comment_key:
                keys.add(str(r.comment_key))
            else:
                keys.add(_legacy_key(r.created_at, r.body))
                # An undated source comment was stamped with the import time,
                # so its text is the only thing that can match it again.
                keys.add(_legacy_key(None, r.body))
    return out


async def _update_task(
    db: Any,
    task_id: str,
    task: Task,
    incoming: dict[str, Any],
    extras: dict[str, Any],
    existing: dict[str, dict[str, Any]],
    comments: dict[str, list[Any]],
    bundle: ImportBundle,
    run_id: str,
    admin: str,
    counts: Counter[str],
    people: dict[str, str | None],
    names: dict[str, str],
) -> None:
    """The three-way update of one task this import wrote before (§6.9)."""
    state = existing.get(task_id)
    if state is None:
        counts["skipped"] += 1
        return
    row = state["row"]
    current = {
        "title": row.title,
        "description": row.description,
        "status_id": str(row.status_id) if row.status_id else None,
        "due_at": row.due_at,
        "start_date": row.start_date,
        "importance": row.importance,
        "estimate_mins": row.estimate_mins,
        "tags": list(row.tags or []),
        "assignees": state["assignees"],
    }
    # A task a member moved to another project, or that sits in another List
    # in the new file, keeps its status: the status set it would take is not
    # the set it lives in.
    # Its assignees stay too: a member may have moved it into a PERSONAL
    # project, and adding a person there would widen who can read it
    # (`assert_assignable_here`, core.py). So a moved task keeps its status
    # and its people.
    frozen: list[str] = []
    if str(row.project_id) != extras["project_id"]:
        # The status set it would take is not the set it lives in.
        frozen.append("status_id")
        counts["moved"] += 1
    if str(row.root_project_id) != extras["root"] or bool(row.in_personal):
        # It left the imported space, or sits in a PERSONAL project: a new
        # assignee there would widen who can read it (`assert_assignable_here`).
        frozen.append("assignees")
    merge = merge_fields(
        current,
        state["origin"].get("import_values"),
        incoming,
        last_source=state["origin"].get("import_source"),
        incoming_source=extras["source"],
        frozen=tuple(frozen),
    )

    values: dict[str, Any] = {k: v for k, v in merge.changes.items() if k != "assignees"}
    if "tags" in values and extras["fitted_tags"]:
        # Registered only now that the merge writes them.
        values["tags"] = await apply_task_tags(
            db, str(row.root_project_id), extras["fitted_tags"], by=admin
        )
    if "status_id" in values:
        # The completion date follows the status, as a status move in the app does.
        if extras["closed"] and row.completed_at is None:
            values["completed_at"] = extras["completed_at"]
            counts["completed_estimated"] += 1
        elif not extras["closed"]:
            values["completed_at"] = None
    # `run_id` stays the run that CREATED the task (I-6 discards by it). The
    # last run that looked at it is `updated_by_run`.
    new_origin = {
        **state["origin"],
        "updated_by_run": run_id,
        "import_values": merge.new_snapshot,
        "import_source": merge.new_source,
    }
    for key in ("custom_id", "url", "time_spent_mins", "assignee_names"):
        if key in extras["origin"]:
            new_origin[key] = extras["origin"][key]
    if values:
        values["origin"] = new_origin
        await update_row(db, "pm_tasks", task_id, values)
    elif new_origin != state["origin"]:
        # Bookkeeping only, so no touch: `updated_at` is the If-Match token
        # (D-PM-20) and the delta feed's cursor, so a task that did not change
        # must keep it.
        await update_row(db, "pm_tasks", task_id, {"origin": new_origin}, touch=False)

    if "assignees" in merge.changes:
        await _sync_assignees(
            db, task_id, set(incoming["assignees"]), set(state["assignees"]), admin
        )

    changed = sorted(merge.changes)
    if changed:
        counts["updated"] += 1
        counts["fields_updated"] += len(changed)
    else:
        counts["unchanged"] += 1
    counts["conflicts"] += len(merge.conflicts)
    if changed or merge.conflicts:
        await _record_update(
            db, task_id, changed, list(merge.conflicts), bundle.source, run_id, admin
        )
    counts["comments"] += await _add_comments(
        db, task_id, task, comments, state["comment_keys"], bundle, run_id, people, names
    )


async def _sync_assignees(
    db: Any, task_id: str, wanted: set[str], have: set[str], admin: str
) -> None:
    """Make the assignee set ``wanted``. Only called when the merge decided
    the whole set changes, so a member's own additions are never removed."""
    if wanted - have:
        await insert_assignees(db, task_id, sorted(wanted - have), by=admin)
        await ensure_watchers(db, task_id, sorted(wanted - have), by=admin)
    if have - wanted:
        await db.execute(text(REMOVE_ASSIGNEES_SQL), {"tid": task_id, "who": sorted(have - wanted)})


async def _record_update(
    db: Any, task_id: str, changed: list[str], kept: list[str], source: str, run_id: str, admin: str
) -> None:
    """One timeline row per updated task, naming the fields. It notifies nobody."""
    label = SOURCE_LABEL.get(source, source)
    await record_activity(
        db,
        activity_type="system",
        created_by=admin,
        task_id=task_id,
        body=(
            f"Updated from {label}: {', '.join(changed)}"
            if changed
            else f"Kept member edits over {label}: {', '.join(kept)}"
        ),
        meta={
            "import": {
                "run_id": run_id,
                "source": source,
                "updated": changed,
                "kept_member_edits": kept,
            }
        },
    )


async def _add_comments(
    db: Any,
    task_id: str,
    task: Task,
    comments: dict[str, list[Any]],
    seen: set[str],
    bundle: ImportBundle,
    run_id: str,
    people: dict[str, str | None],
    names: dict[str, str],
) -> int:
    """Write the task's source comments that are not imported yet."""
    source = bundle.source
    written = 0
    for comment in comments.get(task.ref, []):
        key = comment_key(comment)
        legacy = _legacy_key(comment.created_at, comment.body_md)
        if key in seen or legacy in seen:
            continue
        author = people.get(comment.author_ref or "") if comment.author_ref else None
        await record_activity(
            db,
            activity_type="comment",
            created_by=author or f"system:import:{source}",
            task_id=task_id,
            body=comment.body_md,
            meta={
                "import": {"run_id": run_id, "source": source, "comment_key": key},
                "author_name": names.get(comment.author_ref or "", comment.author_ref),
            },
            created_at=comment.created_at,
        )
        seen.add(key)
        written += 1
    return written


def _legacy_key(created_at: Any, body: Any) -> str:
    stamp = created_at.astimezone(dt.UTC).replace(microsecond=0).isoformat() if created_at else ""
    return f"legacy|{stamp}|{str(body or '').strip()}"


async def _next_number(db: Any, numbers: dict[str, list[int]], root: str) -> int:
    """A reserved number, or one more when the reservation ran out. It runs
    out only when a task vanished between the pre-read and the batch."""
    pool = numbers.setdefault(root, [])
    if pool:
        return pool.pop(0)
    return await reserve_task_numbers(db, root, 1)


async def _fit_tags(db: Any, root: str, raw: list[str]) -> tuple[list[str], int]:
    """The tags that fit the shared caps (``tags.py``): a legal tag, at most
    :data:`MAX_TAGS_PER_TASK` on the task, and room in the space's registry.
    A tag that does not fit is dropped and counted, never a failed run."""
    clean: list[str] = []
    for tag in raw:
        try:
            name = normalise_tag(tag)
        except HTTPException:
            name = None
        if name and len(name) <= MAX_TAG and name.lower() not in {c.lower() for c in clean}:
            clean.append(name)
    clean = clean[:MAX_TAGS_PER_TASK]
    registry = await load_registry(db, root)
    fresh = [n for n in clean if n.lower() not in registry]
    if fresh:
        used = int(
            (
                await db.execute(
                    text(f"SELECT count(*) FROM pm_tags WHERE {vocabulary_scope()}"),
                    {"root": root},
                )
            ).scalar()
            or 0
        )
        room = max(MAX_TAGS_PER_PROJECT - used, 0)
        keep = {n.lower() for n in fresh[:room]}
        clean = [n for n in clean if n.lower() in registry or n.lower() in keep]
    distinct = set()
    for tag in raw:
        try:
            name = normalise_tag(tag)
        except HTTPException:
            name = None
        distinct.add((name or str(tag)).lower())
    # A tag that differs from another only by case is one tag, not a drop.
    return clean, len(distinct) - len(clean)


def _status_for(task: Task, statuses: list[list[str]], final: dict[str, Any]) -> tuple[str, str]:
    """The exact status (D79 rule 1) and ITS stage: the task's mapped name in
    its project's set, else the set's first status."""
    if task.status_name is not None:
        wanted = final[task.status_name][0].lower()
        for name, status_id, category in statuses:
            if name == wanted:
                return status_id, category
    return statuses[0][1], statuses[0][2]


async def _types_by_root(db: Any, roots: set[str]) -> dict[str, dict[str, tuple[str, bool]]]:
    out: dict[str, dict[str, tuple[str, bool]]] = {}
    for root in roots:
        rows = (await db.execute(text(TYPES_SQL), {"root": root})).fetchall()
        out[root] = {str(r.name).lower(): (str(r.id), bool(r.is_epic)) for r in rows}
    return out


async def _type_id(
    db: Any, types: dict[str, dict[str, tuple[str, bool]]], root: str, name: str | None
) -> tuple[str | None, bool]:
    """§6.5 — the source type by name onto the root's types, created when
    missing. Returns ``(type id, is_epic)``. No name takes the root's default
    type, which is ``None`` here."""
    if not name:
        return None, False
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
        known[key] = (str(row.id), False)
    return known[key]


async def _save_progress(
    db: Any, organization_id: str, run_id: str, lease: str, progress: dict[str, Any]
) -> None:
    saved = (
        await db.execute(
            text(PROGRESS_SQL),
            {
                "id": run_id,
                "org": organization_id,
                "lease": lease,
                "progress": json.dumps(progress),
            },
        )
    ).fetchone()
    if saved is None:
        # Another writer took the run over. This transaction rolls back.
        raise LeaseLost()


async def _finish(
    organization_id: str,
    run_id: str,
    lease: str,
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
        # Update mode (§6.9): a task an earlier import wrote.
        "tasks_updated": progress.get("updated", 0),
        "tasks_unchanged": progress.get("unchanged", 0),
        "fields_updated": progress.get("fields_updated", 0),
        "conflicts_kept": progress.get("conflicts", 0),
        "tasks_moved": progress.get("moved", 0),
        "subtasks_detached": progress.get("detached", 0),
        "comments_written": progress.get("comments", 0),
        "completed_at_estimated": progress.get("completed_estimated", 0),
        "done_status_added": progress.get("done_status_added", 0),
        "lanes_added": progress.get("lanes_added", 0),
        "tags_dropped": progress.get("tags_dropped", 0),
        "epic_demoted": progress.get("epic_demoted", 0),
        "people_unassigned": sum(1 for p in plan["people"] if p["member"] is None),
        "warnings": plan["warnings"],
        "losses": plan["losses"],
        "space_ids": sorted(set(progress.get("roots", {}).values())),
    }
    label = SOURCE_LABEL.get(bundle.source, bundle.source)
    written = progress.get("written_by_root", {})
    async with _tenant_session(organization_id) as db:
        for root in report["space_ids"]:
            await record_activity(
                db,
                activity_type="system",
                created_by=admin,
                project_id=root,
                body=f"Imported {int(written.get(root, 0))} tasks from {label}",
                meta={"import": {"run_id": run_id, "source": bundle.source}},
            )
        done = (
            await db.execute(
                text(FINISH_SQL),
                {
                    "id": run_id,
                    "org": organization_id,
                    "lease": lease,
                    "report": json.dumps(report),
                    "progress": json.dumps(progress),
                },
            )
        ).fetchone()
        if done is None:
            raise LeaseLost()
    imports._discard_files(organization_id, run_id)
    _log.info("projects.import.done", run_id=run_id, tasks=report["tasks_written"])


async def fail_run(organization_id: str, run_id: str, lease: str, err: BaseException) -> None:
    """Mark the run failed and drop its files, ONLY if this writer still holds
    the lease. The rows already written stay: a new upload of the same file
    reuses this run's nodes, skips its tasks and writes the rest."""
    from gateway.routes.projects import imports

    if isinstance(err, ImportRefused):
        detail = str(err)
    elif isinstance(err, HTTPException):
        detail = str(err.detail)
    else:
        detail = (
            f"{type(err).__name__}. The rows written so far stay; upload the file again to finish."
        )
    async with _tenant_session(organization_id) as db:
        failed = (
            await db.execute(
                text(FAIL_SQL),
                {
                    "id": run_id,
                    "org": organization_id,
                    "lease": lease,
                    "report": json.dumps({"error": detail[:500]}),
                },
            )
        ).fetchone()
    if failed is not None:
        imports._discard_files(organization_id, run_id)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value
