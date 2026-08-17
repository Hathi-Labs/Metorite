"""WS-27o against a REAL Postgres.

The date maths is pure and tested hermetically. What only a real database can
answer: does closing a task actually spawn its successor, does closing it TWICE
spawn one, do the CHECKs refuse the rules they claim to, and does the successor
carry what it should.
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import recurrence as rec_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.core import Page, TaskIn  # noqa: E402
from gateway.routes.projects.recurrence import RecurrenceIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
RAVI = "ravi@fracktal.in"
PID = "11111111-1111-1111-1111-111111111111"
TODO = "22222222-2222-2222-2222-222222222222"
DONE = "33333333-3333-3333-3333-333333333333"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        await db.execute(text(
            "INSERT INTO pm_projects (id,name,source,created_by) "
            "VALUES (CAST(:p AS uuid),'Ops','manual',:me)"), {"p": PID, "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id,subject,created_by) "
            "VALUES (CAST(:p AS uuid),:s,:s)"), {"p": PID, "s": ME})
        for sid, name, cat, default in (
            (TODO, "To do", "todo", True), (DONE, "Done", "done", False),
        ):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id,project_id,name,position,category,"
                "is_default) VALUES (CAST(:s AS uuid),CAST(:p AS uuid),:n,1,:c,:d)"),
                {"s": sid, "p": PID, "n": name, "c": cat, "d": default})
        await db.commit()
    finally:
        await db.close()


async def make_task(title, *, due, tags=("ops",), assignees=(RAVI,)):
    db = await get_db()
    try:
        tid = str(uuid.uuid4())
        n = int((await db.execute(text(
            "INSERT INTO pm_task_counters (project_id,last_value) "
            "VALUES (CAST(:p AS uuid),1) ON CONFLICT (project_id) DO UPDATE "
            "SET last_value = pm_task_counters.last_value + 1 RETURNING last_value"),
            {"p": PID})).scalar())
        await db.execute(text(
            "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,title,"
            "description,importance,due_at,tags,custom_fields,source,created_by,"
            "task_number) VALUES (CAST(:i AS uuid),CAST(:p AS uuid),CAST(:p AS uuid),"
            "CAST(:s AS uuid),:t,'the standing description',2,:due,:tags,"
            "CAST('{\"owner\":\"ops\"}' AS jsonb),'manual',:me,:n)"),
            {"i": tid, "p": PID, "s": TODO, "t": title, "due": due,
             "tags": list(tags), "me": ME, "n": n})
        for who in assignees:
            await db.execute(text(
                "INSERT INTO pm_task_assignees (task_id,assignee,assigned_by) "
                "VALUES (CAST(:t AS uuid),:a,:me)"), {"t": tid, "a": who, "me": ME})
        await db.commit()
        return tid
    finally:
        await db.close()


async def rows(sql, **p):
    db = await get_db()
    try:
        return (await db.execute(text(sql), p)).fetchall()
    finally:
        await db.close()


async def main():
    await seed()
    user = UserContext(email=ME, role="member")
    yesterday = datetime.now(UTC) - timedelta(days=1)

    # ── Setting a rule ─────────────────────────────────────────────────────
    t1 = await make_task("Weekly stock count", due=yesterday)
    got = await rec_mod.set_recurrence(
        t1, RecurrenceIn(freq="daily", interval=7, anchor="due"), user=user)
    check("a rule can be set", got["rule"]["freq"], "daily")
    check("and read back", (await rec_mod.get_recurrence(t1, user=user))["rule"]["interval"], 7)

    for label, payload in (
        ("an unknown frequency", RecurrenceIn(freq="fortnightly")),
        ("a weekly rule with no weekdays", RecurrenceIn(freq="weekly")),
        ("a monthly rule with no day", RecurrenceIn(freq="monthly")),
        ("an interval of zero", RecurrenceIn(freq="daily", interval=0)),
    ):
        try:
            await rec_mod.set_recurrence(t1, payload, user=user)
            check(f"{label} is refused", "no error", "422")
        except Exception as exc:
            check(f"{label} is refused", getattr(exc, "status_code", None), 422)

    # ── Closing spawns the successor ───────────────────────────────────────
    before = len(await rows("SELECT id FROM pm_tasks"))
    await tasks_mod.patch_task(t1, TaskIn(status_id=DONE), user=user)
    after = await rows(
        "SELECT * FROM pm_tasks WHERE id <> CAST(:t AS uuid) ORDER BY created_at DESC",
        t=t1)
    check("closing it created exactly one successor", len(await rows("SELECT id FROM pm_tasks")),
          before + 1)

    successor = after[0]
    check("the successor carries the title", successor.title, "Weekly stock count")
    check("and the description", successor.description, "the standing description")
    check("and the priority", successor.importance, 2)
    check("and the tags", list(successor.tags), ["ops"])
    check("and the custom fields", successor.custom_fields, {"owner": "ops"})
    check("but starts in the DEFAULT lane, not Done", str(successor.status_id), TODO)
    check("and is not already finished", successor.completed_at, None)
    check("with a fresh task number", successor.task_number != 1, True)
    check("and is due in the FUTURE", successor.due_at > datetime.now(UTC), True)
    check("and stays in the same project", str(successor.project_id), PID)

    people = await rows(
        "SELECT assignee FROM pm_task_assignees WHERE task_id = :t", t=successor.id)
    check("the assignees came with it", [r.assignee for r in people], [RAVI])

    # ── Closing TWICE does not spawn twice ─────────────────────────────────
    count_now = len(await rows("SELECT id FROM pm_tasks"))
    await tasks_mod.patch_task(t1, TaskIn(status_id=TODO), user=user)   # reopen
    await tasks_mod.patch_task(t1, TaskIn(status_id=DONE), user=user)   # re-close
    check("reopening and re-closing spawns nothing more",
          len(await rows("SELECT id FROM pm_tasks")), count_now)

    stamped = await rows(
        "SELECT recurrence_spawned_at FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=t1)
    check("because the spawn is stamped", stamped[0].recurrence_spawned_at is not None, True)

    # ── A task with no rule does nothing ───────────────────────────────────
    plain = await make_task("One-off", due=yesterday)
    count_now = len(await rows("SELECT id FROM pm_tasks"))
    await tasks_mod.patch_task(plain, TaskIn(status_id=DONE), user=user)
    check("a task with no rule spawns nothing",
          len(await rows("SELECT id FROM pm_tasks")), count_now)

    # ── The occurrence cap ends the series ─────────────────────────────────
    t2 = await make_task("Three times only", due=yesterday)
    await rec_mod.set_recurrence(
        t2, RecurrenceIn(freq="daily", max_occurrences=1), user=user)
    await tasks_mod.patch_task(t2, TaskIn(status_id=DONE), user=user)
    made = await rows(
        "SELECT occurrences_made FROM pm_recurrences WHERE id = ("
        " SELECT recurrence_id FROM pm_tasks WHERE id = CAST(:t AS uuid))", t=t2)
    check("the counter advanced", made[0].occurrences_made if made else None, 1)

    # The successor is now at the cap, so closing IT ends the series.
    child = (await rows(
        "SELECT id FROM pm_tasks WHERE title = 'Three times only' "
        "AND id <> CAST(:t AS uuid)", t=t2))[0]
    count_now = len(await rows("SELECT id FROM pm_tasks"))
    await tasks_mod.patch_task(str(child.id), TaskIn(status_id=DONE), user=user)
    check("at the cap, closing spawns nothing",
          len(await rows("SELECT id FROM pm_tasks")), count_now)

    # ── Stopping a series keeps the work ───────────────────────────────────
    t3 = await make_task("Stoppable", due=yesterday)
    await rec_mod.set_recurrence(t3, RecurrenceIn(freq="daily"), user=user)
    gone = await rec_mod.clear_recurrence(t3, user=user)
    check("stopping reports what it detached", gone["cascaded"]["tasks_detached"], 1)
    still = await rows(
        "SELECT id FROM pm_tasks WHERE id = CAST(:t AS uuid)", t=t3)
    check("and the task itself survives", len(still), 1)
    check("clearing again is not an error",
          (await rec_mod.clear_recurrence(t3, user=user))["cleared"], False)

    # ── The database refuses what Python refuses ───────────────────────────
    db = await get_db()
    try:
        for label, sql in (
            ("a weekly rule with no weekdays",
             "INSERT INTO pm_recurrences (project_id,freq,created_by) "
             "VALUES (CAST(:p AS uuid),'weekly',:me)"),
            ("a monthly rule with no day",
             "INSERT INTO pm_recurrences (project_id,freq,created_by) "
             "VALUES (CAST(:p AS uuid),'monthly',:me)"),
            ("an interval of zero",
             "INSERT INTO pm_recurrences (project_id,freq,interval,created_by) "
             "VALUES (CAST(:p AS uuid),'daily',0,:me)"),
            ("a weekday of 8",
             "INSERT INTO pm_recurrences (project_id,freq,weekdays,created_by) "
             "VALUES (CAST(:p AS uuid),'weekly',ARRAY[8]::smallint[],:me)"),
        ):
            try:
                await db.execute(text(sql), {"p": PID, "me": ME})
                await db.commit()
                check(f"the DATABASE refuses {label}", "inserted", "refused")
            except Exception:
                await db.rollback()
                check(f"the DATABASE refuses {label}", "refused", "refused")
    finally:
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
