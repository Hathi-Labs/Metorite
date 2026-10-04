"""WS-27bg slice 1 against a REAL Postgres — the run-state axis and archive.

Drives the real endpoint functions: ``tree.archive_node`` / ``tree.unarchive_node``,
``tasks.list_tasks``, ``automation.run_lifecycle_sweep``,
``recurrence.spawn_successor`` and ``agent_dispatch.on_event``.

What only a database can answer here, and why the hermetic suite cannot:

* **The subtree CTE.** ``archive_node`` stamps a project's whole subtree through
  a ``WITH RECURSIVE``. The fake has no recursion and no join planner, so it
  would return whatever it was handed — and a subtree walk that quietly returns
  only the root looks *identical* to a working one until somebody archives a
  department with children.
* **The reversibility column.** ``archived_root_id`` only earns its place if an
  independently-archived subproject SURVIVES its parent's unarchive. That is a
  three-write, two-path property; it is a statement about what the WHERE clause
  matched, which is exactly what a fake cannot check.
* **The D-PM-26 fence.** "Project state derives onto tasks and never writes
  them" is only true if no ``pm_tasks`` row moves. The check below reads every
  task ``updated_at`` before and after an archive/pause/stop cycle and compares.
  A fake that does not implement UPDATE would pass that trivially and prove
  nothing.
* **The read path's plan (R8).** The default task read gained
  ``EXISTS (SELECT 1 FROM pm_projects p WHERE p.id = t.project_id AND
  p.archived_at IS NULL)``. Whether the planner turns that into a hash
  semi-join or a per-row subplan is a question only ``EXPLAIN`` answers, and
  WS-27be is the standing precedent for taking that seriously: an index that
  *looked* like it covered the case was unusable for twenty-four migrations
  and every unit test stayed green.
* **The three automation guards.** The sweep, recurrence and dispatch each
  consult ``is_runnable``. Each check below is run TWICE — once with the
  project ``active`` (the automation must fire) and once ``on_hold`` (it must
  not) — because a guard that refuses everything passes a one-sided test.

Run:
    su pgrunner -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /tmp -p 5439' start"
    uv run python tests/live/live_ws27bg.py

⚠️ ``seed()`` TRUNCATEs ``pm_projects CASCADE``. Scratch databases only.
"""
import asyncio
import os
import pathlib
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/ws27bg?host=/tmp&port=5439",
)
# Derived from THIS file rather than hard-coded, for live_ws27aa's reason: run
# from a worktree, an absolute path silently imports the MAIN checkout's
# gateway and the script then reports on code that is not under test.
sys.path.insert(0, str(
    pathlib.Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway"
))

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import agent_dispatch  # noqa: E402
from gateway.routes.projects import recurrence as rec_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects import tree as tree_mod  # noqa: E402
from gateway.routes.projects.automation import run_lifecycle_sweep  # noqa: E402
from gateway.routes.projects.core import Page  # noqa: E402
from sqlalchemy import text  # noqa: E402

OWNER = "owner@acme.example"
ORG = "11111111-1111-4000-8000-111111111111"

ROOT = "aaaaaaaa-0000-4000-8000-0000000000a0"   # the department
CHILD = "aaaaaaaa-0000-4000-8000-0000000000a1"  # a subproject of ROOT
GRAND = "aaaaaaaa-0000-4000-8000-0000000000a2"  # a subproject of CHILD
LONER = "aaaaaaaa-0000-4000-8000-0000000000a3"  # a second root, never archived

TODO = "aaaaaaaa-0000-4000-8000-0000000000b0"
DONE = "aaaaaaaa-0000-4000-8000-0000000000b1"

T_ROOT = "aaaaaaaa-0000-4000-8000-0000000000c0"
T_GRAND = "aaaaaaaa-0000-4000-8000-0000000000c1"
T_LONER = "aaaaaaaa-0000-4000-8000-0000000000c2"
T_STALE = "aaaaaaaa-0000-4000-8000-0000000000c3"   # for the close sweep
T_REPEAT = "aaaaaaaa-0000-4000-8000-0000000000c4"  # for recurrence
T_AGENT = "aaaaaaaa-0000-4000-8000-0000000000c5"   # for dispatch

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email: str = OWNER) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(["*"]),
    )


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        await db.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": OWNER})
        await db.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
                         {"o": ORG})
        await db.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (CAST(:o AS uuid), 'acme', 'Acme')"), {"o": ORG})
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'employee', 'active', "
            "CAST(:o AS uuid))"), {"e": OWNER, "o": ORG})

        # ROOT → CHILD → GRAND, so the subtree walk has something to get wrong,
        # plus LONER as the control that must never move.
        await db.execute(text(
            "INSERT INTO pm_projects (id, name, source, created_by, "
            "organization_id, close_after_months) VALUES "
            "(CAST(:p AS uuid), 'Delivery', 'manual', :who, CAST(:o AS uuid), 2)"),
            {"p": ROOT, "who": OWNER, "o": ORG})
        for pid, parent, name in (
            (CHILD, ROOT, "Firmware"), (GRAND, CHILD, "Bootloader"),
        ):
            # organization_id deliberately omitted — 161's trigger derives it.
            await db.execute(text(
                "INSERT INTO pm_projects (id, name, parent_project_id, source, "
                "created_by) VALUES (CAST(:p AS uuid), :n, CAST(:pa AS uuid), "
                "'manual', :who)"),
                {"p": pid, "n": name, "pa": parent, "who": OWNER})
        await db.execute(text(
            "INSERT INTO pm_projects (id, name, source, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), 'Untouched', 'manual', "
            ":who, CAST(:o AS uuid))"), {"p": LONER, "who": OWNER, "o": ORG})

        # Everything is granted to the org so the caller can see it.
        for pid in (ROOT, LONER):
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), 'org', :who)"),
                {"p": pid, "who": OWNER})

        for sid, name, category, default in (
            (TODO, "To do", "todo", True), (DONE, "Shipped", "done", False),
        ):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:s AS uuid), "
                "CAST(:p AS uuid), :n, 10, :c, :d)"),
                {"s": sid, "p": ROOT, "n": name, "c": category, "d": default})

        for tid, pid, title, num in (
            (T_ROOT, ROOT, "In the department", 1),
            (T_GRAND, GRAND, "Two levels down", 2),
            (T_LONER, LONER, "Somewhere else", 3),
            (T_AGENT, ROOT, "Research the thing", 5),
        ):
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, "
                "status_id, title, task_number, created_by) VALUES "
                "(CAST(:t AS uuid), CAST(:p AS uuid), CAST(:r AS uuid), "
                "CAST(:s AS uuid), :ti, :n, :who)"),
                {"t": tid, "p": pid, "r": LONER if pid == LONER else ROOT,
                 "s": TODO, "ti": title, "n": num, "who": OWNER})

        # Open and five years stale — the close sweep's candidate.
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "title, task_number, created_by, updated_at) VALUES "
            "(CAST(:t AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
            "CAST(:s AS uuid), 'Stale and open', 4, :who, "
            "now() - interval '5 years')"),
            {"t": T_STALE, "p": ROOT, "s": TODO, "who": OWNER})

        # The counter must agree with the numbers seeded above, or the first
        # `next_task_number` hands out 1 again and the (root, number) UNIQUE
        # fires — which is the recurrence path, not the code under test.
        for pid, last in ((ROOT, 9), (LONER, 3)):
            await db.execute(text(
                "INSERT INTO pm_task_counters (project_id, last_value, "
                "organization_id) VALUES (CAST(:p AS uuid), :n, CAST(:o AS uuid)) "
                "ON CONFLICT (project_id) DO UPDATE SET last_value = :n"),
                {"p": pid, "n": last, "o": ORG})
        await db.commit()
    finally:
        await db.close()


async def scalar(sql: str, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).scalar()
    finally:
        await db.close()


async def set_status(project_id: str, status: str):
    db = await get_db()
    try:
        await db.execute(text(
            "UPDATE pm_projects SET status = :s WHERE id = CAST(:p AS uuid)"),
            {"s": status, "p": project_id})
        await db.commit()
    finally:
        await db.close()


async def task_fingerprint() -> list:
    """Every task's mutable state, for the D-PM-26 no-write fence."""
    db = await get_db()
    try:
        return list((await db.execute(text(
            "SELECT id, status_id, updated_at, archived_at, completed_at "
            "FROM pm_tasks ORDER BY id"))).fetchall())
    finally:
        await db.close()


async def visible_task_ids(include_archived: bool = False) -> set:
    result = await tasks_mod.list_tasks(
        user=user(), page=Page(page=1, page_size=100),
        include_archived=include_archived,
    )
    return {str(r["id"]) for r in result.rows}


# ── The checks ──────────────────────────────────────────────────────────────

async def archive_checks():
    before = await task_fingerprint()
    check("all four tasks are visible before archiving",
          await visible_task_ids() >= {T_ROOT, T_GRAND, T_LONER}, True)

    res = await tree_mod.archive_node(ROOT, user=user())
    # ROOT + CHILD + GRAND — the recursive walk, two levels deep.
    check("archiving a root stamps its whole subtree", res.projects, 3)
    check("it reports the open tasks it did NOT touch", res.open_tasks >= 3, True)

    check("the subtree is filed", await scalar(
        "SELECT count(*) FROM pm_projects WHERE archived_at IS NOT NULL"), 3)
    check("every filed row names ROOT as its origin", await scalar(
        "SELECT count(*) FROM pm_projects WHERE archived_root_id = CAST(:p AS uuid)",
        p=ROOT), 3)
    check("the unrelated root is untouched", await scalar(
        "SELECT archived_at FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=LONER), None)

    # The whole point of D-PM-26. Compared as a boolean rather than by printing
    # both sides: the rows are the assertion, not the report, and dumping five
    # of them twice buries every other line of output.
    check("NOT ONE pm_tasks row was written",
          await task_fingerprint() == before, True)

    # And the read path honours it without a task write.
    visible = await visible_task_ids()
    check("a task in an archived project leaves the default read",
          T_ROOT in visible or T_GRAND in visible, False)
    check("the task in the untouched project stays", T_LONER in visible, True)
    check("include_archived brings them back",
          {T_ROOT, T_GRAND} <= await visible_task_ids(include_archived=True), True)

    # Idempotence: a double-clicked button is not an error.
    again = await tree_mod.archive_node(ROOT, user=user())
    check("archiving again stamps nothing", again.projects, 0)


async def unarchive_checks():
    # GRAND was swept in by ROOT's archive; restoring it alone is refused.
    try:
        await tree_mod.unarchive_node(GRAND, user=user())
        check("unarchiving a swept-in child is refused", "no refusal", "422")
    except Exception as exc:  # HTTPException
        check("unarchiving a swept-in child is refused",
              getattr(exc, "status_code", None), 422)

    res = await tree_mod.unarchive_node(ROOT, user=user())
    check("unarchiving the origin restores its whole subtree", res.projects, 3)
    check("nothing is left filed", await scalar(
        "SELECT count(*) FROM pm_projects WHERE archived_at IS NOT NULL"), 0)
    check("the tasks are visible again",
          {T_ROOT, T_GRAND} <= await visible_task_ids(), True)


async def reversibility_check():
    """The property `archived_root_id` exists for, and the reason it is a column.

    Archive the CHILD on its own, THEN archive ROOT, then unarchive ROOT. The
    child was somebody's separate decision and must survive — a naive
    "clear the subtree" unarchive silently un-files it, which is the quiet data
    loss that surfaces months later as "who un-archived this?".
    """
    await tree_mod.archive_node(CHILD, user=user())
    check("the child's own archive names itself", await scalar(
        "SELECT archived_root_id = id FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=CHILD), True)

    res = await tree_mod.archive_node(ROOT, user=user())
    # CHILD and GRAND are already filed, so only ROOT is newly stamped.
    check("archiving the parent stamps only what was not already filed",
          res.projects, 1)
    check("the child KEEPS its own origin", await scalar(
        "SELECT archived_root_id = id FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=CHILD), True)

    await tree_mod.unarchive_node(ROOT, user=user())
    check("the parent's restore lifts the parent", await scalar(
        "SELECT archived_at FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=ROOT), None)
    check("...and the independently-archived child SURVIVES it", await scalar(
        "SELECT archived_at IS NOT NULL FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=CHILD), True)
    check("...as does everything the child's own archive filed", await scalar(
        "SELECT archived_at IS NOT NULL FROM pm_projects WHERE id = CAST(:p AS uuid)",
        p=GRAND), True)

    await tree_mod.unarchive_node(CHILD, user=user())
    check("restoring the child clears the rest", await scalar(
        "SELECT count(*) FROM pm_projects WHERE archived_at IS NOT NULL"), 0)


async def sweep_checks():
    """Run twice. A guard that refuses everything passes a one-sided test."""
    async def closed() -> bool:
        return bool(await scalar(
            "SELECT s.category = 'done' FROM pm_tasks t "
            "JOIN pm_task_statuses s ON s.id = t.status_id "
            "WHERE t.id = CAST(:t AS uuid)", t=T_STALE))

    await set_status(ROOT, "on_hold")
    db = await get_db()
    try:
        await run_lifecycle_sweep(db, organization_id=ORG, actor="system:test")
        await db.commit()
    finally:
        await db.close()
    check("a PAUSED project's stale open task is NOT auto-closed",
          await closed(), False)

    await set_status(ROOT, "active")
    db = await get_db()
    try:
        await run_lifecycle_sweep(db, organization_id=ORG, actor="system:test")
        await db.commit()
    finally:
        await db.close()
    check("...and the SAME task IS closed once the project is active",
          await closed(), True)


async def recurrence_checks():
    """Same two-sided shape, and the stamp must NOT be set on the paused path."""
    db = await get_db()
    try:
        rule = (await db.execute(text(
            "INSERT INTO pm_recurrences (project_id, freq, interval, anchor, "
            "day_of_month, created_by) VALUES (CAST(:p AS uuid), 'monthly', 1, "
            "'due', 1, :who) RETURNING id"),
            {"p": ROOT, "who": OWNER})).fetchone()
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "title, task_number, created_by, due_at, recurrence_id) VALUES "
            "(CAST(:t AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
            "CAST(:s AS uuid), 'Monthly report', 9, :who, now(), :r)"),
            {"t": T_REPEAT, "p": ROOT, "s": DONE, "who": OWNER, "r": rule.id})
        await db.commit()
    finally:
        await db.close()

    async def task() -> object:
        db = await get_db()
        try:
            return (await db.execute(text(
                "SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                {"t": T_REPEAT})).fetchone()
        finally:
            await db.close()

    await set_status(ROOT, "on_hold")
    db = await get_db()
    try:
        spawned = await rec_mod.spawn_successor(db, await task(), actor_id=OWNER)
        await db.commit()
    finally:
        await db.close()
    check("a paused project spawns no successor", spawned, None)
    check("...and the series is NOT stamped dead by the pause", await scalar(
        "SELECT recurrence_spawned_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
        t=T_REPEAT), None)

    await set_status(ROOT, "active")
    db = await get_db()
    try:
        spawned = await rec_mod.spawn_successor(db, await task(), actor_id=OWNER)
        await db.commit()
    finally:
        await db.close()
    check("...and the same series resumes once the project is active",
          spawned is not None, True)


async def dispatch_checks():
    calls: list[str] = []

    async def fake_run(agent, message, task_id, organization_id):
        calls.append(agent)

    original = agent_dispatch._run_and_record
    agent_dispatch._run_and_record = fake_run
    try:
        payload = {
            "task_id": T_AGENT, "assignees": ["agent:researcher"],
            "organization_id": ORG,
        }
        await set_status(ROOT, "on_hold")
        await agent_dispatch.on_event("projects", "pm.task.assigned", payload)
        # The sink starts each run in the background (2026-10-04).
        await agent_dispatch.wait_for_runs()
        check("no agent is dispatched into a paused project", calls, [])

        await set_status(ROOT, "active")
        await agent_dispatch.on_event("projects", "pm.task.assigned", payload)
        await agent_dispatch.wait_for_runs()
        check("...and the same assignment dispatches once it is active",
              calls, ["researcher"])
    finally:
        agent_dispatch._run_and_record = original


async def ancestor_checks():
    """🔴 The slice-1 defect: the three guards disagreed about WHICH project.

    Measured 2026-08-13 on this database: the lifecycle sweep read the ROOT,
    while recurrence and dispatch read the task's IMMEDIATE project. A task in
    an ACTIVE subproject beneath a PAUSED root was skipped by the sweep and
    still spawned successors and still dispatched agents — while the tree drew
    that subproject as "Paused — inherited from a parent project". The product
    said paused and the automation ran.

    Both directions are checked, because the two halves failed in opposite
    ways: one guard read too HIGH (the root), two read too LOW (the immediate
    project), and the subtree in between was governed by nobody.
    """
    # ── Direction 1: paused ROOT, active descendant ─────────────────────────
    #
    # ⚠️ The fixtures MUST be moved into GRAND first. On the first version of
    # this check they sat directly in ROOT, so pausing ROOT paused their own
    # immediate project and the ancestor walk was never exercised at all — the
    # checks passed with the recursion deleted. A mutation found it; nothing
    # else would have. A fence that cannot fail is worse than no fence,
    # because it reads as coverage.
    db = await get_db()
    try:
        await db.execute(text(
            "UPDATE pm_tasks SET project_id = CAST(:g AS uuid) "
            "WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"g": GRAND, "ids": [T_REPEAT, T_AGENT]})
        # The series must be re-armed: `sweep_checks`/`recurrence_checks` left
        # it stamped, and a stamped task returns None for a reason that has
        # nothing to do with the project chain.
        await db.execute(text(
            "UPDATE pm_tasks SET recurrence_spawned_at = NULL "
            "WHERE id = CAST(:t AS uuid)"), {"t": T_REPEAT})
        await db.commit()
    finally:
        await db.close()

    await set_status(ROOT, "on_hold")
    await set_status(CHILD, "active")
    await set_status(GRAND, "active")

    db = await get_db()
    try:
        spawned = await rec_mod.spawn_successor(
            db, (await db.execute(text(
                "SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                {"t": T_REPEAT})).fetchone(), actor_id=OWNER)
        await db.commit()
    finally:
        await db.close()
    check("a task under a PAUSED ROOT spawns nothing, even in an active child",
          spawned, None)

    calls: list[str] = []

    async def fake_run(agent, message, task_id, organization_id):
        calls.append(agent)

    original = agent_dispatch._run_and_record
    agent_dispatch._run_and_record = fake_run
    try:
        await agent_dispatch.on_event("projects", "pm.task.assigned", {
            "task_id": T_AGENT, "assignees": ["agent:researcher"],
            "organization_id": ORG,
        })
        await agent_dispatch.wait_for_runs()
        check("...and dispatches no agent either", calls, [])
    finally:
        agent_dispatch._run_and_record = original

    # ── Direction 2: active ROOT, paused SUBPROJECT ─────────────────────────
    # The sweep iterates roots and acts on `root_project_id`, i.e. the whole
    # subtree — so before the chain predicate it closed a paused subproject's
    # stale work anyway.
    await set_status(ROOT, "active")
    await set_status(GRAND, "on_hold")

    db = await get_db()
    try:
        # ⚠️ Reset it to OPEN first. `sweep_checks` already closed this task,
        # so without this the assertion below reads a five-minute-old closure
        # as proof the new predicate failed — which is exactly what it did on
        # the first run. A fixture reused across checks must be returned to a
        # known state, or the second check measures the first one's leftovers.
        await db.execute(text(
            "UPDATE pm_tasks SET project_id = CAST(:g AS uuid), "
            "status_id = CAST(:todo AS uuid), completed_at = NULL, "
            "updated_at = now() - interval '5 years' "
            "WHERE id = CAST(:t AS uuid)"),
            {"g": GRAND, "t": T_STALE, "todo": TODO})
        await db.commit()
        await run_lifecycle_sweep(db, organization_id=ORG, actor="system:test")
        await db.commit()
    finally:
        await db.close()

    closed = bool(await scalar(
        "SELECT s.category = 'done' FROM pm_tasks t "
        "JOIN pm_task_statuses s ON s.id = t.status_id "
        "WHERE t.id = CAST(:t AS uuid)", t=T_STALE))
    check("a stale task in a PAUSED SUBPROJECT is not swept by an active root",
          closed, False)

    # And the control: move it back under the active root and it IS swept.
    await set_status(GRAND, "active")
    db = await get_db()
    try:
        await run_lifecycle_sweep(db, organization_id=ORG, actor="system:test")
        await db.commit()
    finally:
        await db.close()
    check("...and the SAME task IS swept once its own project is running",
          bool(await scalar(
              "SELECT s.category = 'done' FROM pm_tasks t "
              "JOIN pm_task_statuses s ON s.id = t.status_id "
              "WHERE t.id = CAST(:t AS uuid)", t=T_STALE)), True)


async def plan_check():
    """R8 — what the planner does with the new EXISTS on the default read."""
    db = await get_db()
    try:
        plan = "\n".join(r[0] for r in (await db.execute(text(
            "EXPLAIN SELECT t.id FROM pm_tasks t WHERE t.archived_at IS NULL "
            "AND EXISTS (SELECT 1 FROM pm_projects p WHERE p.id = t.project_id "
            "            AND p.archived_at IS NULL)"))).fetchall())
        print("--- plan for the archived-project exclusion ---")
        print(plan)
        # A semi-join, not a correlated per-row subplan. On this row count the
        # shape is what is being asserted, never a duration: a timing taken on
        # a handful of rows is not a measurement (WS-27be's lesson).
        check("the exclusion plans as a JOIN, not a per-row SubPlan",
              "SubPlan" in plan, False)
    finally:
        await db.close()


async def main():
    await seed()
    # The endpoints below are called OUTSIDE a request, so nothing has bound
    # a tenant for them. Binding explicitly is the documented way in
    # (MT-1c) and is what a scheduled job does; inheriting an ambient one is
    # exactly what `tenant_session` refuses.
    bind_tenant(ORG)
    await archive_checks()
    await unarchive_checks()
    await reversibility_check()
    await sweep_checks()
    await recurrence_checks()
    await dispatch_checks()
    await ancestor_checks()
    await plan_check()

    print()
    if failures:
        print(f"FAILED {len(failures)}: {failures}")
        return 1
    print("all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
