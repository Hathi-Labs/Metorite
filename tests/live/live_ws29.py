"""WS-29a + WS-29b against a REAL Postgres. Two organizations, real routes.

What only a database can answer:

* does `organization_id = CAST(:vis_org AS uuid)` bind a Python `str`, and a
  Python `None`, without asyncpg complaining about the inferred type?
* does the recursive CTE still plan with a WHERE on the recursive term?
* does the BEFORE INSERT trigger actually FILL a NULL before NOT NULL is
  checked (constraint order), and REFUSE a mismatched tenant?
* and the whole point: can tenant B reach ANY of tenant A's rows through the
  real endpoint functions?
"""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import core as pm_core  # noqa: E402
from gateway.routes.projects import calendar as pm_calendar  # noqa: E402
from gateway.routes.projects import me as pm_me  # noqa: E402
from gateway.routes.projects import notifications as pm_notes  # noqa: E402
from gateway.routes.projects import personal as pm_personal  # noqa: E402
from gateway.routes.projects import relations as pm_relations  # noqa: E402
from gateway.routes.projects import search as pm_search  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from gateway.routes.projects import tree as pm_tree  # noqa: E402
from sqlalchemy import text  # noqa: E402

ANA = "ana@alpha.example"
BEN = "ben@beta.example"
BOSS = "boss@alpha.example"

A_PROJ = "aaaaaaaa-0000-4000-8000-000000000001"
B_PROJ = "bbbbbbbb-0000-4000-8000-000000000001"
A_UNGRANTED = "aaaaaaaa-0000-4000-8000-000000000002"
A_STATUS = "aaaaaaaa-0000-4000-8000-000000000011"
B_STATUS = "bbbbbbbb-0000-4000-8000-000000000011"
A_TASK = "aaaaaaaa-0000-4000-8000-000000000021"
B_TASK = "bbbbbbbb-0000-4000-8000-000000000021"

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def member(email):
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


def org_reader(email):
    return UserContext(email=email, role=UserRole.EXECUTIVE,
                       access=build_access(["feature:projects", "data:org:read"]))


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        await db.execute(text(
            "DELETE FROM app_user WHERE email IN (:a, :b, :c)"),
            {"a": ANA, "b": BEN, "c": BOSS})
        await db.execute(text(
            "DELETE FROM organization WHERE slug IN ('alpha', 'beta')"))
        orgs = {}
        for slug in ("alpha", "beta"):
            row = (await db.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"), {"s": slug})).fetchone()
            orgs[slug] = str(row.id)
        for email, slug in ((ANA, "alpha"), (BOSS, "alpha"), (BEN, "beta")):
            await db.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": orgs[slug]})

        # Two projects, granted IDENTICALLY (`subject = 'org'`). Anything that
        # tells them apart afterwards can only be the tenant.
        for pid, name, org, grant in (
            (A_PROJ, "Alpha work", "alpha", "org"),
            (B_PROJ, "Beta work", "beta", "org"),
            (A_UNGRANTED, "Alpha secret", "alpha", None),
        ):
            await db.execute(text(
                "INSERT INTO pm_projects (id, name, source, created_by, "
                "organization_id) VALUES (CAST(:p AS uuid), :n, 'manual', :who, "
                "CAST(:o AS uuid))"),
                {"p": pid, "n": name, "who": ANA, "o": orgs[org]})
            if grant:
                # ⚠️ organization_id deliberately NOT supplied — the trigger
                # must derive it from the project.
                await db.execute(text(
                    "INSERT INTO pm_project_grants (project_id, subject, "
                    "created_by) VALUES (CAST(:p AS uuid), :s, :who)"),
                    {"p": pid, "s": grant, "who": ANA})
        for sid, pid in ((A_STATUS, A_PROJ), (B_STATUS, B_PROJ)):
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:s AS uuid), "
                "CAST(:p AS uuid), 'To do', 10, 'todo', true)"),
                {"s": sid, "p": pid})
        for tid, pid, sid, title in (
            (A_TASK, A_PROJ, A_STATUS, "Quarterly margin review"),
            (B_TASK, B_PROJ, B_STATUS, "Quarterly margin secrets"),
        ):
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, "
                "status_id, title, task_number, created_by) VALUES "
                "(CAST(:t AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                "CAST(:s AS uuid), :ti, 1, :who)"),
                {"t": tid, "p": pid, "s": sid, "ti": title, "who": ANA})
        # ⚠️ Leak 3: Beta names Ana as an assignee on THEIR task.
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (CAST(:t AS uuid), :who, :by)"),
            {"t": B_TASK, "who": ANA, "by": BEN})
        await db.commit()
        return orgs
    finally:
        await db.close()


async def trigger_checks(orgs):
    """The database half: FILL, REFUSE, and the root that cannot be invented."""
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT organization_id FROM pm_project_grants g "
            "WHERE g.project_id = CAST(:p AS uuid)"), {"p": A_PROJ})).fetchone()
        check("a grant inherits its project's tenant",
              str(row.organization_id), orgs["alpha"])
        row = (await db.execute(text(
            "SELECT organization_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": A_TASK})).fetchone()
        check("a task inherits its project's tenant",
              str(row.organization_id), orgs["alpha"])
        row = (await db.execute(text(
            "SELECT organization_id FROM pm_task_assignees "
            "WHERE task_id = CAST(:t AS uuid)"), {"t": B_TASK})).fetchone()
        check("an assignee row inherits its task's tenant",
              str(row.organization_id), orgs["beta"])
    finally:
        await db.close()

    # A child claiming the WRONG tenant must be refused.
    db = await get_db()
    try:
        await db.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, position, "
            "category, organization_id) VALUES (CAST(:p AS uuid), 'Sneak', 99, "
            "'todo', CAST(:o AS uuid))"), {"p": A_PROJ, "o": orgs["beta"]})
        await db.commit()
        check("a mismatched child tenant is refused", "accepted", "refused")
    except Exception as exc:
        check("a mismatched child tenant is refused",
              "does not match" in str(exc), True)
    finally:
        await db.close()

    # A ROOT project with no tenant must be refused — nothing can derive it.
    db = await get_db()
    try:
        await db.execute(text(
            "INSERT INTO pm_projects (name, created_by) VALUES ('orphan', :w)"),
            {"w": ANA})
        await db.commit()
        check("a rootless project with no tenant is refused",
              "accepted", "refused")
    except Exception as exc:
        check("a rootless project with no tenant is refused",
              "not-null" in str(exc) or "null value" in str(exc), True)
    finally:
        await db.close()

    # A task whose ROOT lives in another organization — the second attachment.
    db = await get_db()
    try:
        await db.execute(text(
            "INSERT INTO pm_tasks (project_id, root_project_id, status_id, "
            "title, task_number, created_by) VALUES (CAST(:a AS uuid), "
            "CAST(:b AS uuid), CAST(:s AS uuid), 'straddle', 99, :w)"),
            {"a": A_PROJ, "b": B_PROJ, "s": A_STATUS, "w": ANA})
        await db.commit()
        check("a task straddling two organizations is refused",
              "accepted", "refused")
    except Exception as exc:
        check("a task straddling two organizations is refused",
              "does not match" in str(exc), True)
    finally:
        await db.close()


async def main():
    orgs = await seed()
    await trigger_checks(orgs)

    ana, ben, boss = member(ANA), member(BEN), org_reader(BOSS)

    # ── ⚠️ Leak 1: `subject = 'org'` ────────────────────────────────────────
    listed = await pm_tree.list_nodes(user=ana)
    check("ana's portfolio is alpha's only",
          sorted(r["name"] for r in listed["rows"]), ["Alpha work"])
    listed = await pm_tree.list_nodes(user=ben)
    check("ben's portfolio is beta's only",
          sorted(r["name"] for r in listed["rows"]), ["Beta work"])

    try:
        await pm_tree.get_node(B_PROJ, user=ana)
        check("an org grant does not cross the tenant", "visible", 404)
    except HTTPException as exc:
        check("an org grant does not cross the tenant", exc.status_code, 404)

    # ── ⚠️ Leak 2: `data:org:read` ──────────────────────────────────────────
    listed = await pm_tree.list_nodes(user=boss)
    check("data:org:read sees the whole ALPHA portfolio, ungranted included",
          sorted(r["name"] for r in listed["rows"]),
          ["Alpha secret", "Alpha work"])
    try:
        await pm_tree.get_node(B_PROJ, user=boss)
        check("data:org:read stops at the tenant", "visible", 404)
    except HTTPException as exc:
        check("data:org:read stops at the tenant", exc.status_code, 404)

    tasks = await pm_tasks.list_tasks(user=boss, page=pm_core.Page(1, 50))
    check("an org reader's task list stops at the tenant",
          [r["title"] for r in tasks.rows], ["Quarterly margin review"])

    # ── ⚠️ Leak 3: the assignee escape hatch ────────────────────────────────
    try:
        await pm_tasks.get_task(B_TASK, user=ana)
        check("being assigned in another tenant grants nothing", "visible", 404)
    except HTTPException as exc:
        check("being assigned in another tenant grants nothing",
              exc.status_code, 404)
    tasks = await pm_tasks.list_tasks(user=ana, page=pm_core.Page(1, 50))
    check("…and it does not appear in her list",
          [r["title"] for r in tasks.rows], ["Quarterly margin review"])

    # Search — the widest read, for both principals.
    for who, label in ((ana, "member"), (boss, "org reader")):
        hits = await pm_search.search_tasks(q="quarterly", user=who)
        check(f"search stops at the tenant ({label})",
              [r["title"] for r in hits["rows"]], ["Quarterly margin review"])

    # A caller the directory does not know: fails CLOSED via `column = NULL`.
    stranger = member("nobody@nowhere.example")
    vis = None
    db = await get_db()
    try:
        vis = await pm_core.resolve_visibility(db, stranger)
    finally:
        await db.close()
    check("an unknown caller resolves to no tenant", vis.organization_id, None)
    listed = await pm_tree.list_nodes(user=stranger)
    check("…and sees nothing (NULL comparison, not an if)",
          listed["rows"], [])
    ghost = org_reader("ghost@nowhere.example")
    listed = await pm_tree.list_nodes(user=ghost)
    check("…even holding data:org:read", listed["rows"], [])

    # ── Writes ──────────────────────────────────────────────────────────────
    created = await pm_tree.create_node(pm_tree.ProjectIn(name="Ana's new"),
                                        user=ana)
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT organization_id FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": created["id"]})).fetchone()
        check("a created root carries the caller's tenant",
              str(row.organization_id), orgs["alpha"])
        # Everything the route seeded beneath it inherited the tenant.
        for table, column in (
            ("pm_project_grants", "project_id"),
            ("pm_task_statuses", "project_id"),
            ("pm_task_types", "project_id"),
            ("pm_views", "project_id"),
        ):
            wrong = (await db.execute(text(
                f"SELECT count(*) FROM {table} WHERE {column} = CAST(:p AS uuid) "
                f"AND organization_id <> CAST(:o AS uuid)"),
                {"p": created["id"], "o": orgs["alpha"]})).scalar()
            check(f"{table} seeded under it is in the same tenant", int(wrong), 0)
        activities = (await db.execute(text(
            "SELECT count(*) FROM pm_activities WHERE project_id = "
            "CAST(:p AS uuid) AND organization_id = CAST(:o AS uuid)"),
            {"p": created["id"], "o": orgs["alpha"]})).scalar()
        check("the creation activity is in the same tenant", int(activities), 1)
    finally:
        await db.close()

    try:
        await pm_tree.create_node(
            pm_tree.ProjectIn(name="Wedge", parent_project_id=B_PROJ), user=ana)
        check("grafting onto another tenant's project is 404", "created", 404)
    except HTTPException as exc:
        check("grafting onto another tenant's project is 404",
              exc.status_code, 404)

    try:
        await pm_tree.create_node(pm_tree.ProjectIn(name="Orphan"),
                                  user=stranger)
        check("a caller with no organization cannot create", "created", 403)
    except HTTPException as exc:
        check("a caller with no organization cannot create",
              exc.status_code, 403)

    # Quick capture creates a personal ROOT project — the second decision point.
    captured = await pm_personal.capture(
        pm_personal.CaptureIn(title="Think about it"), user=ben)
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT p.organization_id FROM pm_projects p JOIN pm_tasks t "
            "ON t.project_id = p.id WHERE t.id = CAST(:t AS uuid)"),
            {"t": captured["id"]})).fetchone()
        check("a capture's personal project is in the caller's tenant",
              str(row.organization_id), orgs["beta"])
        # ⚠️ Nothing in `pm_*` may be left tenant-less.
        for table in (
            "pm_projects", "pm_project_grants", "pm_task_statuses",
            "pm_task_types", "pm_task_counters", "pm_tasks",
            "pm_task_assignees", "pm_activities", "pm_views",
        ):
            null = (await db.execute(text(
                f"SELECT count(*) FROM {table} WHERE organization_id IS NULL"
            ))).scalar()
            check(f"{table} has no tenant-less row", int(null), 0)
    finally:
        await db.close()

    # ── ⚠️ The two reads with NO grant clause ───────────────────────────────
    mine = await pm_me.assigned_to_me(user=ana, page=pm_core.Page(1, 50))
    check("assigned-to-me does not import beta's task",
          [r["title"] for r in mine.rows], [])
    inbox = await pm_personal.my_inbox(user=ana, page=pm_core.Page(1, 50))
    check("my/inbox does not import beta's task",
          [r["title"] for r in inbox.rows], [])
    # …and ben, who IS in beta, still sees his own captured work.
    inbox = await pm_personal.my_inbox(user=ben, page=pm_core.Page(1, 50))
    check("ben still sees his own capture",
          [r["title"] for r in inbox.rows], ["Think about it"])

    # ── Every other consumer of `vis.params`, driven for the BIND ───────────
    #
    # `vis.params` now always carries `vis_org`. A statement that does not name
    # it, or names it without binding it, is an error only a real driver
    # raises — the fake ignores unknown parameters by construction.
    cal = await pm_calendar.get_calendar(
        user=ana, date_from="2026-01-01", date_to="2026-12-31")
    check("the calendar binds and stops at the tenant",
          [r["title"] for r in cal["rows"]], [])
    cal = await pm_calendar.get_calendar(
        user=boss, date_from="2026-01-01", date_to="2026-12-31")
    check("the calendar binds for data:org:read too",
          [r["title"] for r in cal["rows"]], [])
    notes = await pm_notes.list_notifications(user=ana, page=pm_core.Page(1, 50))
    check("notifications bind", notes["total"], 0)
    rel = await pm_relations.get_relations(A_TASK, user=ana)
    check("relations bind", (rel["subtasks"], rel["links"]), ([], []))
    try:
        await pm_relations.get_relations(B_TASK, user=ana)
        check("relations on another tenant's task is 404", "visible", 404)
    except HTTPException as exc:
        check("relations on another tenant's task is 404", exc.status_code, 404)
    try:
        await pm_relations.get_relations(B_TASK, user=boss)
        check("…and 404 for data:org:read too", "visible", 404)
    except HTTPException as exc:
        check("…and 404 for data:org:read too", exc.status_code, 404)

    print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "all green"))
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
