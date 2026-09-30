"""Golden trajectories: HITL round-trips through the harness (HH-1).

Unlike the unit tests (which pin individual helpers), these drive the full
agent-visible trajectory: tool call → SSE card event → user answer via
``resolve_user_input`` → tool return value → pending-registry cleanup.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from acb_skills.ask_tools import ask_questions, request_confirmation
from orchestrator import executor


def _extract_request_id(sse_line: str) -> str:
    payload = json.loads(sse_line.split("data:", 1)[1].strip())
    return payload["value"]["request_id"]


async def _answer_when_pending(answer: str, thread_id: str) -> None:
    """Resolve the first pending HITL future once it registers.

    H-201: only the thread that owns the request may answer it, so the
    trajectory answers as that thread, and a stranger's answer is refused.
    """
    for _ in range(100):
        if executor._pending_user_input:
            break
        await asyncio.sleep(0.01)
    request_id = next(iter(executor._pending_user_input))
    assert executor._pending_user_input.owner_of(request_id) == thread_id
    assert executor.resolve_user_input(request_id, answer, False,
                                       thread_id="another-thread") is False
    assert executor.resolve_user_input(request_id, answer, False,
                                       thread_id=thread_id) is True


@pytest.fixture(autouse=True)
def _clean_pending():
    executor._pending_user_input.clear()
    yield
    executor._pending_user_input.clear()


async def test_ask_questions_relay_roundtrip(monkeypatch):
    """Full Path-C trajectory: card emitted to the relay, agent blocks,
    user answers, tool returns the answer, registry is clean."""
    frames: list[str] = []

    async def _fake_push(thread_id: str, line: str) -> None:
        frames.append(line)

    monkeypatch.setattr(executor, "_push_sse_to_stream", _fake_push)
    executor._stream_relay_thread_id.set("traj-thread-1")
    # Skip the Path-B executor-bridge poll (3 s) — no bridge in this
    # trajectory; a set-but-unknown request id falls straight through to C.
    executor._active_elicitation_request_id.set("no-bridge")

    answer_task = asyncio.create_task(_answer_when_pending("Staging", "traj-thread-1"))
    result = await ask_questions(json.dumps({
        "questions": [{
            "header": "Target",
            "question": "Deploy to staging or production?",
            "options": [{"label": "Staging", "recommended": True},
                        {"label": "Production"}],
        }],
    }))
    await answer_task

    assert result == "User response: Staging"
    assert len(frames) == 1
    assert "elicitation_requested" in frames[0]
    payload = json.loads(frames[0].split("data:", 1)[1].strip())
    q = payload["value"]["questions"][0]
    assert q["header"] == "Target"
    assert [o["label"] for o in q["options"]] == ["Staging", "Production"]
    assert executor._pending_user_input == {}


async def test_ask_questions_executor_bridge_roundtrip():
    """Path-B1 trajectory: the executor pre-created the Future (Copilot SDK
    bridge); the tool blocks on it and returns the resolved answer."""
    rid = "traj-bridge-rid"
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    executor._pending_user_input.park(rid, fut, "traj-bridge-thread")
    executor._active_elicitation_request_id.set(rid)
    executor._stream_relay_thread_id.set(None)

    async def _answer() -> None:
        await asyncio.sleep(0.01)
        assert executor.resolve_user_input(rid, "Use the blue theme",
                                           thread_id="traj-bridge-thread") is True

    answer_task = asyncio.create_task(_answer())
    result = await ask_questions(json.dumps({
        "questions": [{"header": "Theme", "question": "Which theme?"}],
    }))
    await answer_task

    assert result == "User response: Use the blue theme"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("APPROVE", True), ("REJECT", False), ("nonsense", False)],
)
async def test_request_confirmation_relay_roundtrip(monkeypatch, answer, expected):
    """Confirmation-card trajectory: only an explicit APPROVE proceeds."""
    frames: list[str] = []

    async def _fake_push(thread_id: str, line: str) -> None:
        frames.append(line)

    monkeypatch.setattr(executor, "_push_sse_to_stream", _fake_push)
    executor._stream_relay_thread_id.set("traj-thread-2")

    answer_task = asyncio.create_task(_answer_when_pending(answer, "traj-thread-2"))
    approved = await request_confirmation(
        "Send this email?", "To a@b.com", "Full body here",
    )
    await answer_task

    assert approved is expected
    assert len(frames) == 1
    assert "confirmation_requested" in frames[0]
    assert executor._pending_user_input == {}


async def test_request_confirmation_fails_closed_without_channel():
    """HH-2: with no way to deliver the card (non-interactive run), a
    destructive action is DENIED — never silently auto-approved."""
    executor._stream_relay_thread_id.set(None)
    denied = await request_confirmation("Send this email?", "To a@b.com")
    assert denied is False


async def test_request_confirmation_reversible_opt_in_without_channel():
    """Reversible automation can explicitly opt into proceeding unattended."""
    executor._stream_relay_thread_id.set(None)
    approved = await request_confirmation(
        "Archive 3 newsletters?", non_interactive_default="approve",
    )
    assert approved is True
