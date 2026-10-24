"""A card closes when the member writes in the chat while it waits.

The owner's case of 2026-10-20: a reply card showed one text, and the member
asked in the chat for a change. The words waited behind the card, because a
steer rides the NEXT tool result (``orchestrator.steer.decorate_tool_result``)
and the card blocks the tool. So the card kept the old text.

``request_confirmation(close_on_steer=True)`` watches the steer buffer of the
run's thread. A NEW note closes the card with ``REVISED``, which is falsy, and
the member's words then reach the model on the tool's result.

Each rule names the mutation that turns it red (R7):

1. **A new steer closes the card**, and the stream gets the closing event.
   Mutation: drop the watcher -> ``test_a_new_steer_closes_the_card``.
2. **A note that waited before the card does not close it.** Mutation:
   compare against an empty set -> ``test_an_older_note_does_not_close_the_card``.
3. **A plain card is unchanged**: with no opt-in, a steer leaves it open.
   Mutation: default ``close_on_steer`` to True ->
   ``test_a_plain_card_ignores_a_steer``.
4. **REVISED is no approval** for a caller that tests truth.
5. **The email tools answer REVISED with the revise text**, never "sent".
"""
from __future__ import annotations

import asyncio
import importlib

from orchestrator import executor, steer

at = importlib.import_module("acb_skills.ask_tools")

THREAD = "t-steer-card"


async def _card(**kwargs):
    q: asyncio.Queue = asyncio.Queue()
    executor._active_run_queue.set(q)
    executor._stream_relay_thread_id.set(THREAD)
    task = asyncio.ensure_future(
        at.request_confirmation("Send this email?", "to ravi@contoso.test", "Hi Ravi", **kwargs))
    card = await asyncio.wait_for(q.get(), timeout=2)
    return q, task, card


def _clean() -> None:
    steer.clear_guidance(THREAD)


def test_a_new_steer_closes_the_card():
    _clean()

    async def run():
        q, task, card = await _card(close_on_steer=True)
        assert steer.buffer_guidance(THREAD, "jane", "make it shorter")
        result = await asyncio.wait_for(task, timeout=3)
        return result, card, q.get_nowait()

    result, card, closed = asyncio.run(run())
    assert result is at.REVISED and not result
    assert closed["name"] == "confirmation_resolved"
    assert closed["value"] == {"request_id": card["value"]["request_id"], "answer": "REVISED"}
    # The note stays for the tool result, which carries it to the model.
    assert steer.drain_guidance(THREAD) == ["[steer from jane] make it shorter"]


def test_an_older_note_does_not_close_the_card():
    _clean()
    steer.buffer_guidance(THREAD, "jane", "also check my calendar")

    async def run():
        _q, task, card = await _card(close_on_steer=True)
        await asyncio.sleep(1.2)  # more than two polls
        assert not task.done()
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, "APPROVE", was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid))
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(run()) is True
    _clean()


def test_a_plain_card_ignores_a_steer():
    _clean()

    async def run():
        _q, task, card = await _card()
        steer.buffer_guidance(THREAD, "jane", "make it shorter")
        await asyncio.sleep(1.2)
        assert not task.done()
        rid = card["value"]["request_id"]
        executor.resolve_user_input(
            rid, "REJECT", was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid))
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(run()) is False
    _clean()


def test_revised_is_no_approval():
    assert not at.REVISED
    assert at.REVISED is not True and at.REVISED is not None


def test_a_mark_is_a_number_never_an_address():
    """CPython gives a freed address to a new object, so a mark by ``id()``
    could make a new note look old (review round 1, P3)."""
    _clean()
    steer.buffer_guidance(THREAD, "jane", "one")
    first = steer.guidance_marks(THREAD)
    steer.drain_guidance(THREAD)
    steer.buffer_guidance(THREAD, "jane", "two")
    assert steer.guidance_marks(THREAD).isdisjoint(first)
    _clean()


# ── Item 5 (owner, 2026-10-24): a steer closes only the card it is about ─────


async def _two_cards(first: str, second: str):
    q: asyncio.Queue = asyncio.Queue()
    executor._active_run_queue.set(q)
    executor._stream_relay_thread_id.set(THREAD)
    one = asyncio.ensure_future(at.request_confirmation(first, close_on_steer=True))
    c1 = await asyncio.wait_for(q.get(), timeout=2)
    await asyncio.sleep(0.05)
    two = asyncio.ensure_future(at.request_confirmation(second, close_on_steer=True))
    c2 = await asyncio.wait_for(q.get(), timeout=2)
    return one, two, c1, c2


def _answer(card, text="REJECT"):
    rid = card["value"]["request_id"]
    executor.resolve_user_input(rid, text, was_freeform=False,
                                thread_id=executor._pending_user_input.owner_of(rid))


def test_a_steer_that_names_no_card_closes_every_waiting_card():
    """Review P3-b: a steer that names no card closes each waiting card, the
    safe default, so no older card stays answerable with old text. Mutation:
    close the newest card only -> the older card stays open -> red."""
    _clean()

    async def run():
        one, two, _c1, _c2 = await _two_cards("Send this draft to Kiran?",
                                              "Send this draft to Veena?")
        steer.buffer_guidance(THREAD, "jane", "make it shorter")
        newest = await asyncio.wait_for(two, timeout=3)
        older = await asyncio.wait_for(one, timeout=3)
        return newest, older

    newest, older = asyncio.run(run())
    assert newest is at.REVISED and older is at.REVISED
    _clean()


def test_a_steer_that_names_a_card_closes_that_card():
    _clean()

    async def run():
        one, two, _c1, c2 = await _two_cards("Send this draft to kiran@fracktal.in?",
                                             "Send this draft to veena@fracktal.in?")
        steer.buffer_guidance(THREAD, "jane", "for the kiran one, add the date")
        older = await asyncio.wait_for(one, timeout=3)
        await asyncio.sleep(1.2)
        still_open = not two.done()
        _answer(c2)
        await asyncio.wait_for(two, timeout=2)
        return older, still_open

    older, still_open = asyncio.run(run())
    assert older is at.REVISED and still_open is True
    _clean()


def test_the_target_rule_without_a_loop():
    at._STEER_CARDS["t-rule"] = {"a": (1, "Reply to sagar@jain.test?"),
                                 "b": (2, "Send this draft to kiran@fracktal.in?")}
    try:
        assert at._steer_target("t-rule", ["shorter please"]) == {"a", "b"}
        assert at._steer_target("t-rule", ["the sagar reply needs a date"]) == {"a"}
    finally:
        at._STEER_CARDS.pop("t-rule", None)
