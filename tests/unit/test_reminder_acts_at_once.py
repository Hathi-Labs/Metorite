"""WS-66 RX-2 — a reminder write in chat acts at once and prints its Undo.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.8.

The owner rule (2026-11-01): a due date is a day, a reminder is a moment.
A reminder is private and Undo reverses it, so five tools act with no card in
a chat that read no outside mail. **A chat that read outside mail never acts
at once. It shows the card.** ``promote_reminder`` is class C and always asks.

A top-level chat run binds ``no_egress=False`` in a marked chat, because the
mark lives on the session. So each test of a mark first proves that
``no_egress_for_this_run()`` reads False (non-vacuity).

Red first (R7), each run against the built code and then reverted:

* M1. ``writes._reminder_acts_at_once``: return False ->
  ``test_an_open_run_acts_with_no_card`` fails for each of the five tools.
* M2. ``writes._reminder_acts_at_once``: return True ->
  ``test_a_held_run_keeps_the_one_card`` fails for each of the five tools.
* M3. ``writes.promote_reminder`` (``guarded.py``): skip its card ->
  ``test_promote_always_shows_its_card`` fails.
* M4. ``writes._reminder_receipt``: drop the ``undo:`` line ->
  ``test_the_receipt_names_its_undo`` fails.
* M5. ``egress.write_must_show_card``: ignore the mark ->
  ``test_a_marked_session_shows_the_card`` fails for each of the five tools.
* M6. ``egress.write_must_show_card``: drop the ``_session_of`` test ->
  ``test_a_run_with_no_tenant_shows_the_card`` fails.
* M7. ``egress.write_must_show_card``: drop the ``deliver_to`` test ->
  ``test_a_batch_child_of_a_marked_chat_shows_the_card`` fails.
* M8. ``egress.write_must_show_card``: drop the ``_card_can_show`` test ->
  ``test_a_run_that_cannot_read_a_mark_or_ask_shows_the_card`` fails.

No database and no gateway process: the client is patched at httpx, and the
mark lives in the in-memory store of ``tests/conftest.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import acb_skills.egress as eg
import skill_projects
from acb_skills.write_artifact import (
    DELIVER_TO,
    artifact_context,
    artifact_context_scope,
    bind_artifact_context,
    derive_artifact_context,
)

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_projects_agent_writes import ACT_AT_ONCE
from tests.unit.test_reminder_tools import REM, WRITES, Gateway, _fake_for

CASES = [(tool, kwargs) for tool, kwargs in WRITES if tool in ACT_AT_ONCE]
ORG = "11111111-2222-4333-8444-555555555555"
SESSION = "chat-rx2"
MEMBER = "asha@example.com"
AGENT = "projects-assistant"


def _chat(**over: Any) -> dict[str, Any]:
    """The server-side context of the member's own top-level chat run."""
    values: dict[str, Any] = {
        "run_id": "r-rx2", "session_id": SESSION, "agent_name": AGENT,
        "member": MEMBER, "batch_thread": False, "no_egress": False,
        "workspace_root": "/tmp/rx2",
    }
    values.update(over)
    return values


@pytest.fixture
def _bind() -> Any:
    from acb_common.db import bind_tenant, release_tenant

    def bind(*, held: bool = False, **over: Any) -> None:
        bind_artifact_context(**_chat(no_egress=held, **over))

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            yield bind
    finally:
        release_tenant(token)


def _forget_this_process() -> None:
    """A later turn on another worker: only Redis holds the mark."""
    with eg._MARKED_LOCK:
        eg._MARKED_SESSIONS.clear()


class _BrokenRedis:
    async def exists(self, *_a: Any) -> int:
        raise ConnectionError("down")


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
async def test_a_marked_session_shows_the_card(tool, kwargs, monkeypatch, _bind) -> None:
    """THE owner rule. Mutation M5: ignore the mark -> red for each tool."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool))
    _bind()
    assert await eg.mark_untrusted_answer()
    assert eg.no_egress_for_this_run() is False, "non-vacuity: a chat run reads open"
    await getattr(skill_projects, tool)(**kwargs)
    assert len(asked) == 1, f"{tool} acted at once in a chat that read mail"
    assert writes(calls)


@pytest.mark.parametrize(("tool", "kwargs"), CASES)
async def test_a_redis_fault_shows_the_card(tool, kwargs, monkeypatch, _bind) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _fake_for(tool))
    monkeypatch.setattr(eg, "get_tenant_redis", lambda: _BrokenRedis())
    _bind()
    await getattr(skill_projects, tool)(**kwargs)
    assert len(asked) == 1, f"{tool} acted at once on an unknown mark"


async def test_an_unknown_mark_shows_the_card(monkeypatch, _bind) -> None:
    async def unknown(*_a: Any, **_k: Any) -> str:
        return eg.MARK_UNKNOWN

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    monkeypatch.setattr(eg, "session_mark_state", unknown)
    _bind()
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_fault_of_the_reader_shows_the_card(monkeypatch, _bind) -> None:
    async def broken(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("the mark read failed")

    _bind()
    monkeypatch.setattr(eg, "run_holds_mail", broken)
    assert await eg.write_must_show_card() is True
    monkeypatch.setattr(eg, "write_must_show_card", broken)
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_mail_read_earlier_in_the_same_turn_shows_the_card(monkeypatch, _bind) -> None:
    """The pack read of mail marks the session on the frame of the run, with
    the helper of the executor. The same run then sets a reminder."""
    from orchestrator._tool_injection import _mark_pack_session

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind()
    before = await skill_projects.set_reminder(title="Before the mail", at="2099-10-30 17:00")
    assert asked == [] and "reminder_id" in before, "non-vacuity: this chat acts at once"
    await _mark_pack_session("email-assistant", artifact_context())
    assert eg.no_egress_for_this_run() is False, "non-vacuity: the run stays open"
    await skill_projects.set_reminder(title="After the mail", at="2099-10-30 17:00")
    assert len(asked) == 1, "the mail read in this turn did not bring the card back"


async def test_a_mail_read_in_an_earlier_turn_shows_the_card(monkeypatch, _bind) -> None:
    """Turn 1 read mail. Turn 2 starts on another worker, with a new run and an
    empty process cache, so only Redis holds the mark."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(run_id="r-turn-1")
    await eg.mark_untrusted_session("email-assistant", artifact_context())
    _forget_this_process()
    _bind(run_id="r-turn-2")
    assert eg.read_untrusted_answer() is False, "non-vacuity: the sync reader misses it"
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_streaming_child_of_a_marked_chat_shows_the_card(monkeypatch, _bind) -> None:
    """A streaming child keeps the session of its chat. Here it reads as open,
    so only the session mark can hold it."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(agent_name="orchestrator")
    await eg.mark_untrusted_session("email-assistant", artifact_context())
    _forget_this_process()
    derive_artifact_context(agent_name=AGENT, run_id="r-child", no_egress=False)
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_batch_child_of_a_marked_chat_shows_the_card(monkeypatch, _bind) -> None:
    """A child in the batch path binds a session of its own, so the mark of its
    chat cannot reach it by key. ``deliver_to`` names the chat, and a delegated
    run keeps the card."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(agent_name="orchestrator")
    await eg.mark_untrusted_session("email-assistant", artifact_context())
    _forget_this_process()
    bind_artifact_context(**_chat(
        run_id="r-child", session_id="r-child",
        **{DELIVER_TO: {"session_id": SESSION, "agent_name": "orchestrator",
                        "workspace_root": "/tmp/rx2", "instance": ""}},
    ))
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_held_child_shows_the_card(monkeypatch, _bind) -> None:
    """``_delegate_held`` and ``_run_no_egress`` bind the child held."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(run_id="r-child", session_id="r-child", held=True)
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_pack_call_in_a_marked_chat_shows_the_card(monkeypatch, _bind) -> None:
    """``owner_scope`` binds the held answer of the pack, and that answer came
    from a read at the start of the run. A mail read later in the run holds."""
    from acb_skills.tool_packs import owner_scope

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(agent_name="orchestrator")
    await eg.mark_untrusted_answer()
    with owner_scope(AGENT, False):
        assert eg.no_egress_for_this_run() is False, "non-vacuity"
        await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_run_of_the_mail_agent_shows_the_card(monkeypatch, _bind) -> None:
    """Such a run holds stranger text before any mark: ``run_holds_mail``."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(agent_name="email-assistant")
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


@pytest.mark.parametrize(
    "over",
    [{"session_id": ""}, {"member": ""}, {"batch_thread": True}],
    ids=["no session", "no member", "batch run"],
)
async def test_a_run_that_cannot_read_a_mark_or_ask_shows_the_card(
    over, monkeypatch, _bind,
) -> None:
    """No key finds a mark in such a run, or no member reads a receipt. Each
    fails closed. ``session_mark_state`` alone reads the first as clear."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(**over)
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_run_with_no_tenant_shows_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    with artifact_context_scope():
        bind_artifact_context(**_chat())
        await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert len(asked) == 1


async def test_a_mark_of_another_chat_changes_nothing(monkeypatch, _bind) -> None:
    """Non-vacuity of the key: the mark is per session, so an open chat still
    acts at once when another chat of the org read mail."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway())
    _bind(session_id="chat-other")
    await eg.mark_untrusted_answer()
    _bind()
    await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert asked == []


async def test_the_receipt_says_when_the_reminder_exists(monkeypatch, _bind) -> None:
    """The route found the same open reminder and wrote nothing. The model
    reads that, and the receipt offers no Undo: this call made nothing."""
    approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway(saved={"already": True}))
    _bind()
    out = await skill_projects.set_reminder(title="Get a haircut", at="2099-10-30 17:00")
    assert out.startswith("Reminder already set: ")
    assert "do not set it again" in out
    assert "undo:" not in out
    assert f"reminder_id: {REM}" in out


def test_the_rules_say_not_to_set_it_again() -> None:
    from acb_skills.tool_packs import PACKS

    assert "already exists, do not set it again" in " ".join(
        PACKS["projects-reminders"].rules.split())


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
