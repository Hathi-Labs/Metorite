"""WS-39 S6e — continuity with Projects, against a real Postgres (R8).

Spec `my_tasks_cutover.md` §4.8 · §5 S6e · D73.8 · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

S6e's two reads make claims a hermetic fake cannot judge:

  * `?untriaged=true` is the LEFT JOIN's NULL (`p.task_id IS NULL`) on the
    real `pm_task_personal` join, and an overlay row holding ONLY a context
    (no disposition) must still take the row out of the group;
  * `/my/led` is `lower(lead) = :who` on `pm_projects` with the tenant arm,
    the personal tree excluded, and `open_tasks` counted through the closed
    vocabulary on real status rows;
  * both answer NOTHING from another tenant's side.

Two members in one organization, as S6e's done-when 1 and 2 ask.

The script imports the module's own helpers — it does not restate their SQL.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6e.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` (the scratch
tenant database) is used with its driver swapped for asyncpg, which is the
driver production runs.

── Result, 2026-09-23, PostgreSQL 16 (metorite-scratch-tenant), asyncpg: 7/7 ──

  1 a task assigned to Bob is in Bob's untriaged group, not Alice's ..... PASS
  2 Bob writes a context and the row leaves the group ................... PASS
  3 the next assignment is untriaged while the triaged one stays out .... PASS
  4 the project Bob leads lists for Bob with its open count, not for Alice PASS
  5 Bob's assigned open task rides in my_tasks, in the inbox's shape .... PASS
  6 the personal root is excluded even when its lead is its owner ....... PASS
  7 another tenant sees none of it ...................................... PASS
"""
from __future__ import annotations

import asyncio
import os
import uuid

from gateway.routes.projects.personal import (
    _MY_TASKS_SQL,
    UNTRIAGED_CLAUSE,
    _upsert_personal,
    led_projects_for,
    my_tasks_binds,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
TAG = uuid.uuid4().hex[:8]
ALICE = f"alice-{TAG}@fracktal.in"
BOB = f"bob-{TAG}@fracktal.in"
PM = f"pm-{TAG}@fracktal.in"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def _untriaged_titles(db, org, who: str) -> set[str]:
    """The route's own query with the route's own clause, from `org`'s side."""
    binds = {**await my_tasks_binds(db, who, archived=False), "vis_org": str(org)}
    sql = _MY_TASKS_SQL + f" AND {UNTRIAGED_CLAUSE}"
    rows = (await db.execute(text(sql), binds)).fetchall()
    return {r.title for r in rows}


async def _project(db, org, name: str, lead: str | None, created_by: str) -> tuple:
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, lead) "
        "VALUES (:id, :org, :name, :who, true, :lead)"),
        {"id": pid, "org": org, "name": name, "who": created_by, "lead": lead})
    todo, done = uuid.uuid4(), uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
        "VALUES (:a, :p, 'To do', 'todo', 10), (:b, :p, 'Done', 'done', 40)"),
        {"a": todo, "b": done, "p": pid})
    return pid, todo, done


async def _task(db, org, pid, status, title: str, number: int, *, created_by: str,
                archived: bool = False):
    tid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "  task_number, status_id, title, created_by, archived_at) "
        "VALUES (:id, :org, :p, :p, :n, :s, :t, :who, "
        "        CASE WHEN :archived THEN now() ELSE NULL END)"),
        {"id": tid, "org": org, "p": pid, "n": number, "s": status, "t": title,
         "who": created_by, "archived": archived})
    return tid


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6e live') RETURNING id"),
            {"slug": f"live-s6e-{TAG}"},
        )).scalar_one()
        for who in (ALICE, BOB, PM):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"),
                {"e": who, "org": org})

        # ── 1. assign a task to Bob → Bob's untriaged, not Alice's ─────────
        sales, todo, done = await _project(db, org, "Sales", None, PM)
        quote = await _task(db, org, sales, todo, "Draft the quote", 1, created_by=PM)
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (:t, :a, :by)"), {"t": quote, "a": BOB, "by": PM})
        bobs = await _untriaged_titles(db, org, BOB)
        alices = await _untriaged_titles(db, org, ALICE)
        check("1 a task assigned to Bob is in Bob's untriaged group, not Alice's",
              bobs == {"Draft the quote"} and alices == set(),
              f"bob={sorted(bobs)} alice={sorted(alices)}")

        # ── 2. Bob writes a context → the row leaves the group ─────────────
        #     Through the ONE upsert `set_personal` uses. A context and
        #     nothing else: the overlay row exists, the disposition is still
        #     unstated, and the LEFT JOIN is no longer NULL.
        await _upsert_personal(db, str(quote), BOB, {"context": "@calls"})
        after = await _untriaged_titles(db, org, BOB)
        stated = (await db.execute(text(
            "SELECT disposition, context FROM pm_task_personal "
            "WHERE task_id = :t AND member_email = :who"),
            {"t": quote, "who": BOB})).fetchone()
        check("2 Bob writes a context and the row leaves the group",
              after == set() and stated is not None
              and stated.disposition is None and stated.context == "@calls",
              f"untriaged={sorted(after)} row={stated}")

        # ── 3. a second untriaged task stays: the clause is per row ────────
        memo = await _task(db, org, sales, todo, "Write the memo", 2, created_by=PM)
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (:t, :a, :by)"), {"t": memo, "a": BOB, "by": PM})
        again = await _untriaged_titles(db, org, BOB)
        check("3 the next assignment is untriaged while the triaged one stays out",
              again == {"Write the memo"}, f"got {sorted(again)}")

        # ── 4. Bob leads a project with no task of his → Bob's led list ────
        launch, l_todo, l_done = await _project(db, org, "Launch", BOB.upper(), PM)
        open_one = await _task(db, org, launch, l_todo, "Open one", 1, created_by=PM)
        await _task(db, org, launch, l_todo, "Open two", 2, created_by=PM)
        await _task(db, org, launch, l_done, "Closed", 3, created_by=PM)
        await _task(db, org, launch, l_todo, "Archived", 4, created_by=PM, archived=True)
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (:t, :a, :by)"), {"t": open_one, "a": ALICE, "by": PM})
        bobs_led = await led_projects_for(db, BOB, org)
        alices_led = await led_projects_for(db, ALICE, org)
        row = bobs_led[0] if bobs_led else {}
        check("4 the project Bob leads lists for Bob with its open count, not for Alice",
              [r["name"] for r in bobs_led] == ["Launch"] and row.get("open_tasks") == 2
              and row.get("my_tasks") == [] and alices_led == [],
              f"bob={[(r['name'], r.get('open_tasks')) for r in bobs_led]} alice={alices_led}")

        # ── 5. Bob's own open work in a led project comes with it ──────────
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (:t, :a, :by)"), {"t": open_one, "a": BOB, "by": PM})
        row = (await led_projects_for(db, BOB, org))[0]
        check("5 Bob's assigned open task rides in my_tasks, in the inbox's shape",
              [t["title"] for t in row["my_tasks"]] == ["Open one"]
              and row["my_tasks"][0]["is_mine"] is True
              and set(row["my_tasks"][0]["assignees"]) == {ALICE, BOB}
              and row["my_tasks"][0]["project_name"] == "Launch",
              f"my_tasks={[(t['title'], t.get('assignees')) for t in row['my_tasks']]}")

        # ── 6. the personal tree is never a led project ────────────────────
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, "
            "  owns_statuses, lead, personal_owner) "
            "VALUES (:id, :org, 'My tasks', :who, true, :who, :who)"),
            {"id": uuid.uuid4(), "org": org, "who": BOB})
        names = [r["name"] for r in await led_projects_for(db, BOB, org)]
        check("6 the personal root is excluded even when its lead is its owner",
              names == ["Launch"], f"got {names}")

        # ── 7. another tenant sees none of it ──────────────────────────────
        other = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'other') RETURNING id"),
            {"slug": f"live-s6e-other-{TAG}"},
        )).scalar_one()
        leaked_inbox = await _untriaged_titles(db, other, BOB)
        leaked_led = await led_projects_for(db, BOB, other)
        check("7 another tenant sees none of it",
              leaked_inbox == set() and leaked_led == [],
              f"inbox={sorted(leaked_inbox)} led={leaked_led}")

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
