"""WS-46 P5 — the chat's calendar of a project reads its subprojects too.

Spec: ``project-docs/specs/projects_agent_parity.md`` §15, the P4 finding.

``GET /projects/calendar`` reads the named node ALONE unless the caller sends
``include_subtree=true``. The app's calendar always sends it
(``page.tsx`` ``loadMonth``). The ``calendar`` tool never did, so the chat's
calendar of a space showed no work from its subprojects. The tool now sends
the flag with a project, true unless the member asks for the node alone.

Two halves:

1. The wire, hermetic: what the tool sends, for each shape of call.
2. R8: the clause the route applies under the flag, on the driver production
   runs (asyncpg), over a real space with a real subproject. A fake agrees
   with whatever SQL it is handed, so the subtree claim is proved on Postgres.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import fake_gateway

PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _empty(_call: dict) -> Any:
    return {"rows": [], "truncated": False}


def _read(calls: list[dict], path: str) -> dict:
    return next(c for c in calls if c["path"] == path)["params"]


# ── 1. The wire ─────────────────────────────────────────────────────────────


async def test_a_project_calendar_reads_the_subtree_by_default(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _empty)
    await skill_projects.calendar("2026-10-05", "2026-10-12", project_id=PROJECT)
    assert _read(calls, "/projects/calendar") == {
        "from": "2026-10-05",
        "to": "2026-10-12",
        "project_id": PROJECT,
        "include_subtree": True,
    }


async def test_the_member_can_ask_for_the_node_alone(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _empty)
    await skill_projects.calendar(
        "2026-10-05", "2026-10-12", project_id=PROJECT, include_subtree=False
    )
    assert _read(calls, "/projects/calendar")["include_subtree"] is False


async def test_no_project_sends_no_subtree_flag(monkeypatch) -> None:
    """With no node the route reads every visible task, and the flag means
    nothing. So the tool does not send it."""
    calls = fake_gateway(monkeypatch, _empty)
    await skill_projects.calendar("2026-10-05", "2026-10-12")
    assert "include_subtree" not in _read(calls, "/projects/calendar")


async def test_my_calendar_is_unchanged(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _empty)
    await skill_projects.calendar("2026-10-05", "2026-10-12", project_id=PROJECT, mine=True)
    assert _read(calls, "/projects/my/calendar") == {"start": "2026-10-05", "end": "2026-10-12"}


# ── 2. R8 — the subtree clause on a real Postgres ───────────────────────────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_r8 = pytest.mark.skipif(
    not _TENANT_URL,
    reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres.",
)


@pytest.fixture(scope="module")
def db():
    pytest.importorskip("sqlalchemy")
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def tree(db):
    """A space, its subproject, a grandchild, and a second space.

    One dated task sits in each. The second space is the control: a subtree
    walk that is bounded by nothing returns its task too.
    """
    from sqlalchemy import text

    made: dict[str, str] = {}
    with db.begin() as c:
        org = str(
            c.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1")).scalar_one()
        )
        for number, (key, parent) in enumerate(
            (("space", None), ("sub", "space"), ("grand", "sub"), ("other", None)), start=1
        ):
            pid = str(
                c.execute(
                    text(
                        "INSERT INTO pm_projects (name, status, source, created_by,"
                        " organization_id, timezone, parent_project_id, owns_statuses)"
                        " VALUES (:n, 'active', 'manual', 'cal@example.test',"
                        " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:par AS uuid), true)"
                        " RETURNING id"
                    ),
                    {
                        "n": f"cal-{key}-{uuid.uuid4().hex[:6]}",
                        "o": org,
                        "par": made[parent] if parent else None,
                    },
                ).scalar_one()
            )
            made[key] = pid
            sid = str(
                c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                        " category) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo')"
                        " RETURNING id"
                    ),
                    {"p": pid},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
                    " created_by, organization_id, task_number, due_at)"
                    " VALUES (:t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid),"
                    " 'cal@example.test', CAST(:o AS uuid), :num,"
                    " TIMESTAMPTZ '2026-10-07 10:00:00+00')"
                ),
                {
                    "t": f"task-{key}",
                    "p": pid,
                    "r": made["other" if key == "other" else "space"],
                    "s": sid,
                    "o": org,
                    "num": number,
                },
            )
    yield made
    with db.begin() as c:
        ids = list(made.values())
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"), {"p": ids}
        )
        c.execute(
            text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
            {"p": ids},
        )
        for key in ("grand", "sub", "space", "other"):
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": made[key]})


async def _titles(clause: str, pid: str) -> set[str]:
    """The tasks a clause selects, read through asyncpg as production reads."""
    pytest.importorskip("asyncpg")
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(
        _TENANT_URL.replace("+psycopg", "+asyncpg"), future=True, poolclass=NullPool
    )
    try:
        async with eng.connect() as conn:
            rows = await conn.execute(
                text(f"SELECT t.title FROM pm_tasks t WHERE {clause}"), {"pid": pid}
            )
            return {r.title for r in rows}
    finally:
        await eng.dispose()


@_r8
async def test_the_subtree_clause_reads_every_level_below_the_space(tree) -> None:
    from gateway.routes.projects.calendar import _subtree_clause

    titles = await _titles(_subtree_clause(), tree["space"])
    assert {"task-space", "task-sub", "task-grand"} <= titles
    assert "task-other" not in titles, "the subtree walk left its space"


@_r8
async def test_the_node_alone_misses_the_subproject_work(tree) -> None:
    """The route's default, and the chat's calendar before P5."""
    titles = await _titles("t.project_id = CAST(:pid AS uuid)", tree["space"])
    assert "task-space" in titles
    assert not {"task-sub", "task-grand"} & titles
