"""SBX-4a — the ``tools`` target is in the scope set, and nothing starts it yet.

``agent_sandbox.md`` §6.2 and §0c item 4. SBX-4b adds ``build_tool``, the
build session and the smoke test. Until then:

- :meth:`SandboxBroker.acquire` refuses the ``tools`` target, even with
  ``tools:*`` in ``MAF_CODING_SCOPE``, and calls no ``docker``.
- The ``docker-remote`` backend refuses it too (``REMOTE_TARGETS`` holds
  ``code_task`` only, until SBX-15b).
- Only the broker's own ``user-tool`` name maps to ``tools``.
- A ``tools`` agent is never "covered", so the D85 seam never gives it the
  host shell back.

The parser half is SBX-F24, ``test_sandbox_scope_all_orgs.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, bound_run, configure_env

ORG = "aaaaaaaa-0000-0000-0000-00000000004b"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    values = configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(values["settings"], "maf_coding_scope", "code_task:*,tools:*")
    return values


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def broker(docker: FakeDocker, env: dict[str, Any]) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]


async def test_acquire_refuses_the_tools_target(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG, agent=sb.USER_TOOL_AGENT), pytest.raises(
        sb.SandboxRefused, match="only through build_tool",
    ):
        await broker.acquire()
    assert docker.calls == [], "a refused tools start calls no docker"


async def test_a_code_task_still_starts_with_tools_in_the_scope(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG, agent="agent-x"):
        handle = await broker.acquire()
    assert handle.binding.target == "code_task"
    assert len(docker.runs()) == 1
    await broker.stop()


async def test_docker_remote_refuses_the_tools_target(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert sb.TOOLS_TARGET not in sb.REMOTE_TARGETS
    s = env["settings"]
    monkeypatch.setattr(s, "sandbox_backend", "docker-remote")
    monkeypatch.setattr(s, "sandbox_docker_host", "ssh://sandbox@10.20.30.40:2222")
    with bound_run(ORG, agent=sb.USER_TOOL_AGENT):
        binding = sb.read_run_binding()
        with pytest.raises(sb.SandboxRefused):
            await broker.acquire()
    with pytest.raises(sb.SandboxRefused, match="docker-remote"):
        sb.SandboxBroker._check_target(binding, sb.BACKEND_REMOTE)
    assert docker.calls == []


def test_target_for_agent_maps_only_user_tool() -> None:
    assert sb.target_for_agent(sb.USER_TOOL_AGENT) == sb.TOOLS_TARGET
    for agent in ("tools", "user_tool", "user-tools", "agent-x", ""):
        assert sb.target_for_agent(agent) != sb.TOOLS_TARGET, agent


def test_a_tools_agent_is_never_covered(
    broker: sb.SandboxBroker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The D85 seam gives the host shell back only on a cover. ``tools`` has
    none, so the shell stays withheld whatever the scope holds."""
    monkeypatch.setattr(broker, "healthy", lambda: True)
    assert broker.covers(sb.USER_TOOL_AGENT, ORG) is False
    monkeypatch.setattr(sb, "_BROKER", broker)
    assert sb.lifts_shell_block(sb.USER_TOOL_AGENT, ORG) is False
