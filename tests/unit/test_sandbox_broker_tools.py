"""SBX-4a and SBX-4b1 — the ``tools`` target of the broker.

``agent_sandbox.md`` §6.2, §0c item 4 and §12.4 decisions 1, 2 and 4.

- :meth:`SandboxBroker.acquire` refuses the ``tools`` target, even with
  ``tools:*`` in ``MAF_CODING_SCOPE``, and calls no ``docker``. The one way
  in is :meth:`SandboxBroker.acquire_tool` (SBX-4b1).
- The ``docker-remote`` backend refuses it too (``REMOTE_TARGETS`` holds
  ``code_task`` only, until SBX-15b).
- Only the broker's own ``user-tool`` name maps to ``tools``.
- A ``tools`` agent is never "covered", so the D85 seam never gives it the
  host shell back.

The parser half is SBX-F24, ``test_sandbox_scope_all_orgs.py``.

SBX-4b1 adds done-when 20 (the token reaches the command by an env file
only) and 23 (the two mounts of ``<tool-data>``, no overlap with
``projects_mounts``, and ``end_run`` removes ``<tool-data>``). It also holds
the scope check, the two caps and the lock of (org, thread) of
``acquire_tool`` (decision 1).
"""
from __future__ import annotations

import stat
import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import (
    FakeDocker,
    bound_run,
    configure_env,
    flag_values,
    mounts_of,
)

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


# ── SBX-4b1: acquire_tool, the mounts and the env file ──────────────────────

ORG_B = "bbbbbbbb-0000-0000-0000-00000000004b"
TOKEN = "msbx_" + "T" * 43


def _thread() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def tools_on(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The settings that a build needs: the scope, egress and a door upstream."""
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "code_task:*,projects:*,tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")
    monkeypatch.setattr(s, "sandbox_max_per_org", 4)
    monkeypatch.setattr(s, "sandbox_max_total", 8)
    return env


async def test_acquire_tool_refuses_without_the_tools_scope(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 1: the scope check. Mutation: drop the scope check -> red."""
    monkeypatch.setattr(tools_on["settings"], "maf_coding_scope", "code_task:*")
    with bound_run(ORG, thread=_thread()), pytest.raises(sb.SandboxRefused, match="tools"):
        await broker.acquire_tool()
    monkeypatch.setattr(tools_on["settings"], "maf_coding_scope", f"tools:{ORG_B}")
    with bound_run(ORG, thread=_thread()), pytest.raises(sb.SandboxRefused, match="tools"):
        await broker.acquire_tool()
    assert docker.calls == []


async def test_acquire_tool_keys_the_container_on_user_tool(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
) -> None:
    thread = _thread()
    with bound_run(ORG, thread=thread):
        handle = await broker.acquire_tool()
        assert handle.binding == sb.read_tool_binding()
    assert handle.agent == sb.USER_TOOL_AGENT and handle.binding.target == sb.TOOLS_TARGET
    run = docker.runs()[0]
    labels = flag_values(run, "--label")
    assert f"{sb.LABEL_AGENT}={sb.USER_TOOL_AGENT}" in labels
    env = dict(v.split("=", 1) for v in flag_values(run, "--env"))
    assert "PIP_USER" not in env, "pip --target refuses PIP_USER"
    assert env["PYTHONNOUSERSITE"] == "1" and env["PYTHONSAFEPATH"] == "1"
    await broker.release_tool(handle)
    await broker.stop()


async def test_one_build_at_a_time_in_a_thread(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
) -> None:
    """Decision 1: the lock of (org, thread). Mutation: drop it -> red."""
    thread = _thread()
    with bound_run(ORG, thread=thread):
        first = await broker.acquire_tool()
        with pytest.raises(sb.SandboxBusy, match="already runs"):
            await broker.acquire_tool()
        await broker.release_tool(first)
        again = await broker.acquire_tool()
    assert again is first, "a free thread reuses its idle container"
    assert len(docker.runs()) == 1
    await broker.release_tool(again)
    await broker.stop()


async def test_the_two_tool_caps_hold(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 1: ``user_tool_max_per_org`` and ``user_tool_max_total``, each
    1. A held build is never stopped. An idle one is. Mutation: drop either
    cap -> red."""
    s = tools_on["settings"]
    assert s.user_tool_max_per_org == 1 and s.user_tool_max_total == 1
    with bound_run(ORG, thread=_thread()):
        a = await broker.acquire_tool()
    with bound_run(ORG, thread=_thread()), pytest.raises(sb.SandboxBusy, match="share"):
        await broker.acquire_tool()
    monkeypatch.setattr(s, "user_tool_max_per_org", 2)
    with bound_run(ORG_B, thread=_thread()), pytest.raises(sb.SandboxBusy, match="maximum"):
        await broker.acquire_tool()
    await broker.release_tool(a)
    with bound_run(ORG_B, thread=_thread()):
        b = await broker.acquire_tool()
    assert a.removed and not b.removed, "the idle tools container of org A made room"
    await broker.release_tool(b)
    await broker.stop()


async def test_the_tools_mounts_do_not_overlap_projects(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
) -> None:
    """Done-when 23 (decision 2). The ``tools`` container mounts the two dirs
    of ``<tool-data>`` and nothing else. No source of it lies inside a source
    of ``projects_mounts`` of the same thread, nor the other way. ``end_run``
    removes ``<tool-data>`` and the run-data dir: the map holds a set of rels
    for one key. Mutation: mount the working dir, or keep one rel -> red."""
    thread = _thread()
    with bound_run(ORG, agent=sb.PROJECTS_AGENT, thread=thread):
        await broker.acquire()
        projects = sb.read_run_binding()
        handle = await broker.acquire_tool()
        tools = sb.read_tool_binding()
        await broker.release_tool(handle)
        p_run, t_run = docker.runs()
        t_mounts = mounts_of(t_run)
        assert t_mounts == [
            f"type=bind,source={tools.tool_data},target={sb.RUN_DATA_TARGET}",
            f"type=bind,source={tools.tool_data / 'outputs'},target={sb.OUTPUTS_TARGET}",
        ]
        t_sources = [tools.tool_data, tools.tool_data / "outputs"]
        p_sources = [
            sb.Path(m.split("source=", 1)[1].split(",", 1)[0]) for m in mounts_of(p_run)
        ]
        assert projects.run_data in p_sources
        for t in t_sources:
            for p in p_sources:
                assert not t.is_relative_to(p), (t, p)
                assert not p.is_relative_to(t), (p, t)
        assert broker._run_data[(projects.workspace, thread)] == {
            projects.run_data_rel, tools.tool_data_rel,
        }
        assert tools.tool_data.is_dir() and projects.run_data.is_dir()
        assert await sb.SandboxBroker.end_run(broker) is True
        await broker.settle()
    assert not tools.tool_data.exists(), "end_run left <tool-data>"
    assert not projects.run_data.exists()
    assert handle.stale
    await broker.stop()


async def test_the_quota_bounds_the_tool_data(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 6 (audit): ``measure_workspace`` measures ``<tool-data>`` for a
    ``tools`` handle. Mutation: drop the tools branch -> red."""
    monkeypatch.setattr(tools_on["settings"], "sandbox_workspace_quota_mb", 1)
    with bound_run(ORG, thread=_thread()):
        handle = await broker.acquire_tool()
    (handle.binding.tool_data / "outputs" / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    await broker.measure_workspace(handle)
    assert broker.quota_exceeded(handle)
    assert not broker.writes_refused(handle.workspace), (
        "a full <tool-data> must not refuse the writes of the agent's own dir"
    )
    await broker.release_tool(handle)
    await broker.stop()


async def test_the_smoke_token_reaches_the_command_by_env_file_only(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
) -> None:
    """Done-when 20 (decision 4). The token never shows in the ``docker exec``
    argv. The env file is mode 0600 in a 0700 folder, holds the one line, and
    is gone after the command. Mutation: put the token in the argv, or keep
    the file -> red."""
    seen: dict[str, Any] = {}

    async def look(argv: list[str]) -> None:
        if sb.EXEC_WRAPPER in argv:
            path = sb.Path(flag_values(argv, "--env-file")[0])
            seen["path"] = path
            seen["mode"] = stat.S_IMODE(path.stat().st_mode)
            seen["dir_mode"] = stat.S_IMODE(path.parent.stat().st_mode)
            seen["text"] = path.read_text(encoding="utf-8")

    docker.on_stream = look
    with bound_run(ORG, thread=_thread()):
        handle = await broker.acquire_tool()
        await broker.exec(handle, "python3 main.py", 30,
                          env_file={"METORITE_API_TOKEN": TOKEN})
    (call,) = docker.command_execs()
    assert all(TOKEN not in part for part in call), "the token is in the argv"
    assert call.index("--env-file") < call.index(handle.name)
    assert seen["mode"] == 0o600 and seen["dir_mode"] == 0o700
    assert seen["text"] == f"METORITE_API_TOKEN={TOKEN}\n"
    assert not seen["path"].exists(), "the env file outlived the command"
    for bad in ({"PATH": "/x"}, {"METORITE_API_TOKEN": "a\nB=c"}, {}):
        with pytest.raises(sb.SandboxRefused):
            await broker.exec(handle, "true", 5, env_file=bad)
    await broker.release_tool(handle)
    await broker.stop()


async def test_a_refused_claim_leaves_no_orphan_container(
    broker: sb.SandboxBroker, docker: FakeDocker, tools_on: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review P3-3. ``_make_tool_room`` stops the idle ``tools`` container of
    the org, and ``_make_room`` then refuses. The stopped container is still
    removed, so no container is left with no handle. Mutation: drop the
    removal on the refusal path -> red."""
    s = tools_on["settings"]
    monkeypatch.setattr(s, "sandbox_max_per_org", 1)
    monkeypatch.setattr(s, "sandbox_max_total", 1)
    with bound_run(ORG, thread=_thread()):
        idle = await broker.acquire_tool()
    await broker.release_tool(idle)
    monkeypatch.setattr(s, "sandbox_max_per_org", 2)
    monkeypatch.setattr(s, "sandbox_max_total", 2)
    with bound_run(ORG, agent="agent-x", thread=_thread()):
        busy = await broker.acquire()  # leased, so never a victim
    monkeypatch.setattr(s, "sandbox_max_per_org", 1)
    monkeypatch.setattr(s, "sandbox_max_total", 1)
    with bound_run(ORG, thread=_thread()), pytest.raises(sb.SandboxBusy):
        await broker.acquire_tool()
    await broker.settle()
    assert idle.removed
    assert idle.container_id in docker.removals(), "the dropped victim kept its container"
    assert not busy.removed
    await broker.stop()
