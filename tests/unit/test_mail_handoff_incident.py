"""WS-17 incident 2026-10-18: an email hand-off on WhatsApp.

Spec ``project-docs/specs/email_app_master_plan.md`` §12.3.13.8, "Incident
2026-10-18". The member asked over WhatsApp to send a mail. Each email
request of the thread was refused for good, and one run timed out.

This file fences the WhatsApp half of the fix. The egress half (a hand-off
asks no card, a delegated mail agent keeps its own carded writes, the mark
ends with the replay window) lives in ``test_delegation_no_egress.py``.

* The replay floor that ``bot_run`` gives a WhatsApp run
  (``_history_floor_ms``), and the row time that never reaches the model.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from gateway.routes.whatsapp_channel import bot_run


def _rows(n: int, start: int = 1_000) -> list[dict[str, Any]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}",
             "timestamp_ms": start + i} for i in range(n)]


def test_the_floor_is_the_oldest_replayed_row_of_a_full_window(monkeypatch) -> None:
    """A full window of ``HISTORY_TURNS`` rows gives the time of its oldest
    row. A shorter thread replays all of its rows, so the floor is 0, and so
    are a row with no time and a run that a stored session can replay."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: False)
    full = _rows(bot_run.HISTORY_TURNS)
    assert bot_run._history_floor_ms(full) == 1_000
    assert bot_run._history_floor_ms(full[1:]) == 0
    assert bot_run._history_floor_ms([*full[:-1], {"role": "user", "content": "x"}]) == 0
    monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: True)
    assert bot_run._history_floor_ms(full) == 0


def test_the_row_time_never_reaches_the_model() -> None:
    """``_thread_turns`` keeps each row's time for the floor. The payload's
    turns hold the role and the text only."""
    payload = bot_run.build_payload("hi", _rows(3), "m@example.com")
    assert payload["messages"] == [
        {"role": "user", "content": "t0"},
        {"role": "assistant", "content": "t1"},
        {"role": "user", "content": "t2"},
    ]
    assert payload["_history_loader"]() == payload["messages"]


# ── Fix 4: a delegation ends in time for the answer ─────────────────────────


def test_a_whatsapp_deadline_caps_each_delegation() -> None:
    """Outside a run with a deadline, a delegation gets the long budget. In a
    WhatsApp run it ends ``DELEGATION_RESERVE_S`` before the deadline, and
    never gets less than ``DELEGATION_MIN_S``."""
    import time

    from acb_skills import agent_tools as at
    from acb_skills.whatsapp_ui import whatsapp_run

    assert at.delegation_timeout() == at._SUB_AGENT_TIMEOUT
    with whatsapp_run("orchestrator", deadline=time.monotonic() + bot_run.RUN_TIMEOUT_S):
        budget = at.delegation_timeout()
        assert budget >= bot_run.RUN_TIMEOUT_S - at.DELEGATION_RESERVE_S - 2
        assert budget <= bot_run.RUN_TIMEOUT_S - at.DELEGATION_RESERVE_S
    with whatsapp_run("orchestrator", deadline=time.monotonic() + 5):
        assert at.delegation_timeout() == at.DELEGATION_MIN_S
    with whatsapp_run("orchestrator"):
        assert at.delegation_timeout() == at._SUB_AGENT_TIMEOUT


async def test_a_slow_hand_off_returns_in_time_for_the_answer(monkeypatch) -> None:
    """Run 15949617: two hand-offs took 101 s and 108 s, and the WhatsApp run
    hit its limit while the orchestrator wrote its answer. Now ``call_agent``
    and a specialist tool stop a slow hand-off before the deadline, and the
    model reads why, so it can still answer."""
    import asyncio
    import time

    from acb_skills import agent_tools as at
    from acb_skills.whatsapp_ui import whatsapp_run
    from orchestrator import agents as orch_package
    from orchestrator import executor

    # ``orchestrator/agents.py`` loads as ``orchestrator._agents_module``,
    # because the package ``orchestrator/agents/`` shadows its name.
    orch_agents = orch_package._mod

    async def hang(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("the hand-off was not stopped")

    monkeypatch.setattr(at, "DELEGATION_MIN_S", 0.2)
    monkeypatch.setattr(executor, "run_agent", hang)
    monkeypatch.setattr(orch_agents, "delegate_to_agent", hang)
    import gateway.routes.agent as routes_agent

    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    specialist = next(t for t in orch_agents._load_specialist_agents_as_tools()
                      if t.name == "email_assistant")
    for call in (lambda: at.call_agent("email-assistant", "send a top-up mail"),
                 lambda: specialist.func(task="send a top-up mail")):
        with whatsapp_run("orchestrator", deadline=time.monotonic() + at.DELEGATION_RESERVE_S):
            started = time.monotonic()
            out = await asyncio.wait_for(call(), 10)
        assert time.monotonic() - started < 5, "the hand-off ran past its budget"
        assert "timed out" in out and "time limit" in out, out
