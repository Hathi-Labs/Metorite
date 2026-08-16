"""WS-27r against a REAL Postgres.

What only a database can answer:

* does asyncpg bind a Python `None` for `:number` when the statement compares
  it to a BIGINT column three times? (A NULL with no inferable type is exactly
  the shape that has failed twice in this app.)
* is `ORDER BY rank` legal, given `rank` is also a window-function name?
* does the backslash actually work as LIKE's escape on a BOUND parameter,
  which is where `standard_conforming_strings` could have interfered?
* does the three-table join keep the visibility clause's `t.` alias in scope?
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
from gateway.routes.projects import search as search_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
OPEN_P = "11111111-1111-1111-1111-111111111111"
SECRET_P = "44444444-4444-4444-4444-444444444444"
TODO = "22222222-2222-2222-2222-222222222222"
SECRET_S = "55555555-5555-5555-5555-555555555555"

# title, description, number, archived
TASKS = [
    ("Parser rewrite", None, 1, False),
    ("Fix the parser crash", None, 2, False),
    ("Unrelated work", "the parser is mentioned only here", 3, False),
    ("Rename task_id everywhere", None, 4, False),
    ("Rename taskXid everywhere", None, 5, False),
    ("Cut latency 50% by Friday", None, 6, False),
    ("Ship 500 units", None, 7, False),
    ("Old parser work", None, 8, True),
    ("Answer to everything", None, 42, False),
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
        for pid, name, granted in ((OPEN_P, "Ops", True), (SECRET_P, "Secret", False)):
            await db.execute(text(
                "INSERT INTO pm_projects (id,name,source,created_by) "
                "VALUES (CAST(:p AS uuid),:n,'manual',:me)"),
                {"p": pid, "n": name, "me": ME})
            if granted:
                await db.execute(text(
                    "INSERT INTO pm_project_grants (project_id,subject,created_by) "
                    "VALUES (CAST(:p AS uuid),:s,:s)"), {"p": pid, "s": ME})
        for sid, pid in ((TODO, OPEN_P), (SECRET_S, SECRET_P)):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id,project_id,name,position,"
                "category,is_default) VALUES (CAST(:s AS uuid),CAST(:p AS uuid),"
                "'To do',10,'todo',true)"), {"s": sid, "p": pid})
        for title, body, number, archived in TASKS:
            await db.execute(text(
                "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,"
                "title,description,task_number,archived_at,created_by) "
                "VALUES (gen_random_uuid(),CAST(:p AS uuid),CAST(:p AS uuid),"
                f"CAST(:s AS uuid),:ti,:d,:n,{'now()' if archived else 'NULL'},:me)"),
                {"p": OPEN_P, "s": TODO, "ti": title, "d": body, "n": number,
                 "me": ME})
        await db.execute(text(
            "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,"
            "title,task_number,created_by) VALUES (gen_random_uuid(),"
            "CAST(:p AS uuid),CAST(:p AS uuid),CAST(:s AS uuid),"
            "'Confidential parser rewrite',1,:me)"),
            {"p": SECRET_P, "s": SECRET_S, "me": ME})
        await db.commit()
    finally:
        await db.close()


def user():
    return UserContext(
        email=ME, role=UserRole.EMPLOYEE, access=build_access(["feature:projects"]),
    )


async def main():
    await seed()

    hits = await search_mod.search_tasks(q="parser", user=user())
    titles = [r["title"] for r in hits["rows"]]
    check("relevance order", titles, [
        "Parser rewrite",            # rank 1 — title prefix
        "Fix the parser crash",      # rank 2 — title contains
        "Unrelated work",            # rank 3 — description only
    ])
    check("archived not findable", "Old parser work" in titles, False)
    check("an ungranted project is unreachable",
          "Confidential parser rewrite" in titles, False)
    check("the project is named", hits["rows"][0]["project_name"], "Ops")
    check("not truncated", hits["truncated"], False)

    # A NULL bound for :number, compared to a BIGINT three times.
    check("a word query runs at all", len(hits["rows"]), 3)

    numbered = await search_mod.search_tasks(q="#42", user=user())
    check("the exact number ranks first",
          (numbered["rows"][0]["title"], numbered["rows"][0]["rank"]),
          ("Answer to everything", 0))

    underscore = await search_mod.search_tasks(q="task_id", user=user())
    check("an underscore is literal",
          [r["title"] for r in underscore["rows"]],
          ["Rename task_id everywhere"])

    percent = await search_mod.search_tasks(q="50%", user=user())
    check("a percent is literal",
          [r["title"] for r in percent["rows"]],
          ["Cut latency 50% by Friday"])

    short = await search_mod.search_tasks(q="p", user=user())
    check("a one-character query is empty", short["rows"], [])

    capped = await search_mod.search_tasks(q="e", limit=2, user=user())
    check("a one-char query is empty even with a limit", capped["rows"], [])

    small = await search_mod.search_tasks(q="re", limit=1, user=user())
    check("a cap of one truncates and says so",
          (len(small["rows"]), small["truncated"]), (1, True))

    # The list endpoint's own `q` must be escaped too — that was the live bug.
    from gateway.routes.projects.core import Page
    listed = await tasks_mod.list_tasks(
        user=user(), q="task_id", page=Page(page=1, page_size=50),
    )
    check("the LIST endpoint escapes too",
          sorted(r["title"] for r in listed.rows), ["Rename task_id everywhere"])

    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
