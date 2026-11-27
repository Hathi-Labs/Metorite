"""WS-39 SP-3a — the work style and the backlog read, on a real Postgres (R8).

Spec: ``project-docs/specs/project_management_app.md`` §13.1 and the row
SP-3a of §13.6. Migration: the file that adds ``pm_projects.work_style``,
found by CONTENT (R1). Routes: ``PATCH /projects/nodes/{id}`` and
``GET /projects/tasks?cycle=none``.

The real ``/projects`` router runs as the non-privileged role of the phase-4
rehearsal (``promoted``), on a database with the whole ladder and two
organizations. It proves:

* the column is there, NOT NULL, with the default ``flow``, and a node made
  with no style reads ``flow``;
* the CHECK refuses an empty style and a style longer than 40 characters;
* a replay of the migration file changes nothing (idempotent);
* the PATCH round trip, and that a change of style deletes no cycle and no
  membership row, so a change back shows them again;
* a member of another org gets 404 on the node, and the style stays;
* the backlog read leaves out each task in an open (planned or active)
  cycle, keeps a task whose only cycle is closed, leaves out a done task,
  and comes in priority order;
* the backlog read never reads across orgs, and answers 404 while the flag
  is off.

**Mutations this suite catches.** Copy the file aside first, and restore it
from the copy.

* M1. ``tasks.NO_OPEN_CYCLE_SQL``: drop ``AND ct.cycle_open``, so a closed
  cycle counts too -> ``test_a_task_whose_only_cycle_is_closed_is_in_the_backlog``.
* M2. ``tasks.cycle_filter_clauses``: return ``[]`` for ``none`` ->
  ``test_the_backlog_leaves_out_tasks_in_open_cycles``.
* M3. ``tasks.cycle_filter_clauses``: drop the flag check ->
  ``test_the_backlog_read_answers_404_while_the_flag_is_off``.

Each was run on 2026-11-09, and each turned its test red.

Run::

    TENANT_LADDER_DATABASE_URL=... uv run pytest tests/unit/test_project_work_style_r8.py -v -rs
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit.test_decisions import _principal
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_sprint_store_r8 import (
    _add,
    _member,
    _node,
    _one,
    _plan,
    _session,
    _task,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"


def _migration() -> Path:
    """The migration that adds ``work_style``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if "ADD COLUMN IF NOT EXISTS work_style" in p.read_text(encoding="utf-8")
        and "pm_projects" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _open_categories() -> str:
    """The open stages of the canvas's query (`backlogCanvas.OPEN_CATEGORIES`)."""
    return "triage,backlog,todo,in_progress"


def _backlog_path(node: str) -> str:
    return (
        f"/projects/tasks?project_id={node}&include_subtree=true&cycle=none"
        f"&status_category={_open_categories()}&top_level=true"
        "&sort=importance&direction=desc&page_size=100"
    )


def _style_of(p: Any, node: str) -> str:
    return _one(p, "SELECT work_style FROM pm_projects WHERE id = CAST(:n AS uuid)", n=node)[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.ana = "ana@style-r8.test"
    ns.yan = "yan@style-r8-b.test"
    _member(promoted, promoted.org_a, ns.ana)
    _member(promoted, promoted.org_b, ns.yan)
    ns.space = _node(promoted, promoted.org_a, ns.ana, grants=(ns.ana,))
    ns.proj = _node(promoted, promoted.org_a, ns.ana, parent=ns.space)
    ns.space_b = _node(promoted, promoted.org_b, ns.yan, grants=(ns.yan,))
    ns.u_ana = _principal(ns.ana, promoted.org_a)
    ns.u_yan = _principal(ns.yan, promoted.org_b)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The flag on for both orgs, no cycle left, and both nodes back on Flow."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "projects_sprints_orgs",
                        f"{world.p.org_a},{world.p.org_b}")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_cycles"))
        c.execute(text("UPDATE pm_projects SET work_style = 'flow'"))
    return world


@_DB_GATE
class TestTheWorkStyleOnARealDatabase:
    async def test_the_column_has_the_default_flow_and_a_length_check(self, w):
        col = _one(
            w.p,
            "SELECT is_nullable, column_default, data_type FROM information_schema.columns"
            " WHERE table_name = 'pm_projects' AND column_name = 'work_style'",
        )
        assert col is not None, "the migration did not run"
        assert col.is_nullable == "NO"
        assert col.data_type == "text"
        assert "'flow'" in col.column_default
        check = _one(
            w.p,
            "SELECT pg_get_constraintdef(oid) AS cdef, convalidated FROM pg_constraint"
            " WHERE conname = 'pm_projects_work_style'",
        )
        assert check is not None and check.convalidated
        assert "char_length(work_style)" in check.cdef
        # A node made with no style reads Flow, through the database default.
        made = _node(w.p, w.p.org_a, w.ana, grants=(w.ana,))
        assert _style_of(w.p, made["id"]) == "flow"

    async def test_the_check_refuses_an_empty_and_a_long_style(self, w):
        for bad in ("", "x" * 41):
            with pytest.raises(IntegrityError), w.p.admin_engine.begin() as c:
                c.execute(
                    text("UPDATE pm_projects SET work_style = :s WHERE id = CAST(:n AS uuid)"),
                    {"s": bad, "n": w.proj["id"]},
                )
        assert _style_of(w.p, w.proj["id"]) == "flow"

    async def test_a_replay_of_the_migration_changes_nothing(self, w):
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_projects SET work_style = 'sprints' WHERE id = CAST(:n AS uuid)"),
                      {"n": w.proj["id"]})
        sql = _migration().read_text(encoding="utf-8")
        with w.p.admin_engine.connect() as c:
            with c.connection.dbapi_connection.cursor() as cur:
                cur.execute(sql)
            c.commit()
        assert _style_of(w.p, w.proj["id"]) == "sprints"
        count = _one(w.p, "SELECT count(*) FROM pg_constraint WHERE conname = 'pm_projects_work_style'")[0]
        assert count == 1

    async def test_the_patch_round_trip_and_a_change_never_deletes(self, w):
        task = _task(w.p, w.p.org_a, w.ana, w.proj)
        async with _session(w.p) as call:
            got = await call(w.u_ana, "PATCH", f"/projects/nodes/{w.space['id']}", {"work_style": "sprints"})
            assert got.status_code == 200, got.text
            assert got.json()["work_style"] == "sprints"
            read = await call(w.u_ana, "GET", f"/projects/nodes/{w.space['id']}")
            assert read.json()["work_style"] == "sprints"
            cycle = await _plan(call, w.u_ana, w.space["id"])
            assert (await _add(call, w.u_ana, cycle, task)).status_code == 200

            for style in ("flow", "sprints"):
                got = await call(w.u_ana, "PATCH", f"/projects/nodes/{w.space['id']}", {"work_style": style})
                assert got.status_code == 200, got.text
                # The cycle and its member stay through each change (§13.1 rule 2).
                assert _one(w.p, "SELECT count(*) FROM pm_cycles WHERE id = CAST(:c AS uuid)", c=cycle)[0] == 1
                assert _one(
                    w.p,
                    "SELECT count(*) FROM pm_cycle_tasks WHERE cycle_id = CAST(:c AS uuid)"
                    " AND task_id = CAST(:t AS uuid)",
                    c=cycle, t=task,
                )[0] == 1
            listed = await call(w.u_ana, "GET", f"/projects/nodes/{w.space['id']}/cycles")
            assert [c["id"] for c in listed.json()["cycles"]] == [cycle]

    async def test_a_bad_style_is_a_422_and_writes_nothing(self, w):
        async with _session(w.p) as call:
            for body in ({"work_style": "kanban"}, {"work_style": "shape_up"},
                         {"work_style": None}, {"work_style": "x" * 50_000},
                         {"work_style": {"a": [1, 2, 3]}}):
                got = await call(w.u_ana, "PATCH", f"/projects/nodes/{w.space['id']}", body)
                assert got.status_code == 422, (body, got.text)
        assert _style_of(w.p, w.space["id"]) == "flow"

    async def test_a_member_of_another_org_gets_404_and_the_style_stays(self, w):
        async with _session(w.p) as call:
            got = await call(w.u_yan, "PATCH", f"/projects/nodes/{w.space['id']}", {"work_style": "sprints"})
            assert got.status_code == 404, got.text
            read = await call(w.u_yan, "GET", f"/projects/nodes/{w.space['id']}")
            assert read.status_code == 404
        assert _style_of(w.p, w.space["id"]) == "flow"

    # ── The backlog read ────────────────────────────────────────────────

    async def test_the_backlog_leaves_out_tasks_in_open_cycles(self, w):
        p = w.p
        planned_t = _task(p, p.org_a, w.ana, w.proj)
        active_t = _task(p, p.org_a, w.ana, w.proj)
        loose_hi = _task(p, p.org_a, w.ana, w.proj)
        loose_lo = _task(p, p.org_a, w.ana, w.space)
        done_t = _task(p, p.org_a, w.ana, w.proj, category="done")
        with p.admin_engine.begin() as c:
            # Important beats not important (the shared priority rank).
            c.execute(text("UPDATE pm_tasks SET importance = 3 WHERE id = CAST(:t AS uuid)"), {"t": loose_hi})
            c.execute(text("UPDATE pm_tasks SET importance = 0 WHERE id = CAST(:t AS uuid)"), {"t": loose_lo})
        async with _session(p) as call:
            planned = await _plan(call, w.u_ana, w.space["id"], name="Planned")
            active = await _plan(call, w.u_ana, w.space["id"], name="Active", goal="Run")
            for cycle, task in ((planned, planned_t), (active, active_t)):
                assert (await _add(call, w.u_ana, cycle, task)).status_code == 200
            started = await call(w.u_ana, "POST", f"/projects/cycles/{active}/start", {})
            assert started.status_code == 200, started.text
            got = await call(w.u_ana, "GET", _backlog_path(w.space["id"]))
            assert got.status_code == 200, got.text
            ids = [r["id"] for r in got.json()["rows"]]
            assert planned_t not in ids, "a task of a planned cycle is not in the backlog"
            assert active_t not in ids, "a task of the active cycle is not in the backlog"
            assert done_t not in ids, "a done task is not open work"
            assert loose_hi in ids and loose_lo in ids
            assert ids.index(loose_hi) < ids.index(loose_lo), "priority order"
            assert got.json()["total"] == len(ids)
            # The same read with no cycle filter holds every open task.
            every = await call(w.u_ana, "GET", _backlog_path(w.space["id"]).replace("&cycle=none", ""))
            assert {planned_t, active_t, loose_hi} <= {r["id"] for r in every.json()["rows"]}

    async def test_a_task_whose_only_cycle_is_closed_is_in_the_backlog(self, w):
        """A closed cycle is history (SP-1 fix round 3). Its row stays with
        ``cycle_open`` false, and the task is back in the backlog."""
        p = w.p
        task = _task(p, p.org_a, w.ana, w.proj)
        async with _session(p) as call:
            cycle = await _plan(call, w.u_ana, w.space["id"], name="Old", goal="Old goal")
            assert (await _add(call, w.u_ana, cycle, task)).status_code == 200
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_tasks SET status_id = CAST(:s AS uuid) WHERE id = CAST(:t AS uuid)"),
                    {"s": w.proj["lanes"]["done"], "t": task})
            assert (await call(w.u_ana, "POST", f"/projects/cycles/{cycle}/start", {})).status_code == 200
            closed = await call(w.u_ana, "POST", f"/projects/cycles/{cycle}/close", {"choices": []})
            assert closed.status_code == 200, closed.text
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE pm_tasks SET status_id = CAST(:s AS uuid) WHERE id = CAST(:t AS uuid)"),
                    {"s": w.proj["lanes"]["todo"], "t": task})
            row = _one(p, "SELECT cycle_open FROM pm_cycle_tasks WHERE task_id = CAST(:t AS uuid)", t=task)
            assert row is not None and row[0] is False
            got = await call(w.u_ana, "GET", _backlog_path(w.space["id"]))
            assert task in [r["id"] for r in got.json()["rows"]]

    async def test_the_backlog_read_never_reads_across_orgs(self, w):
        p = w.p
        mine = _task(p, p.org_a, w.ana, w.proj)
        theirs = _task(p, p.org_b, w.yan, w.space_b)
        async with _session(p) as call:
            # Another org's node is a 404, never an empty list.
            other = await call(w.u_yan, "GET", _backlog_path(w.space["id"]))
            assert other.status_code == 404, other.text
            # With no node, each member reads only its own org.
            yan = await call(w.u_yan, "GET", "/projects/tasks?cycle=none&page_size=100")
            assert yan.status_code == 200, yan.text
            yan_ids = {r["id"] for r in yan.json()["rows"]}
            assert theirs in yan_ids and mine not in yan_ids
            ana = await call(w.u_ana, "GET", "/projects/tasks?cycle=none&page_size=100")
            assert theirs not in {r["id"] for r in ana.json()["rows"]}

    async def test_the_backlog_read_answers_404_while_the_flag_is_off(self, w, monkeypatch):
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "projects_sprints_orgs", str(w.p.org_b))
        async with _session(w.p) as call:
            got = await call(w.u_ana, "GET", _backlog_path(w.space["id"]))
            assert got.status_code == 404, got.text
            # The plain list is not dark.
            plain = await call(w.u_ana, "GET", f"/projects/tasks?project_id={w.space['id']}")
            assert plain.status_code == 200
            # And an unknown value is a 422 where the flag is on.
            bad = await call(w.u_yan, "GET", "/projects/tasks?cycle=closed")
            assert bad.status_code == 422, bad.text


def test_the_suite_finds_one_migration() -> None:
    sql = _migration().read_text(encoding="utf-8")
    assert re.search(r"ADD COLUMN IF NOT EXISTS work_style TEXT NOT NULL DEFAULT 'flow'", sql)
