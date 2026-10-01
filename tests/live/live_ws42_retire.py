"""H-205 — retiring a SHARED tag, field or type, against a REAL Postgres (R8).

Owner ruling 2026-10-01: an admin may delete a shared tag, field or type, and
merge a shared tag into another shared tag, after the count. Spec
`project-docs/specs/projects_settings.md` §7 row PS-3b.

What only a database can prove: `governed_tasks_scope` leaves out a space that
keeps its OWN tag or field of the same identity. Tags are stored as text and
field values by key, so without that exclusion an organization-wide rename,
merge or delete would rewrite the space's own data. The hermetic fake agrees
with any SQL it is handed, so this file is the fence.

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant" \\
      uv run python tests/live/live_ws42_retire.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL", "postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant"
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.db import get_db
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import custom_fields as pm_fields
from gateway.routes.projects import tags as pm_tags
from gateway.routes.projects import vocabulary as pm_vocab
from sqlalchemy import text

OWNER = "ws42r-owner@example.test"
MEMBER = "ws42r-member@example.test"
SLUG = "ws42retire"
SLUG_B = "ws42retire-b"
S1 = "42e00000-0000-4000-8000-0000000000a1"
S2 = "42e00000-0000-4000-8000-0000000000a2"
failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email: str, features: str = "*") -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access([features]))


async def run(sql: str, **params: object) -> object:
    db = await get_db()
    try:
        result = await db.execute(text(sql), params)
        await db.commit()
        return result
    finally:
        await db.close()


async def one(sql: str, **params: object) -> object:
    return (await run(sql, **params)).scalar()  # type: ignore[attr-defined]


async def clean() -> None:
    await run("DELETE FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[]))", ids=[S1, S2])
    for table in ("pm_tags", "pm_custom_fields", "pm_task_types"):
        await run(
            f"DELETE FROM {table} WHERE organization_id IN "
            "(SELECT id FROM organization WHERE slug = ANY(:s))",
            s=[SLUG, SLUG_B],
        )
    await run("DELETE FROM app_user WHERE email = ANY(:e)", e=[OWNER, MEMBER])
    await run("DELETE FROM organization WHERE slug = ANY(:s)", s=[SLUG, SLUG_B])


async def seed() -> tuple[str, dict[str, str]]:
    org = str(
        (
            await run(
                "INSERT INTO organization (slug, display_name) VALUES (:s, :s) RETURNING id", s=SLUG
            )
        )
        .fetchone()
        .id  # type: ignore[attr-defined]
    )
    for email in (OWNER, MEMBER):
        await run(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))",
            e=email,
            o=org,
        )
    for pid in (S1, S2):
        await run(
            "INSERT INTO pm_projects (id, name, source, created_by, organization_id, owns_statuses) "
            "VALUES (CAST(:p AS uuid), :p, 'manual', :me, CAST(:o AS uuid), true)",
            p=pid,
            me=OWNER,
            o=org,
        )
    ids: dict[str, str] = {}

    async def row(table: str, cols: str, vals: str, key: str, **params: object) -> None:
        ids[key] = str(
            (
                await run(
                    f"INSERT INTO {table} ({cols}) VALUES ({vals}) RETURNING id",
                    o=org,
                    me=OWNER,
                    **params,
                )
            )
            .fetchone()
            .id  # type: ignore[attr-defined]
        )

    tag_cols = "project_id, organization_id, name, color, created_by"
    await row("pm_tags", tag_cols, "NULL, CAST(:o AS uuid), 'shared', 'blue', :me", "tag_shared")
    await row("pm_tags", tag_cols, "NULL, CAST(:o AS uuid), 'other', 'red', :me", "tag_other")
    # Space 2 keeps its OWN "shared" tag: it shadows the organization's.
    await row(
        "pm_tags",
        tag_cols,
        "CAST(:p AS uuid), CAST(:o AS uuid), 'shared', 'gray', :me",
        "tag_local",
        p=S2,
    )
    field_cols = "project_id, organization_id, field_key, name, field_type, position, created_by"
    await row(
        "pm_custom_fields",
        field_cols,
        "NULL, CAST(:o AS uuid), 'cost', 'Cost', 'number', 1, :me",
        "field_shared",
    )
    await row(
        "pm_custom_fields",
        field_cols,
        "CAST(:p AS uuid), CAST(:o AS uuid), 'cost', 'Cost', 'number', 1, :me",
        "field_local",
        p=S2,
    )
    await row(
        "pm_task_types",
        "project_id, organization_id, name, is_system",
        "NULL, CAST(:o AS uuid), 'Spike', false",
        "type_shared",
    )
    tasks = (
        ("t1", S1, ["shared"], ids["type_shared"], True),
        ("t2", S2, ["shared"], None, True),
        ("t3", S1, ["other"], None, False),
    )
    for n, (name, space, tag_list, type_id, cost) in enumerate(tasks):
        status = (
            (
                await run(
                    "INSERT INTO pm_task_statuses (project_id, name, position, category) "
                    "VALUES (CAST(:p AS uuid), :n, :pos, 'todo') RETURNING id",
                    p=space,
                    n=f"To do {n}",
                    pos=10 + n,
                )
            )
            .fetchone()
            .id
        )  # type: ignore[attr-defined]
        ids[name] = str(
            (
                await run(
                    "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, title, "
                    " task_number, tags, type_id, custom_fields, created_by) VALUES "
                    "(gen_random_uuid(), CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t,"
                    " :n, CAST(:tags AS text[]), CAST(:ty AS uuid), CAST(:cf AS jsonb), :me)"
                    " RETURNING id",
                    p=space,
                    s=str(status),
                    t=name,
                    n=200 + n,
                    tags=tag_list,
                    ty=type_id,
                    cf=json.dumps({"cost": 5} if cost else {}),
                    me=OWNER,
                )
            )
            .fetchone()
            .id  # type: ignore[attr-defined]
        )
    # Another organization, with one shared tag of its own.
    org_b = str(
        (
            await run(
                "INSERT INTO organization (slug, display_name) VALUES (:s, :s) RETURNING id",
                s=SLUG_B,
            )
        )
        .fetchone()
        .id  # type: ignore[attr-defined]
    )
    ids["tag_b"] = str(
        (
            await run(
                "INSERT INTO pm_tags (project_id, organization_id, name, color, created_by) "
                "VALUES (NULL, CAST(:o AS uuid), 'theirs', 'red', 'b@example.test') RETURNING id",
                o=org_b,
            )
        )
        .fetchone()
        .id  # type: ignore[attr-defined]
    )
    # Space 1 keeps its own "kept" tag, which a shared merge must not fill.
    await row(
        "pm_tags",
        tag_cols,
        "CAST(:p AS uuid), CAST(:o AS uuid), 'kept', 'gray', :me",
        "tag_kept_local",
        p=S1,
    )
    await row("pm_tags", tag_cols, "NULL, CAST(:o AS uuid), 'kept', 'gray', :me", "tag_kept_shared")
    return org, ids


async def task(ids: dict[str, str], name: str) -> object:
    db = await get_db()
    try:
        return (
            await db.execute(
                text(
                    "SELECT tags, type_id, custom_fields FROM pm_tasks WHERE id = CAST(:i AS uuid)"
                ),
                {"i": ids[name]},
            )
        ).fetchone()
    finally:
        await db.close()


async def status_of(call) -> int:  # type: ignore[no-untyped-def]
    try:
        await call
    except HTTPException as exc:
        return exc.status_code
    return 200


async def main() -> None:
    await clean()
    org, ids = await seed()
    token = bind_tenant(org)
    try:
        owner, member = user(OWNER), user(MEMBER, "feature:projects")
        imp = await pm_vocab.vocabulary_impact("tags", ids["tag_shared"], user=owner)
        check(
            "the count leaves out the space with its own tag",
            (imp["tasks"], imp["projects"]),
            (1, 1),
        )
        check(
            "a member may not read a shared delete's count",
            await status_of(pm_vocab.vocabulary_impact("tags", ids["tag_shared"], user=member)),
            403,
        )
        check(
            "a member may not delete a shared tag",
            await status_of(pm_tags.delete_tag(ids["tag_shared"], user=member)),
            403,
        )

        # The review's P0: another organization's shared tag is not found,
        # for every act, and it survives.
        theirs = ids["tag_b"]
        for label, call in (
            ("rename", pm_tags.patch_tag(theirs, pm_tags.TagIn(name="x"), user=owner)),
            ("delete", pm_tags.delete_tag(theirs, user=owner)),
            ("count", pm_vocab.vocabulary_impact("tags", theirs, user=owner)),
            (
                "merge",
                pm_tags.merge_tag(
                    ids["tag_other"], pm_tags.MergeIn(into_tag_id=theirs), user=owner
                ),
            ),
        ):
            check(
                f"another organization's shared tag: {label} is not found",
                await status_of(call),
                404,
            )
        check(
            "and it survives",
            await one("SELECT count(*) FROM pm_tags WHERE id = CAST(:i AS uuid)", i=theirs),
            1,
        )
        check(
            "a merge onto a name a space keeps for itself is refused",
            await status_of(
                pm_tags.merge_tag(
                    ids["tag_other"],
                    pm_tags.MergeIn(into_tag_id=ids["tag_kept_shared"]),
                    user=owner,
                )
            ),
            409,
        )

        # The rename regression the scope fix closes: space 2's own "shared" stays.
        await pm_tags.patch_tag(ids["tag_shared"], pm_tags.TagIn(name="common"), user=owner)
        check(
            "a shared rename reaches a space without its own tag",
            list((await task(ids, "t1")).tags),
            ["common"],
        )
        check(
            "and leaves the space's own tag alone", list((await task(ids, "t2")).tags), ["shared"]
        )

        check(
            "a shared tag never merges into a space's tag",
            await status_of(
                pm_tags.merge_tag(
                    ids["tag_shared"], pm_tags.MergeIn(into_tag_id=ids["tag_local"]), user=owner
                )
            ),
            409,
        )
        merged = await pm_tags.merge_tag(
            ids["tag_shared"], pm_tags.MergeIn(into_tag_id=ids["tag_other"]), user=owner
        )
        check("a shared tag merges into another shared tag", merged["retagged"], 1)
        check("its task now wears the target", list((await task(ids, "t1")).tags), ["other"])
        check(
            "the merged row is gone",
            await one(
                "SELECT count(*) FROM pm_tags WHERE id = CAST(:i AS uuid)", i=ids["tag_shared"]
            ),
            0,
        )

        imp = await pm_vocab.vocabulary_impact("fields", ids["field_shared"], user=owner)
        check("a field's count leaves out the space with its own field", imp["tasks"], 1)
        await pm_fields.delete_field(ids["field_shared"], user=owner)
        check(
            "deleting a shared field clears its values",
            dict((await task(ids, "t1")).custom_fields),
            {},
        )
        check(
            "but not the values of a space's own field",
            dict((await task(ids, "t2")).custom_fields),
            {"cost": 5},
        )

        imp = await pm_vocab.vocabulary_impact("types", ids["type_shared"], user=owner)
        check("a type's count", imp["tasks"], 1)
        done = await pm_admin.delete_type(ids["type_shared"], user=owner)
        check("deleting a shared type reports its tasks", done["tasks_untyped"], 1)
        check("and leaves them untyped", (await task(ids, "t1")).type_id, None)

        gone = await pm_tags.delete_tag(ids["tag_other"], user=owner)
        check("deleting a shared tag strips it", gone["cascaded"]["tasks_untagged"], 2)
        check(
            "space 2's own tag survives every step", list((await task(ids, "t2")).tags), ["shared"]
        )
    finally:
        release_tenant(token)
        await clean()
    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
