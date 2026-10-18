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
