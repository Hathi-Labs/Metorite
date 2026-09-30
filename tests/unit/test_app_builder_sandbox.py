"""`_maybe_sandbox_session_workspace` — BO-7 phase 2, App Workshop app-builder.

The App Workshop app-builder is the interactive-chat analogue of code_task's
sandboxing: identified via the SAME allow_session_workspace hook
_session_workspace_override already gates on (never a hardcoded agent name),
containerized via copilot_sandbox.get_or_spawn_sticky's per-thread reuse
(a fresh container every chat turn would regress latency this path doesn't
have today), and must fall back to the untouched in-process agent whenever
the sandbox fails to come up.
"""
from __future__ import annotations

import types

import pytest
from acb_skills.write_artifact import (
    artifact_context,
    derive_artifact_context,
)
from orchestrator import copilot_sandbox
from orchestrator.copilot_sandbox import CopilotSandboxHandle
from orchestrator.executor import _maybe_sandbox_session_workspace


class _FakeAgent:
    def __init__(self):
        self._sandbox_cli_url = None
        self._default_options = {"working_directory": "/host/custom_apps/my-app"}


def _settings(**overrides) -> types.SimpleNamespace:
    base = dict(copilot_sandbox_scope="")
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset():
    derive_artifact_context(permission_check_root=None)
    yield
    derive_artifact_context(permission_check_root=None)


@pytest.fixture
def _fake_t2_vendor_dir(monkeypatch):
    import sys
    import types as _types

    mod = _types.ModuleType("gateway.routes.apps._common")
    mod.t2_vendor_dir = lambda: "/host/vendor/t2-react"
    monkeypatch.setitem(sys.modules, "gateway.routes.apps._common", mod)


@pytest.mark.asyncio
async def test_not_a_session_workspace_agent_is_a_noop(monkeypatch, _fake_t2_vendor_dir):
    spawn_calls = []

    async def _fake_sticky(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id="t1", session_ws=None,
        effective_agent_dir="/host/agent-clone", agent_dir="/host/agent-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope="app_builder"),
    )

    assert spawn_calls == []
    assert agent._sandbox_cli_url is None
    assert agent._default_options["working_directory"] == "/host/custom_apps/my-app"


@pytest.mark.asyncio
async def test_scope_off_is_a_noop_even_for_session_workspace_agent(monkeypatch, _fake_t2_vendor_dir):
    spawn_calls = []

    async def _fake_sticky(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id="t1", session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope=""),
    )

    assert spawn_calls == []
    assert agent._sandbox_cli_url is None


@pytest.mark.asyncio
async def test_scope_on_and_spawn_succeeds_redirects_workspace_and_mounts(monkeypatch, _fake_t2_vendor_dir):
    handle = CopilotSandboxHandle(
        name="cc-copilot-app_builder-abc", cli_url="127.0.0.1:9999",
        workspace="/host/custom_apps/my-app", label="app_builder",
    )
    spawn_calls = []

    async def _fake_sticky(**kwargs):
        spawn_calls.append(kwargs)
        return handle

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id="thread-1", session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope="app_builder"),
    )

    assert len(spawn_calls) == 1
    call = spawn_calls[0]
    assert call["thread_id"] == "thread-1"
    assert call["workspace"] == "/host/custom_apps/my-app"
    assert call["label"] == "app_builder"
    assert call["extra_mounts"] == {
        "/host/agent-builder-clone": "/host/agent-builder-clone",
        "/host/vendor/t2-react": "/opt/t2-vendor",
    }
    assert call["extra_env"] == {"CUSTOM_APPS_T2_VENDOR_DIR": "/opt/t2-vendor"}

    assert agent._sandbox_cli_url == "127.0.0.1:9999"
    assert agent._default_options["working_directory"] == copilot_sandbox.CONTAINER_WORKSPACE
    assert artifact_context()["permission_check_root"] == copilot_sandbox.CONTAINER_WORKSPACE


@pytest.mark.asyncio
async def test_scope_on_but_spawn_fails_falls_back_in_process(monkeypatch, _fake_t2_vendor_dir):
    async def _fake_sticky(**kwargs):
        return None

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id="thread-1", session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope="app_builder"),
    )

    assert agent._sandbox_cli_url is None
    assert agent._default_options["working_directory"] == "/host/custom_apps/my-app"
    assert "permission_check_root" not in artifact_context()


@pytest.mark.asyncio
async def test_scope_with_code_task_only_does_not_sandbox_app_builder(monkeypatch, _fake_t2_vendor_dir):
    spawn_calls = []

    async def _fake_sticky(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id="thread-1", session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope="code_task"),
    )

    assert spawn_calls == []


@pytest.mark.asyncio
async def test_no_thread_id_is_a_noop(monkeypatch, _fake_t2_vendor_dir):
    spawn_calls = []

    async def _fake_sticky(**kwargs):
        spawn_calls.append(kwargs)
        return None

    monkeypatch.setattr(copilot_sandbox, "get_or_spawn_sticky", _fake_sticky)
    agent = _FakeAgent()

    await _maybe_sandbox_session_workspace(
        thread_id=None, session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=_settings(copilot_sandbox_scope="app_builder"),
    )

    assert spawn_calls == []


@pytest.mark.asyncio
async def test_never_raises_when_settings_are_malformed(_fake_t2_vendor_dir):
    agent = _FakeAgent()
    # settings without copilot_sandbox_scope at all — getattr default path.
    await _maybe_sandbox_session_workspace(
        thread_id="t1", session_ws="/host/custom_apps/my-app",
        effective_agent_dir="/host/custom_apps/my-app", agent_dir="/host/agent-builder-clone",
        agent=agent, settings=types.SimpleNamespace(),
    )
    assert agent._sandbox_cli_url is None
