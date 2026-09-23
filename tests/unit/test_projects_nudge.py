"""WS-27bk wave 6 — the follow-up nudge (spec §9.12.9, H-113).

Migration 188 shipped `waiting_on`, `delegated_at`, `expected_by` and
`last_nudged_at` on `pm_task_personal`, and the Tasks app has drawn all four
since — including "nudged 3d ago". **Nothing ever wrote `last_nudged_at`**,
because the act that should write it did not exist. `POST /tasks/{id}/nudge`
is that act, and this file is its fence.

⚠️ **In-app only.** One `pm_notifications` row through the shared `notify()`,
the same path a mention takes. An OUTWARD nudge — mail, WhatsApp — is
Action-Broker work and owner-gated (CLAUDE.md §3a rule 3).

🔴 **The rule most likely to rot is the one at the bottom of this file**: the
Python kind vocabulary and the database CHECK are one rule in two places, and
a kind in one alone is a 500 rather than a refusal. Migration 213 widened the
CHECK, and `test_projects_notifications.py` reads the whole ladder for the
EFFECTIVE definition rather than the migration that first created it.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import notifications as pm_notify
from gateway.routes.projects import personal as pm_personal
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal, pm_notify)

ALICE = member_user("alice@fracktal.in")

#: ⚠️ The async cases carry the mark INDIVIDUALLY. A module-level
#: `pytestmark` would also mark the two synchronous vocabulary tests at the
#: bottom, which pytest warns about — the same note
#: `test_projects_move_sql_asyncpg.py` carries.
ASYNC = pytest.mark.asyncio


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _assign(db: FakeProjectsDB, task_id: str, *emails: str) -> None:
    for email in emails:
        db.seed(
            "pm_task_assignees", task_id=task_id, assignee=email,
            assigned_by="owner@fracktal.in",
        )


def _delegated_task(db: FakeProjectsDB, *, to: str = "priya@fracktal.in"):
    """A task Alice is waiting on somebody for, and they can see it."""
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    task = db.seed_task(project.id, todo.id, title="Send the signed quote")
    # Both of them reach the task: Alice to nudge, the other to receive.
    _assign(db, task.id, "alice@fracktal.in", to)
    db.seed(
        "pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
        disposition="WAITING",
        waiting_on={"name": "Priya", "email": to},
        delegated_at="2026-09-01T09:00:00+00:00",
    )
    return task


def _notifications(db: FakeProjectsDB) -> list[dict]:
    return [dict(r) for r in db.rows("pm_notifications")]


def _personal(db: FakeProjectsDB, task_id: str, email: str) -> dict:
    for row in db.rows("pm_task_personal"):
        row = dict(row)
        if str(row.get("task_id")) == str(task_id) and row.get("member_email") == email:
            return row
    return {}


# ── What it does when it works ──────────────────────────────────────────────

@ASYNC
async def test_the_nudge_tells_the_person_you_are_waiting_on(db: FakeProjectsDB):
    task = _delegated_task(db)

    result = await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert result["notified"] == ["priya@fracktal.in"]
    assert result["skipped"] == []
    rows = _notifications(db)
    assert len(rows) == 1
    assert rows[0]["recipient"] == "priya@fracktal.in"
    assert rows[0]["kind"] == "nudge"
    assert rows[0]["actor"] == "alice@fracktal.in"


@ASYNC
async def test_it_stamps_when_it_told_somebody(db: FakeProjectsDB):
    """`last_nudged_at` is what the Tasks app draws as "nudged 3d ago"."""
    task = _delegated_task(db)
    assert _personal(db, task.id, "alice@fracktal.in").get("last_nudged_at") is None

    result = await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert result["last_nudged_at"] is not None
    assert _personal(db, task.id, "alice@fracktal.in")["last_nudged_at"] is not None


@ASYNC
async def test_the_stamp_is_the_LAST_nudge_not_the_first(db: FakeProjectsDB):
    """The column is `last_nudged_at`. A second chase is a member's own call,
    and the surface shows when they last made one so they can judge it."""
    task = _delegated_task(db)

    first = await pm_personal.nudge_task(str(task.id), user=ALICE)
    second = await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert first["last_nudged_at"] is not None
    assert second["last_nudged_at"] is not None
    assert len(_notifications(db)) == 2


# ── What it refuses, and why each refusal is its own case ───────────────────

@ASYNC
async def test_a_task_you_are_not_waiting_on_is_refused(db: FakeProjectsDB):
    """409, not 422 — the request is well formed and the STATE is wrong.

    Nothing was delegated, so there is nobody to chase.
    """
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    with pytest.raises(HTTPException) as caught:
        await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert caught.value.status_code == 409
    assert "not waiting on anybody" in str(caught.value.detail)
    assert _notifications(db) == []


@ASYNC
async def test_an_agent_is_refused_BY_NAME_rather_than_silently(db: FakeProjectsDB):
    """🔴 `notifiable()` drops an agent without a word, and the table's
    `pm_notifications_recipient_is_human` CHECK refuses the row anyway. Left to
    those two the endpoint would answer "nobody was told" with no reason, and
    the member would press it again. So the endpoint says so itself."""
    task = _delegated_task(db, to="agent:sales")

    with pytest.raises(HTTPException) as caught:
        await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert caught.value.status_code == 409
    assert "agent" in str(caught.value.detail).lower()
    assert _notifications(db) == []


@ASYNC
async def test_a_delegation_with_no_address_is_refused(db: FakeProjectsDB):
    """`waiting_on` is jsonb, so a `{"name": "Priya"}` with no email is a shape
    the column accepts and the notifier cannot use."""
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    db.seed(
        "pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
        disposition="WAITING", waiting_on={"name": "Priya"},
        delegated_at="2026-09-01T09:00:00+00:00",
    )

    with pytest.raises(HTTPException) as caught:
        await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert caught.value.status_code == 409
    assert _notifications(db) == []


# ── The one that keeps the surface honest ───────────────────────────────────

@ASYNC
async def test_it_does_NOT_stamp_when_nobody_could_be_told(db: FakeProjectsDB):
    """🔴 **The defect this test exists to prevent.**

    `notify()` delivers only to people who can open the task. Somebody you
    delegated to outside the project's grants receives nothing. Stamping
    `last_nudged_at` anyway would draw "nudged just now" on the row beside a
    chase that reached NOBODY — the member then waits on a person who was
    never told, believing they were. Same shape as a board that says it holds
    every task and holds one page.
    """
    # ⚠️ Granted to Alice ALONE, not to the org. With `subject="org"` every
    # member can see the task and Priya is deliverable after all — which is
    # what this test measured on its first run, and why the setup says so.
    project = db.seed_project(name="Private", subject="alice@fracktal.in")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    task = db.seed_task(project.id, todo.id)
    # Alice reaches it through the grant. Priya has no grant and no assignment.
    db.seed(
        "pm_task_personal", task_id=task.id, member_email="alice@fracktal.in",
        disposition="WAITING",
        waiting_on={"name": "Priya", "email": "priya@fracktal.in"},
        delegated_at="2026-09-01T09:00:00+00:00",
    )

    result = await pm_personal.nudge_task(str(task.id), user=ALICE)

    assert result["notified"] == []
    assert result["skipped"] == ["priya@fracktal.in"]
    # Reported, not swallowed — and NOT stamped.
    assert result["last_nudged_at"] is None
    assert _personal(db, task.id, "alice@fracktal.in").get("last_nudged_at") is None
    assert _notifications(db) == []


# ── The two-places rule ─────────────────────────────────────────────────────

def test_nudge_is_a_kind_the_notifier_accepts():
    """`notify()` raises `ValueError` on an unknown kind, so the endpoint would
    500 if the vocabulary had not been widened beside it."""
    assert "nudge" in pm_notify.NOTIFICATION_KINDS


def test_migration_213_widens_the_check_for_it():
    """The database half of the same rule.

    ⚠️ `test_projects_notifications.py::test_the_kind_vocabulary_matches_the_module`
    is the general fence and reads the whole ladder. This one names 213, so a
    revert of that single file fails HERE with the reason, rather than as a set
    comparison somewhere else.
    """
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "infra" / "postgres"
           / "213_projects_nudge_notification.sql").read_text(encoding="utf-8")
    assert "pm_notifications_kind_check" in sql
    assert "'nudge'" in sql
