"""WS-27p against a REAL Postgres.

The cycle maths is pure and tested hermetically. What only a database can
answer: does the two-direction UNION actually run, does the visibility clause
scope the CHILDREN rather than inheriting from the parent, and does a link to a
task the reader cannot see stay hidden.
"""
import asyncio
import os
import sys
import uuid

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import relations as rel_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.tasks import LinkIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
OPEN_P = "11111111-1111-1111-1111-111111111111"
SECRET_P = "44444444-4444-4444-4444-444444444444"
TODO = "22222222-2222-2222-2222-222222222222"
DONE = "33333333-3333-3333-3333-333333333333"
SECRET_S = "55555555-5555-5555-5555-555555555555"
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
        for sid, pid, name, cat, dflt in (
            (TODO, OPEN_P, "To do", "todo", True),
            (DONE, OPEN_P, "Done", "done", False),
            (SECRET_S, SECRET_P, "To do", "todo", True),
        ):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id,project_id,name,position,category,"
                "is_default) VALUES (CAST(:s AS uuid),CAST(:p AS uuid),:n,1,:c,:d)"),
                {"s": sid, "p": pid, "n": name, "c": cat, "d": dflt})
        await db.commit()
    finally:
        await db.close()


async def task(title, *, status=TODO, project=OPEN_P, parent=None):
    db = await get_db()
    try:
        tid = str(uuid.uuid4())
        n = int((await db.execute(text(
            "INSERT INTO pm_task_counters (project_id,last_value) VALUES "
            "(CAST(:p AS uuid),1) ON CONFLICT (project_id) DO UPDATE SET "
            "last_value = pm_task_counters.last_value + 1 RETURNING last_value"),
            {"p": project})).scalar())
        await db.execute(text(
            "INSERT INTO pm_tasks (id,project_id,root_project_id,status_id,title,"
            "parent_task_id,source,created_by,task_number) VALUES "
            "(CAST(:i AS uuid),CAST(:p AS uuid),CAST(:p AS uuid),CAST(:s AS uuid),"
            ":t,CAST(:par AS uuid),'manual',:me,:n)"),
            {"i": tid, "p": project, "s": status, "t": title, "par": parent,
             "me": ME, "n": n})
        await db.commit()
        return tid
    finally:
        await db.close()


async def main():
    await seed()
    user = UserContext(email=ME, role="member")

    parent = await task("Ship the firmware")
    kid1 = await task("Write it", parent=parent)
    kid2 = await task("Test it", parent=parent, status=DONE)
    kid3 = await task("Ship it", parent=parent)
    hidden_kid = await task("Classified step", parent=parent,
                            project=SECRET_P, status=SECRET_S)

    # ── Subtasks and progress ──────────────────────────────────────────────
    got = await rel_mod.get_relations(parent, user=user)
    check("subtasks are listed at all", len(got["subtasks"]), 3)
    check("progress counts the finished one", got["progress"], {"done": 1, "total": 3})
    check("a subtask in a project the reader cannot see is NOT listed",
          [s["title"] for s in got["subtasks"]],
          ["Write it", "Test it", "Ship it"])
    check("and its title did not leak",
          any("Classified" in s["title"] for s in got["subtasks"]), False)
    check("the status NAME comes back so the panel need not re-read",
          got["subtasks"][1]["status_name"], "Done")

    # ── Links, both directions ─────────────────────────────────────────────
    await tasks_mod.create_link(
        kid1, LinkIn(target_task_id=kid3, link_type="blocks"), user=user)
    await tasks_mod.create_link(
        kid1, LinkIn(target_task_id=kid2, link_type="relates_to"), user=user)

    from_kid1 = await rel_mod.get_relations(kid1, user=user)
    outgoing = [x for x in from_kid1["links"] if x["direction"] == "outgoing"]
    check("outgoing links come back", len(outgoing), 2)
    check("kid1 blocks kid3",
          [x["title"] for x in outgoing if x["link_type"] == "blocks"], ["Ship it"])
    check("and kid1 is blocked by nothing", from_kid1["blocked_by"], [])

    from_kid3 = await rel_mod.get_relations(kid3, user=user)
    incoming = [x for x in from_kid3["links"] if x["direction"] == "incoming"]
    check("the OTHER end sees it as incoming", len(incoming), 1)
    check("kid3 is blocked by kid1",
          [x["title"] for x in from_kid3["blocked_by"]], ["Write it"])

    # ── A finished blocker stops blocking ──────────────────────────────────
    from gateway.routes.projects.core import TaskIn
    await tasks_mod.patch_task(kid1, TaskIn(status_id=DONE), user=user)
    from_kid3 = await rel_mod.get_relations(kid3, user=user)
    check("once the blocker is done, nothing is blocking",
          from_kid3["blocked_by"], [])
    check("but the LINK is still there — it is history, not a flag",
          len([x for x in from_kid3["links"] if x["link_type"] == "blocks"]), 1)

    # ── The cycle guard, live ──────────────────────────────────────────────
    a, b, c = await task("A"), await task("B"), await task("C")
    await tasks_mod.create_link(a, LinkIn(target_task_id=b, link_type="blocks"), user=user)
    await tasks_mod.create_link(b, LinkIn(target_task_id=c, link_type="blocks"), user=user)

    try:
        await tasks_mod.create_link(
            c, LinkIn(target_task_id=a, link_type="blocks"), user=user)
        check("a three-hop cycle is refused", "created", "422")
    except Exception as exc:
        check("a three-hop cycle is refused", getattr(exc, "status_code", None), 422)

    try:
        await tasks_mod.create_link(
            b, LinkIn(target_task_id=a, link_type="blocks"), user=user)
        check("a reciprocal block is refused", "created", "422")
    except Exception as exc:
        check("a reciprocal block is refused", getattr(exc, "status_code", None), 422)

    # relates_to is NOT directed, so a reciprocal one is fine.
    await tasks_mod.create_link(
        c, LinkIn(target_task_id=a, link_type="relates_to"), user=user)
    check("but a reciprocal relates_to is allowed",
          len((await rel_mod.get_relations(c, user=user))["links"]) >= 1, True)

    # ── A link to an invisible task cannot be created at all ───────────────
    secret = await task("Classified", project=SECRET_P, status=SECRET_S)
    try:
        await tasks_mod.create_link(
            a, LinkIn(target_task_id=secret, link_type="blocks"), user=user)
        check("linking to an unreadable task is refused", "created", "404")
    except Exception as exc:
        check("linking to an unreadable task is refused",
              getattr(exc, "status_code", None), 404)

    # ── A task with nothing attached answers cleanly ───────────────────────
    lonely = await task("Nothing attached")
    empty = await rel_mod.get_relations(lonely, user=user)
    check("no subtasks is 0 of 0", empty["progress"], {"done": 0, "total": 0})
    check("and no links is an empty list, not null", empty["links"], [])
    check("and nothing blocking", empty["blocked_by"], [])

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
