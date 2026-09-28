"""A new step lands in its parent's lane from every door (D-PM-38, D79), live.

Spec: ``project-docs/specs/project_management_app.md`` §11.42.

The rule is ``core.parent_lane_status``, and its set check is a SQL clause
(``project_id = :home``). A hermetic fake returns whatever row it is handed,
so only a database can judge the clause (R8). Each check below goes through a
real route function on a real Postgres:

* **(a)** ``POST /projects/tasks`` with a parent in "In review" lands the step
  in "In review". This is the Projects panel and the board.
* **(b)** A parent in a closed lane: the step falls back to the first lane.
* **(c)** A stated ``status_id`` is kept.
* **(d)** A parent in ANOTHER project, with its own status set: the step
  falls back to the first lane of its own set.
* **(e)** A step in a subproject that inherits its parent's set takes the
  parent's lane: the set check is on the status OWNER, not on the project.
* **(f)** The chat tool ``add_subtasks``, with its HTTP calls sent to the
  real route functions, lands both steps in "In review", and its receipt
  names the lane.
* **(g)** My Tasks S4 is unchanged: ``POST /my/tasks/{id}/subtasks`` under a
  NEXT parent in "In review" lands the step in "In review" and states NEXT.

Running it, on a FRESH database (01_schema.sql, then apply_migrations.sh)::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_x" \\
      uv run python tests/live/live_subtask_parent_lane.py

It writes only rows it marks, and deletes them at the end.
"""
import asyncio
import os
import sys
import uuid

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))
sys.path.insert(0, "apps/skills/skill-projects")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant
from fastapi.encoders import jsonable_encoder
from gateway.db import get_db
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects.core import TaskIn
from skill_projects import writes as chat_writes
from sqlalchemy import text

RUN = uuid.uuid4().hex[:8]
MEMBER = f"member.lane.{RUN}@fracktal.in"
MARK = f"__live_lane_{RUN}__"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


def member() -> UserContext:
    return UserContext(
        email=MEMBER, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


class Seed:
    def __init__(self, db, org: str):
        self.db = db
        self.org = org
        self.projects: list[str] = []
        self.lanes: dict[str, str] = {}
        self.n = 0

    async def project(self, name, *, parent=None, owns=True):
        pid = str(uuid.uuid4())
        await self.db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses, parent_project_id) VALUES "
            "(CAST(:id AS uuid), CAST(:o AS uuid), :n, 'manual', :me, :own, "
            "CAST(:parent AS uuid))"),
            {"id": pid, "o": self.org, "n": f"{MARK} {name}", "me": MEMBER,
             "own": owns, "parent": parent})
        await self.db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": MEMBER})
        self.projects.append(pid)
        return pid

    async def lane(self, key, project, name, category, pos):
        sid = str(uuid.uuid4())
        await self.db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), :n, :pos, "
            ":c)"),
            {"id": sid, "p": project, "n": name, "pos": pos, "c": category})
        self.lanes[key] = sid
        return sid

    async def task(self, project, root, lane):
        self.n += 1
        tid = str(uuid.uuid4())
        await self.db.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, "
            "root_project_id, status_id, title, source, created_by, "
            "task_number) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
            "CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :t, "
            "'manual', :me, :n)"),
            {"id": tid, "o": self.org, "p": project, "r": root, "s": lane,
             "t": f"{MARK} parent {self.n}", "me": MEMBER, "n": 1000 + self.n})
        await self.db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, organization_id, "
            "assignee, assigned_by) VALUES (CAST(:t AS uuid), "
            "CAST(:o AS uuid), :w, :w)"),
            {"t": tid, "o": self.org, "w": MEMBER})
        return tid


async def lane_of(db, task_id) -> str | None:
    row = (await db.execute(text(
        "SELECT status_id::text AS sid FROM pm_tasks WHERE id = CAST(:i AS uuid)"),
        {"i": task_id})).fetchone()
    return row.sid if row else None


async def create(project, parent, status_id=None) -> str:
    row = await pm_tasks.create_task(
        TaskIn(project_id=project, parent_task_id=parent, status_id=status_id,
               title=f"{MARK} step"),
        user=member())
    return str(row["id"])


def route_the_chat_to_the_gateway():
    """The chat tool's HTTP calls, answered by the real route functions."""

    async def get(path, params=None):
        parts = path.strip("/").split("/")
        if parts[:2] == ["projects", "tasks"] and len(parts) == 3:
            return jsonable_encoder(await pm_tasks.get_task(parts[2], user=member()))
        if parts[:2] == ["projects", "nodes"] and parts[-1] == "statuses":
            return jsonable_encoder(
                await pm_admin.list_statuses(parts[2], user=member()))
        raise AssertionError(f"unexpected GET {path}")

    async def post(path, json=None, params=None):
        assert path == "/projects/tasks", path
        return jsonable_encoder(
            await pm_tasks.create_task(TaskIn(**json), user=member()))

    async def confirm(title, detail, context):
        return True

    chat_writes.get = get
    chat_writes.post = post
    chat_writes._confirm = confirm


async def main():
    db = await get_db()
    seed = None
    try:
        org = str((await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).scalar())
        bind_tenant(org)
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:e, CAST(:o AS uuid), :n)"),
            {"e": MEMBER, "o": org, "n": MARK})
        seed = Seed(db, org)
        a = await seed.project("A")
        # Backlog FIRST, so the first-lane rule and the parent's lane differ.
        await seed.lane("a_backlog", a, "Backlog", "backlog", 0)
        await seed.lane("a_todo", a, "To do", "todo", 1)
        await seed.lane("a_review", a, "In review", "in_progress", 2)
        await seed.lane("a_done", a, "Done", "done", 3)
        b = await seed.project("B")
        await seed.lane("b_backlog", b, "Queue", "backlog", 0)
        await seed.lane("b_doing", b, "Doing", "in_progress", 1)
        await seed.lane("b_done", b, "Shipped", "done", 2)
        sub = await seed.project("A sub", parent=a, owns=False)
        in_review = await seed.task(a, a, seed.lanes["a_review"])
        closed = await seed.task(a, a, seed.lanes["a_done"])
        other = await seed.task(b, b, seed.lanes["b_doing"])
        await db.commit()

        check("(a) Projects panel step under 'In review' lands in 'In review'",
              await lane_of(db, await create(a, in_review)), seed.lanes["a_review"])
        check("(b) a parent in a closed lane falls back to the first lane",
              await lane_of(db, await create(a, closed)), seed.lanes["a_backlog"])
        check("(c) a stated status_id is kept",
              await lane_of(db, await create(a, in_review, seed.lanes["a_todo"])),
              seed.lanes["a_todo"])
        check("(d) a parent in another status set falls back to our first lane",
              await lane_of(db, await create(a, other)), seed.lanes["a_backlog"])
        check("(e) a subproject that inherits the set takes the parent's lane",
              await lane_of(db, await create(sub, in_review)), seed.lanes["a_review"])

        route_the_chat_to_the_gateway()
        out = await chat_writes.add_subtasks(in_review, "chat one\nchat two")
        kids = (await db.execute(text(
            "SELECT status_id::text AS sid FROM pm_tasks "
            "WHERE parent_task_id = CAST(:p AS uuid) AND title LIKE '%chat%'"),
            {"p": in_review})).fetchall()
        check("(f) the chat tool made two steps", len(kids), 2)
        check("(f) both chat steps landed in 'In review'",
              {k.sid for k in kids}, {seed.lanes["a_review"]})
        check("(f) the chat receipt names the lane",
              out.split("\n")[0].startswith("Added to «In review» under #"), True)

        # (g) My Tasks S4: the personal door, under a NEXT parent.
        await pm_personal._upsert_personal(
            db, in_review, MEMBER, {"disposition": "NEXT"})
        await db.commit()
        made = await pm_personal.add_my_steps(
            in_review, pm_personal.StepsIn(titles=["my step"]), user=member())
        step = made["created"][0]
        row = (await db.execute(text(
            "SELECT t.status_id::text AS sid, p.disposition FROM pm_tasks t "
            "LEFT JOIN pm_task_personal p ON p.task_id = t.id "
            "AND lower(p.member_email) = :who WHERE t.id = CAST(:i AS uuid)"),
            {"i": step, "who": MEMBER})).fetchone()
        check("(g) My Tasks step lands in the parent's lane",
              row.sid, seed.lanes["a_review"])
        check("(g) My Tasks step states NEXT under a NEXT parent",
              row.disposition, "NEXT")
    finally:
        await db.rollback()
        if seed is not None:
            for pid in seed.projects:
                await db.execute(text(
                    "DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                    {"p": pid})
            for pid in reversed(seed.projects):
                await db.execute(text(
                    "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                    {"p": pid})
        await db.execute(
            text("DELETE FROM app_user WHERE email = :e"), {"e": MEMBER})
        await db.commit()
        await db.close()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
