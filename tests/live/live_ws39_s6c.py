"""WS-39 S6c — the promote door's gateway seam, against a real Postgres (R8).

Spec `my_tasks_cutover.md` §5 S6c · D53.4 · D-PM-29 · migration 192.

── Why this file exists ─────────────────────────────────────────────────────

`landing.py` is the one seam both move routes cross a root through. The
hermetic suite (`test_projects_landing.py`) proves the ORDER — map, answers,
check — but patches the one rule the fake cannot run: `_REMAP_TARGET_SQL`,
which picks the landing lane and so decides `completed_at`. Only Postgres
can say whether a DONE task promoted onto a board with no closing lane is
reopened by the real lane rule, and whether the name-matched map survives
`load_definitions`' real union.

The script imports the route's own helper (`move_task_in`) — it does not
restate its SQL.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6c.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` with its
driver swapped for asyncpg, the driver production runs.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from gateway.routes.projects.core import Visibility, from_jsonb
from gateway.routes.projects.personal import ensure_personal_project
from gateway.routes.projects.tasks import MoveTask, move_task_in
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
TAG = uuid.uuid4().hex[:8]
WHO = f"alice-{TAG}@fracktal.in"
DONE_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def _lane(db, project, category: str):
    row = (await db.execute(text(
        "SELECT id FROM pm_task_statuses WHERE project_id = :p AND category = :c "
        "ORDER BY position LIMIT 1"), {"p": project, "c": category})).fetchone()
    if row is None:
        lane = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
            "VALUES (:id, :p, :n, :c, 90)"),
            {"id": lane, "p": project, "n": category.title(), "c": category})
        return lane
    return row.id


async def _field(db, project, key: str, name: str, required: bool) -> None:
    await db.execute(text(
        "INSERT INTO pm_custom_fields (project_id, field_key, name, field_type, "
        "  options, position, required, created_by) "
        "VALUES (:p, :k, :n, 'text', '[]'::jsonb, 0, :r, :who)"),
        {"p": project, "k": key, "n": name, "r": required, "who": WHO})


async def _task(db, org, project, status, title: str, **cols):
    task_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "  task_number, status_id, title, created_by, custom_fields, completed_at) "
        "VALUES (:id, :org, :p, :p, :n, :s, :t, :who, CAST(:cf AS jsonb), :done)"),
        {"id": task_id, "org": org, "p": project, "n": cols.get("number", 1),
         "s": status, "t": title, "who": WHO,
         "cf": __import__("json").dumps(cols.get("custom_fields", {})),
         "done": cols.get("completed_at")})
    await db.execute(text(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
        "VALUES (:t, :a, :a)"), {"t": task_id, "a": WHO})
    return (await db.execute(text(
        "SELECT * FROM pm_tasks WHERE id = :id"), {"id": task_id})).fetchone()


async def _read(db, task_id):
    return (await db.execute(text(
        "SELECT project_id, root_project_id, status_id, completed_at, custom_fields "
        "FROM pm_tasks WHERE id = :id"), {"id": task_id})).fetchone()


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6c live') RETURNING id"),
            {"slug": f"live-s6c-{TAG}"},
        )).scalar_one()
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, status) "
            "VALUES (:e, :org, 'active')"), {"e": WHO, "org": org})
        vis = Visibility(unrestricted=False, email=WHO, groups=(), organization_id=str(org))

        # The member's personal root, minted the way capture mints it.
        root = await ensure_personal_project(db, WHO)
        mine = root.id
        mine_todo = await _lane(db, mine, "todo")
        mine_done = await _lane(db, mine, "done")
        await _field(db, mine, "po", "PO", required=False)
        await _field(db, mine, "scratch", "Scratch", required=False)

        # A company board with NO closing lane, and a renamed required field.
        team = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :org, 'S6c launch', :who, true)"),
            {"id": team, "org": org, "who": WHO})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (:p, :s, :s)"), {"p": team, "s": WHO})
        backlog = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
            "VALUES (:id, :p, 'Backlog', 'todo', 10)"), {"id": backlog, "p": team})
        await _field(db, team, "customer_po", "PO", required=True)

        # ── 1. the renamed required field lands by NAME, with no answer ───
        t1 = await _task(db, org, mine, mine_todo, "Order the parts",
                         custom_fields={"po": "PO-9"}, number=1)
        await move_task_in(db, vis, t1, MoveTask(project_id=str(team)), by=WHO)
        r1 = await _read(db, t1.id)
        check("1 po lands as customer_po through the real definitions union",
              from_jsonb(r1.custom_fields) == {"customer_po": "PO-9"}
              and str(r1.project_id) == str(team) and str(r1.root_project_id) == str(team),
              f"custom_fields={r1.custom_fields} project={r1.project_id}")

        # ── 2. a DONE task promoted to a board with no closing lane ───────
        t2 = await _task(db, org, mine, mine_done, "Finished at home",
                         custom_fields={"po": "PO-2"}, completed_at=DONE_AT, number=2)
        await move_task_in(db, vis, t2, MoveTask(project_id=str(team)), by=WHO)
        r2 = await _read(db, t2.id)
        check("2 a DONE task reopens when the real lane rule lands it in Backlog",
              str(r2.status_id) == str(backlog) and r2.completed_at is None,
              f"status={r2.status_id} backlog={backlog} completed_at={r2.completed_at}")

        # ── 3. a blank required field is refused, and nothing moved ───────
        t3 = await _task(db, org, mine, mine_todo, "No PO yet", custom_fields={}, number=3)
        try:
            async with db.begin_nested():
                await move_task_in(db, vis, t3, MoveTask(project_id=str(team)), by=WHO)
            check("3 a blank required field is refused with its name", False, "accepted")
        except HTTPException as exc:
            r3 = await _read(db, t3.id)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            check("3 a blank required field is refused with its name",
                  exc.status_code == 422 and "PO" in str(exc.detail)
                  and str(r3.project_id) == str(mine)
                  and [f["field_key"] for f in detail.get("fields", [])] == ["customer_po"],
                  f"status={exc.status_code} detail={exc.detail} project={r3.project_id}")

        # ── 4. an answer under the DESTINATION key lands after the map ────
        await move_task_in(db, vis, t3, MoveTask(
            project_id=str(team), custom_fields={"customer_po": "PO-33"}), by=WHO)
        r4 = await _read(db, t3.id)
        check("4 an answer under the destination key is accepted",
              from_jsonb(r4.custom_fields) == {"customer_po": "PO-33"}
              and str(r4.project_id) == str(team),
              f"custom_fields={r4.custom_fields}")

        # ── 5. a value with no field in the destination is dropped AND recorded
        t5 = await _task(db, org, mine, mine_todo, "With a scratch note",
                         custom_fields={"po": "PO-5", "scratch": "keep me"}, number=5)
        await move_task_in(db, vis, t5, MoveTask(project_id=str(team)), by=WHO)
        r5 = await _read(db, t5.id)
        entry = (await db.execute(text(
            "SELECT body, meta FROM pm_activities WHERE task_id = :t "
            "AND meta ? 'dropped_custom_fields'"), {"t": t5.id})).fetchone()
        check("5 the drop is written to the timeline with its value (D-PM-29)",
              from_jsonb(r5.custom_fields) == {"customer_po": "PO-5"}
              and entry is not None and "keep me" in str(entry.body)
              and from_jsonb(entry.meta)["dropped_custom_fields"] == {"scratch": "keep me"},
              f"custom_fields={r5.custom_fields} entry={entry}")

        await outer.rollback()
    await eng.dispose()

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        pad = "." * (width + 4 - len(name))
        print(f"  {name} {pad} {'PASS' if ok else 'FAIL: ' + detail}")
        failed += 0 if ok else 1
    print("")
    print(f"{len(results) - failed}/{len(results)} PASS")
    raise SystemExit(1 if failed else 0)


asyncio.run(main())
