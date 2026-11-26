"""SBX-F12: the build of a user tool (SBX-4b1, Metorite task 3986).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.4. This file holds
done-when 7 to 18, 27, 31, 35 and 36 when the step engine lands. SBX-4b1
parts (a) to (c) put the first case here: the reserved names of decision 11.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_broker_agent_names_are_reserved_at_registration(monkeypatch) -> None:
    """Done-when 7 (decision 11). ``register_agent`` answers 409 for
    ``user-tool`` and ``compute`` and saves nothing. Both names pass the name
    rule, so only the reserved set stops them.

    The set is pinned to ``COMPUTE_AGENT`` of the broker and to the literal
    ``"user-tool"``. PR 2862 adds ``USER_TOOL_AGENT``. After it merges, this
    case reads that constant.

    Mutation: drop ``BROKER_AGENT_NAMES`` from the reserved set -> red."""
    pytest.importorskip("gateway.routes.agent")
    from acb_skills.agent_paths import AGENT_NAME_RE, BROKER_AGENT_NAMES
    from fastapi import BackgroundTasks, HTTPException
    from gateway.routes import agent as routes
    from orchestrator.sandbox_broker import COMPUTE_AGENT

    assert frozenset({"user-tool", COMPUTE_AGENT}) == BROKER_AGENT_NAMES
    monkeypatch.setattr(routes, "_load_dynamic_agents", lambda: [])
    saved: list[Any] = []
    monkeypatch.setattr(routes, "_save_dynamic_agents", lambda a: saved.append(a))
    for name in sorted(BROKER_AGENT_NAMES):
        assert AGENT_NAME_RE.fullmatch(name), "the name rule alone lets it pass"
        req = routes.RegisterAgentRequest(name=name, description="x",
                                          local_path=str(REPO_ROOT))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routes.register_agent(
                req, BackgroundTasks(), user=SimpleNamespace(email="o@x.test")))
        assert exc.value.status_code == 409, name
        assert "reserved" in str(exc.value.detail), name
    assert saved == []
