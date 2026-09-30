"""Unit tests for the native ask_user HITL round-trip in the executor.

Covers the on_user_input_request handler factory and resolve_user_input:
  - the handler parks on a Future and returns the resolved answer
  - resolve_user_input unblocks it and reports delivery status
  - a stale / unknown request_id is reported as not-delivered
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator import executor


@pytest.mark.asyncio
async def test_handler_blocks_then_resolves(monkeypatch):
    """The handler should return the answer posted via resolve_user_input."""
    captured: list[str] = []

    async def _fake_push(thread_id: str, line: str) -> None:
        captured.append(line)

    monkeypatch.setattr(executor, "_push_sse_to_stream", _fake_push)

    handler = executor._make_user_input_handler("thread-xyz")

    request = {"question": "Proceed?", "choices": ["Yes", "No"],
               "allowFreeform": False}

    async def _answer_after_delay() -> None:
        # Wait until the handler has registered its pending Future.
        for _ in range(50):
            if executor._pending_user_input:
                break
            await asyncio.sleep(0.01)
        request_id = next(iter(executor._pending_user_input))
        # H-201: the answer must name the thread that owns the request.
        assert executor.resolve_user_input(request_id, "Yes", False,
                                           thread_id="other-thread") is False
        assert executor.resolve_user_input(request_id, "Yes", False,
                                           thread_id="thread-xyz") is True

    answer_task = asyncio.create_task(_answer_after_delay())
    result = await handler(request, {"session_id": "s"})
    await answer_task

    assert result == {"answer": "Yes", "wasFreeform": False}
    # The prompt was emitted to the relay exactly once.
    assert len(captured) == 1
    assert "user_input_requested" in captured[0]
    # Pending registry is cleaned up after resolution.
    assert executor._pending_user_input == {}


def test_resolve_unknown_request_id_returns_false():
    assert executor.resolve_user_input("does-not-exist", "x", thread_id="t") is False
