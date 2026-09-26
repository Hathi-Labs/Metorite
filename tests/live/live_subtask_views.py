"""Subtasks S1 (D-PM-38), against a REAL Postgres.

Three claims only a database can answer, because each one is decided in SQL
and a hermetic fake agrees with whatever SQL it is handed (R8):

* **(a) A hidden parent's title never leaves the server.** The parent context
  is filtered by `task_visibility_clause` in the WHERE. This drives the list,
  the search, the Projects calendar and three My Tasks reads as a member who
  holds no grant on the parent's project, and searches each JSON body for the
  title.
* **(b) The card chip equals the panel's progress.** A parent with children in
  a granted project, in an ungranted one, and archived. `attach_relation_counts`
  and `relations._SUBTASKS_SQL` must give one answer, and so must My Tasks'
  `subtask_count` and `subtask_done`.
* **(c) A delete at depth 2 lifts the subtask to its GRANDPARENT**, through the
  single route and the bulk action. The bulk case also deletes a grandparent
  and its child in ONE selection, which only a real FK can judge.

Running it, on a FRESH database (01_schema.sql, then apply_migrations.sh)::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_r8" \\
      uv run python tests/live/live_subtask_views.py

It writes only rows it marks, and deletes them at the end.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import analytics as pm_analytics  # noqa: E402
from gateway.routes.projects import bulk as pm_bulk  # noqa: E402
from gateway.routes.projects import calendar as pm_calendar  # noqa: E402
from gateway.routes.projects import personal as pm_personal  # noqa: E402
from gateway.routes.projects import relations as pm_relations  # noqa: E402
from gateway.routes.projects import search as pm_search  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from gateway.routes.projects.core import (  # noqa: E402
    Page,
    _tenant_session,
    resolve_visibility,
)
from sqlalchemy import text  # noqa: E402

OWNER = "owner.s1@fracktal.in"
MEMBER = "member.s1@fracktal.in"
MARK = "__live_subtasks__"
SECRET = "Acquire Initech quietly"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


def owner() -> UserContext:
    return UserContext(
        email=OWNER, role=UserRole.EMPLOYEE, access=build_access(["*"]),
    )


def member() -> UserContext:
    # NOT `*`: that includes `data:org:read`, which sees every project and
    # would make every visibility check here pass without the fix.
    return UserContext(
        email=MEMBER, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


def wire(value) -> str:
    """What the route would serialise, as one string to search."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, default=str)


async def one(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchone()
    finally:
        await db.close()


async def seed() -> dict:
    db = await get_db()
    try:
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)
        for who in (OWNER, MEMBER):
            await db.execute(
                text("DELETE FROM app_user WHERE email = :e"), {"e": who},
            )
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, display_name) "
                "VALUES (:e, CAST(:o AS uuid), :n)"),
                {"e": who, "o": org_id, "n": f"{MARK} {who}"})

        made: dict = {"org": org_id, "projects": {}, "tasks": {}}
        # `open` is granted to the member, `secret` to the owner only.
        for key, grantee in (("open", MEMBER), ("secret", OWNER)):
            pid = str(uuid.uuid4())
            todo, done = str(uuid.uuid4()), str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, source, "
                "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), :n, 'manual', :me, true)"),
                {"id": pid, "o": org_id, "n": f"{MARK} {key}", "me": OWNER})
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :by)"),
                {"p": pid, "s": grantee, "by": OWNER})
            for sid, name, cat, dflt, pos in (
                (todo, "To do", "todo", True, 1),
                (done, "Done", "done", False, 2),
            ):
                await db.execute(text(
                    "INSERT INTO pm_task_statuses (id, project_id, name, "
                    "position, category, is_default) VALUES (CAST(:id AS uuid), "
                    "CAST(:p AS uuid), :n, :pos, :c, :d)"),
                    {"id": sid, "p": pid, "n": name, "pos": pos, "c": cat,
                     "d": dflt})
            made["projects"][key] = {"id": pid, "todo": todo, "done": done}

        numbers = {"n": 0}
        soon = datetime.now(timezone.utc) + timedelta(days=2)

        async def task(key, project, *, parent=None, done=False,
                       archived=False, assign=False, title=None):
            numbers["n"] += 1
            tid = str(uuid.uuid4())
            p = made["projects"][project]
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number, parent_task_id, due_at, archived_at) VALUES "
                "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), "
                "CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', :me, :n, "
                "CAST(:parent AS uuid), :due, "
                "CASE WHEN :arch THEN now() ELSE NULL END)"),
                {"id": tid, "o": org_id, "p": p["id"],
                 "s": p["done"] if done else p["todo"],
                 "t": title or f"{MARK} {key}", "me": OWNER,
                 "n": numbers["n"], "parent": made["tasks"].get(parent),
                 "due": soon, "arch": archived})
            if assign:
                await db.execute(text(
                    "INSERT INTO pm_task_assignees (task_id, organization_id, "
                    "assignee, assigned_by) VALUES (CAST(:t AS uuid), "
                    "CAST(:o AS uuid), :w, :me)"),
                    {"t": tid, "o": org_id, "w": MEMBER, "me": OWNER})
                # A block on the member's calendar, so `/my/calendar` has it.
                await db.execute(text(
                    "INSERT INTO pm_task_personal (task_id, member_email, "
                    "organization_id, scheduled_start, scheduled_end) VALUES "
                    "(CAST(:t AS uuid), :w, CAST(:o AS uuid), :s, :e)"),
                    {"t": tid, "w": MEMBER, "o": org_id, "s": soon,
                     "e": soon + timedelta(hours=1)})
            made["tasks"][key] = tid
            return tid

        # (a) A parent the member cannot see, and its child they can.
        await task("secret_parent", "secret", title=SECRET)
        await task("orphan_child", "open", parent="secret_parent", assign=True,
                   title=f"{MARK} zebra child")
        # A control: a parent the member CAN see, so the check is not
        # passing because no parent ever comes back.
        await task("seen_parent", "open")
        await task("seen_child", "open", parent="seen_parent", assign=True,
                   title=f"{MARK} zebra seen child")

        # The visible parent blocks the child, so the child is on the
        # analytics "blocked" list as well.
        await db.execute(text(
            "INSERT INTO pm_task_links (source_task_id, target_task_id, "
            "link_type, organization_id, created_by) VALUES "
            "(CAST(:s AS uuid), CAST(:d AS uuid), 'blocks', CAST(:o AS uuid), "
            ":me)"),
            {"s": made["tasks"]["seen_parent"],
             "d": made["tasks"]["orphan_child"], "o": org_id, "me": OWNER})

        # (b) One parent, four children of mixed visibility.
        await task("mixed", "open", assign=True)
        await task("k_open", "open", parent="mixed")
        await task("k_done", "open", parent="mixed", done=True)
        await task("k_secret", "secret", parent="mixed")
        await task("k_archived", "open", parent="mixed", archived=True)

        # (c) Three generations, three times.
        for tag in ("single", "bulk", "pair"):
            await task(f"g_{tag}", "open")
            await task(f"p_{tag}", "open", parent=f"g_{tag}")
            await task(f"c_{tag}", "open", parent=f"p_{tag}")

        await db.commit()
        return made
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        for key in made["projects"]:
            pid = made["projects"][key]["id"]
            await db.execute(text(
                "DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                {"p": pid})
            await db.execute(text(
                "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": pid})
        for who in (OWNER, MEMBER):
            await db.execute(
                text("DELETE FROM app_user WHERE email = :e"), {"e": who},
            )
        await db.commit()
    finally:
        await db.close()


async def main():  # noqa: C901
    made = await seed()
    bind_tenant(made["org"])
    t = made["tasks"]
    page = Page(page=1, page_size=200)
    try:
        # ── (a) the hidden parent's title ───────────────────────────────────
        listed = await pm_tasks.list_tasks(user=member(), page=page)
        rows = {r["id"]: r for r in listed.rows}
        check("list: the child of a hidden parent is listed",
              t["orphan_child"] in rows, True)
        check("list: its parent is {hidden: true}",
              rows.get(t["orphan_child"], {}).get("parent"), {"hidden": True})
        check("list: the hidden title is nowhere in the JSON",
              SECRET in wire(listed), False)
        check("list: a visible parent is named (the control)",
              (rows.get(t["seen_child"], {}).get("parent") or {}).get("title"),
              f"{MARK} seen_parent")

        found = await pm_search.search_tasks(
            q="zebra", limit=50, include_triage=False, user=member(),
        )
        hits = {h["id"]: h for h in found["rows"]}
        check("search: the child is found", t["orphan_child"] in hits, True)
        check("search: its parent is hidden",
              hits.get(t["orphan_child"], {}).get("parent"), {"hidden": True})
        check("search: the hidden title is nowhere in the JSON",
              SECRET in wire(found), False)
        check("search: the visible parent is named",
              (hits.get(t["seen_child"], {}).get("parent") or {}).get("ref")
              is not None, True)

        today = datetime.now(timezone.utc).date()
        cal = await pm_calendar.get_calendar(
            user=member(),
            date_from=today.isoformat(),
            date_to=(today + timedelta(days=7)).isoformat(),
        )
        cal_rows = {r["id"]: r for r in cal["rows"]}
        check("calendar: the child is in the window",
              t["orphan_child"] in cal_rows, True)
        check("calendar: its parent is hidden",
              cal_rows.get(t["orphan_child"], {}).get("parent"),
              {"hidden": True})
        check("calendar: the hidden title is nowhere in the JSON",
              SECRET in wire(cal), False)

        inbox = await pm_personal.my_inbox(user=member(), page=page)
        mine = {r["id"]: r for r in inbox.rows}
        check("my inbox: the child is mine", t["orphan_child"] in mine, True)
        check("my inbox: its parent is hidden",
              mine.get(t["orphan_child"], {}).get("parent"), {"hidden": True})
        check("my inbox: the hidden title is nowhere in the JSON",
              SECRET in wire(inbox), False)
        check("my inbox: the visible parent is named",
              (mine.get(t["seen_child"], {}).get("parent") or {}).get("title"),
              f"{MARK} seen_parent")

        week = await pm_personal.my_calendar(
            start=(datetime.now(timezone.utc)).isoformat(),
            end=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            user=member(),
        )
        week_rows = {r["id"]: r for r in week.rows}
        check("my calendar: the child is on it",
              t["orphan_child"] in week_rows, True)
        check("my calendar: the hidden title is nowhere in the JSON",
              SECRET in wire(week), False)

        single = await pm_personal.my_task(t["orphan_child"], user=member())
        check("my task: its parent is hidden",
              single.get("parent"), {"hidden": True})
        check("my task: the hidden title is nowhere in the JSON",
              SECRET in wire(single), False)

        stuck = await pm_analytics.stuck(
            project_id=made["projects"]["open"]["id"], include_subtree=True,
            user=member(),
        )
        blocked = {b["id"]: b for b in stuck["blocked"]}
        check("analytics stuck: the child is blocked",
              t["orphan_child"] in blocked, True)
        check("analytics stuck: its parent is hidden",
              blocked.get(t["orphan_child"], {}).get("parent"),
              {"hidden": True})
        check("analytics stuck: the hidden title is nowhere in the JSON",
              SECRET in wire(stuck), False)
        async with _tenant_session() as session:
            vis = await resolve_visibility(session, member())
            hygiene = await pm_analytics.hygiene_body(
                session, vis, project_id=made["projects"]["open"]["id"],
                include_subtree=True,
            )
        named = [r for r in hygiene["rows"] if r["id"] == t["orphan_child"]]
        check("analytics hygiene: the child is named", bool(named), True)
        check("analytics hygiene: its parent is hidden",
              named[0].get("parent") if named else None, {"hidden": True})
        check("analytics hygiene: the hidden title is nowhere in the JSON",
              SECRET in wire(hygiene), False)

        # ── (b) the chip equals the panel ───────────────────────────────────
        chip = rows.get(t["mixed"], {}).get("subtasks")
        panel = (await pm_relations.get_relations(
            t["mixed"], user=member()))["progress"]
        check("chip: a hidden and an archived child are not counted",
              chip, {"done": 1, "total": 2})
        check("chip == panel progress", chip, panel)
        lens = mine.get(t["mixed"], {})
        check("my tasks: subtask_count matches the panel total",
              lens.get("subtask_count"), panel["total"])
        check("my tasks: subtask_done matches the panel done",
              lens.get("subtask_done"), panel["done"])
        as_owner = await pm_tasks.list_tasks(user=owner(), page=page)
        owner_chip = {r["id"]: r for r in as_owner.rows}[t["mixed"]]["subtasks"]
        owner_panel = (await pm_relations.get_relations(
            t["mixed"], user=owner()))["progress"]
        check("owner: sees the secret child too", owner_chip,
              {"done": 1, "total": 3})
        check("owner: chip == panel progress", owner_chip, owner_panel)

        # ── (c) a delete at depth 2 lifts to the grandparent ────────────────
        result = await pm_tasks.delete_task(t["p_single"], user=owner())
        row = await one(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["c_single"])
        check("single delete: the grandchild moved to the grandparent",
              str(row.parent_task_id), t["g_single"])
        check("single delete: the count says one moved",
              result.cascaded["subtasks_promoted"], 1)

        outcome = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["p_bulk"]], action="delete"),
            user=owner(),
        )
        check("bulk delete: applied", outcome["applied"], 1)
        row = await one(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["c_bulk"])
        check("bulk delete: the grandchild moved to the grandparent",
              str(row.parent_task_id), t["g_bulk"])

        # The grandparent FIRST, then its child, in one selection. After the
        # first delete the FK has set the middle task's parent to NULL, so
        # the second must lift its child to NULL, not to a deleted row.
        outcome = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["g_pair"], t["p_pair"]], action="delete"),
            user=owner(),
        )
        check("bulk pair: both deleted, no FK failure",
              (outcome["applied"], outcome["failed"]), (2, []))
        row = await one(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["c_pair"])
        check("bulk pair: the survivor is top-level", row.parent_task_id, None)
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
