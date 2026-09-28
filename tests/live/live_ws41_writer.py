"""WS-41 I-3 — the writer lands the real export in pm_*, against a real Postgres (R8).

Spec `project-docs/specs/project_import.md` §5.3, §6, §7.3, §9 row I-3 · **D80**.

── What this proves ─────────────────────────────────────────────────────────

1. A run stopped after three batches is TAKEN OVER by a new lease once its
   heartbeat is stale. The old writer loses its next write, touches nothing,
   and the new one finishes.
2. The counts match the file.
3. Comments keep their dates, people map, nobody is notified.
4. A second run of the same file writes nothing AND creates no node: it
   reuses the first run's tree.
5. A run that cannot read its file ends `failed` with a reason.
6. In a second organization: an import into an EXISTING space follows the
   grammar, a task the pre-D52 importer wrote is skipped, and a re-run into
   the same space reuses its folders and projects.

It drives the REAL writer, which opens its own tenant sessions, so it points
the shared engine at the scratch database through `DATABASE_URL`. The writer
COMMITS, so this script works in two fresh organizations and deletes both.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws41_writer.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

os.environ["DATABASE_URL"] = os.environ.get("LIVE_DSN") or os.environ[
    "TENANT_LADDER_DATABASE_URL"
].replace("+psycopg", "+asyncpg")
os.environ["PROJECT_IMPORT_DIR"] = tempfile.mkdtemp(prefix="ws41-live-")
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from gateway.db import tenant_session
from gateway.routes.projects import import_writer, imports
from gateway.routes.projects.core import resolve_visibility_for
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.plan import ImportMapping, Target, build_plan
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "tests" / "unit" / "import_fixtures" / "clickup_workspace.csv"
MIGRATION = ROOT / "infra" / "postgres" / "219_pm_import_runs.sql"
TAG = uuid.uuid4().hex[:6]
ADMIN = f"admin.{TAG}@acme.test"
MEMBER = f"member.{TAG}@acme.test"
ADMIN2 = f"admin2.{TAG}@acme.test"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def one(org: str, sql: str, **params: object) -> object:
    async with tenant_session(org) as db:
        return (await db.execute(text(sql), {"org": org, **params})).scalar()


async def rows(org: str, sql: str, **params: object) -> list:
    async with tenant_session(org) as db:
        return list((await db.execute(text(sql), {"org": org, **params})).fetchall())


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else json.loads(value or "{}")


async def new_run(
    org: str, admin: str, bundle: object, raw: bytes, mapping: ImportMapping
) -> tuple[str, str]:
    """What POST /runs and POST /apply do, without an HTTP request."""
    run_id, lease = str(uuid.uuid4()), str(uuid.uuid4())
    async with tenant_session(org) as db:
        vis = await resolve_visibility_for(db, admin)
        facts = await imports._facts(db, bundle, mapping, vis, org)
        plan = build_plan(bundle, mapping, **facts)
        stored = imports._store(org, run_id, [(FIXTURE.name, raw)])
        await db.execute(
            text(imports.INSERT_RUN_SQL),
            {
                "id": run_id,
                "org": org,
                "who": admin,
                "source": "clickup",
                "files": json.dumps(stored),
                "mapping": mapping.model_dump_json(),
                "plan": json.dumps(plan, default=str),
            },
        )
        await db.execute(text(imports.LOCK_ORG_SQL), {"org": org})
        started = (
            await db.execute(text(imports.START_SQL), {"org": org, "id": run_id, "lease": lease})
        ).fetchone()
    check(
        f"run {run_id[:8]} plans ready and starts",
        bool(plan["ready"] and started),
        str(plan["errors"]),
    )
    return run_id, lease


async def seed(label: str, admins: list[str]) -> str:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.begin() as db:
        if (await db.execute(text("SELECT to_regclass('pm_import_runs')"))).scalar() is None:
            raw = await db.get_raw_connection()
            await raw.driver_connection.execute(MIGRATION.read_text(encoding="utf-8"))
        org = str(
            (
                await db.execute(
                    text(
                        "INSERT INTO organization (slug, display_name) VALUES (:s, :n) RETURNING id"
                    ),
                    {
                        "s": f"live-ws41w-{label}-{uuid.uuid4().hex[:8]}",
                        "n": f"WS-41 writer live {label}",
                    },
                )
            ).scalar_one()
        )
        for email in admins:
            await db.execute(
                text(
                    "INSERT INTO app_user (email, organization_id, status) VALUES (:e, CAST(:o AS uuid), 'active')"
                ),
                {"e": email, "o": org},
            )
    await eng.dispose()
    return org


async def drop(org: str) -> None:
    # The scratch database is SHARED between worktrees (H-172), and a
    # neighbour replaying migrations can deadlock this cascade. Retry, and
    # never let the cleanup hide the results.
    eng = create_async_engine(os.environ["DATABASE_URL"])
    for attempt in range(1, 6):
        try:
            async with eng.begin() as db:
                await db.execute(
                    text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org}
                )
            break
        except Exception as exc:
            if attempt == 5:
                print(f"WARN  cleanup of organization {org} failed: {type(exc).__name__}")
            await asyncio.sleep(2 * attempt)
    await eng.dispose()


async def main() -> None:
    raw = FIXTURE.read_bytes()
    bundle = clickup.parse([(FIXTURE.name, raw)])
    org = await seed("a", [ADMIN, MEMBER])
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.begin() as db:
        # The member carries the fixture's "Person 1" name, so the name match
        # proposes them.
        await db.execute(
            text(
                "INSERT INTO people (name, email, status, organization_id) "
                "VALUES ('Person 1', :e, 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"
            ),
            {"e": MEMBER, "o": org},
        )
        await db.execute(
            text("UPDATE people SET name = 'Person 1' WHERE lower(email) = :e"), {"e": MEMBER}
        )
    await eng.dispose()
    person1 = sum(1 for t in bundle.tasks if "name:person 1" in t.assignee_refs)
    org2 = await seed("b", [ADMIN2])
    try:
        await first_org(org, bundle, raw, person1)
        await second_org(org2, bundle, raw)
    finally:
        await drop(org)
        await drop(org2)


async def first_org(org: str, bundle: object, raw: bytes, person1: int) -> None:
    # ── 1. a crash after three batches, a takeover, a finish ────────────
    run_id, old_lease = await new_run(org, ADMIN, bundle, raw, ImportMapping())
    await import_writer.apply_run(org, run_id, old_lease, stop_after=3)
    mid = await one(
        org,
        "SELECT (progress->>'cursor')::int FROM pm_import_runs WHERE id = CAST(:id AS uuid)",
        id=run_id,
    )
    check("1.1 the 'crash' leaves three batches written", mid == 600, f"cursor={mid}")
    fresh = await one(
        org,
        imports.START_SQL.replace("RETURNING *", "RETURNING id"),
        id=run_id,
        lease=str(uuid.uuid4()),
    )
    check("1.2 a fresh heartbeat blocks a takeover", fresh is None, str(fresh))
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "UPDATE pm_import_runs SET heartbeat_at = now() - interval '5 minutes' WHERE id = CAST(:id AS uuid)"
            ),
            {"id": run_id},
        )
        new_lease = str(uuid.uuid4())
        taken = (
            await db.execute(
                text(imports.START_SQL), {"org": org, "id": run_id, "lease": new_lease}
            )
        ).fetchone()
    check("1.3 a stale heartbeat is taken over with a new lease", taken is not None)
    try:
        await import_writer.apply_run(org, run_id, old_lease)
        check("1.4 the old writer stops at once", False, "it ran")
    except import_writer.LeaseLost:
        check("1.4 the old writer stops at once", True)
    await import_writer.fail_run(org, run_id, old_lease, RuntimeError("late"))
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
    )
    check("1.5 the old writer cannot fail the run", state == "applying", str(state))
    await import_writer.apply_run(org, run_id, new_lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id)
    )
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
    )
    check("1.6 the new writer finishes", state == "done", f"state={state}")

    # ── 2. the counts match the file ────────────────────────────────────
    q = "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = :id"
    check("2.1 every task lands once", await one(org, q, id=run_id) == 2423)
    subs = await one(org, q + " AND parent_task_id IS NOT NULL", id=run_id)
    check("2.2 subtasks keep their parent", subs == 1270, f"subtasks={subs}")
    kinds = await tree_counts(org)
    check(
        "2.3 five spaces, nine folders, 48 projects",
        kinds == {(True, "project"): 5, (False, "folder"): 9, (False, "project"): 48},
        str(kinds),
    )
    closed = await one(org, q + " AND completed_at IS NOT NULL", id=run_id)
    check("2.4 1,647 closed tasks carry an estimated completion", closed == 1647, str(closed))
    no_done = await one(
        org,
        "SELECT count(*) FROM pm_projects p WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' "
        "   AND p.owns_statuses AND NOT EXISTS (SELECT 1 FROM pm_task_statuses s "
        "        WHERE s.project_id = p.id AND s.category = 'done')",
    )
    check("2.5 every status set holds a Done status (D79)", no_done == 0, str(no_done))
    due = await one(org, q + " AND due_at IS NOT NULL", id=run_id)
    noon = await one(
        org, q + " AND to_char(due_at AT TIME ZONE 'UTC', 'HH24:MI') = '06:30'", id=run_id
    )
    check("2.6 1,091 due dates, 1,075 at local noon", due == 1091 and noon == 1075, f"{due} {noon}")
    numbers = await one(
        org,
        "SELECT count(*) FROM (SELECT root_project_id, task_number FROM pm_tasks "
        " WHERE organization_id = CAST(:org AS uuid) GROUP BY 1, 2 HAVING count(*) > 1) d",
    )
    check("2.7 task numbers are unique per space", numbers == 0, str(numbers))

    # ── 3. comments, people, quiet ──────────────────────────────────────
    comments = await one(
        org,
        "SELECT count(*) FROM pm_activities a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.origin->>'run_id' = :id AND a.type = 'comment' AND a.created_at < now() - interval '1 day' "
        "   AND a.meta ? 'author_name'",
        id=run_id,
    )
    check("3.1 93 comments, each dated and named", comments == 93, str(comments))
    assigned = await one(
        org,
        "SELECT count(*) FROM pm_task_assignees a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.origin->>'run_id' = :id AND a.assignee = :m",
        id=run_id,
        m=MEMBER,
    )
    check(
        "3.2 'Person 1' lands on the matched member",
        assigned == person1,
        f"{assigned} vs {person1}",
    )
    notes = await one(
        org,
        "SELECT count(*) FROM pm_notifications n JOIN pm_tasks t ON t.id = n.task_id WHERE t.origin->>'run_id' = :id",
        id=run_id,
    )
    check("3.3 nobody is notified (§6.10)", notes == 0, str(notes))
    via = await one(
        org,
        "SELECT count(*) FROM pm_activities a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.origin->>'run_id' = :id AND a.meta ? 'via'",
        id=run_id,
    )
    check("3.4 no request header stamps imported rows", via == 0, str(via))
    check(
        "3.5 the report counts what it wrote",
        report.get("tasks_written") == 2423
        and report.get("comments_written") == 93
        and report.get("done_status_added") == 7
        and report.get("tags_dropped") == 0,
        json.dumps(
            {
                k: report.get(k)
                for k in (
                    "tasks_written",
                    "comments_written",
                    "done_status_added",
                    "tags_dropped",
                    "created",
                )
            }
        ),
    )
    per_space = await rows(
        org,
        "SELECT a.body FROM pm_activities a JOIN pm_projects p ON p.id = a.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND a.body LIKE 'Imported % tasks from ClickUp'",
    )
    totals = sum(int(r.body.split()[1]) for r in per_space)
    check(
        "3.6 each space names its own count, and they add up",
        totals == 2423 and len(per_space) == 5,
        f"{totals} over {len(per_space)}",
    )
    files = [
        p
        for p in Path(os.environ["PROJECT_IMPORT_DIR"]).rglob("*")
        if run_id in str(p) and p.is_file()
    ]
    check("3.7 the upload is deleted when the run is done", files == [], str(files))

    # ── 4. a second run of the same file writes and creates nothing ─────
    before_rerun = await one(org, "SELECT now()")
    again, lease = await new_run(org, ADMIN, bundle, raw, ImportMapping())
    busy = await one(org, imports.BUSY_SQL, id=str(uuid.uuid4()))
    check("4.1 while one run writes, a second start sees it", str(busy) == again, str(busy))
    await import_writer.apply_run(org, again, lease)
    second = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again)
    )
    check(
        "4.2 a re-run of the same file writes nothing and finds all 2,423 unchanged",
        second.get("tasks_written") == 0
        and second.get("tasks_unchanged") == 2423
        and second.get("tasks_updated") == 0
        and second.get("comments_written") == 0
        # The I-4 browser walk: a re-run said "Added a Done status to 7
        # lists" and added nothing. A reused list already holds its Done.
        and second.get("done_status_added") == 0
        and second.get("lanes_added") == 0,
        json.dumps(second)[:300],
    )
    created = second.get("created", {})
    check(
        "4.3 a re-run creates no node and reuses all 62",
        created.get("spaces") == 0
        and created.get("folders") == 0
        and created.get("projects") == 0
        and created.get("reused") == 62,
        json.dumps(created),
    )
    after = await tree_counts(org)
    check("4.4 the tree is still 5 / 9 / 48", after == kinds, str(after))

    # ── 4b. a NEW export of the same workspace updates the tasks ────────
    # Owner decision 2026-09-28 (§11 Q-5). Four tasks, four cases:
    #   A — the title changed in ClickUp, untouched here      → updated
    #   B — the priority changed in ClickUp                   → updated
    #   C — the title changed in BOTH places                  → member wins, conflict
    #   D — a member edited the description; ClickUp did not  → kept, no conflict
    # and A gains a new ClickUp comment, which lands once.
    import csv
    import io

    table = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    head = table[0]
    col = {name: i for i, name in enumerate(head)}
    roots = [r for r in table[1:] if r[col["Parent ID"]] == "null"]
    a, b, c, d = (roots[i][col["Task ID"]] for i in (0, 1, 2, 3))
    for row in table[1:]:
        tid = row[col["Task ID"]]
        if tid == a:
            row[col["Task Name"]] = "Renamed in ClickUp"
            row[col["Comments"]] = json.dumps(
                [
                    {
                        "text": "A new comment\n",
                        "by": "person1@example.com",
                        "date": "9/27/2026, 10:00:00 AM GMT+5:30",
                        "assigned": False,
                        "resolved": "N/A",
                    }
                ]
            )
        elif tid == b:
            row[col["Priority"]] = "1"
            # A status its list does not hold yet: the writer adds a lane to a
            # set an earlier run made, and the report must say so (I-4).
            row[col["Status"]] = "waiting on vendor"
        elif tid == c:
            row[col["Task Name"]] = "Renamed in ClickUp too"
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(table)
    edited = out.getvalue().encode("utf-8")
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "UPDATE pm_tasks SET title = 'A member renamed it' "
                " WHERE organization_id = CAST(:o AS uuid) AND origin->>'external_id' = :c"
            ),
            {"o": org, "c": c},
        )
        await db.execute(
            text(
                "UPDATE pm_tasks SET description = 'A member wrote this' "
                " WHERE organization_id = CAST(:o AS uuid) AND origin->>'external_id' = :d"
            ),
            {"o": org, "d": d},
        )
    edited_bundle = clickup.parse([(FIXTURE.name, edited)])
    update_run, lease = await new_run(org, ADMIN, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, update_run, lease)
    rep_ = as_dict(
        await one(
            org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=update_run
        )
    )
    check(
        "4b.1 the new export updates two tasks and keeps one member edit",
        rep_.get("tasks_updated") == 2
        and rep_.get("conflicts_kept") == 1
        and rep_.get("tasks_written") == 0
        and rep_.get("comments_written") == 1
        and rep_.get("lanes_added") == 1,
        json.dumps(
            {
                k: rep_.get(k)
                for k in (
                    "tasks_updated",
                    "tasks_unchanged",
                    "conflicts_kept",
                    "tasks_written",
                    "comments_written",
                    "lanes_added",
                    "created",
                )
            }
        ),
    )
    by_ref = {
        r.ref: r
        for r in await rows(
            org,
            "SELECT origin->>'external_id' AS ref, title, description, importance FROM pm_tasks "
            " WHERE organization_id = CAST(:org AS uuid) AND origin->>'external_id' = ANY(:refs)",
            refs=[a, b, c, d],
        )
    }
    check(
        "4b.2 A takes ClickUp's new title", by_ref[a].title == "Renamed in ClickUp", by_ref[a].title
    )
    check(
        "4b.3 B takes ClickUp's new priority (1 = Urgent = 3)",
        by_ref[b].importance == 3,
        str(by_ref[b].importance),
    )
    check(
        "4b.4 C keeps the member's title", by_ref[c].title == "A member renamed it", by_ref[c].title
    )
    check(
        "4b.5 D keeps the member's description",
        by_ref[d].description == "A member wrote this",
        str(by_ref[d].description),
    )
    a_comments = await one(
        org,
        "SELECT count(*) FROM pm_activities x JOIN pm_tasks t ON t.id = x.task_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :a "
        "   AND x.type = 'comment' AND x.body = 'A new comment'",
        a=a,
    )
    check("4b.6 the new comment lands once", a_comments == 1, str(a_comments))
    again_upd, lease = await new_run(org, ADMIN, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, again_upd, lease)
    rep2 = as_dict(
        await one(
            org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again_upd
        )
    )
    a_comments = await one(
        org,
        "SELECT count(*) FROM pm_activities x JOIN pm_tasks t ON t.id = x.task_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :a "
        "   AND x.type = 'comment' AND x.body = 'A new comment'",
        a=a,
    )
    touched = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
        "   AND origin->>'kind' = 'import' AND updated_at > :t",
        t=before_rerun,
    )
    # The 4b member edits and updates happen after this point, so compare the
    # rerun window only: `again` ran between `before_rerun` and 4b.
    check_rerun_quiet = touched  # read again below, once 4b has run
    check(
        "4b.7 the same export again changes nothing and adds no comment twice",
        rep2.get("tasks_updated") == 0 and rep2.get("comments_written") == 0 and a_comments == 1,
        json.dumps(
            {k: rep2.get(k) for k in ("tasks_updated", "conflicts_kept", "comments_written")}
        ),
    )

    # ── 4c. what the I-3b review found ──────────────────────────────────
    created_by_first = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
        "   AND origin->>'run_id' = :id",
        id=run_id,
    )
    check(
        "4c.1 origin.run_id still names the run that CREATED each task",
        created_by_first == 2423,
        str(created_by_first),
    )
    quiet_before = await one(org, "SELECT now()")
    idle, lease = await new_run(org, ADMIN, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, idle, lease)
    moved_at = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
        "   AND origin->>'kind' = 'import' AND updated_at > :t",
        t=quiet_before,
    )
    check(
        "4c.2 a no-op re-run moves no task's updated_at (the If-Match token)",
        moved_at == 0,
        f"{moved_at} moved; the earlier window saw {check_rerun_quiet}",
    )

    # The admin now maps "Person 1" to nobody. The SOURCE did not change, so
    # nobody is unassigned.
    assigned_before = await one(
        org, "SELECT count(*) FROM pm_task_assignees WHERE assignee = :m", m=MEMBER
    )
    remap, lease = await new_run(
        org, ADMIN, edited_bundle, edited, ImportMapping(people={"name:person 1": None})
    )
    await import_writer.apply_run(org, remap, lease)
    assigned_after = await one(
        org, "SELECT count(*) FROM pm_task_assignees WHERE assignee = :m", m=MEMBER
    )
    check(
        "4c.3 a changed people mapping unassigns nobody",
        assigned_after == assigned_before,
        f"{assigned_before} -> {assigned_after}",
    )

    # A member renames a status lane. The same export again must not bring the
    # old name back as a new lane, nor move a task.
    lane = (
        await rows(
            org,
            "SELECT s.id, s.project_id, (SELECT count(*) FROM pm_task_statuses x "
            "        WHERE x.project_id = s.project_id) AS n "
            "  FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
            " WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' "
            "   AND p.parent_project_id IS NOT NULL AND s.name = 'backlog' LIMIT 1",
        )
    )[0]
    async with tenant_session(org) as db:
        await db.execute(
            text("UPDATE pm_task_statuses SET name = 'Someday' WHERE id = CAST(:s AS uuid)"),
            {"s": str(lane.id)},
        )
    in_lane = await one(
        org, "SELECT count(*) FROM pm_tasks WHERE status_id = CAST(:s AS uuid)", s=str(lane.id)
    )
    renamed, lease = await new_run(org, ADMIN, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, renamed, lease)
    lanes_now = await one(
        org,
        "SELECT count(*) FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
        p=str(lane.project_id),
    )
    still_in = await one(
        org, "SELECT count(*) FROM pm_tasks WHERE status_id = CAST(:s AS uuid)", s=str(lane.id)
    )
    check(
        "4c.4 a member-renamed lane is followed: no duplicate lane, no task moves",
        lanes_now == lane.n and still_in == in_lane,
        f"lanes {lane.n}->{lanes_now}, tasks {in_lane}->{still_in}",
    )
    renamed_report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=renamed)
    )
    check(
        "4c.4b the report counts no added lane when every name has one",
        renamed_report.get("lanes_added") == 0,
        str(renamed_report.get("lanes_added")),
    )

    async with tenant_session(org) as db:
        _inherited_mapping, inherited_run = await imports._inherited_mapping(db, org, edited_bundle)
    check(
        "4c.5 a new upload of the same workspace starts from the NEWEST earlier run's mapping",
        inherited_run == renamed,
        str(inherited_run),
    )
    # I-4: the plan asks the writer's own rule before any write.
    digest = [hashlib.sha256(edited).hexdigest()]
    async with tenant_session(org) as db:
        same = await import_writer.continues_earlier(
            db, org, str(uuid.uuid4()), edited_bundle, ImportMapping(), digest
        )
        elsewhere = await import_writer.continues_earlier(
            db,
            org,
            str(uuid.uuid4()),
            edited_bundle,
            ImportMapping(target=Target(kind="new_space", name="Elsewhere")),
            digest,
        )
    check(
        "4c.6 the plan says a re-run continues, and a new destination does not",
        same is True and elsewhere is False,
        f"same={same} elsewhere={elsewhere}",
    )

    # A list moved to another space since: the old map is stale, so the list
    # is created again rather than reused with a wrong root.
    progress = as_dict(
        await one(
            org, "SELECT progress FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
        )
    )
    spaces = sorted(set(progress["roots"].values()))
    _list_ref, list_id = next(
        (ref, node)
        for ref, node in progress["nodes"].items()
        if progress["roots"][ref] == spaces[0]
    )
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "UPDATE pm_projects SET parent_project_id = CAST(:s AS uuid) WHERE id = CAST(:p AS uuid)"
            ),
            {"s": spaces[1], "p": list_id},
        )
    moved, lease = await new_run(org, ADMIN, bundle, raw, ImportMapping())
    await import_writer.apply_run(org, moved, lease)
    created = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=moved)
    ).get("created", {})
    check(
        "4.5 a list moved since is created again, the rest reused",
        created.get("projects") == 1 and created.get("reused") == 61,
        json.dumps(created),
    )

    # A DIFFERENT workspace whose Spaces carry the same names: new ids, the
    # same names. It must never land in this import's spaces (§6.9).
    other = raw.replace(b"zz", b"yy").replace(b"90000000", b"80000000")
    other_bundle = clickup.parse([(FIXTURE.name, other)])
    stranger, lease = await new_run(org, ADMIN, other_bundle, other, ImportMapping())
    await import_writer.apply_run(org, stranger, lease)
    report = as_dict(
        await one(
            org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=stranger
        )
    )
    created = report.get("created", {})
    check(
        "4.6 a different workspace with the same Space names gets new spaces",
        created.get("spaces") == 5
        and created.get("reused") == 0
        and report.get("tasks_written") == 2423,
        json.dumps(created),
    )

    # ── 5. a failure is recorded with its reason ────────────────────────
    broken, lease = await new_run(org, ADMIN, bundle, raw, ImportMapping())
    for path in Path(os.environ["PROJECT_IMPORT_DIR"]).rglob("*"):
        if broken in str(path) and path.is_file():
            path.write_bytes(b"changed")
    await import_writer._guarded(org, broken, lease)
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=broken
    )
    reason = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=broken)
    )
    check(
        "5.1 a run that cannot read its file ends 'failed' with a reason",
        state == "failed" and "Upload again" in str(reason.get("error")),
        f"{state} {reason}",
    )


async def second_org(org: str, bundle: object, raw: bytes) -> None:
    # A space the member made by hand, and one task the pre-D52 importer
    # wrote with a ClickUp id from the file.
    target, sid = str(uuid.uuid4()), str(uuid.uuid4())
    legacy_ref = next(t.ref for t in bundle.tasks if t.parent_ref is None)
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, source, created_by, owns_statuses) "
                "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), 'Hand-made space', 'manual', :me, true)"
            ),
            {"id": target, "o": org, "me": ADMIN2},
        )
        await db.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) VALUES (CAST(:p AS uuid), 'org', :me)"
            ),
            {"p": target, "me": ADMIN2},
        )
        await db.execute(
            text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, category, is_default) "
                "VALUES (CAST(:id AS uuid), CAST(:p AS uuid), 'To do', 1, 'todo', true)"
            ),
            {"id": sid, "p": target},
        )
        await db.execute(
            text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, status_id, title, source, "
                "  created_by, task_number, clickup_id) VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:p AS uuid), "
                "  CAST(:p AS uuid), CAST(:s AS uuid), 'old import', 'import', :me, 1, :c)"
            ),
            {"o": org, "p": target, "s": sid, "me": ADMIN2, "c": legacy_ref},
        )
        await db.execute(
            text(
                "INSERT INTO pm_task_counters (project_id, last_value) VALUES (CAST(:p AS uuid), 1)"
            ),
            {"p": target},
        )

    mapping = ImportMapping(target=Target(kind="existing", project_id=target))
    run_id, lease = await new_run(org, ADMIN2, bundle, raw, mapping)
    await import_writer.apply_run(org, run_id, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id)
    )
    check(
        "6.1 an import into an existing space finishes, and skips the old importer's task",
        report.get("tasks_written") == 2422 and report.get("tasks_skipped") == 1,
        json.dumps(report)[:240],
    )
    outside = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = :id "
        "   AND root_project_id <> CAST(:t AS uuid)",
        id=run_id,
        t=target,
    )
    check("6.2 every task lands in the chosen space", outside == 0, str(outside))
    shape = {
        (r.kind, r.parent): r.n
        for r in await rows(
            org,
            "SELECT coalesce(c.kind, 'project') AS kind, "
            "       CASE WHEN c.parent_project_id = CAST(:t AS uuid) THEN 'target' ELSE coalesce(p.kind, 'project') END "
            "         AS parent, count(*) AS n "
            "  FROM pm_projects c LEFT JOIN pm_projects p ON p.id = c.parent_project_id "
            " WHERE c.organization_id = CAST(:org AS uuid) AND c.source = 'import' GROUP BY 1, 2",
            t=target,
        )
    }
    check(
        "6.3 folders sit on the space, projects in folders (the grammar)",
        set(shape) == {("folder", "target"), ("project", "folder")}
        and shape[("project", "folder")] == 48,
        str(shape),
    )
    numbers = await one(
        org,
        "SELECT count(*) FROM (SELECT task_number FROM pm_tasks WHERE root_project_id = CAST(:t AS uuid) "
        " GROUP BY 1 HAVING count(*) > 1) d",
        t=target,
    )
    check(
        "6.4 the space's own task keeps its number; no number repeats", numbers == 0, str(numbers)
    )
    again, lease = await new_run(org, ADMIN2, bundle, raw, mapping)
    await import_writer.apply_run(org, again, lease)
    second = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again)
    )
    created = second.get("created", {})
    check(
        "6.5 a re-run into the same space reuses its folders and projects",
        created.get("folders") == 0
        and created.get("projects") == 0
        and second.get("tasks_written") == 0,
        json.dumps(created),
    )


async def tree_counts(org: str) -> dict:
    return {
        (r.parent_is_null, r.kind): r.n
        for r in await rows(
            org,
            "SELECT parent_project_id IS NULL AS parent_is_null, coalesce(kind,'project') AS kind, count(*) AS n "
            "  FROM pm_projects WHERE organization_id = CAST(:org AS uuid) AND source = 'import' GROUP BY 1, 2",
        )
    }


if __name__ == "__main__":
    asyncio.run(main())
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail if not ok else ''}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)
