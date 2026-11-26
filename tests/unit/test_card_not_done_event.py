"""Task 3827: an approved card that the tool refused tells the chat.

The member approves a card, the chat draws "Approved", and the tool then
refuses the write because the object changed after the card (HRD-10). The
tool answer says "Changed since the card ... nothing was done" to the model,
and the receipt line of the chat had no link to it. The link is the id of the
card: `card_outcome_head` of a "Changed since the card" text publishes
`confirmation_not_done` with that id, on the stream that carried the card.

Fence for the client half: `src/lib/confirmationReceipts.test.ts`
("approve-card-changed-since").
"""

from __future__ import annotations

import asyncio
from typing import Any

from acb_skills import ask_tools as at
from orchestrator import executor


def _drive(answer: str, *, texts: int = 1, text_first: bool = False) -> list[dict[str, Any]]:
    """One card on the native path, answered with *answer*. The tool then
    builds the "Changed since" text *texts* times. Returns the events."""

    async def _tool() -> None:
        if text_first:
            at.card_outcome_text(at.CARD_CHANGED_SINCE, "No card yet.")
        await at.request_confirmation("Update «Ops»?", "one task", "task: «Ops»")
        for _ in range(texts):
            at.card_outcome_text(at.CARD_CHANGED_SINCE, "The task was not updated.")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _run() -> list[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-3827")
        task = asyncio.ensure_future(_tool())
        card = await asyncio.wait_for(q.get(), timeout=5)
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, answer, was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid), responder=None)
        await asyncio.wait_for(task, timeout=5)
        events = [card]
        while not q.empty():
            events.append(q.get_nowait())
        return events

    return asyncio.run(_run())


def _named(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("name") == at.CONFIRMATION_NOT_DONE]


def test_an_approved_card_with_a_changed_object_tells_the_chat_once() -> None:
    events = _drive("APPROVE", texts=2)
    rid = events[0]["value"]["request_id"]
    sent = _named(events)
    assert len(sent) == 1, "one event for one card, not one for each text"
    assert sent[0]["value"] == {"request_id": rid, "outcome": at.CARD_CHANGED_SINCE}
    # It follows the close of the card, so the chat has its line first.
    names = [e.get("name") for e in events]
    assert names.index(at.CONFIRMATION_RESOLVED) < names.index(at.CONFIRMATION_NOT_DONE)


def test_a_declined_card_never_tells_the_chat() -> None:
    assert _named(_drive("REJECT")) == []


def test_a_text_with_no_card_before_it_never_tells_the_chat() -> None:
    assert _named(_drive("REJECT", text_first=True)) == []


def test_the_event_name_is_the_one_the_client_reads() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "workbench" / "control_plane" / "src" / "lib"
           / "confirmationQueue.ts").read_text(encoding="utf-8")
    assert f'CONFIRMATION_NOT_DONE = "{at.CONFIRMATION_NOT_DONE}"' in src
