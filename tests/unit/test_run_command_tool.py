"""WS43-F6 — ``run_command``, and where it may never appear.

Spec ``project-docs/specs/maf_coding_engine.md`` §7.4, §10 WS43-F6 and the
done-when 1 to 4 and 8 of WS-43d.

What breaks this fence: ``run_command`` skips ``decide()``, shows up in
``_collect_injectable_platform_tools()`` or in the output of
``_inject_agent_tools`` for an agent with no ``tool_scope``, or leaves
``SHELL_TOOLS``. The suite also pins that it runs ONLY through the broker,
never on the host, and that the risk block of every other agent's addendum
never names it.

Mutations this suite catches (R7), each run red once by hand:

* ``_audit_decision`` no longer called in ``_run_in_sandbox``: the
  enforce-mode denylist test;
* ``"run_command"`` taken out of ``manifest.SHELL_TOOLS``: the tier test;
* the ``SANDBOX_TOOL_NAMES`` filter taken out of ``risk_summary_block``: the
  byte-identical addendum test;
* the broker failure answered with a host ``subprocess.run``: the host trap;
* an ``exec()`` that raises after the start answered with a host
  ``subprocess.run`` (the verifier's M2c): the raising-exec test.
"""
from __future__ import annotations

import types
from typing import Any

import pytest
from acb_skills import sandbox_tools as st
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import bound_run
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    ORG_B,
    PA,
    host_trap,
    new_thread,
    sandbox,
    short_tmp,
)

# ── it runs in the broker, and never on the host ─────────────────────────────


async def test_run_command_runs_through_the_broker_exec(sandbox, host_trap) -> None:  # noqa: F811
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        out = await st.run_command("python3 /workspace/.run/chart.py", 30)
    assert out.startswith("run_command — exit 0"), out
    execs = sandbox.docker.command_execs()
    assert len(execs) == 1
    assert execs[0][-1] == "python3 /workspace/.run/chart.py"
    assert execs[0][-2] == "30"
    assert host_trap == []


async def test_the_timeout_is_at_most_300_seconds(sandbox, host_trap) -> None:  # noqa: F811
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        await st.run_command("sleep 1", 10_000)
    assert sandbox.docker.command_execs()[0][-2] == "300"


async def test_a_broker_that_fails_runs_nothing_on_the_host(sandbox, host_trap) -> None:  # noqa: F811
    sandbox.docker.fail_run = True
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        out = await st.run_command("echo hi")
    assert "failed" in out and "Nothing ran on the host" in out
    assert sandbox.docker.command_execs() == []
    assert host_trap == []


async def test_an_exec_that_raises_runs_nothing_on_the_host(
    sandbox, host_trap, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The verifier's M2c: the container started, and then ``exec()`` raised.
    The trap on every host process start shows that nothing ran on the host."""
    async def broken_exec(handle: Any, command: str, timeout_s: float) -> Any:
        raise sb.SandboxUnavailable("The sandbox restart failed, so the sandbox is gone.")

    monkeypatch.setattr(sandbox.broker, "exec", broken_exec)
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        out = await st.run_command("python3 /workspace/.run/chart.py")
    assert "failed" in out and "Nothing ran on the host" in out
    assert sandbox.docker.runs(), "the container never started, so the test proves nothing"
    assert host_trap == []


async def test_no_run_bound_runs_nothing(sandbox, host_trap) -> None:  # noqa: F811
    out = await st.run_command("echo hi")
    assert "no run is bound" in out
    assert sandbox.docker.calls == [] and host_trap == []


async def test_an_org_outside_the_scope_runs_nothing(sandbox, host_trap) -> None:  # noqa: F811
    with bound_run(ORG_B, agent=PA, thread=new_thread()):
        out = await st.run_command("echo hi")
    assert "does not cover" in out
    assert sandbox.docker.calls == [] and host_trap == []


# ── decide() with the whole command (§7.4 step 2) ────────────────────────────


async def test_enforce_mode_refuses_a_denylisted_command_before_the_broker(
    sandbox, host_trap, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        out = await st.run_command("rm -rf / --no-preserve-root")
    assert out == "[blocked by permission policy: shell_denied]"
    assert sandbox.docker.calls == [], "the broker saw a refused command"


async def test_audit_mode_logs_and_still_runs_only_in_the_container(
    sandbox, host_trap, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """``audit`` approves everything, so the container is the boundary."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "audit")
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        out = await st.run_command("rm -rf /tmp/scratch")
    assert out.startswith("run_command — exit 0")
    assert sandbox.docker.command_execs()[0][-1] == "rm -rf /tmp/scratch"
    assert host_trap == []


def test_the_decide_context_carries_the_whole_command() -> None:
    from acb_skills.permission_policy import build_tool_call_context, decide

    ctx = build_tool_call_context("run_command", {"command": "rm -rf ~"})
    assert ctx == {"tool_name": "run_command", "full_command_text": "rm -rf ~"}
    approved, code, _ = decide(ctx)
    assert (approved, code) == (False, "shell_denied")


# ── never a platform tool (§7.4) ─────────────────────────────────────────────


def test_run_command_is_not_an_injectable_platform_tool() -> None:
    from orchestrator._tool_injection import (
        _CORE_STANDARD_TOOL_NAMES,
        _collect_injectable_platform_tools,
    )

    names = {fn.__name__ for fn in _collect_injectable_platform_tools()}
    assert "run_command" not in names
    assert "request_network_access" not in names
    assert "run_command" not in _CORE_STANDARD_TOOL_NAMES


def test_an_unscoped_agent_is_never_injected_run_command() -> None:
    from orchestrator._tool_injection import _inject_agent_tools

    agent = types.SimpleNamespace(
        name="unscoped", default_options={"tools": [], "instructions": "x"},
    )
    _inject_agent_tools([agent], tool_scope=None, agent_name="unscoped")
    names = {getattr(getattr(t, "func", t), "__name__", "") for t in agent.default_options["tools"]}
    assert names, "the unscoped agent got no tool at all, so the test proves nothing"
    assert "run_command" not in names and "request_network_access" not in names


def test_run_command_is_a_shell_tool_and_derives_t2() -> None:
    from acb_skills.manifest import SHELL_TOOLS, AgentManifest

    assert "run_command" in SHELL_TOOLS
    m = AgentManifest.from_config({"name": "x", "tool_scope": ["run_command"]}, name="x")
    assert m.isolation_tier() == "T2"


def test_the_risk_block_never_names_a_sandbox_tool() -> None:
    """The addendum of every other agent stays byte-identical (empty scope)."""
    from acb_skills.tool_annotations import (
        SANDBOX_TOOL_NAMES,
        TOOL_ANNOTATIONS,
        get_annotations,
        risk_summary_block,
    )

    block = risk_summary_block()
    for name in SANDBOX_TOOL_NAMES:
        assert name not in block
        assert get_annotations(name) is not None, f"{name} has no risk annotation"
    saved = {n: TOOL_ANNOTATIONS.pop(n) for n in SANDBOX_TOOL_NAMES}
    try:
        assert risk_summary_block() == block
    finally:
        TOOL_ANNOTATIONS.update(saved)


def test_run_command_is_annotated_as_a_closed_world_write() -> None:
    from acb_skills.tool_annotations import get_annotations

    hints: dict[str, Any] | None = get_annotations("run_command")
    assert hints == {
        "read_only": False, "destructive": False, "idempotent": False, "open_world": False,
    }


# ── request_network_access is a stub (done-when 8) ───────────────────────────


async def test_request_network_access_says_the_network_is_off() -> None:
    assert await st.request_network_access("pip install", ["pypi.org"]) == (
        "Network access is off on this platform."
    )


def test_the_projects_track_never_attaches_request_network_access() -> None:
    import inspect

    assert "request_network_access" not in inspect.getsource(st._add_tools)


def test_covers_still_refuses_every_code_task_and_app_builder_agent(sandbox) -> None:  # noqa: F811
    for agent in ("agent-x", "app-builder", "metorite"):
        assert sb.covers(agent, ORG_A) is False
