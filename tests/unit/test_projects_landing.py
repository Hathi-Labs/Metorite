"""S6c repair — the two move routes land a task the SAME way.

Spec `my_tasks_cutover.md` §5 S6c · D-PM-29 · migration 192.

Until 2026-09-23 the bulk route (`move.py`) and the narrow route
(`tasks.py`, the one My Tasks promotes through) disagreed about a cross-root
move. `landing.py` is now the one seam, and this file drives BOTH routes
through it for the two cases review named:

1. **A required field renamed between roots.** The source has `po` ("PO"),
   the destination requires `customer_po` ("PO"). The map resolves by name,
   so the task's `po` value lands as `customer_po` and the required check
   passes — on both routes, with no answer typed.
2. **`completed_at` follows the landing lane's category.** A DONE task
   promoted to a board with no closing lane is reopened; an open task
   landing in a closing lane is completed.

Plus the D-PM-29 drop entry the narrow route never wrote, and the ORDER
`land_custom_fields` fixes: an answer under the DESTINATION key is coerced
by the destination's definitions, after the map.

Hermetic, on `FakeProjectsDB`. Two SQL rules the fake cannot evaluate are
patched at the module seam: `_REMAP_TARGET_SQL` (the lane landing, via
`remap_one_status` and `_status_proposal`), which `test_projects_move_sql_
asyncpg.py` and `tests/live/live_ws39_s6c.py` cover for real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import custom_fields as pm_cf
from gateway.routes.projects import landing as pm_landing
from gateway.routes.projects import move as pm_move
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

# The modules that open a session. `custom_fields` and `landing` take the
# session they are handed and bind nothing.
MODULES = (pm_core, pm_move, pm_tree, pm_tasks)
USER = projects_user()
DONE_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture(autouse=True)
def _events(monkeypatch: pytest.MonkeyPatch) -> list:
    return silence_events(monkeypatch, MODULES)


def _field(db: FakeProjectsDB, project_id: str, key: str, name: str, **cols: Any):
    return db.seed(
        "pm_custom_fields", project_id=project_id, field_key=key, name=name,
        field_type=cols.pop("field_type", "text"), required=cols.pop("required", False),
        **cols,
    )


def _roots(db: FakeProjectsDB, *, dest_closing: bool):
    """Two roots. The source has a Done lane; the destination may not."""
    source = db.seed_project(name="My Tasks", personal_owner=None)
    s_todo = db.seed_status(source.id, name="To do", category="todo", is_default=True)
    s_done = db.seed_status(
        source.id, name="Done", category="done", is_default=False, position=40,
    )
    dest = db.seed_project(name="Launch", personal_owner=None)
    d_backlog = db.seed_status(
        dest.id, name="Backlog", category="todo", is_default=True,
    )
    d_done = (
        db.seed_status(dest.id, name="Shipped", category="done",
                       is_default=False, position=40)
        if dest_closing else None
    )
    _field(db, source.id, "po", "PO")
    _field(db, dest.id, "customer_po", "PO", required=True)
    return source, s_todo, s_done, dest, d_backlog, d_done


def _patch_lanes(monkeypatch: pytest.MonkeyPatch, landing_lane: Any) -> None:
    """The fake cannot run `_REMAP_TARGET_SQL`; the lane is decided here."""
    async def one(db: Any, *, status_id: str, owner_id: str) -> str:
        return str(landing_lane.id)

    async def proposal(db: Any, status_ids: list[str], owner_id: str):
        return [
            {"from": {"id": sid, "name": "?", "category": "?"},
             "to": {"id": str(landing_lane.id), "name": landing_lane.name,
                    "category": landing_lane.category}}
            for sid in status_ids
        ]

    monkeypatch.setattr(pm_tasks, "remap_one_status", one)
    monkeypatch.setattr(pm_move, "_status_proposal", proposal)


def _task_row(db: FakeProjectsDB, task_id: str) -> dict[str, Any]:
    row = dict(next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task_id)))
    # The fake stores JSONB the way `update_row` serialises it — as text.
    row["custom_fields"] = pm_core.from_jsonb(row.get("custom_fields"))
    return row


async def _narrow(db: FakeProjectsDB, task: Any, dest: Any, **body: Any) -> dict:
    return await pm_tasks.move_task(
        str(task.id), pm_tasks.MoveTask(project_id=str(dest.id), **body), user=USER,
    )


async def _bulk(db: FakeProjectsDB, task: Any, dest: Any, **body: Any) -> dict:
    return await pm_move.move_tasks(
        pm_move.MoveIn(task_ids=[str(task.id)], destination_project_id=str(dest.id),
                       **body),
        user=USER,
    )


ROUTES = pytest.mark.parametrize("route", [_narrow, _bulk], ids=["narrow", "bulk"])


# ── 1. The renamed required field ───────────────────────────────────────────

@ROUTES
async def test_a_required_field_renamed_between_roots_is_satisfied_by_the_map(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, route,
) -> None:
    source, s_todo, _s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_todo.id, title="Order the parts",
                        custom_fields={"po": "PO-9"})

    await route(db, task, dest)

    row = _task_row(db, str(task.id))
    assert row["custom_fields"] == {"customer_po": "PO-9"}, (
        "`po` must land as `customer_po` — by NAME — and satisfy the requirement"
    )
    assert str(row["project_id"]) == str(dest.id)
    assert str(row["root_project_id"]) == str(dest.id)


@ROUTES
async def test_a_blank_required_field_is_still_refused_with_its_name(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, route,
) -> None:
    source, s_todo, _s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_todo.id, title="No PO yet", custom_fields={})

    with pytest.raises(HTTPException) as caught:
        await route(db, task, dest)
    assert caught.value.status_code == 422
    assert "PO" in str(caught.value.detail)
    assert str(_task_row(db, str(task.id))["project_id"]) == str(source.id), (
        "a refusal leaves the task exactly where it was"
    )


async def test_the_narrow_route_takes_an_answer_under_the_DESTINATION_key(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answers merge AFTER the map, coerced by the destination's definitions.

    Merged before the map, `customer_po` was "No custom field on this
    project" — the source has no such key — which is the refusal My Tasks
    met on every promote into a root whose required field had been renamed.
    """
    source, s_todo, _s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_todo.id, title="No PO yet", custom_fields={})

    await _narrow(db, task, dest, custom_fields={"customer_po": "PO-77"})

    assert _task_row(db, str(task.id))["custom_fields"] == {"customer_po": "PO-77"}


async def test_the_narrow_route_refuses_an_answer_the_destination_does_not_define(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, s_todo, _s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_todo.id, custom_fields={"po": "PO-9"})

    with pytest.raises(HTTPException) as caught:
        await _narrow(db, task, dest, custom_fields={"typo": "x"})
    assert caught.value.status_code == 422
    assert "typo" in str(caught.value.detail)


# ── 2. completed_at follows the landing lane ────────────────────────────────

@ROUTES
async def test_a_done_task_landing_on_a_board_with_no_closing_lane_is_reopened(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, route,
) -> None:
    source, _s_todo, s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_done.id, title="Finished at home",
                        custom_fields={"po": "PO-1"}, completed_at=DONE_AT)

    await route(db, task, dest)

    row = _task_row(db, str(task.id))
    assert str(row["status_id"]) == str(d_backlog.id)
    assert row["completed_at"] is None, (
        "a task in an open lane cannot carry a completion date"
    )


@ROUTES
async def test_an_open_task_landing_in_a_closing_lane_is_completed(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch, route,
) -> None:
    source, s_todo, _s_done, dest, _d_backlog, d_done = _roots(db, dest_closing=True)
    assert d_done is not None
    _patch_lanes(monkeypatch, d_done)
    task = db.seed_task(source.id, s_todo.id, custom_fields={"po": "PO-1"},
                        completed_at=None)

    await route(db, task, dest)

    row = _task_row(db, str(task.id))
    assert str(row["status_id"]) == str(d_done.id)
    assert row["completed_at"] is not None


async def test_a_task_already_closed_keeps_its_date_in_a_closing_lane(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _s_todo, s_done, dest, _d_backlog, d_done = _roots(db, dest_closing=True)
    _patch_lanes(monkeypatch, d_done)
    task = db.seed_task(source.id, s_done.id, custom_fields={"po": "PO-1"},
                        completed_at=DONE_AT)

    await _narrow(db, task, dest)

    assert _task_row(db, str(task.id))["completed_at"] == DONE_AT


# ── 3. The drop entry (D-PM-29), on the narrow route too ────────────────────

async def test_the_narrow_route_writes_the_drop_entry_with_the_value(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, s_todo, _s_done, dest, d_backlog, _ = _roots(db, dest_closing=False)
    _field(db, source.id, "scratch", "Scratch")
    _patch_lanes(monkeypatch, d_backlog)
    task = db.seed_task(source.id, s_todo.id,
                        custom_fields={"po": "PO-1", "scratch": "keep me"})

    await _narrow(db, task, dest)

    row = _task_row(db, str(task.id))
    assert row["custom_fields"] == {"customer_po": "PO-1"}
    drops = [a for a in db.activities("system")
             if (a.get("meta") or {}).get("dropped_custom_fields")]
    assert len(drops) == 1
    assert drops[0]["meta"]["dropped_custom_fields"] == {"scratch": "keep me"}
    assert "keep me" in str(drops[0]["body"]), "the VALUE, not just the key"


# ── 4. The seam's own order, without a database ─────────────────────────────

class TestLandCustomFields:
    DEST = [
        {"field_key": "customer_po", "name": "PO", "field_type": "text",
         "options": [], "required": True},
        {"field_key": "qty", "name": "Quantity", "field_type": "number",
         "options": [], "required": False},
    ]

    @pytest.fixture(autouse=True)
    def _defs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake(db: Any, root: str) -> list[dict[str, Any]]:
            return self.DEST
        monkeypatch.setattr(pm_cf, "load_definitions", fake)

    class _Task:
        def __init__(self, values: dict[str, Any]) -> None:
            self.custom_fields = values

    async def test_map_then_answers_then_check(self) -> None:
        landed, dropped = await pm_landing.land_custom_fields(
            None, self._Task({"po": "PO-9", "old": 1}),
            dest_root="r", field_map={"po": "customer_po"}, dest_defs=self.DEST,
            answers={"qty": 3},
        )
        assert landed == {"customer_po": "PO-9", "qty": 3}, (
            "the answer is validated by the DESTINATION's definition (number)"
        )
        assert dropped == {"old": 1}

    async def test_an_answer_may_override_a_mapped_value(self) -> None:
        landed, _ = await pm_landing.land_custom_fields(
            None, self._Task({"po": "PO-9"}),
            dest_root="r", field_map={"po": "customer_po"}, dest_defs=self.DEST,
            answers={"customer_po": "PO-10"},
        )
        assert landed["customer_po"] == "PO-10"

    async def test_the_check_runs_on_the_landed_values(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await pm_landing.land_custom_fields(
                None, self._Task({"po": "PO-9"}),
                dest_root="r", field_map={}, dest_defs=self.DEST,
            )
        assert caught.value.status_code == 422
        assert caught.value.detail["error"] == "required_fields_missing"


class TestCompletionCorrection:
    class _Task:
        def __init__(self, completed_at: Any) -> None:
            self.completed_at = completed_at

    def test_no_lane_change_means_no_patch(self) -> None:
        assert pm_landing.completion_correction(self._Task(DONE_AT), None) == {}

    def test_open_lane_reopens_a_closed_task(self) -> None:
        assert pm_landing.completion_correction(self._Task(DONE_AT), "todo") == {
            "completed_at": None,
        }

    def test_closing_lane_completes_an_open_task(self) -> None:
        patch = pm_landing.completion_correction(self._Task(None), "done")
        assert patch["completed_at"] is not None

    def test_agreement_writes_nothing(self) -> None:
        assert pm_landing.completion_correction(self._Task(DONE_AT), "done") == {}
        assert pm_landing.completion_correction(self._Task(None), "active") == {}
