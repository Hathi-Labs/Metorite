"""D-PM-36 against a REAL Postgres: ``X-Actor-Via`` lands ``meta.via`` on the row.

Spec: ``project-docs/specs/projects_ai_chat.md`` §6 · §10.2 done-when 5.

``tests/unit/test_projects_actor_via.py`` fakes the insert, on the argument
that the SQL is unchanged. That argument is true and it is still an argument
(R8: "hermetic fakes agree with whatever SQL they are handed"). This is the
half that reads the JSONB back from the table.

It seeds one project, one lane and one task under a marker, the way
``live_comment_threads.py`` does, binds the tenant, calls ``record_activity``
with the ContextVar bound and unbound, and reads ``pm_activities.meta`` back.
It removes what it made in a ``finally``.

Running it::

    LIVE_DATABASE_URL="postgresql+asyncpg://acb:acb@127.0.0.1:5434/acb_tenant" \\
      uv run python tests/live/live_actor_via.py
"""

import asyncio
import json
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


async def seed() -> dict:
    """One project, one lane, one task — the comment-threads seed, trimmed."""
    db = await get_db()
    try:
        org = (
            await db.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))
        ).fetchone()
        if org is None:
            raise SystemExit("no organization in this database")
        org_id = str(org.id)
        pid, sid, tid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, source, "
                "created_by, owns_statuses) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), :n, 'manual', :me, true)"
            ),
            {"id": pid, "o": org_id, "n": f"{MARK} tree", "me": ME},
        )
        await db.execute(
            text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), "
                "'To do', 1, 'todo', true)"
            ),
            {"id": sid, "p": pid},
        )
        await db.execute(
            text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "root_project_id, status_id, title, source, created_by, "
                "task_number) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :t, "
                "'manual', :me, 1)"
            ),
            {"id": tid, "o": org_id, "p": pid, "s": sid, "t": f"{MARK} task", "me": ME},
        )
        await db.commit()
        return {"org": org_id, "project": pid, "task": tid}
    finally:
        await db.close()


async def clean(made: dict) -> None:
    db = await get_db()
    try:
        for sql in (
            "DELETE FROM pm_activities WHERE task_id = CAST(:t AS uuid)",
            "DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)",
        ):
            await db.execute(text(sql), {"t": made["task"]})
        for sql in (
            "DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)",
            "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)",
        ):
            await db.execute(text(sql), {"p": made["project"]})
        await db.commit()
    finally:
        await db.close()


def _meta(value: object) -> dict | None:
    if value is None:
        return None
    return value if isinstance(value, dict) else json.loads(str(value))


async def main() -> None:
    made = await seed()
    try:
        bind_tenant(made["org"])
        db = await get_db()
        try:
            pm_core.ACTOR_VIA.set("chat:projects-assistant")
            via = await pm_core.record_activity(
                db,
                activity_type="comment",
                created_by=ME,
                task_id=made["task"],
                body=f"{MARK} via chat",
            )
            pm_core.ACTOR_VIA.set("")
            plain = await pm_core.record_activity(
                db,
                activity_type="comment",
                created_by=ME,
                task_id=made["task"],
                body=f"{MARK} by hand",
            )
            await db.commit()
            rows = (
                await db.execute(
                    text(
                        "SELECT id, meta, created_by FROM pm_activities "
                        "WHERE task_id = CAST(:t AS uuid)"
                    ),
                    {"t": made["task"]},
                )
            ).fetchall()
        finally:
            await db.close()
        by_id = {str(r.id): r for r in rows}
        chat, hand = by_id[str(via.id)], by_id[str(plain.id)]
        check(
            "meta.via on the real row",
            (_meta(chat.meta) or {}).get("via"),
            "chat:projects-assistant",
        )
        check("created_by stays the member", chat.created_by, ME)
        check("no via stamps nothing", _meta(hand.meta), None)
        check("two rows were written", len(rows), 2)
    finally:
        await clean(made)
    if failures:
        print(f"FAILURES: {failures}")
        sys.exit(1)
    print("all live checks passed")


if __name__ == "__main__":
    asyncio.run(main())
