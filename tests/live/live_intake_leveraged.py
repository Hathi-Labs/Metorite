"""A capture writes Leveraged, against a REAL Postgres (H-196, R8).

`IntakeIn` had no `leveraged` field until H-196, and pydantic dropped the key
without a word. The hermetic suite (`test_projects_intake.py`) proves the
route hands the value to `insert_row`. Only a database proves the rest: that
`pm_tasks.leveraged` exists after a fresh replay of the ladder (migration
218), that asyncpg binds a Python bool to it, and that an omitted value
leaves the column default, `false`, and not NULL.

Running it, against a database built fresh from `01_schema.sql` and
`scripts/apply_migrations.sh`::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:<pw>@localhost:5432/acb_r8" \\
      uv run python tests/live/live_intake_leveraged.py
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

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant
from gateway.db import get_db
from gateway.routes.projects import intake as pm_intake
from sqlalchemy import text

ME = "dev@fracktal.in"
MARK = "__live_intake_leveraged__"
failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def owner() -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, access=build_access(["*"]))


async def seed() -> dict:
    db = await get_db()
    try:
        org = (await db.execute(text(
            "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id, pid = str(org.id), str(uuid.uuid4())
        await db.execute(text("DELETE FROM app_user WHERE email = :me"), {"me": ME})
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, display_name) "
            "VALUES (:me, CAST(:o AS uuid), :n)"),
            {"me": ME, "o": org_id, "n": f"{MARK} runner"})
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, "
            "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :n, 'manual', :me, true)"),
            {"id": pid, "o": org_id, "n": MARK, "me": ME})
        await db.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), :s, :s)"), {"p": pid, "s": ME})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, "
            "category, is_default) VALUES (gen_random_uuid(), "
            "CAST(:p AS uuid), 'To do', 1, 'todo', true)"), {"p": pid})
        await db.commit()
        return {"org": org_id, "project": pid}
    finally:
        await db.close()


async def stored(task_id: str):
    db = await get_db()
    try:
        return (await db.execute(text(
            "SELECT leveraged, importance FROM pm_tasks WHERE id = CAST(:i AS uuid)"),
            {"i": task_id})).fetchone()
    finally:
        await db.close()


async def cleanup(pid: str) -> None:
    db = await get_db()
    try:
        for sql in (
            "DELETE FROM pm_activities WHERE task_id IN (SELECT id FROM "
            "pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
            "DELETE FROM pm_intake WHERE task_id IN (SELECT id FROM "
            "pm_tasks WHERE root_project_id = CAST(:p AS uuid))",
            "DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)",
        ):
            await db.execute(text(sql), {"p": pid})
        await db.execute(text(
            "DELETE FROM app_user WHERE email = :me AND display_name = :n"),
            {"me": ME, "n": f"{MARK} runner"})
        await db.commit()
    finally:
        await db.close()


async def main():
    made = await seed()
    bind_tenant(made["org"])
    try:
        for label, kwargs, want in (
            ("a stated true is stored", {"leveraged": True, "importance": 2}, (True, 2)),
            ("a stated false is stored", {"leveraged": False, "importance": 0}, (False, 0)),
            ("an omitted flag leaves the default false, not NULL", {}, (False, None)),
        ):
            out = await pm_intake.capture_intake(
                pm_intake.IntakeIn(project_id=made["project"], title=f"{MARK} {label}",
                                   **kwargs),
                user=owner(),
            )
            row = await stored(out["task"]["id"])
            check(label, (row.leveraged, row.importance), want)
            check(f"{label} (the answer)", out["task"]["leveraged"], want[0])
    finally:
        await cleanup(made["project"])
    print("FAILED: " + ", ".join(failures) if failures else "ALL OK")
    sys.exit(1 if failures else 0)


asyncio.run(main())
