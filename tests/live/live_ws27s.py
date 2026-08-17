"""WS-27s against a REAL Postgres.

The badge maths is trivial. What only a database can answer: do the two
aggregates actually PARSE and RUN — `count(*) FILTER (WHERE …)`, an aggregate
over `= ANY(CAST(:ids AS uuid[]))` with a list of Python strings, and a
`GROUP BY` whose rows have to be matched back to the page by string id.

asyncpg has bitten this project twice on exactly that last point: it infers a
bound parameter's type from a surrounding CAST and refuses a mismatched Python
type.
"""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.core import Page  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
P = "11111111-1111-1111-1111-111111111111"
TODO = "22222222-2222-2222-2222-222222222222"
DONE = "33333333-3333-3333-3333-333333333333"
PARENT = "aaaaaaaa-0000-0000-0000-000000000001"
KID_DONE = "aaaaaaaa-0000-0000-0000-000000000002"
KID_OPEN = "aaaaaaaa-0000-0000-0000-000000000003"
KID_GONE = "aaaaaaaa-0000-0000-0000-000000000004"
BLOCKED = "bbbbbbbb-0000-0000-0000-000000000001"
BLOCKER_OPEN = "bbbbbbbb-0000-0000-0000-000000000002"
BLOCKER_DONE = "bbbbbbbb-0000-0000-0000-000000000003"
LONELY = "cccccccc-0000-0000-0000-000000000001"

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
        rows = (
            (PARENT, TODO, "Ship it", None, None),
            (KID_DONE, DONE, "One", PARENT, None),
            (KID_OPEN, TODO, "Two", PARENT, None),
            (KID_GONE, TODO, "Dropped", PARENT, "now()"),
            (BLOCKED, TODO, "Waiting", None, None),
            (BLOCKER_OPEN, TODO, "Open blocker", None, None),
            (BLOCKER_DONE, DONE, "Shipped blocker", None, None),
            (LONELY, TODO, "Alone", None, None),
        )
        for n, (tid, sid, title, parent, archived) in enumerate(rows, start=1):
            await db.execute(text(
                "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,"
                "title,task_number,parent_task_id,archived_at,created_by) "
                "VALUES (CAST(:t AS uuid),CAST(:p AS uuid),CAST(:p AS uuid),"
                f"CAST(:s AS uuid),:ti,:n,CAST(:par AS uuid),"
                f"{archived or 'NULL'},:me)"),
                {"t": tid, "p": P, "s": sid, "ti": title, "n": n,
                 "par": parent, "me": ME})
        for src in (BLOCKER_OPEN, BLOCKER_DONE):
            await db.execute(text(
                "INSERT INTO pm_task_links (source_task_id,target_task_id,"
                "link_type,created_by) VALUES (CAST(:s AS uuid),"
                "CAST(:t AS uuid),'blocks',:me)"),
                {"s": src, "t": BLOCKED, "me": ME})
        # A non-blocking link, both ways, must not count.
        await db.execute(text(
            "INSERT INTO pm_task_links (source_task_id,target_task_id,"
            "link_type,created_by) VALUES (CAST(:s AS uuid),CAST(:t AS uuid),"
            "'relates_to',:me)"),
            {"s": LONELY, "t": BLOCKED, "me": ME})
        await db.commit()
    finally:
        await db.close()


def user(email=ME):
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(["*"]),
    )


async def main():
    await seed()
    result = await tasks_mod.list_tasks(user=user(), page=Page(page=1, page_size=50))
    by_id = {str(r["id"]): r for r in result.rows}

    check("the page came back", len(by_id), 7)  # KID_GONE is archived
    check("parent progress", by_id[PARENT]["subtasks"], {"done": 1, "total": 2})
    check("blocked count", by_id[BLOCKED]["blocked_by_count"], 1)
    check("a lonely task has zeros",
          (by_id[LONELY]["subtasks"], by_id[LONELY]["blocked_by_count"]),
          ({"done": 0, "total": 0}, 0))
    check("the open blocker is not itself blocked",
          by_id[BLOCKER_OPEN]["blocked_by_count"], 0)
    check("a child carries the keys too", by_id[KID_OPEN]["subtasks"],
          {"done": 0, "total": 0})
    check("every row has both keys",
          all("subtasks" in r and "blocked_by_count" in r for r in result.rows),
          True)

    # Second page: the aggregates must be bounded to THIS page's ids, not to
    # every task the filter matched.
    page2 = await tasks_mod.list_tasks(
        user=user(), page=Page(page=1, page_size=2),
    )
    check("a short page still fills both keys",
          all("subtasks" in r for r in page2.rows), True)
    check("a short page is short", len(page2.rows), 2)

    # Assignees still attach — the two roll-ups run after that one.
    check("assignees survived", "assignees" in by_id[PARENT], True)

    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
