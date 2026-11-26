"""EAC-1: the edit card of a new task against the real routes (R8, task 3468).

Spec: ``project-docs/specs/projects_ai_chat.md`` §32.6 (EAC-1) and §32.7.

``create_task`` runs through the skill's own client against the REAL
``/projects`` router, on a phase-4 promoted scratch database as the
non-privileged ``acb_app`` role. The card is the REAL gate
(``ask_tools.request_confirmation``) on the native queue path, and the answer
goes through the REAL ``executor.resolve_user_input``, as the
``/agent/respond-input`` route sends it. The hermetic half is
``test_task_edit_card_answer.py``. This file proves what a fake cannot:

* an edited due date, status and assignee are WRITTEN as edited on the task
  row and its assignee rows, and the run's member is the writer;
* a plain ``APPROVE`` writes the original values;
* an address that the directory does not know is written and warned, as the
  line of the card warns, and a forged project id in the answer writes nothing;
* a late approve (the card of a stored call, HRD-22 and RESUME) writes the
  original values;
* the route's own rule refuses an edited field that the member may not write:
  a person on a task of a personal project.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_task_edit_card_r8.py -v -rs

**The mutations (R7)**, run on 2026-11-16 on a private ladder database. Each
turned the named test red.

* R-M6. ``writes._card_edit``: keep the task of the card (``edited = new``). ``test_r8_an_edited_due_status_and_assignee_are_written_as_edited``.
* R-M7. ``ask_tools._verdict``: read a plain ``APPROVE`` on an edit card as a
  REJECT. ``test_r8_a_plain_approve_writes_the_original_values``.
* R-M8. ``ask_tools._verdict``: approve an answer that does not parse.
  ``test_r8_a_forged_project_id_writes_nothing``.
* R-M12. ``writes._card_edit``: no stranger warning.
  ``test_r8_a_stranger_is_written_and_warned_as_the_card_warns``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("orchestrator", reason="the orchestrator is not installed")

import skill_projects
from fastapi import Request
from skill_projects.refusals import refusals_as_text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_projects_agent_grants import _route_tools_to

pytestmark = _DB_GATE

ALICE = "alice@eac1-r8.test"
BOB = "bob@eac1-r8.test"
STRANGER = "stranger@elsewhere-eac1.test"
PEOPLE = [ALICE, BOB]
SID = "thread-alice-eac1-r8-0001"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """One space in org B with one project inside it, open to the whole org,
    with a To do lane (the default) and a Doing lane. Alice's own personal
    project, with its own lanes. Alice and Bob are in the directory. Seeded as
    the superuser. ``PROJECT_EDIT_CARD_ORGS`` names org B."""
    from acb_common import get_settings
    from sqlalchemy import text

    org = promoted.org_b
    monkeypatch.setattr(get_settings(), "project_edit_card_orgs", org)
    space, ops, mine = (str(uuid.uuid4()) for _ in range(3))
    lanes = {name: str(uuid.uuid4()) for name in ("todo", "doing", "my_todo")}
    with promoted.admin_engine.begin() as c:
        for email in PEOPLE:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                "(SELECT 1 FROM app_user WHERE lower(email) = :e)"),
                {"e": email, "o": org})
            # The directory row, when the app_user row did not make one.
            c.execute(text(
                "INSERT INTO people (id, name, email, status, skills, source, source_key, "
                "organization_id, updated_by, updated_at) SELECT gen_random_uuid(), :n, :e, "
                "'active', ARRAY[]::text[], 'manual', :k, CAST(:o AS uuid), 'test', now() "
                "WHERE NOT EXISTS (SELECT 1 FROM people WHERE organization_id = "
                "CAST(:o AS uuid) AND lower(email) = :e)"),
                {"n": email.split("@")[0].title(), "e": email, "k": f"manual:{email}",
                 "o": org})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'Space EAC1', "
            "'manual', :me, true)"), {"p": space, "o": org, "me": ALICE})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "owns_statuses, parent_project_id) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
            "'Ops EAC1', 'manual', :me, true, CAST(:s AS uuid))"),
            {"p": ops, "o": org, "me": ALICE, "s": space})
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "owns_statuses, personal_owner) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
            "'Alice private EAC1', 'manual', :me, true, :me)"),
            {"p": mine, "o": org, "me": ALICE})
        for lane, project, name, position, default in (
            (lanes["todo"], ops, "To do", 10, True),
            (lanes["doing"], ops, "Doing", 20, False),
            (lanes["my_todo"], mine, "To do", 10, True),
        ):
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
                "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), :n, :pos, :c, :d)"),
                {"s": lane, "p": project, "n": name, "pos": position,
                 "c": "in_progress" if name == "Doing" else "todo", "d": default})
        for project, subject in ((space, "org"), (mine, ALICE)):
            c.execute(text(
                "INSERT INTO pm_project_grants (id, project_id, organization_id, subject, "
                "created_by) VALUES (gen_random_uuid(), CAST(:p AS uuid), CAST(:o AS uuid), "
                ":s, :me)"), {"p": project, "o": org, "s": subject, "me": ALICE})
    yield type("World", (), {"org": org, "promoted": promoted, "ops": ops, "mine": mine,
                             "lanes": lanes})
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM pm_notifications WHERE organization_id = CAST(:o AS uuid) "
            "AND recipient = ANY(:e)"), {"o": org, "e": [*PEOPLE, STRANGER]})
        c.execute(text(
            "DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
            "AND created_by = ANY(:e)"), {"o": org, "e": PEOPLE})
        c.execute(text(
            "DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
            {"p": [ops, mine]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": space})
        c.execute(text("DELETE FROM people WHERE email = ANY(:e)"), {"e": PEOPLE})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": PEOPLE})


def _app():
    """The real Projects router, signed in as whoever the header names."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user

    people = {ALICE: member_user(ALICE), BOB: member_user(BOB)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


async def _card_of(q: asyncio.Queue) -> dict[str, Any]:
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=20)
        if ev.get("name") == "confirmation_requested":
            return ev


async def _create(world: Any, monkeypatch, call: dict[str, Any],
                  answer: str | Callable[[dict[str, Any]], str] | None, *,
                  responder: str = ALICE) -> tuple[dict[str, Any] | None, str]:
    """``create_task`` as Alice, with the REAL card gate. *answer* is the
    text of the answer, or a function of the card event that gives it (the
    member edits the form that the card drew). None expects no card. Gives
    the card event and the tool's text."""
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import executor

    from tests.unit._tenant_ladder import tenant_engine_scope

    _route_tools_to(monkeypatch, _app(), ALICE)
    token = bind_tenant(world.org)
    q: asyncio.Queue = asyncio.Queue()
    q_token = executor._active_run_queue.set(q)
    t_token = executor._stream_relay_thread_id.set(SID)
    try:
        url = world.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            task = asyncio.ensure_future(refusals_as_text(skill_projects.create_task)(**call))
            if answer is None:
                return None, await asyncio.wait_for(task, timeout=60)
            card = await _card_of(q)
            rid = card["value"]["request_id"]
            said = answer(card) if callable(answer) else answer
            assert executor.resolve_user_input(
                rid, said, was_freeform=False,
                thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
            return card, await asyncio.wait_for(task, timeout=60)
    finally:
        executor._stream_relay_thread_id.reset(t_token)
        executor._active_run_queue.reset(q_token)
        release_tenant(token)


def _call(world: Any, title: str, project: str = "") -> dict[str, Any]:
    return {
        "project_id": project or world.ops,
        "title": title,
        "status": "To do",
        "assignees": ALICE,
        "due": "2026-12-01",
        "description": "The original text.",
    }


def _edit(card: dict[str, Any], **changes: Any) -> str:
    """The answer of a member who changed *changes* on the form of *card*."""
    values = dict(card["value"]["edit"]["values"])
    values.update(changes)
    return "APPROVE " + json.dumps({"edit": values})


def _task(world: Any, title: str) -> Any:
    from sqlalchemy import text

    with world.promoted.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id, to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS due, status_id, "
            "description, importance, created_by, project_id FROM pm_tasks "
            "WHERE organization_id = CAST(:o AS uuid) AND title = :t"),
            {"o": world.org, "t": title}).fetchall()
    return rows


def _assignees(world: Any, task_id: Any) -> list[str]:
    from sqlalchemy import text

    with world.promoted.admin_engine.connect() as c:
        return sorted(r.assignee for r in c.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": str(task_id)}).fetchall())


async def test_r8_a_plain_approve_writes_the_original_values(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    title = "EAC1 plain approve"
    card, out = await _create(world, monkeypatch, _call(world, title), "APPROVE")
    assert card is not None and card["value"]["edit"]["values"]["due"] == "2026-12-01"
    assert out.startswith("Created:"), out
    [row] = _task(world, title)
    assert (row.due, str(row.status_id), row.description, row.created_by) == (
        "2026-12-01", world.lanes["todo"], "The original text.", ALICE)
    assert _assignees(world, row.id) == [ALICE]


async def test_r8_an_edited_due_status_and_assignee_are_written_as_edited(
    world, monkeypatch,
) -> None:
    pytest.importorskip("asyncpg")
    title = "EAC1 edited values"
    _card, out = await _create(world, monkeypatch, _call(world, title), lambda card: _edit(
        card, due="2026-12-24", status="Doing", assignees=[BOB], important=True))
    assert out.startswith("Created:"), out
    assert "These are the approved values" in out, out  # EAC-1b
    [row] = _task(world, title)
    assert row.due == "2026-12-24"
    assert str(row.status_id) == world.lanes["doing"]
    assert row.importance is not None and row.importance >= 2
    assert row.description == "The original text."  # not changed, so as the card
    assert row.created_by == ALICE  # the member's own write, under the routes' grants
    assert _assignees(world, row.id) == [BOB]


async def test_r8_a_stranger_is_written_and_warned_as_the_card_warns(world, monkeypatch) -> None:
    """The route takes any address for a member (D-PM-4). The card of before
    names a stranger on its ``not in the directory`` line, and the edit path
    names it on the same line of the receipt. No new server rule."""
    pytest.importorskip("asyncpg")
    title = "EAC1 stranger"
    _card, out = await _create(world, monkeypatch, _call(world, title),
                               lambda card: _edit(card, assignees=[STRANGER]))
    assert f"  not in the directory: {STRANGER}" in out, out
    [row] = _task(world, title)
    assert _assignees(world, row.id) == [STRANGER]


async def test_r8_a_forged_project_id_writes_nothing(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    title = "EAC1 forged project"
    _card, out = await _create(world, monkeypatch, _call(world, title),
                               lambda card: _edit(card, project_id=world.mine))
    assert out.startswith("Declined:"), out  # HRD-7: the one table
    assert _task(world, title) == []


async def test_r8_a_late_approve_writes_the_original_values(world, monkeypatch) -> None:
    """The card of a stored call takes its late approve with no answer of its
    own (``ask_tools._late_approved``, HRD-22). So the original values are
    written, and no edit of the member applies."""
    pytest.importorskip("asyncpg")
    import acb_skills.ask_tools as at

    async def _granted(_card: Any) -> bool:
        return True

    monkeypatch.setattr(at, "_late_approved", _granted)
    title = "EAC1 late approve"
    card, out = await _create(world, monkeypatch, _call(world, title), None)
    assert card is None and out.startswith("Created:"), out
    [row] = _task(world, title)
    assert (row.due, str(row.status_id)) == ("2026-12-01", world.lanes["todo"])
    assert _assignees(world, row.id) == [ALICE]


async def test_r8_the_route_refuses_an_edited_field_the_member_may_not_write(
    world, monkeypatch,
) -> None:
    """Alice's personal project takes only Alice (``core.assert_assignable_here``).
    She adds Bob on the card. The write runs as Alice through the routes, so
    the route's own rule refuses Bob, and the receipt says so. The tool adds
    no rule of its own."""
    pytest.importorskip("asyncpg")
    title = "EAC1 personal assignee"
    _card, out = await _create(world, monkeypatch, _call(world, title, world.mine),
                               lambda card: _edit(card, assignees=[ALICE, BOB]))
    assert "The assignees were NOT saved" in out and "personal project" in out, out
    [row] = _task(world, title)
    assert str(row.project_id) == world.mine
    assert _assignees(world, row.id) == []


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed (``tests/fences.json``, key ``r8``)."""
    from tests.unit import _r8_lists as r8_lists

    assert "tests/unit/test_task_edit_card_r8.py" in r8_lists.must_run()
