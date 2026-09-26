"""WS-39 S6e — continuity with Projects, hermetic.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §4.8 · §5 S6e · D73.8.

    GET /projects/my/inbox?untriaged=true   the rows I have not looked at
    GET /projects/my/led                    the projects I lead

The claims a hermetic fake CAN judge are pinned here: which arm of the one
membership fragment a row enters by, what an overlay write does to the
untriaged group, and that the led list is the LEAD column and nothing else.
The Postgres claims — the LEFT JOIN's NULL under a real join, the tenant
bound on a real ``organization_id`` — live in ``tests/live/live_ws39_s6e.py``
(R8).

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from gateway.routes.projects import bulk as pm_bulk
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
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

ALICE = member_user("alice@fracktal.in")
BOB = member_user("bob@fracktal.in")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _team_project(db: FakeProjectsDB, name: str = "Sales", **columns) -> tuple:
    project = db.seed_project(name=name, subject="org", **columns)
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


def _assign(db: FakeProjectsDB, task_id: str, *emails: str, by: str = "pm@fracktal.in") -> None:
    for email in emails:
        db.seed(
            "pm_task_assignees", task_id=task_id, assignee=email, assigned_by=by,
        )


async def _untriaged(user) -> list[dict]:
    out = await pm_personal.my_inbox(user=user, untriaged=True, page=page())
    return out.rows


# ── untriaged=true ──────────────────────────────────────────────────────────

async def test_a_task_assigned_to_bob_is_in_bobs_untriaged_group_not_alices(
    db: FakeProjectsDB,
) -> None:
    """The row a colleague put on Bob's plate, before Bob has looked at it.
    Alice is not assigned, so it is nobody's row for her."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "bob@fracktal.in", by="pm@fracktal.in")

    bobs = await _untriaged(BOB)
    assert [r["id"] for r in bobs] == [str(task.id)]
    row = bobs[0]
    # Derived, not stored: NEXT because it is assigned to him (D53).
    assert row["disposition"] == "NEXT"
    assert row["is_triaged"] is False
    # Who put it there, and where it lives — the two facts the group draws.
    assert row["assigned_by"] == "pm@fracktal.in"
    assert row["project_name"] == "Sales"

    assert await _untriaged(ALICE) == []


async def test_a_disposition_write_is_the_triage_and_the_row_leaves_the_group(
    db: FakeProjectsDB,
) -> None:
    """Triage is a STATED disposition, which Clarify always writes. A context
    alone is not: the row exists, the disposition is still NULL, and the
    member may never have seen who put the task there."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "bob@fracktal.in")
    assert len(await _untriaged(BOB)) == 1

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(context="@calls"), user=BOB,
    )
    still = await _untriaged(BOB)
    assert [r["id"] for r in still] == [str(task.id)], "a context alone is not a triage"
    assert still[0]["context"] == "@calls"
    assert still[0]["is_triaged"] is False

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=BOB,
    )
    assert await _untriaged(BOB) == []
    # …and it is still in the plain inbox: triage files it, never hides it.
    inbox = await pm_personal.my_inbox(user=BOB, page=page())
    assert [r["id"] for r in inbox.rows] == [str(task.id)]
    assert inbox.rows[0]["is_triaged"] is True


async def test_a_planner_block_alone_does_not_triage(db: FakeProjectsDB) -> None:
    """`apply_blocks` upserts the scheduled block onto the overlay row. A
    task the day planner packed is still one the member has not filed."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "bob@fracktal.in")
    db.seed(
        "pm_task_personal", task_id=task.id, member_email="bob@fracktal.in",
        scheduled_start=datetime(2026, 9, 24, 9, tzinfo=UTC),
        scheduled_end=datetime(2026, 9, 24, 10, tzinfo=UTC),
    )
    rows = await _untriaged(BOB)
    assert [r["id"] for r in rows] == [str(task.id)]
    assert rows[0]["scheduled_start"] is not None


async def test_the_untriaged_group_is_composed_on_the_one_fragment(
    db: FakeProjectsDB,
) -> None:
    """The flag adds ONE clause to `_MY_TASKS_SQL`. There is no second
    membership query to keep in step with the first."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "bob@fracktal.in")
    await _untriaged(BOB)

    reads = [s for s in db.statements if "LEFT JOIN pm_task_personal p" in s]
    assert len(reads) == 1
    assert pm_personal.UNTRIAGED_CLAUSE in reads[0]
    assert "lower(a.assignee) = :who" in reads[0], "the assignee arm is the door"
    assert "t.organization_id = CAST(:vis_org AS uuid)" in reads[0]


async def test_the_plain_inbox_does_not_carry_the_clause(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "bob@fracktal.in")
    await pm_personal.my_inbox(user=BOB, page=page())
    reads = [s for s in db.statements if "LEFT JOIN pm_task_personal p" in s]
    assert pm_personal.UNTRIAGED_CLAUSE not in reads[-1]


async def test_a_personal_capture_is_never_untriaged(db: FakeProjectsDB) -> None:
    """A thought I captured is in my own root — I have looked at it by
    definition. The group is for what OTHERS put on my plate."""
    await pm_personal.capture(pm_personal.CaptureIn(title="A thought"), user=BOB)
    # `capture` writes no overlay row, so the LEFT JOIN is NULL here too —
    # the clause's second half (`proj.personal_owner IS NULL`) is what keeps
    # my own tree out. "From Projects" means a company board.
    assert await _untriaged(BOB) == []
    inbox = await pm_personal.my_inbox(user=BOB, page=page())
    assert [r["title"] for r in inbox.rows] == ["A thought"], "still in the inbox"


# ── /my/led ─────────────────────────────────────────────────────────────────

async def test_a_project_bob_leads_lists_for_bob_with_no_task_assigned_to_him(
    db: FakeProjectsDB,
) -> None:
    """Leading it is the fact. Nothing in the project is 'mine' through the
    membership fragment, and the project lists anyway."""
    project, todo, done = _team_project(db, name="Launch", lead="Bob@Fracktal.in")
    db.seed_task(project.id, todo.id, title="Open one")
    db.seed_task(project.id, todo.id, title="Open two")
    db.seed_task(project.id, done.id, title="Closed", completed_at="2026-09-01T00:00:00+00:00")
    db.seed_task(project.id, todo.id, title="Archived", archived_at="2026-09-01T00:00:00+00:00")

    bobs = await pm_personal.my_led_projects(user=BOB)
    assert bobs["total"] == 1
    row = bobs["rows"][0]
    assert row["id"] == str(project.id)
    assert row["name"] == "Launch"
    assert row["open_tasks"] == 2, "open = not archived, not in a closed lane"
    assert row["my_tasks"] == []

    alices = await pm_personal.my_led_projects(user=ALICE)
    assert alices == {"rows": [], "total": 0}


async def test_led_carries_my_open_tasks_in_the_inbox_shape(db: FakeProjectsDB) -> None:
    project, todo, done = _team_project(db, name="Launch", lead="bob@fracktal.in")
    mine = db.seed_task(project.id, todo.id, title="Mine, open")
    _assign(db, mine.id, "bob@fracktal.in")
    theirs = db.seed_task(project.id, todo.id, title="Theirs")
    _assign(db, theirs.id, "alice@fracktal.in")
    finished = db.seed_task(project.id, done.id, title="Mine, done")
    _assign(db, finished.id, "bob@fracktal.in")
    db.seed("pm_task_personal", task_id=mine.id, member_email="bob@fracktal.in",
            context="@office")

    row = (await pm_personal.my_led_projects(user=BOB))["rows"][0]
    assert row["open_tasks"] == 2
    assert [t["title"] for t in row["my_tasks"]] == ["Mine, open"]
    task = row["my_tasks"][0]
    # The same row `/my/inbox` gives: overlay projected, assignees attached.
    assert task["context"] == "@office"
    assert task["is_mine"] is True
    assert task["assignees"] == ["bob@fracktal.in"]
    assert task["project_name"] == "Launch"


async def test_led_excludes_the_personal_tree_and_archived_projects(
    db: FakeProjectsDB,
) -> None:
    db.seed_project(name="Old", subject="org", lead="bob@fracktal.in",
                    archived_at="2026-01-01T00:00:00+00:00")
    db.seed_project(name="My tasks", subject=None, lead="bob@fracktal.in",
                    personal_owner="bob@fracktal.in")
    live, _, _ = _team_project(db, name="Live", lead="bob@fracktal.in")

    rows = (await pm_personal.my_led_projects(user=BOB))["rows"]
    assert [r["id"] for r in rows] == [str(live.id)]


async def test_led_answers_empty_for_a_member_the_directory_does_not_know(
    db: FakeProjectsDB,
) -> None:
    """No directory row means no tenant. The route binds NULL through, the
    way `my_tasks_binds` does, and answers an empty list — never a 500 on
    `CAST('None' AS uuid)`."""
    _team_project(db, name="Ours", lead="bob@fracktal.in")
    db.organization_id = None
    assert await pm_personal.my_led_projects(user=BOB) == {"rows": [], "total": 0}
    read = next(s for s in db.statements if "lower(lead) = :who" in s)
    bound = next(a for st, a in db.calls if st == read)
    assert bound["vis_org"] is None


async def test_my_task_lanes_answer_behind_the_membership_check(
    db: FakeProjectsDB,
) -> None:
    """The lanes a task of mine can be in, so the shared task body can draw
    its Status select for a task the member reaches by assignment alone —
    `/nodes/{id}/statuses` is behind a grant they may not hold. 404 for a
    task that is not mine, exactly as the single read answers."""
    project, todo, done = _team_project(db)
    # D79 — each lane carries its stored colour, so the status menu draws a
    # violet "In review" in the hue its board draws it.
    review = db.seed(
        "pm_task_statuses", project_id=project.id, name="In review",
        category="in_progress", position=30, color="violet", is_default=False,
    )
    task = db.seed_task(project.id, todo.id, title="Draft the quote")
    _assign(db, task.id, "bob@fracktal.in")
    out = await pm_personal.my_task_lanes(str(task.id), user=BOB)
    assert [(s["id"], s["name"], s["category"], s["color"]) for s in out["rows"]] == [
        (str(todo.id), "To do", "todo", "gray"),
        (str(review.id), "In review", "in_progress", "violet"),
        (str(done.id), "Done", "done", "gray"),
    ]
    assert out["total"] == 3
    with pytest.raises(HTTPException) as caught:
        await pm_personal.my_task_lanes(str(task.id), user=ALICE)
    assert caught.value.status_code == 404
    # …and the single read's key set did not grow: the three personal
    # readers stay one shape (`test_projects_personal.py`'s fence).
    assert "statuses" not in await pm_personal.my_task(str(task.id), user=BOB)


async def test_led_is_tenant_bound(db: FakeProjectsDB) -> None:
    """The lead column is a bare address. Without the tenant arm, a project
    in another organization whose lead typed Bob's address would land in
    Bob's list."""
    _team_project(db, name="Ours", lead="bob@fracktal.in")
    db.seed_project(name="Theirs", subject="org", lead="bob@fracktal.in",
                    organization_id="00000000-0000-0000-0000-00000000dead")

    rows = (await pm_personal.my_led_projects(user=BOB))["rows"]
    assert [r["name"] for r in rows] == ["Ours"]
    read = next(s for s in db.statements if "lower(lead) = :who" in s)
    assert "organization_id = CAST(:vis_org AS uuid)" in read
