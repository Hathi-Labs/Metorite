"""WS-27bl — the move's resolution SQL, on the driver production actually uses.

R8, and §9.13.3 names it by hand: *"the preview's resolution query is verified
against a real Postgres, not a fake. It is the query the whole feature's
correctness rests on."*

⚠️ **Why a separate file from `test_projects_sql_asyncpg.py`.** That file's
contract is "every builder in `analytics.py` and both reads in `tree.py`", and
it carries no completeness fence for a third module. Bolting three statements
onto it would make it look like it covered `move.py` while covering only the
three somebody remembered — H-114's warning, in its own words: *"a partial
fence reads exactly like a whole one."* This file owns `move.py` and says so,
and the completeness test below fails by name when a statement is added.

⚠️ **A function-scoped engine with `NullPool`.** A module-scoped engine binds
its pool to the first test's event loop, and every later test then fails with
*"another operation is in progress"* — which reads exactly like a SQL fault and
is not one. `test_projects_sql_asyncpg.py` records the same trap.

These assert **nothing about the answer.** Either asyncpg accepts the statement
and its bound parameters, or it raises. Five live bugs shipped green because a
hermetic fake agreed with whatever SQL it was handed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "")

#: The async cases carry the asyncio mark individually. A module-level one
#: would also mark the synchronous completeness test, which pytest warns about.
pytestmark = [
    pytest.mark.skipif(
        not _URL,
        reason="TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres",
    ),
]

#: A uuid that exists nowhere. Every statement here is a READ, so a miss is a
#: valid result: what is under test is whether the driver accepts the shape.
_ABSENT = "00000000-0000-0000-0000-000000000000"

MOVE_PY = Path("apps/services/gateway/gateway/routes/projects/move.py")


def _async_url() -> str:
    return _URL.replace("+psycopg", "+asyncpg")


@pytest.fixture
async def conn():
    engine = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    async with engine.begin() as connection:
        yield connection
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_status_proposal_runs_on_asyncpg(conn):
    """The preview's central read: where every source lane would land.

    ⚠️ Binds a uuid[] and two TEXT parameters into the existing
    `_REMAP_TARGET_SQL`, which was written for a single-row call. Wrapping a
    proven fragment in `= ANY(CAST(:ids AS uuid[]))` is exactly the change that
    passes on psycopg and can refuse on asyncpg.
    """
    from gateway.routes.projects.core import _REMAP_TARGET_SQL, TRIAGE_CATEGORY

    await conn.execute(
        text(
            "SELECT old.id AS from_id, old.name AS from_name, "
            "       old.category AS from_category, "
            + _REMAP_TARGET_SQL + " AS to_id "
            "  FROM pm_task_statuses old "
            " WHERE old.id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": [_ABSENT], "owner": _ABSENT, "triage": TRIAGE_CATEGORY},
    )


@pytest.mark.asyncio
async def test_remap_one_type_runs_on_asyncpg(conn):
    """The type rule (WS-27bl), which closes the dangling-type defect.

    A correlated subquery in the WHERE clause, with two uuid casts from TEXT
    binds. Shared with `/tasks/{id}/move`, so a refusal here breaks both.
    """
    await conn.execute(
        text(
            "SELECT n.id FROM pm_task_types n "
            " WHERE n.project_id = CAST(:root AS uuid) "
            "   AND lower(btrim(n.name)) = ("
            "       SELECT lower(btrim(o.name)) FROM pm_task_types o "
            "        WHERE o.id = CAST(:tid AS uuid))"
            " ORDER BY n.name LIMIT 1"
        ),
        {"tid": _ABSENT, "root": _ABSENT},
    )


@pytest.mark.asyncio
async def test_the_type_name_lookup_runs_on_asyncpg(conn):
    await conn.execute(
        text("SELECT id, name FROM pm_task_types WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [_ABSENT]},
    )


@pytest.mark.asyncio
async def test_the_status_name_lookup_runs_on_asyncpg(conn):
    await conn.execute(
        text(
            "SELECT id, name, category FROM pm_task_statuses "
            " WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": [_ABSENT]},
    )


@pytest.mark.asyncio
async def test_the_destination_tag_read_runs_on_asyncpg(conn):
    await conn.execute(
        text("SELECT name FROM pm_tags WHERE project_id = CAST(:root AS uuid)"),
        {"root": _ABSENT},
    )


def test_every_SQL_statement_in_move_py_is_covered():
    """⚠️ The completeness fence, and the reason this file is worth having.

    Without it the suite stops being complete the first time somebody adds a
    statement to `move.py`, and a partial fence reads exactly like a whole one
    (H-114). Counts `text(` call sites in the module and compares against what
    is exercised above.

    A raise here is not a failure of the new statement. It means: add a case.
    """
    source = MOVE_PY.read_text(encoding="utf-8")
    # `text(` opens every statement in the module. The count is the contract.
    statements = len(re.findall(r"\btext\(", source))
    covered = 4  # the four `text(` sites in move.py, exercised above
    assert statements == covered, (
        f"move.py now has {statements} SQL statements and this file covers "
        f"{covered}. Add the new one as a case above — a fence that silently "
        f"stops being complete is worse than no fence."
    )
