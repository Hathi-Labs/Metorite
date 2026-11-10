"""Regression tests for emit_generative_ui's blocking HITL + panel surface
(generative_ui_2 Phase 1).

``hitl: true`` parks the tool call on the same Future machinery as
ask_questions — the UI's submit resolves it via /agent/respond-input and the
values return as THIS call's result. ``surface: "panel"`` passes through to
the frontend, which opens the spec as an immersive side-panel view.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import pytest
from orchestrator import executor

# acb_skills re-exports the write_artifact FUNCTION at package level, which
# shadows the submodule under attribute access — import the module explicitly.
wa = importlib.import_module("acb_skills.write_artifact")


@pytest.fixture
def queue(monkeypatch) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid=None: q)
    wa.derive_artifact_context(session_id="t-genui")
    return q


def test_plain_emit_is_nonblocking(queue: asyncio.Queue):
    async def _run():
        res = await wa.emit_generative_ui(
            '{"type":"template","props":{"name":"weatherCard","data":{}}}'
        )
        ev = queue.get_nowait()
        return res, ev

    res, ev = asyncio.run(_run())
    assert res == {"ok": True}
    assert ev["name"] == "generative_ui"
    assert "request_id" not in ev["value"]


def test_surface_panel_passes_through(queue: asyncio.Queue):
    async def _run():
        await wa.emit_generative_ui(
            '{"type":"template","surface":"panel","title":"Trip plan",'
            '"props":{"name":"trainStatus","data":{}}}'
        )
        return queue.get_nowait()

    ev = asyncio.run(_run())
    assert ev["value"]["surface"] == "panel"
    assert ev["value"]["title"] == "Trip plan"


def test_hitl_blocks_until_user_responds(queue: asyncio.Queue):
    async def _run():
        task = asyncio.ensure_future(wa.emit_generative_ui(
            '{"type":"template","hitl":true,'
            '"props":{"name":"formCard","data":{"fields":[]}}}'
        ))
        # The event must carry a request_id registered in the HITL registry.
        ev = await asyncio.wait_for(queue.get(), timeout=2)
        req_id = ev["value"]["request_id"]
        assert req_id in executor._pending_user_input
        assert not task.done(), "hitl call must park until the user answers"
        # The frontend answers via /agent/respond-input → resolve_user_input.
        assert executor.resolve_user_input(req_id, 'Form — {"temp": 22}',
            thread_id=executor._pending_user_input.owner_of(req_id))
        res = await asyncio.wait_for(task, timeout=2)
        return req_id, res

    req_id, res = asyncio.run(_run())
    assert res["ok"] is True
    assert res["response"] == 'Form — {"temp": 22}'
    # The result names its card, so the chat keeps the card answered after a
    # reload (workbench lib/askAnswers.ts, review round 1 P2-c).
    assert res["request_id"] == req_id
    assert req_id not in executor._pending_user_input, "registry must be cleaned"


def test_hitl_flag_never_reaches_the_frontend(queue: asyncio.Queue):
    """The hitl flag is a backend contract; the frontend keys off request_id."""
    async def _run():
        task = asyncio.ensure_future(wa.emit_generative_ui(
            '{"type":"template","hitl":true,"props":{"name":"optionPicker","data":{}}}'
        ))
        ev = await asyncio.wait_for(queue.get(), timeout=2)
        executor.resolve_user_input(ev["value"]["request_id"], "Selected: A",
            thread_id=executor._pending_user_input.owner_of(ev["value"]["request_id"]))
        await task
        return ev

    ev = asyncio.run(_run())
    assert "hitl" not in ev["value"]
    assert ev["value"]["request_id"]


def test_no_active_run_cleans_up_pending_registry(monkeypatch):
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid=None: None)
    wa.derive_artifact_context(session_id="t-genui")
    before = set(executor._pending_user_input)

    async def _run() -> dict[str, Any]:
        return await wa.emit_generative_ui('{"type":"card","hitl":true}')

    res = asyncio.run(_run())
    assert res["ok"] is False
    assert set(executor._pending_user_input) == before, (
        "a failed emit must not leak a parked future"
    )


def test_parked_genui_suppresses_copilot_stall_detector(queue: asyncio.Queue):
    """While a blocking genUI awaits the user, the Copilot stall detector's
    HITL suppression must engage (it reads the same pending registry)."""
    from orchestrator import copilot_agent

    async def _run():
        task = asyncio.ensure_future(wa.emit_generative_ui(
            '{"type":"template","hitl":true,'
            '"props":{"name":"formCard","data":{"fields":[]}}}'
        ))
        ev = await asyncio.wait_for(queue.get(), timeout=2)
        suppressed = copilot_agent._hitl_pending()
        executor.resolve_user_input(ev["value"]["request_id"], "done",
            thread_id=executor._pending_user_input.owner_of(ev["value"]["request_id"]))
        await task
        return suppressed

    assert asyncio.run(_run()) is True


# ── confirmation_resolved (2026-10-06) ───────────────────────────────────────
#
# A turn made ten card-gated calls in parallel. The stream carried ten
# ``confirmation_requested`` cards and no event said which one was answered,
# so a replay showed an answered card again and every Approve got a 409.
# ``request_confirmation`` now closes each card on the stream that carried it.

at = importlib.import_module("acb_skills.ask_tools")


def _answer(rid: str, answer: str) -> bool:
    return executor.resolve_user_input(
        rid, answer, was_freeform=False,
        thread_id=executor._pending_user_input.owner_of(rid),
    )


def _parked(n: int, q: asyncio.Queue):
    """Start *n* confirmations in parallel on the native queue path."""
    executor._active_run_queue.set(q)
    return [
        asyncio.ensure_future(at.request_confirmation("Create this task?", f"«Task {i}»"))
        for i in range(n)
    ]


@pytest.mark.parametrize("answer, approved", [("APPROVE", True), ("REJECT", False)])
def test_an_answer_closes_its_card_on_the_stream(queue, answer, approved):
    """Mutation caught: dropping the publish in ``_block_on``'s ``finally``."""
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        (task,) = _parked(1, q)
        ev = await asyncio.wait_for(q.get(), timeout=2)
        rid = ev["value"]["request_id"]
        assert _answer(rid, answer)
        result = await asyncio.wait_for(task, timeout=2)
        return rid, result, q.get_nowait()

    rid, result, closed = asyncio.run(_run())
    assert result is approved
    assert closed == {
        "type": "CUSTOM",
        "name": at.CONFIRMATION_RESOLVED,
        "value": {"request_id": rid, "answer": answer},
    }
    assert rid not in executor._pending_user_input


def test_ten_parallel_cards_one_answer_closes_only_that_one(queue):
    """Mutation caught: a resolved event that names the wrong id, or one per run."""
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        tasks = _parked(10, q)
        cards = [await asyncio.wait_for(q.get(), timeout=2) for _ in range(10)]
        ids = [c["value"]["request_id"] for c in cards]
        assert _answer(ids[3], "APPROVE")
        assert await asyncio.wait_for(tasks[3], timeout=2) is True
        closed = [q.get_nowait() for _ in range(q.qsize())]
        still = [rid for rid in ids if rid in executor._pending_user_input]
        for rid in still:
            _answer(rid, "REJECT")
        await asyncio.gather(*tasks)
        return cards, ids, closed, still

    cards, ids, closed, still = asyncio.run(_run())
    assert {c["name"] for c in cards} == {"confirmation_requested"}
    assert len(set(ids)) == 10, "each parallel card has its own request_id"
    assert [c["value"] for c in closed] == [{"request_id": ids[3], "answer": "APPROVE"}]
    assert still == ids[:3] + ids[4:]


def test_a_timeout_closes_the_card(queue, monkeypatch):
    """Mutation caught: publishing only on an answer, so a timed-out card
    stays open on every replay."""
    async def _timeout(_fut, _timeout_s, thread_id=None):
        raise TimeoutError

    monkeypatch.setattr(executor, "wait_user_future", _timeout)

    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        (task,) = _parked(1, q)
        result = await asyncio.wait_for(task, timeout=2)
        return result, [q.get_nowait() for _ in range(q.qsize())]

    result, events = asyncio.run(_run())
    assert result is False
    assert events[-1]["name"] == at.CONFIRMATION_RESOLVED
    assert events[-1]["value"]["answer"] == "TIMEOUT"
    assert events[-1]["value"]["request_id"] == events[0]["value"]["request_id"]


def test_a_cancel_closes_the_card_and_still_cancels(queue):
    """Mutation caught: swallowing the CancelledError to publish."""
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        (task,) = _parked(1, q)
        first = await asyncio.wait_for(q.get(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first, q.get_nowait()

    first, closed = asyncio.run(_run())
    assert closed["value"] == {
        "request_id": first["value"]["request_id"], "answer": "CANCELLED",
    }


def test_the_relay_path_closes_the_card_on_the_same_stream(monkeypatch):
    """The Copilot path pushes to the Redis relay, not to a queue. The
    closing event goes to the thread that carried the card.
    Mutation caught: a relay path that never publishes."""
    pushed: list[tuple[str, str]] = []

    async def _fake_push(tid: str, line: str) -> None:
        pushed.append((tid, line))

    monkeypatch.setattr(executor, "_push_sse_to_stream", _fake_push)

    async def _run():
        executor._active_run_queue.set(None)
        executor._stream_relay_thread_id.set("t-relay")
        task = asyncio.ensure_future(at.request_confirmation("Send this email?", "To a@b.com"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if pushed:
                break
        rid = json.loads(pushed[0][1].removeprefix("data: "))["value"]["request_id"]
        assert executor.resolve_user_input(rid, "REJECT", was_freeform=False, thread_id="t-relay")
        return rid, await asyncio.wait_for(task, timeout=2)

    rid, result = asyncio.run(_run())
    assert result is False
    assert [tid for tid, _ in pushed] == ["t-relay", "t-relay"]
    closed = json.loads(pushed[1][1].removeprefix("data: "))
    assert closed["name"] == at.CONFIRMATION_RESOLVED
    assert closed["value"] == {"request_id": rid, "answer": "REJECT"}
    assert set(at.CONFIRMATION_ANSWERS) == {"APPROVE", "REJECT", "TIMEOUT", "CANCELLED"}
