"""WS-27q against a REAL Postgres.

What only a database can answer:

* does `CAST(t.start_date AS timestamp) AT TIME ZONE 'UTC'` actually parse and
  produce a timestamptz comparable to a bound `datetime`?
* does asyncpg accept an aware `datetime` for `:window_from` with no CAST in
  the statement to mislead its type inference? (It refused a `str` for
  `due_before` under exactly those conditions in WS-27k.)
* does the interval overlap behave as claimed for the six real cases — before,
  after, spanning, touching each edge, and neither date?
* is the ORDER BY over `coalesce(start_date, CAST(due_at AS date))` legal, given
  the two branches have different types before the cast?
* does the session TimeZone actually NOT move the answer?
"""
import asyncio
import os
import sys
from datetime import UTC, date, datetime

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import calendar as cal_mod  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
P = "11111111-1111-1111-1111-111111111111"
TODO = "22222222-2222-2222-2222-222222222222"
DONE = "33333333-3333-3333-3333-333333333333"

# title, start_date, due_at, status  — August 2026 is the window.
TASKS = [
    ("due inside", None, "2026-08-14T10:00:00Z", TODO),
    ("start only inside", "2026-08-03", None, TODO),
    ("ended before", None, "2026-07-20T10:00:00Z", TODO),
    ("starts after", "2026-09-10", None, TODO),
    ("spans the window", "2026-06-01", "2026-12-01T00:00:00Z", TODO),
    ("touches the first day", "2026-08-01", "2026-08-01T12:00:00Z", TODO),
    ("due at the far edge", None, "2026-09-01T00:00:00Z", TODO),
    ("due just inside the edge", None, "2026-08-31T23:59:00Z", TODO),
    ("no dates at all", None, None, TODO),
    ("closed and inside", None, "2026-08-20T10:00:00Z", DONE),
    ("bar ending on day one", "2026-07-01", "2026-08-01T00:00:00Z", TODO),
]

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
            "VALUES (CAST(:p AS uuid),'Ops','manual',:me)"), {"p": P, "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id,subject,created_by) "
            "VALUES (CAST(:p AS uuid),:s,:s)"), {"p": P, "s": ME})
        for sid, name, cat, dflt, pos in (
            (TODO, "To do", "todo", True, 10),
            (DONE, "Done", "done", False, 40),
        ):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id,project_id,name,position,"
                "category,is_default) VALUES (CAST(:s AS uuid),CAST(:p AS uuid),"
                ":n,:pos,:c,:d)"),
                {"s": sid, "p": P, "n": name, "pos": pos, "c": cat, "d": dflt})
        for n, (title, start, due, sid) in enumerate(TASKS, start=1):
            await db.execute(text(
                "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,"
                "title,task_number,start_date,due_at,created_by) "
                "VALUES (gen_random_uuid(),CAST(:p AS uuid),CAST(:p AS uuid),"
                "CAST(:s AS uuid),:ti,:n,CAST(:sd AS date),CAST(:du AS timestamptz),"
                ":me)"),
                {"p": P, "s": sid, "ti": title, "n": n,
                 "sd": date.fromisoformat(start) if start else None,
                 "du": datetime.fromisoformat(due.replace("Z", "+00:00"))
                       if due else None,
                 "me": ME})
        await db.commit()
    finally:
        await db.close()


def user():
    return UserContext(
        email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]),
    )


async def august(**kwargs):
    return await cal_mod.get_calendar(
        user=user(), date_from="2026-08-01", date_to="2026-09-01", **kwargs,
    )


async def main():
    await seed()
    result = await august()
    titles = sorted(r["title"] for r in result["rows"])

    check("the window returned the right set", titles, sorted([
        "due inside",
        "start only inside",
        "spans the window",
        "touches the first day",
        "due just inside the edge",
        "closed and inside",
        "bar ending on day one",
    ]))
    check("undated counted, not shown", result["undated"], 1)
    check("not truncated", result["truncated"], False)
    check("the window echoes back", (result["from"], result["to"]),
          ("2026-08-01", "2026-09-01"))

    # A bar whose END is exactly the window's first instant is INSIDE (>=), and
    # a due date exactly at the far edge is OUTSIDE (<). Both edges in one run.
    check("far edge excluded", "due at the far edge" in titles, False)
    check("near edge included", "bar ending on day one" in titles, True)

    filtered = await august(status_category="todo")
    check("the board's filter applies",
          "closed and inside" in {r["title"] for r in filtered["rows"]}, False)

    # The badges WS-27s added must survive onto the calendar's rows.
    check("chips have their badge data",
          all("subtasks" in r and "blocked_by_count" in r and "assignees" in r
              for r in result["rows"]), True)

    # Ordering must be stable and by the interval's START — earliest first.
    # "spans the window" starts 2026-06-01, "bar ending on day one" 2026-07-01.
    check("ordered by the interval start",
          [r["title"] for r in result["rows"]][:2],
          ["spans the window", "bar ending on day one"])
    # And the coalesce must fall back to the due date for a task with no start,
    # rather than sorting every start-less task to one end.
    check("a start-less task sorts by its due date",
          [r["title"] for r in result["rows"]].index("due inside")
          < [r["title"] for r in result["rows"]].index("closed and inside"),
          True)

    # The session TimeZone must not move the answer. `CAST(… AS timestamptz)`
    # would; `AT TIME ZONE 'UTC'` must not.
    db = await get_db()
    try:
        await db.execute(text("SET TIME ZONE 'America/Los_Angeles'"))
        rows = (await db.execute(
            text(
                "SELECT t.title FROM pm_tasks t "
                f"WHERE {cal_mod.OVERLAPS} ORDER BY t.task_number"
            ),
            {"window_from": datetime(2026, 8, 1, tzinfo=UTC),
             "window_to": datetime(2026, 9, 1, tzinfo=UTC)},
        )).fetchall()
        check("a hostile session TimeZone does not move the window",
              sorted(r.title for r in rows), titles)
    finally:
        await db.close()

    # A window wider than the maximum, and a malformed one, both from the route.
    for label, kwargs in (
        ("too wide", {"date_from": "2026-01-01", "date_to": "2030-01-01"}),
        ("backwards", {"date_from": "2026-09-01", "date_to": "2026-08-01"}),
        ("nonsense", {"date_from": "august", "date_to": "2026-09-01"}),
    ):
        try:
            await cal_mod.get_calendar(user=user(), **kwargs)
            check(f"{label} refused", "no error", "422")
        except Exception as exc:  # noqa: BLE001
            check(f"{label} refused", getattr(exc, "status_code", type(exc)), 422)

    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
