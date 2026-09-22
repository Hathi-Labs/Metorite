"""D-PM-36 against a REAL Postgres: ``X-Actor-Via`` lands ``meta.via`` on the row.

Spec: ``project-docs/specs/projects_ai_chat.md`` §6 · §10.2 done-when 5.

``tests/unit/test_projects_actor_via.py`` fakes the insert, on the argument
that the SQL is unchanged. That argument is true and it is still an argument
(R8: "hermetic fakes agree with whatever SQL they are handed"). This is the
half that reads the JSONB back from the table.

It seeds one project, one status and one task under a marker, binds the
tenant, calls ``record_activity`` with the ContextVar bound and unbound, and
reads ``pm_activities.meta`` back. It removes what it made in a ``finally``.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@localhost:5432/acb_r8" \\
      uv run python tests/live/live_actor_via.py
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

from acb_common.db import bind_tenant
from gateway.db import get_db
from gateway.routes.projects import core as pm_core
from sqlalchemy import text

ME = "dev@fracktal.in"
MARK = "__live_actor_via__"
failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    line = f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if not ok:
        failures.append(label)


async def main() -> None:
    org = None
    project = task = None
    async for db in get_db():
        org = (
            await db.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))
        ).scalar()
        if org is None:
            print("no organization row — seed one first")
            sys.exit(2)
        bind_tenant(str(org))
        try:
            project = (
                await db.execute(
                    text(
                        "INSERT INTO pm_projects (name, organization_id, created_by) "
                        "VALUES (:n, CAST(:o AS uuid), :by) RETURNING id"
                    ),
                    {"n": MARK, "o": str(org), "by": ME},
                )
            ).scalar()
            status = (
                await db.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id, name, category, position, is_default) "
                        "VALUES (CAST(:p AS uuid), 'To do', 'todo', 1, true) RETURNING id"
                    ),
                    {"p": str(project)},
                )
            ).scalar()
            task = (
                await db.execute(
                    text(
                        "INSERT INTO pm_tasks (project_id, root_project_id, status_id, title, created_by, task_number) "
                        "VALUES (CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, :by, 1) RETURNING id"
                    ),
                    {"p": str(project), "s": str(status), "t": MARK, "by": ME},
                )
            ).scalar()

            pm_core.ACTOR_VIA.set("chat:projects-assistant")
            row = await pm_core.record_activity(
                db,
                activity_type="comment",
                created_by=ME,
                task_id=str(task),
                body="via chat",
            )
            pm_core.ACTOR_VIA.set("")
            plain = await pm_core.record_activity(
                db,
                activity_type="comment",
                created_by=ME,
                task_id=str(task),
                body="by hand",
            )
            await db.commit()

            got = (
                await db.execute(
                    text(
                        "SELECT id, meta, created_by FROM pm_activities WHERE task_id = CAST(:t AS uuid) ORDER BY created_at"
                    ),
                    {"t": str(task)},
                )
            ).fetchall()
            by_id = {str(r.id): r for r in got}
            chat = by_id[str(row.id)]
            hand = by_id[str(plain.id)]
            meta = (
                chat.meta
                if isinstance(chat.meta, dict)
                else __import__("json").loads(chat.meta or "{}")
            )
            check("meta.via on the real row", meta.get("via"), "chat:projects-assistant")
            check("created_by stays the member", chat.created_by, ME)
            check("no via stamps nothing", hand.meta, None)
        finally:
            if task is not None:
                await db.execute(
                    text("DELETE FROM pm_activities WHERE task_id = CAST(:t AS uuid)"),
                    {"t": str(task)},
                )
                await db.execute(
                    text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": str(task)}
                )
            if project is not None:
                await db.execute(
                    text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": str(project)}
                )
            await db.commit()
        break
    if failures:
        print(f"FAILURES: {failures}")
        sys.exit(1)
    print("all live checks passed")


if __name__ == "__main__":
    asyncio.run(main())
    _ = uuid  # keep the import for callers that extend the seed
