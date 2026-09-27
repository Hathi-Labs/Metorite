"""Subtasks S5 (D-PM-38 decisions 2 and 4), against a REAL Postgres.

Each claim here is decided in SQL, or in a chain of writes a fake cannot
judge, so it runs on a real database (R8):

* **(a) A cross-project subtree moves whole.** Four levels over two projects
  and two status sets. Each task lands in the destination, each status is
  remapped from ITS OWN lane by name, and no task of the subtree is left
  outside the destination. The single move and the bulk move both do it.
* **(b) A hidden descendant refuses the move.** A member who cannot see one
  child gets a 409, and nothing moves.
* **(c) The complete cascade writes the right Done status per project.** A
  set whose FIRST Done status is "Shipped" gets "Shipped". A closed child
  keeps its date, a hidden child stays open, and a recurring child spawns its
  next instance. Without the flag, every child stays open.
* **(d) Cascade plus Undo restores the exact prior statuses.** Undo is the
  client's rule: read the task, and put the prior status back with If-Match
  only while the task still holds the status the cascade set. A teammate's
  later move is kept, and a stale If-Match answers 412.
* **(e) Archive at depth 3, and its Undo.** Unarchive does not cascade. The
  Undo restores exactly the ids the archive reported.
* **(f) The PATCH door** cascades a move into `done`, and not into
  `cancelled`.
* **(g) A bulk move WITH subtasks shows what THEY cost** (review of #493).
  A subtask's value with no home in the destination is in the preview's
  drops, and the apply refuses it until the member accepts. A field the
  destination requires and a subtask lacks is in `required_missing`, and the
  apply refuses the whole move.

Running it, on a FRESH database (01_schema.sql, then apply_migrations.sh)::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_s5" \\
      uv run python tests/live/live_subtask_lifecycle.py

It writes only rows it marks, and deletes them at the end.
"""
import asyncio
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
from fastapi import HTTPException  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import bulk as pm_bulk  # noqa: E402
from gateway.routes.projects import core as pm_core  # noqa: E402
from gateway.routes.projects import move as pm_move  # noqa: E402
from gateway.routes.projects import personal as pm_personal  # noqa: E402
from gateway.routes.projects import recurrence as pm_recurrence  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from sqlalchemy import text  # noqa: E402

OWNER = "owner.s5@fracktal.in"
MEMBER = "member.s5@fracktal.in"
MARK = "__live_s5__"
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
    return UserContext(email=OWNER, role=UserRole.EMPLOYEE, access=build_access(["*"]))


def member() -> UserContext:
    # NOT `*`: that includes `data:org:read`, which sees every project and
    # would make the hidden-child check pass without the fix.
    return UserContext(
        email=MEMBER, role=UserRole.EMPLOYEE, access=build_access(["feature:projects"]),
    )


async def one(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchone()
    finally:
        await db.close()


async def status_of(tid: str) -> str:
    return str((await one(
        "SELECT status_id FROM pm_tasks WHERE id = CAST(:i AS uuid)", i=tid,
    )).status_id)


#: Each project owns its set. `b` puts "Shipped" ABOVE "Done", so its FIRST
#: Done status is "Shipped" (D79: the first Done status by position).
LANES = {
    "a": [("To do", "todo"), ("Review", "in_progress"), ("Done", "done"),
          ("Dropped", "cancelled")],
    "b": [("To do", "todo"), ("Review", "in_progress"), ("Shipped", "done"),
          ("Done", "done")],
    "c": [("Backlog", "backlog"), ("To do", "todo"), ("Review", "in_progress"),
          ("Done", "done")],
}


async def seed() -> dict:
    db = await get_db()
    try:
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)
        for who in (OWNER, MEMBER):
            await db.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": who})
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, display_name) "
                "VALUES (:e, CAST(:o AS uuid), :n)"),
                {"e": who, "o": org_id, "n": f"{MARK} {who}"})

        made: dict = {"org": org_id, "projects": {}, "tasks": {}}
        # The member is granted `a` and `c`, never `b`.
        for key in ("a", "b", "c"):
            pid = str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, source, "
                "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), :n, 'manual', :me, true)"),
                {"id": pid, "o": org_id, "n": f"{MARK} {key}", "me": OWNER})
            grantees = [OWNER] if key == "b" else [OWNER, MEMBER]
            for who in grantees:
                await db.execute(text(
                    "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                    "VALUES (CAST(:p AS uuid), :s, :by)"),
                    {"p": pid, "s": who, "by": OWNER})
            lanes = {}
            for pos, (name, cat) in enumerate(LANES[key], start=1):
                sid = str(uuid.uuid4())
                await db.execute(text(
                    "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                    "category, is_default) VALUES (CAST(:id AS uuid), "
                    "CAST(:p AS uuid), :n, :pos, :c, false)"),
                    {"id": sid, "p": pid, "n": name, "pos": pos * 10, "c": cat})
                lanes[name] = sid
            made["projects"][key] = {"id": pid, "lanes": lanes}

        numbers = {"n": 0}
        soon = datetime.now(timezone.utc) + timedelta(days=2)

        async def task(key, project, lane="To do", *, parent=None):
            numbers["n"] += 1
            tid = str(uuid.uuid4())
            p = made["projects"][project]
            closed = dict(LANES[project])[lane] in ("done", "cancelled")
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number, parent_task_id, due_at, completed_at) VALUES "
                "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), "
                "CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', :me, :n, "
                "CAST(:parent AS uuid), :due, :done_at)"),
                {"id": tid, "o": org_id, "p": p["id"], "s": p["lanes"][lane],
                 "t": f"{MARK} {key}", "me": OWNER, "n": numbers["n"],
                 "parent": made["tasks"].get(parent), "due": soon,
                 "done_at": datetime(2026, 1, 2, tzinfo=timezone.utc) if closed else None})
            made["tasks"][key] = tid
            return tid

        # (a) + (b) — four levels over two projects.
        await task("m_parent", "a", "Review")
        await task("m_child", "a", "To do", parent="m_parent")
        await task("m_grand", "b", "Review", parent="m_child")
        await task("m_great", "a", "To do", parent="m_grand")
        # (a) the bulk move.
        await task("b_parent", "a", "To do")
        await task("b_child", "b", "Review", parent="b_parent")
        await task("b_grand", "a", "Review", parent="b_child")

        # (c) + (d) — the complete cascade, and its Undo.
        await task("c_parent", "a", "To do")
        await task("c_child", "a", "Review", parent="c_parent")
        await task("c_kid_b", "b", "To do", parent="c_parent")
        await task("c_grand", "b", "Review", parent="c_kid_b")
        await task("c_done", "a", "Done", parent="c_parent")
        await task("c_rec", "a", "To do", parent="c_parent")
        # Without the flag: nothing below the parent moves.
        await task("n_parent", "a", "To do")
        await task("n_child", "a", "To do", parent="n_parent")
        # The member completes a parent with a child they cannot see.
        await task("h_parent", "a", "To do")
        await task("h_seen", "a", "To do", parent="h_parent")
        await task("h_hidden", "b", "To do", parent="h_parent")

        # (e) archive at depth 3.
        await task("a_parent", "a", "To do")
        await task("a_child", "a", "Done", parent="a_parent")
        await task("a_grand", "b", "To do", parent="a_child")
        await task("a_great", "a", "To do", parent="a_grand")

        # (f) the PATCH door.
        await task("p_parent", "a", "To do")
        await task("p_child", "b", "To do", parent="p_parent")
        await task("x_parent", "a", "To do")
        await task("x_child", "b", "To do", parent="x_parent")

        # (g) A field `sev` on `a` that the destination `c` does not have, and
        # a subtask that carries a value in it.
        for key, name in (("sev", "Severity"),):
            await db.execute(text(
                "INSERT INTO pm_custom_fields (project_id, organization_id, "
                "field_key, name, field_type, created_by) VALUES "
                "(CAST(:p AS uuid), CAST(:o AS uuid), :k, :n, 'text', :me)"),
                {"p": made["projects"]["a"]["id"], "o": org_id, "k": key,
                 "n": name, "me": OWNER})
        await task("g_parent", "a", "To do")
        await task("g_child", "a", "To do", parent="g_parent")
        await db.execute(text(
            "UPDATE pm_tasks SET custom_fields = '{\"sev\": \"high\"}'::jsonb "
            "WHERE id = CAST(:i AS uuid)"), {"i": made["tasks"]["g_child"]})
        await task("r_parent", "a", "To do")
        await task("r_child", "a", "To do", parent="r_parent")

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
                "DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"), {"p": pid})
            await db.execute(text(
                "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": pid})
        for who in (OWNER, MEMBER):
            await db.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": who})
        await db.commit()
    finally:
        await db.close()


async def undo_cascade(changes: list[dict]) -> dict[str, str]:
    """The client's Undo, on the server's own doors (`revertCascade`).

    For each task: read it, and put the prior status back with If-Match only
    while it still holds the status the cascade set. Answers each id's
    outcome: `restored`, `moved-since` or `refused`.
    """
    out: dict[str, str] = {}
    for change in changes:
        tid = change["task_id"]
        now = await pm_tasks.get_task(tid, user=owner())
        if str(now["status_id"]) != change["to_status_id"]:
            out[tid] = "moved-since"
            continue
        try:
            await pm_tasks.patch_task(
                tid, pm_core.TaskIn(status_id=change["from_status_id"]),
                user=owner(), if_match=str(now["updated_at"]),
            )
            out[tid] = "restored"
        except HTTPException as exc:
            out[tid] = f"refused {exc.status_code}"
    return out


async def main():  # noqa: C901
    made = await seed()
    bind_tenant(made["org"])
    t = made["tasks"]
    lane = {k: v["lanes"] for k, v in made["projects"].items()}
    pc = made["projects"]["c"]["id"]
    try:
        # ── (b) a hidden descendant refuses the whole move ──────────────────
        try:
            await pm_tasks.move_task(
                t["m_parent"],
                pm_tasks.MoveTask(project_id=pc, include_subtasks=True),
                user=member())
            check("hidden child: the move is refused", "no refusal", 409)
        except HTTPException as exc:
            check("hidden child: the move is refused", exc.status_code, 409)
            check("hidden child: the refusal names the count",
                  str(exc.detail).startswith("1 subtask of this task is hidden"), True)
        row = await one("SELECT project_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
                        i=t["m_parent"])
        check("hidden child: the parent did not move",
              str(row.project_id), made["projects"]["a"]["id"])

        # ── (a) the single move carries the whole subtree ───────────────────
        got = await pm_tasks.move_task(
            t["m_parent"], pm_tasks.MoveTask(project_id=pc, include_subtasks=True),
            user=owner())
        check("single move: subtasks_moved", got["subtasks_moved"], 3)
        subtree = [t["m_parent"], t["m_child"], t["m_grand"], t["m_great"]]
        rows = {}
        for tid in subtree:
            rows[tid] = await one(
                "SELECT project_id, root_project_id, status_id, parent_task_id "
                "FROM pm_tasks WHERE id = CAST(:i AS uuid)", i=tid)
        check("single move: no task of the subtree is left behind (no split)",
              sorted({str(r.project_id) for r in rows.values()}), [pc])
        check("single move: every root is the destination's",
              sorted({str(r.root_project_id) for r in rows.values()}), [pc])
        check("single move: the parent's Review lands in C's Review",
              str(rows[t["m_parent"]].status_id), lane["c"]["Review"])
        check("single move: the child's To do lands in C's To do",
              str(rows[t["m_child"]].status_id), lane["c"]["To do"])
        check("single move: B's Review (another set) lands in C's Review",
              str(rows[t["m_grand"]].status_id), lane["c"]["Review"])
        check("single move: the depth-3 task lands in C's To do",
              str(rows[t["m_great"]].status_id), lane["c"]["To do"])
        check("single move: the tree's links are intact",
              [str(rows[x].parent_task_id) for x in subtree[1:]], subtree[:-1])

        bulk = await pm_move.move_tasks(
            pm_move.MoveIn(task_ids=[t["b_parent"]], destination_project_id=pc,
                           include_subtasks=True),
            user=owner())
        check("bulk move: subtasks_moved", bulk["subtasks_moved"], 2)
        for key, want in (("b_child", "Review"), ("b_grand", "Review")):
            row = await one("SELECT project_id, status_id FROM pm_tasks "
                            "WHERE id = CAST(:i AS uuid)", i=t[key])
            check(f"bulk move: {key} is in C", str(row.project_id), pc)
            check(f"bulk move: {key} is in C's {want}", str(row.status_id),
                  lane["c"][want])

        # ── (c) the complete cascade, a Done status per project ─────────────
        await pm_recurrence.set_recurrence(
            t["c_rec"], pm_recurrence.RecurrenceIn(freq="daily"), user=owner())
        before = {k: await status_of(t[k]) for k in (
            "c_parent", "c_child", "c_kid_b", "c_grand", "c_done", "c_rec")}
        done_at = await one("SELECT completed_at FROM pm_tasks "
                            "WHERE id = CAST(:i AS uuid)", i=t["c_done"])
        got = await pm_personal.complete_task(
            t["c_parent"], user=owner(), include_subtasks=True)
        check("complete: subtasks_completed", got["subtasks_completed"], 4)
        check("complete: the parent is in A's Done",
              await status_of(t["c_parent"]), lane["a"]["Done"])
        check("complete: an A child is in A's Done",
              await status_of(t["c_child"]), lane["a"]["Done"])
        check("complete: a B child is in B's FIRST Done status, Shipped",
              await status_of(t["c_kid_b"]), lane["b"]["Shipped"])
        check("complete: the B grandchild is in Shipped too",
              await status_of(t["c_grand"]), lane["b"]["Shipped"])
        after = await one("SELECT completed_at FROM pm_tasks "
                          "WHERE id = CAST(:i AS uuid)", i=t["c_done"])
        check("complete: a closed child keeps its completion date",
              after.completed_at, done_at.completed_at)
        spawned = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE title = :t AND "
            "completed_at IS NULL", t=f"{MARK} c_rec")
        check("complete: the recurring child spawned its next instance",
              spawned.n, 1)
        rec = [c for c in got["subtask_changes"] if c["task_id"] == t["c_rec"]]
        check("complete: the Undo record names the successor",
              bool(rec and rec[0]["recurred_to"]), True)

        plain = await pm_personal.complete_task(t["n_parent"], user=owner())
        check("complete without the flag: no count", "subtasks_completed" in plain,
              False)
        check("complete without the flag: the child stays open",
              await status_of(t["n_child"]), lane["a"]["To do"])

        seen = await pm_personal.complete_task(
            t["h_parent"], user=member(), include_subtasks=True)
        check("member complete: only the child they can see",
              seen["subtasks_completed"], 1)
        check("member complete: the hidden child stays open",
              await status_of(t["h_hidden"]), lane["b"]["To do"])

        # ── (d) Undo restores the exact prior statuses ──────────────────────
        # A teammate moves the grandchild on, from Shipped to Done, after the
        # cascade. Undo must keep that move.
        await pm_tasks.patch_task(
            t["c_grand"], pm_core.TaskIn(status_id=lane["b"]["Done"]),
            user=owner(), if_match=None)
        parent_change = {"task_id": t["c_parent"],
                         "from_status_id": before["c_parent"],
                         "to_status_id": lane["a"]["Done"]}
        outcome = await undo_cascade([parent_change, *got["subtask_changes"]])
        check("undo: the parent and three children are restored",
              sorted(k for k, v in outcome.items() if v == "restored"),
              sorted([t["c_parent"], t["c_child"], t["c_kid_b"], t["c_rec"]]))
        check("undo: the teammate's later move is kept",
              outcome.get(t["c_grand"]), "moved-since")
        for key in ("c_parent", "c_child", "c_kid_b", "c_rec"):
            check(f"undo: {key} is back in its exact prior status",
                  await status_of(t[key]), before[key])
        check("undo: c_grand stays where the teammate put it",
              await status_of(t["c_grand"]), lane["b"]["Done"])
        check("undo: the closed child was never touched",
              await status_of(t["c_done"]), before["c_done"])
        # A stale If-Match — the row changed since it was read — is a 412.
        stale = await pm_tasks.get_task(t["c_child"], user=owner())
        await pm_tasks.patch_task(
            t["c_child"], pm_core.TaskIn(title=f"{MARK} c_child edited"),
            user=owner(), if_match=None)
        try:
            await pm_tasks.patch_task(
                t["c_child"], pm_core.TaskIn(status_id=lane["a"]["Done"]),
                user=owner(), if_match=str(stale["updated_at"]))
            check("undo: a stale If-Match is refused", "no refusal", 412)
        except HTTPException as exc:
            check("undo: a stale If-Match is refused", exc.status_code, 412)

        # ── (e) archive at depth 3, and its Undo ────────────────────────────
        got = await pm_tasks.archive_task(
            t["a_parent"], user=owner(), include_subtasks=True)
        check("archive: subtasks_archived", got["subtasks_archived"], 3)
        shelved = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE archived_at IS NOT NULL "
            "AND id = ANY(CAST(:ids AS uuid[]))",
            ids=[t[k] for k in ("a_parent", "a_child", "a_grand", "a_great")])
        check("archive: the whole subtree is on the shelf", shelved.n, 4)
        await pm_tasks.unarchive_task(t["a_parent"], user=owner())
        kept = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE archived_at IS NOT NULL "
            "AND id = ANY(CAST(:ids AS uuid[]))", ids=got["subtask_ids"])
        check("unarchive: the parent alone comes back (no cascade)", kept.n, 3)
        for tid in got["subtask_ids"]:
            await pm_tasks.unarchive_task(tid, user=owner())
        back = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE archived_at IS NULL "
            "AND id = ANY(CAST(:ids AS uuid[]))", ids=got["subtask_ids"])
        check("archive undo: the reported ids are restored", back.n, 3)

        # ── (f) the PATCH door ──────────────────────────────────────────────
        got = await pm_tasks.patch_task(
            t["p_parent"], pm_core.TaskIn(status_id=lane["a"]["Done"]),
            user=owner(), if_match=None, include_subtasks=True)
        check("patch into done: subtasks_completed", got["subtasks_completed"], 1)
        check("patch into done: the B child is in Shipped",
              await status_of(t["p_child"]), lane["b"]["Shipped"])
        got = await pm_tasks.patch_task(
            t["x_parent"], pm_core.TaskIn(status_id=lane["a"]["Dropped"]),
            user=owner(), if_match=None, include_subtasks=True)
        check("patch into cancelled: nothing cascades", got["subtasks_completed"], 0)
        check("patch into cancelled: the child stays open",
              await status_of(t["x_child"]), lane["b"]["To do"])

        # ── (g) a bulk move WITH subtasks shows what they cost ─────────────
        plain = await pm_move.preview_move(
            pm_move.MoveIn(task_ids=[t["g_parent"]], destination_project_id=pc),
            user=owner())
        check("preview without subtasks: nothing drops", plain["drops"], {})
        check("preview without subtasks: the box still has its count",
              plain["subtasks"], {"count": 1, "hidden": 0})
        full = await pm_move.preview_move(
            pm_move.MoveIn(task_ids=[t["g_parent"]], destination_project_id=pc,
                           include_subtasks=True),
            user=owner())
        check("preview with subtasks: the subtask's Severity drops",
              [d["task_id"] for d in full["drops"].get("sev", [])], [t["g_child"]])
        try:
            await pm_move.move_tasks(
                pm_move.MoveIn(task_ids=[t["g_parent"]], destination_project_id=pc,
                               include_subtasks=True),
                user=owner())
            check("apply without accept_drops: refused", "no refusal", 422)
        except HTTPException as exc:
            check("apply without accept_drops: refused", exc.status_code, 422)
        row = await one("SELECT project_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
                        i=t["g_child"])
        check("apply without accept_drops: the subtask did not move",
              str(row.project_id), made["projects"]["a"]["id"])
        moved = await pm_move.move_tasks(
            pm_move.MoveIn(task_ids=[t["g_parent"]], destination_project_id=pc,
                           include_subtasks=True, accept_drops=True,
                           accepted_drops=["sev"]),
            user=owner())
        check("apply with the loss accepted: the subtask moved",
              moved["subtasks_moved"], 1)
        trail = await one(
            "SELECT count(*) AS n FROM pm_activities WHERE task_id = CAST(:i AS uuid) "
            "AND meta ? 'dropped_custom_fields'", i=t["g_child"])
        check("apply with the loss accepted: the value is on the timeline",
              trail.n, 1)

        # A field the destination REQUIRES, which the parent carries and the
        # subtask does not. Added last, because `c` is the destination above.
        db = await get_db()
        try:
            await db.execute(text(
                "INSERT INTO pm_custom_fields (project_id, organization_id, "
                "field_key, name, field_type, required, created_by) VALUES "
                "(CAST(:p AS uuid), CAST(:o AS uuid), 'po', 'PO', 'text', true, :me)"),
                {"p": pc, "o": made["org"], "me": OWNER})
            await db.execute(text(
                "INSERT INTO pm_custom_fields (project_id, organization_id, "
                "field_key, name, field_type, created_by) VALUES "
                "(CAST(:p AS uuid), CAST(:o AS uuid), 'po', 'PO', 'text', :me)"),
                {"p": made["projects"]["a"]["id"], "o": made["org"], "me": OWNER})
            await db.execute(text(
                "UPDATE pm_tasks SET custom_fields = '{\"po\": \"PO-7\"}'::jsonb "
                "WHERE id = CAST(:i AS uuid)"), {"i": t["r_parent"]})
            await db.commit()
        finally:
            await db.close()
        need = await pm_move.preview_move(
            pm_move.MoveIn(task_ids=[t["r_parent"]], destination_project_id=pc,
                           include_subtasks=True),
            user=owner())
        check("preview with subtasks: the subtask's missing PO is named",
              need["required_missing"], ["PO"])
        alone = await pm_move.preview_move(
            pm_move.MoveIn(task_ids=[t["r_parent"]], destination_project_id=pc),
            user=owner())
        check("preview without subtasks: the parent alone satisfies PO",
              alone["required_missing"], [])
        try:
            await pm_move.move_tasks(
                pm_move.MoveIn(task_ids=[t["r_parent"]], destination_project_id=pc,
                               include_subtasks=True),
                user=owner())
            check("apply with a subtask missing PO: refused", "no refusal", 422)
        except HTTPException as exc:
            check("apply with a subtask missing PO: refused", exc.status_code, 422)

        # ── the bulk door, once for the batch ───────────────────────────────
        outcome = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["n_parent"]], action="archive",
                           include_subtasks=True),
            user=owner())
        check("bulk archive: the child went too", outcome["subtask_ids"],
              [t["n_child"]])
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
