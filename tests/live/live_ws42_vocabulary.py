"""WS-42 PS-3 — the organization's own vocabulary, against a REAL Postgres (R8).

Spec `project-docs/specs/projects_settings.md` §7 row PS-3.

`GET /projects/vocabulary` lists the org-wide tags, fields and types of the
caller's tenant, and it has no project to anchor on. So what needs a database is
the part a hermetic fake would agree with whatever it is handed:

* only ORG-WIDE rows come back, never a space's own row;
* only the CALLER'S tenant, never another organization's org-wide row;
* the counts are right: open tasks only, and across every space;
* a member without `admin:settings:manage` gets the rows and no counts.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant" \\
      uv run python tests/live/live_ws42_vocabulary.py

It seeds two throwaway organizations and deletes them at the end.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL", "postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant"
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.db import get_db
from gateway.routes.projects import vocabulary as pm_vocab
from sqlalchemy import text

OWNER = "ws42-owner@example.test"
MEMBER = "ws42-member@example.test"
THEM = "ws42-them@example.test"
SLUGS = ("ws42ps3a", "ws42ps3b")
SPACE_A = "42000000-0000-4000-8000-00000000000a"
SPACE_A2 = "42000000-0000-4000-8000-0000000000a2"
SPACE_B = "42000000-0000-4000-8000-00000000000b"
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


async def clean() -> None:
    await run(
        "DELETE FROM pm_projects WHERE id = ANY(CAST(:ids AS uuid[]))",
        ids=[SPACE_A, SPACE_A2, SPACE_B],
    )
    for table in ("pm_tags", "pm_custom_fields", "pm_task_types"):
        await run(
            f"DELETE FROM {table} WHERE organization_id IN "
            "(SELECT id FROM organization WHERE slug = ANY(:s))",
            s=list(SLUGS),
        )
    await run("DELETE FROM app_user WHERE email = ANY(:e)", e=[OWNER, MEMBER, THEM])
    await run("DELETE FROM organization WHERE slug = ANY(:s)", s=list(SLUGS))


async def seed() -> tuple[str, str]:
    orgs = []
    for slug in SLUGS:
        result = await run(
            "INSERT INTO organization (slug, display_name) VALUES (:s, :s) RETURNING id", s=slug
        )
        orgs.append(str(result.fetchone().id))  # type: ignore[attr-defined]
    org_a, org_b = orgs
    for email, org in ((OWNER, org_a), (MEMBER, org_a), (THEM, org_b)):
        await run(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))",
            e=email, o=org,
        )
    for pid, org in ((SPACE_A, org_a), (SPACE_A2, org_a), (SPACE_B, org_b)):
        await run(
            "INSERT INTO pm_projects (id, name, source, created_by, organization_id, owns_statuses) "
            "VALUES (CAST(:p AS uuid), :p, 'manual', :me, CAST(:o AS uuid), true)",
            p=pid, me=OWNER, o=org,
        )
    # Org-wide rows: two in A, one of each kind, and one tag in B.
    type_id = (await run(
        "INSERT INTO pm_task_types (project_id, organization_id, name, is_system) "
        "VALUES (NULL, CAST(:o AS uuid), 'Spike', false) RETURNING id", o=org_a,
    )).fetchone().id  # type: ignore[attr-defined]
    await run(
        "INSERT INTO pm_tags (project_id, organization_id, name, color, created_by) "
        "VALUES (NULL, CAST(:o AS uuid), 'shared', 'blue', :me)", o=org_a, me=OWNER,
    )
    await run(
        "INSERT INTO pm_custom_fields (project_id, organization_id, field_key, name, "
        "field_type, position, created_by) VALUES (NULL, CAST(:o AS uuid), 'cost', 'Cost', 'number', 1, :me)",
        o=org_a, me=OWNER,
    )
    await run(
        "INSERT INTO pm_tags (project_id, organization_id, name, color, created_by) "
        "VALUES (NULL, CAST(:o AS uuid), 'theirs', 'red', :me)", o=org_b, me=THEM,
    )
    # A space's OWN tag, which the organization list must not show.
    await run(
        "INSERT INTO pm_tags (project_id, organization_id, name, color, created_by) "
        "VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'local-only', 'gray', :me)",
        p=SPACE_A, o=org_a, me=OWNER,
    )
    # Tasks: two open ones in two spaces, and one archived one that must not count.
    for n, (space, archived) in enumerate(((SPACE_A, False), (SPACE_A2, False), (SPACE_A, True))):
        status = (await run(
            "INSERT INTO pm_task_statuses (project_id, name, position, category) "
            "VALUES (CAST(:p AS uuid), :n, :pos, 'todo') RETURNING id",
            p=space, n=f"To do {n}", pos=10 + n,
        )).fetchone().id  # type: ignore[attr-defined]
        await run(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, title, "
            " task_number, tags, type_id, custom_fields, created_by, archived_at) VALUES "
            "(gen_random_uuid(), CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, "
            " :n, ARRAY['shared'], CAST(:ty AS uuid), CAST('{\"cost\": 3}' AS jsonb), :me, "
            " CASE WHEN :a THEN now() END)",
            p=space, s=str(status), t=f"Task {n}", n=100 + n, ty=str(type_id), me=OWNER,
            a=archived,
        )
    return org_a, org_b


async def as_tenant(org: str, who: UserContext) -> dict:
    token = bind_tenant(org)
    try:
        return await pm_vocab.org_vocabulary(user=who)
    finally:
        release_tenant(token)


async def main() -> None:
    await clean()
    org_a, org_b = await seed()
    try:
        os.environ.pop("PROJECTS_ORG_VOCABULARIES", None)
        mine = await as_tenant(org_a, user(OWNER))
        check("only org-wide tags, only this tenant", [t["name"] for t in mine["tags"]], ["shared"])
        check("the tag counts open tasks across both spaces", mine["tags"][0].get("task_count"), 2)
        check("the field is listed", [f["field_key"] for f in mine["fields"]], ["cost"])
        check("the field counts tasks holding a value", mine["fields"][0].get("task_count"), 2)
        check("the type is listed", [t["name"] for t in mine["types"]], ["Spike"])
        check("the type counts its open tasks", mine["types"][0].get("task_count"), 2)
        check("an org-wide row says so", mine["types"][0]["project_id"], None)
        check("the owner may rename", mine["can_edit"], True)
        check("creating is dark while the flag is unset", mine["can_create"], False)

        os.environ["PROJECTS_ORG_VOCABULARIES"] = "1"
        check("the flag releases the create", (await as_tenant(org_a, user(OWNER)))["can_create"], True)
        os.environ.pop("PROJECTS_ORG_VOCABULARIES", None)

        member = await as_tenant(org_a, user(MEMBER, "feature:projects"))
        check("a member sees the same rows", [t["name"] for t in member["tags"]], ["shared"])
        check("a member gets no count", "task_count" in member["tags"][0], False)
        check("a member gets no field count", "task_count" in member["fields"][0], False)
        check("a member may not rename", member["can_edit"], False)
        check("a member may not create", member["can_create"], False)

        theirs = await as_tenant(org_b, user(THEM))
        check("the other tenant sees only its own", [t["name"] for t in theirs["tags"]], ["theirs"])
        check("the other tenant has no fields", theirs["fields"], [])
        check("the other tenant's tag is used by nothing", theirs["tags"][0].get("task_count"), 0)
    finally:
        await clean()
    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
