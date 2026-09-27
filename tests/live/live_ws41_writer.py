"""WS-41 I-3 — the writer lands the real export in pm_*, against a real Postgres (R8).

Spec `project-docs/specs/project_import.md` §6, §7.3, §9 row I-3 · **D80**.

── What this proves ─────────────────────────────────────────────────────────

I-3's "done when": the scrubbed P-1 export applies into new spaces, the counts
match the file, a killed run resumes to the same counts, and a second run
skips every task. It drives the REAL writer (`import_writer.apply_run`), which
opens its own tenant sessions, so it points the shared engine at the scratch
database through `DATABASE_URL`.

The writer COMMITS, batch by batch, so this script cannot roll back. It works
in a fresh organization and deletes that organization at the end.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws41_writer.py
"""

from __future__ import annotations

import asyncio
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
from gateway.routes.projects.importer.plan import ImportMapping, build_plan
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "tests" / "unit" / "import_fixtures" / "clickup_workspace.csv"
MIGRATION = ROOT / "infra" / "postgres" / "219_pm_import_runs.sql"
ADMIN = f"admin.{uuid.uuid4().hex[:6]}@acme.test"
MEMBER = f"member.{uuid.uuid4().hex[:6]}@acme.test"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def one(org: str, sql: str, **params: object) -> object:
    async with tenant_session(org) as db:
        return (await db.execute(text(sql), {"org": org, **params})).scalar()


async def new_run(org: str, bundle: object, raw: bytes) -> str:
    """What POST /runs and POST /apply do, without an HTTP request."""
    run_id = str(uuid.uuid4())
    mapping = ImportMapping()
    async with tenant_session(org) as db:
        vis = await resolve_visibility_for(db, ADMIN)
        facts = await imports._facts(db, bundle, mapping, vis, org)
        plan = build_plan(bundle, mapping, **facts)
        stored = imports._store(org, run_id, [(FIXTURE.name, raw)])
        await db.execute(
            text(imports.INSERT_RUN_SQL),
            {
                "id": run_id,
                "org": org,
                "who": ADMIN,
                "source": "clickup",
                "files": json.dumps(stored),
                "mapping": mapping.model_dump_json(),
                "plan": json.dumps(plan, default=str),
            },
        )
        await db.execute(text(imports.LOCK_ORG_SQL), {"org": org})
        started = (await db.execute(text(imports.START_SQL), {"org": org, "id": run_id})).fetchone()
    check(
        f"run {run_id[:8]} plans ready and starts",
        bool(plan["ready"] and started),
        str(plan["errors"]),
    )
    return run_id


async def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    eng = create_async_engine(dsn)
    async with eng.begin() as db:
        if (await db.execute(text("SELECT to_regclass('pm_import_runs')"))).scalar() is None:
            raw = await db.get_raw_connection()
            await raw.driver_connection.execute(MIGRATION.read_text(encoding="utf-8"))
        org = str(
            (
                await db.execute(
                    text(
                        "INSERT INTO organization (slug, display_name) VALUES (:s, 'WS-41 writer live') RETURNING id"
                    ),
                    {"s": f"live-ws41w-{uuid.uuid4().hex[:8]}"},
                )
            ).scalar_one()
        )
        for email in (ADMIN, MEMBER):
            await db.execute(
                text(
                    "INSERT INTO app_user (email, organization_id, status) VALUES (:e, CAST(:o AS uuid), 'active')"
                ),
                {"e": email, "o": org},
            )
        # The directory: the member carries the fixture's "Person 1" name, so
        # the name match proposes them. The admin row may come from a trigger.
        await db.execute(
            text(
                "INSERT INTO people (name, email, status, organization_id) "
                "VALUES ('Person 1', :e, 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"
            ),
            {"e": MEMBER, "o": org},
        )
        await db.execute(
            text("UPDATE people SET name = 'Person 1' WHERE lower(email) = :e"), {"e": MEMBER}
        )
    await eng.dispose()

    raw = FIXTURE.read_bytes()
    bundle = clickup.parse([(FIXTURE.name, raw)])
    person1 = sum(1 for t in bundle.tasks if "name:person 1" in t.assignee_refs)
    try:
        await run_checks(org, bundle, raw, person1)
    finally:
        # The scratch database is SHARED between worktrees (H-172), and a
        # neighbour replaying migrations can deadlock this cascade. Retry, and
        # never let the cleanup hide the results above.
        eng = create_async_engine(dsn)
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


async def run_checks(org: str, bundle: object, raw: bytes, person1: int) -> None:
    # ── 1. a crash after three batches, then a resume ───────────────────
    run_id = await new_run(org, bundle, raw)
    await import_writer.apply_run(org, run_id, stop_after=3)
    mid = await one(
        org,
        "SELECT (progress->>'cursor')::int FROM pm_import_runs WHERE id = CAST(:id AS uuid)",
        id=run_id,
    )
    written_mid = await one(
        org, "SELECT count(*) FROM pm_tasks WHERE origin->>'run_id' = :id", id=run_id
    )
    check(
        "1.1 the 'crash' leaves three batches written",
        mid == 600 and written_mid == 600,
        f"cursor={mid} written={written_mid}",
    )
    await import_writer.apply_run(org, run_id)
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
    )
    report = await one(
        org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=run_id
    )
    check("1.2 the resume finishes the run", state == "done", f"state={state} report={report}")

    # ── 2. the counts match the file ────────────────────────────────────
    q = "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid) AND origin->>'run_id' = :id"
    check("2.1 every task lands once", await one(org, q, id=run_id) == 2423)
    subs = await one(org, q + " AND parent_task_id IS NOT NULL", id=run_id)
    check("2.2 subtasks keep their parent", subs == 1270, f"subtasks={subs}")
    same_root = await one(
        org,
        "SELECT count(*) FROM pm_tasks c JOIN pm_tasks p ON p.id = c.parent_task_id "
        " WHERE c.origin->>'run_id' = :id AND c.root_project_id <> p.root_project_id",
        id=run_id,
    )
    check("2.3 no subtask crosses into another space", same_root == 0, str(same_root))
    kinds = {
        (r.parent_is_null, r.kind): r.n
        for r in await _rows(
            org,
            "SELECT parent_project_id IS NULL AS parent_is_null, coalesce(kind,'project') AS kind, count(*) AS n "
            "  FROM pm_projects WHERE organization_id = CAST(:org AS uuid) AND source = 'import' GROUP BY 1, 2",
        )
    }
    check(
        "2.4 five spaces, nine folders, 48 projects",
        kinds == {(True, "project"): 5, (False, "folder"): 9, (False, "project"): 48},
        str(kinds),
    )
    closed = await one(org, q + " AND completed_at IS NOT NULL", id=run_id)
    estimated = await one(org, q + " AND (origin->>'completed_at_estimated')::boolean", id=run_id)
    check(
        "2.5 1,647 closed tasks carry an estimated completion",
        closed == 1647 and estimated == 1647,
        f"closed={closed} estimated={estimated}",
    )
    in_done = await one(
        org,
        "SELECT count(*) FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        " WHERE t.origin->>'run_id' = :id AND s.category IN ('done', 'cancelled')",
        id=run_id,
    )
    check("2.6 the same 1,647 sit in a done status", in_done == 1647, str(in_done))
    no_done = await one(
        org,
        "SELECT count(*) FROM pm_projects p WHERE p.organization_id = CAST(:org AS uuid) AND p.source = 'import' "
        "   AND p.owns_statuses AND NOT EXISTS (SELECT 1 FROM pm_task_statuses s "
        "        WHERE s.project_id = p.id AND s.category = 'done')",
    )
    check("2.7 every status set holds a Done status (D79)", no_done == 0, str(no_done))
    due = await one(org, q + " AND due_at IS NOT NULL", id=run_id)
    noon = await one(
        org, q + " AND to_char(due_at AT TIME ZONE 'UTC', 'HH24:MI') = '06:30'", id=run_id
    )
    check(
        "2.8 1,091 due dates, 1,075 at local noon (06:30 UTC for +5:30)",
        due == 1091 and noon == 1075,
        f"due={due} noon={noon}",
    )
    importance = {
        r.importance: r.n
        for r in await _rows(
            org,
            "SELECT importance, count(*) AS n FROM pm_tasks WHERE origin->>'run_id' = :id GROUP BY 1",
            id=run_id,
        )
    }
    check(
        "2.9 priorities map by ClickUp's meaning",
        importance.get(3) == 53
        and importance.get(2) == 212
        and importance.get(1) == 199
        and importance.get(0, 0) >= 1,
        str(importance),
    )
    old = await one(org, q + " AND created_at < '2026-01-01'", id=run_id)
    check("2.10 tasks keep their ClickUp creation date", old and old > 100, str(old))

    # ── 3. comments, people, quiet ──────────────────────────────────────
    comments = await one(
        org,
        "SELECT count(*) FROM pm_activities a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.origin->>'run_id' = :id AND a.type = 'comment'",
        id=run_id,
    )
    dated = await one(
        org,
        "SELECT count(*) FROM pm_activities a JOIN pm_tasks t ON t.id = a.task_id "
        " WHERE t.origin->>'run_id' = :id AND a.type = 'comment' AND a.created_at < now() - interval '1 day'",
        id=run_id,
    )
    check(
        "3.1 93 comments, each with its ClickUp date",
        comments == 93 and dated == 93,
        f"comments={comments} dated={dated}",
    )
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
    watchers = await one(
        org,
        "SELECT count(*) FROM pm_task_watchers w JOIN pm_tasks t ON t.id = w.task_id "
        " WHERE t.origin->>'run_id' = :id",
        id=run_id,
    )
    check(
        "3.3 the only watchers are the mapped assignees",
        watchers == person1,
        f"{watchers} vs {person1}",
    )
    notes = await one(
        org,
        "SELECT count(*) FROM pm_notifications n JOIN pm_tasks t ON t.id = n.task_id "
        " WHERE t.origin->>'run_id' = :id",
        id=run_id,
    )
    check("3.4 nobody is notified (§6.10)", notes == 0, str(notes))
    footers = await one(org, q + " AND description LIKE '%Assigned in ClickUp to:%'", id=run_id)
    check(
        "3.5 unmatched people are named in the description", footers and footers > 0, str(footers)
    )
    grants = await one(
        org,
        "SELECT count(*) FROM pm_project_grants g JOIN pm_projects p ON p.id = g.project_id "
        " WHERE p.organization_id = CAST(:org AS uuid) AND p.parent_project_id IS NULL AND g.subject = 'org'",
    )
    check("3.6 each space is shared with the organization", grants == 5, str(grants))
    numbers = await one(
        org,
        "SELECT count(*) FROM (SELECT root_project_id, task_number FROM pm_tasks WHERE origin->>'run_id' = :id "
        " GROUP BY 1, 2 HAVING count(*) > 1) d",
        id=run_id,
    )
    check("3.7 task numbers are unique per space", numbers == 0, str(numbers))
    files = list(Path(os.environ["PROJECT_IMPORT_DIR"]).rglob(f"*{run_id}*"))
    check("3.8 the upload is deleted when the run is done", files == [], str(files))
    report = report if isinstance(report, dict) else json.loads(report or "{}")
    check(
        "3.9 the report counts what it wrote",
        report.get("tasks_written") == 2423
        and report.get("comments_written") == 93
        and report.get("done_status_added") == 7,
        json.dumps(
            {
                k: report.get(k)
                for k in ("tasks_written", "comments_written", "done_status_added", "created")
            }
        ),
    )

    # ── 4. a second run of the same file skips everything ───────────────
    again = await new_run(org, bundle, raw)
    busy = await one(org, imports.BUSY_SQL, id=str(uuid.uuid4()))
    check(
        "4.0 while one run writes, a second start sees it (one writer per org)",
        str(busy) == again,
        str(busy),
    )
    await import_writer.apply_run(org, again)
    second = await one(
        org, "SELECT report FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=again
    )
    second = second if isinstance(second, dict) else json.loads(second or "{}")
    check(
        "4.1 a re-run writes nothing and skips all 2,423",
        second.get("tasks_written") == 0 and second.get("tasks_skipped") == 2423,
        json.dumps(second)[:200],
    )
    total = await one(
        org, "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:org AS uuid)"
    )
    check("4.2 the organization still holds 2,423 tasks", total == 2423, str(total))

    # ── 5. a failure is recorded, not swallowed ─────────────────────────
    broken = await new_run(org, bundle, raw)
    for path in Path(os.environ["PROJECT_IMPORT_DIR"]).rglob("*"):
        if broken in str(path) and path.is_file():
            path.write_bytes(b"changed")
    await import_writer._guarded(org, broken)
    state = await one(
        org, "SELECT state FROM pm_import_runs WHERE id = CAST(:id AS uuid)", id=broken
    )
    check("5.1 a run that cannot read its file ends 'failed'", state == "failed", str(state))


async def _rows(org: str, sql: str, **params: object) -> list:
    async with tenant_session(org) as db:
        return list((await db.execute(text(sql), {"org": org, **params})).fetchall())


if __name__ == "__main__":
    asyncio.run(main())
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail if not ok else ''}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)
