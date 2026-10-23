"""``save_memory`` tells the truth when Mem0 is off (WS-63 PZ-1).

Spec: ``project-docs/specs/personalization.md`` §2.1. ``MemoryClient.add``
returns in silence when Mem0 is off, so the tool used to answer "Memory
saved." and keep nothing. This file is the fence of the honest answer:

- when the settings switch is False, the tool never says "Memory saved.",
  and it calls nothing in ``acb_memory``;
- when the switch is True and the writer is a stub, it saves and says so.

The switch is a stub in this process only. No test here sets an env var.
"""
from __future__ import annotations

import asyncio
import types
from typing import Any

import acb_memory
import pytest
from acb_skills import memory_tools


class _Settings:
    def __init__(self, enabled: bool) -> None:
        self.mem0_enabled = enabled


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    seen: list[tuple[str, Any]] = []

    async def _add(user_id: str, messages: Any, agent_id: str = "") -> None:
        seen.append((user_id, agent_id))

    monkeypatch.setattr(acb_memory, "add_memories_background", _add)
    return seen


def _mem0(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    import acb_common

    monkeypatch.setattr(acb_common, "get_settings", lambda: _Settings(enabled))


def _save(fact: str, user: str = "ana@alpha.example") -> str:
    async def _run() -> str:
        token = memory_tools._memory_user_id.set(user)
        try:
            return await memory_tools.save_memory(fact)
        finally:
            memory_tools._memory_user_id.reset(token)

    return asyncio.run(_run())


def test_mem0_off_never_says_saved_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, Any]],
) -> None:
    _mem0(monkeypatch, False)
    answer = _save("I travel every Friday")
    assert answer == memory_tools.MEMORY_OFF
    assert "saved." not in answer.lower()
    assert "memory is off" in answer
    assert calls == []


def test_mem0_off_answers_off_even_with_no_user(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, Any]],
) -> None:
    _mem0(monkeypatch, False)
    assert _save("x", user="") == memory_tools.MEMORY_OFF
    assert calls == []


def test_settings_without_the_switch_count_as_off(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, Any]],
) -> None:
    import acb_common

    monkeypatch.setattr(acb_common, "get_settings", lambda: types.SimpleNamespace())
    assert _save("I travel every Friday") == memory_tools.MEMORY_OFF
    assert calls == []


def test_mem0_on_saves_through_the_writer(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, Any]],
) -> None:
    _mem0(monkeypatch, True)
    assert _save("I travel every Friday") == "Memory saved."
    assert calls == [("ana@alpha.example", "memory_tool")]


def test_the_tool_reads_the_same_switch_as_the_client() -> None:
    """``MemoryClient._get_client`` returns None when the switch is False.
    The tool must read that same switch, or the two can disagree again."""
    import inspect

    from acb_memory.mem0_client import MemoryClient

    assert '"mem0_enabled"' in inspect.getsource(MemoryClient._get_client)
    assert '"mem0_enabled"' in inspect.getsource(memory_tools._mem0_on)
