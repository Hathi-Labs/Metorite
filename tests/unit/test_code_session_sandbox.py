"""`run_copilot_code_session`'s BO-7 phase 2 sandbox wiring.

When "code_task" is in settings.copilot_sandbox_scope, the session should
spawn a copilot_sandbox container, redirect working_directory + the
permission-containment root at the container mount point, and always tear
the sandbox down — but fall back to the untouched in-process path whenever
the sandbox fails to come up, since copilot_sandbox.spawn_copilot_sandbox()
returning None must never turn into a hard failure.
"""
from __future__ import annotations

import types
from typing import ClassVar

import pytest
from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
from orchestrator import copilot_sandbox
from orchestrator.copilot_sandbox import CopilotSandboxHandle


class _FakeResult:
    def __init__(self, text: str):
        self.text = text


class _FakeAgent:
    """Stands in for MetoriteCopilotAgent — records what it was built with."""

    instances: ClassVar[list[_FakeAgent]] = []

    def __init__(self, *, name, instructions, default_options):
        self.name = name
        self.default_options = default_options
        self._permission_handler = None
        self._sandbox_cli_url = None
        _FakeAgent.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, task):
        return _FakeResult(f"did: {task}")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _FakeAgent.instances = []
    _WRITE_ARTIFACT_CONTEXT.pop("permission_check_root", None)
    monkeypatch.setattr(
        "orchestrator.copilot_agent.MetoriteCopilotAgent", _FakeAgent,
    )
    yield
    _WRITE_ARTIFACT_CONTEXT.pop("permission_check_root", None)


def _settings(**overrides) -> types.SimpleNamespace:
    base = dict(
        litellm_base_url="http://127.0.0.1:8080",
        gateway_internal_token="tok",
        litellm_master_key="",
        copilot_sandbox_scope="",
        copilot_sandbox_image="acb-copilot-sandbox:latest",
        copilot_sandbox_port=41041,
        copilot_sandbox_memory_limit="768m",
        copilot_sandbox_cpu_limit="1",
        copilot_sandbox_pids_limit=256,
        copilot_sandbox_ready_timeout_seconds=0.1,
        copilot_sandbox_idle_ttl_seconds=600,
        copilot_sandbox_state_dir="/tmp/acb-copilot-sandbox-test-state",
        agents_clone_dir="/tmp/acb-agents-test",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_scope_off_never_touches_sandbox_or_working_directory(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.code_session.get_settings", lambda: _settings(copilot_sandbox_scope=""),
    )
    spawn_calls = []

    async def _fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "spawn_copilot_sandbox", _fake_spawn)

    from orchestrator.code_session import run_copilot_code_session

    result = await run_copilot_code_session(task="do a thing", workspace="/tmp/ws")

    assert result == "did: do a thing"
    assert spawn_calls == []
    agent = _FakeAgent.instances[0]
    assert agent.default_options["working_directory"] == "/tmp/ws"
    assert agent._sandbox_cli_url is None
    assert "permission_check_root" not in _WRITE_ARTIFACT_CONTEXT


@pytest.mark.asyncio
async def test_scope_on_and_spawn_succeeds_redirects_workspace_and_cleans_up(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.code_session.get_settings", lambda: _settings(copilot_sandbox_scope="code_task"),
    )
    handle = CopilotSandboxHandle(
        name="cc-copilot-code_task-abc", cli_url="127.0.0.1:9999",
        workspace="/tmp/ws", label="code_task",
    )
    spawn_calls = []
    stop_calls = []

    async def _fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return handle

    async def _fake_stop(h):
        stop_calls.append(h)

    monkeypatch.setattr(copilot_sandbox, "spawn_copilot_sandbox", _fake_spawn)
    monkeypatch.setattr(copilot_sandbox, "stop_copilot_sandbox", _fake_stop)

    permission_root_during_call = {}
    real_aenter = _FakeAgent.__aenter__

    async def _capturing_aenter(self):
        permission_root_during_call["value"] = _WRITE_ARTIFACT_CONTEXT.get("permission_check_root")
        return await real_aenter(self)

    monkeypatch.setattr(_FakeAgent, "__aenter__", _capturing_aenter)

    from orchestrator.code_session import run_copilot_code_session

    result = await run_copilot_code_session(task="do a thing", workspace="/tmp/ws")

    assert result == "did: do a thing"
    assert len(spawn_calls) == 1
    assert spawn_calls[0]["workspace"] == "/tmp/ws"
    assert spawn_calls[0]["label"] == "code_task"

    agent = _FakeAgent.instances[0]
    assert agent.default_options["working_directory"] == copilot_sandbox.CONTAINER_WORKSPACE
    assert agent._sandbox_cli_url == "127.0.0.1:9999"

    assert permission_root_during_call["value"] == copilot_sandbox.CONTAINER_WORKSPACE
    # Cleaned up after the call — no leakage into the next (unsandboxed) call.
    assert "permission_check_root" not in _WRITE_ARTIFACT_CONTEXT
    assert stop_calls == [handle]


@pytest.mark.asyncio
async def test_scope_on_but_spawn_fails_falls_back_in_process(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.code_session.get_settings", lambda: _settings(copilot_sandbox_scope="code_task"),
    )

    async def _fake_spawn(**kwargs):
        return None

    stop_calls = []

    async def _fake_stop(h):
        stop_calls.append(h)

    monkeypatch.setattr(copilot_sandbox, "spawn_copilot_sandbox", _fake_spawn)
    monkeypatch.setattr(copilot_sandbox, "stop_copilot_sandbox", _fake_stop)

    from orchestrator.code_session import run_copilot_code_session

    result = await run_copilot_code_session(task="do a thing", workspace="/tmp/ws")

    assert result == "did: do a thing"
    agent = _FakeAgent.instances[0]
    assert agent.default_options["working_directory"] == "/tmp/ws"
    assert agent._sandbox_cli_url is None
    assert stop_calls == []  # nothing to stop — never spawned
    assert "permission_check_root" not in _WRITE_ARTIFACT_CONTEXT


@pytest.mark.asyncio
async def test_scope_with_app_builder_only_does_not_sandbox_code_task(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.code_session.get_settings", lambda: _settings(copilot_sandbox_scope="app_builder"),
    )
    spawn_calls = []

    async def _fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "spawn_copilot_sandbox", _fake_spawn)

    from orchestrator.code_session import run_copilot_code_session

    await run_copilot_code_session(task="do a thing", workspace="/tmp/ws")

    assert spawn_calls == []
