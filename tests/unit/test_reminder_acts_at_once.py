"""WS-66 RX-2 — a reminder write in chat acts at once and prints its Undo.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.8.

The owner rule (2026-11-01): a due date is a day, a reminder is a moment.
A reminder is private and Undo reverses it, so five tools act with no card
in a run that may send. A HELD run keeps its one card. ``promote_reminder``
is class C and always asks.

Red first (R7), each run against the built code and then reverted:

* M1. ``writes._reminder_acts_at_once``: return False ->
  ``test_an_open_run_acts_with_no_card`` fails for each of the five tools.
* M2. ``writes._reminder_acts_at_once``: return True ->
  ``test_a_held_run_keeps_the_one_card`` fails for each of the five tools.
* M3. ``writes.promote_reminder`` (``guarded.py``): skip its card ->
  ``test_promote_always_shows_its_card`` fails.
* M4. ``writes._reminder_receipt``: drop the ``undo:`` line ->
  ``test_the_receipt_names_its_undo`` fails.

No database and no gateway process: the client is patched at httpx.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_projects_agent_writes import ACT_AT_ONCE
from tests.unit.test_reminder_tools import REM, WRITES, Gateway, _fake_for

CASES = [(tool, kwargs) for tool, kwargs in WRITES if tool in ACT_AT_ONCE]


@pytest.fixture
def _bind() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    def bind(*, held: bool) -> None:
        bind_artifact_context(agent_name="projects-assistant", no_egress=held)

    with artifact_context_scope():
        yield bind


@pytest.mark.parametrize(("tool", "kwargs"), CASES)
async def test_an_open_run_acts_with_no_card(tool, kwargs, monkeypatch, _bind) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool))
    _bind(held=False)
    out = await getattr(skill_projects, tool)(**kwargs)
    assert asked == [], f"{tool} showed a card in a run that may send"
    assert writes(calls), f"{tool} wrote nothing: {out!r}"
    assert f"reminder_id: {REM}" in out


@pytest.mark.parametrize(("tool", "kwargs"), CASES)
async def test_a_held_run_keeps_the_one_card(tool, kwargs, monkeypatch, _bind) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool))
    _bind(held=True)
    await getattr(skill_projects, tool)(**kwargs)
    assert len(asked) == 1, f"{tool} must show one card in a held run"
    assert writes(calls)


async def test_a_run_with_no_context_is_held(monkeypatch) -> None:
    """The reader fails closed: no bound run reads as held, so the card shows."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_promote_always_shows_its_card(monkeypatch, _bind) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(held=False)
    await skill_projects.promote_reminder(reminder_id=REM)
    assert len(asked) == 1


async def test_a_denied_card_in_a_held_run_writes_nothing(monkeypatch, _bind) -> None:
    from tests.unit._projects_agent_fakes import deny

    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    _bind(held=True)
    out = await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert "Cancelled" in out and writes(calls) == []


async def test_the_gate_still_refuses_before_any_write(monkeypatch, _bind) -> None:
    """A flag that is off refuses in an open run too: no write, no card."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(off=True))
    _bind(held=False)
    out = await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert asked == [] and writes(calls) == []
    assert "reminder_id" not in out


async def test_the_receipt_names_its_undo(monkeypatch, _bind) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(held=False)
    made = await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert "  undo: drop" in made
    assert "  fire_at: 2099-10-30T11:30:00+00:00" in made
    assert "  zone: Asia/Kolkata" in made

    moved = await skill_projects.reschedule_reminder(reminder_id=REM, at="2099-11-02 10:00")
    assert "  undo: reschedule" in moved
    assert "  was_fire_at: 2099-10-30T11:30:00+00:00" in moved
    assert "  was_zone: Asia/Kolkata" in moved

    renamed = await skill_projects.update_reminder(reminder_id=REM, title="Get a short haircut")
    assert "  undo: patch" in renamed and "  was_title:" in renamed

    # A change that Undo cannot put back shows no Undo.
    noted = await skill_projects.update_reminder(reminder_id=REM, notes="Ask for Raj")
    assert "undo:" not in noted

    done = await skill_projects.resolve_reminder(reminder_id=REM)
    assert "undo:" not in done, "a done or a drop is undone by reopen_reminder"


async def test_a_linked_task_is_on_the_receipt(monkeypatch, _bind) -> None:
    approve(monkeypatch)
    task = "1f8fad5b-d9cb-469f-a165-70867728950e"
    fake_gateway(monkeypatch, Gateway(saved={"subject_kind": "task", "subject_id": task}))
    _bind(held=False)
    out = await skill_projects.set_reminder(
        title="Get a haircut", at="2099-10-30 17:00", subject_kind="task", subject_id=task,
    )
    assert f"  task_id: {task}" in out
    assert "full_id" not in out
