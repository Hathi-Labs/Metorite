"""R8 for the space rule's one SQL statement (2026-10-26).

``tree._OWN_LIVE_TASKS_SQL`` counts the live tasks that sit on one node
itself. ``tree.move_node`` refuses a project that holds any when a move would
make it a space. The hermetic fake answers it by its alias
(``_projects_fakes``), so this file runs the real statement on asyncpg, on the
driver production uses, and checks the answer on seeded rows.

Each case runs in one transaction that rolls back, so it leaves nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        not _URL, reason="TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres",
    ),
]


def _async_url() -> str:
    return _URL.replace("+psycopg", "+asyncpg")


@pytest.fixture
async def conn():
    engine = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _org(conn) -> str:
    """A tenant for the seeded rows. A fresh one, inside the rolled-back
    transaction, so a database with no organization row still runs it."""
    org = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO organization (id, slug, display_name) VALUES"
        " (CAST(:id AS uuid), :slug, :slug)"), {"id": org, "slug": f"r8-{org[:8]}"})
    return org


@pytest.mark.asyncio
async def test_the_count_runs_on_asyncpg_and_counts_only_live_own_tasks(conn) -> None:
    from gateway.routes.projects.tree import _OWN_LIVE_TASKS_SQL

    absent = (await conn.execute(
        text(_OWN_LIVE_TASKS_SQL), {"pid": "00000000-0000-0000-0000-000000000000"},
    )).first()
    assert absent.own_live_tasks == 0

    org = await _org(conn)
    tag = uuid.uuid4().hex[:8]

    async def project(name: str, parent: str | None) -> str:
        return str((await conn.execute(text(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses, parent_project_id) VALUES (:n, 'active', 'manual',"
            " 'r8@example.test', CAST(:o AS uuid), 'UTC', true, CAST(:p AS uuid))"
            " RETURNING id"), {"n": f"{name}-{tag}", "o": org, "p": parent})).scalar_one())

    space = await project("space", None)
    hr = await project("hr", space)
    child = await project("hiring", hr)
    lane = str((await conn.execute(text(
        "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
        " organization_id) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo',"
        " CAST(:o AS uuid)) RETURNING id"), {"p": hr, "o": org})).scalar_one())

    async def task(project_id: str, n: int, archived: bool) -> None:
        await conn.execute(text(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number, archived_at) VALUES"
            " (:t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid),"
            " 'r8@example.test', CAST(:o AS uuid), :n,"
            " CASE WHEN :a THEN now() END)"),
            {"t": f"t{n}", "p": project_id, "r": space, "s": lane, "o": org,
             "n": 990_000 + n, "a": archived})

    await task(hr, 1, False)
    await task(hr, 2, False)
    await task(hr, 3, True)
    await task(child, 4, False)

    got = (await conn.execute(text(_OWN_LIVE_TASKS_SQL), {"pid": hr})).first()
    # Two live tasks on HR itself. The archived one and the subproject's are
    # not HR's own live work.
    assert got.own_live_tasks == 2
