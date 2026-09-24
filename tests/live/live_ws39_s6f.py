"""WS-39 S6f — one set of fields across My Tasks and Projects (D77), against
a real Postgres (R8).

Spec `my_tasks_cutover.md` §4.10 · §5 S6f · D77 · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

S6f makes claims a hermetic fake cannot judge:

  * a teammate moves the LANE (a plain `status_id` write, no overlay write)
    and my list follows: reopened → my Next, closed → out of my Next;
  * who I wait on is the real `ARRAY(… pm_task_assignees …)` subquery, so a
    reassignment in Projects moves my Waiting-For with it;
  * my estimate edit lands on `pm_tasks.estimate_mins`, and the People
    capacity query (`people/core.py::compute_load`, tenant-bound through
    `app.tenant_id`) and the day planner (`planning._PM_SELECT`) both read it;
  * `start_date` against the real `current_date` hides a task from my inbox;
  * migration 216's copy picks the assignee's estimate, fills only an empty
    estimate, and a second run changes nothing — and the ledger guard skips;
  * the shared Priority reaches the planner BESIDE my own `important`, never
    as it (D76), and the backfill leaves the Priority alone;
  * the retired overlay estimate is refused before any SQL runs, and my own
    `important` is not;
  * `time_spent_mins` sums every member's actuals on the real table;
  * another tenant sees none of it.

Two members in one organization. The script imports the module's own
helpers — it does not restate their SQL.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6f.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` (the scratch
tenant database) is used with its driver swapped for asyncpg, which is the
driver production runs.

── Result, 2026-09-23, PostgreSQL 16 (metorite-scratch-tenant), asyncpg ─────

  (the run's own output is recorded in `my_tasks_cutover.md` §5 S6f)
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gateway.routes.people.core import compute_load
from gateway.routes.projects.bulk import _act_on_one
from gateway.routes.projects.core import update_row
from gateway.routes.projects.personal import (
    _MY_TASKS_SQL,
    DEFERRED_CLAUSE,
    MY_TASKS_FROM,
    _project_task,
    _upsert_personal,
    complete_for_member,
    my_tasks_binds,
)
from gateway.routes.projects.planning import _PM_SELECT
from gateway.routes.projects.tasks import time_spent_mins
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
# Found by its name, not its number: R1 can renumber it at merge.
(MIGRATION,) = (Path(__file__).resolve().parents[2] / "infra/postgres").glob(
    "*_pm_tasks_estimate_backfill.sql")
PARITY = Path(__file__).resolve().parents[2] / "tests/fixtures/deferred_parity.json"
TAG = uuid.uuid4().hex[:8]
ALICE = f"alice-{TAG}@fracktal.in"
BOB = f"bob-{TAG}@fracktal.in"
CAROL = f"carol-{TAG}@fracktal.in"
PM = f"pm-{TAG}@fracktal.in"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def _mine(db, org, who: str, *, deferred_hidden: bool = False) -> dict:
    """My tasks as the inbox projects them, from `org`'s side: title → task."""
    binds = {**await my_tasks_binds(db, who, archived=False), "vis_org": str(org)}
    sql = _MY_TASKS_SQL + (f" AND {DEFERRED_CLAUSE}" if deferred_hidden else "")
    rows = (await db.execute(text(sql), binds)).fetchall()
    return {r.title: _project_task(r)[0] for r in rows}


async def _project(db, org, name: str, created_by: str) -> tuple:
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
        "VALUES (:id, :org, :name, :who, true)"),
        {"id": pid, "org": org, "name": name, "who": created_by})
    todo, done = uuid.uuid4(), uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
        "VALUES (:a, :p, 'To do', 'todo', 10), (:b, :p, 'Done', 'done', 40)"),
        {"a": todo, "b": done, "p": pid})
    return pid, todo, done


async def _task(db, org, pid, status, title: str, number: int, **cols):
    tid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "  task_number, status_id, title, created_by, estimate_mins) "
        "VALUES (:id, :org, :p, :p, :n, :s, :t, :who, :est)"),
        {"id": tid, "org": org, "p": pid, "n": number, "s": status, "t": title,
         "who": PM, "est": cols.get("estimate_mins")})
    return tid


async def _assign(db, tid, who: str, at: str) -> None:
    # asyncpg binds an instant only as a datetime, never as its ISO text.
    await db.execute(text(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, assigned_at) "
        "VALUES (:t, :a, :by, :at)"),
        {"t": tid, "a": who, "by": PM,
         "at": datetime.fromisoformat(at).astimezone(UTC)})


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6f live') RETURNING id"),
            {"slug": f"live-s6f-{TAG}"},
        )).scalar_one()
        for who in (ALICE, BOB, CAROL, PM):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"),
                {"e": who, "org": org})
        sales, todo, done = await _project(db, org, "Sales", PM)
        # Alice may see the board, so the WAITING arm reaches its tasks.
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (:p, :who, :by)"), {"p": sales, "who": ALICE, "by": PM})

        # ── 1. a teammate reopens it → my Next ─────────────────────────────
        quote = await _task(db, org, sales, done, "Ship the quote", 1)
        await _assign(db, quote, ALICE, "2026-09-01T09:00:00+00:00")
        await _assign(db, quote, BOB, "2026-09-02T09:00:00+00:00")
        await _upsert_personal(db, str(quote), ALICE, {"disposition": "DONE"})
        before = (await _mine(db, org, ALICE))["Ship the quote"]["disposition"]
        await update_row(db, "pm_tasks", str(quote), {"status_id": str(todo)})
        row = (await _mine(db, org, ALICE))["Ship the quote"]
        stored = (await db.execute(text(
            "SELECT disposition FROM pm_task_personal "
            "WHERE task_id = :t AND member_email = :who"),
            {"t": quote, "who": ALICE})).scalar_one()
        check("1 a teammate reopens it in Projects and it is back in my Next",
              before == "DONE" and row["disposition"] == "NEXT"
              and row["is_triaged"] is True and stored == "DONE",
              f"before={before} after={row['disposition']} "
              f"triaged={row['is_triaged']} stored={stored}")

        # ── 2. Projects closes it → it leaves Bob's Next ───────────────────
        await _upsert_personal(db, str(quote), BOB, {"disposition": "NEXT"})
        open_now = (await _mine(db, org, BOB))["Ship the quote"]["disposition"]
        await update_row(db, "pm_tasks", str(quote), {"status_id": str(done)})
        closed = (await _mine(db, org, BOB))["Ship the quote"]["disposition"]
        check("2 Projects closes it and it leaves my Next, with no overlay write",
              open_now == "NEXT" and closed == "DONE",
              f"open={open_now} closed={closed}")

        # ── 3. reassigned in Projects → my waiting-on follows ──────────────
        drawings = await _task(db, org, sales, todo, "Send the drawings", 2)
        await _assign(db, drawings, BOB, "2026-09-01T09:00:00+00:00")
        await _upsert_personal(db, str(drawings), ALICE, {
            "disposition": "WAITING",
            "waiting_on": {"name": "Bob Builder", "email": BOB},
            "delegated_at": "2026-09-01T09:00:00+00:00"})
        first = (await _mine(db, org, ALICE))["Send the drawings"]["waiting_on"]
        await db.execute(text(
            "DELETE FROM pm_task_assignees WHERE task_id = :t"), {"t": drawings})
        await _assign(db, drawings, CAROL, "2026-09-03T09:00:00+00:00")
        second = (await _mine(db, org, ALICE))["Send the drawings"]["waiting_on"]
        check("3 reassign in Projects and my waiting-on follows the assignee",
              first == {"name": "Bob Builder", "email": BOB}
              and second == {"name": CAROL, "email": CAROL},
              f"first={first} second={second}")

        # ── 4. my estimate edit → the shared column, capacity and planner ──
        #     My Tasks' Estimate is `PATCH /projects/tasks/{id}` with
        #     `estimate_mins` (lens.ts TASK_KEYS), which writes through the
        #     same `update_row` seam.
        deck = await _task(db, org, sales, todo, "Board deck", 3)
        await _assign(db, deck, ALICE, "2026-09-01T09:00:00+00:00")
        await update_row(db, "pm_tasks", str(deck), {"estimate_mins": 90})
        await db.execute(text("SELECT set_config('app.tenant_id', :org, true)"),
                         {"org": str(org)})
        load = await compute_load(db, ALICE)
        mine_row = (await _mine(db, org, ALICE))["Board deck"]
        planner = (await db.execute(
            text(_PM_SELECT + MY_TASKS_FROM + " AND t.id = CAST(:tid AS uuid)"),
            {**await my_tasks_binds(db, ALICE, archived=False),
             "vis_org": str(org), "tid": str(deck)},
        )).fetchone()
        check("4 my estimate lands on the task, People capacity and the planner read it",
              mine_row["estimate_mins"] == 90
              and load["estimated_hours"] == 1.5
              and planner is not None and planner.time_estimate_mins == 90,
              f"task={mine_row['estimate_mins']} load={load} "
              f"planner={getattr(planner, 'time_estimate_mins', None)}")

        # ── 5. the shared Priority SEEDS my important, never becomes it ───
        #     D76: the planner reads my answer AND the Priority beside it.
        #     A Highest task I have not judged carries no stored answer. My
        #     "no" is stored as mine, and the Priority does not move.
        async def _planner_row():
            return (await db.execute(
                text(_PM_SELECT + MY_TASKS_FROM + " AND t.id = CAST(:tid AS uuid)"),
                {**await my_tasks_binds(db, ALICE, archived=False),
                 "vis_org": str(org), "tid": str(deck)},
            )).fetchone()

        await update_row(db, "pm_tasks", str(deck), {"importance": 3})
        unjudged = await _planner_row()
        mine_row = (await _mine(db, org, ALICE))["Board deck"]
        await _upsert_personal(db, str(deck), ALICE, {"important": False})
        judged = await _planner_row()
        level = (await db.execute(text(
            "SELECT importance FROM pm_tasks WHERE id = :t"), {"t": deck})).scalar_one()
        check("5 the Priority reaches the planner beside my important, never as it",
              unjudged.important is None and unjudged.org_priority == 3
              and mine_row["importance"] == 3 and mine_row["important"] is None
              and judged.important is False and judged.org_priority == 3
              and level == 3,
              f"unjudged={unjudged.important}/{unjudged.org_priority} "
              f"judged={judged.important}/{judged.org_priority} level={level}")

        # ── 6. a future start date hides it from my inbox ─────────────────
        await db.execute(text(
            "UPDATE pm_tasks SET start_date = current_date + 3 WHERE id = :t"),
            {"t": deck})
        hidden = "Board deck" not in await _mine(db, org, ALICE, deferred_hidden=True)
        await db.execute(text(
            "UPDATE pm_tasks SET start_date = current_date WHERE id = :t"),
            {"t": deck})
        shown = "Board deck" in await _mine(db, org, ALICE, deferred_hidden=True)
        check("6 a start date in the future hides it; today shows it",
              hidden and shown, f"hidden={hidden} shown={shown}")

        # ── 6b. the clause itself, against the shared fixture (F4) ─────────
        parity = json.loads(PARITY.read_text(encoding="utf-8"))["cases"]
        wrong = []
        for case in parity:
            hidden = (await db.execute(text(
                "SELECT NOT (" + DEFERRED_CLAUSE + ") FROM "
                "(SELECT now() + make_interval(days => CAST(:d AS int)) "
                "   AS defer_until) p, "
                "(SELECT current_date + CAST(:s AS int) AS start_date) t"),
                {"d": case["defer_days"], "s": case["start_days"]})).scalar_one()
            if bool(hidden) is not case["hidden"]:
                wrong.append(case["name"])
        check("6b DEFERRED_CLAUSE on Postgres agrees with the shared fixture",
              not wrong and len(parity) >= 10, f"wrong={wrong}")

        # ── 7. the retired overlay estimate is refused before any SQL ─────
        refused = []
        for field, value in (("important", True), ("time_estimate_mins", 1)):
            try:
                await _upsert_personal(db, str(deck), ALICE, {field: value})
            except ValueError:
                refused.append(field)
        check("7 the one upsert refuses the retired estimate, and takes my important",
              refused == ["time_estimate_mins"], f"refused={refused}")

        # ── 8. migration 216: the assignee's estimate, once ────────────────
        #     The copy statement is the file's own, run on this transaction.
        #     Two overlay estimates on one task: Carol's (not assigned, and
        #     written first) and Bob's (the assignee). Bob's wins.
        memo = await _task(db, org, sales, todo, "Write the memo", 4)
        kept = await _task(db, org, sales, todo, "Kept estimate", 5, estimate_mins=15)
        await _assign(db, memo, BOB, "2026-09-01T09:00:00+00:00")
        await db.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, time_estimate_mins, "
            "  updated_at) VALUES "
            "(:m, :carol, 20, now() - interval '2 days'), "
            "(:m, :bob, 45, now()), "
            "(:k, :bob, 99, now())"),
            {"m": memo, "k": kept, "carol": CAROL, "bob": BOB})
        # A task Bob flagged important, with no Priority. D76: the flag is
        # his, so the backfill must leave the shared Priority unset.
        flagged = await _task(db, org, sales, todo, "Flagged", 6)
        await _assign(db, flagged, BOB, "2026-09-01T09:00:00+00:00")
        await db.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, important) "
            "VALUES (:f, :bob, true)"), {"f": flagged, "bob": BOB})
        # The file's own statement, run on this transaction.
        sql = MIGRATION.read_text(encoding="utf-8")
        copy = sql[sql.index("WITH pick AS"):sql.rindex("END")].strip().rstrip(";")
        first_run = (await db.execute(text(copy))).rowcount
        second_run = (await db.execute(text(copy))).rowcount
        level = (await db.execute(text(
            "SELECT importance FROM pm_tasks WHERE id = :f"), {"f": flagged})).scalar_one()
        check("8b the backfill leaves the Priority alone, whatever I flagged",
              level is None, f"importance={level}")
        memo_est, kept_est = (await db.execute(text(
            "SELECT (SELECT estimate_mins FROM pm_tasks WHERE id = :m), "
            "       (SELECT estimate_mins FROM pm_tasks WHERE id = :k)"),
            {"m": memo, "k": kept})).one()
        check("8 the backfill copies the assignee's estimate, only into an empty one",
              memo_est == 45 and kept_est == 15 and first_run >= 1,
              f"memo={memo_est} kept={kept_est} first_run={first_run}")
        check("9 the backfill applied twice changes nothing the second time",
              second_run == 0, f"second_run={second_run}")

        # ── 10. the whole file, under the ledger, is a no-op ───────────────
        await db.execute(text(
            "UPDATE pm_tasks SET estimate_mins = NULL WHERE id = :m"), {"m": memo})
        guarded = (await db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM schema_migrations "
            "WHERE filename = :f)"), {"f": MIGRATION.name})).scalar_one()
        raw = await db.get_raw_connection()
        await raw.driver_connection.execute(sql)
        after_file = (await db.execute(text(
            "SELECT estimate_mins FROM pm_tasks WHERE id = :m"), {"m": memo})).scalar_one()
        check("10 a replay under the ledger does not refill a cleared estimate",
              guarded and after_file is None,
              f"ledger={guarded} estimate={after_file}")

        # ── 11. time spent sums every member's actuals ─────────────────────
        await _upsert_personal(db, str(deck), ALICE, {
            "actual_start": "2026-09-20T09:00:00+00:00",
            "actual_end": "2026-09-20T09:30:00+00:00"})
        await _upsert_personal(db, str(deck), BOB, {
            "actual_start": "2026-09-21T10:00:00+00:00",
            "actual_end": "2026-09-21T10:45:00+00:00"})
        spent = await time_spent_mins(db, str(deck))
        check("11 time spent is every member's actuals, summed", spent == 75,
              f"spent={spent}")

        # ── 11b. complete it, then un-check it: the board reopens ──────────
        #     The card checkbox, Focus mode and Undo all reach the bulk
        #     `personal` action. `complete_for_member` is the real completion.
        box = await _task(db, org, sales, todo, "Checkbox task", 9)
        await _assign(db, box, ALICE, "2026-09-01T09:00:00+00:00")
        row = (await db.execute(text("SELECT * FROM pm_tasks WHERE id = :t"),
                                {"t": box})).fetchone()
        await complete_for_member(db, row, ALICE)
        closed_first = (await _mine(db, org, ALICE))["Checkbox task"]["disposition"]
        row = (await db.execute(text("SELECT * FROM pm_tasks WHERE id = :t"),
                                {"t": box})).fetchone()
        outcome, _ = await _act_on_one(db, row, "personal", by=ALICE,
                                       personal={"disposition": "NEXT"})
        after = (await _mine(db, org, ALICE))["Checkbox task"]
        state = (await db.execute(text(
            "SELECT s.category, t.completed_at FROM pm_tasks t "
            "JOIN pm_task_statuses s ON s.id = t.status_id WHERE t.id = :t"),
            {"t": box})).one()
        moves = (await db.execute(text(
            "SELECT meta->>'to_category' FROM pm_activities "
            "WHERE task_id = :t AND type = 'status_change' "
            "ORDER BY created_at, seq"), {"t": box})).scalars().all()
        check("11b complete it, then bulk NEXT: open, in my Next, and on the timeline",
              closed_first == "DONE" and outcome == "applied"
              and after["disposition"] == "NEXT" and state.category == "todo"
              and state.completed_at is None and moves[-1:] == ["todo"],
              f"first={closed_first} now={after['disposition']} "
              f"lane={state.category} completed={state.completed_at} moves={moves}")

        # ── 12. another tenant sees none of it ─────────────────────────────
        other = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'other') RETURNING id"),
            {"slug": f"live-s6f-other-{TAG}"},
        )).scalar_one()
        leaked = await _mine(db, other, ALICE)
        check("12 another tenant sees none of it", leaked == {},
              f"leaked={sorted(leaked)}")

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
