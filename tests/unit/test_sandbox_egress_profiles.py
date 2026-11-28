"""SBX-F5, first part: the grants of a smoke round (SBX-4b1, task 3986).

Spec: ``project-docs/specs/agent_sandbox.md`` §5.5 and §12.4 decision 3,
done-when 24 and 25.

What breaks this fence: the install runs with a host that is not a package
host, the smoke command runs with any host but the door, a round leaves a
grant on a pass or a failure, a build starts on a stack with no door
upstream, or a ``tools`` handle fails ``_grant_inputs`` while a handle of
another run passes it.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, bound_run, configure_env
from tests.unit.test_sandbox_network_grant import EgressDocker
from tests.unit.test_user_tool_build import (  # noqa: F401
    engine,
    package_files,
    passing,
    write_package,
)

ORG = "aaaaaaaa-0000-4000-8000-0000000004b5"
MEMBER = "member@example.com"


@pytest.mark.parametrize("smoke_exit", [0, 1])
async def test_the_round_grants_are_narrow(engine: Any, smoke_exit: int) -> None:  # noqa: F811
    """Done-when 24. The install runs with the two PyPI hosts only, and the
    smoke command with the door name only. The round ends with no grant on
    a pass and on a failure. Mutation: open the door for the install, or the
    package hosts for the smoke command, or skip the revoke -> red."""
    ute = engine.ute
    passing(engine.broker)
    plain = engine.broker.on_exec

    def run(command: str, env_file: Any) -> Any:
        plain(command, env_file)
        if command == ute.SMOKE_COMMAND and smoke_exit:
            return sb.ExecResult(smoke_exit, "", 0, False, 0.1)
        return None

    engine.broker.on_exec = run
    async with ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        result = await build.run_smoke()
    assert result.passed is (smoke_exit == 0)
    install, smoke = engine.broker.execs()
    assert "pip install" in install[1] and install[3] == ("pypi.org", "files.pythonhosted.org")
    assert smoke[1] == ute.SMOKE_COMMAND and smoke[3] == (sb.DOOR_HOST,)
    grants = [c[1] for c in engine.broker.calls if c[0] == "grant"]
    assert grants == [("pypi.org", "files.pythonhosted.org"), (sb.DOOR_HOST,)]
    assert engine.broker.hosts is None, "the round left a grant"
    assert ("revoke", (sb.DOOR_HOST,)) in engine.broker.calls


async def test_an_empty_lock_opens_no_package_host(engine: Any) -> None:  # noqa: F811
    """Decision 5: an empty lock uses the image packages only, so the round
    opens the door and no package host."""
    passing(engine.broker)
    async with engine.ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files(lock=""))
        assert (await build.run_smoke()).passed
    assert [c[1] for c in engine.broker.calls if c[0] == "grant"] == [(sb.DOOR_HOST,)]


@pytest.fixture
def tools_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")
    return env


async def test_the_tools_grant_takes_the_tool_binding(
    tools_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Done-when 25. With ``sandbox_egress_door_upstream`` empty, the build
    refuses before any start. A ``tools`` handle passes ``_grant_inputs``, and
    a handle of another run fails it. Mutation: compare a ``tools`` handle
    with the run binding, or drop the door check of ``acquire_tool`` -> red."""
    from acb_auth import sandbox_capabilities
    from acb_skills import user_tool_build as ute

    assert sb.TOOLS_TARGET in sb.EGRESS_TARGETS
    assert {sb.PROJECTS_TARGET, sb.COMPUTE_TARGET}.isdisjoint(sb.EGRESS_TARGETS)
    docker = EgressDocker()
    broker = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    monkeypatch.setattr(ute, "_broker", lambda: broker)

    async def nothing(*_a: Any, **_k: Any) -> int:
        return 0

    monkeypatch.setattr(sandbox_capabilities, "revoke_step", nothing)
    thread, other = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(tools_env["settings"], "sandbox_egress_door_upstream", "")
    with bound_run(ORG, member=MEMBER, thread=thread):
        with pytest.raises(sb.SandboxRefused, match="upstream"):
            async with ute.start_build({"crm:read"}):
                pass
        with pytest.raises(sb.SandboxRefused, match="upstream"):
            await broker.acquire_tool()
    assert docker.calls == [], "a stack with no door upstream starts nothing"

    monkeypatch.setattr(tools_env["settings"], "sandbox_egress_door_upstream", "10.0.0.5:8080")
    with bound_run(ORG, member=MEMBER, thread=thread):
        handle = await broker.acquire_tool()
        assert broker._grant_inputs(handle)[1] == MEMBER
        grant = await broker.grant_egress(handle, "smoke", [sb.DOOR_HOST])
        assert grant.hosts == (sb.DOOR_HOST,) and handle.network == grant.network
        with pytest.raises(sb.SandboxRefused, match="not on the egress allowlist"):
            await broker.grant_egress(handle, "smoke", ["example.com"])
    with bound_run(ORG, member=MEMBER, thread=other), pytest.raises(
        sb.SandboxRefused, match="not the bound run",
    ):
        broker._grant_inputs(handle)
    await broker.release_tool(handle)
    await broker.stop()


async def test_a_code_task_handle_does_not_pass_as_the_tool_binding(
    tools_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Done-when 25, the other side: the tool binding is not a pass for a
    ``code_task`` handle of the same thread, and the run binding is not a
    pass for the ``tools`` handle."""
    monkeypatch.setattr(tools_env["settings"], "maf_coding_scope", "tools:*,code_task:*")
    broker = sb.SandboxBroker(docker=FakeDocker())  # type: ignore[arg-type]
    thread = str(uuid.uuid4())
    with bound_run(ORG, member=MEMBER, thread=thread):
        tools = await broker.acquire_tool()
        code = await broker.acquire()
        assert broker._grant_inputs(tools)[1] == MEMBER
        assert broker._grant_inputs(code)[1] == MEMBER
        monkeypatch.setattr(sb, "read_tool_binding", sb.read_run_binding)
        with pytest.raises(sb.SandboxRefused, match="not the bound run"):
            broker._grant_inputs(tools)
    await broker.release_tool(tools)
    await broker.stop()
