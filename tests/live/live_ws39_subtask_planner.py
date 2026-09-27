"""Subtasks S4 (D-PM-38) — the planner, the step door and the AI counts, live.

Four claims only a database can answer, because each one is decided in SQL
and a hermetic fake agrees with whatever SQL it is handed (R8):

* **(a) A scheduled subtask is busy time.** `LENS_SOURCE.busy_window`,
  `scheduled_today`, `carry_forward` and `overdue` each return a subtask of
  mine that holds a block. Before S4 each query dropped it, so the planner
  counted its slot as free and could book over it.
* **(b) The candidate rule.** Every case in
  `tests/fixtures/subtask_planner_parity.json` is seeded as its own tree and
  `LENS_SOURCE.candidates` must return exactly its `candidates`. The rail
  (`shared.test.ts`) reads the same table.
* **(c) A new step under a NEXT parent is NEXT (B9).** Through
  `POST /my/tasks/{id}/subtasks` and through organize: the step lands in the
  parent's open lane, states NEXT, and reads NEXT on `/my/inbox`. A step
  under a parent with no stated NEXT, in the personal Inbox lane, still reads
  SOMEDAY: the inheritance is only for NEXT.
* **(d) The AI counts agree (B13).** `PM_ITEMS.insight_counts` counts a
  subtask of mine, and `open_items` lists it. So does `siblings` and the
  context backfill.

Running it, on a FRESH database (01_schema.sql, then apply_migrations.sh)::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_s4" \\
      uv run python tests/live/live_ws39_subtask_planner.py

It writes only rows it marks, and deletes them at the end.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant
from gateway.db import get_db
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects.core import Page
from gateway.routes.projects.item_lens import PM_ITEMS
from gateway.routes.projects.planning import LENS_SOURCE
from sqlalchemy import text

RUN = uuid.uuid4().hex[:8]
MEMBER = f"member.s4.{RUN}@fracktal.in"
OTHER = f"other.s4.{RUN}@fracktal.in"
MARK = f"__live_s4_{RUN}__"
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures"
     / "subtask_planner_parity.json").read_text(encoding="utf-8"))
failures: list[str] = []

NOW = datetime.now(UTC).replace(microsecond=0)
DAY0 = NOW.replace(hour=0, minute=0, second=0)
DAY1 = DAY0 + timedelta(days=1)


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


def member() -> UserContext:
    return UserContext(
        email=MEMBER, role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects"]),
    )


class Seed:
    """One team project granted to the member, and the rows this run made."""

    def __init__(self, db, org: str):
        self.db = db
        self.org = org
        self.n = 0
        self.project = str(uuid.uuid4())
        self.backlog = str(uuid.uuid4())
        self.todo = str(uuid.uuid4())
        self.done = str(uuid.uuid4())
        self.ids: dict[str, str] = {}

    async def project_and_lanes(self):
        await self.db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :n, 'manual', :me, true)"),
            {"id": self.project, "o": self.org, "n": f"{MARK} team",
             "me": OTHER})
        await self.db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :by)"),
            {"p": self.project, "s": MEMBER, "by": OTHER})
        # A Backlog lane FIRST, so the first-lane rule and the parent's lane
        # give different answers and (c) can tell them apart.
        for sid, name, cat, pos in ((self.backlog, "Backlog", "backlog", 0),
                                    (self.todo, "To do", "todo", 1),
                                    (self.done, "Done", "done", 2)):
            await self.db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, "
                "position, category) VALUES (CAST(:id AS uuid), "
                "CAST(:p AS uuid), :n, :pos, :c)"),
                {"id": sid, "p": self.project, "n": name, "pos": pos, "c": cat})

    async def task(self, key, *, parent=None, mine=True, state="open",
                   start=None, minutes=60, flexible=True):
        self.n += 1
        tid = str(uuid.uuid4())
        await self.db.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, "
            "root_project_id, status_id, title, source, created_by, "
            "task_number, parent_task_id, archived_at) VALUES "
            "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), "
            "CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', :me, :n, "
            "CAST(:parent AS uuid), "
            "CASE WHEN :arch THEN now() ELSE NULL END)"),
            {"id": tid, "o": self.org, "p": self.project,
             "s": self.done if state == "done" else self.todo,
             "t": f"{MARK} {key}", "me": OTHER, "n": self.n,
             "parent": self.ids.get(parent) if parent else None,
             "arch": state == "archived"})
        await self.db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, organization_id, "
            "assignee, assigned_by) VALUES (CAST(:t AS uuid), "
            "CAST(:o AS uuid), :w, :me)"),
            {"t": tid, "o": self.org, "w": MEMBER if mine else OTHER,
             "me": OTHER})
        if start is not None or state == "trash":
            await self.db.execute(text(
                "INSERT INTO pm_task_personal (task_id, member_email, "
                "organization_id, scheduled_start, scheduled_end, flexible, "
                "disposition) VALUES (CAST(:t AS uuid), :w, CAST(:o AS uuid), "
                ":s, :e, :flex, :d)"),
                {"t": tid, "w": MEMBER, "o": self.org, "s": start,
                 "e": start + timedelta(minutes=minutes) if start else None,
                 "flex": flexible,
                 "d": "TRASH" if state == "trash" else None})
        self.ids[key] = tid
        return tid

    async def clear_tasks(self):
        await self.db.execute(text(
            "DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
            {"p": self.project})
        self.ids.clear()


async def org_id(db) -> str:
    org = (await db.execute(text(
        "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    if org is None:
        raise SystemExit("no organization in this database")
    return str(org.id)


async def users(db, org):
    for who in (MEMBER, OTHER):
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:e, CAST(:o AS uuid), :n)"),
            {"e": who, "o": org, "n": f"{MARK} {who}"})


def titles(rows) -> set[str]:
    return {str(r.title).replace(f"{MARK} ", "") for r in rows}


async def check_busy(seed: Seed):
    """(a) A scheduled subtask is busy time, on every planner query."""
    await seed.task("parent")
    at = DAY0 + timedelta(hours=10)
    await seed.task("step_today", parent="parent", start=at)
    await seed.task("step_past", parent="parent",
                    start=DAY0 - timedelta(days=1, hours=-9))
    await seed.db.commit()

    busy = await LENS_SOURCE.busy_window(
        seed.db, MEMBER, at - timedelta(minutes=30), at + timedelta(hours=2))
    check("(a) busy_window holds the scheduled step",
          "step_today" in titles(busy), True)
    today = await LENS_SOURCE.scheduled_today(seed.db, MEMBER, DAY0, DAY1)
    check("(a) scheduled_today holds the step", "step_today" in titles(today),
          True)
    carry = await LENS_SOURCE.carry_forward(seed.db, MEMBER, DAY0)
    check("(a) carry_forward holds yesterday's step",
          "step_past" in titles(carry), True)
    late = await LENS_SOURCE.overdue(seed.db, MEMBER, NOW + timedelta(days=2))
    check("(a) overdue holds a step whose block ended",
          {"step_today", "step_past"} <= titles(late), True)
    await seed.clear_tasks()
    await seed.db.commit()


async def check_candidates(seed: Seed):
    """(b) Every fixture case, as its own tree, on Postgres."""
    later = NOW + timedelta(days=3)
    for case in FIXTURE["cases"]:
        for t in case["tasks"]:
            await seed.task(
                t["key"], parent=t.get("parent"), mine=t.get("mine", True),
                state=t.get("state", "open"),
                start=later if t.get("scheduled") else None)
        await seed.db.commit()
        got = sorted(titles(await LENS_SOURCE.candidates(seed.db, MEMBER)))
        check(f"(b) {case['name']}", got, sorted(case["candidates"]))
        await seed.clear_tasks()
        await seed.db.commit()


async def check_steps(seed: Seed):
    """(c) A new step under a NEXT parent lands in its lane and reads NEXT."""
    parent = await seed.task("next_parent")
    await pm_personal._upsert_personal(
        seed.db, parent, MEMBER, {"disposition": "NEXT"})
    await seed.db.commit()

    made = await pm_personal.add_my_steps(
        parent, pm_personal.StepsIn(titles=["write it", " "]), user=member())
    check("(c) the door made one step", len(made["created"]), 1)
    step = made["created"][0] if made["created"] else None
    row = (await seed.db.execute(text(
        "SELECT t.status_id::text AS sid, t.parent_task_id::text AS pid, "
        "p.disposition FROM pm_tasks t LEFT JOIN pm_task_personal p "
        "ON p.task_id = t.id AND lower(p.member_email) = :who "
        "WHERE t.id = CAST(:id AS uuid)"),
        {"id": step, "who": MEMBER})).fetchone()
    check("(c) the step is under the parent", row.pid if row else None, parent)
    check("(c) the step is in the parent's open lane",
          row.sid if row else None, seed.todo)
    check("(c) the step states NEXT", row.disposition if row else None, "NEXT")
    inbox = await pm_personal.my_inbox(
        user=member(), page=Page(page=1, page_size=200))
    mine = {r["id"]: r for r in inbox.rows}
    check("(c) /my/inbox reads the step as NEXT",
          (mine.get(step) or {}).get("disposition"), "NEXT")
    check("(c) /my/inbox names the step's parent",
          ((mine.get(step) or {}).get("parent") or {}).get("id"), parent)

    # Organize with steps, on a capture in MY tree: the personal root's first
    # lane is Inbox (backlog), and organize states NEXT before the steps.
    capture = await pm_personal.capture(
        pm_personal.CaptureIn(title=f"{MARK} capture"), user=member())
    cap_id = capture["id"]
    await pm_personal.organize_my_task(
        cap_id,
        pm_personal.OrganizeIn(kind="next", next_action="do it",
                               subtasks=["organized step"]),
        user=member())
    kids = (await seed.db.execute(text(
        "SELECT t.id::text AS id, s.category, p.disposition "
        "FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
        "LEFT JOIN pm_task_personal p ON p.task_id = t.id "
        "AND lower(p.member_email) = :who "
        "WHERE t.parent_task_id = CAST(:id AS uuid)"),
        {"id": cap_id, "who": MEMBER})).fetchall()
    check("(c) organize made one step", len(kids), 1)
    check("(c) the organized step states NEXT",
          kids[0].disposition if kids else None, "NEXT")
    inbox = await pm_personal.my_inbox(
        user=member(), page=Page(page=1, page_size=200))
    mine = {r["id"]: r for r in inbox.rows}
    check("(c) the organized step reads NEXT, not SOMEDAY",
          (mine.get(kids[0].id) if kids else {}).get("disposition"), "NEXT")

    # The control: a parent I never stated NEXT on, in the Inbox lane. The
    # step inherits nothing, so it derives SOMEDAY off the backlog lane.
    plain = await pm_personal.capture(
        pm_personal.CaptureIn(title=f"{MARK} plain"), user=member())
    await seed.db.execute(text(
        "UPDATE pm_task_personal SET disposition = NULL "
        "WHERE task_id = CAST(:id AS uuid)"), {"id": plain["id"]})
    await seed.db.commit()
    made = await pm_personal.add_my_steps(
        plain["id"], pm_personal.StepsIn(titles=["plain step"]), user=member())
    inbox = await pm_personal.my_inbox(
        user=member(), page=Page(page=1, page_size=200))
    mine = {r["id"]: r for r in inbox.rows}
    check("(c) control: a step under an unstated parent derives SOMEDAY",
          (mine.get(made["created"][0]) or {}).get("disposition"), "SOMEDAY")


async def check_ai_counts(seed: Seed):
    """(d) The AI seam counts and lists the same subtasks."""
    await seed.task("ai_parent")
    await seed.task("ai_step", parent="ai_parent")
    await seed.db.commit()
    counts = (await PM_ITEMS.insight_counts(seed.db, MEMBER))["counts"]
    listed = await PM_ITEMS.open_items(seed.db, MEMBER, 500)
    nexts = [r for r in listed if r.disposition == "NEXT"]
    check("(d) open_items lists the step", "ai_step" in titles(listed), True)
    check("(d) insight_counts NEXT == open_items NEXT",
          counts.get("NEXT", 0), len(nexts))
    sib = await PM_ITEMS.siblings(
        seed.db, MEMBER, seed.project, seed.ids["ai_parent"], 50)
    check("(d) siblings offers the step", "ai_step" in titles(sib), True)
    bare = await PM_ITEMS.context_less_actionables(seed.db, MEMBER, 50)
    check("(d) the context backfill reaches the step",
          "ai_step" in titles(bare), True)
    await seed.clear_tasks()
    await seed.db.commit()


async def main():
    db = await get_db()
    seed = None
    try:
        org = await org_id(db)
        bind_tenant(org)
        await users(db, org)
        seed = Seed(db, org)
        await seed.project_and_lanes()
        await db.commit()

        await check_busy(seed)
        await check_candidates(seed)
        await check_steps(seed)
        await check_ai_counts(seed)
    finally:
        await db.rollback()
        if seed is not None:
            await seed.clear_tasks()
            await db.execute(text(
                "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                {"p": seed.project})
        # The member's personal tree, made by the capture door.
        await db.execute(text(
            "DELETE FROM pm_tasks WHERE root_project_id IN ("
            "SELECT id FROM pm_projects WHERE lower(personal_owner) = :w)"),
            {"w": MEMBER})
        await db.execute(text(
            "DELETE FROM pm_projects WHERE lower(personal_owner) = :w "
            "AND parent_project_id IS NOT NULL"), {"w": MEMBER})
        await db.execute(text(
            "DELETE FROM pm_projects WHERE lower(personal_owner) = :w"),
            {"w": MEMBER})
        for who in (MEMBER, OTHER):
            await db.execute(
                text("DELETE FROM app_user WHERE email = :e"), {"e": who})
        await db.commit()
        await db.close()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
