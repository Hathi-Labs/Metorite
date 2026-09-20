"""Delete, archive and unarchive a SELECTION, against a REAL Postgres.

Owner request, 2026-09-20: the three lifecycle verbs, on a multi-select and
in the right-click menu, plus a way to see the archive and get a task back.
The single-task routes have existed since WS-27w. Nothing could reach them
from a selection, and nothing in the UI could reach them at all.

What only a database can answer here, and why the hermetic suite cannot:

* **`DELETE FROM pm_tasks` promotes, it does not cascade.** `parent_task_id`
  is ``ON DELETE SET NULL``, so a subtask SURVIVES its parent at the top
  level. That is a property of the foreign key, not of any Python here, and a
  fake that deletes from a dict agrees with whichever behaviour you assumed.
  It is also the half a caller is most likely to get wrong in the other
  direction: somebody deleting a parent to be rid of a subtree.

* **The tombstone is a TRIGGER.** Migration 168 writes it AFTER DELETE,
  deliberately not from the endpoint, so that the `pm_projects` cascade is
  recorded too. Nothing in Python can show that it fired.

* **`archived_only` is a WHERE arm**, and the claim is about what Postgres
  matched: a filed task is IN that read and a live one is OUT, which is the
  inverse of every other read in the product.

* **The archive guard is read from the STATUS row**, one join away from the
  task. The bulk path re-reads it per task, and "did the guard actually
  refuse this one" is a question about a real category value.

⚠️ **This does NOT ``TRUNCATE pm_projects CASCADE``**, unlike most of its
neighbours — see `live_ws27bj_rename.py`, which explains the reasoning. It
seeds under a marker and removes what it made in a ``finally``.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://<user>:<pw>@localhost:5434/acb_tenant" \\
      uv run python tests/live/live_task_lifecycle.py
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
from gateway.routes.projects import bulk as pm_bulk  # noqa: E402
from gateway.routes.projects import core as pm_core  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "dev@fracktal.in"
MARK = "__live_life__"
failures: list[str] = []

#: `Page`'s defaults are FastAPI `Query` objects, which only become integers
#: when FastAPI resolves them. Calling the handler directly has to pass one.
PAGE = pm_core.Page(page=1, page_size=50)


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    # ⚠️ Windows is the primary dev box and its console is cp1252, so a
    # character outside that set raises UnicodeEncodeError from print() and
    # takes the whole run down - after the writes, before the cleanup.
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
    """One project, two lanes (one open, one done), and five tasks."""
    db = await get_db()
    try:
        # The tenant this harness runs as. Any organization will do - it binds
        # the tenant itself and creates its own grant - so it does not depend
        # on the scratch database already holding a project.
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)

        # ⚠️ The caller needs a DIRECTORY row, not just a grant.
        # `resolve_visibility` reads the organization from `app_user`, so a
        # caller the directory does not know sees nothing and every task is
        # reported "not_found" - which reads exactly like a broken guard.
        # (email) is not unique here - one address can sit in several
        # organizations - so this deletes and re-inserts rather than upserting.
        await db.execute(text("DELETE FROM app_user WHERE email = :me"), {"me": ME})
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:me, CAST(:o AS uuid), :n)"),
            {"me": ME, "o": org_id, "n": f"{MARK} runner"})

        pid = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :n, 'manual', :me, true)"),
            {"id": pid, "o": org_id, "n": f"{MARK} tree", "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})

        lanes = {}
        for name, category, pos in (("To do", "todo", 1), ("Done", "done", 2)):
            sid = str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:id AS uuid), "
                "CAST(:p AS uuid), :n, :pos, :c, :d)"),
                {"id": sid, "p": pid, "n": name, "pos": pos, "c": category,
                 "d": category == "todo"})
            lanes[category] = sid

        made = {"org": org_id, "project": pid, "lanes": lanes, "tasks": {}}

        async def task(key, lane, number, parent=None):
            tid = str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number, parent_task_id) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                "CAST(:s AS uuid), :t, 'manual', :me, :n, "
                "CAST(:par AS uuid))"),
                {"id": tid, "o": org_id, "p": pid, "s": lanes[lane],
                 "t": f"{MARK} {key}", "me": ME, "n": number, "par": parent})
            made["tasks"][key] = tid
            return tid

        parent = await task("parent", "done", 1)
        await task("child", "todo", 2, parent=parent)
        await task("closed_a", "done", 3)
        await task("closed_b", "done", 4)
        await task("open_one", "todo", 5)
        await db.commit()
        return made
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        await db.execute(text(
            "DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"),
            {"p": made["project"]})
        await db.execute(text(
            "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
            {"p": made["project"]})
        await db.execute(text(
            "DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
            {"p": made["project"]})
        await db.execute(text(
            "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
            {"p": made["project"]})
        await db.execute(text(
            "DELETE FROM app_user WHERE email = :me AND display_name = :n"),
            {"me": ME, "n": f"{MARK} runner"})
        await db.commit()
        print(f"cleaned up {MARK} rows")
    finally:
        await db.close()


async def main():
    made = await seed()
    bind_tenant(made["org"])
    t = made["tasks"]
    try:
        # -- Any status files, and the row says which --------------------
        #
        # The open task was REFUSED here until 2026-09-21. Owner ruling
        # removed the category guard: archive is a shelf, and many projects
        # have no `cancelled` lane to satisfy the old rule with.
        out = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(
                task_ids=[t["closed_a"], t["open_one"]], action="archive",
            ),
            user=owner(),
        )
        check("both file, whatever their lane", out["applied"], 2)
        check("and nothing is refused on a category", len(out["failed"]), 0)

        filed = await one(
            "SELECT archived_at FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["closed_a"])
        live = await one(
            "SELECT archived_at FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["open_one"])
        check("the closed one carries a timestamp", filed.archived_at is not None, True)
        check("the OPEN one carries one too", live.archived_at is not None, True)

        # The history is the only thing left that can say a task was parked
        # while it was still owed, so it has to.
        note = await one(
            "SELECT body FROM pm_activities WHERE task_id = CAST(:i AS uuid) "
            "  AND type = 'system' ORDER BY created_at DESC LIMIT 1",
            i=t["open_one"])
        check("the open one's row names its lane and says it was open",
              ("To do" in (note.body or "")) and ("still open" in (note.body or "")),
              True)
        shut = await one(
            "SELECT body FROM pm_activities WHERE task_id = CAST(:i AS uuid) "
            "  AND type = 'system' ORDER BY created_at DESC LIMIT 1",
            i=t["closed_a"])
        check("the closed one's row does NOT",
              "still open" in (shut.body or ""), False)

        # Put the open one back, so the reads below describe one filed task.
        await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["open_one"]], action="unarchive"),
            user=owner(),
        )

        # ── Idempotent ────────────────────────────────────────────────────
        again = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["closed_a"]], action="archive"),
            user=owner(),
        )
        check("archiving an archived task is skipped, not failed",
              (again["applied"], len(again["failed"])), (0, 0))
        check("and it is reported as unchanged",
              again["skipped"][0]["reason"], "unchanged")

        # ── The archive, as a place you can go ────────────────────────────
        rows = await pm_tasks.list_tasks(
            project_id=made["project"], include_subtree=True,
            archived_only=True, page=PAGE, user=owner(),
        )
        got = sorted(r["title"] for r in rows.rows)
        check("archived_only shows the filed task", got, [f"{MARK} closed_a"])

        live_rows = await pm_tasks.list_tasks(
            project_id=made["project"], include_subtree=True, page=PAGE,
            user=owner(),
        )
        check("and the default read still hides it",
              f"{MARK} closed_a" in [r["title"] for r in live_rows.rows], False)

        # ── Back again ────────────────────────────────────────────────────
        back = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["closed_a"]], action="unarchive"),
            user=owner(),
        )
        check("it comes back", back["applied"], 1)
        restored = await one(
            "SELECT archived_at FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["closed_a"])
        check("and the timestamp is cleared", restored.archived_at is None, True)

        # ── Delete PROMOTES its children ──────────────────────────────────
        before = await one(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["child"])
        check("the child starts under its parent",
              str(before.parent_task_id), t["parent"])

        gone = await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=[t["parent"]], action="delete"),
            user=owner(),
        )
        check("the parent is deleted", gone["applied"], 1)
        check("the parent row is gone",
              (await one("SELECT count(*) AS n FROM pm_tasks "
                         "WHERE id = CAST(:i AS uuid)", i=t["parent"])).n, 0)
        after = await one(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["child"])
        check("the CHILD SURVIVED (promoted, not cascaded)", after is not None, True)
        check("and was promoted, not destroyed",
              after.parent_task_id if after else "gone", None)

        # ── An action is not an edit ──────────────────────────────────────
        try:
            await pm_bulk.bulk_edit(
                pm_bulk.BulkIn(
                    task_ids=[t["closed_b"]], action="archive", tags_add=["x"],
                ),
                user=owner(),
            )
            check("an action beside an edit is refused", "allowed", "422")
        except Exception as exc:  # HTTPException
            check("an action beside an edit is refused",
                  getattr(exc, "status_code", None), 422)
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
