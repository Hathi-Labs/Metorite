"""Task 3813 (owner, 2026-11-18): every path that writes mail the member sends
runs on ``tier-balanced``. Sorting, triage, analysis and previews keep their tier.

The fence lists the writing paths. A path that names another tier fails here.
Mutation: put ``tier-powerful`` back in ``EMAIL_TASK_TIERS`` or in one default
``model=`` of the drafting functions, and the matching test goes red.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from gateway.routes.email.automation import assistant, drafting

_ROOT = Path(__file__).resolve().parents[2]
BALANCED = "tier-balanced"


def test_the_task_tiers_are_balanced() -> None:
    assert dict(assistant.EMAIL_TASK_TIERS) == {k: BALANCED for k in ("draft", "compose", "chat")}


def test_each_drafting_writer_defaults_to_balanced() -> None:
    seen = 0
    for name, fn in inspect.getmembers(drafting, inspect.isfunction):
        p = inspect.signature(fn).parameters.get("model")
        if p is None or p.default is inspect.Parameter.empty:
            continue
        seen += 1
        assert p.default == BALANCED, f"{name} writes mail on {p.default}"
    assert seen >= 4, "the scan found no writer: the fence is blind"


def test_the_chat_agent_floor_is_balanced() -> None:
    src = (_ROOT / "apps/agents/agent-email-assistant/agents.py").read_text(encoding="utf-8")
    assert re.findall(r'EMAIL_AGENT_MODEL",\s*"([^"]+)"', src) == [BALANCED]
