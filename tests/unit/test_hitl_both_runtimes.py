"""Regression tests: blocking HITL (ask_questions / request_confirmation) works
on BOTH the GitHub-Copilot SDK path and the native-MAF path.

Bug (2026-07-03, startup-guru): multi-question ``ask_questions`` on a Copilot
agent did not block — the card looped and never accepted input, while
single-question ``ask_user`` (SDK-native) worked. Root cause: the tool's
blocking paths keyed off ``_stream_relay_thread_id`` / ``_active_elicitation_request_id``
ContextVars, which the Copilot SDK RESETS when it dispatches a tool from its
JSON-RPC read thread (run_coroutine_threadsafe → fresh context). On native MAF
the ContextVar is visible so it blocked; on Copilot it wasn't so it fell through
to the non-blocking path. The SAME bug affected ``request_confirmation`` (the
send-email safety gate) — worse, it silently denied without showing the card.

Fix: ``executor.resolve_relay_thread_id()`` resolves the thread_id from a
thread-hop-surviving source with the ContextVar as the first choice — so the
Redis-relay blocking path (Path C) engages for BOTH runtimes. That source was a
process-global dict until H-201 (§21.16). It is now the run's own artifact
context, which ``carry_run_context`` carries into the Copilot SDK callbacks.
"""
from __future__ import annotations

import asyncio

import orchestrator.executor as ex
from acb_skills.write_artifact import (
    derive_artifact_context,
)


def _clear_relay_state() -> None:
    ex._stream_relay_thread_id.set(None)
    derive_artifact_context(session_id=None)
    ex._RUN_QUEUES.clear()


# ---------------------------------------------------------------------------
# resolve_relay_thread_id — the cross-runtime resolver
# ---------------------------------------------------------------------------

def test_maf_path_uses_contextvar() -> None:
    """Native-MAF: the ContextVar is visible → used directly (first choice)."""
    _clear_relay_state()
    ex._stream_relay_thread_id.set("thread-maf")
    try:
        assert ex.resolve_relay_thread_id() == "thread-maf"
    finally:
        _clear_relay_state()


def test_copilot_path_survives_thread_hop_via_run_context() -> None:
    """Copilot SDK: the ContextVar was RESET by the thread hop (None), but the
    run artifact context still carries session_id → the resolver finds the thread.

    This is the core of the fix — the case that used to return None and break
    blocking on Copilot agents."""
    _clear_relay_state()
    # Simulate the thread hop: ContextVar reset to None...
    ex._stream_relay_thread_id.set(None)
    # ...but carry_run_context gave the tool the run artifact context.
    derive_artifact_context(session_id="thread-copilot")
    try:
        assert ex.resolve_relay_thread_id() == "thread-copilot"
    finally:
        _clear_relay_state()


def test_contextvar_wins_over_run_context() -> None:
    """When both are set (native MAF with a stale dict), the live ContextVar wins."""
    _clear_relay_state()
    ex._stream_relay_thread_id.set("thread-live")
    derive_artifact_context(session_id="thread-stale")
    try:
        assert ex.resolve_relay_thread_id() == "thread-live"
    finally:
        _clear_relay_state()


def test_no_context_never_guesses_the_single_active_run() -> None:
    """No ContextVar and no run context, and one run live → ``None``.

    H-201 (§21.16): the old third step answered "the single live run" for a
    frame with no run. That frame's HITL card then parked in another run's
    thread. It fails closed now.
    """
    _clear_relay_state()
    ex._RUN_QUEUES["only-thread"] = asyncio.Queue()
    try:
        assert ex.resolve_relay_thread_id() is None
    finally:
        _clear_relay_state()


def test_returns_none_when_nothing_resolvable() -> None:
    _clear_relay_state()
    assert ex.resolve_relay_thread_id() is None


# ---------------------------------------------------------------------------
# ask_questions / request_confirmation actually BLOCK on the Copilot path
# ---------------------------------------------------------------------------

def test_ask_questions_blocks_on_copilot_thread_hop(monkeypatch) -> None:
    """With ONLY the module-dict thread_id set (Copilot thread-hop simulation),
    ask_questions parks on a Future and returns the user's answer — it does NOT
    fall through to the non-blocking 'Questions displayed… waiting' string."""
    import acb_skills.ask_tools as at

    _clear_relay_state()
    ex._stream_relay_thread_id.set(None)          # thread hop reset it
    derive_artifact_context(session_id="t-copilot-aq")

    pushed: list[str] = []

    async def _fake_push(tid: str, line: str) -> None:
        pushed.append(line)

    monkeypatch.setattr(ex, "_push_sse_to_stream", _fake_push)

    async def _run() -> str:
        task = asyncio.create_task(at.ask_questions(
            '{"questions":[{"header":"Region","question":"Which region?",'
            '"options":[{"label":"Delhi"},{"label":"Mumbai"}]}]}'
        ))
        # Let the tool reach the blocking wait + push the card.
        for _ in range(40):
            await asyncio.sleep(0.02)
            if pushed:
                break
        assert pushed, "ask_questions should have pushed an elicitation card"
        # Extract the request_id it parked on, then resolve it like the frontend.
        import json
        payload = json.loads(pushed[0].removeprefix("data: ").strip())
        rid = payload["value"]["request_id"]
        # H-201: the request is owned by the thread its card went to, even
        # across the Copilot thread hop, and only that thread may answer it.
        assert ex._pending_user_input.owner_of(rid) == "t-copilot-aq"
        assert ex.resolve_user_input(rid, "Delhi", was_freeform=False,
                                     thread_id="another-thread") is False
        assert ex.resolve_user_input(rid, "Delhi", was_freeform=False,
                                     thread_id="t-copilot-aq") is True
        return await asyncio.wait_for(task, timeout=5)

    result = asyncio.run(_run())
    _clear_relay_state()
    assert "Delhi" in result
    assert "waiting" not in result.lower(), "must block, not return the non-blocking string"


def test_request_confirmation_blocks_on_copilot_thread_hop(monkeypatch) -> None:
    """request_confirmation (the send-email safety gate) must show the card and
    block on the Copilot path — not silently fall through to the deny default."""
    import acb_skills.ask_tools as at

    _clear_relay_state()
    ex._stream_relay_thread_id.set(None)
    derive_artifact_context(session_id="t-copilot-rc")

    pushed: list[str] = []

    async def _fake_push(tid: str, line: str) -> None:
        pushed.append(line)

    monkeypatch.setattr(ex, "_push_sse_to_stream", _fake_push)

    async def _run() -> bool:
        task = asyncio.create_task(at.request_confirmation("Send this email?", "To a@b.com"))
        for _ in range(40):
            await asyncio.sleep(0.02)
            if pushed:
                break
        assert pushed, "request_confirmation should have pushed a card (not silently denied)"
        import json
        payload = json.loads(pushed[0].removeprefix("data: ").strip())
        rid = payload["value"]["request_id"]
        # H-201: the request is owned by the thread its card went to, even
        # across the Copilot thread hop, and only that thread may answer it.
        assert ex._pending_user_input.owner_of(rid) == "t-copilot-rc"
        assert ex.resolve_user_input(rid, "APPROVE", was_freeform=False,
                                     thread_id="another-thread") is False
        assert ex.resolve_user_input(rid, "APPROVE", was_freeform=False,
                                     thread_id="t-copilot-rc") is True
        return await asyncio.wait_for(task, timeout=5)

    approved = asyncio.run(_run())
    _clear_relay_state()
    assert approved is True, "user approved via the card → must return True, not the deny default"
