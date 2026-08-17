"""Test that MetoriteCopilotAgent re-applies identity on session resume.

Regression for the "agent says it is GitHub CLI after refresh" bug: the
upstream GitHubCopilotAgent._resume_session drops system_message (the
agent's instructions / identity), provider (BYOK) and model.  Resuming a
stored session on page refresh / re-opening an old chat therefore lost the
agent persona.  MetoriteCopilotAgent._resume_session must forward them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class _CapturingClient:
    """Stand-in CopilotClient that records the config passed to resume."""

    def __init__(self) -> None:
        self.resume_config: dict[str, Any] | None = None
        self.resume_session = AsyncMock(side_effect=self._resume)

    async def _resume(self, session_id: str, config: dict[str, Any]) -> Any:
        self.resume_config = config
        return MagicMock(session_id=session_id)


@pytest.fixture
def agent() -> Any:
    from orchestrator.copilot_agent import MetoriteCopilotAgent

    a = MetoriteCopilotAgent(
        name="agent-sales-assistant",
        instructions="You are SALES ASSISTANT, a Fracktal Works sales agent.",
    )
    a._started = True
    return a


class TestResumeReappliesIdentity:

    async def test_resume_forwards_system_message(self, agent: Any) -> None:
        """The agent's system_message (identity) must be sent on resume."""
        client = _CapturingClient()
        agent._client = client

        await agent._resume_session("sess-123", streaming=True)

        cfg = client.resume_config
        assert cfg is not None
        assert "system_message" in cfg, (
            "Resume MUST forward system_message or the agent loses its "
            "identity and reverts to the GitHub Copilot CLI persona."
        )
        sm = cfg["system_message"]
        content = sm["content"] if isinstance(sm, dict) else sm
        assert "SALES ASSISTANT" in content

    async def test_resume_forwards_byok_provider_and_model(
        self, agent: Any,
    ) -> None:
        """BYOK provider + model must survive resume so routing is stable."""
        agent._default_options["provider"] = {
            "type": "openai",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "sk-local",
        }
        agent._default_options["model"] = "deepseek/deepseek-chat"
        client = _CapturingClient()
        agent._client = client

        await agent._resume_session("sess-123", streaming=True)

        cfg = client.resume_config
        assert cfg is not None
        assert cfg.get("model") == "deepseek/deepseek-chat"
        assert cfg.get("provider", {}).get("base_url") == (
            "http://127.0.0.1:8080/v1"
        )

    async def test_resume_passes_streaming_flag(self, agent: Any) -> None:
        """The streaming flag must be forwarded to the resumed session."""
        client = _CapturingClient()
        agent._client = client

        await agent._resume_session("sess-123", streaming=True)

        assert client.resume_config is not None
        assert client.resume_config.get("streaming") is True
