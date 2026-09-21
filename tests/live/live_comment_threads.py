"""Threaded comments and the two-list split, against a REAL Postgres.

Owner request, 2026-09-21: separate the comments from the activity, and let a
comment be replied to, one level deep.

What only a database can answer here, and the hermetic suite cannot:

* **The ``kind`` clause is CONCATENATED into a WHERE that already has a
  condition.** ``tests/unit/test_projects_comments.py`` checks the SHAPE of
  each clause — the leading space, the ``AND`` — because a fake agrees with
  whatever string it is handed (R8). Whether the spliced statement parses,
  and whether the two arms actually PARTITION the stream, is a question for
  Postgres.

* **``ON DELETE SET NULL`` is the foreign key, not any Python here.** The
  claim in migration 208 is that deleting a comment leaves other people's
  replies standing. A fake that deletes from a dict agrees with whichever
  behaviour you assumed, in either direction.

* **The tenant on a reply comes from a TRIGGER.** Migration 161's
  ``pm_organization_from_parent`` fills ``organization_id`` from the task, and
  no INSERT site names it. R5 is satisfied by inheritance, which is a claim
  about the database and nothing else.

* **A soft delete is invisible to a foreign key.** That is the whole reason
  208 did not take the cascade, and it is only checkable where both the
  ``deleted_at`` column and the FK exist.

⚠️ This does NOT ``TRUNCATE`` anything — see `live_ws27bj_rename.py` for the
reasoning. It seeds under a marker and removes what it made in a ``finally``.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@localhost:5432/acb_r8" \\
      uv run python tests/live/live_comment_threads.py
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

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import activities as pm_act  # noqa: E402
from gateway.routes.projects import core as pm_core  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "dev@fracktal.in"
MARK = "__live_threads__"
failures: list[str] = []

PAGE = pm_core.Page(page=1, page_size=50)


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    # ⚠️ Windows is the primary dev box and its console is cp1252, so a
    # character outside that set raises UnicodeEncodeError from print() and
    # takes the run down after the writes and before the cleanup.
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


def owner() -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]))


async def one(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchone()
    finally:
        await db.close()


async def seed() -> dict:
    """One project, one lane, one task."""
    db = await get_db()
    try:
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)

        # ⚠️ A DIRECTORY row, not only a grant. `resolve_visibility` reads the
        # organization from `app_user`, so a caller the directory does not
        # know sees nothing — which reads exactly like a broken guard.
        await db.execute(text("DELETE FROM app_user WHERE email = :me"), {"me": ME})
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:me, CAST(:o AS uuid), :n)"),
            {"me": ME, "o": org_id, "n": f"{MARK} runner"})

        pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :n, 'manual', :me, true)"),
            {"id": pid, "o": org_id, "n": f"{MARK} tree", "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category, is_default) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), "
            "'To do', 1, 'todo', true)"), {"id": sid, "p": pid})

        made = {"org": org_id, "project": pid, "status": sid, "tasks": {}}
        for key, number in (("main", 1), ("other", 2)):
            tid = str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, "
                "'manual', :me, :n)"),
                {"id": tid, "o": org_id, "p": pid, "s": sid,
                 "t": f"{MARK} {key}", "me": ME, "n": number})
            made["tasks"][key] = tid
        await db.commit()
        return made
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        for sql in (
            "DELETE FROM pm_activities WHERE task_id IN "
            "(SELECT id FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
            "DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)",
        ):
            await db.execute(text(sql), {"p": made["project"]})
        await db.execute(text(
            "DELETE FROM app_user WHERE email = :me AND display_name = :n"),
            {"me": ME, "n": f"{MARK} runner"})
        await db.commit()
        print(f"cleaned up {MARK} rows")
    finally:
        await db.close()


async def refused(coro, label, want=422):
    """Run something that must be refused, and say which refusal arrived."""
    try:
        await coro
        check(label, "allowed", want)
    except Exception as exc:  # HTTPException
        check(label, getattr(exc, "status_code", None), want)


async def main():  # noqa: C901
    made = await seed()
    bind_tenant(made["org"])
    task, other = made["tasks"]["main"], made["tasks"]["other"]
    try:
        # ── A reply attaches, and the row says so ──────────────────────────
        root = await pm_act.add_comment(
            task, pm_act.CommentIn(body="the question"), user=owner())
        reply = await pm_act.add_comment(
            task, pm_act.CommentIn(body="the answer", parent_id=root["id"]),
            user=owner())
        check("the reply names its parent", reply["parent_id"], root["id"])
        check("a top-level comment names none", root["parent_id"], None)

        # ⚠️ R5 by INHERITANCE. No INSERT site names the tenant; migration
        # 161's trigger fills it from the task. Nothing in Python can show it.
        row = await one(
            "SELECT organization_id FROM pm_activities WHERE id = CAST(:i AS uuid)",
            i=reply["id"])
        check("the reply inherited its task's tenant",
              str(row.organization_id), made["org"])

        # ── One level, and every way past it is refused ────────────────────
        await refused(
            pm_act.add_comment(
                task, pm_act.CommentIn(body="too deep", parent_id=reply["id"]),
                user=owner()),
            "a reply cannot be replied to")
        await refused(
            pm_act.add_comment(
                other, pm_act.CommentIn(body="wrong task", parent_id=root["id"]),
                user=owner()),
            "a reply cannot cross tasks")
        await refused(
            pm_act.add_comment(
                task, pm_act.CommentIn(body="?", parent_id=str(uuid.uuid4())),
                user=owner()),
            "an unknown parent is a 404", want=404)

        # An EVENT is not answerable. Written straight through
        # `record_activity` because no route creates one on demand.
        db = await get_db()
        try:
            evt = await pm_core.record_activity(
                db, activity_type="status_change", created_by=ME,
                task_id=task, body="moved")
            await db.commit()
            evt_id = str(evt.id)
        finally:
            await db.close()
        await refused(
            pm_act.add_comment(
                task, pm_act.CommentIn(body="answering an event", parent_id=evt_id),
                user=owner()),
            "you cannot reply to a system event")

        # ── The two lists, and the claim that they partition ───────────────
        whole = await pm_act.get_timeline(task, user=owner(), page=PAGE)
        conv = await pm_act.get_timeline(
            task, kind="comments", user=owner(), page=PAGE)
        evts = await pm_act.get_timeline(
            task, kind="events", user=owner(), page=PAGE)
        check("comments + events == the whole stream",
              conv["total"] + evts["total"], whole["total"])
        check("every row of the comments list IS a comment",
              {r["type"] for r in conv["rows"]}, {"comment"})
        check("and none of the events list is",
              "comment" in {r["type"] for r in evts["rows"]}, False)
        # ⚠️ `total` drives the roll-up's "Show N older". Counting the table
        # while returning one kind makes that button promise rows that do not
        # exist.
        check("the count is narrowed with the rows",
              evts["total"], len(evts["rows"]))
        await refused(
            pm_act.get_timeline(task, kind="nonsense", user=owner(), page=PAGE),
            "an unknown kind is refused, not silently widened")

        # ── Deleting a comment does not destroy somebody else's reply ──────
        #
        # ⚠️ THE ruling migration 208 argues at length, and the only place it
        # can be shown. A soft delete is invisible to a foreign key, so a
        # CASCADE would not have fired here at all — and if it were made to,
        # one person's tidy-up would silently remove another's words.
        await pm_act.delete_comment(root["id"], user=owner())
        still = await one(
            "SELECT deleted_at, parent_id FROM pm_activities "
            "WHERE id = CAST(:i AS uuid)", i=reply["id"])
        check("the reply survives its parent's delete", still is not None, True)
        check("and still points at it, soft-deleted as it is",
              str(still.parent_id) if still else None, root["id"])
        after = await pm_act.get_timeline(
            task, kind="comments", user=owner(), page=PAGE)
        check("the deleted root is withheld from the list",
              root["id"] in {r["id"] for r in after["rows"]}, False)
        check("the orphaned reply is NOT",
              reply["id"] in {r["id"] for r in after["rows"]}, True)

        # A HARD delete promotes it. This is the foreign key, not the route.
        db = await get_db()
        try:
            await db.execute(text(
                "DELETE FROM pm_activities WHERE id = CAST(:i AS uuid)"),
                {"i": root["id"]})
            await db.commit()
        finally:
            await db.close()
        promoted = await one(
            "SELECT parent_id FROM pm_activities WHERE id = CAST(:i AS uuid)",
            i=reply["id"])
        check("a hard delete promotes the reply rather than killing it",
              promoted is not None, True)
        check("its parent is cleared, not dangling",
              promoted.parent_id if promoted else "gone", None)
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
