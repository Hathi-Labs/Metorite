"""`MetoriteCopilotAgent.start()`'s BO-7 phase 2 sandbox branch.

When `_sandbox_cli_url` is set (by code_session.py/executor.py after spawning
a copilot_sandbox container), start() must connect via the SDK's `cli_url`
transport instead of spawning/token-authing a local CLI — and must never
also take the token branch (the SDK treats cli_url + github_token as mutually
exclusive and raises).
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from agent_framework_github_copilot import GitHubCopilotAgent
from orchestrator.copilot_agent import MetoriteCopilotAgent


class _FakeCopilotClient:
    """Records the keywords, then proves the REAL SDK client accepts them.

    SDK 1.0 (H-181) takes keyword arguments, and ``cli_url`` became
    ``connection=RuntimeConnection.for_uri(...)``. Building a real client
    from the same keywords fails the test if the SDK signature drifts again.
    """

    instances: ClassVar[list[_FakeCopilotClient]] = []

    def __init__(self, **options):
        from copilot import CopilotClient

        CopilotClient(**options)  # raises TypeError on an unknown keyword
        self.options = options
        _FakeCopilotClient.instances.append(self)


@pytest.fixture(autouse=True)
def _reset():
    _FakeCopilotClient.instances = []
    yield


@pytest.mark.asyncio
async def test_sandbox_cli_url_uses_tcp_transport_not_token(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.copilot_agent.CopilotClient", _FakeCopilotClient,
    )
    base_start_calls = []

    async def _fake_base_start(self):
        base_start_calls.append(self)

    monkeypatch.setattr(GitHubCopilotAgent, "start", _fake_base_start)
    # No token env vars set — proves the sandbox branch doesn't need them.
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    agent = MetoriteCopilotAgent(name="code-task", instructions="x")
    agent._sandbox_cli_url = "127.0.0.1:54321"

    await agent.start()

    from copilot import UriRuntimeConnection

    assert len(_FakeCopilotClient.instances) == 1
    opts = _FakeCopilotClient.instances[0].options
    assert set(opts) == {"connection"}, "the sandbox branch must not send a token"
    assert isinstance(opts["connection"], UriRuntimeConnection)
    assert opts["connection"].url == "127.0.0.1:54321"
    assert agent._client is _FakeCopilotClient.instances[0]
    assert base_start_calls == [agent]


@pytest.mark.asyncio
async def test_no_sandbox_url_falls_back_to_existing_token_path(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.copilot_agent.CopilotClient", _FakeCopilotClient,
    )
    base_start_calls = []

    async def _fake_base_start(self):
        base_start_calls.append(self)

    monkeypatch.setattr(GitHubCopilotAgent, "start", _fake_base_start)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fake-token")

    agent = MetoriteCopilotAgent(name="code-task", instructions="x")
    # _sandbox_cli_url intentionally left unset (the normal, non-sandboxed case).

    await agent.start()

    assert len(_FakeCopilotClient.instances) == 1
    assert _FakeCopilotClient.instances[0].options == {"github_token": "fake-token"}
    assert base_start_calls == [agent]
