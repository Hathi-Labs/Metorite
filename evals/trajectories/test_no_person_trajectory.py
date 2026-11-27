"""Golden trajectory: a turn with no person present (AUTO-7a).

Spec: ``project-docs/specs/proactive_agent.md`` §4.2.

The unit suite ``tests/unit/test_no_person_present.py`` pins each seam. This
eval drives what the agent sees through the relay path. A live relay waits in
the context, so a card could reach a person. A marked turn draws no card,
parks nothing and gets a deny at once. The same turn with a person present
draws the card and waits. So the deny comes from the mark, not from a broken
path.
"""
from __future__ import annotations

import asyncio

import pytest
from acb_common.no_person import no_person_scope
from acb_skills.ask_tools import ask_questions, confirmation_channel_open, request_confirmation
from orchestrator import executor

QUESTIONS = '{"questions": [{"header": "Go", "question": "Approve the write?"}]}'


@pytest.fixture
def frames(monkeypatch) -> list[str]:
    sent: list[str] = []

    async def _push(_thread_id: str, line: str) -> None:
        sent.append(line)

    monkeypatch.setattr(executor, "_push_sse_to_stream", _push)
    executor._stream_relay_thread_id.set("traj-no-person")
    return sent


async def test_a_marked_turn_draws_no_card_and_parks_nothing(frames) -> None:
    with no_person_scope():
        assert confirmation_channel_open() is False
        approved = await asyncio.wait_for(
            request_confirmation("Send it?", non_interactive_default="approve"), 1)
        said = await asyncio.wait_for(ask_questions(QUESTIONS), 1)

    assert approved is False
    assert "cannot show questions" in said
    assert frames == []
    assert executor._pending_user_input == {}


async def test_the_same_turn_with_a_person_present_draws_the_card(frames) -> None:
    assert confirmation_channel_open() is True
    task = asyncio.create_task(request_confirmation("Send it?"))
    for _ in range(100):
        if executor._pending_user_input:
            break
        await asyncio.sleep(0.01)
    request_id = next(iter(executor._pending_user_input))
    assert any("confirmation_requested" in line for line in frames)

    assert executor.resolve_user_input(
        request_id, "APPROVE", False, thread_id="traj-no-person") is True
    assert await asyncio.wait_for(task, 1) is True
