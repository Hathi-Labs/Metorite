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
   the same space reuses its folders and projects. The space gains only the
   status names it lacks, and keeps them after a discard (I-10).
7. In a third organization (I-10): a task with no status lands in Backlog.
   Then the tree is rebuilt by hand in its pre-I-10 shape, with one status
   set per List, and a re-upload of the same file adds 0 lanes.

I-10 also checks, in the first organization, that a new space holds ONE set
and that every List and Folder the run creates uses it (2b).

It drives the REAL writer, which opens its own tenant sessions, so it points
the shared engine at the scratch database through `DATABASE_URL`. The writer
COMMITS, so this script works in three fresh organizations and deletes them.

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
from gateway.routes.projects import import_discard, import_writer, imports
from gateway.routes.projects.core import resolve_visibility_for
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.layout import STAGE_ORDER
from gateway.routes.projects.importer.plan import (
    ImportMapping,
    Target,
    build_plan,
    propose_category,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "tests" / "unit" / "import_fixtures" / "clickup_workspace.csv"
MIGRATION = ROOT / "infra" / "postgres" / "219_pm_import_runs.sql"
TAG = uuid.uuid4().hex[:6]
ADMIN = f"admin.{TAG}@acme.test"
MEMBER = f"member.{TAG}@acme.test"
ADMIN2 = f"admin2.{TAG}@acme.test"
ADMIN3 = f"admin3.{TAG}@acme.test"
ADMIN4 = f"admin4.{TAG}@acme.test"
ADMIN5 = f"admin5.{TAG}@acme.test"
ADMIN6 = f"admin6.{TAG}@acme.test"
#: I-10 — what the ten ClickUp statuses of the fixture become (§6.3).
SIX = {"Backlog", "To do", "In progress", "Review", "On hold", "Done"}

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
    org3 = await seed("c", [ADMIN3])
    org4 = await seed("d", [ADMIN4])
    org5 = await seed("e", [ADMIN5])
    org6 = await seed("f", [ADMIN6])
    try:
        await first_org(org, bundle, raw, person1)
        await second_org(org2, bundle, raw)
        await third_org(org3, raw)
        await fourth_org(org4, raw)
        await fifth_org(org5, bundle, raw)
        await sixth_org(org6, bundle, raw)
    finally:
        await drop(org)
        await drop(org2)
        await drop(org3)
        await drop(org4)
        await drop(org5)
        await drop(org6)


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

    # ── 2b. I-10: one status set per space (§6.3) ───────────────────────
    owning = await one(
        org,
        "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
        "   AND source = 'import' AND parent_project_id IS NOT NULL AND owns_statuses",
    )
    check(
        "2b.1 every List and Folder the run created sets owns_statuses = false",
        owning == 0,
        str(owning),
    )
    holders = await rows(
        org,
        "SELECT p.parent_project_id IS NULL AS is_space, count(DISTINCT p.id) AS n "
        "  FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' GROUP BY 1",
    )
    check(
        "2b.2 a new space holds one set, and only the five spaces hold one",
        {(r.is_space, r.n) for r in holders} == {(True, 5)},
        str([(r.is_space, r.n) for r in holders]),
    )
    lanes = await rows(
        org,
        "SELECT s.name, s.category, count(*) AS n FROM pm_task_statuses s "
        "  JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' GROUP BY 1, 2",
    )
    stage = {r.name: r.category for r in lanes}
    per_name = {r.name: r.n for r in lanes}
    check(
        "2b.3 the sets hold the seed in every space, and the proposals: six names",
        set(stage) == SIX
        and all(per_name[n] == 5 for n in ("Backlog", "To do", "In progress", "Done"))
        and stage["Review"] == "in_progress"
        and stage["On hold"] == "backlog",
        str(sorted(per_name.items())),
    )
    foreign = await one(
        org,
        "SELECT count(*) FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'run_id' = :id "
        "   AND s.project_id <> t.root_project_id",
        id=run_id,
    )
    check("2b.4 every task's status comes from its space's set", foreign == 0, str(foreign))
    landed = {
        r.name: r.n
        for r in await rows(
            org,
            "SELECT s.name, count(*) AS n FROM pm_tasks t JOIN pm_task_statuses s "
            "    ON s.id = t.status_id "
            " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'run_id' = :id "
            " GROUP BY 1",
            id=run_id,
        )
    }
    check(
        "2b.5 the merges land: closed + done + completed in Done, the rest by name",
        landed
        == {
            "Done": 1647,
            "Backlog": 564,
            "To do": 125,
            "In progress": 78,
            "Review": 5,
            "On hold": 4,
        },
        str(landed),
    )
    misplaced = await one(
        org,
        "SELECT count(*) FROM pm_task_statuses n "
        "  JOIN pm_projects p ON p.id = n.project_id "
        "  JOIN pm_task_statuses lo ON lo.project_id = n.project_id "
        "  JOIN pm_task_statuses hi ON hi.project_id = n.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) "
        "   AND ((n.name = 'Review' AND lo.name = 'In progress' AND hi.name = 'Done') "
        "     OR (n.name = 'On hold' AND lo.name = 'Backlog' AND hi.name = 'To do')) "
        "   AND NOT (lo.position < n.position AND n.position < hi.position)",
    )
    check(
        "2b.6 Review lands after In progress, and On hold after Backlog",
        misplaced == 0,
        str(misplaced),
    )

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
        # I-10: D79 counts SETS that gained a Done, and the seed holds one.
        # Before I-10 it was 7, one per List with no done-like status.
        and report.get("done_status_added") == 0
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
    check(
        "3.5b the names a run adds to its own new spaces are not lanes_added (I-10)",
        report.get("lanes_added") == 0,
        str(report.get("lanes_added")),
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
    # The I-10 review, P2-b: a re-upload plans against the spaces' REAL sets,
    # so nothing the first run made reads as "new" on the Map step.
    async with tenant_session(org) as db:
        vis = await resolve_visibility_for(db, ADMIN)
        facts = await imports._facts(
            db,
            bundle,
            ImportMapping(),
            vis,
            org,
            run_id=str(uuid.uuid4()),
            file_hashes=[hashlib.sha256(raw).hexdigest()],
        )
    replan = build_plan(bundle, ImportMapping(), **facts)
    check(
        "4.2b a re-upload's plan reads the real sets: every status exists, none is new",
        replan["continues"]
        and all(r["existing"] for r in replan["statuses"])
        and {s["name"] for s in replan["target_statuses"]} == SIX,
        str([(r["name"], r["becomes"], r["existing"]) for r in replan["statuses"]]),
    )

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
    # old name back as a new lane, nor move a task. I-10: the lane is the
    # space's "Backlog", on a node with no parent, because a List made by
    # this run holds no set of its own.
    lane = (
        await rows(
            org,
            "SELECT s.id, s.project_id, (SELECT count(*) FROM pm_task_statuses x "
            "        WHERE x.project_id = s.project_id) AS n "
            "  FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
            " WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' "
            "   AND p.parent_project_id IS NULL AND s.name = 'Backlog' "
            "   AND EXISTS (SELECT 1 FROM pm_tasks t WHERE t.status_id = s.id) LIMIT 1",
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
    # I-10: the hand-made space held only "To do". It gains the names it
    # lacks, through the one insert path, and keeps its own "To do".
    lanes = await rows(
        org,
        "SELECT id, name FROM pm_task_statuses WHERE project_id = CAST(:t AS uuid)",
        t=target,
    )
    names = [r.name for r in lanes]
    check(
        "6.6 an existing space gains only the missing names, case-blind",
        set(names) == SIX
        and len(names) == len(SIX)
        and any(str(r.id) == sid and r.name == "To do" for r in lanes)
        and len(lanes) == 1 + int(report.get("lanes_added") or 0)
        and report.get("done_status_added") == 0,
        f"{sorted(names)} lanes_added={report.get('lanes_added')}",
    )
    elsewhere = await one(
        org,
        "SELECT count(*) FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'run_id' = :id "
        "   AND s.project_id <> CAST(:t AS uuid)",
        id=run_id,
        t=target,
    )
    owning = await one(
        org,
        "SELECT count(*) FROM pm_projects WHERE organization_id = CAST(:org AS uuid) "
        "   AND source = 'import' AND owns_statuses",
    )
    check(
        "6.7 every task takes a status of the space's set, and no node owns one",
        elsewhere == 0 and owning == 0,
        f"tasks elsewhere={elsewhere} owning nodes={owning}",
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
    # I-10: a discard removes the run's own nodes and tasks. The lanes it
    # added to a space it did not create stay (§6.9 "what a discard cannot
    # undo"). The later run goes first, because it continued the earlier one.
    refusals = []
    for discarded in (again, run_id):
        async with tenant_session(org) as db:
            row = (
                await db.execute(text(imports.LOAD_RUN_SQL), {"id": discarded, "org": org})
            ).fetchone()
        try:
            async with tenant_session(org) as db:
                await import_discard.discard_written_run(db, org, row)
        except import_discard.DiscardRefused as refused:
            refusals.append(refused.message)
    kept = await rows(
        org,
        "SELECT id, name FROM pm_task_statuses WHERE project_id = CAST(:t AS uuid)",
        t=target,
    )
    left = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) "
        "   AND origin->>'run_id' = :id",
        id=run_id,
    )
    check(
        "6.8 after a discard, the run's tasks are gone and the added lanes stay",
        not refusals and left == 0 and {str(r.id) for r in kept} == {str(r.id) for r in lanes},
        f"refusals={refusals} tasks left={left} lanes {len(lanes)}->{len(kept)}",
    )


def root_statuses(raw: bytes, statuses: list[str]) -> tuple[bytes, list[str]]:
    """The fixture with the first root tasks given these statuses, in order.
    Returns the file and the refs of the tasks it changed."""
    import csv
    import io

    table = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    col = {name: i for i, name in enumerate(table[0])}
    roots = [r for r in table[1:] if r[col["Parent ID"]] == "null"]
    for row, status in zip(roots, statuses, strict=False):
        row[col["Status"]] = status
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(table)
    return out.getvalue().encode("utf-8"), [r[col["Task ID"]] for r in roots[: len(statuses)]]


async def lane_of(org: str, ref: str) -> object:
    return await one(
        org,
        "SELECT s.name FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :r",
        r=ref,
    )


async def fourth_org(org: str, raw: bytes) -> None:
    """I-10 review fixes, on an existing space a member made (§6.3).

    P2-a: the space marks "Doing" with the retired ``is_default`` flag, and
    "To do" sits first by position. A task with no status takes the lane that
    ``core.load_default_status`` names, never the flagged one.

    P1-b: the space also holds the intake lane "Triage" (category
    ``triage``), first by position, and one task in the file has the ClickUp
    status "triage". No task lands in the intake lane."""
    target = str(uuid.uuid4())
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, source, created_by, owns_statuses) "
                "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), 'Member space', 'manual', :me, true)"
            ),
            {"id": target, "o": org, "me": ADMIN4},
        )
        await db.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), 'org', :me)"
            ),
            {"p": target, "me": ADMIN4},
        )
        for name, category, position, default in (
            # Where intake.load_triage_status puts its lane.
            ("Triage", "triage", 5, False),
            ("To do", "todo", 10, False),
            ("Doing", "in_progress", 20, True),
        ):
            await db.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, position, category, is_default) "
                    "VALUES (CAST(:p AS uuid), :n, :pos, :c, :d)"
                ),
                {"p": target, "n": name, "pos": position, "c": category, "d": default},
            )
    file, (blank_ref, triage_ref) = root_statuses(raw, ["", "triage"])
    bundle = clickup.parse([(FIXTURE.name, file)])
    mapping = ImportMapping(target=Target(kind="existing", project_id=target))
    async with tenant_session(org) as db:
        vis = await resolve_visibility_for(db, ADMIN4)
        facts = await imports._facts(db, bundle, mapping, vis, org)
    planned = {r["name"]: r for r in build_plan(bundle, mapping, **facts)["statuses"]}
    run_id, lease = await new_run(org, ADMIN4, bundle, file, mapping)
    await import_writer.apply_run(org, run_id, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id)
    )
    in_pen = await one(
        org,
        "SELECT count(*) FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE s.project_id = CAST(:t AS uuid) AND s.category = 'triage'",
        t=target,
    )
    pens = await one(
        org,
        "SELECT count(*) FROM pm_task_statuses WHERE project_id = CAST(:t AS uuid) "
        "   AND lower(name) = 'triage'",
        t=target,
    )
    check(
        "8.2 a ClickUp 'triage' lands in a new 'Triage (imported)', never in the intake lane",
        planned["triage"]["becomes"] == "Triage (imported)"
        and not planned["triage"]["existing"]
        and report.get("tasks_written") == 2423
        and await lane_of(org, triage_ref) == "Triage (imported)"
        and in_pen == 0
        and pens == 1,
        f"plan={planned['triage']['becomes']} report={report.get('error') or report.get('tasks_written')} "
        f"in_pen={in_pen} pens={pens}",
    )
    first = await one(
        org,
        "SELECT name FROM pm_task_statuses WHERE project_id = CAST(:t AS uuid) "
        "   AND category <> 'triage' ORDER BY position, name LIMIT 1",
        t=target,
    )
    landed = await lane_of(org, blank_ref)
    check(
        "8.1 a task with no status takes the first lane by position, not is_default",
        landed == first and landed != "Doing",
        f"landed={landed} first={first}",
    )


async def fifth_org(org: str, bundle: object, raw: bytes) -> None:
    """The I-10 review, P1-a. Run A leaves one List out, and a member renames
    the space's "Backlog". Run B continues and creates that List. Its tasks
    must land in the renamed lane, with no second "Backlog" in the ONE set."""
    from gateway.routes.projects.importer.layout import status_ids_by_name
    from gateway.routes.projects.importer.plan import ContainerChoice

    by_ref = {c.ref: c for c in bundle.containers}

    def space_of(ref: str) -> str:
        while by_ref[ref].parent_ref:
            ref = by_ref[ref].parent_ref
        return ref

    lists = [c.ref for c in bundle.containers if c.kind == "project"]
    left_out = next(
        ref
        for ref in lists
        if any(t.container_ref == ref and t.status_name == "backlog" for t in bundle.tasks)
        and sum(1 for other in lists if space_of(other) == space_of(ref)) > 1
    )
    in_list = sum(
        1 for t in bundle.tasks if t.container_ref == left_out and t.status_name == "backlog"
    )
    first, lease = await new_run(
        org,
        ADMIN5,
        bundle,
        raw,
        ImportMapping(containers={left_out: ContainerChoice(skip=True)}),
    )
    await import_writer.apply_run(org, first, lease)
    progress = as_dict(
        await one(org, "SELECT progress FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=first)
    )
    space = progress["node_ids"][space_of(left_out)]
    lane = await one(
        org,
        "SELECT id::text FROM pm_task_statuses WHERE project_id = CAST(:s AS uuid) "
        "   AND name = 'Backlog'",
        s=space,
    )
    async with tenant_session(org) as db:
        await db.execute(
            text("UPDATE pm_task_statuses SET name = 'Someday' WHERE id = CAST(:s AS uuid)"),
            {"s": lane},
        )
    later, lease = await new_run(org, ADMIN5, bundle, raw, ImportMapping())
    await import_writer.apply_run(org, later, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=later)
    )
    after = as_dict(
        await one(org, "SELECT progress FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=later)
    )
    new_list = after["nodes"][left_out]
    again = await one(
        org,
        "SELECT count(*) FROM pm_task_statuses WHERE project_id = CAST(:s AS uuid) "
        "   AND lower(name) = 'backlog'",
        s=space,
    )
    landed = await one(
        org,
        "SELECT count(*) FROM pm_tasks WHERE project_id = CAST(:p AS uuid) "
        "   AND status_id = CAST(:s AS uuid)",
        p=new_list,
        s=lane,
    )
    check(
        "9.1 a List made under a continued space follows a renamed lane, with no duplicate",
        report.get("created", {}).get("projects") == 1
        and again == 0
        and landed == in_list
        and report.get("lanes_added") == 0,
        f"projects={report.get('created')} backlog lanes={again} "
        f"landed={landed}/{in_list} lanes_added={report.get('lanes_added')}",
    )
    # The writer path itself, with no plan in front of it: a List with no
    # earlier map of its own still finds the lane through the union.
    earlier = {
        ref: {str(e[0]): str(e[1]) for e in entries}
        for ref, entries in progress["statuses"].items()
    }
    async with tenant_session(org) as db:
        lanes_before = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pm_task_statuses WHERE project_id = CAST(:s AS uuid)"
                    ),
                    {"s": space},
                )
            ).scalar()
        )
        have = await import_writer._reuse_statuses(
            db, new_list, [("Backlog", "backlog")], status_ids_by_name(earlier)
        )
        lanes_after = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pm_task_statuses WHERE project_id = CAST(:s AS uuid)"
                    ),
                    {"s": space},
                )
            ).scalar()
        )
    check(
        "9.2 the writer finds the renamed lane by id, for a List with no map of its own",
        ["backlog", lane, "backlog"] in have and lanes_after == lanes_before,
        f"lanes {lanes_before}->{lanes_after}",
    )

    # The I-10 review, P2-b: a member renames "Review" in every space. A
    # re-upload's plan shows "In review" as a status that exists, and the run
    # adds no "Review" lane back.
    async with tenant_session(org) as db:
        await db.execute(
            text(
                "UPDATE pm_task_statuses s SET name = 'In review' FROM pm_projects p "
                " WHERE p.id = s.project_id AND p.organization_id = CAST(:o AS uuid) "
                "   AND s.name = 'Review'"
            ),
            {"o": org},
        )
        vis = await resolve_visibility_for(db, ADMIN5)
        facts = await imports._facts(
            db,
            bundle,
            ImportMapping(),
            vis,
            org,
            run_id=str(uuid.uuid4()),
            file_hashes=[hashlib.sha256(raw).hexdigest()],
        )
    replan = build_plan(bundle, ImportMapping(), **facts)
    review = next(r for r in replan["statuses"] if r["name"] == "review")
    names = {s["name"] for s in replan["target_statuses"]}
    third, lease = await new_run(org, ADMIN5, bundle, raw, ImportMapping())
    await import_writer.apply_run(org, third, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=third)
    )
    back = await one(
        org,
        "SELECT count(*) FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND lower(s.name) = 'review'",
    )
    check(
        "9.3 a re-upload follows a renamed lane: the plan shows it, the run adds none",
        (review["becomes"], review["existing"]) == ("In review", True)
        and "In review" in names
        and "Review" not in names
        and report.get("lanes_added") == 0
        and back == 0,
        f"plan={review['becomes']},{review['existing']} names={sorted(names)} "
        f"lanes_added={report.get('lanes_added')} review lanes={back}",
    )


async def sixth_org(org: str, bundle: object, raw: bytes) -> None:
    """The fix-round follow-up: two intake lanes the plan cannot see.

    * P2: a List a member gave a set of its own holds the intake lane
      "Triage". The plan reads only the spaces' sets.
    * F-1: one space holds the intake lane "Triage", and another space holds
      a normal "Triage". The plan merges the sets case-blind, so it keeps
      the name.

    A continuing export gives one task in each place the status "Triage".
    The run must complete, put no task in an intake lane, and add no lane
    twice."""
    import csv
    import io

    by_ref = {c.ref: c for c in bundle.containers}

    def space_of(ref: str) -> str:
        while by_ref[ref].parent_ref:
            ref = by_ref[ref].parent_ref
        return ref

    roots = [t for t in bundle.tasks if t.parent_ref is None]
    spaces = [
        c.ref
        for c in bundle.containers
        if c.kind == "space" and any(space_of(t.container_ref) == c.ref for t in roots)
    ]
    s_pen, s_lane, s_own = spaces[:3]
    t_pen = next(t for t in roots if space_of(t.container_ref) == s_pen)
    t_lane = next(t for t in roots if space_of(t.container_ref) == s_lane)
    t_own = next(t for t in roots if space_of(t.container_ref) == s_own)
    own_list = t_own.container_ref

    first, lease = await new_run(org, ADMIN6, bundle, raw, ImportMapping())
    await import_writer.apply_run(org, first, lease)
    progress = as_dict(
        await one(org, "SELECT progress FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=first)
    )
    pen_space = progress["node_ids"][s_pen]
    lane_space = progress["node_ids"][s_lane]
    own_space = progress["node_ids"][s_own]
    own_node = progress["nodes"][own_list]
    add_lane = (
        "INSERT INTO pm_task_statuses (project_id, name, position, category) "
        "VALUES (CAST(:p AS uuid), :n, :pos, :c)"
    )
    async with tenant_session(org) as db:
        await db.execute(text(add_lane), {"p": pen_space, "n": "Triage", "pos": 5, "c": "triage"})
        await db.execute(text(add_lane), {"p": lane_space, "n": "Triage", "pos": 15, "c": "todo"})
        # What `admin` does when a member gives the List a set of its own:
        # copy the space's lanes, move the List's tasks onto them by name,
        # and own the set. The intake lane then goes into THAT set.
        await db.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id, name, color, position, category) "
                "SELECT CAST(:me AS uuid), name, color, position, category "
                "  FROM pm_task_statuses WHERE project_id = CAST(:src AS uuid)"
            ),
            {"me": own_node, "src": own_space},
        )
        await db.execute(
            text(
                "UPDATE pm_tasks t SET status_id = me.id, origin = jsonb_set(t.origin, "
                "       '{import_values,status_id}', to_jsonb(me.id::text)) "
                "  FROM pm_task_statuses s, pm_task_statuses me "
                " WHERE t.project_id = CAST(:me AS uuid) AND s.id = t.status_id "
                "   AND me.project_id = CAST(:me AS uuid) AND me.name = s.name"
            ),
            {"me": own_node},
        )
        await db.execute(
            text("UPDATE pm_projects SET owns_statuses = true WHERE id = CAST(:p AS uuid)"),
            {"p": own_node},
        )
        await db.execute(text(add_lane), {"p": own_node, "n": "Triage", "pos": 5, "c": "triage"})

    table = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    col = {name: i for i, name in enumerate(table[0])}
    for row in table[1:]:
        if row[col["Task ID"]] in {t_pen.ref, t_lane.ref, t_own.ref}:
            row[col["Status"]] = "Triage"
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(table)
    edited = out.getvalue().encode("utf-8")
    edited_bundle = clickup.parse([(FIXTURE.name, edited)])

    async def where(ref: str) -> tuple[str, str, str]:
        found = (
            await rows(
                org,
                "SELECT s.name, s.project_id::text AS owner, s.category FROM pm_tasks t "
                "  JOIN pm_task_statuses s ON s.id = t.status_id "
                " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :r",
                r=ref,
            )
        )[0]
        return str(found.name), str(found.owner), str(found.category)

    pen_sql = (
        "SELECT count(*) FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        "  JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND s.category = 'triage'"
    )
    imported_sql = (
        "SELECT s.project_id::text AS owner, count(*) AS n FROM pm_task_statuses s "
        "  JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND lower(s.name) = 'triage (imported)' "
        " GROUP BY 1"
    )
    later, lease = await new_run(org, ADMIN6, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, later, lease)
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=later
    )
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=later)
    )
    in_pens = await one(org, pen_sql)
    made = {r.owner: r.n for r in await rows(org, imported_sql)}
    own_at = await where(t_own.ref)
    check(
        "10.1 a List with its own intake lane: the run completes into 'Triage (imported)'",
        state == "done"
        and own_at[:2] == ("Triage (imported)", own_node)
        and made.get(own_node) == 1
        and in_pens == 0,
        f"state={state} error={report.get('error')} t_own={own_at} made={made} in_pens={in_pens}",
    )
    pen_at = await where(t_pen.ref)
    lane_at = await where(t_lane.ref)
    check(
        "10.2 one space's intake 'Triage' beside another's normal 'Triage': each task lands right",
        state == "done"
        and pen_at[:2] == ("Triage (imported)", pen_space)
        and lane_at[:2] == ("Triage", lane_space)
        and lane_at[2] == "todo"
        and made.get(pen_space) == 1
        and lane_space not in made
        and in_pens == 0,
        f"t_pen={pen_at} t_lane={lane_at} made={made} in_pens={in_pens}",
    )
    lanes_sql = (
        "SELECT count(*) FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid)"
    )
    before = await one(org, lanes_sql)
    again, lease = await new_run(org, ADMIN6, edited_bundle, edited, ImportMapping())
    await import_writer.apply_run(org, again, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again)
    )
    check(
        "10.3 the same export again adds no lane and still puts no task in an intake lane",
        await one(org, lanes_sql) == before
        and report.get("lanes_added") == 0
        and report.get("tasks_updated") == 0
        and await one(org, pen_sql) == 0,
        f"lanes {before}->{await one(org, lanes_sql)} lanes_added={report.get('lanes_added')} "
        f"updated={report.get('tasks_updated')} error={report.get('error')}",
    )


async def third_org(org: str, raw: bytes) -> None:
    """I-10 in a third organization: a task with no status, then a re-upload
    of a tree in its pre-I-10 shape."""
    import csv
    import io

    # One root task loses its status in the file.
    table = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    col = {name: i for i, name in enumerate(table[0])}
    blank = next(r for r in table[1:] if r[col["Parent ID"]] == "null")
    blank_ref = blank[col["Task ID"]]
    blank[col["Status"]] = ""
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(table)
    file = out.getvalue().encode("utf-8")
    bundle = clickup.parse([(FIXTURE.name, file)])

    run_id, lease = await new_run(org, ADMIN3, bundle, file, ImportMapping())
    await import_writer.apply_run(org, run_id, lease)
    landed = await one(
        org,
        "SELECT s.name FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE t.organization_id = CAST(:org AS uuid) AND t.origin->>'external_id' = :r",
        r=blank_ref,
    )
    check("7.1 a task with no status lands in its space's Backlog", landed == "Backlog", landed)

    # Rebuild the tree as the writer made it before I-10: each List owns a
    # set of the source names its tasks use, with a Done where none is
    # done-like, and the run recorded no status names.
    progress = as_dict(
        await one(
            org, "SELECT progress FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
        )
    )
    async with tenant_session(org) as db:
        for ref, node in progress["nodes"].items():
            names: list[tuple[str, str]] = []
            for task in bundle.tasks:
                name = task.status_name
                seen = {n.lower() for n, _ in names}
                if task.container_ref == ref and name and name.lower() not in seen:
                    names.append((name, propose_category(name)))
            ordered = sorted(names, key=lambda nc: STAGE_ORDER[nc[1]]) or [("To do", "todo")]
            if not any(c == "done" for _, c in ordered):
                ordered.append(("Done", "done"))
            await db.execute(
                text("UPDATE pm_projects SET owns_statuses = true WHERE id = CAST(:p AS uuid)"),
                {"p": node},
            )
            entries = []
            for position, (name, category) in enumerate(ordered, start=1):
                lane = (
                    await db.execute(
                        text(
                            "INSERT INTO pm_task_statuses "
                            "  (project_id, name, color, position, category, is_default) "
                            "VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :c, :d) RETURNING id"
                        ),
                        {
                            "p": node,
                            "n": name,
                            "pos": position * 10,
                            "c": category,
                            "d": position == 1,
                        },
                    )
                ).scalar_one()
                entries.append([name.lower(), str(lane), category])
                await db.execute(
                    text(
                        "UPDATE pm_tasks SET status_id = CAST(:s AS uuid), origin = jsonb_set("
                        "       origin, '{import_values,status_id}', to_jsonb(CAST(:s AS text))) "
                        " WHERE project_id = CAST(:p AS uuid) AND ("
                        "       origin->'import_source'->>'status_id' = :n "
                        "       OR (:first AND origin->'import_source'->>'status_id' IS NULL))"
                    ),
                    {"s": str(lane), "p": node, "n": name, "first": position == 1},
                )
            progress["statuses"][ref] = entries
        progress.pop("status_names", None)
        await db.execute(
            text(
                "UPDATE pm_import_runs SET progress = CAST(:p AS jsonb) WHERE id = CAST(:id AS uuid)"
            ),
            {"p": json.dumps(progress), "id": run_id},
        )
    state_sql = (
        "SELECT (SELECT count(*) FROM pm_task_statuses s JOIN pm_projects p ON p.id = s.project_id "
        "         WHERE p.organization_id = CAST(:org AS uuid)) AS lanes, "
        "       (SELECT md5(string_agg(id::text || status_id::text, ',' ORDER BY id)) FROM pm_tasks "
        "         WHERE organization_id = CAST(:org AS uuid)) AS placed"
    )
    before = (await rows(org, state_sql))[0]
    again, lease = await new_run(org, ADMIN3, bundle, file, ImportMapping())
    await import_writer.apply_run(org, again, lease)
    report = as_dict(
        await one(org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again)
    )
    after = (await rows(org, state_sql))[0]
    check(
        "7.2 a re-upload of a pre-I-10 tree adds 0 lanes and moves no task",
        report.get("lanes_added") == 0
        and report.get("done_status_added") == 0
        and report.get("tasks_written") == 0
        and report.get("tasks_updated") == 0
        and after.lanes == before.lanes
        and after.placed == before.placed,
        json.dumps(
            {
                **{k: report.get(k) for k in ("lanes_added", "done_status_added", "tasks_updated")},
                "lanes": [before.lanes, after.lanes],
                "same_places": after.placed == before.placed,
            }
        ),
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
