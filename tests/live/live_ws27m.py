"""WS-27m against a REAL Postgres.

The registry's claims are array operations — `&&`, `@>`, `array_remove`, the
case-insensitive unique index, and a merge that rewrites rows. A fake
re-implements those in Python and can only agree with itself.
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
from gateway.routes.projects import tags as tags_mod  # noqa: E402
from gateway.routes.projects import tasks as tasks_mod  # noqa: E402
from gateway.routes.projects.core import Page, TaskIn  # noqa: E402
from gateway.routes.projects.tags import MergeIn, TagIn  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "priya@fracktal.in"
PID = "11111111-1111-1111-1111-111111111111"
SID = "22222222-2222-2222-2222-222222222222"
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
        await db.execute(text(
            "INSERT INTO pm_projects (id, name, source, created_by) "
            "VALUES (CAST(:p AS uuid), 'Ops', 'manual', :me)"), {"p": PID, "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": PID, "s": ME})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
            "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, "
            "'todo', true)"), {"s": SID, "p": PID})
        ids = []
        for n in range(1, 4):
            tid = str(uuid.uuid4())
            ids.append(tid)
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
                "title, source, created_by, task_number) VALUES (CAST(:id AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', "
                ":me, :n)"), {"id": tid, "p": PID, "s": SID, "t": f"task {n}",
                              "me": ME, "n": n})
        await db.commit()
        return ids
    finally:
        await db.close()


async def main():
    t1, t2, t3 = await seed()
    user = UserContext(email=ME, role="member")

    async def ls(**kw):
        return await tasks_mod.list_tasks(
            user=user, project_id=PID, page=Page(page=1, page_size=100), **kw)

    titles = lambda r: sorted(x["title"] for x in r.rows)  # noqa: E731

    # ── Auto-registration on use ───────────────────────────────────────────
    after = await tasks_mod.patch_task(t1, TaskIn(tags=["Bug", "ops"]), user=user)
    check("tags round-trip as a text[]", after["tags"], ["Bug", "ops"])

    registry = await tags_mod.list_tags(PID, user=user)
    check("using a tag registers it", sorted(r["name"] for r in registry["rows"]),
          ["Bug", "ops"])
    check("and the count is right",
          {r["name"]: r["task_count"] for r in registry["rows"]},
          {"Bug": 1, "ops": 1})

    # ── One spelling per tag ───────────────────────────────────────────────
    after = await tasks_mod.patch_task(t2, TaskIn(tags=["BUG", "bug"]), user=user)
    check("a differently-cased tag is stored with the REGISTRY spelling",
          after["tags"], ["Bug"])

    registry = await tags_mod.list_tags(PID, user=user)
    check("and no second tag was created", len(registry["rows"]), 2)

    # ── Filtering ──────────────────────────────────────────────────────────
    await tasks_mod.patch_task(t3, TaskIn(tags=["ops"]), user=user)
    check("tags= is ANY", titles(await ls(tags="Bug,ops")),
          ["task 1", "task 2", "task 3"])
    check("tags_all= is ALL", titles(await ls(tags_all="Bug,ops")), ["task 1"])
    check("a tag nobody uses matches nothing", (await ls(tags="ghost")).total, 0)

    # ── Duplicate refused with the spelling that exists ────────────────────
    try:
        await tags_mod.create_tag(PID, TagIn(name="bug"), user=user)
        check("a duplicate tag is refused", "no error", "409")
    except Exception as exc:
        check("a duplicate tag is refused", getattr(exc, "status_code", None), 409)
        check("and the refusal names the existing spelling",
              "'Bug'" in str(getattr(exc, "detail", "")), True)

    # ── Rename ─────────────────────────────────────────────────────────────
    by_name = {r["name"]: r for r in (await tags_mod.list_tags(PID, user=user))["rows"]}
    renamed = await tags_mod.patch_tag(
        by_name["Bug"]["id"], TagIn(name="defect", color="red"), user=user)
    check("a rename reports how many tasks it retagged", renamed["retagged"], 2)
    check("and recolours in the same call", renamed["color"], "red")

    fresh = await tasks_mod.get_task(t1, user=user)
    check("the task now wears the new name", fresh["tags"], ["defect", "ops"])
    check("in the SAME position it had", fresh["tags"][0], "defect")

    check("and the old name finds nothing", (await ls(tags="Bug")).total, 0)
    check("while the new one finds them", (await ls(tags="defect")).total, 2)

    # ── Rename onto an existing name is refused, not a silent merge ────────
    by_name = {r["name"]: r for r in (await tags_mod.list_tags(PID, user=user))["rows"]}
    try:
        await tags_mod.patch_tag(by_name["defect"]["id"], TagIn(name="ops"), user=user)
        check("renaming onto an existing tag is refused", "no error", "409")
    except Exception as exc:
        check("renaming onto an existing tag is refused",
              getattr(exc, "status_code", None), 409)
        check("and it points at merge",
              "erge" in str(getattr(exc, "detail", "")), True)

    # ── Merge ──────────────────────────────────────────────────────────────
    merged = await tags_mod.merge_tag(
        by_name["defect"]["id"], MergeIn(into_tag_id=by_name["ops"]["id"]), user=user)
    check("merge reports what moved", merged["retagged"], 2)

    both = await tasks_mod.get_task(t1, user=user)
    check("a task that carried BOTH ends with the target ONCE", both["tags"], ["ops"])
    only_source = await tasks_mod.get_task(t2, user=user)
    check("a task that carried only the source gets the target",
          only_source["tags"], ["ops"])

    left = await tags_mod.list_tags(PID, user=user)
    check("the source tag is gone", [r["name"] for r in left["rows"]], ["ops"])
    check("and the survivor's count is the union, not the sum",
          left["rows"][0]["task_count"], 3)

    try:
        await tags_mod.merge_tag(
            left["rows"][0]["id"], MergeIn(into_tag_id=left["rows"][0]["id"]),
            user=user)
        check("a tag cannot be merged into itself", "no error", "422")
    except Exception as exc:
        check("a tag cannot be merged into itself",
              getattr(exc, "status_code", None), 422)

    # ── Delete strips it from every task ───────────────────────────────────
    gone = await tags_mod.delete_tag(left["rows"][0]["id"], user=user)
    check("delete reports how many tasks it untagged",
          gone["cascaded"], {"tasks_untagged": 3})
    stripped = await tasks_mod.get_task(t1, user=user)
    check("and the task really lost it", stripped["tags"], [])
    check("the registry is empty again",
          (await tags_mod.list_tags(PID, user=user))["total"], 0)

    # ── The unique index is real, not only a Python rule ───────────────────
    db = await get_db()
    try:
        await db.execute(text(
            "INSERT INTO pm_tags (project_id, name, created_by) "
            "VALUES (CAST(:p AS uuid), 'Bug', :me)"), {"p": PID, "me": ME})
        await db.commit()
        try:
            await db.execute(text(
                "INSERT INTO pm_tags (project_id, name, created_by) "
                "VALUES (CAST(:p AS uuid), 'bug', :me)"), {"p": PID, "me": ME})
            await db.commit()
            check("the DATABASE refuses a differently-cased duplicate",
                  "inserted", "refused")
        except Exception:
            await db.rollback()
            check("the DATABASE refuses a differently-cased duplicate",
                  "refused", "refused")
        try:
            await db.execute(text(
                "INSERT INTO pm_tags (project_id, name, created_by) "
                "VALUES (CAST(:p AS uuid), ' padded ', :me)"), {"p": PID, "me": ME})
            await db.commit()
            check("the DATABASE refuses an untrimmed name", "inserted", "refused")
        except Exception:
            await db.rollback()
            check("the DATABASE refuses an untrimmed name", "refused", "refused")
    finally:
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
