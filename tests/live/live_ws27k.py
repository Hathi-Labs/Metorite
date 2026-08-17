"""WS-27k against a REAL Postgres.

The hermetic fake agrees with whatever SQL it is handed. This does not: it runs
the actual endpoint functions against a database with the actual migrations
applied, which is the only thing that catches a missing column, a CHECK the
code violates, or a cast Postgres refuses.
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects.core import Page  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects import views as views_mod  # noqa: E402
from gateway.routes.projects.views import ViewIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
OTHER = "ravi@fracktal.in"

failures: list[str] = []


def check(label: str, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        pid = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO pm_projects (id, name, source, created_by) "
                "VALUES (CAST(:id AS uuid), 'Ops', 'manual', :me)"
            ),
            {"id": pid, "me": ME},
        )
        await db.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :s)"
            ),
            {"p": pid, "s": ME},
        )
        lanes = {}
        for i, (name, category) in enumerate(
            [("To do", "todo"), ("Doing", "in_progress"), ("Done", "done")]
        ):
            sid = str(uuid.uuid4())
            lanes[category] = sid
            await db.execute(
                text(
                    "INSERT INTO pm_task_statuses "
                    "(id, project_id, name, position, category, is_default) "
                    "VALUES (CAST(:id AS uuid), CAST(:p AS uuid), :n, :pos, :c, :d)"
                ),
                {"id": sid, "p": pid, "n": name, "pos": i, "c": category,
                 "d": category == "todo"},
            )

        counter = {"n": 0}

        async def task(title, category, *, due=None, people=(), importance=None,
                       description=None):
            tid = str(uuid.uuid4())
            counter["n"] += 1
            await db.execute(
                text(
                    "INSERT INTO pm_tasks "
                    "(id, project_id, root_project_id, status_id, title, "
                    " description, importance, due_at, source, created_by, task_number) "
                    "VALUES (CAST(:id AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                    "        CAST(:s AS uuid), :t, :d, :i, "
                    "        CAST(:due AS timestamptz), 'manual', :me, :num)"
                ),
                {"id": tid, "p": pid, "s": lanes[category], "t": title,
                 "d": description, "i": importance, "due": due, "me": ME, "num": counter["n"]},
            )
            for who in people:
                await db.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
                        "VALUES (CAST(:t AS uuid), :a, :me)"
                    ),
                    {"t": tid, "a": who, "me": ME},
                )
            return tid

        await task("Fix the extruder", "todo", due=datetime(2020, 1, 1, tzinfo=UTC),
                   people=[ME, OTHER], importance=3)
        await task("Shipped late", "done", due=datetime(2020, 1, 1, tzinfo=UTC), people=[ME])
        await task("Nobody's problem", "todo", description="jammed nozzle")
        await task("Ravi's job", "in_progress", people=[OTHER])
        await db.commit()
        return pid
    finally:
        await db.close()


async def main():
    pid = await seed()
    user = UserContext(email=ME, role="member")

    async def ls(**kw):
        return await tasks_mod.list_tasks(
            user=user, project_id=pid, page=Page(page=1, page_size=100), **kw
        )

    titles = lambda r: sorted(t["title"] for t in r.rows)  # noqa: E731

    all_rows = await ls()
    check("all four tasks are visible", all_rows.total, 4)
    check(
        "assignees arrive on the LIST, sorted",
        next(t["assignees"] for t in all_rows.rows if t["title"] == "Fix the extruder"),
        [ME, OTHER],
    )
    check(
        "an unassigned task still carries the key",
        next(t["assignees"] for t in all_rows.rows if t["title"] == "Nobody's problem"),
        [],
    )

    check("status_category=todo", titles(await ls(status_category="todo")),
          ["Fix the extruder", "Nobody's problem"])
    check("two categories", (await ls(status_category="todo,done")).total, 3)
    check("overdue excludes the finished one",
          titles(await ls(overdue=True)), ["Fix the extruder"])
    check("assignee, capitalised", titles(await ls(assignee="Priya@Fracktal.IN")),
          ["Fix the extruder", "Shipped late"])
    check("assignees CSV", (await ls(assignees=f"{ME},{OTHER}")).total, 3)
    check("unassigned", titles(await ls(unassigned=True)), ["Nobody's problem"])
    check("q searches the description too", titles(await ls(q="nozzle")),
          ["Nobody's problem"])
    check("q is case-insensitive", (await ls(q="EXTRUDER")).total, 1)
    check("importance_gte", titles(await ls(importance_gte=3)), ["Fix the extruder"])
    check("due_before, as a bare date from a query string",
          (await ls(due_before="2021-01-01")).total, 2)
    check("due_before, as a full timestamp",
          (await ls(due_before="2021-01-01T00:00:00Z")).total, 2)
    try:
        await ls(due_before="tomorrow")
        check("an unparseable due_before is refused", "no error", "422")
    except Exception as exc:
        check("an unparseable due_before is refused",
              getattr(exc, "status_code", None), 422)
    check("filters combine", titles(await ls(status_category="todo", assignee=ME)),
          ["Fix the extruder"])
    check("a filter that matches nothing is empty, not an error",
          (await ls(q="zzzz")).total, 0)

    try:
        await ls(status_category="in-progress")
        check("unknown category is refused", "no error", "422")
    except Exception as exc:  # HTTPException
        check("unknown category is refused", getattr(exc, "status_code", None), 422)

    # Saved views, round trip through the real column.
    payload = ViewIn(
        name="My open work",
        view_type="board",
        config={
            "filters": {"status_category": "todo", "assignee": ME, "colour": "red"},
            "group_by": "assignee",
            "nonsense": 1,
        },
        position=300.0,
    )

    created = await views_mod.create_view(pid, payload, user=user)
    check("unknown config keys are dropped on the way in",
          created["config"],
          {"filters": {"status_category": "todo", "assignee": ME},
           "group_by": "assignee"})

    listed = await views_mod.list_views(pid, user=user)
    stored = next(v for v in listed["rows"] if v["id"] == created["id"])
    check("the config survives a round trip through jsonb",
          stored["config"]["group_by"], "assignee")

    saved = await ls(**stored["config"]["filters"])
    check("the saved view and the same filters typed by hand agree",
          titles(saved), titles(await ls(status_category="todo", assignee=ME)))

    patched = await views_mod.patch_view(
        created["id"],
        ViewIn(config={"filters": {"overdue": True}, "group_by": "phase"}),
        user=user,
    )
    check("a patch normalises too, and an unknown grouping falls back",
          patched["config"], {"filters": {"overdue": True}, "group_by": "status"})

    gone = await views_mod.delete_view(created["id"], user=user)
    check("delete reports its cascade", gone["cascaded"], {"positions": 0})

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
