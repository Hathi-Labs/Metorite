"""WS-39 S6f — one set of fields across My Tasks and Projects (D76), hermetic.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §4.10 · §5 S6f · **D76**.

The rule: a fact about the WORK has one home, ``pm_tasks``, and both apps
read and write it. The overlay holds only how one member holds the work.
The claims a hermetic fake can judge are pinned here:

* completion is the shared lane, so a reopen or a close in Projects moves
  the task in My Tasks with no overlay write (`effective_disposition`);
* who a WAITING task waits on is its assignees minus me (`waiting_on_for`);
* the shared start date hides a task from my inbox (`DEFERRED_CLAUSE`);
* the overlay's ``important`` and ``time_estimate_mins`` are refused, never
  dropped, at the route and at the one upsert;
* the organize estimate lands on the task, and a delegation keeps a
  deadline the task already has;
* migration 215's text, and the one number the client and the gateway
  share (``IMPORTANT_AT``).

The Postgres claims — the ARRAY subquery, ``current_date``, the backfill
run twice, People capacity reading the shared column — are in
``tests/live/live_ws39_s6f.py`` (R8).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import bulk as pm_bulk
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import planning as pm_planning
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal, pm_bulk)
ROOT = Path(__file__).resolve().parents[2]

SINCE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

ALICE = member_user("alice@fracktal.in")
BOB = member_user("bob@fracktal.in")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _team_project(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


def _assign(db: FakeProjectsDB, task_id: str, *emails: str, at: str = "") -> None:
    for n, email in enumerate(emails):
        db.seed(
            "pm_task_assignees", task_id=task_id, assignee=email,
            assigned_by="pm@fracktal.in",
            assigned_at=at or f"2026-09-0{n + 1}T09:00:00+00:00",
        )


def _move(db: FakeProjectsDB, task_id: str, status_id: str) -> None:
    """A teammate moves the lane in Projects. The overlay is not touched."""
    for row in db.rows("pm_tasks"):
        if str(row["id"]) == str(task_id):
            row["status_id"] = status_id


async def _mine(user) -> dict[str, dict]:
    rows = (await pm_personal.my_inbox(user=user, page=page())).rows
    return {r["id"]: r for r in rows}


# ── Completion is the shared lane ───────────────────────────────────────────

@pytest.mark.parametrize(
    ("stated", "category", "expected"),
    [
        ("NEXT", "done", "DONE"),       # Projects closed it: it leaves my Next.
        ("WAITING", "cancelled", "DONE"),
        ("DONE", "todo", "NEXT"),       # A teammate reopened it.
        ("DONE", "in_progress", "NEXT"),
        ("DONE", "done", "DONE"),
        ("SOMEDAY", "todo", "SOMEDAY"),
        ("TRASH", "done", "TRASH"),     # My removal is not a completion.
        (None, "todo", "NEXT"),         # Derived: assigned to me.
        (None, "backlog", "SOMEDAY"),
    ],
)
def test_the_lane_wins_over_what_i_stated(stated, category, expected) -> None:
    assert pm_personal.effective_disposition(
        stated, status_category=category, is_mine=True, has_assignee=True,
    ) == expected


async def test_a_teammate_reopens_it_and_it_is_back_in_my_next(
    db: FakeProjectsDB,
) -> None:
    project, todo, done = _team_project(db)
    task = db.seed_task(project.id, done.id, title="Ship the quote")
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id,
            member_email="alice@fracktal.in", disposition="DONE")

    assert str(task.id) not in await _mine(ALICE), "closed: not in my list"

    _move(db, task.id, todo.id)  # Bob reopens it on the board.

    row = (await _mine(ALICE))[str(task.id)]
    assert row["disposition"] == "NEXT"
    assert row["is_triaged"] is True, "I triaged it once; the review must not ask"
    stored = next(r for r in db.rows("pm_task_personal")
                  if str(r["task_id"]) == str(task.id))
    assert stored["disposition"] == "DONE", "read-side only: nothing was written"


async def test_projects_closes_it_and_it_leaves_my_next(db: FakeProjectsDB) -> None:
    project, todo, done = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Call the vendor")
    _assign(db, task.id, "alice@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id,
            member_email="alice@fracktal.in", disposition="NEXT")
    assert (await _mine(ALICE))[str(task.id)]["disposition"] == "NEXT"

    _move(db, task.id, done.id)

    assert str(task.id) not in await _mine(ALICE)
    done_view = await pm_personal.my_inbox(
        user=ALICE, include_done=True, page=page(),
    )
    assert {r["id"]: r["disposition"] for r in done_view.rows}[str(task.id)] == "DONE"


def test_every_reader_calls_the_one_rule() -> None:
    """The inbox, the planner and the AI seam: one function, no copies."""
    for rel in ("personal.py", "planning.py", "item_lens.py"):
        src = (ROOT / "apps/services/gateway/gateway/routes/projects" / rel).read_text(
            encoding="utf-8")
        assert "effective_disposition(" in src, rel
        assert "stated or derive_disposition(" not in src, rel


# ── Waiting on is the assignees ─────────────────────────────────────────────

def test_waiting_on_is_the_first_other_assignee() -> None:
    stored = {"name": "Bob Smith", "email": "bob@fracktal.in"}
    assert pm_personal.waiting_on_for(["carol@fracktal.in"], stored) == {
        "name": "carol@fracktal.in", "email": "carol@fracktal.in",
    }, "reassigned in Projects: the chase follows the assignee"
    assert pm_personal.waiting_on_for(["BOB@fracktal.in"], stored) == {
        "name": "Bob Smith", "email": "BOB@fracktal.in",
    }, "the typed name is a LABEL for the same address"
    assert pm_personal.waiting_on_for([], stored) == stored, (
        "nobody else assigned: an outside person, held only on the overlay"
    )
    assert pm_personal.waiting_on_for(None, None) is None


async def test_reassigned_in_projects_my_waiting_on_follows(
    db: FakeProjectsDB,
) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Send the drawings")
    _assign(db, task.id, "bob@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
            disposition="WAITING",
            waiting_on={"name": "Bob", "email": "bob@fracktal.in"},
            delegated_at=SINCE)
    # The WAITING arm needs Alice to see the root.
    db.seed("pm_project_grants", project_id=project.id, subject="alice@fracktal.in")

    row = (await _mine(ALICE))[str(task.id)]
    assert row["waiting_on"]["email"] == "bob@fracktal.in"

    # Projects hands it from Bob to Carol.
    db.rows("pm_task_assignees").clear()
    _assign(db, task.id, "carol@fracktal.in")

    row = (await _mine(ALICE))[str(task.id)]
    assert row["waiting_on"] == {"name": "carol@fracktal.in",
                                 "email": "carol@fracktal.in"}


async def test_a_task_i_do_not_wait_on_names_nobody(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
            disposition="NEXT",
            waiting_on={"name": "Bob", "email": "bob@fracktal.in"},
            delegated_at=SINCE)
    assert (await _mine(ALICE))[str(task.id)]["waiting_on"] is None


async def test_the_nudge_goes_to_whoever_holds_it_now(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Send the drawings")
    _assign(db, task.id, "alice@fracktal.in", "carol@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
            disposition="WAITING",
            waiting_on={"name": "Bob", "email": "bob@fracktal.in"},
            delegated_at=SINCE)

    sent = await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert sent["notified"] == ["carol@fracktal.in"]


# ── The start date is shared, and it hides the task ─────────────────────────

def test_the_deferred_clause_honours_both_dates() -> None:
    clause = pm_personal.DEFERRED_CLAUSE
    assert "p.defer_until IS NULL OR p.defer_until <= now()" in clause
    assert "t.start_date IS NULL OR t.start_date <= current_date" in clause


async def test_a_future_start_date_hides_it_from_my_inbox(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    later = db.seed_task(project.id, todo.id, title="Q4 plan", start_date="2099-01-01")
    now = db.seed_task(project.id, todo.id, title="Today", start_date="2020-01-01")
    _assign(db, later.id, "alice@fracktal.in")
    _assign(db, now.id, "alice@fracktal.in")

    default = await _mine(ALICE)
    assert str(later.id) not in default
    assert str(now.id) in default
    everything = await pm_personal.my_inbox(
        user=ALICE, include_deferred=True, page=page(),
    )
    assert str(later.id) in {r["id"] for r in everything.rows}


# ── The two retired overlay columns ─────────────────────────────────────────

@pytest.mark.parametrize("field", ["important", "time_estimate_mins"])
def test_the_overlay_route_refuses_a_retired_column(field) -> None:
    value = True if field == "important" else 30
    with pytest.raises(HTTPException) as caught:
        pm_personal.validate_overlay({field: value})
    assert caught.value.status_code == 422
    assert "D76" in caught.value.detail


@pytest.mark.parametrize("field", ["important", "time_estimate_mins"])
async def test_the_one_upsert_refuses_a_retired_column(field, db) -> None:
    with pytest.raises(ValueError, match="D76"):
        await pm_personal._upsert_personal(
            db, "00000000-0000-0000-0000-000000000001", "alice@fracktal.in",
            {field: 1},
        )


async def test_the_bulk_overlay_refuses_a_retired_column() -> None:
    with pytest.raises(HTTPException) as caught:
        pm_bulk.validate_personal(
            "personal", pm_personal.PersonalIn(time_estimate_mins=30),
        )
    assert caught.value.status_code == 422


def test_no_reader_projects_a_retired_column() -> None:
    assert "p.time_estimate_mins" not in pm_personal._MY_TASKS_SQL
    assert "p.important" not in pm_personal._MY_TASKS_SQL
    assert "time_estimate_mins" not in pm_personal._OVERLAY_PASSTHROUGH
    assert "important" not in pm_personal._OVERLAY_PASSTHROUGH


def test_the_planner_reads_the_shared_estimate_and_priority() -> None:
    select = pm_planning._PM_SELECT
    assert "t.estimate_mins           AS time_estimate_mins" in select
    assert f"coalesce(t.importance, 0) >= {pm_personal.IMPORTANT_AT}" in select
    assert "p.time_estimate_mins" not in select
    assert "p.important" not in select
    assert "tk.estimate_mins" in pm_planning._PM_RATIO_SQL


def test_important_at_is_one_number_in_both_apps() -> None:
    ts = (ROOT / "workbench/control_plane/src/app/tasks/lib/priority.ts").read_text(
        encoding="utf-8")
    found = re.search(r"export const IMPORTANT_AT = (\d+);", ts)
    assert found, "priority.ts must export IMPORTANT_AT"
    assert int(found.group(1)) == pm_personal.IMPORTANT_AT == 2


# ── Organize ────────────────────────────────────────────────────────────────

async def test_a_delegation_keeps_the_deadline_the_task_already_has(
    db: FakeProjectsDB,
) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Board deck",
                        due_at="2026-10-01T12:00:00+00:00")
    _assign(db, task.id, "alice@fracktal.in")
    db.seed("pm_project_grants", project_id=project.id, subject="org")

    await pm_personal.organize_my_task(
        str(task.id),
        pm_personal.OrganizeIn(
            kind="delegate", next_action="Draft the deck",
            due_at="2026-09-25T12:00:00+00:00", time_estimate_mins=90,
            assignee=pm_personal.OrganizeAssignee(name="Bob", email="bob@fracktal.in"),
        ),
        user=ALICE,
    )

    shared = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert str(shared["due_at"]).startswith("2026-10-01"), (
        "the team's deadline stands; the delegator's date does not replace it"
    )
    assert shared["estimate_mins"] == 90, "the one estimate, on the task"


# ── Migration 215 ───────────────────────────────────────────────────────────

def test_the_estimate_backfill_is_guarded_tenant_bound_and_drops_nothing() -> None:
    sql = (ROOT / "infra/postgres/215_pm_tasks_estimate_backfill.sql").read_text(
        encoding="utf-8")
    body = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    assert "t.estimate_mins IS NULL" in body, "only where the task has none"
    assert body.count("t.estimate_mins IS NULL") >= 2, "in the pick AND the UPDATE"
    assert "t.organization_id = p.organization_id" in body, "tenant-correct"
    assert "DISTINCT ON (p.task_id)" in body, "one value per task"
    assert "(a.task_id IS NULL)" in body, "an assignee's estimate first"
    assert "a.assigned_at NULLS LAST" in body, "then the first assignee"
    assert "p.time_estimate_mins > 0" in body, "a 0 was a clear"
    assert "schema_migrations" in body, "a replay does not refill a cleared estimate"
    assert "updated_at" not in body.split("UPDATE pm_tasks", 1)[1].split("FROM", 1)[0]
    for verb in ("DROP", "ALTER TABLE", "DELETE", "TRUNCATE"):
        assert verb not in body.upper(), verb


# ── Time spent ──────────────────────────────────────────────────────────────

def test_time_spent_is_every_members_actuals_bound_to_the_task() -> None:
    sql = pm_tasks._TIME_SPENT_SQL
    assert "FROM pm_task_personal p" in sql
    assert "p.task_id = CAST(:tid AS uuid)" in sql
    assert "t.organization_id = p.organization_id" in sql
    assert "member_email" not in sql, "everybody's actuals, not only mine"


# ── Un-checking a closed task reopens it, on every overlay door ─────────────

def test_every_open_disposition_reopens_and_only_those() -> None:
    assert pm_personal.OPEN_DISPOSITIONS == {
        "INBOX", "NEXT", "WAITING", "SOMEDAY", "PROJECT", "REFERENCE",
    }


async def test_bulk_next_on_a_completed_task_reopens_it(db: FakeProjectsDB) -> None:
    """The card checkbox, Focus mode and Undo all arrive as bulk `personal`."""
    project, todo, done = _team_project(db)
    task = db.seed_task(project.id, done.id, title="Ship the quote",
                        completed_at="2026-09-20T09:00:00+00:00")
    _assign(db, task.id, "alice@fracktal.in")
    db.seed("pm_task_personal", task_id=task.id,
            member_email="alice@fracktal.in", disposition="DONE")

    await pm_bulk.bulk_edit(
        pm_bulk.BulkIn(task_ids=[str(task.id)], action="personal",
                       personal=pm_personal.PersonalIn(disposition="NEXT")),
        user=ALICE,
    )

    shared = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert str(shared["status_id"]) == str(todo.id), "open, in the first to-do lane"
    assert shared.get("completed_at") is None
    assert (await _mine(ALICE))[str(task.id)]["disposition"] == "NEXT"
    moves = [a for a in db.activities("status_change")
             if str(a.get("task_id")) == str(task.id)]
    assert len(moves) == 1, "the timeline records the reopen"
    assert moves[0]["meta"]["to_category"] == "todo"


async def test_the_personal_patch_reopens_a_closed_task(db: FakeProjectsDB) -> None:
    project, todo, done = _team_project(db)
    task = db.seed_task(project.id, done.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="SOMEDAY"), user=ALICE,
    )
    shared = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert str(shared["status_id"]) == str(todo.id)


async def test_trash_and_a_context_leave_a_closed_task_closed(db: FakeProjectsDB) -> None:
    project, _todo, done = _team_project(db)
    task = db.seed_task(project.id, done.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(context="@home"), user=ALICE,
    )
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="TRASH"), user=ALICE,
    )
    shared = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert str(shared["status_id"]) == str(done.id)


def test_the_backfill_carries_important_up_and_never_down() -> None:
    sql = (ROOT / "infra/postgres/215_pm_tasks_estimate_backfill.sql").read_text(
        encoding="utf-8")
    body = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    promote = body[body.index("WITH flag AS"):]
    assert "SET importance = 2" in promote
    assert promote.count("(t.importance IS NULL OR t.importance < 2)") == 2, (
        "raises only an unset or lower Priority, in the pick and the UPDATE"
    )
    assert "AND flag.important" in promote, "a false flag changes nothing"
    assert "(a.task_id IS NULL)" in promote, "the assignee's flag first"
    assert "t.organization_id = p.organization_id" in promote


def test_the_ledger_guard_names_this_very_file() -> None:
    """R1 can renumber a migration at merge. The guard must follow it, or a
    renamed file never skips and a replay refills cleared estimates."""
    (path,) = (ROOT / "infra/postgres").glob("*_pm_tasks_estimate_backfill.sql")
    sql = path.read_text(encoding="utf-8")
    guard = re.search(r"WHERE filename = '([^']+)'", sql)
    assert guard, "the ledger guard is missing"
    assert guard.group(1) == path.name
    header = sql.splitlines()[0]
    assert header.startswith(f"-- {path.name} ")
