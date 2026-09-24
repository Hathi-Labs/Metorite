"""WS-39 S6a — the CRUD tail's three gateway routes, hermetic.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 S6a · D73.

    POST /projects/my/tasks/batch               many captures, one transaction
    POST /projects/my/tasks/{id}/organize       one clarify decision, atomically
    POST /projects/tasks/bulk  action=personal  my overlay on a selection

The claims worth pinning are the ones a hermetic fake CAN judge — which
seam each route goes through, what it refuses before writing, and what it
writes where. The transaction claims (a failing 4th capture rolls back the
first 3; a failing subtask insert rolls back the overlay) are Postgres
claims and live in ``tests/live/live_ws39_s6a.py`` (R8).

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

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
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal, pm_bulk)

ALICE = member_user("alice@fracktal.in")
BOB = member_user("bob@fracktal.in")
OWNER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    # The clarification memory is advisory and reaches an LLM; it is not what
    # these tests pin, and it must not be what makes them slow.
    monkeypatch.setattr(
        "gateway.routes.tasks.task_memory.remember_decision_background",
        lambda **kwargs: None,
    )
    return fake


def _team_project(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo


def _assign(db: FakeProjectsDB, task_id: str, *emails: str) -> None:
    for email in emails:
        db.seed(
            "pm_task_assignees", task_id=task_id, assignee=email,
            assigned_by="owner@fracktal.in",
        )


async def _captured(db: FakeProjectsDB, title: str = "A thought") -> dict:
    """One task in Alice's personal root, the way capture puts it there."""
    return await pm_personal.capture(
        pm_personal.CaptureIn(title=title), user=ALICE,
    )


def _overlay(db: FakeProjectsDB, task_id: str, email: str) -> dict | None:
    return next(
        (
            r for r in db.rows("pm_task_personal")
            if str(r["task_id"]) == str(task_id) and r["member_email"] == email
        ),
        None,
    )


def _assert_capture_overlay_untouched(db: FakeProjectsDB, task_id: str) -> None:
    """A refused decision wrote nothing: the overlay is still the row the
    capture STATED (S8a, §13.5a decision 4 — `disposition = 'INBOX'`,
    nothing clarified), not the decision's."""
    row = _overlay(db, task_id, "alice@fracktal.in")
    assert row is not None
    assert row["disposition"] == "INBOX"
    assert row.get("next_action") is None
    assert row.get("clarified_at") is None
    assert row.get("waiting_on") is None


# ── Batch capture ───────────────────────────────────────────────────────────

async def test_batch_captures_in_order_through_the_one_capture_path(
    db: FakeProjectsDB,
) -> None:
    """Three thoughts, one request, the same rows `capture` would have made:
    self-assigned, in the personal root, numbered in order."""
    out = await pm_personal.capture_batch(
        pm_personal.BatchCaptureIn(items=[
            pm_personal.CaptureIn(title="One"),
            pm_personal.CaptureIn(title="Two", notes="with a body"),
            pm_personal.CaptureIn(title="Three", context="@calls"),
        ]),
        user=ALICE,
    )

    assert [r["title"] for r in out.rows] == ["One", "Two", "Three"]
    assert out.total == 3
    tasks = db.rows("pm_tasks")
    assert [t["task_number"] for t in tasks] == [1, 2, 3]
    assert {t["created_by"] for t in tasks} == {"alice@fracktal.in"}
    assert len(db.rows("pm_task_assignees")) == 3
    # The rows come back in `/my/inbox`'s shape — the overlay projected, not
    # the bare `pm_tasks` row `capture` answers with.
    assert out.rows[1]["description"] == "with a body"
    assert out.rows[2]["context"] == "@calls"
    assert "is_triaged" in out.rows[0]
    # ONE commit for the whole batch.
    assert db.committed == 1


async def test_a_blank_title_is_refused_before_any_row_is_written(
    db: FakeProjectsDB,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await pm_personal.capture_batch(
            pm_personal.BatchCaptureIn(items=[
                pm_personal.CaptureIn(title="Fine"),
                pm_personal.CaptureIn(title="   "),
            ]),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert db.rows("pm_tasks") == []
    assert db.rows("pm_projects") == [], "not even the personal root was minted"


async def test_the_batch_is_bounded(db: FakeProjectsDB) -> None:
    too_many = [pm_personal.CaptureIn(title=f"t{i}")
                for i in range(pm_personal.MAX_BATCH + 1)]
    with pytest.raises(HTTPException) as caught:
        await pm_personal.capture_batch(
            pm_personal.BatchCaptureIn(items=too_many), user=ALICE,
        )
    assert caught.value.status_code == 422
    assert db.rows("pm_tasks") == []


async def test_an_empty_batch_is_a_422_not_an_empty_200(db: FakeProjectsDB) -> None:
    with pytest.raises(HTTPException) as caught:
        await pm_personal.capture_batch(
            pm_personal.BatchCaptureIn(items=[]), user=ALICE,
        )
    assert caught.value.status_code == 422


# ── The personal child ──────────────────────────────────────────────────────

async def test_a_personal_child_is_private_like_its_root(db: FakeProjectsDB) -> None:
    """`personal_owner` at depth 1 — the first writer of it below a root.

    The team tree filters `personal_owner IS NULL`, so the child must carry
    the owner or it appears beside Sales in the Projects app.
    """
    _team_project(db)
    child = await pm_personal.ensure_personal_child(db, "alice@fracktal.in", "Kitchen reno")

    roots = [p for p in db.rows("pm_projects") if p.get("personal_owner")]
    assert len(roots) == 2, "the root was minted first, then the child"
    row = next(p for p in db.rows("pm_projects") if str(p["id"]) == str(child.id))
    assert row["personal_owner"] == "alice@fracktal.in"
    assert row["parent_project_id"] is not None
    # Its own grant, like the root — and the ROOT's lanes, not a copy: one
    # lane vocabulary per member, so a move into the child never remaps.
    assert [g["subject"] for g in db.rows("pm_project_grants")
            if str(g["project_id"]) == str(child.id)] == ["alice@fracktal.in"]
    assert row["owns_statuses"] is False
    assert not [s for s in db.rows("pm_task_statuses")
                if str(s["project_id"]) == str(child.id)]
    assert await pm_core.status_owner_id(db, str(child.id)) == str(row["parent_project_id"])

    tree = await pm_tree.get_tree(user=OWNER)
    assert [r["name"] for r in tree["rows"]] == ["Sales"]


async def test_a_child_needs_a_name(db: FakeProjectsDB) -> None:
    with pytest.raises(HTTPException) as caught:
        await pm_personal.ensure_personal_child(db, "alice@fracktal.in", "  ")
    assert caught.value.status_code == 422


# ── Organize ────────────────────────────────────────────────────────────────

async def test_next_with_subtasks_writes_the_overlay_and_the_children(
    db: FakeProjectsDB,
) -> None:
    """The ordinary decision: NEXT, a context, and three steps under it —
    each an ordinary `pm_tasks` row in the same project, assigned to me."""
    task = await _captured(db, "Plan the offsite")

    out = await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(
            kind="next", next_action="Book the venue", context="@computer",
            energy="high", time_estimate_mins=45,
            subtasks=["Shortlist venues", "", "Call the top two"],
        ),
        user=ALICE,
    )

    overlay = _overlay(db, task["id"], "alice@fracktal.in")
    assert overlay["disposition"] == "NEXT"
    assert overlay["next_action"] == "Book the venue"
    assert overlay["context"] == "@computer"
    assert overlay["energy"] == "high"
    # D77: the clarify card's Estimate is the task's ONE estimate — the
    # column People capacity reads — and the overlay no longer holds one.
    shared = next(t for t in db.rows("pm_tasks") if str(t["id"]) == task["id"])
    assert shared["estimate_mins"] == 45
    assert overlay.get("time_estimate_mins") is None
    assert overlay["is_two_minute"] is False
    assert overlay["clarified_at"] is not None

    children = [t for t in db.rows("pm_tasks")
                if str(t.get("parent_task_id")) == task["id"]]
    assert [c["title"] for c in children] == ["Shortlist venues", "Call the top two"]
    assert {str(c["project_id"]) for c in children} == {task["project_id"]}
    assert {str(c["organization_id"]) for c in children} == {str(db.organization_id)}
    assigned = {str(a["task_id"]) for a in db.rows("pm_task_assignees")}
    assert all(str(c["id"]) in assigned for c in children), "self-assigned"
    # Answered in the inbox's shape, with the roll-up the children changed.
    assert out["disposition"] == "NEXT"
    assert out["subtask_count"] == 2
    assert db.committed == 2, "capture, then ONE commit for the decision"


async def test_delegate_goes_through_the_move_seam_and_records_the_wait(
    db: FakeProjectsDB,
) -> None:
    """Three facts in one transaction: Bob is the assignee (shared), I am
    waiting on him (mine), since now (mine — 188's CHECK)."""
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "alice@fracktal.in")

    out = await pm_personal.organize_my_task(
        str(task.id),
        pm_personal.OrganizeIn(
            kind="delegate", next_action="Draft it",
            assignee=pm_personal.OrganizeAssignee(name="Bob", email="bob@fracktal.in"),
            due_at="2026-10-01T00:00:00+00:00",
        ),
        user=ALICE,
    )

    assignees = {a["assignee"] for a in db.rows("pm_task_assignees")
                 if str(a["task_id"]) == str(task.id)}
    assert assignees == {"bob@fracktal.in"}, "REPLACED, as lensDelegateItem does"
    overlay = _overlay(db, str(task.id), "alice@fracktal.in")
    assert overlay["disposition"] == "WAITING"
    assert overlay["delegated_at"] is not None
    assert "bob@fracktal.in" in str(overlay["waiting_on"])
    # No promise invented: `expected_by` stays NULL, `due_at` is the task's.
    assert overlay.get("expected_by") is None
    row = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert row["due_at"] is not None
    # The assignment left the same trace the single-task route leaves.
    assert len(db.activities("assignment")) == 1
    assert out["disposition"] == "WAITING"


async def test_delegating_inside_my_personal_root_is_refused_by_the_assign_guard(
    db: FakeProjectsDB,
) -> None:
    """Owner directive 2026-08-26, through the ONE seam: a colleague cannot be
    put on a task in my private tree. And because it is one transaction, the
    overlay is not written either."""
    task = await _captured(db, "Mine, privately")

    with pytest.raises(HTTPException) as caught:
        await pm_personal.organize_my_task(
            task["id"],
            pm_personal.OrganizeIn(
                kind="delegate", next_action="Do it",
                assignee=pm_personal.OrganizeAssignee(name="Bob", email="bob@fracktal.in"),
            ),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "personal project" in str(caught.value.detail)
    _assert_capture_overlay_untouched(db, task["id"])


async def test_do_now_completes_for_the_project_not_only_for_me(
    db: FakeProjectsDB,
) -> None:
    """§13.5a decision 1. The two-minute rule ends in a completed task, and
    completion is the SHARED status moving into the done lane."""
    project, todo = _team_project(db)
    done = next(s for s in db.rows("pm_task_statuses") if s["name"] == "Done")
    task = db.seed_task(project.id, todo.id, title="Reply to the mail")
    _assign(db, task.id, "alice@fracktal.in")

    out = await pm_personal.organize_my_task(
        str(task.id), pm_personal.OrganizeIn(kind="do-now"), user=ALICE,
    )

    row = next(t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id))
    assert str(row["status_id"]) == str(done["id"])
    assert row["completed_at"] is not None
    assert len(db.activities("status_change")) == 1
    overlay = _overlay(db, str(task.id), "alice@fracktal.in")
    assert overlay["disposition"] == "DONE"
    assert overlay["is_two_minute"] is True
    assert out["disposition"] == "DONE"


async def test_calendar_sets_the_shared_deadline_and_my_hard_date(
    db: FakeProjectsDB,
) -> None:
    task = await _captured(db, "Dentist")

    await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(
            kind="calendar", next_action="Go", due_at="2026-10-02T09:00:00+00:00",
        ),
        user=ALICE,
    )

    row = next(t for t in db.rows("pm_tasks") if str(t["id"]) == task["id"])
    assert row["due_at"] is not None
    overlay = _overlay(db, task["id"], "alice@fracktal.in")
    assert overlay["disposition"] == "NEXT"
    assert overlay["is_hard_date"] is True


async def test_project_mints_a_personal_child_and_files_the_task_there(
    db: FakeProjectsDB,
) -> None:
    """`kind="project"`: a child of my root named for the outcome, private,
    and the task moved into it through the move seam."""
    _team_project(db)
    task = await _captured(db, "Redo the kitchen")

    out = await pm_personal.organize_my_task(
        task["id"],
        pm_personal.OrganizeIn(
            kind="project", next_action="Measure the room",
            outcome="Kitchen renovated by March",
            subtasks=["Measure", "Get quotes"],
        ),
        user=ALICE,
    )

    child = next(p for p in db.rows("pm_projects")
                 if p.get("name") == "Kitchen renovated by March")
    assert child["personal_owner"] == "alice@fracktal.in"
    assert str(child["parent_project_id"]) == task["project_id"]
    row = next(t for t in db.rows("pm_tasks") if str(t["id"]) == task["id"])
    assert str(row["project_id"]) == str(child["id"]), "moved into the child"
    # The steps followed the task into the child.
    children = [t for t in db.rows("pm_tasks")
                if str(t.get("parent_task_id")) == task["id"]]
    assert {str(c["project_id"]) for c in children} == {str(child["id"])}
    assert out["project_id"] == str(child["id"])
    assert out["disposition"] == "NEXT"
    # And the Projects app still lists only the team's work.
    tree = await pm_tree.get_tree(user=OWNER)
    assert [r["name"] for r in tree["rows"]] == ["Sales"]


async def test_a_team_task_cannot_become_a_private_project(
    db: FakeProjectsDB,
) -> None:
    """D62 through the move seam: a task on a team board is not taken into a
    private tree, and the refusal rolls the minted child back with it."""
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Team work")
    _assign(db, task.id, "alice@fracktal.in")
    before = len(db.rows("pm_projects"))

    with pytest.raises(HTTPException) as caught:
        await pm_personal.organize_my_task(
            str(task.id),
            pm_personal.OrganizeIn(
                kind="project", next_action="Start", outcome="Mine now",
            ),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "personal" in str(caught.value.detail)
    # The fake commits only on a clean exit, like the real seam: nothing
    # from the refused request survives.
    assert db.committed == 0
    assert _overlay(db, str(task.id), "alice@fracktal.in") is None
    del before  # the fake keeps the uncommitted rows; the live test proves the rollback


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (dict(kind="sideways"), "Unknown kind"),
        (dict(kind="next"), "next_action is required"),
        (dict(kind="delegate", next_action="x"), "assignee is required"),
        (dict(kind="project", next_action="x"), "outcome is required"),
        (dict(kind="calendar", next_action="x"), "due_at is required"),
    ],
)
async def test_the_decision_rules_are_400s_with_the_old_messages(
    db: FakeProjectsDB, payload: dict, message: str,
) -> None:
    """The same words `routes/tasks/items.py` answered, so the Clarify card's
    error handling did not learn a second vocabulary at the cutover."""
    task = await _captured(db)
    with pytest.raises(HTTPException) as caught:
        await pm_personal.organize_my_task(
            task["id"], pm_personal.OrganizeIn(**payload), user=ALICE,
        )
    assert caught.value.status_code == 400
    assert message in str(caught.value.detail)
    _assert_capture_overlay_untouched(db, task["id"])


async def test_somebody_elses_task_is_a_404(db: FakeProjectsDB) -> None:
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "bob@fracktal.in")
    db.tables["pm_project_grants"] = []
    with pytest.raises(HTTPException) as caught:
        await pm_personal.organize_my_task(
            str(task.id), pm_personal.OrganizeIn(kind="trash"), user=ALICE,
        )
    assert caught.value.status_code == 404


# ── Bulk personal ───────────────────────────────────────────────────────────

async def test_bulk_personal_writes_my_overlay_on_every_task_i_can_see(
    db: FakeProjectsDB,
) -> None:
    """Two members, one task, two dispositions — the overlay's whole reason.
    Bulk must not collapse that: Alice's sweep writes Alice's rows only."""
    project, todo = _team_project(db)
    one = db.seed_task(project.id, todo.id, title="One")
    two = db.seed_task(project.id, todo.id, title="Two")
    _assign(db, one.id, "alice@fracktal.in", "bob@fracktal.in")
    _assign(db, two.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(one.id), pm_personal.PersonalIn(disposition="WAITING"), user=BOB,
    )

    out = await pm_bulk.bulk_edit(
        pm_bulk.BulkIn(
            task_ids=[str(one.id), str(two.id), "00000000-0000-0000-0000-00000000dead"],
            action="personal",
            personal=pm_personal.PersonalIn(disposition="SOMEDAY", context="@home"),
        ),
        user=ALICE,
    )

    assert out["applied"] == 2
    assert [r["changed"] for r in out["results"]] == [["personal"], ["personal"]]
    assert out["skipped"] == [
        {"task_id": "00000000-0000-0000-0000-00000000dead", "reason": "not_found"},
    ]
    assert _overlay(db, str(one.id), "alice@fracktal.in")["disposition"] == "SOMEDAY"
    assert _overlay(db, str(two.id), "alice@fracktal.in")["context"] == "@home"
    assert _overlay(db, str(one.id), "bob@fracktal.in")["disposition"] == "WAITING"
    # The board did not move, and the shared row was not touched.
    assert not [s for s, _ in db.calls if s.upper().startswith("UPDATE PM_TASKS")]


async def test_bulk_personal_refuses_DONE_and_names_the_complete_route(
    db: FakeProjectsDB,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(
                task_ids=["t1"], action="personal",
                personal=pm_personal.PersonalIn(disposition="DONE"),
            ),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "/complete" in str(caught.value.detail)
    assert db.statements == [], "refused before the database was opened"


@pytest.mark.parametrize(
    "payload",
    [
        dict(action="personal"),
        dict(action="personal", personal=pm_personal.PersonalIn()),
        dict(action="archive", personal=pm_personal.PersonalIn(disposition="NEXT")),
        dict(personal=pm_personal.PersonalIn(disposition="NEXT")),
        dict(action="personal", personal=pm_personal.PersonalIn(disposition="LATER")),
        dict(action="personal", personal=pm_personal.PersonalIn(disposition="NEXT"),
             tags_add=["bug"]),
    ],
)
async def test_a_malformed_personal_request_is_refused_up_front(
    db: FakeProjectsDB, payload: dict,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(task_ids=["t1"], **payload), user=ALICE,
        )
    assert caught.value.status_code == 422
    assert db.statements == []


async def test_bulk_personal_judges_the_block_per_task(db: FakeProjectsDB) -> None:
    """Migration 187's CHECK, per task: an `end` alone is legal only against
    the start each task already stores, so the merged row is what is judged."""
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(scheduled_start="2026-09-01T10:00:00+00:00"),
        user=ALICE,
    )
    with pytest.raises(HTTPException) as caught:
        await pm_bulk.bulk_edit(
            pm_bulk.BulkIn(
                task_ids=[str(task.id)], action="personal",
                personal=pm_personal.PersonalIn(scheduled_end="2026-09-01T09:00:00+00:00"),
            ),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "scheduled_end must be after" in str(caught.value.detail)


# ── The WAITING arm is bounded (P0), and the planner never packs it (F4) ────

async def test_a_delegated_task_leaves_my_lists_when_my_grant_is_revoked(
    db: FakeProjectsDB,
) -> None:
    """The overlay may NARROW a grant, never WIDEN one.

    Alice delegates a team task and keeps it by the WAITING arm. An admin
    then removes her grant. Her overlay row still says WAITING — and it must
    keep nothing: an overlay is a thing she wrote for herself, and a read
    grant no revocation reaches is not a grant.
    """
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.organize_my_task(
        str(task.id),
        pm_personal.OrganizeIn(
            kind="delegate", next_action="Draft it",
            assignee=pm_personal.OrganizeAssignee(name="Bob", email="bob@fracktal.in"),
        ),
        user=ALICE,
    )
    assert (await pm_personal.my_task(str(task.id), user=ALICE))["disposition"] == "WAITING"

    db.tables["pm_project_grants"] = []  # the revocation

    inbox = await pm_personal.my_inbox(user=ALICE, page=page())
    assert inbox.rows == []
    with pytest.raises(HTTPException) as caught:
        await pm_personal.my_task(str(task.id), user=ALICE)
    assert caught.value.status_code == 404
    # The overlay row is untouched: the BOUND did the work, not a delete.
    assert _overlay(db, str(task.id), "alice@fracktal.in")["disposition"] == "WAITING"


async def test_delegating_clears_my_block_for_the_work_i_handed_away(
    db: FakeProjectsDB,
) -> None:
    project, todo = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            scheduled_start="2026-09-24T09:00:00+00:00",
            scheduled_end="2026-09-24T10:00:00+00:00",
        ),
        user=ALICE,
    )
    await pm_personal.organize_my_task(
        str(task.id),
        pm_personal.OrganizeIn(
            kind="delegate", next_action="Do it",
            assignee=pm_personal.OrganizeAssignee(name="Bob", email="bob@fracktal.in"),
        ),
        user=ALICE,
    )
    overlay = _overlay(db, str(task.id), "alice@fracktal.in")
    assert overlay["scheduled_start"] is None and overlay["scheduled_end"] is None


async def test_the_planner_never_packs_a_waiting_task(monkeypatch) -> None:
    """F4. The membership fragment admits a WAITING task so I can CHASE it;
    `candidates` and `carry_forward` must refuse it, because it is somebody
    else's work now. Pinned on the rows the SQL hands back, whatever the
    SQL let through."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    yesterday = datetime.now(UTC) - timedelta(days=1)

    def row(id_, disposition):
        return SimpleNamespace(
            id=id_, title=id_, notes=None, due_at=None, project_id="p",
            parent_task_id=None, status_category="todo",
            stated_disposition=disposition, next_action=None, context=None,
            energy=None, time_estimate_mins=None, scheduled_start=yesterday,
            scheduled_end=yesterday, flexible=None, is_hard_date=None,
            actual_start=None, actual_end=None, important=None,
            leveraged=None, deep_work=None, kept_mine=None, sort_key=None,
            assignee_count=1, is_mine=True, org_priority=None,
        )

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _DB:
        async def execute(self, statement, params=None):
            return _Rows([row("next", "NEXT"), row("waiting", "WAITING")])

    async def binds(db, who, *, archived, **extra):
        return {"who": who, "vis_org": "o", "vis_email": who,
                "vis_groups": [], "archived": archived, **extra}
    monkeypatch.setattr(pm_planning, "my_tasks_binds", binds)

    lens = pm_planning._LensSource("pm")
    assert [r.id for r in await lens.candidates(_DB(), "alice@fracktal.in")] == ["next"]
    assert [r.id for r in await lens.carry_forward(
        _DB(), "alice@fracktal.in", datetime.now(UTC))] == ["next"]
    # And the SQL half prunes with the stated column before Python rules.
    # D77: a stated DONE is kept in the prune too, because a teammate may
    # have reopened the task and `effective_disposition` then reads NEXT.
    assert "p.disposition IN ('NEXT', 'DONE')" in pm_planning._PM_CANDIDATE_WHERE


async def test_a_second_project_outcome_of_the_same_name_shares_the_area(
    db: FakeProjectsDB,
) -> None:
    """Find-or-create on `lower(name)`: two clarifies into "Website redesign"
    share one Area rather than minting two."""
    first = await pm_personal.ensure_personal_child(db, "alice@fracktal.in", "Website redesign")
    again = await pm_personal.ensure_personal_child(db, "alice@fracktal.in", "website REDESIGN")
    assert str(again.id) == str(first.id)
    children = [p for p in db.rows("pm_projects") if p.get("parent_project_id")]
    assert len(children) == 1
