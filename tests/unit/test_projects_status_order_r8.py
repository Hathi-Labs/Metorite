"""Task 1792 — ONE order for status lanes: stage, then position, then name.

Production drew Done as the FIRST column, and created two tasks already Done,
because every read ordered by ``position`` alone and a Done row held a low
position. This file pins the rule against a real Postgres (R8):

* ``status_order_sql`` puts a Done row at position 1 after In progress.
* ``load_default_status`` with no category never returns a closing lane.
* A set with only closing lanes refuses with a plain 422.
* A named category (and the triage path) keep their old behaviour.
* The stage list matches the TypeScript ``STATUS_CATEGORIES``.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects.core import (
    STATUS_STAGE_ORDER,
    load_default_status,
    status_order_sql,
    status_sort_key,
)
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_DB_GATE = pytest.mark.skipif(
    not _TENANT_URL,
    reason="TENANT_LADDER_DATABASE_URL unset — R8 needs a real Postgres",
)

_TS = Path(__file__).resolve().parents[2] / (
    "workbench/control_plane/src/lib/statusCategory.ts"
)


def test_stage_order_matches_the_typescript_vocabulary():
    source = _TS.read_text(encoding="utf-8")
    block = re.search(
        r"export const STATUS_CATEGORIES = \[(.*?)\] as const", source, re.S
    )
    assert block, "STATUS_CATEGORIES not found in statusCategory.ts"
    assert tuple(re.findall(r'"([a-z_]+)"', block.group(1))) == STATUS_STAGE_ORDER


def test_python_key_orders_stage_then_position_then_name():
    rows = [("done", 1, "Done"), ("todo", 5, "B"), ("todo", 5, "A"),
            ("in_progress", 2, "Doing"), ("odd", 0, "X")]
    got = [r[2] for r in sorted(rows, key=lambda r: status_sort_key(*r))]
    assert got == ["A", "B", "Doing", "Done", "X"]


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _project(db, lanes):
    """A project owning ``lanes`` = [(name, category, position)]."""
    with db.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, owns_statuses)"
                " VALUES (:n,'active','manual','ord@example.test',"
                " CAST(:o AS uuid),'Asia/Kolkata',true) RETURNING id"
            ),
            {"n": f"so-{uuid.uuid4().hex[:6]}", "o": org},
        ).scalar_one())
        for name, category, position in lanes:
            c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,"
                    " position,category) VALUES (CAST(:p AS uuid),:n,'gray',"
                    " :pos,:c)"
                ),
                {"p": pid, "n": name, "pos": position, "c": category},
            )
    return pid


def _drop(db, pid):
    with db.begin() as c:
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id ="
                       " CAST(:p AS uuid)"), {"p": pid})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": pid})


class _AsyncShim:
    """Runs the function's own SQL on a sync connection (Windows psycopg
    cannot use the Proactor loop). The statement and params are untouched."""

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, stmt, params=None):
        return self._conn.execute(stmt, params or {})


def _default(db, pid, category=None):
    with db.begin() as conn:
        return asyncio.run(load_default_status(_AsyncShim(conn), pid, category))


PROD_SHAPE = [  # Done holds the LOWEST position, as production's shared set did.
    ("Done", "done", 1),
    ("Backlog", "backlog", 10),
    ("To do", "todo", 20),
    ("In progress", "in_progress", 30),
    ("Cancelled", "cancelled", 2),
]


@_DB_GATE
def test_read_order_puts_done_after_in_progress(db):
    pid = _project(db, PROD_SHAPE)
    try:
        with db.begin() as c:
            names = [r[0] for r in c.execute(text(
                "SELECT name FROM pm_task_statuses WHERE project_id ="
                f" CAST(:p AS uuid) ORDER BY {status_order_sql()}"), {"p": pid})]
        assert names == ["Backlog", "To do", "In progress", "Done", "Cancelled"]
    finally:
        _drop(db, pid)


@_DB_GATE
def test_new_task_lands_in_backlog_not_done(db):
    pid = _project(db, PROD_SHAPE)
    try:
        assert _default(db, pid).name == "Backlog"
        assert _default(db, pid, "done").name == "Done"
        assert _default(db, pid, "todo").name == "To do"
    finally:
        _drop(db, pid)


@_DB_GATE
def test_new_task_lands_in_todo_when_there_is_no_backlog(db):
    pid = _project(db, [("Done", "done", 1), ("To do", "todo", 20),
                        ("Doing", "in_progress", 30)])
    try:
        assert _default(db, pid).name == "To do"
    finally:
        _drop(db, pid)


@_DB_GATE
def test_triage_is_never_a_default_lane(db):
    pid = _project(db, [("Inbox", "triage", 1), ("Done", "done", 2),
                        ("Backlog", "backlog", 50)])
    try:
        assert _default(db, pid).name == "Backlog"
        assert _default(db, pid, "triage").name == "Inbox"
    finally:
        _drop(db, pid)


@_DB_GATE
def test_a_set_with_only_closing_lanes_refuses(db):
    pid = _project(db, [("Done", "done", 1), ("Cancelled", "cancelled", 2)])
    try:
        with pytest.raises(HTTPException) as err:
            _default(db, pid)
        assert err.value.status_code == 422
        assert "no open status" in err.value.detail
    finally:
        _drop(db, pid)
