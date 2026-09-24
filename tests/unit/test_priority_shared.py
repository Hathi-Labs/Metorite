"""D78 — the priority matrix is ONE shared answer on the task, on the SERVER.

Owner decision 2026-09-24. It amends D76. Projects and My Tasks show one
priority system, the matrix (important x urgent x leveraged):

* Important is ``pm_tasks.importance >= 2`` (``IMPORTANT_AT``).
* Leveraged is ``pm_tasks.leveraged`` (migration 218).
* Urgent derives from ``due_at``, and is never stored.

The overlay's ``important`` and ``leveraged`` are no longer read or written.
A write that names one is refused by name.

This file pins the server half. The Postgres half, the rank ORDER BY on a
real database, is check 5b of ``tests/live/live_ws39_s6f.py`` (R8).
"""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import merge as pm_merge
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import planning
from gateway.routes.projects.automation import PATCHABLE_FIELDS
from gateway.routes.projects.recurrence import CARRIED_FIELDS
from gateway.routes.projects.tasks import _TRACKED_TASK_FIELDS
from gateway.routes.tasks import calendar
from gateway.routes.tasks.priority import (
    CELL_META,
    IMPORTANT_AT,
    PriorityInputs,
    cell_for_inputs,
    important_from_importance,
    priority_cell,
)

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "workbench/control_plane/src/app/tasks/lib/priority.ts"


# ── The threshold ────────────────────────────────────────────────────────────

def test_the_important_threshold_matches_the_client() -> None:
    """R7: one number, two languages. If they drift, the list and the planner
    disagree about one task again."""
    src = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"export const IMPORTANT_AT = (\d+);", src)
    assert match, "IMPORTANT_AT is gone from priority.ts"
    assert int(match.group(1)) == IMPORTANT_AT == 2


def test_the_d76_seed_is_gone() -> None:
    from gateway.routes.tasks import priority

    assert not hasattr(priority, "seeded_important")
    assert not hasattr(priority, "ORG_PRIORITY_SEED")


@pytest.mark.parametrize(
    ("importance", "expected"),
    [(None, None), (0, False), (1, False), (2, True), (3, True)],
)
def test_important_from_importance(importance, expected) -> None:
    assert important_from_importance(importance) is expected


def test_the_cell_takes_the_shared_inputs_directly() -> None:
    far = datetime.now(tz=UTC) + timedelta(days=30)
    assert priority_cell(important=True, leveraged=False, due_at=far) == "important"
    # NULL importance means nobody judged it. The matrix reads not important.
    assert priority_cell(important=None, leveraged=False, due_at=far) == "low-priority"
    assert priority_cell(important=None, leveraged=True, due_at=far) == "speculative-bet"


# ── The planner ─────────────────────────────────────────────────────────────

def _brief_row(**kw) -> SimpleNamespace:
    base = dict(
        id="t", title="t", time_estimate_mins=30, energy=None,
        importance=None, leveraged=None, deep_work=None, due_at=None,
        context=None,
    )
    return SimpleNamespace(**{**base, **kw})


def test_the_planner_brief_reads_the_task() -> None:
    now = datetime.now(tz=UTC)
    brief = calendar._candidate_brief
    assert brief(_brief_row(importance=2), now)["important"] is True
    assert brief(_brief_row(importance=3), now)["important"] is True
    assert brief(_brief_row(importance=1), now)["important"] is False
    assert brief(_brief_row(importance=None), now)["important"] is False
    assert brief(_brief_row(leveraged=True), now)["leveraged"] is True
    assert brief(_brief_row(leveraged=None), now)["leveraged"] is False
    # A stale overlay `important` on the row is NOT read any more.
    assert brief(_brief_row(important=True), now)["important"] is False


def test_the_planner_row_derives_important_from_the_task() -> None:
    row = SimpleNamespace(
        **{k: None for k in planning._PM_PASSTHROUGH},
        leveraged=True, stated_disposition=None, status_category="todo",
        assignee_count=1,
    )
    row.importance = 2
    out = planning._pm_row(row)
    assert out.important is True
    assert out.leveraged is True
    assert out.importance == 2


def test_the_planner_select_reads_the_task_not_the_overlay() -> None:
    select = planning._PM_SELECT
    assert "t.importance, t.leveraged" in select
    assert "p.important" not in select
    assert "p.leveraged" not in select
    assert "p.deep_work" in select
    assert "org_priority" not in select
    assert "importance" in planning._PM_PASSTHROUGH


def test_a_high_task_outranks_a_normal_one_in_the_fallback() -> None:
    """`_rank_fallback` orders the day when the LLM is off."""
    now = datetime.now(tz=UTC)

    def brief(tid, **kw):
        return calendar._candidate_brief(_brief_row(id=tid, title=tid, **kw), now)

    ranked = calendar._rank_fallback([
        brief("normal", importance=1),
        brief("high", importance=2),
        brief("bet", importance=0, leveraged=True),
    ])
    assert [c["id"] for c in ranked] == ["bet", "high", "normal"]


# ── The my-lens projection ─────────────────────────────────────────────────

def test_my_list_reads_the_matrix_off_the_task() -> None:
    assert "p.important" not in pm_personal._MY_TASKS_SQL
    assert "p.leveraged" not in pm_personal._MY_TASKS_SQL
    assert "important" not in pm_personal._OVERLAY_PASSTHROUGH
    assert "leveraged" not in pm_personal._OVERLAY_PASSTHROUGH
    task: dict = {}
    pm_personal._apply_overlay(task, SimpleNamespace(
        importance=3, leveraged=None, p_important=False, p_leveraged=True,
    ))
    assert task["important"] is True
    assert task["leveraged"] is False
    task = {}
    pm_personal._apply_overlay(task, SimpleNamespace(importance=None, leveraged=True))
    # Tri-state: NULL importance means nobody judged the task.
    assert task["important"] is None
    assert task["leveraged"] is True


def test_the_ai_lens_reads_the_matrix_off_the_task() -> None:
    from gateway.routes.projects import item_lens

    assert "important" not in item_lens._OVERLAY
    assert "leveraged" not in item_lens._OVERLAY


# ── The refusal ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("field", "names"),
    [("important", "`importance`"), ("leveraged", "pm_tasks.leveraged")],
)
async def test_the_personal_patch_refuses_the_matrix_flags(field, names) -> None:
    """PATCH /projects/tasks/{id}/personal answers 422 BEFORE any database
    work, and the message names where the flag lives now."""
    with pytest.raises(HTTPException) as caught:
        await pm_personal.set_personal(
            "00000000-0000-0000-0000-000000000001",
            pm_personal.PersonalIn(**{field: True}),
            user=SimpleNamespace(email="alice@fracktal.in"),
        )
    assert caught.value.status_code == 422
    assert names in caught.value.detail
    assert "D78" in caught.value.detail


# ── The shared column, on every task path ──────────────────────────────────

def test_leveraged_is_a_task_field_everywhere_importance_is() -> None:
    assert "leveraged" in pm_core.TaskIn.model_fields
    assert "leveraged" in pm_core.TaskModel.model_fields
    assert "leveraged" in _TRACKED_TASK_FIELDS
    assert "leveraged" in PATCHABLE_FIELDS
    assert "leveraged" in CARRIED_FIELDS


def test_a_null_leveraged_reads_as_false() -> None:
    row = SimpleNamespace(
        id="t", project_id="p", root_project_id="p", status_id="s",
        title="t", source="manual", leveraged=None,
    )
    assert pm_core.row_to_dict(row, pm_core.TaskModel)["leveraged"] is False


def test_a_merge_is_leveraged_when_any_task_was() -> None:
    def task(**kw):
        base = dict(
            description=None, tags=[], importance=None, estimate_mins=None,
            start_date=None, due_at=None, leveraged=None,
        )
        return SimpleNamespace(**{**base, **kw})

    out = pm_merge._fold_scalars(task(), [task(leveraged=True)])
    assert out.get("leveraged") is True
    out = pm_merge._fold_scalars(task(leveraged=True), [task()])
    assert "leveraged" not in out
    out = pm_merge._fold_scalars(task(), [task(leveraged=False)])
    assert "leveraged" not in out


# ── The sort ────────────────────────────────────────────────────────────────

def _eval_rank(important: bool, urgent: bool, leveraged: bool) -> int:
    """Evaluate `PRIORITY_RANK_SQL` for one input, without a database.

    The three input fragments become Python booleans, and each WHEN arm is
    evaluated in order, the way Postgres evaluates CASE.
    """
    sql = pm_core.PRIORITY_RANK_SQL
    for fragment, value in (
        (pm_core._IMP, important), (pm_core._LEV, leveraged), (pm_core._URG, urgent),
    ):
        sql = sql.replace(fragment, str(value))
    arms = re.findall(r"WHEN (.+?) THEN (\d)", sql)
    assert len(arms) == 6, sql
    for cond, rank in arms:
        if eval(cond.replace(" AND ", " and ")):
            return 8 - int(rank)
    assert "ELSE 7" in sql
    return 8 - 7


@pytest.mark.parametrize(
    ("important", "urgent", "leveraged"),
    list(itertools.product((True, False), repeat=3)),
)
def test_the_rank_sql_agrees_with_the_matrix(important, urgent, leveraged) -> None:
    want = CELL_META[cell_for_inputs(PriorityInputs(
        important=important, urgent=urgent, leveraged=leveraged))][0]
    assert _eval_rank(important, urgent, leveraged) == 8 - want


def test_the_rank_sql_reads_the_shared_columns() -> None:
    assert pm_core._IMP == "(COALESCE(t.importance, 0) >= 2)"
    assert pm_core._LEV == "COALESCE(t.leveraged, false)"
    assert pm_core._URG == (
        "(t.due_at IS NOT NULL AND t.due_at <= now() + interval '48 hours')"
    )
    # The 48 hours is the urgent window the Python matrix uses.
    from gateway.routes.tasks.priority import DEFAULT_URGENT_WINDOW_HOURS

    assert f"'{DEFAULT_URGENT_WINDOW_HOURS} hours'" in pm_core._URG


def test_the_importance_sort_key_orders_by_the_matrix() -> None:
    """The KEY stays `importance`: clients and saved views name it. DESC is
    most pressing first, and the tiebreak stays last."""
    frag = pm_core.TASK_SORTS["importance"]
    assert frag.startswith(pm_core.PRIORITY_RANK_SQL + " {dir}")
    assert frag.endswith(pm_core.SORT_TIEBREAK)
    assert "t.importance {dir}" not in frag


def test_my_work_breaks_a_due_date_tie_by_the_matrix() -> None:
    src = Path(pm_me.__file__).read_text(encoding="utf-8")
    assert "{PRIORITY_RANK_SQL} DESC" in src
    assert "t.importance DESC" not in src
