"""Regression tests — chat-stack hardening batches 1-2.

Batch 1: review P0-1/2/4/5, P1-8.  Batch 2: P0-7/8, P1-4.
See project-docs/specs/chat_implementation_review_2026-07.md.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_push_event_refreshes_active_flag_without_resurrecting():
    """P0-2: every push refreshes cc:active TTL — but only when it still
    exists (xx=True), so a cancelled run isn't marked active again."""
    from orchestrator import stream_relay

    r = MagicMock()
    r.xadd = AsyncMock(return_value="1-1")
    r.expire = AsyncMock()
    r.set = AsyncMock()
    with patch.object(stream_relay, "_get_client", AsyncMock(return_value=r)):
        await stream_relay.push_event("t1", {"type": "X"})
    r.set.assert_awaited_once()
    _, kwargs = r.set.await_args
    assert kwargs.get("xx") is True, "active refresh must not resurrect a finished run"
    assert kwargs.get("ex") == stream_relay.STREAM_TTL_SECONDS


def test_reconnect_endpoint_enforces_thread_ownership():
    """P0-1: the replayed stream is the whole conversation — reconnect must
    apply the same owner guard as cancel."""
    from gateway.routes import agent as agent_routes

    src = inspect.getsource(agent_routes.reconnect_agent_stream)
    assert "_thread_owner_ok" in src
    assert "403" in src


def test_session_upsert_attributes_real_user_and_never_downgrades():
    """P1-8: the Copilot session-store upsert uses the acting user (not a
    hardcoded 'system') and only claims system-owned rows."""
    from orchestrator import executor

    src = inspect.getsource(executor._store_session_id)
    assert "_get_memory_user_id" in src
    assert "chat_session.user_id = 'system'" in src
    assert '"uid": "system",' not in src


def test_translator_persists_real_tool_status_and_stable_stream_id():
    """P0-5: failed tools persist as error; P0-4: the fallback persistence id
    is minted once per stream (not per thread, not per checkpoint)."""
    route = (REPO / "workbench/control_plane/src/app/api/agent/chat/route.ts").read_text(encoding="utf-8")
    assert 'status: ev.success !== false ? "done" : "error"' in route
    assert "const persistId =" in route
    # the collapsing per-thread constant must not be the effective fallback
    assert "id: messageId || `assistant-${threadId}`," not in route


# ── Batch 2: delegation guards, sub-agent HITL, cancel cascade ──────────────


def test_delegation_guard_refuses_cycles_and_depth():
    """P0-7: A→B→A cycles and chains beyond SUB_AGENT_MAX_DEPTH are refused
    with an instructive message instead of recursing."""
    from acb_skills import agent_tools as at

    assert at._delegation_refusal("agent-a") is None  # root: allowed

    tok = at._delegation_chain.set(("agent-a",))
    try:
        cycle = at._delegation_refusal("agent-a")
        assert cycle is not None and "cycle" in cycle
        assert at._delegation_refusal("agent-b") is None  # depth 1 < 2: allowed
    finally:
        at._delegation_chain.reset(tok)

    tok = at._delegation_chain.set(("agent-a", "agent-b"))
    try:
        depth = at._delegation_refusal("agent-c")
        assert depth is not None and "depth" in depth
    finally:
        at._delegation_chain.reset(tok)

    assert at._delegation_refusal("agent-a") is None  # context restored


@pytest.mark.asyncio
async def test_call_agent_refuses_before_spawning():
    """P0-7: the refusal happens before any sub-agent work starts."""
    from acb_skills import agent_tools as at

    tok = at._delegation_chain.set(("looper",))
    try:
        out = await at.call_agent("looper", "again")
    finally:
        at._delegation_chain.reset(tok)
    assert "Delegation refused" in out


def test_delegation_paths_have_wallclock_timeouts():
    """P1-4: every awaited delegation path is bounded by wait_for so a
    slow-but-not-idle sub-agent can't hold the parent open forever."""
    from acb_skills import agent_tools as at

    assert at._SUB_AGENT_TIMEOUT > 0
    for fn in (at.call_agent, at.call_agents_parallel):
        src = inspect.getsource(fn)
        assert "wait_for" in src, f"{fn.__name__} lacks a delegation timeout"
        assert "_SUB_AGENT_TIMEOUT" in src


@pytest.mark.asyncio
async def test_cancel_run_cascades_to_background_children():
    """P1-4: Stop on the parent thread cancels registered background
    sub-agent tasks instead of leaving them burning tokens."""
    from orchestrator import stream_relay

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _child() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(_child())
    stream_relay.register_background_child("t-cascade", task)
    await started.wait()

    with patch.object(stream_relay, "mark_inactive", AsyncMock()), \
         patch.object(stream_relay, "push_event", AsyncMock()):
        await stream_relay.cancel_run("t-cascade")

    assert cancelled.is_set(), "background child must be cancelled with the run"
    assert "t-cascade" not in stream_relay._BACKGROUND_CHILDREN


@pytest.mark.asyncio
async def test_background_child_registry_self_cleans():
    """Completed children deregister themselves — no leak between runs."""
    from orchestrator import stream_relay

    async def _quick() -> None:
        return None

    task = asyncio.create_task(_quick())
    stream_relay.register_background_child("t-clean", task)
    await task
    await asyncio.sleep(0)  # let done-callbacks run
    assert "t-clean" not in stream_relay._BACKGROUND_CHILDREN


# ── Batch 3: sub-agent reconnect parity, dup user turn, HITL lifecycle ──────


def test_sub_agent_events_persisted_and_replayed():
    """P0-6: the translator persists the nested delegation timeline on the
    parent call_agent tool event, and BOTH frontend SSE loops route
    sub_agent_* events through the shared applySubAgentEvent reducer."""
    route = (REPO / "workbench/control_plane/src/app/api/agent/chat/route.ts").read_text(encoding="utf-8")
    # Server-side accumulation + attachment to the persisted delegate tool.
    assert "subAgent.text +=" in route
    assert "subAgentTools: subAgent.tools" in route
    assert "...subAgentFields," in route

    hook = (REPO / "workbench/control_plane/src/hooks/useAgentChat.ts").read_text(encoding="utf-8")
    # Shared reducer used (no inline duplicated sub-agent state logic left) …
    assert hook.count("applySubAgentEvent(m, evt)") >= 2, (
        "both the live AND reconnect loops must route sub_agent_* events "
        "through the shared reducer"
    )
    reducer = (REPO / "workbench/control_plane/src/lib/chatStream.ts").read_text(encoding="utf-8")
    assert "export function applySubAgentEvent" in reducer


def test_current_turn_not_duplicated_in_history():
    """P1-1: the frontend excludes the just-appended user turn (and the
    assistant placeholder) from history, and the route drops a trailing
    duplicate server-side on the copilot/executor paths."""
    hook = (REPO / "workbench/control_plane/src/hooks/useAgentChat.ts").read_text(encoding="utf-8")
    assert ".messages.slice(0, -2)" in hook
    route = (REPO / "workbench/control_plane/src/app/api/agent/chat/route.ts").read_text(encoding="utf-8")
    assert "function withoutCurrentTurn" in route
    assert route.count("withoutCurrentTurn(") >= 3  # orchestrator + executor + legacy


def test_hitl_cards_cleared_on_session_switch_and_failures_surfaced():
    """P1-3: HITL card state resets when the user switches sessions, and a
    failed respond-input POST restores the card instead of being swallowed
    (fire-and-forget left a parked agent with no card and no error)."""
    chat = (REPO / "workbench/control_plane/src/components/AgentChat.tsx").read_text(encoding="utf-8")
    # Session-switch reset (React reset-during-render pattern).
    assert "setHitlSession(sessionId);" in chat
    assert (
        'dispatchConfirmation({ type: "reset" });\n    setElicitation(null);\n    setUserInput(null);'
        in chat
    )
    # All blocking answers go through the restoring helper. The ONLY fetch to
    # respond-input is `lib/respondInput.ts`, which owns the failure rule (a
    # 4xx drops the card, a 5xx or a network fault restores it).
    assert chat.count("postRespondInput(") >= 4
    assert 'fetch("/api/agent/respond-input"' not in chat
    helper = (REPO / "workbench/control_plane/src/lib/respondInput.ts").read_text(encoding="utf-8")
    assert helper.count('"/api/agent/respond-input"') == 1


def test_confirmation_resolved_name_agrees_across_the_wire():
    """2026-10-06: the server closes each confirmation card with
    ``confirmation_resolved``. The client drops the card, keeps the event off
    the message, and hides it in the panel. A rename on one side alone puts
    the answered card back on every replay. Mutation caught: renaming the
    constant in ``ask_tools.py`` or in ``lib/confirmationQueue.ts`` alone."""
    from acb_skills.ask_tools import CONFIRMATION_RESOLVED

    cp = REPO / "workbench/control_plane/src"
    queue = (cp / "lib/confirmationQueue.ts").read_text(encoding="utf-8")
    assert f'export const CONFIRMATION_RESOLVED = "{CONFIRMATION_RESOLVED}";' in queue
    for rel in ("hooks/useAgentChat.ts", "components/GenerativeUIPanel.tsx"):
        src = (cp / rel).read_text(encoding="utf-8")
        assert f'"{CONFIRMATION_RESOLVED}"' in src, rel
    chat = (cp / "components/AgentChat.tsx").read_text(encoding="utf-8")
    assert "name === CONFIRMATION_RESOLVED" in chat


def test_sub_agent_streaming_binds_hitl_handler():
    """P0-8: delegated Copilot SDK agents get on_user_input_request bound to
    the parent thread's handler, so their ask_user renders a card instead of
    parking the SDK on an unanswered Future."""
    from orchestrator import executor

    src = inspect.getsource(executor._run_sub_agent_streaming)
    assert 'on_user_input_request' in src
    assert "_make_user_input_handler(_relay_tid)" in src
