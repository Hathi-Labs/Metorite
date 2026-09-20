"""WS-27bj / D-PM-16 — the org-wide RENAME, against a REAL Postgres.

Owner decision, 2026-09-20: an organization-wide tag, field or type may be
renamed by somebody who can change organization settings, after being shown
how many tasks the rename would rewrite. Merge and delete stay refused.

What only a database can answer here, and why the hermetic suite cannot:

* **``governed_tasks_scope`` produces a WHERE arm, and the arm is the whole
  claim.** For an org-wide row it is ``organization_id = CAST(:scope AS uuid)``
  and for a local one ``root_project_id = …``. The fake matches SQL by shape
  and answers from its own Python dicts, so it agrees with either arm. The
  question "did the rewrite reach the OTHER project's tasks" is a question
  about what Postgres matched.

* **The bug this replaced was a cast.** Every write path used to hand
  ``str(row.project_id)`` to ``CAST(:root AS uuid)``. For an org-wide row that
  is the literal string ``"None"``, which Postgres answers with an unhandled
  error — a 500 on a route that should have said something. A fake casts
  nothing and so cannot see it. `refuse_org_wide_write` existed to hide it.

* **The impact aggregate.** ``count(*)`` with
  ``count(DISTINCT root_project_id)`` over a ``:name = ANY(tags)`` predicate is
  precisely the shape a fake answers with whatever it was handed (R8).

⚠️ **This one does NOT ``TRUNCATE pm_projects CASCADE``**, unlike its
neighbours. It is meant to run against the local scratch tenant, which is also
the database the Projects UI is reviewed against, and truncating that destroys
the board somebody is looking at. It seeds rows under a recognisable prefix and
removes them in a ``finally`` instead. The cost is that it must clean up after
itself correctly; the alternative was a harness nobody dared run.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://<user>:<pw>@localhost:5434/acb_tenant" \\
      uv run python tests/live/live_ws27bj_rename.py
"""
import asyncio
import os
import sys
import uuid

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(
    0,
    os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"),
)

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from gateway.routes.projects import admin as pm_admin  # noqa: E402
from gateway.routes.projects import custom_fields as pm_fields  # noqa: E402
from gateway.routes.projects import tags as pm_tags  # noqa: E402
from sqlalchemy import text  # noqa: E402

from fastapi import HTTPException  # noqa: E402

ME = "dev@fracktal.in"
MARK = "__live_bj__"
#: pm_custom_fields.field_key is CHECKed against ^[a-z][a-z0-9_]{0,62}$,
#: so the marker prefix the other rows carry is not legal here.
FIELD_KEY = "live_bj_sev"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def owner() -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]))


def member() -> UserContext:
    return UserContext(
        email=ME, role=UserRole.EMPLOYEE, access=build_access(["feature:projects"]),
    )


async def one(sql, **params):
    db = await get_db()
    try:
        return (await db.execute(text(sql), params)).fetchone()
    finally:
        await db.close()


async def seed() -> dict:
    """Two SEPARATE trees in one organization, and an org-wide tag on both.

    Two trees is the whole point. One tree cannot tell an organization-scoped
    rewrite from a root-scoped one.
    """
    db = await get_db()
    try:
        # ⚠️ The CALLER's organization, taken from a project that already
        # exists. Any other organization seeds rows the tenant session cannot
        # see, and every check then reads zero and passes for the wrong reason.
        org = (await db.execute(
            text("SELECT organization_id AS id FROM pm_projects "
                 " WHERE organization_id IS NOT NULL LIMIT 1"),
        )).fetchone()
        if org is None:
            raise SystemExit('no tenanted project in this database - seed one first')
        org_id = str(org.id)

        made: dict = {"org": org_id, "projects": [], "tasks": [], "tags": [],
                      "fields": [], "types": [], "statuses": []}
        for n in (1, 2):
            pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, source, "
                "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), :name, 'manual', :me, true)"),
                {"id": pid, "o": org_id, "name": f"{MARK} tree {n}", "me": ME})
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), "
                "'To do', 1, 'todo', true)"), {"id": sid, "p": pid})
            made["projects"].append(pid)
            made["statuses"].append(sid)

            tid = str(uuid.uuid4())
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number, tags) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, "
                "'manual', :me, 1, ARRAY['shared'])"),
                {"id": tid, "o": org_id, "p": pid, "s": sid,
                 "t": f"{MARK} task {n}", "me": ME})
            made["tasks"].append(tid)

        # A task in tree 2 that does NOT wear the tag — the count must exclude it.
        extra = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, "
            "root_project_id, status_id, title, source, created_by, task_number, "
            "tags) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), "
            "CAST(:p AS uuid), CAST(:s AS uuid), :t, 'manual', :me, 2, "
            "ARRAY['untouched'])"),
            {"id": extra, "o": org_id, "p": made["projects"][1],
             "s": made["statuses"][1], "t": f"{MARK} spare", "me": ME})
        made["tasks"].append(extra)

        tag = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_tags (id, organization_id, project_id, name, color, "
            "created_by) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), NULL, "
            "'shared', 'gray', :me)"), {"id": tag, "o": org_id, "me": ME})
        made["tags"].append(tag)

        field = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_custom_fields (id, organization_id, project_id, "
            "field_key, name, field_type, created_by) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), NULL, :k, 'Severity', 'text', :me)"),
            {"id": field, "o": org_id, "k": FIELD_KEY, "me": ME})
        made["fields"].append(field)

        ttype = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO pm_task_types (id, organization_id, project_id, name) "
            "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), NULL, :n)"),
            {"id": ttype, "o": org_id, "n": f"{MARK} Spike"})
        made["types"].append(ttype)

        await db.commit()
        return made
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        for table, ids in (
            ("pm_tags", made["tags"]), ("pm_custom_fields", made["fields"]),
            ("pm_task_types", made["types"]), ("pm_tasks", made["tasks"]),
            ("pm_task_statuses", made["statuses"]),
        ):
            for row_id in ids:
                await db.execute(
                    text(f"DELETE FROM {table} WHERE id = CAST(:i AS uuid)"),
                    {"i": row_id},
                )
        for pid in made["projects"]:
            await db.execute(
                text("DELETE FROM pm_project_grants WHERE project_id = CAST(:i AS uuid)"),
                {"i": pid})
            await db.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:i AS uuid)"), {"i": pid})
        await db.commit()
        print(f"cleaned up {MARK} rows")
    finally:
        await db.close()


async def main():
    made = await seed()
    # ⚠️ The endpoints open a TENANT session, and there is no request here to
    # have bound one. Without this every call raises TenantUnbound - which is
    # the seam doing its job (MT-1c), not a fault in the script.
    bind_tenant(made["org"])
    try:
        tag_id, org_id = made["tags"][0], made["org"]
        here, there = made["projects"]

        # ── The preview, before anything is written ────────────────────────
        impact = await pm_tags.tag_impact(tag_id, user=owner())
        check("impact scope", impact["scope"], "organization")
        check("impact counts the tasks in BOTH trees", impact["tasks"], 2)
        check("impact counts the trees, not the nodes", impact["projects"], 2)

        # ── A member may not ──────────────────────────────────────────────
        try:
            await pm_tags.patch_tag(tag_id, pm_tags.TagIn(name="nope"), user=member())
            check("a member is refused", "allowed", "403")
        except HTTPException as exc:
            check("a member is refused", exc.status_code, 403)

        # ── The rename, and the scope of its rewrite ──────────────────────
        out = await pm_tags.patch_tag(
            tag_id, pm_tags.TagIn(name="renamed"), user=owner(),
        )
        check("the row is renamed", out["name"], "renamed")
        check("and it reports the whole organization", out["retagged"], 2)

        for label, pid in (("tree 1", here), ("tree 2", there)):
            row = await one(
                "SELECT count(*) AS n FROM pm_tasks "
                " WHERE root_project_id = CAST(:p AS uuid) AND 'renamed' = ANY(tags)",
                p=pid)
            check(f"{label}'s task now wears the new name", int(row.n), 1)

        left = await one(
            "SELECT count(*) AS n FROM pm_tasks "
            " WHERE organization_id = CAST(:o AS uuid) AND 'shared' = ANY(tags)",
            o=org_id)
        check("nothing still wears the old name", int(left.n), 0)

        spare = await one(
            "SELECT count(*) AS n FROM pm_tasks "
            " WHERE organization_id = CAST(:o AS uuid) AND 'untouched' = ANY(tags)",
            o=org_id)
        check("a task that never wore it is untouched", int(spare.n), 1)

        # ── The collision that would be a silent merge ─────────────────────
        local = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(text(
                "INSERT INTO pm_tags (id, organization_id, project_id, name, "
                "color, created_by) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
                "CAST(:p AS uuid), 'mine', 'gray', :me)"),
                {"id": local, "o": org_id, "p": there, "me": ME})
            await db.commit()
        finally:
            await db.close()
        made["tags"].append(local)

        try:
            await pm_tags.patch_tag(tag_id, pm_tags.TagIn(name="mine"), user=owner())
            check("a rename onto another tree's name is refused", "allowed", "409")
        except HTTPException as exc:
            check("a rename onto another tree's name is refused", exc.status_code, 409)
            check("and the message names where it lives",
                  f"in '{MARK} tree 2'" in str(exc.detail), True)

        # ── Delete and merge are still refused ────────────────────────────
        try:
            await pm_tags.delete_tag(tag_id, user=owner())
            check("delete is still refused", "allowed", "409")
        except HTTPException as exc:
            check("delete is still refused", exc.status_code, 409)
        still = await one(
            "SELECT count(*) AS n FROM pm_tags WHERE id = CAST(:i AS uuid)", i=tag_id)
        check("and the row survived the attempt", int(still.n), 1)

        # ── Field and type: a label move, with no task touched ────────────
        before = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE organization_id = "
            "CAST(:o AS uuid) AND custom_fields ? :k", o=org_id, k=FIELD_KEY)
        renamed_field = await pm_fields.patch_field(
            made["fields"][0], pm_fields.FieldIn(name="Impact"), user=owner())
        check("the field's label moves", renamed_field["name"], "Impact")
        after = await one(
            "SELECT count(*) AS n FROM pm_tasks WHERE organization_id = "
            "CAST(:o AS uuid) AND custom_fields ? :k", o=org_id, k=FIELD_KEY)
        check("and no task value moved with it", int(after.n), int(before.n))

        renamed_type = await pm_admin.patch_type(
            made["types"][0], pm_admin.TypeIn(name=f"{MARK} Spike II"), user=owner())
        check("the type's label moves", renamed_type["name"], f"{MARK} Spike II")

        try:
            await pm_admin.patch_type(
                made["types"][0],
                pm_admin.TypeIn(name=f"{MARK} Spike II", is_default=True),
                user=owner())
            check("is_default is refused org-wide", "allowed", "409")
        except HTTPException as exc:
            check("is_default is refused org-wide", exc.status_code, 409)
    finally:
        await clean(made)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print("all live checks passed")


asyncio.run(main())
