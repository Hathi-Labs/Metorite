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
