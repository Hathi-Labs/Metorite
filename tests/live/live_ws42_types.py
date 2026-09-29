"""WS-42 PS-2 — a task type's name clash is a 409, against a REAL Postgres (R8).

Spec `project-docs/specs/projects_settings.md` §7 row PS-2.

The first real walk of the task-type screen added "Bug" to a space that the
seed had already given a "Bug". Migration 175's `uq_pm_task_types_project_name`
fired, and the route answered a 500. The fix checks first and answers 409.
Only a database can prove it: the hermetic fake answers the new SELECT with
nothing, so it would agree with a check that never matched.

Running it, against a database whose owner the gateway has provisioned
(`EXECUTIVE_EMAILS=dev@fracktal.in`)::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant" \\
      uv run python tests/live/live_ws42_types.py

It creates one space under a recognisable name and deletes it at the end.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

os.environ["DATABASE_URL"] = os.environ.get(
    "LIVE_DATABASE_URL", "postgresql+asyncpg://acb:acb@127.0.0.1:5436/acb_tenant"
)
sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from acb_auth import UserContext, UserRole, build_access  # noqa: E402
from acb_common.db import bind_tenant  # noqa: E402
from gateway.db import get_db  # noqa: E402
from sqlalchemy import text  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from gateway.routes.projects import admin as pm_admin  # noqa: E402
from gateway.routes.projects import tree as pm_tree  # noqa: E402
from gateway.routes.projects.core import ProjectIn  # noqa: E402

ME = os.environ.get("LIVE_ME", "dev@fracktal.in")
failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def owner() -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]))


async def status_of(call) -> int:  # type: ignore[no-untyped-def]
    try:
        await call
    except HTTPException as exc:
        return exc.status_code
    return 200


async def main() -> None:
    # The owner's own organization: routes open a TENANT session, and a
    # script has no request to have bound one (MT-1c).
    db = await get_db()
    try:
        org = (
            await db.execute(
                text("SELECT organization_id FROM app_user WHERE lower(email) = lower(:e)"), {"e": ME}
            )
        ).scalar()
    finally:
        await db.close()
    if org is None:
        sys.exit(f"{ME} has no app_user row here; start the gateway with EXECUTIVE_EMAILS={ME} once.")
    bind_tenant(str(org))
    name = f"__live_ws42__ {uuid.uuid4().hex[:6]}"
    space = await pm_tree.create_node(ProjectIn(name=name, kind="project"), user=owner())
    space_id = str(space["id"] if isinstance(space, dict) else space.id)
    try:
        seeded = await pm_admin.list_types(space_id, user=owner())
        names = sorted(r["name"] for r in seeded["rows"])
        print("seeded:", names)
        clash = names[0]
        check(
            f"adding a second '{clash}' is a 409, not a 500",
            await status_of(pm_admin.create_type(space_id, pm_admin.TypeIn(name=clash), user=owner())),
            409,
        )
        made = await pm_admin.create_type(space_id, pm_admin.TypeIn(name="Spike"), user=owner())
        check("a new name is created", made["name"], "Spike")
        check(
            f"renaming Spike to the taken '{clash}' is a 409",
            await status_of(pm_admin.patch_type(made["id"], pm_admin.TypeIn(name=clash), user=owner())),
            409,
        )
        renamed = await pm_admin.patch_type(made["id"], pm_admin.TypeIn(name="Spike 2"), user=owner())
        check("renaming to a free name works", renamed["name"], "Spike 2")
        again = await pm_admin.patch_type(made["id"], pm_admin.TypeIn(name="Spike 2"), user=owner())
        check("renaming to its own name is no clash", again["name"], "Spike 2")
    finally:
        await pm_tree.delete_node(space_id, user=owner())
    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
