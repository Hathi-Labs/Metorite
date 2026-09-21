"""Merging tasks, against a REAL Postgres.

Owner request, 2026-09-21: *"merge multiple tasks into one … and the data of
both the tasks are appropriately combined."*

⚠️ **Almost nothing in this feature is Python.** It is twelve foreign keys'
worth of UPDATE and DELETE, and a hermetic fake agrees with whichever SQL you
hand it (R8). Five separate things here can only be answered by a database:

* **The UNIQUE keys.** `pm_task_assignees (task_id, assignee)` and three
  siblings refuse a duplicate. Whether the de-dupe runs BEFORE the move, and
  whether it de-dupes on the right column, is the difference between a merge
  and an IntegrityError.
* **The CHECK that holds the design up.** `pm_tasks_merged_is_archived` is
  what keeps a merged task reachable from the Archived shelf. A test that
  cannot violate a constraint cannot show that one binds.
* **`parent_task_id` is a self-reference.** Merging a parent into its own
  subtask is a real gesture, and nothing forbids a row being its own parent.
* **Link de-duplication is a correlated DELETE.** Self-links and duplicates
  are both created by the re-point itself.
* **Deleting the target must not delete the stub** — `ON DELETE SET NULL`,
  which is the foreign key and not any code here.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_r8" \\
      uv run python tests/live/live_task_merge.py
"""
import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import merge as pm_merge  # noqa: E402
from gateway.routes.projects import tasks as pm_tasks  # noqa: E402
from sqlalchemy import text  # noqa: E402

ME = "dev@fracktal.in"
MARK = "__live_merge__"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    # Windows console is cp1252; a character outside it kills the run after
    # the writes and before the cleanup.
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


def ts(day: str) -> datetime:
    """A date string → an aware datetime, which is what asyncpg wants."""
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def owner() -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]))


async def one(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchone()
    finally:
        await db.close()


async def rows(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchall()
    finally:
        await db.close()


async def scalar(sql, **params):
    row = await one(sql, **params)
    return None if row is None else row[0]


async def refused(coro, label, want=422):
    try:
        await coro
        check(label, "allowed", want)
    except Exception as exc:  # HTTPException
        check(label, getattr(exc, "status_code", None), want)


async def seed() -> dict:
    """Two projects, and in the first: a target, two sources, a subtask and
    a bystander for the links to point at."""
    db = await get_db()
    try:
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)

        await db.execute(text("DELETE FROM app_user WHERE email = :me"), {"me": ME})
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:me, CAST(:o AS uuid), :n)"),
            {"me": ME, "o": org_id, "n": f"{MARK} runner"})

        made: dict = {"org": org_id, "projects": {}, "tasks": {}, "views": {}}
        for key in ("home", "other"):
            pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, source, "
                "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), :n, 'manual', :me, true)"),
                {"id": pid, "o": org_id, "n": f"{MARK} {key}", "me": ME})
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:id AS uuid), "
                "CAST(:p AS uuid), 'To do', 1, 'todo', true)"),
                {"id": sid, "p": pid})
            made["projects"][key] = {"id": pid, "status": sid}

        async def task(key, project, number, **cols):
            tid = str(uuid.uuid4())
            p = made["projects"][project]
            fields = {
                "id": tid, "o": org_id, "p": p["id"], "s": p["status"],
                "t": f"{MARK} {key}", "me": ME, "n": number,
                "desc": cols.get("description"),
                "imp": cols.get("importance"),
                "est": cols.get("estimate_mins"),
                # ⚠️ REAL date objects. asyncpg binds by type and refuses a
                # string even behind a CAST — the gateway runs asyncpg, and a
                # harness that passed strings would be testing psycopg's
                # coercion rather than the driver in production (H-114).
                "due": cols.get("due_at"),
                "start": cols.get("start_date"),
                "tags": cols.get("tags", []),
                "custom": cols.get("custom_fields", "{}"),
                "parent": cols.get("parent"),
            }
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number, description, importance, estimate_mins, due_at, "
                "start_date, tags, custom_fields, parent_task_id) VALUES "
                "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), "
                "CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', :me, :n, "
                ":desc, :imp, :est, CAST(:due AS timestamptz), "
                "CAST(:start AS date), :tags, CAST(:custom AS jsonb), "
                "CAST(:parent AS uuid))"), fields)
            made["tasks"][key] = tid
            return tid

        # The survivor: mid priority, a later deadline, one tag, one custom
        # answer, its own description.
        await task("target", "home", 1, description="The target's own prose.",
                   importance=1, estimate_mins=60, due_at=ts("2026-12-01"),
                   start_date=date(2026, 11, 1), tags=["alpha"],
                   custom_fields='{"owner_team": "firmware"}')
        # Source A: more urgent, earlier deadline, overlapping + new tag, a
        # custom key the target has not answered.
        await task("a", "home", 2, description="Source A prose.",
                   importance=3, estimate_mins=30, due_at=ts("2026-10-01"),
                   start_date=date(2026, 10, 15), tags=["Alpha", "beta"],
                   custom_fields='{"owner_team": "radio", "risk": "high"}')
        # Source B: no description, no dates, no priority.
        await task("b", "home", 3, tags=["gamma"])
        await task("kid", "home", 4, parent=made["tasks"]["a"])
        # A three-generation chain for the GRANDCHILD case: gp -> mid -> gc.
        await task("gp", "home", 20)
        await task("mid", "home", 21, parent=made["tasks"]["gp"])
        await task("gc", "home", 22, parent=made["tasks"]["mid"])
        # And a pair for the blocks-cycle case: cyc_s blocks via mid_x to cyc_t.
        await task("cyc_s", "home", 30)
        await task("cyc_x", "home", 31)
        await task("cyc_t", "home", 32)
        await task("bystander", "home", 5)
        await task("faraway", "other", 1)

        t = made["tasks"]
        # Comments and history on both sources and the target.
        for key, n in (("target", 1), ("a", 2), ("b", 1)):
            for i in range(n):
                await db.execute(text(
                    "INSERT INTO pm_activities (task_id, type, body, created_by) "
                    "VALUES (CAST(:t AS uuid), 'comment', :b, :me)"),
                    {"t": t[key], "b": f"{MARK} {key} {i}", "me": ME})

        # Assignees: one shared, one unique to each side.
        for key, who in (("target", "ada@x.com"), ("target", "shared@x.com"),
                         ("a", "shared@x.com"), ("a", "priya@x.com")):
            await db.execute(text(
                "INSERT INTO pm_task_assignees (task_id, organization_id, "
                "assignee, assigned_by) VALUES (CAST(:t AS uuid), "
                "CAST(:o AS uuid), :w, :me)"),
                {"t": t[key], "o": org_id, "w": who, "me": ME})
        for key, who in (("target", "ada@x.com"), ("a", "ada@x.com"),
                         ("a", "ravi@x.com")):
            await db.execute(text(
                "INSERT INTO pm_task_watchers (task_id, watcher, created_by) "
                "VALUES (CAST(:t AS uuid), :w, :me)"),
                {"t": t[key], "w": who, "me": ME})

        # Links: A blocks the bystander, and so does the target (a DUPLICATE
        # after the merge). A also blocks the target (a SELF-LINK after it).
        for src, dst, kind in (("a", "bystander", "blocks"),
                               ("target", "bystander", "blocks"),
                               ("a", "target", "blocks"),
                               ("b", "bystander", "relates_to"),
                               # S blocks X, X blocks T. Re-pointing S onto T
                               # closes a two-node loop.
                               ("cyc_s", "cyc_x", "blocks"),
                               ("cyc_x", "cyc_t", "blocks")):
            await db.execute(text(
                "INSERT INTO pm_task_links (source_task_id, target_task_id, "
                "link_type, organization_id, created_by) VALUES "
                "(CAST(:s AS uuid), CAST(:d AS uuid), :k, CAST(:o AS uuid), :me)"),
                {"s": t[src], "d": t[dst], "k": kind, "o": org_id, "me": ME})

        # ⚠️ An intake row on BOTH sides. `pm_intake.task_id` is UNIQUE, and
        # two emails about one thing is the canonical use of merging — the
        # first draft answered 500 here.
        for key in ("target", "a"):
            await db.execute(text(
                "INSERT INTO pm_intake (task_id, organization_id, source, "
                "source_ref, status, created_by) VALUES (CAST(:t AS uuid), "
                "CAST(:o AS uuid), 'email', :s, 'accepted', :me)"),
                {"t": t[key], "o": org_id, "s": f"{MARK} {key}", "me": ME})

        # The same attachment id on both: `(task_id, attachment_id)` is the
        # primary key, so a blind move would collide.
        shared_file = str(uuid.uuid4())
        for key in ("target", "a"):
            await db.execute(text(
                "INSERT INTO pm_task_attachments (task_id, attachment_id, "
                "organization_id, added_by) VALUES (CAST(:t AS uuid), "
                "CAST(:f AS uuid), CAST(:o AS uuid), :me)"),
                {"t": t[key], "f": shared_file, "o": org_id, "me": ME})

        # A personal overlay for the same member on BOTH sides: the target's
        # must win, and the source's must not collide.
        # ⚠️ The SOURCE's row carries tracked actuals. "Two rows say the
        # same thing" is true of an assignee and false of this table.
        for key, disp, actual in (("target", "NEXT", None), ("a", "SOMEDAY", True)):
            await db.execute(text(
                "INSERT INTO pm_task_personal (task_id, member_email, "
                "disposition, organization_id, actual_start, actual_end) "
                "VALUES (CAST(:t AS uuid), :m, :d, CAST(:o AS uuid), "
                "CAST(:s AS timestamptz), CAST(:e AS timestamptz))"),
                {"t": t[key], "m": ME, "d": disp, "o": org_id,
                 "s": ts("2026-09-01") if actual else None,
                 "e": ts("2026-09-02") if actual else None})

        await db.commit()
        return made
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        for p in made["projects"].values():
            for sql in (
                "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM "
                "pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
                "DELETE FROM pm_intake WHERE task_id IN (SELECT id FROM "
                "pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
                "DELETE FROM pm_task_attachments WHERE task_id IN (SELECT id "
                "FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
                "DELETE FROM pm_task_personal WHERE task_id IN (SELECT id "
                "FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
                "DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)",
                "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
                "DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)",
                "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)",
            ):
                await db.execute(text(sql), {"p": p["id"]})
        await db.execute(text(
            "DELETE FROM app_user WHERE email = :me AND display_name = :n"),
            {"me": ME, "n": f"{MARK} runner"})
        await db.commit()
        print(f"cleaned up {MARK} rows")
    finally:
        await db.close()


async def main():  # noqa: C901
    made = await seed()
    bind_tenant(made["org"])
    t = made["tasks"]
    try:
        # ── The refusals, BEFORE the merge changes the world ───────────────
        await refused(
            pm_merge.merge_tasks(t["target"], pm_merge.MergeIn(sources=[t["target"]]),
                                 user=owner()),
            "a task cannot be merged into itself")
        await refused(
            pm_merge.merge_tasks(t["target"], pm_merge.MergeIn(sources=[t["faraway"]]),
                                 user=owner()),
            "a cross-project merge is refused")
        await refused(
            pm_merge.merge_tasks(t["target"], pm_merge.MergeIn(sources=[]),
                                 user=owner()),
            "an empty merge is refused")

        # ── The merge ──────────────────────────────────────────────────────
        out = await pm_merge.merge_tasks(
            t["target"], pm_merge.MergeIn(sources=[t["a"], t["b"]]), user=owner())
        check("both sources report merged", len(out["merged"]), 2)

        # ── The stub: archived, pointing, and REACHABLE ────────────────────
        stub = await one(
            "SELECT merged_into_task_id, archived_at, merged_by FROM pm_tasks "
            "WHERE id = CAST(:i AS uuid)", i=t["a"])
        check("the source points at the target",
              str(stub.merged_into_task_id), t["target"])
        # 🔴 The owner's objection, answered. A merged task is ON THE SHELF,
        # so the Archived filter lists it and Delete and Unarchive reach it.
        check("and is archived, so nothing becomes unreachable",
              stub.archived_at is not None, True)
        check("and records who did it", stub.merged_by, ME)

        # ── The content moved ──────────────────────────────────────────────
        check("every comment is on the target now",
              await scalar("SELECT count(*) FROM pm_activities WHERE task_id = "
                           "CAST(:i AS uuid) AND type = 'comment'", i=t["target"]), 4)
        check("and none is left on the stub",
              await scalar("SELECT count(*) FROM pm_activities WHERE task_id = "
                           "CAST(:i AS uuid) AND type = 'comment'", i=t["a"]), 0)
        check("the stub's timeline says where it went",
              await scalar("SELECT count(*) FROM pm_activities WHERE task_id = "
                           "CAST(:i AS uuid) AND type = 'merge'", i=t["a"]), 1)
        check("and so does the survivor's",
              await scalar("SELECT count(*) FROM pm_activities WHERE task_id = "
                           "CAST(:i AS uuid) AND type = 'merge'", i=t["target"]), 1)

        # ⚠️ The NAMES, not a count. A count passes on the right total made
        # of the wrong people — and the first version of this check wanted
        # three watchers where the union is two, which a list would have
        # said plainly instead of reading as a bug in the merge.
        #
        # `shared@x.com` is on both sides and `ada@x.com` watches both, so
        # each is one row after the merge: the UNIQUE key would have refused
        # a second, which is what the de-dupe before the move exists for.
        check("assignees union, de-duplicated",
              sorted(r[0] for r in await rows(
                  "SELECT assignee FROM pm_task_assignees WHERE task_id = "
                  "CAST(:i AS uuid)", i=t["target"])),
              ["ada@x.com", "priya@x.com", "shared@x.com"])
        check("watchers union, de-duplicated",
              sorted(r[0] for r in await rows(
                  "SELECT watcher FROM pm_task_watchers WHERE task_id = "
                  "CAST(:i AS uuid)", i=t["target"])),
              ["ada@x.com", "ravi@x.com"])
        check("the target's own personal overlay survives",
              await scalar("SELECT disposition FROM pm_task_personal WHERE "
                           "task_id = CAST(:i AS uuid) AND member_email = :m",
                           i=t["target"], m=ME), "NEXT")

        # ── The subtask is MOVED, not merged ───────────────────────────────
        check("the source's subtask is the target's now",
              str(await scalar("SELECT parent_task_id FROM pm_tasks WHERE id = "
                               "CAST(:i AS uuid)", i=t["kid"])), t["target"])

        # ── Links ──────────────────────────────────────────────────────────
        check("the self-link is gone, not drawn",
              await scalar("SELECT count(*) FROM pm_task_links WHERE "
                           "source_task_id = target_task_id"), 0)
        check("the duplicate blocks edge collapsed to one",
              await scalar("SELECT count(*) FROM pm_task_links WHERE "
                           "source_task_id = CAST(:i AS uuid) AND link_type = "
                           "'blocks'", i=t["target"]), 1)
        check("and B's relates_to came across",
              await scalar("SELECT count(*) FROM pm_task_links WHERE "
                           "source_task_id = CAST(:i AS uuid) AND link_type = "
                           "'relates_to'", i=t["target"]), 1)

        # ── The scalars ────────────────────────────────────────────────────
        kept = await one(
            "SELECT description, importance, estimate_mins, due_at, start_date, "
            "tags, custom_fields FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["target"])
        check("priority takes the HIGHER", kept.importance, 3)
        check("estimate SUMS", kept.estimate_mins, 90)
        check("due date takes the EARLIEST, so no commitment is relaxed",
              kept.due_at.date().isoformat(), "2026-10-01")
        check("start date takes the earliest",
              kept.start_date.isoformat(), "2026-10-15")
        # `Alpha` folds onto `alpha`; `beta` and `gamma` are new.
        check("tags union, case-folded", list(kept.tags),
              ["alpha", "beta", "gamma"])
        check("a custom answer the target gave is NOT overwritten",
              kept.custom_fields.get("owner_team"), "firmware")
        check("and one it never gave is filled in",
              kept.custom_fields.get("risk"), "high")
        check("the source's prose is kept, under a rule naming it",
              f"merged from #{2}" in (kept.description or ""), True)
        check("and the target's own prose is still first",
              (kept.description or "").startswith("The target's own prose."), True)

        # ── Merging into a stub is refused, with the end named ─────────────
        await refused(
            pm_merge.merge_tasks(t["a"], pm_merge.MergeIn(sources=[t["bystander"]]),
                                 user=owner()),
            "you cannot merge into a task that was itself merged away")
        await refused(
            pm_merge.merge_tasks(t["target"], pm_merge.MergeIn(sources=[t["a"]]),
                                 user=owner()),
            "and a merged task cannot be merged again")

        # 🔴 `pm_intake.task_id` is UNIQUE. The first draft moved it blind and
        # answered 500 — on the canonical use of the feature, two captured
        # emails about one thing.
        check("both intake records survive the merge",
              await scalar("SELECT count(*) FROM pm_intake WHERE source_ref LIKE :m",
                           m=f"{MARK}%"), 2)
        check("and the source's still names the task it created",
              str(await scalar(
                  "SELECT task_id FROM pm_intake WHERE source_ref = :s",
                  s=f"{MARK} a")), t["a"])

        # `(task_id, attachment_id)` is the primary key.
        check("a shared attachment does not collide",
              await scalar("SELECT count(*) FROM pm_task_attachments WHERE "
                           "task_id = CAST(:i AS uuid)", i=t["target"]), 1)

        # 🔴 The source's tracked actuals are a member's real work. The first
        # draft DELETED the row to de-duplicate.
        check("the source's tracked actuals are not destroyed",
              await scalar("SELECT count(*) FROM pm_task_personal WHERE "
                           "task_id = CAST(:i AS uuid) AND actual_start IS NOT NULL",
                           i=t["a"]), 1)

        # A re-parented subtask is an edit no delta client sees otherwise.
        kid_seen = await one(
            "SELECT updated_at, created_at FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["kid"])
        check("a re-parented subtask is bumped for the delta feed",
              kid_seen.updated_at > kid_seen.created_at, True)

        # ── The cases adversarial review found, 2026-09-21 ────────────────
        #
        # Every one of these was a live defect in the first draft, and the
        # first draft's own comments claimed three of them were impossible.

        # 🔴 A GRANDCHILD. The first guard only tested the direct-child case,
        # so merging a task into its own grandchild made the two tasks each
        # other's parent — a shape `assert_no_task_cycle` refuses on every
        # other write path in the product.
        await pm_merge.merge_tasks(
            t["gc"], pm_merge.MergeIn(sources=[t["gp"]]), user=owner())
        gc_parent = await scalar(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["gc"])
        mid_parent = await scalar(
            "SELECT parent_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["mid"])
        check("merging into a GRANDCHILD leaves no parent cycle",
              str(gc_parent) == t["mid"] and str(mid_parent) == t["gc"], False)
        check("and the middle task hangs off the survivor",
              str(mid_parent), t["gc"])

        # 🔴 A `blocks` CYCLE. S blocks X and X blocks T; re-pointing S's edge
        # onto T closes the loop. `assert_no_block_cycle` calls this "a
        # deadlock no human can resolve by finishing something", and the link
        # endpoint refuses it with 422 — so the merge must not create it.
        await pm_merge.merge_tasks(
            t["cyc_t"], pm_merge.MergeIn(sources=[t["cyc_s"]]), user=owner())
        loop = await scalar(
            "SELECT count(*) FROM pm_task_links a JOIN pm_task_links b "
            "ON a.source_task_id = b.target_task_id "
            "AND a.target_task_id = b.source_task_id "
            "WHERE a.link_type = 'blocks' AND b.link_type = 'blocks'")
        check("a merge cannot create a blocks cycle", loop, 0)

        # 🔴 A REDIRECT CHAIN. Merge A into B, then B into C: A pointed at a
        # stub, and one hop from A landed on an empty task. Merging must
        # re-point everything that aimed at the task being merged away.
        chain = await one(
            "SELECT merged_into_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["a"])
        # `a` was merged into `target` earlier. Now fold `target` into `kid`.
        await pm_merge.merge_tasks(
            t["kid"], pm_merge.MergeIn(sources=[t["target"]]), user=owner())
        rechained = await scalar(
            "SELECT merged_into_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["a"])
        check("a stub follows its target when that target is merged on",
              str(rechained), t["kid"])
        check("so no stub ever points at another stub",
              await scalar(
                  "SELECT count(*) FROM pm_tasks s JOIN pm_tasks m "
                  "ON s.merged_into_task_id = m.id "
                  "WHERE m.merged_into_task_id IS NOT NULL"), 0)
        assert chain is not None

        # ── Unarchiving a stub makes it a task again ───────────────────────────
        #
        # ⚠️ The DATABASE forces this: `pm_tasks_merged_is_archived` refuses a
        # row that claims to be merged while off the shelf, so clearing one
        # without the other is a 500. This proves the endpoint clears both.
        await pm_tasks.unarchive_task(t["a"], user=owner())
        back = await one(
            "SELECT archived_at, merged_into_task_id FROM pm_tasks WHERE "
            "id = CAST(:i AS uuid)", i=t["a"])
        check("unarchiving takes it off the shelf", back.archived_at, None)
        check("and it is no longer merged", back.merged_into_task_id, None)

        # ── Deleting the TARGET must not destroy the stub ──────────────────
        #
        # The foreign key, not any code here. A cascade would delete a row
        # whose owner never touched it, and with it the record that a merge
        # ever happened.
        db = await get_db()
        try:
            await db.execute(text(
                "UPDATE pm_tasks SET merged_into_task_id = CAST(:dst AS uuid), "
                "archived_at = now() WHERE id = CAST(:src AS uuid)"),
                {"src": t["b"], "dst": t["target"]})
            await db.execute(text(
                "DELETE FROM pm_tasks WHERE id = CAST(:i AS uuid)"),
                {"i": t["target"]})
            await db.commit()
        finally:
            await db.close()
        survivor = await one(
            "SELECT merged_into_task_id FROM pm_tasks WHERE id = CAST(:i AS uuid)",
            i=t["b"])
        check("the stub survives its target's deletion", survivor is not None, True)
        check("and simply stops redirecting",
              survivor.merged_into_task_id if survivor else "gone", None)
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
