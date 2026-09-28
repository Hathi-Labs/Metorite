"""Subtasks S4 (D-PM-38) — My Tasks and the Calendar, the gateway half.

Spec: ``project-docs/specs/project_management_app.md`` §12.9 (the rule) and
§11.40 (the build record).

Hermetic. Each rule here is also driven against a real Postgres by
``tests/live/live_ws39_subtask_planner.py`` (R8). This file pins the parts a
fake can judge: which helper a door calls, what it passes on, and which
clause a query composes.

* **B9** — a new step lands in the parent's lane when that lane is open, and
  states NEXT when the member stated NEXT on the parent.
* **B1** — the planner reads subtasks: no ``parent_task_id IS NULL`` on any
  of its five queries, and CANDIDATE refuses a parent with an open step of
  mine.
* **B13** — the AI seam reads subtasks on every read, so its counts and its
  lists agree.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from gateway.routes.projects import core, item_lens, personal, planning

# ── B9: the step's lane and the step's stated disposition ──────────────────


class _Rows:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DB:
    """Answers the lane read and the overlay read, and records both."""

    def __init__(self, *, lane=None, stated=None):
        self.lane = lane
        self.stated = stated
        self.sql: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM pm_task_statuses" in sql:
            return _Rows(self.lane)
        if "FROM pm_task_personal" in sql:
            row = None if self.stated is None else SimpleNamespace(
                disposition=self.stated)
            return _Rows(row)
        raise AssertionError(f"unexpected SQL: {sql}")


PARENT = SimpleNamespace(
    id="parent-1", project_id="proj-1", root_project_id="root-1",
    status_id="lane-parent", organization_id="org-1",
)
FIRST = SimpleNamespace(id="lane-first", category="backlog")


@pytest.fixture
def first_lane(monkeypatch):
    """`load_default_status` answers the first lane, and records the call."""
    calls: list[tuple] = []

    async def load_default_status(db, owner, category=None):
        calls.append((owner, category))
        return FIRST

    async def status_owner_id(db, project_id):
        return f"owner-of-{project_id}"

    # The rule lives in `core.parent_lane_status` (§11.42), and it calls the
    # resolver by its `core` name. The `personal` name is faked too, so a
    # `step_status` that skips the seam and asks the resolver is caught.
    monkeypatch.setattr(core, "load_default_status", load_default_status)
    monkeypatch.setattr(personal, "load_default_status", load_default_status)
    monkeypatch.setattr(personal, "status_owner_id", status_owner_id)
    return calls


@pytest.mark.parametrize("category", ["todo", "in_progress", "backlog"])
async def test_a_step_lands_in_the_parents_open_lane(first_lane, category):
    lane = SimpleNamespace(id="lane-parent", category=category)
    got = await personal.step_status(_DB(lane=lane), PARENT)
    assert got is lane
    assert first_lane == [], "an open parent lane needs no resolver"


@pytest.mark.parametrize("category", ["done", "cancelled", "triage"])
async def test_a_closed_or_triage_parent_lane_falls_back_to_the_resolver(
    first_lane, category,
):
    lane = SimpleNamespace(id="lane-parent", category=category)
    got = await personal.step_status(_DB(lane=lane), PARENT)
    assert got is FIRST
    # The ONE resolver (D79), on the step's own status set, with no category.
    assert first_lane == [("owner-of-proj-1", None)]


@pytest.mark.parametrize(("stated", "want"), [
    ("NEXT", {"disposition": "NEXT"}),
    ("SOMEDAY", None),
    ("WAITING", None),
    ("INBOX", None),
    (None, None),
])
async def test_a_step_states_next_only_under_a_next_parent(stated, want):
    db = _DB(stated=stated)
    assert await personal.step_overlay(db, "Me@Fracktal.in", PARENT) == want


async def test_add_subtasks_hands_the_lane_and_the_overlay_to_the_capture(
    monkeypatch, first_lane,
):
    """`_add_subtasks` is the one step door. It must pass the parent's lane
    and the inherited NEXT to `create_personal_task` for EVERY step."""
    lane = SimpleNamespace(id="lane-parent", category="todo")
    made: list[tuple] = []

    async def create_personal_task(db, email, project_id, root_id, status_id,
                                   values, overlay=None):
        made.append((status_id, values["parent_task_id"], overlay))
        return SimpleNamespace(id=f"child-{len(made)}")

    async def touch_task(db, task_id):
        return None

    monkeypatch.setattr(personal, "create_personal_task", create_personal_task)
    monkeypatch.setattr(personal, "touch_task", touch_task)
    ids = await personal._add_subtasks(
        _DB(lane=lane, stated="NEXT"), "me@fracktal.in", PARENT,
        ["one", "  ", "two"],
    )
    assert ids == ["child-1", "child-2"]
    assert made == [
        ("lane-parent", "parent-1", {"disposition": "NEXT"}),
        ("lane-parent", "parent-1", {"disposition": "NEXT"}),
    ]


def test_the_my_tasks_step_door_is_mounted():
    """`lens.ts` MY_ROUTES.steps posts here. `test_client_route_contract`
    compares the literal with the mounted routes as well."""
    paths = {
        (getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", ()) or ())))
        for r in personal.router.routes
    }
    assert any(
        p.endswith("/my/tasks/{task_id}/subtasks") and "POST" in m
        for p, m in paths
    )


# ── B1: the planner reads subtasks ─────────────────────────────────────────

_PLANNER_WHERES = {
    "TODAY": planning._PM_TODAY_WHERE,
    "CARRY": planning._PM_CARRY_WHERE,
    "CANDIDATE": planning._PM_CANDIDATE_WHERE,
    "OVERDUE": planning._PM_OVERDUE_WHERE,
    "BUSY": planning._PM_BUSY_WHERE,
}


@pytest.mark.parametrize("name", sorted(_PLANNER_WHERES))
def test_no_planner_query_drops_subtasks(name):
    """A scheduled subtask is a block on my grid, so it is busy time. A
    planner query that drops subtasks counts its slot as free."""
    assert "parent_task_id IS NULL" not in _PLANNER_WHERES[name]


def test_the_step_read_is_mine_alive_and_only_steps():
    where = planning._PM_MY_STEPS_WHERE
    assert "t.parent_task_id IS NOT NULL" in where
    assert planning._PM_MINE in where
    assert planning._PM_ALIVE in where


async def test_candidate_refuses_only_a_parent_with_a_NEXT_step_of_mine(
    monkeypatch,
):
    """The rule is on the EFFECTIVE disposition, so it runs in Python. Only a
    NEXT step takes its parent out: an INBOX, SOMEDAY or WAITING step cannot
    be scheduled, and the parent then stays the unit to plan."""
    calls: list[tuple[str, bool]] = []
    cand = [SimpleNamespace(id="p1"), SimpleNamespace(id="p2"),
            SimpleNamespace(id="solo")]
    steps = [
        SimpleNamespace(id="s1", parent_task_id="p1", disposition="NEXT"),
        SimpleNamespace(id="s2", parent_task_id="p2", disposition="SOMEDAY"),
    ]

    async def _rows(self, db, uid, where, params, keep):
        if where == planning._PM_CANDIDATE_WHERE:
            calls.append(("candidate", keep("NEXT")))
            return cand
        assert where == planning._PM_MY_STEPS_WHERE
        calls.append(("steps", keep("SOMEDAY")))
        return [s for s in steps if keep(s.disposition)]

    monkeypatch.setattr(type(planning.LENS_SOURCE), "_rows", _rows)
    got = await planning.LENS_SOURCE.candidates(None, "me@fracktal.in")
    assert [r.id for r in got] == ["p2", "solo"]
    assert calls == [("candidate", True), ("steps", False)]


def test_no_planner_query_filters_on_the_parent():
    """BUSY, TODAY, CARRY and OVERDUE read the block itself, and CANDIDATE
    leaves the step rule to `candidates`. A parent that has a block is busy,
    whatever its steps say."""
    for name, where in _PLANNER_WHERES.items():
        assert "parent_task_id" not in where, name


# ── B13: the AI seam reads subtasks on every read ──────────────────────────


@pytest.fixture
def captured(monkeypatch):
    wheres: dict[str, str] = {}

    async def _items(self, db, uid, where="", **params):
        wheres[inspect.stack()[1].function] = where
        return []

    async def projects_for(self, db, uid):
        return []

    async def member_today(db, uid, at):
        return at.date()

    monkeypatch.setattr(item_lens._PmLens, "_items", _items)
    monkeypatch.setattr(item_lens._PmLens, "projects_for", projects_for)
    monkeypatch.setattr(item_lens, "member_today", member_today)
    return wheres


async def test_every_ai_read_keeps_subtasks(captured):
    lens = item_lens.PM_ITEMS
    await lens.open_items(None, "me@fracktal.in", 10)
    await lens.siblings(None, "me@fracktal.in", "proj-1", "task-1", 10)
    await lens.context_less_actionables(None, "me@fracktal.in", 10)
    await lens.insight_counts(None, "me@fracktal.in")
    assert set(captured) == {
        "open_items", "siblings", "context_less_actionables", "insight_counts",
    }
    for read, where in captured.items():
        assert "t.parent_task_id IS NULL" not in where.split("ORDER BY")[0], read


def test_open_items_takes_no_top_level_switch():
    """The switch was the one door that read My Tasks without its steps."""
    sig = inspect.signature(item_lens._PmLens.open_items)
    assert "top_level" not in sig.parameters


# ── The step door (review of #492) ─────────────────────────────────────────


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def door(monkeypatch):
    """`add_my_steps` with the session, the visibility and the helper faked.
    `load_visible_task` refuses: the parent is not visible."""
    from fastapi import HTTPException

    wrote: list[str] = []

    async def load_visible_task(db, vis, task_id):
        raise HTTPException(status_code=404, detail="Task not found")

    async def resolve_visibility(db, user):
        return object()

    async def _add_subtasks(db, email, parent, titles):
        wrote.extend(titles)
        return ["x"]

    monkeypatch.setattr(personal, "_tenant_session", _Session)
    monkeypatch.setattr(personal, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(personal, "load_visible_task", load_visible_task)
    monkeypatch.setattr(personal, "_add_subtasks", _add_subtasks)
    return wrote


def _user():
    from acb_auth import UserContext, UserRole, build_access
    return UserContext(email="me@fracktal.in", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


async def test_a_parent_i_cannot_see_is_404_and_writes_nothing(door):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        await personal.add_my_steps(
            "hidden", personal.StepsIn(titles=["sneak"]), user=_user())
    assert err.value.status_code == 404
    assert door == []


async def test_the_step_door_is_capped_at_max_batch(door):
    from fastapi import HTTPException
    many = [f"step {n}" for n in range(personal.MAX_BATCH + 1)]
    with pytest.raises(HTTPException) as err:
        await personal.add_my_steps(
            "p", personal.StepsIn(titles=many), user=_user())
    assert err.value.status_code == 422
    assert str(personal.MAX_BATCH) in err.value.detail
    assert door == []
