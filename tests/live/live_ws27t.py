"""WS-27t's backend half against a REAL Postgres.

What only a database can answer: does `= ANY(CAST(:ids AS uuid[]))` on BOTH
ends of the same row actually run, does the relations read still work now that
it selects a DATE beside a timestamptz, and does an edge to a task the reader
cannot see stay hidden — the visibility question a new query always has to be
asked again, because `_WINDOW_LINKS_SQL` carries no visibility clause of its
own and relies entirely on its ids coming from an already-scoped set.
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
from gateway.routes.projects import relations as rel_mod  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
OPEN_P = "11111111-1111-1111-1111-111111111111"
SECRET_P = "44444444-4444-4444-4444-444444444444"
TODO = "22222222-2222-2222-2222-222222222222"
SECRET_S = "55555555-5555-5555-5555-555555555555"
A = "aaaaaaaa-0000-0000-0000-00000000000a"
B = "aaaaaaaa-0000-0000-0000-00000000000b"
C = "aaaaaaaa-0000-0000-0000-00000000000c"
HIDDEN = "aaaaaaaa-0000-0000-0000-00000000000d"

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
        rows = (
            (A, OPEN_P, TODO, "A", "2026-08-03", "2026-08-07T17:00:00Z"),
            (B, OPEN_P, TODO, "B", "2026-08-05", "2026-08-12T17:00:00Z"),
            (C, OPEN_P, TODO, "C", "2026-08-20", None),
            (HIDDEN, SECRET_P, SECRET_S, "Hidden", "2026-08-06", None),
        )
        for n, (tid, pid, sid, title, start, due) in enumerate(rows, start=1):
            await db.execute(text(
                "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,"
                "title,task_number,start_date,due_at,created_by) "
                "VALUES (CAST(:t AS uuid),CAST(:p AS uuid),CAST(:p AS uuid),"
                "CAST(:s AS uuid),:ti,:n,CAST(:sd AS date),"
                "CAST(:du AS timestamptz),:me)"),
                {"t": tid, "p": pid, "s": sid, "ti": title, "n": n,
                 "sd": date.fromisoformat(start) if start else None,
                 "du": datetime.fromisoformat(due.replace("Z", "+00:00"))
                       if due else None,
                 "me": ME})
        for src, tgt in ((A, B), (B, C), (HIDDEN, C)):
            await db.execute(text(
                "INSERT INTO pm_task_links (source_task_id,target_task_id,"
                "link_type,created_by) VALUES (CAST(:s AS uuid),"
                "CAST(:t AS uuid),'blocks',:me)"), {"s": src, "t": tgt, "me": ME})
        await db.commit()
    finally:
        await db.close()


def user():
    return UserContext(
        email=ME, role=UserRole.EMPLOYEE, access=build_access(["feature:projects"]),
    )


async def main():
    await seed()
    result = await cal_mod.get_calendar(
        user=user(), date_from="2026-08-01", date_to="2026-09-01",
        include_links=True,
    )
    ids = {r["id"] for r in result["rows"]}
    edges = {(e["blocker_id"], e["blocked_id"]) for e in result["links"]}

    check("only granted tasks in the window", ids, {A, B, C})
    check("both drawable edges came back", edges, {(A, B), (B, C)})
    check("an edge FROM an ungranted task is not drawn",
          any(HIDDEN in pair for pair in edges), False)
    check("but C still knows it is blocked",
          next(r for r in result["rows"] if r["id"] == C)["blocked_by_count"], 2)

    without = await cal_mod.get_calendar(
        user=user(), date_from="2026-08-01", date_to="2026-09-01",
    )
    check("links absent unless asked for", without["links"], [])

    # The relations read now selects a DATE beside a timestamptz in a UNION ALL.
    rel = await rel_mod.get_relations(B, user=user())
    incoming = [x for x in rel["links"] if x["direction"] == "incoming"]
    check("relations carries the blocker's dates",
          (incoming[0]["start_date"], incoming[0]["due_at"][:10]),
          ("2026-08-03", "2026-08-07"))
    check("a DATE round-trips as a bare date, not an instant",
          "T" in str(incoming[0]["start_date"]), False)

    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
