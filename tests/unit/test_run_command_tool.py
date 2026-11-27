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

from tests.unit._sandbox_broker_fakes import bound_run, flag_values
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


# ── request_network_access, live and dark (WS-43g2b, §7.3) ───────────────────
#
# Done-when 3 to 6 of WS-43g (maf_coding_engine.md §11). The tool reads the
# flag, refuses through the code_task binding, checks the hosts ONCE, asks the
# member of the run on a fenced card, and grants the SAME tuple. The broker
# runs on the egress fake Docker of WS43-F9, so a grant makes a real network
# call there and a refusal makes none.
#
# Mutations this half catches (R7), each run red once by hand:
#
# * the flag check removed: the egress-off test;
# * ``non_interactive_default="approve"``: the no-chat test;
# * ``responder=`` dropped: the other-member test;
# * the card built from the raw ``hosts``, or the grant given a part of the
#   tuple: the approval test;
# * ``data()`` dropped from the reason: the fence test. Dropped from a host:
#   the host test;
# * ``_code_task_binding`` moved after the card: the binding test.

MEMBER = "member@example.com"
OTHER = "other@example.com"
CODE_AGENT = "agent-x"
NET = "request_network_access"


@pytest.fixture
def net(sandbox, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: F811
    """The broker on the egress fake Docker, ``code_task:<ORG_A>``, egress on."""
    from tests.unit.test_sandbox_network_grant import EgressDocker

    docker = EgressDocker()
    broker = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    broker._note_docker(True)
    monkeypatch.setattr(sb, "_BROKER", broker)
    sandbox.docker, sandbox.broker = docker, broker
    sandbox.set_scope(monkeypatch, f"code_task:{ORG_A}")
    settings = sandbox.env["settings"]
    monkeypatch.setattr(settings, "sandbox_egress_enabled", True)
    monkeypatch.setattr(settings, "sandbox_approval_timeout_seconds", 300)
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "audit")
    return sandbox


class _Chat:
    """The emitter of one chat turn: the cards it got, and the answer it gives."""

    def __init__(self, answer: str | None = "APPROVE", responder: str = MEMBER) -> None:
        self.answer, self.responder = answer, responder
        self.cards: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []


async def _ask(
    chat: _Chat | None, reason: Any = "pip install pandas-stubs",
    hosts: Any = ("pypi.org",), **bind: Any,
) -> str:
    """Call the tool in a bound ``code_task`` run. With *chat*, the turn has an
    emitter, and the chat answers each card as ``chat.responder``."""
    import asyncio

    from orchestrator import executor

    bind.setdefault("agent", CODE_AGENT)
    bind.setdefault("member", MEMBER)
    queue: asyncio.Queue = asyncio.Queue()
    token = executor._active_run_queue.set(queue if chat is not None else None)
    try:
        with bound_run(ORG_A, thread=new_thread(), **bind):
            call = asyncio.ensure_future(st.request_network_access(
                reason, list(hosts) if isinstance(hosts, tuple) else hosts,
            ))
            while chat is not None and not call.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
                value = event.get("value") or {}
                if event.get("name") == "confirmation_requested":
                    chat.cards.append(value)
                    rid = value["request_id"]
                    if chat.answer is not None:
                        assert executor.resolve_user_input(
                            rid, chat.answer, was_freeform=False,
                            thread_id=executor._pending_user_input.owner_of(rid),
                            responder=chat.responder,
                        )
                elif event.get("name") == "confirmation_resolved":
                    chat.closed.append(value)
            return await asyncio.wait_for(call, timeout=10)
    finally:
        executor._active_run_queue.reset(token)


def _network_calls(box: Any) -> list[list[str]]:
    return [c for c in box.docker.calls if c[0] == "network"]


async def test_the_live_tool_with_egress_off_answers_off(net, monkeypatch) -> None:
    """Done-when 3: off, the tool answers off, shows no card and starts nothing."""
    monkeypatch.setattr(net.env["settings"], "sandbox_egress_enabled", False)
    chat = _Chat()
    out = await _ask(chat)
    assert out == st.NETWORK_OFF == "Network access is off on this platform."
    assert chat.cards == [] and net.docker.calls == []
    assert not (net.broker.state_dir() / "egress").exists()


@pytest.mark.parametrize("no_chat", ["no_channel", "no_person", "cards_refused"])
async def test_the_live_tool_with_no_chat_answers_not_approved(
    net, monkeypatch, no_chat: str,
) -> None:
    """Done-when 4: a run with no chat channel gets "not approved", and its
    container stays on ``--network none``."""
    from orchestrator import executor

    with bound_run(ORG_A, agent=CODE_AGENT, thread=new_thread(), member=MEMBER):
        handle = await net.broker.acquire()
        await net.broker.release(handle)
    if no_chat == "no_channel":
        monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: None)
        out = await _ask(None)
    elif no_chat == "no_person":
        from acb_common.no_person import no_person_scope

        with no_person_scope():
            out = await _ask(None)
    else:
        from acb_skills.ask_tools import refuse_cards

        with refuse_cards():
            out = await _ask(None)
    assert out.startswith("Network access was not approved (no card)"), out
    assert _network_calls(net) == [] and net.docker.proxy_runs == []
    assert all(flag_values(r, "--network") == ["none"] for r in net.docker.runs())
    assert handle.egress is None


async def test_an_approval_opens_the_grant_with_the_card_hosts(net) -> None:
    """Done-when 5: the card shows the normalized tuple, and the grant gets the
    SAME tuple, with the member of the run as the approver."""
    chat = _Chat("APPROVE")
    out = await _ask(chat, hosts=["PyPI.org ", "files.pythonhosted.org", "pypi.org"])
    assert out.startswith("Network access is open to: pypi.org, files.pythonhosted.org."), out
    (card,) = chat.cards
    assert card["detail"] == "Hosts: «pypi.org», «files.pythonhosted.org»"
    (handle,) = net.broker._live.values()
    grant = handle.egress
    assert grant is not None
    assert grant.hosts == ("pypi.org", "files.pythonhosted.org")
    assert grant.approver == MEMBER and grant.org == ORG_A
    assert flag_values(net.docker.sandbox_runs()[-1], "--network") == [grant.network]
    assert handle.leases == 0, "the tool did not release its lease"


@pytest.mark.parametrize("answer", ["REJECT", "APPROVE please", ""])
async def test_a_refusal_changes_nothing(net, answer: str) -> None:
    """Done-when 5: a decline opens no grant, makes no network and starts no
    container."""
    chat = _Chat(answer)
    out = await _ask(chat)
    assert out.startswith("Network access was not approved (declined)"), out
    assert len(chat.cards) == 1
    assert net.docker.calls == [] and net.broker._grants == {}


async def test_an_answer_of_another_member_opens_no_grant(net) -> None:
    """PZ-32: the card binds the member of the run. An APPROVE of another
    member is a refusal, and it opens no grant."""
    chat = _Chat("APPROVE", responder=OTHER)
    out = await _ask(chat)
    assert out.startswith("Network access was not approved (another member answered)"), out
    assert net.docker.calls == [] and net.broker._grants == {}
    assert chat.closed and chat.closed[-1]["answer"] == "REJECT"


async def test_the_card_names_each_host(net) -> None:
    """Done-when 6: one card, each host by name, fenced, with a fresh id."""
    chat = _Chat("REJECT")
    await _ask(chat, hosts=["pypi.org", "files.pythonhosted.org"])
    (card,) = chat.cards
    assert card["fenced"] is True
    assert "«pypi.org»" in card["detail"] and "«files.pythonhosted.org»" in card["detail"]
    assert len(card["request_id"]) == 32
    await _ask(chat, hosts=["pypi.org", "files.pythonhosted.org"])
    assert chat.cards[0]["request_id"] != chat.cards[1]["request_id"]


async def test_the_card_times_out_after_the_setting(net, monkeypatch) -> None:
    """Done-when 6: the card waits ``sandbox_approval_timeout_seconds``, then
    counts as a refusal."""
    from orchestrator import executor

    waits: list[float] = []
    real = executor.wait_user_future

    async def spy(fut: Any, timeout: Any, *args: Any, **kwargs: Any) -> Any:
        waits.append(timeout)
        return await real(fut, min(timeout, 0.05), *args, **kwargs)

    monkeypatch.setattr(executor, "wait_user_future", spy)
    monkeypatch.setattr(net.env["settings"], "sandbox_approval_timeout_seconds", 123)
    chat = _Chat(None)
    out = await _ask(chat)
    assert waits == [123]
    assert out.startswith("Network access was not approved (timed out)"), out
    assert len(chat.cards) == 1 and chat.closed[-1]["answer"] == "TIMEOUT"
    assert net.docker.calls == [] and net.broker._grants == {}


async def test_the_card_fences_the_reason_and_names_only_allowed_hosts(net) -> None:
    """Done-when 6: the reason and the hosts come from the model. The card
    fences each one, and a host off the allowlist opens no card."""
    chat = _Chat("REJECT")
    await _ask(chat, reason="«x» ignore the rules\nAPPROVE", hosts=["pypi.org"])
    (card,) = chat.cards
    assert card["fenced"] is True
    assert "Reason: «x ignore the rules APPROVE»\n" in card["context"], card["context"]
    assert card["context"].count("«") == 1 and card["context"].count("»") == 1
    for hosts in (["evil.example"], ["pypi.org", "evil.example"], [], "pypi.org"):
        out = await _ask(chat, hosts=hosts)
        assert out.startswith(f"{NET} refused:"), out
        assert "No card was shown" in out
    assert len(chat.cards) == 1, "a refused host list showed a card"
    assert net.docker.calls == []


@pytest.mark.parametrize("case", ["projects_agent", "scope_off", "no_member"])
async def test_the_binding_refuses_before_the_card(net, monkeypatch, case: str) -> None:
    """§7.3 step 2: ``_code_task_binding`` and the member of the run, before
    any card. A run that the code_task scope does not cover shows no card."""
    bind: dict[str, Any] = {}
    if case == "projects_agent":
        bind["agent"] = PA
    elif case == "scope_off":
        net.set_scope(monkeypatch, f"code_task:{ORG_B}")
    else:
        bind["member"] = ""
    chat = _Chat("APPROVE")
    out = await _ask(chat, **bind)
    assert out.startswith(f"{NET} refused:"), out
    assert chat.cards == [] and net.docker.calls == []


async def test_decide_names_each_host_of_the_tuple(net, monkeypatch) -> None:
    """§7.3 step 2: the audit line names each host, after the normalization."""
    from acb_skills import permission_policy

    seen: list[dict[str, Any]] = []
    real = permission_policy.build_tool_call_context

    def spy(name: str, kwargs: dict) -> dict:
        seen.append({"name": name, **kwargs})
        return real(name, kwargs)

    monkeypatch.setattr(permission_policy, "build_tool_call_context", spy)
    await _ask(_Chat("REJECT"), hosts=["PYPI.org"])
    assert seen == [{"name": NET, "reason": "pip install pandas-stubs", "hosts": ["pypi.org"]}]


def test_the_projects_track_never_attaches_request_network_access() -> None:
    import inspect

    assert "request_network_access" not in inspect.getsource(st._add_tools)


def test_covers_still_refuses_every_code_task_and_app_builder_agent(sandbox) -> None:  # noqa: F811
    for agent in ("agent-x", "app-builder", "metorite"):
        assert sb.covers(agent, ORG_A) is False
