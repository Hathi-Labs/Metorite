"""Regression tests for the disappearing HITL question cards.

Root cause (two layers, same 5-minute fuse):
  1. MetoriteCopilotAgent._stream_updates raised "session stalled" after
     COPILOT_STREAM_STALL_TIMEOUT (300s) of silence — but a blocking
     ask_user/ask_questions parks the run on a Future while the HUMAN answers,
     which is legitimate silence. The killed run emitted RUN_FINISHED and the
     frontend cleared the question card without input.
  2. The Tier-2 batch shim bounded every async tool with
     COPILOT_TOOL_TIMEOUT_SECONDS (300s) — cancelling a parked HITL tool
     mid-question.

Fixes: the stall detector suppresses while executor._pending_user_input is
non-empty (up to HITL_IDLE_TIMEOUT_SECONDS), and the HITL tools joined
call_agent on the per-tool-timeout exemption list.
"""
from __future__ import annotations

import asyncio

import pytest
from orchestrator import copilot_agent, executor


def test_hitl_pending_reflects_executor_registry():
    executor._pending_user_input.clear()
    assert copilot_agent._hitl_pending() is False
    fut: asyncio.Future = asyncio.get_event_loop_policy().new_event_loop().create_future()
    try:
        executor._pending_user_input["req-1"] = fut
        assert copilot_agent._hitl_pending() is True
    finally:
        executor._pending_user_input.clear()


def test_hitl_stall_budget_defaults_to_an_hour():
    # The suppression budget must dwarf the 5-min stall fuse, matching the
    # native-MAF tiered watchdog's HITL budget.
    assert copilot_agent._HITL_STALL_TIMEOUT >= 3600
    assert copilot_agent._HITL_STALL_TIMEOUT > copilot_agent._STREAM_STALL_TIMEOUT


@pytest.mark.asyncio
async def test_tier2_shim_exempts_hitl_tools_from_per_tool_timeout():
    # Source-level guard: the exemption branch must cover the blocking HITL
    # tools, not just call_agent (regression: they were cancelled at 300s).
    import inspect
    src = inspect.getsource(executor)
    for name in ("ask_questions", "ask_user", "request_confirmation"):
        assert f'"{name}"' in src, f"{name} missing from per-tool timeout exemption"


# ── Tool-in-flight grace (audit C3) ─────────────────────────────────────────
# A long silent tool (multi-minute shell build/test) emits no events until it
# completes; the flat 300s fuse killed it with a misleading "CLI subprocess
# may have crashed". The stall branch now grants the native watchdog's
# tool_open budget while any tool is in flight.


def test_tool_stall_budget_exceeds_bare_fuse():
    assert copilot_agent._TOOL_STALL_TIMEOUT > copilot_agent._STREAM_STALL_TIMEOUT


def test_stall_branch_grants_tool_in_flight_grace():
    import inspect
    src = inspect.getsource(copilot_agent.MetoriteCopilotAgent._stream_updates)
    assert "_tools_in_flight" in src, "stall loop must know about running tools"
    assert "_TOOL_STALL_TIMEOUT" in src
    # The counter must be maintained on BOTH tool event families (built-in
    # TOOL_EXECUTION_* and injected EXTERNAL_TOOL_*), start and completion.
    assert src.count("_tools_in_flight.add(") >= 2
    assert src.count("_tools_in_flight.discard(") >= 2


# ── Blocking generative UI (generative_ui_2 Phase 1) ────────────────────────
# emit_generative_ui(hitl:true) parks the turn exactly like ask_questions, so
# every HITL suppression/exemption surface must recognise it on BOTH runtimes.


def test_emit_generative_ui_is_a_blocking_hitl_tool():
    from acb_skills.ask_tools import is_hitl_blocking_tool
    assert is_hitl_blocking_tool("emit_generative_ui")


def test_tier2_shim_exempts_blocking_genui_from_per_tool_timeout():
    import inspect
    src = inspect.getsource(executor)
    assert '"emit_generative_ui"' in src, (
        "emit_generative_ui missing from the per-tool timeout exemption — a "
        "blocking form would be cancelled at 300s mid-fill on the batch path"
    )


def test_tier1_watchdog_recognises_blocking_genui_events():
    """The native-MAF idle watchdog must grant the HITL budget (3600s) while a
    generative_ui event carrying a request_id is pending — not the 600s
    tool-open budget."""
    import inspect
    src = inspect.getsource(executor.run_agent_stream)
    assert "_is_hitl_event" in src
    assert '"generative_ui"' in src and "request_id" in src
