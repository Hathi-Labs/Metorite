"""R8 — a released task goes to the right lane (task 3648, owner ask 2026-11-17).

The board showed 409 tasks "In progress", and most had nobody on them:
``release_task`` kept the status. The owner's stages are:

* **Backlog**: a task that nobody started, and that is not planned soon.
* **To Do**: a task planned to run soon, and a started task that is paused or
  given back.
* **In progress**: work that moves now: a live run, or an open pull request
  that waits for review or merge.
* **Done**: merged or delivered.

``POST /projects/agent-runs/{id}/release`` (``agent_runs._settle_task_lane``)
now moves the task in the release's own transaction:

1. an open pull request keeps it in progress, in a review lane when the board
   has one;
2. started work (a handoff ``done``, or a branch, a commit or a pull request)
   puts it in the To Do lane, and **never in Backlog**;
3. nothing started puts it back in the lane that this agent's claim left;
4. a board with no To Do lane, and a task that is not in a started lane, stay as
   they are. (One open run for each task is a unique index, migration 289, so
   no other live run can hold the task.)

Against a REAL Postgres (the SQL reads ``pm_activities`` and ``pm_task_refs``),
through the real router, as the non-privileged role of the phase-4 rehearsal.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_agent_runs_r8 import (  # noqa: F401
    _DB_GATE,
    _principal,
    _rows,
    _seed_connection,
    _session,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: A board like the real one, and the lanes that the cases change.
STANDARD = (("Backlog", "backlog", 10), ("To do", "todo", 20),
            ("In progress", "in_progress", 30), ("Done", "done", 40))


def _board(p: Any, org: str, email: str, lanes: tuple[tuple[str, str, int], ...]) -> SimpleNamespace:
    """A project that owns its lanes, a grant for *email*, and the lanes."""
    project = str(uuid.uuid4())
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :o, 'lanes', :e, true)"), {"id": project, "o": org, "e": email})
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by) "
            "VALUES (:o, :p, :s, :e)"), {"o": org, "p": project, "s": email, "e": email})
        for name, category, position in lanes:
            ids[name] = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": ids[name], "o": org, "p": project, "n": name, "c": category,
                 "pos": position})
    return SimpleNamespace(project=project, lanes=ids, count=0, org=org, email=email)


def _task(p: Any, board: SimpleNamespace, lane: str) -> str:
    board.count += 1
    task = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :p, :n, :s, 'Fix the extruder', :e)"),
            {"id": task, "o": board.org, "p": board.project, "n": board.count,
             "s": board.lanes[lane], "e": board.email})
    return task


def _ref(p: Any, board: SimpleNamespace, task: str, kind: str, state: str | None = None) -> None:
    """One reference of *kind* on *task* (the agent attached it)."""
    ref = uuid.uuid4().hex[:8]
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "state, via, created_by) VALUES (:o, :t, :k, 'github', :r, :u, :s, "
            "'mcp:claude.ai:claude-code', :e)"),
            {"o": board.org, "t": task, "k": kind, "r": ref,
             "u": f"https://github.com/acme/repo/{kind}/{ref}", "s": state,
             "e": board.email})


def _lane_of(p: Any, task: str) -> tuple[str, str]:
    row = _rows(p, "SELECT s.name, s.category FROM pm_tasks t JOIN pm_task_statuses s "
                   "ON s.id = t.status_id WHERE t.id = CAST(:t AS uuid)", t=task)[0]
    return str(row[0]), str(row[1])


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 - a fixture of the same name
    email = "lanes@release-r8.test"
    conn = _seed_connection(promoted, promoted.org_a, email)
    return SimpleNamespace(
        p=promoted, email=email, org=promoted.org_a,
        agent=_principal(email, promoted.org_a, agent_connection=conn),
    )


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    return world


async def _claim_and_release(
    w: Any, task: str, *, before: Any = None, handoff: dict | None = None,
) -> dict:
    """The agent claims *task*, *before* may change it (a reference), and the
    agent releases the run. Returns the answer of the release."""
    async with _session(w.p) as call:
        claimed = await call(w.agent, "POST", f"/projects/tasks/{task}/claim", {})
        assert claimed.status_code == 200, claimed.text
        run = claimed.json()["agent_run_id"]
        assert run, claimed.json()
        if before is not None:
            before()
        body = {"handoff": handoff} if handoff else {}
        released = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", body)
        assert released.status_code == 200, released.text
        out = released.json()
        out["_call"] = (call, run)
        return out


# ── 1. An open pull request keeps the task in progress ─────────────────────


async def test_an_open_pull_request_keeps_the_task_in_progress(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(
        w, task, before=lambda: _ref(w.p, board, task, "pull_request", "open"))
    assert out["task_status"] == {
        "name": "In progress", "category": "in_progress", "moved": False,
        "reason": "open_pull_request"}
    assert _lane_of(w.p, task) == ("In progress", "in_progress")


async def test_an_open_pull_request_goes_to_the_review_lane_when_the_board_has_one(w) -> None:
    board = _board(w.p, w.org, w.email, (*STANDARD, ("In review", "in_progress", 35)))
    task = _task(w.p, board, "To do")
    out = await _claim_and_release(
        w, task, before=lambda: _ref(w.p, board, task, "pull_request", "draft"))
    assert out["task_status"]["reason"] == "open_pull_request"
    assert out["task_status"]["moved"] is True
    assert _lane_of(w.p, task) == ("In review", "in_progress")


async def test_a_merged_pull_request_is_not_an_open_one(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "To do")
    out = await _claim_and_release(
        w, task, before=lambda: _ref(w.p, board, task, "pull_request", "merged"))
    # A merged pull request is started work, and the task is not in review.
    assert out["task_status"]["reason"] == "started_work"
    assert _lane_of(w.p, task) == ("To do", "todo")


# ── 2. Started work goes to To Do, never to Backlog ────────────────────────


async def test_a_branch_is_started_work_so_the_task_goes_to_to_do(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(
        w, task, before=lambda: _ref(w.p, board, task, "branch"))
    assert out["task_status"] == {
        "name": "To do", "category": "todo", "moved": True, "reason": "started_work"}
    assert _lane_of(w.p, task) == ("To do", "todo")
    # One timeline row per move: the claim and the release.
    moves = _rows(w.p, "SELECT body FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                       "AND type = 'status_change' ORDER BY created_at, id", t=task)
    assert [str(m[0]) for m in moves] == ["Backlog → In progress", "In progress → To do"]


async def test_a_handoff_done_is_started_work(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(w, task, handoff={"done": "The module is written."})
    assert out["task_status"]["reason"] == "started_work"
    assert _lane_of(w.p, task) == ("To do", "todo")


async def test_a_started_task_never_goes_to_backlog_even_when_it_came_from_there(w) -> None:
    board = _board(w.p, w.org, w.email, (("Backlog", "backlog", 10),
                                         ("In progress", "in_progress", 30),
                                         ("Done", "done", 40)))
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(
        w, task, before=lambda: _ref(w.p, board, task, "commit"))
    # No To Do lane on this board: the task stays in progress, and says why.
    assert out["task_status"] == {
        "name": "In progress", "category": "in_progress", "moved": False,
        "reason": "no_todo_lane"}
    assert _lane_of(w.p, task)[1] != "backlog"


# ── 3. Nothing started: back to the lane before the claim ──────────────────


async def test_nothing_started_goes_back_to_backlog(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(w, task)
    assert out["task_status"] == {
        "name": "Backlog", "category": "backlog", "moved": True, "reason": "nothing_started"}
    assert _lane_of(w.p, task) == ("Backlog", "backlog")


async def test_nothing_started_goes_back_to_the_lane_of_its_name(w) -> None:
    board = _board(w.p, w.org, w.email, (("Backlog", "backlog", 10), ("Later", "todo", 15),
                                         ("Ready", "todo", 25), ("In progress", "in_progress", 30),
                                         ("Done", "done", 40)))
    task = _task(w.p, board, "Ready")
    out = await _claim_and_release(w, task)
    assert out["task_status"]["name"] == "Ready"
    assert _lane_of(w.p, task) == ("Ready", "todo")


async def test_a_task_that_was_already_in_progress_is_left_alone(w) -> None:
    """Nobody started it by this claim, and this agent made no move: the lane
    that it had before is not this agent's to restore."""
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "In progress")
    out = await _claim_and_release(w, task)
    assert out["task_status"]["moved"] is False
    assert out["task_status"]["reason"] == "no_status_before_claim"
    assert _lane_of(w.p, task) == ("In progress", "in_progress")


# ── 4. Custom lanes map by category, then by name ──────────────────────────


async def test_two_todo_lanes_take_the_one_named_like_to_do_or_next(w) -> None:
    board = _board(w.p, w.org, w.email, (("Backlog", "backlog", 10), ("Later", "todo", 15),
                                         ("Next", "todo", 25), ("In progress", "in_progress", 30),
                                         ("Done", "done", 40)))
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(w, task, before=lambda: _ref(w.p, board, task, "branch"))
    assert out["task_status"]["name"] == "Next"
    assert _lane_of(w.p, task) == ("Next", "todo")


async def test_two_todo_lanes_with_no_known_name_take_the_first_by_position(w) -> None:
    board = _board(w.p, w.org, w.email, (("Backlog", "backlog", 10), ("Lined up", "todo", 15),
                                         ("Waiting", "todo", 25), ("In progress", "in_progress", 30),
                                         ("Done", "done", 40)))
    task = _task(w.p, board, "Backlog")
    out = await _claim_and_release(w, task, before=lambda: _ref(w.p, board, task, "branch"))
    assert out["task_status"]["name"] == "Lined up"


# ── 5. What the release leaves alone ───────────────────────────────────────


async def test_a_second_release_changes_nothing_and_says_so(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    async with _session(w.p) as call:
        claimed = await call(w.agent, "POST", f"/projects/tasks/{task}/claim", {})
        run = claimed.json()["agent_run_id"]
        _ref(w.p, board, task, "branch")
        first = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", {})
        assert first.json()["task_status"]["reason"] == "started_work"
        again = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", {})
    assert again.status_code == 200
    assert again.json()["task_status"] == {
        "name": "To do", "category": "todo", "moved": False, "reason": "already_released"}
    moves = _rows(w.p, "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
                       "AND type = 'status_change'", t=task)
    assert moves[0][0] == 2


async def test_a_done_task_is_never_moved_by_a_release(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    async with _session(w.p) as call:
        claimed = await call(w.agent, "POST", f"/projects/tasks/{task}/claim", {})
        run = claimed.json()["agent_run_id"]
        # A person closes the task by hand, and the run stays open.
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) "
                           "WHERE id = CAST(:t AS uuid)"),
                      {"s": board.lanes["Done"], "t": task})
        released = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", {})
    assert released.status_code == 200, released.text
    assert released.json()["task_status"]["reason"] == "not_started_lane"
    assert _lane_of(w.p, task) == ("Done", "done")


# ── 6. A person's own move is never reversed (verifier finding F4) ─────────


def _person_move(p: Any, board: SimpleNamespace, task: str, to_lane: str, lane_from: str) -> None:
    """The task moves by hand in the app: a ``status_change`` of the SAME
    member, with no ``via`` (a person's move never carries the stamp of a
    client)."""
    import json

    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET status_id = CAST(:s AS uuid) "
                       "WHERE id = CAST(:t AS uuid)"), {"s": board.lanes[to_lane], "t": task})
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, body, meta, created_by) "
            "VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'status_change', :b, "
            "CAST(:m AS jsonb), :e)"),
            {"o": board.org, "t": task, "b": f"{lane_from} → {to_lane}", "e": board.email,
             "m": json.dumps({"from": lane_from, "to": to_lane,
                              "from_category": "todo", "to_category": "in_progress"})})


async def test_a_move_that_the_member_made_by_hand_before_the_claim_is_not_reversed(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "To do")
    _person_move(w.p, board, task, "In progress", "To do")
    # The agent claims a task that is already started (no move), then gives it
    # back with nothing done.
    out = await _claim_and_release(w, task)
    assert out["task_status"]["moved"] is False
    assert out["task_status"]["reason"] == "no_status_before_claim"
    assert _lane_of(w.p, task) == ("In progress", "in_progress")


async def test_a_move_that_the_member_made_by_hand_after_the_claim_is_not_reversed(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    async with _session(w.p) as call:
        claimed = await call(w.agent, "POST", f"/projects/tasks/{task}/claim", {})
        run = claimed.json()["agent_run_id"]
        # After the claim, the member moves it by hand (To do, then In progress).
        _person_move(w.p, board, task, "In progress", "To do")
        released = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", {})
    assert released.status_code == 200, released.text
    assert released.json()["task_status"]["reason"] == "no_status_before_claim"
    assert _lane_of(w.p, task) == ("In progress", "in_progress")


# ── 7. An archived task is left alone (verifier finding F8) ────────────────


async def test_an_archived_task_does_not_move_on_a_release(w) -> None:
    board = _board(w.p, w.org, w.email, STANDARD)
    task = _task(w.p, board, "Backlog")
    async with _session(w.p) as call:
        claimed = await call(w.agent, "POST", f"/projects/tasks/{task}/claim", {})
        run = claimed.json()["agent_run_id"]
        _ref(w.p, board, task, "branch")
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET archived_at = now() "
                           "WHERE id = CAST(:t AS uuid)"), {"t": task})
        released = await call(w.agent, "POST", f"/projects/agent-runs/{run}/release", {})
    assert released.status_code == 200, released.text
    assert released.json()["task_status"]["reason"] == "archived"
    assert _lane_of(w.p, task) == ("In progress", "in_progress")
