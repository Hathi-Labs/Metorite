"""D85 and D129 — no agent that a member reaches runs code on the host.

The owner decided on 2026-10-03: until the sandbox is live, block code
execution on the shared server for the Projects agent and the other shared
agents (``work_plan.md`` D85, ``maf_coding_engine.md`` §7.9). Fence WS43-F23.
On 2026-10-27 D129 put personal agents in scope too (task 808, H-225). Only
the root ``metorite`` dev agent, which a first-party admin runs, keeps them.

The seam is ONE function, ``_tool_injection._withheld_shell_tools``. Its
answer leaves the SCOPE (``_resolve_injected_scope(withheld=)``), and three
consumers read that one scope: the injected tool list, the system-prompt
addendum and the skill bodies. A last filter after the no-match fallback makes
sure no branch puts a shell tool back.

What each test pins, and the mutation that turned it red (2026-10-03):

* projects-assistant, crm-assistant and apis-config get no ``SHELL_TOOLS``
  member, and neither does any other agent in the registry. Red when
  ``_withheld_shell_tools`` returns ``frozenset()`` for a shared agent.
* email-assistant and whatsapp-assistant are personal, and they get no shell
  tool (D129). A run of whatsapp-assistant, and a delegation to either, holds
  none of the three. A personal Copilot agent's CLI shell is refused. Red when
  ``_d85_leaves_alone`` gets its ``personal`` early return back (2026-11-21).
* ``covers(agent, org)`` gives the tools back for that agent in that org only,
  a personal agent too (D130). Red when the cover branch goes. A run with no
  org gets no cover. Red when the ``org`` guard goes.
* The addendum has no coding section for an agent without the tools, and it
  loses nothing else. Red when ``_resolve_injected_scope`` drops ``withheld``
  while the last filter stays.
* The no-match fallback cannot restore a shell tool. Red when the last filter
  goes.
* Every executor call site passes ``agent_config``. Red when one site drops it.

Section 7 is the HOST half of D85: the Copilot CLI's own shell, which
task-manager and app-builder hold. Since fix round 1 of PR #598 the two halves
have two functions and two flags. ``_withheld_shell_tools`` gives
``shell_tools_withheld`` (a cover may lift it). ``_host_shell_refused`` gives
``host_shell_refused`` (a cover never lifts it, because the CLI runs on the
host). ``permission_policy.guard_shared_agent_shell`` reads the second, and
``_install_copilot_permission_handler`` puts the guard on every Copilot
handler, a factory's own handler included.

* A shell request of a shared or a personal agent is refused in every mode,
  with any factory handler, and under a cover. Only the root dev agent's
  shell reaches the inner handler.
* A frame with no flag refuses. Each artifact-context site binds both flags,
  and a sub-agent takes its own answer, not its parent's.
* ``decide()`` contains an SDK 1.0 write (``file_name``) and read
  (``path``) in the workspace.
* Tier 2 always denies the CLI shell. A Metorite session loads no file hooks.
  A policy that cannot load refuses everything.
* The probe drives the real ``run_agent_stream`` for task-manager with its
  real config. Its 29 ``my_tasks_*`` requests and a write inside its
  workspace pass. A shell request, and in ``enforce`` a write outside the
  workspace, are refused.

The mutations that turned each claim red are listed in PR #598.

No database, no network. The settings loader, the run org and the per-agent
tool modules are monkeypatched on the module that reads them.
"""
from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path
from typing import Any

import orchestrator._tool_injection as ti
import orchestrator.executor as executor
import pytest
from acb_skills import addendum as ad
from acb_skills.manifest import SHELL_TOOLS

REPO = Path(__file__).resolve().parents[2]
EXECUTOR_PY = REPO / "apps" / "services" / "orchestrator" / "orchestrator" / "executor.py"

#: The real resolver, kept before the autouse fixture stubs it, for the probe.
_REAL_CURRENT_RUN_ORG = executor._current_run_org

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"

def _slug_to_dir() -> dict[str, str]:
    """Registry slug → agent dir, read from ``_AGENT_REGISTRY``, the mapping of
    record. So an in-repo agent added later is checked with no edit here."""
    from gateway.routes.agent import _AGENT_REGISTRY

    return {
        a["name"]: Path(a["local_path"]).name
        for a in _AGENT_REGISTRY if a.get("local_path")
    }


SLUG_TO_DIR = _slug_to_dir()

#: Section text that only the coding sections carry. The risk block names the
#: tools bare, so a test that looks for a bare name would read the risk list.
CODING_MARKERS_FULL = (
    "### Coding skill (durable scripts)",
    "**code_task(task)**",
    "**run_script(path, args?)**",
    "### Runtime dependencies",
)
CODING_MARKERS_COMPACT = ("code_task(task)", "run_script(path,args?)",
                          "install_dependency(packages)")


def _config(slug: str) -> dict[str, Any]:
    path = REPO / "apps" / "agents" / SLUG_TO_DIR[slug] / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeNativeAgent:
    """The native MAF ``Agent`` shape: tools live in ``default_options``."""

    def __init__(self) -> None:
        self.name = "fake-native"
        self.default_options: dict[str, Any] = {"tools": [], "instructions": ""}


class _FakeCopilotAgent:
    """The GitHubCopilotAgent shape: ``_tools`` + a system message."""

    def __init__(self) -> None:
        self.name = "fake-copilot"
        self._tools: list[Any] = []
        self._default_options: dict[str, Any] = {}


def _native_names(agent: _FakeNativeAgent) -> set[str]:
    return {getattr(t, "__name__", "") for t in agent.default_options["tools"]}


def _copilot_names(agent: _FakeCopilotAgent) -> set[str]:
    return {getattr(getattr(t, "func", t), "__name__", "") for t in agent._tools}


def _system_message(agent: _FakeCopilotAgent) -> str:
    msg = agent._default_options.get("system_message")
    assert isinstance(msg, dict), "the addendum never reached the system message"
    return msg.get("content") or ""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """No gate wrapping, no app or workflow tools, no settings rows, no org."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    monkeypatch.delenv("SKILLS_INDEX_ONLY", raising=False)
    fake_apps = types.ModuleType("orchestrator.app_tools")
    fake_apps.load_app_action_tools = lambda name: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.app_tools", fake_apps)
    fake_wf = types.ModuleType("orchestrator.workflow_tools")
    fake_wf.load_workflow_tools = lambda name: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.workflow_tools", fake_wf)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(executor, "_current_run_org", lambda: None)
    # No database: the registry block and the dynamic agents read the
    # ``dynamic_agents`` table through a pooled engine. A test that rebuilds
    # the scratch database later would then meet a dead connection.
    import gateway.routes.agent as agent_routes
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


def _inject_native(slug: str, config: dict[str, Any] | None = None) -> set[str]:
    cfg = _config(slug) if config is None else config
    agent = _FakeNativeAgent()
    ti._inject_agent_tools(
        [agent], tool_scope=cfg.get("tool_scope") or None,
        agent_name=slug, agent_config=cfg,
    )
    return _native_names(agent)


# ── The registry map this file relies on ───────────────────────────────────

def test_the_registry_holds_the_agents_this_fence_names() -> None:
    """The named cases below must read real configs, so they must exist."""
    named = {"projects-assistant", "crm-assistant", "apis-config", "email-assistant",
             "whatsapp-assistant", "orchestrator", "task-manager", "app-builder"}
    assert named <= set(SLUG_TO_DIR), sorted(named - set(SLUG_TO_DIR))
    for slug, dirname in SLUG_TO_DIR.items():
        assert (REPO / "apps" / "agents" / dirname / "config.json").is_file(), slug


# ── 1. Shared agents get no shell tool ─────────────────────────────────────

@pytest.mark.parametrize("slug", ["projects-assistant", "crm-assistant", "apis-config"])
def test_the_named_shared_agents_get_no_shell_tool(slug: str) -> None:
    names = _inject_native(slug)
    assert not (names & SHELL_TOOLS), f"{slug} still holds {sorted(names & SHELL_TOOLS)}"
    # The block is narrow: the rest of the floor stays, but for a tool that
    # the agent opts out of itself (config.json: floor_opt_out).
    opted_out = ti._floor_opt_out(slug, _config(slug))
    for kept in ("write_artifact", "web_search", "ask_questions", "list_integrations"):
        if kept in opted_out:
            assert kept not in names, f"{slug} holds {kept!r}, which it opted out of"
            continue
        assert kept in names, f"{slug} lost {kept!r}, which D85 does not touch"


def test_apis_config_loses_the_install_dependency_it_declares() -> None:
    """apis-config names ``install_dependency`` in its own ``tool_scope``."""
    assert "install_dependency" in _config("apis-config")["tool_scope"]
    assert "install_dependency" not in _inject_native("apis-config")


def test_every_agent_in_the_registry_gets_no_shell_tool() -> None:
    """Shared and personal alike (D129). No registry agent is exempt."""
    assert {"projects-assistant", "crm-assistant", "apis-config", "orchestrator",
            "task-manager", "app-builder", "email-assistant",
            "whatsapp-assistant"} <= set(SLUG_TO_DIR)
    for slug in SLUG_TO_DIR:
        names = _inject_native(slug)
        assert not (names & SHELL_TOOLS), f"{slug} still holds {sorted(names & SHELL_TOOLS)}"


@pytest.mark.parametrize("config", [
    None,
    {},
    {"sharing": {"instancing": "shared"}},
    {"sharing": {"instancing": "team", "team": "ops"}},
    {"sharing": {"instancing": ["not", "a", "string"]}},
    {"schema_version": "not-a-number"},
    {"sharing": {"instancing": "personal"}},
    {"sharing": {"instancing": "personal", "visibility": "private"}},
])
def test_every_config_withholds_every_shell_tool(config: Any) -> None:
    """A personal config too, since D129 (2026-10-27)."""
    assert ti._withheld_shell_tools("some-agent", config) == SHELL_TOOLS
    assert ti._host_shell_refused("some-agent", config) is True


# ── 2. Personal agents lose them too (D129) ────────────────────────────────

@pytest.mark.parametrize("slug", ["email-assistant", "whatsapp-assistant"])
def test_a_personal_agent_gets_no_shell_tool(slug: str) -> None:
    cfg = _config(slug)
    assert cfg["sharing"]["instancing"] == "personal"
    # The open scope still names two shell tools, so the block has work to do.
    open_scope = (ti._resolve_injected_scope(cfg.get("tool_scope")) or set()) & SHELL_TOOLS
    assert open_scope == {"run_script", "code_task"}, "the floor changed; re-read D85"
    assert ti._withheld_shell_tools(slug, cfg) == SHELL_TOOLS
    assert ti._host_shell_refused(slug, cfg) is True
    names = _inject_native(slug)
    assert not (names & SHELL_TOOLS), f"{slug} still holds {sorted(names & SHELL_TOOLS)}"


def test_a_covered_personal_agent_gets_its_shell_tools_back(monkeypatch) -> None:
    """D130: the way back is the same for a personal agent. The host half
    stays refused, because the broker does not hold the CLI."""
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("whatsapp-assistant", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    cfg = _config("whatsapp-assistant")
    assert ti._withheld_shell_tools("whatsapp-assistant", cfg) == frozenset()
    assert ti._host_shell_refused("whatsapp-assistant", cfg) is True
    assert {"run_script", "code_task"} <= _inject_native("whatsapp-assistant")
    # The cover is per agent: email-assistant in the same org stays blocked.
    assert ti._withheld_shell_tools("email-assistant", _config("email-assistant")) == SHELL_TOOLS


# ── 3. covers(agent, org) gives the tools back ─────────────────────────────

def _cover_only(agent: str, org: str):
    return lambda a, o: (a, o) == (agent, org)


def test_a_covered_agent_gets_its_shell_tools_back(monkeypatch) -> None:
    """A cover here means the broker runs the shell tools (§7.7). It is not
    the ``projects`` target of D86, under which projects-assistant keeps the
    three withheld (§16.3), so this test covers crm-assistant."""
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("crm-assistant", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    names = _inject_native("crm-assistant")
    assert {"run_script", "code_task"} <= names
    # The cover is per agent: projects-assistant in the same org stays blocked.
    assert not (_inject_native("projects-assistant") & SHELL_TOOLS)


def test_a_cover_in_one_org_is_not_a_cover_in_another(monkeypatch) -> None:
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("crm-assistant", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_B)
    assert not (_inject_native("crm-assistant") & SHELL_TOOLS)


def test_a_run_with_no_org_gets_no_cover(monkeypatch) -> None:
    """The org comes from the run binding (R11). No binding, no cover."""
    monkeypatch.setattr(ti, "_sandbox_covers", lambda a, o: True)
    monkeypatch.setattr(executor, "_current_run_org", lambda: None)
    assert not (_inject_native("projects-assistant") & SHELL_TOOLS)


def test_the_local_cover_says_no_for_every_agent() -> None:
    """Until WS-43f, ``covers()`` is ``False`` for every agent (§7.7)."""
    for slug in SLUG_TO_DIR:
        assert ti._sandbox_covers(slug, ORG_A) is False


# ── 4. The addendum does not advertise a tool the agent lacks ──────────────

def test_a_shared_copilot_agent_gets_no_coding_section() -> None:
    cfg = _config("app-builder")
    assert (cfg.get("sharing") or {}).get("instancing", "shared") == "shared"
    for is_sub, markers in ((False, CODING_MARKERS_FULL), (True, CODING_MARKERS_COMPACT)):
        agent = _FakeCopilotAgent()
        ti._inject_agent_tools(
            [agent], is_sub_agent=is_sub, tool_scope=cfg.get("tool_scope"),
            agent_name="app-builder", agent_config=cfg,
        )
        assert not (_copilot_names(agent) & SHELL_TOOLS)
        text = _system_message(agent)
        for marker in markers:
            assert marker not in text, f"is_sub={is_sub}: the addendum still says {marker!r}"


def _coach_copilot() -> _FakeCopilotAgent:
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools(
        [agent], tool_scope=None, agent_name="coach",
        agent_config={"sharing": {"instancing": "personal"}},
    )
    return agent


def test_a_personal_copilot_agent_gets_no_coding_section() -> None:
    """D129: a personal prompt does not advertise a tool the agent lacks."""
    agent = _coach_copilot()
    text = _system_message(agent)
    for marker in CODING_MARKERS_FULL:
        assert marker not in text, f"the addendum still says {marker!r}"
    assert not (_copilot_names(agent) & SHELL_TOOLS)


def test_a_covered_copilot_agent_keeps_its_coding_section(monkeypatch) -> None:
    """The control for the two tests above: the same path, under a cover."""
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("coach", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    agent = _coach_copilot()
    text = _system_message(agent)
    assert "### Coding skill (durable scripts)" in text
    assert {"run_script", "code_task", "install_dependency"} <= _copilot_names(agent)


def test_an_unscoped_shared_agent_loses_only_the_shell_sections() -> None:
    """The withheld scope renders every section the open scope renders, less
    the two that only the shell tools gate."""
    shell_only = [s.text for s in ad.FULL_SECTIONS if s.gate and set(s.gate) <= SHELL_TOOLS]
    assert len(shell_only) == 2, "a shell section was added or removed; re-read D85"
    scope = ti._resolve_injected_scope(None, withheld=SHELL_TOOLS)
    assert scope is not None and not (scope & SHELL_TOOLS)
    open_parts = [t for _, t in ad.rendered_parts(effective_scope=None, risk_block="")]
    held_parts = [t for _, t in ad.rendered_parts(
        effective_scope=frozenset(scope), risk_block="",
    )]
    assert held_parts == [t for t in open_parts if t not in shell_only]


def test_the_skill_bodies_read_the_same_withheld_scope(monkeypatch, tmp_path) -> None:
    seen: dict[str, Any] = {}

    def _capture(root: str, *, effective_scope, registry_block):
        seen["scope"] = effective_scope
        return {}

    from acb_skills import skill_index
    monkeypatch.setattr(ti, "_skills_index_only", lambda: True)
    monkeypatch.setattr(skill_index, "materialize_skill_bodies", _capture)
    cfg = _config("projects-assistant")
    ti.materialize_skill_bodies_for_agent(
        "projects-assistant", str(tmp_path),
        tool_scope=cfg.get("tool_scope"), agent_config=cfg,
    )
    assert seen["scope"] is not None and not (seen["scope"] & SHELL_TOOLS)
    # A personal agent with no opt-out reads the withheld scope too (D129).
    ti.materialize_skill_bodies_for_agent(
        "whatsapp-assistant", str(tmp_path),
        tool_scope=_config("whatsapp-assistant").get("tool_scope"),
        agent_config=_config("whatsapp-assistant"),
    )
    assert seen["scope"] is not None and not (seen["scope"] & SHELL_TOOLS)
    ti.materialize_skill_bodies_for_agent(
        "email-assistant", str(tmp_path),
        tool_scope=_config("email-assistant").get("tool_scope"),
        agent_config=_config("email-assistant"),
    )
    assert not {"run_script", "code_task"} & seen["scope"], "the opt-out reaches the bodies"


# ── 5. No branch puts a shell tool back ────────────────────────────────────

def test_the_no_match_fallback_cannot_restore_a_shell_tool(monkeypatch) -> None:
    """With nothing in scope, injection falls back to the whole chain."""
    async def run_script(path: str, args: str = "") -> str:
        return ""

    async def code_task(task: str) -> str:
        return ""

    # It takes the agent name since WS-45 S1 (the `decide` engine).
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools",
                        lambda *_a, **_k: [run_script, code_task])
    agent = _FakeNativeAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="crm-assistant",
                           agent_config=_config("crm-assistant"))
    assert not (_native_names(agent) & SHELL_TOOLS)


# ── 6. Every executor call site threads the config ─────────────────────────

def test_every_executor_call_site_passes_the_agent_config() -> None:
    """Since D129 the D85 scope reads the name only. The config still holds
    the agent's ``floor_opt_out``, so a site that drops it loses that list."""
    tree = ast.parse(EXECUTOR_PY.read_text(encoding="utf-8"))
    calls: dict[str, list[int]] = {
        "_inject_agent_tools": [], "materialize_skill_bodies_for_agent": [],
    }
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in calls:
            calls[name].append(node.lineno)
            if not any(kw.arg == "agent_config" for kw in node.keywords):
                missing.append(f"{name} at executor.py:{node.lineno}")
    # Three: the stream run, the sub-agent and the one batch assembly. The
    # self-anneal retry injects through that assembly (2026-10-16).
    assert len(calls["_inject_agent_tools"]) >= 3, calls
    assert len(calls["materialize_skill_bodies_for_agent"]) >= 3, calls
    assert not missing, f"these call sites pass no agent_config: {missing}"


# ── 7. The Copilot CLI's own shell (D85, the host half) ────────────────────

def _shell_request(command: str = "ls -la") -> Any:
    from copilot.generated.session_events import (
        PermissionRequestShell,
        PermissionRequestShellCommand,
    )
    return PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[PermissionRequestShellCommand(identifier="ls", read_only=True)],
        full_command_text=command,
        has_write_file_redirection=False,
        intention="list the files",
        possible_paths=[],
        possible_urls=[],
    )


def _tool_request(name: str) -> Any:
    from copilot.generated.session_events import PermissionRequestCustomTool
    return PermissionRequestCustomTool(tool_description="a My Tasks tool", tool_name=name)


def _write_request(file_name: str) -> Any:
    from copilot.generated.session_events import PermissionRequestWrite
    return PermissionRequestWrite(
        can_offer_session_approval=False, diff="+ a line", file_name=file_name,
        intention="write a file", new_file_contents="a line\n",
    )


def _read_request(path: str) -> Any:
    from copilot.generated.session_events import PermissionRequestRead
    return PermissionRequestRead(intention="read a file", path=path)


_INVOCATION = {"session_id": "s-d85", "managed_settings_enabled": False}


def _refused(result: Any) -> bool:
    from copilot.generated.rpc import PermissionDecisionReject
    return isinstance(result, PermissionDecisionReject)


def _approved(result: Any) -> bool:
    """An explicit approval. ``None`` (no handler ran) is neither."""
    from copilot.generated.rpc import PermissionDecisionApproveOnce
    return isinstance(result, PermissionDecisionApproveOnce)


def _with_run_flag(refused: bool | None, *, workspace: str | None = None):
    """A run's artifact context with ``host_shell_refused`` set, or with no key."""
    import contextlib

    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    @contextlib.contextmanager
    def _scope():
        with artifact_context_scope():
            values: dict[str, Any] = {
                "agent_name": "probe", "workspace_root": workspace or str(REPO),
            }
            if refused is not None:
                values["host_shell_refused"] = refused
            bind_artifact_context(**values)
            yield
    return _scope()


@pytest.mark.parametrize("request_factory", [
    _shell_request,
    lambda: {"kind": "shell", "full_command_text": "pwd"},
    lambda: {"kind": "powershell", "full_command_text": "Get-ChildItem"},
    lambda: {"kind": "bash"},
    lambda: {"full_command_text": "node build/build_t2.mjs"},
])
def test_the_guard_refuses_a_shell_request_of_a_refused_run(request_factory) -> None:
    from acb_skills import permission_policy as pp

    calls: list[Any] = []
    guarded = pp.guard_shared_agent_shell(lambda r, i: calls.append(r) or "approved")
    with _with_run_flag(True):
        result = guarded(request_factory(), _INVOCATION)
    assert _refused(result), result
    assert pp.SHELL_WITHHELD_REASON in result.feedback
    assert not calls, "a refused shell request must not reach the inner handler"


def test_the_guard_passes_every_other_request_on() -> None:
    from acb_skills import permission_policy as pp

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run_flag(True):
        assert guarded(_tool_request("my_tasks_capture"), _INVOCATION) == "approved"
        assert guarded({"kind": "read", "path": "x"}, _INVOCATION) == "approved"


def test_a_run_that_allows_the_shell_reaches_the_inner_handler() -> None:
    from acb_skills import permission_policy as pp

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run_flag(False):
        assert guarded(_shell_request(), _INVOCATION) == "approved"


def test_a_frame_with_no_run_flag_refuses_the_shell() -> None:
    """Fail closed, as every H-201 reader does: no flag reads as refused."""
    from acb_skills import permission_policy as pp
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    guarded = pp.guard_shared_agent_shell(lambda r, i: "approved")
    with _with_run_flag(None):
        assert _refused(guarded(_shell_request(), _INVOCATION))
    with artifact_context_scope():
        bind_artifact_context()
        assert _refused(guarded(_shell_request(), _INVOCATION))


@pytest.mark.parametrize("mode", ["enforce", "audit", "approve_all"])
def test_the_factory_guards_the_shell_in_every_mode(monkeypatch, mode: str) -> None:
    """Production runs ``enforce``. A later switch must not waive D85."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", mode)
    handler = executor._copilot_permission_handler()
    with _with_run_flag(True):
        assert _refused(handler(_shell_request(), _INVOCATION)), mode
        assert _approved(handler(_tool_request("my_tasks_list"), _INVOCATION)), mode
    with _with_run_flag(False):
        assert _approved(handler(_shell_request(), _INVOCATION)), mode


def test_the_factory_refuses_everything_when_the_policy_cannot_load(monkeypatch) -> None:
    """The old fallback was a bare ``approve_all``, with no D85 guard."""
    monkeypatch.setitem(sys.modules, "acb_skills.permission_policy", None)
    handler = executor._copilot_permission_handler()
    assert _refused(handler(_tool_request("my_tasks_list"), _INVOCATION))
    assert _refused(handler(_shell_request(), _INVOCATION))


def test_every_artifact_context_site_binds_both_flags() -> None:
    """The run, the batch run and the sub-agent each set their OWN flags."""
    tree = ast.parse(EXECUTOR_PY.read_text(encoding="utf-8"))
    sites: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        keys = {kw.arg for kw in node.keywords}
        is_bind = name == "bind_artifact_context"
        is_sub_agent = name == "derive_artifact_context" and "agent_name" in keys
        if is_bind or is_sub_agent:
            sites.append((name, node.lineno, keys))
    assert len(sites) >= 3, sites
    need = {"shell_tools_withheld", "host_shell_refused"}
    missing = [f"{n} at executor.py:{line}" for n, line, keys in sites if not need <= keys]
    assert not missing, f"these sites bind no D85 flags: {missing}"


# ── 7a. Two halves: a cover lifts the tools, and never the host shell ──────

def test_the_host_half_ignores_a_cover(monkeypatch) -> None:
    """The broker runs the shell TOOLS in a container. The CLI is not in it."""
    monkeypatch.setattr(ti, "_sandbox_covers", lambda a, o: True)
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    cfg = _config("crm-assistant")
    assert ti._withheld_shell_tools("crm-assistant", cfg) == frozenset()
    assert ti._host_shell_refused("crm-assistant", cfg) is True
    # A covered personal agent keeps its CLI shell refused as well (D129).
    personal = {"sharing": {"instancing": "personal"}}
    assert ti._withheld_shell_tools("coach", personal) == frozenset()
    assert ti._host_shell_refused("coach", personal) is True


def test_no_agent_has_its_cli_in_a_broker_sandbox_today() -> None:
    for slug in SLUG_TO_DIR:
        assert ti._copilot_cli_in_broker_sandbox(slug, ORG_A) is False


# ── 7b. The root agent is left as it is, until the owner decides ──────────

def test_the_root_dev_agent_is_the_one_owner_pending_name() -> None:
    root_cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    assert root_cfg.get("name") == "metorite" and "sharing" not in root_cfg, (
        "the root config changed. Re-read maf_coding_engine.md §15.4 before you touch the exemption"
    )
    assert frozenset({"metorite"}) == ti._D85_OWNER_PENDING
    assert ti._withheld_shell_tools("metorite", root_cfg) == frozenset()
    assert ti._host_shell_refused("metorite", root_cfg) is False
    # The same config under any other name is shared, and D85 binds it.
    assert ti._withheld_shell_tools("not-metorite", root_cfg) == SHELL_TOOLS
    assert ti._host_shell_refused("not-metorite", root_cfg) is True


# ── 7c. One install function, at every site ────────────────────────────────

def test_every_copilot_site_installs_through_the_one_function() -> None:
    """No site may gate on an empty slot, and no site may set the slot
    itself. A factory's own handler must get the guard as well (P1, fix
    round 1 of PR #598)."""
    code_session = EXECUTOR_PY.parent / "code_session.py"
    installs = 0
    for path in (EXECUTOR_PY, code_session):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    assert not (
                        isinstance(tgt, ast.Attribute) and tgt.attr == "_permission_handler"
                    ), f"{path.name}:{node.lineno} sets _permission_handler itself"
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute):
                assert node.left.attr != "_permission_handler", (
                    f"{path.name}:{node.lineno} gates on the handler slot"
                )
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                installs += name == "_install_copilot_permission_handler"
    assert installs >= 6, installs


def test_a_failed_install_refuses_instead_of_keeping_the_factory_handler(monkeypatch) -> None:
    """Fix round 2, item (e): the install used to log and leave a factory's
    own ``approve_all`` in place, with no guard at all."""
    import orchestrator._copilot_session as cs
    from copilot import PermissionHandler

    def _boom(existing: Any = None) -> Any:
        raise RuntimeError("the factory broke")

    monkeypatch.setattr(cs, "_copilot_permission_handler", _boom)

    class _Agent:
        _permission_handler = PermissionHandler.approve_all

    agent = _Agent()
    cs._install_copilot_permission_handler(agent)
    with _with_run_flag(False):
        assert _refused(agent._permission_handler(_shell_request(), _INVOCATION))
        assert _refused(agent._permission_handler(_tool_request("my_tasks_list"), _INVOCATION))


# ── 7d. decide() reads the SDK 1.0 write and read shapes ───────────────────

def test_a_cli_write_is_contained_in_the_workspace(tmp_path) -> None:
    from acb_skills import permission_policy as pp

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = str(tmp_path / "elsewhere" / "outside.txt")
    with _with_run_flag(False, workspace=str(ws)):
        assert pp.decide(_write_request("agent-data/notes.md"))[:2] == (True, "write_in_workspace")
        assert pp.decide(_write_request(str(ws / "outputs" / "a.md")))[0] is True
        assert pp.decide(_write_request(outside))[:2] == (False, "write_out_of_workspace")
    with _with_run_flag(None, workspace=""):
        from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
        with artifact_context_scope():
            bind_artifact_context()
            assert pp.decide(_write_request("agent-data/notes.md"))[:2] == (
                False, "write_without_workspace",
            )


def test_a_cli_read_is_a_read_and_is_contained(tmp_path) -> None:
    from acb_skills import permission_policy as pp
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    ws = tmp_path / "ws"
    ws.mkdir()
    with _with_run_flag(False, workspace=str(ws)):
        assert pp.decide(_read_request("agent-data/NOTES.md"))[:2] == (True, "read_in_workspace")
        assert pp.decide(_read_request(str(tmp_path / ".env")))[:2] == (
            False, "read_out_of_workspace",
        )
    with artifact_context_scope():
        bind_artifact_context()
        assert pp.decide(_read_request("agent-data/NOTES.md"))[:2] == (
            False, "read_without_workspace",
        )


def test_the_enforce_handler_refuses_a_write_outside_end_to_end(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    handler = executor._copilot_permission_handler()
    with _with_run_flag(False, workspace=str(tmp_path)):
        assert _refused(handler(_write_request("C:/elsewhere/outside.txt"
                                               if sys.platform == "win32"
                                               else "/elsewhere/outside.txt"), _INVOCATION))
        assert _approved(handler(_write_request("outputs/report.md"), _INVOCATION))


# ── 7e. No file hooks, on create and on resume ─────────────────────────────

def test_a_metorite_session_never_loads_file_hooks() -> None:
    """A hook in the working dir runs a command with no permission request."""
    import asyncio

    from orchestrator.copilot_agent import MetoriteCopilotAgent

    agent = MetoriteCopilotAgent(
        name="probe", instructions="x", default_options={"model": "tier-balanced"},
    )

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def create_session(self, **kw: Any) -> object:
            self.calls.append(("create", kw))
            return object()

        async def resume_session(self, sid: str, **kw: Any) -> object:
            self.calls.append(("resume", kw))
            return object()

    client = _Client()
    agent._client = client
    asyncio.run(agent._create_session(False))
    asyncio.run(agent._resume_session("sid-1", False))
    assert [c for c, _ in client.calls] == ["create", "resume"]
    for call, kw in client.calls:
        assert kw.get("enable_file_hooks") is False, f"{call}: {sorted(kw)}"


# ── 7f. The self-heal hint and the integrations prose ──────────────────────

def test_the_self_heal_hint_offers_install_dependency_only_to_a_holder() -> None:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    for value, offered in ((True, False), (None, False), (False, True)):
        with artifact_context_scope():
            bind_artifact_context(**({} if value is None else {"shell_tools_withheld": value}))
            text = executor._missing_dependency_message("t", ImportError("no pandas"), "pandas")
        assert ("install_dependency" in text) is offered, (value, text)
        assert ("ask an admin" in text) is not offered, (value, text)


def test_an_agent_without_the_coding_section_still_reads_about_integrations() -> None:
    scope = frozenset(ti._resolve_injected_scope(None, withheld=SHELL_TOOLS))
    full = ti._build_injected_tools_addendum(effective_scope=scope)
    compact = ti._build_injected_tools_addendum(is_sub_agent=True, effective_scope=scope)
    assert "### Coding skill (durable scripts)" not in full
    assert "### Integrations" in full and "**list_integrations()**" in full
    assert "list_integrations() — which platform integrations" in compact
    # An agent that holds the coding skill reads the bullet once, not twice.
    open_full = ti._build_injected_tools_addendum(effective_scope=None)
    assert open_full.count("**list_integrations()**") == 1


# ── 7g. The probe: one turn through the real Copilot path ──────────────────

def _my_tasks_tool_names() -> list[str]:
    """The tools task-manager's own factory imports from skill_my_tasks."""
    src = (REPO / "apps" / "agents" / "agent-task-manager" / "agents.py").read_text(
        encoding="utf-8",
    )
    names: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "skill_my_tasks":
            names += [a.name for a in node.names]
    return names


_OUTSIDE = "C:/elsewhere/outside.txt" if sys.platform == "win32" else "/elsewhere/outside.txt"


class _CopilotProbeAgent:
    """Copilot-SDK shaped: ``_tools``, ``_default_options``, a handler slot and
    ``_prepare_tools``, so injection, the handler install and
    ``carry_run_context`` all treat it as a Copilot agent. During its turn it
    asks its handler, from a thread with no context, as the SDK does.
    *preset* is a handler that the agent's own factory set."""

    def __init__(self, tool_names: list[str], preset: Any = None) -> None:
        self.name = "probe"
        self._tools: list[Any] = []
        self.tools: list[Any] = []
        self._default_options: dict[str, Any] = {"model": "tier-balanced"}
        self._permission_handler: Any = preset
        self._tool_names = tool_names
        self.decisions: dict[str, Any] = {}

    def _prepare_tools(self, tools: Any) -> list[Any]:
        return list(tools or [])

    def _ask(self, key: str, request: Any) -> None:
        import threading

        box: dict[str, Any] = {}
        thread = threading.Thread(
            target=lambda: box.update(r=self._permission_handler(request, _INVOCATION)),
        )
        thread.start()
        thread.join()
        self.decisions[key] = box.get("r")

    def run(self, *_a: Any, **_k: Any) -> Any:
        return self._turn()

    async def _turn(self) -> Any:
        from types import SimpleNamespace

        for name in self._tool_names:
            self._ask(name, _tool_request(name))
        self._ask("shell", _shell_request("cat /etc/hostname"))
        self._ask("write_inside", _write_request("agent-data/today.md"))
        self._ask("write_outside", _write_request(_OUTSIDE))
        yield SimpleNamespace(
            role="assistant", message_id="m1",
            contents=[SimpleNamespace(type="text", text="Captured it.")],
        )

    async def __aenter__(self) -> _CopilotProbeAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _loader(monkeypatch, tmp_path, agent: Any, config: dict[str, Any], slug: str) -> None:
    from acb_common import get_settings

    class _Loaded:
        agent_dir = tmp_path / "clone"
        agent_name = slug

        def __init__(self) -> None:
            self.config = config

        def build_agents(self) -> list[Any]:
            return [agent]

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    (tmp_path / "clone").mkdir(exist_ok=True)
    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "_current_run_org", _REAL_CURRENT_RUN_ORG)


def _run_stream(slug: str) -> list[str]:
    import asyncio

    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context

    async def _collect() -> list[str]:
        return [line async for line in executor.run_agent_stream(
            slug, {"message": "capture: call the plumber"}, thread_id=f"t-d85-{slug}",
        )]

    token = bind_tenant(ORG_A)
    try:
        return asyncio.run(_collect())
    finally:
        release_tenant(token)
        bind_artifact_context()


def _probe(
    monkeypatch, tmp_path, *, slug: str, config: dict[str, Any], preset: Any = None,
) -> _CopilotProbeAgent:
    names = _my_tasks_tool_names()
    agent = _CopilotProbeAgent(names, preset=preset)
    _loader(monkeypatch, tmp_path, agent, config, slug)
    frames = _run_stream(slug)
    assert any("RUN_FINISHED" in f for f in frames), frames[-3:]
    assert len(agent.decisions) == len(names) + 3, sorted(agent.decisions)
    return agent


@pytest.mark.parametrize("mode", ["audit", "enforce"])
def test_probe_task_manager_turn_passes_its_tools_and_refuses_the_shell(
    monkeypatch, tmp_path, mode: str,
) -> None:
    monkeypatch.setenv("AGENT_PERMISSION_MODE", mode)
    names = _my_tasks_tool_names()
    assert len(names) == 29 and all(n.startswith("my_tasks_") for n in names), names
    agent = _probe(monkeypatch, tmp_path, slug="task-manager", config=_config("task-manager"))
    for name in names:
        assert _approved(agent.decisions[name]), f"{mode}: {name} was not approved"
    assert _refused(agent.decisions["shell"]), f"{mode}: the shell was not refused"
    assert not ({ti._tool_name(t) for t in agent._tools} & SHELL_TOOLS)
    # A write inside the run's workspace still works. Production runs enforce,
    # where a write outside the workspace is now refused.
    assert _approved(agent.decisions["write_inside"]), agent.decisions["write_inside"]
    if mode == "enforce":
        assert _refused(agent.decisions["write_outside"]), agent.decisions["write_outside"]


def test_probe_app_builder_loses_its_build_shell(monkeypatch, tmp_path) -> None:
    """WS-43h would give it back in the sandbox. D86 parks WS-43h."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    agent = _probe(monkeypatch, tmp_path, slug="app-builder", config=_config("app-builder"))
    assert _refused(agent.decisions["shell"])


def test_probe_a_personal_copilot_agent_loses_its_shell(monkeypatch, tmp_path) -> None:
    """No in-repo personal agent runs on the Copilot path today. This one
    proves that the guard refuses such an agent's CLI shell (D129)."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    cfg = {"name": "coach", "sharing": {"instancing": "personal"}}
    agent = _probe(monkeypatch, tmp_path, slug="coach", config=cfg)
    assert _refused(agent.decisions["shell"]), agent.decisions["shell"]
    assert not ({ti._tool_name(t) for t in agent._tools} & SHELL_TOOLS)
    assert _approved(agent.decisions["my_tasks_capture"])


def test_probe_a_whatsapp_assistant_run_holds_no_shell_tool(monkeypatch, tmp_path) -> None:
    """D129's own done-when: a run of whatsapp-assistant, with its real
    config, through the real ``run_agent_stream``, gets none of the three."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    cfg = _config("whatsapp-assistant")
    agent = _probe(monkeypatch, tmp_path, slug="whatsapp-assistant", config=cfg)
    held = {ti._tool_name(t) for t in agent._tools}
    assert not (held & SHELL_TOOLS), sorted(held & SHELL_TOOLS)
    assert _refused(agent.decisions["shell"]), agent.decisions["shell"]


def test_probe_a_cover_gives_the_tools_back_and_never_the_host_shell(
    monkeypatch, tmp_path,
) -> None:
    """A cover means the broker runs the shell TOOLS. The CLI still runs on
    the host, so its shell stays refused (the verifier's design rule)."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("task-manager", ORG_A))
    agent = _probe(monkeypatch, tmp_path, slug="task-manager", config=_config("task-manager"))
    assert {"run_script", "code_task"} <= {ti._tool_name(t) for t in agent._tools}
    assert _refused(agent.decisions["shell"]), agent.decisions["shell"]


def _preset_approve_all() -> Any:
    from copilot import PermissionHandler
    return PermissionHandler.approve_all


def _preset_custom() -> Any:
    from copilot.generated.rpc import PermissionDecisionApproveOnce

    def custom(request: Any, invocation: Any) -> Any:
        return PermissionDecisionApproveOnce()
    return custom


@pytest.mark.parametrize("preset", [_preset_approve_all, _preset_custom])
@pytest.mark.parametrize("case", ["shared", "personal", "covered"])
def test_probe_a_factory_handler_still_gets_the_guard(
    monkeypatch, tmp_path, preset, case: str,
) -> None:
    """P1 of fix round 1: the guard used to go only into an EMPTY slot, so an
    agent whose factory set ``approve_all`` or its own handler kept its shell.
    Every repo-registered agent runs as ``github-copilot``. Since D129 the
    guard refuses a personal agent's shell too."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    cfg = _config("task-manager")
    slug = "task-manager"
    if case == "personal":
        cfg, slug = {"name": "coach", "sharing": {"instancing": "personal"}}, "coach"
    if case == "covered":
        monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("task-manager", ORG_A))
    agent = _probe(monkeypatch, tmp_path, slug=slug, config=cfg, preset=preset())
    # The factory's own handler still answers every other request.
    assert _approved(agent.decisions["my_tasks_capture"])
    assert _refused(agent.decisions["shell"]), (case, agent.decisions["shell"])


# ── 7h. The sub-agent sets its OWN flags ───────────────────────────────────

_ROOT = "root"  # the sub_cfg_key of the repo-root config.json


@pytest.mark.parametrize("parent_refused, sub_slug, sub_cfg_key, sub_refused", [
    (False, "task-manager", "task-manager", True),   # open parent, shared sub
    (True, "coach", None, True),                     # refused parent, personal sub (D129)
    (True, "metorite", _ROOT, False),                # refused parent, the root dev agent
])
def test_a_sub_agent_takes_its_own_host_shell_answer(
    monkeypatch, tmp_path, parent_refused, sub_slug, sub_cfg_key, sub_refused,
) -> None:
    """The verifier's gap: the sub-agent's flag was checked only for the key.
    A hard-coded value at the sub-agent site must turn this red. Since D129
    only the root dev agent answers ``False``, so it is the third case. Its
    first-party gate is switched off here, and
    ``test_root_agent_first_party.py`` fences that gate."""
    import asyncio

    import gateway.routes.agent as agent_routes
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    monkeypatch.setenv("AGENT_PERMISSION_MODE", "enforce")
    # Since 2026-10-19 a delegated Copilot agent is refused before it runs
    # (D84, D92, test_copilot_specialist_refused.py). The Copilot sub-agent
    # branch stays as a second layer, so this test switches the refusal off.
    monkeypatch.setattr(executor, "refuse_copilot_specialist", lambda *_a, **_k: None)

    async def _allowed(_name: str) -> None:
        return None

    monkeypatch.setattr(executor, "_assert_may_run_agent", _allowed)
    if sub_cfg_key == _ROOT:
        cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    elif sub_cfg_key:
        cfg = _config(sub_cfg_key)
    else:
        cfg = {"name": "coach", "sharing": {"instancing": "personal"}}
    agent = _CopilotProbeAgent(_my_tasks_tool_names())
    _loader(monkeypatch, tmp_path, agent, cfg, sub_slug)
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [
        {"name": "coach", "agent_runtime": "github-copilot",
         "repo_name": None, "local_path": None},
    ])

    async def _call() -> str:
        return await executor._run_sub_agent_streaming(
            sub_slug, "plan my day", "r-sub-d85", event_queue=asyncio.Queue(),
        )

    token = bind_tenant(ORG_A)
    try:
        with artifact_context_scope():
            bind_artifact_context(
                agent_name="parent", run_id="r-parent", session_id="t-parent",
                workspace_root=str(tmp_path), member="ana@example.com",
                host_shell_refused=parent_refused, shell_tools_withheld=parent_refused,
            )
            asyncio.run(_call())
    finally:
        release_tenant(token)
        bind_artifact_context()
    assert "shell" in agent.decisions, sorted(agent.decisions)
    if sub_refused:
        assert _refused(agent.decisions["shell"]), agent.decisions["shell"]
    else:
        assert _approved(agent.decisions["shell"]), agent.decisions["shell"]


# ── 7j. A shared run delegates to a personal agent (D129, H-225) ──────────

class _NativeSubAgent(_FakeNativeAgent):
    """A native MAF agent. Delegation injects its tools and then hands it to
    the batch run, which this probe stubs."""


def _delegate(
    monkeypatch, tmp_path, slug: str, *, org: str = ORG_A,
) -> tuple[_NativeSubAgent, dict[str, Any]]:
    """A shared parent run (both D85 flags set) delegates to *slug*, with its
    real config, through ``executor._run_sub_agent_streaming``. Returns the
    sub-agent and the artifact context that its batch run saw."""
    import asyncio

    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import (
        artifact_context,
        artifact_context_scope,
        bind_artifact_context,
    )

    agent = _NativeSubAgent()
    _loader(monkeypatch, tmp_path, agent, _config(slug), slug)
    seen: dict[str, Any] = {}

    async def _batch(name: str, payload: dict[str, Any], **_k: Any) -> dict[str, Any]:
        seen.update(artifact_context())
        return {"result": f"{name} is done"}

    async def _no_mark(*_a: Any, **_k: Any) -> None:
        return None

    async def _no_working_set(*_a: Any, **_k: Any) -> tuple[None, str]:
        return None, ""

    async def _not_marked(*_a: Any, **_k: Any) -> bool:
        return False

    # No database and no Redis: the mail marks of the calling chat stay out.
    monkeypatch.setattr(executor, "run_agent", _batch)
    monkeypatch.setattr(executor, "_mark_untrusted_session", _no_mark)
    monkeypatch.setattr(executor, "_email_working_set_for", _no_working_set)
    monkeypatch.setattr(executor, "_chat_marked", _not_marked)

    async def _call() -> str:
        return await executor._run_sub_agent_streaming(
            slug, "read my inbox", "r-sub-d129", event_queue=asyncio.Queue(),
        )

    token = bind_tenant(org)
    try:
        with artifact_context_scope():
            bind_artifact_context(
                agent_name="projects-assistant", run_id="r-parent",
                session_id="t-parent", workspace_root=str(tmp_path),
                member="ana@example.com",
                host_shell_refused=True, shell_tools_withheld=True,
            )
            out = asyncio.run(_call())
    finally:
        release_tenant(token)
        bind_artifact_context()
    assert out == f"{slug} is done", out
    return agent, seen


@pytest.mark.parametrize("slug", ["email-assistant", "whatsapp-assistant"])
def test_probe_a_delegated_personal_agent_holds_no_shell_tool(
    monkeypatch, tmp_path, slug: str,
) -> None:
    """H-225's gap: a shared agent could call a personal agent with
    ``call_agent``, and the personal agent kept its shell tools. Now the
    sub-run holds none of the three, and it binds both D85 flags itself."""
    agent, seen = _delegate(monkeypatch, tmp_path, slug)
    held = _native_names(agent)
    assert not (held & SHELL_TOOLS), f"{slug} still holds {sorted(held & SHELL_TOOLS)}"
    assert seen.get("agent_name") == slug, seen.get("agent_name")
    assert seen.get("shell_tools_withheld") is True, seen.get("shell_tools_withheld")
    assert seen.get("host_shell_refused") is True, seen.get("host_shell_refused")


def test_probe_a_covered_delegated_personal_agent_gets_the_tools_back(
    monkeypatch, tmp_path,
) -> None:
    """D130 through the delegation path: a cover for the sub-agent in the
    run's org lifts the tool half. The host half stays refused."""
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("whatsapp-assistant", ORG_A))
    agent, seen = _delegate(monkeypatch, tmp_path, "whatsapp-assistant")
    assert {"run_script", "code_task"} <= _native_names(agent)
    assert seen.get("shell_tools_withheld") is False, seen.get("shell_tools_withheld")
    assert seen.get("host_shell_refused") is True, seen.get("host_shell_refused")


# ── 7i. Tier 2 always denies the CLI shell, as main did for every case ────

class _Tier2Agent:
    """Not Copilot-shaped (no ``_default_options``), with an unset client, so
    ``run_agent_stream`` takes Tier 2 and builds the CLI connection itself."""

    def __init__(self) -> None:
        self.name = "tier2-probe"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._client: Any = None
        self._settings: dict[str, Any] = {}

    async def run(self, *_a: Any, **_k: Any) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(text="done", messages=[])


def _drive_tier2(monkeypatch, tmp_path, slug: str, cfg: dict[str, Any]) -> dict[str, Any]:
    import copilot

    seen: dict[str, Any] = {}

    class _FakeConnection:
        @classmethod
        def for_stdio(cls, path: Any = None, args: Any = None) -> Any:
            seen["args"] = list(args or [])
            return object()

    class _FakeClient:
        def __init__(self, **kw: Any) -> None:
            seen["client"] = kw

    monkeypatch.setattr(copilot, "RuntimeConnection", _FakeConnection)
    monkeypatch.setattr(copilot, "CopilotClient", _FakeClient)
    _loader(monkeypatch, tmp_path, _Tier2Agent(), cfg, slug)
    _run_stream(slug)
    return seen


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
@pytest.mark.parametrize("slug, cfg_key", [
    ("crm-assistant", "crm-assistant"),   # shared, registry label "maf"
    ("coach", None),                      # personal: as on main
])
def test_tier_2_always_denies_the_cli_shell(monkeypatch, tmp_path, slug, cfg_key) -> None:
    """On main, Tier 2 passed ``--deny-tool shell`` for every agent it could
    reach. It still does, now with no label branch at all."""
    cfg = _config(cfg_key) if cfg_key else {"name": "coach", "sharing": {"instancing": "personal"}}
    seen = _drive_tier2(monkeypatch, tmp_path, slug, cfg)
    assert "client" in seen, "the run did not reach the Tier 2 client"
    assert seen.get("args") == ["--deny-tool", "shell"], seen


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
def test_a_github_copilot_agent_never_reaches_tier_2(monkeypatch, tmp_path) -> None:
    """Why the old "allow the shell for github-copilot" branch could not run:
    the label alone sends a run down Tier 1.5, which always returns."""
    seen = _drive_tier2(monkeypatch, tmp_path, "task-manager", _config("task-manager"))
    assert "client" not in seen, seen


# ── 8. Task 3848: the self-heal and an agent's own shell tools ─────────────
#
# Two host paths that the injected-tool seam did not reach (§7.9).
#
# * The executor's dependency self-heal called ``install_dependency`` on the
#   host for every run. It now asks ``_run_may_install_dependency``, which
#   reads the ``shell_tools_withheld`` binding. Mutation: drop the
#   ``not _run_may_install_dependency()`` branch at the install site, and the
#   withheld Tier 2 test goes red. Bind ``False`` for a personal agent, and
#   its ``personal`` case goes red (D129).
# * ``_drop_withheld`` filters injected tools only. An agent's OWN tool named
#   like a shell tool now leaves a withheld run (``_drop_own_shell_tools``).
#   Mutation: remove its call, and the own-tool tests go red. Skip it for a
#   personal agent, and the ``personal`` case goes red (D129). Make the
#   platform ``run_command`` exemption ``False``, and the app-builder test
#   goes red.
# * The drop walks every slot of ``_tool_pool_slots``: a list, a tuple that
#   a real ``GitHubCopilotAgent`` keeps, and the legacy Copilot
#   ``_default_options["tools"]``. Skip one slot shape, and its real-Copilot
#   or legacy test goes red.

def test_the_install_answer_reads_the_binding_and_fails_closed(monkeypatch) -> None:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    # The package exports a function of the same name, so take the module.
    write_artifact = sys.modules["acb_skills.write_artifact"]
    for value, allowed in ((True, False), (False, True), (None, False), ("no", False)):
        with artifact_context_scope():
            bind_artifact_context(**({} if value is None else {"shell_tools_withheld": value}))
            assert executor._run_may_install_dependency() is allowed, value
    # A frame with no run binding at all.
    with artifact_context_scope():
        write_artifact._RUN_ARTIFACT_CONTEXT.set(None)
        assert executor._run_may_install_dependency() is False

    def _boom() -> Any:
        raise RuntimeError("the reader failed")

    monkeypatch.setattr(write_artifact, "artifact_context", _boom)
    assert executor._run_may_install_dependency() is False


class _SelfHealTier2Agent(_Tier2Agent):
    """A Tier 2 agent whose turn calls its own tool, which fails its import
    once. The shim wraps the entries of ``tools`` in place."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__()

        async def needs_pkg() -> str:
            calls.append("call")
            if len(calls) == 1:
                raise ModuleNotFoundError("No module named 'pkgx'", name="pkgx")
            return "pkgx is ready"

        self.tools = [needs_pkg]

    async def run(self, *_a: Any, **_k: Any) -> Any:
        import contextlib
        from types import SimpleNamespace

        tool_fn = next(t for t in self.tools if getattr(t, "__name__", "") == "needs_pkg")
        with contextlib.suppress(ImportError):
            await tool_fn()
        return SimpleNamespace(text="done", messages=[])


#: A personal agent that no registry row names, as in sections 7g and 7i.
_PERSONAL_COACH: dict[str, Any] = {"name": "coach", "sharing": {"instancing": "personal"}}


def _drive_self_heal(
    monkeypatch, tmp_path, *, covered: bool, personal: bool = False,
) -> tuple[list[str], list[str], str]:
    import copilot
    from acb_skills import dep_tools

    installs: list[str] = []

    async def _fake_install(packages: str) -> str:
        installs.append(packages)
        return f"Installed {packages}"

    class _FakeConnection:
        @classmethod
        def for_stdio(cls, path: Any = None, args: Any = None) -> Any:
            return object()

    class _FakeClient:
        def __init__(self, **kw: Any) -> None:
            pass

    monkeypatch.setattr(dep_tools, "install_dependency", _fake_install)
    monkeypatch.setattr(copilot, "RuntimeConnection", _FakeConnection)
    monkeypatch.setattr(copilot, "CopilotClient", _FakeClient)
    if covered:
        monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("crm-assistant", ORG_A))
    calls: list[str] = []
    slug, cfg = ("coach", _PERSONAL_COACH) if personal else (
        "crm-assistant", _config("crm-assistant"))
    _loader(monkeypatch, tmp_path, _SelfHealTier2Agent(calls), cfg, slug)
    frames = _run_stream(slug)
    results = [f for f in frames if "TOOL_CALL_RESULT" in f]
    return installs, calls, "\n".join(results)


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
@pytest.mark.parametrize("personal", [False, True], ids=["shared", "personal"])
def test_a_withheld_run_never_installs_a_missing_module(
    monkeypatch, tmp_path, personal: bool,
) -> None:
    """crm-assistant is shared, so its run binds ``shell_tools_withheld=True``.
    A personal agent binds it too, since D129 (task 808)."""
    installs, calls, results = _drive_self_heal(
        monkeypatch, tmp_path, covered=False, personal=personal)
    assert installs == [], f"the self-heal installed {installs} on the host"
    assert calls == ["call"], "the tool ran again with no install"
    assert "ask an admin" in results, results
    assert "install_dependency" not in results, results


@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
def test_a_covered_run_keeps_the_self_heal(monkeypatch, tmp_path) -> None:
    """A cover binds ``shell_tools_withheld=False``: install once, then retry."""
    installs, calls, results = _drive_self_heal(monkeypatch, tmp_path, covered=True)
    assert installs == ["pkgx"], installs
    assert calls == ["call", "call"], calls
    assert "pkgx is ready" in results, results


def test_the_root_agent_binds_an_open_install() -> None:
    """The root dev agent withholds nothing, so its self-heal stays open."""
    assert ti._withheld_shell_tools("metorite", None) == frozenset()


def _named(name: str, *, kind: str = "callable") -> Any:
    """An agent's own tool called *name*, in one of three shapes."""
    async def _fn() -> str:
        return name

    _fn.__name__ = name
    if kind == "function_tool":
        from agent_framework import tool
        return tool(_fn, name=name)
    if kind == "dict":
        return {"type": "function", "function": {"name": name}}
    return _fn


def _own_names(pool: list[Any]) -> list[str]:
    return [ti._tool_name(t) for t in pool]


def _own_shell_pool() -> list[Any]:
    from acb_skills import code_tools, sandbox_tools

    ti._collect_injectable_platform_tools(None)  # registers the host tools
    _store, broker = sandbox_tools.app_builder_session_parts()
    return [
        _named("run_script"),
        _named("code_task", kind="function_tool"),
        _named("install_dependency", kind="dict"),
        _named("run_command", kind="function_tool"),   # a repo's own, on the host
        code_tools.run_script,                         # the platform's HOST tool
        broker[0],                                     # the broker's run_command
        _named("my_report"),
    ]


@pytest.mark.parametrize("personal", [False, True], ids=["shared", "personal"])
def test_a_withheld_run_drops_an_own_tool_named_like_a_shell_tool(personal: bool) -> None:
    """A personal agent loses its own shell-named tools too (D129)."""
    slug, cfg = ("coach", _PERSONAL_COACH) if personal else (
        "crm-assistant", _config("crm-assistant"))
    agent = _FakeNativeAgent()
    agent.default_options["tools"] = _own_shell_pool()
    broker_tool = agent.default_options["tools"][5]
    ti._inject_agent_tools([agent], tool_scope=None, agent_name=slug, agent_config=cfg)
    pool = agent.default_options["tools"]
    names = _own_names(pool)
    assert not ({"run_script", "code_task", "install_dependency"} & set(names)), names
    # The only run_command left is the broker's own (app-builder's harness).
    assert names.count("run_command") == 1, names
    assert any(t is broker_tool for t in pool), "the broker's run_command left"
    assert "my_report" in names, names


@pytest.mark.filterwarnings("ignore::Warning")
def test_app_builder_keeps_the_broker_run_command() -> None:
    """app-builder is shared and uncovered, so D85 withholds all four names.
    Its REAL harness agent (§7.8) still holds the broker's ``run_command``."""
    from orchestrator.code_session import build_app_builder_agent

    cfg = _config("app-builder")
    assert ti._withheld_shell_tools("app-builder", cfg) == SHELL_TOOLS
    agent = build_app_builder_agent(instructions="Build the app.", model="tier-balanced")

    def _own(a: Any) -> list[str]:
        return [ti._tool_name(t) for _lbl, pool in ti._own_tool_pools(a) for t in pool]

    assert "run_command" in _own(agent), "the harness holds no run_command to keep"
    ti._inject_agent_tools([agent], tool_scope=cfg.get("tool_scope") or None,
                           agent_name="app-builder", agent_config=cfg)
    assert "run_command" in _own(agent), "D85 took the broker's run_command away"


def test_a_withheld_copilot_agent_drops_its_own_shell_tool() -> None:
    agent = _FakeCopilotAgent()
    agent._tools = [_named("run_script"), _named("my_report")]
    ti._inject_agent_tools(
        [agent], tool_scope=None, agent_name="task-manager",
        agent_config=_config("task-manager"),
    )
    names = _own_names(agent._tools)
    assert "run_script" not in names and "my_report" in names, names


def _real_copilot(shape: str) -> Any:
    """A REAL ``GitHubCopilotAgent``, the class the executor runs, with an
    own ``run_script`` in one pool shape: ``list`` (``tools=``) or ``tuple``
    (``default_options={"tools": (...)}``, which the SDK keeps as a tuple)."""
    from agent_framework_github_copilot import GitHubCopilotAgent

    if shape == "list":
        return GitHubCopilotAgent(
            instructions="x", tools=[_named("run_script"), _named("my_report")],
        )
    return GitHubCopilotAgent(
        instructions="x", tools=[_named("my_report")],
        default_options={"tools": (_named("run_script"),)},
    )


def _session_tool_names(agent: Any) -> list[str]:
    """The tool names that the agent's next Copilot session gets."""
    kwargs = agent._build_session_kwargs(False, {})
    return [getattr(t, "name", None) or ti._tool_name(t) for t in kwargs.get("tools") or []]


class _LegacyCopilotShape:
    """The legacy Copilot shape that the injector also fills: ``_tools`` and
    ``_default_options["tools"]``, with no public ``default_options``."""

    def __init__(self) -> None:
        self.name = "legacy"
        self._tools: list[Any] = [_named("my_report")]
        self._default_options: dict[str, Any] = {"tools": [_named("run_script")]}


@pytest.mark.filterwarnings("ignore::Warning")
@pytest.mark.parametrize("shape", ["list", "tuple"])
@pytest.mark.parametrize("case", ["withheld", "root"])
def test_a_real_copilot_session_holds_no_own_shell_tool(shape: str, case: str) -> None:
    """The verifier's finding: a tuple pool kept ``run_script`` in the
    session. ``_build_session_kwargs`` is what the SDK sends."""
    slug, cfg = ("task-manager", _config("task-manager")) if case == "withheld" else (
        "metorite", None)
    agent = _real_copilot(shape)
    assert "run_script" in _session_tool_names(agent), "the probe holds no run_script"
    ti._inject_agent_tools([agent], tool_scope=None, agent_name=slug, agent_config=cfg)
    names = _session_tool_names(agent)
    assert "my_report" in names, names
    if case == "withheld":
        assert "run_script" not in names, (shape, names)
    else:
        assert "run_script" in names, (shape, names)


@pytest.mark.parametrize("case", ["withheld", "root"])
def test_the_legacy_copilot_pool_loses_its_own_shell_tool(case: str) -> None:
    slug, cfg = ("task-manager", _config("task-manager")) if case == "withheld" else (
        "metorite", None)
    agent = _LegacyCopilotShape()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name=slug, agent_config=cfg)
    names = [ti._tool_name(t) for pool in ti._run_tool_pools(agent) for t in pool]
    assert "my_report" in names, names
    assert ("run_script" in names) is (case == "root"), (case, names)


def test_a_tuple_pool_that_cannot_change_fails_closed() -> None:
    class _Frozen:
        name = "frozen"

        @property
        def tools(self) -> tuple[Any, ...]:
            return (_named("run_script"),)

    with pytest.raises(ti.ToolPoolUnwritable):
        ti._drop_own_shell_tools([_Frozen()], "crm-assistant", SHELL_TOOLS)


@pytest.mark.parametrize("case", ["covered", "root"])
def test_a_run_that_holds_the_shell_tools_keeps_its_own(monkeypatch, case: str) -> None:
    """A run that D85 leaves alone keeps its own tools. The case is the root
    agent and not a personal agent, because task 808 extends D85 to those."""
    cfg: dict[str, Any] | None = _config("crm-assistant")
    slug = "crm-assistant"
    if case == "covered":
        monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("crm-assistant", ORG_A))
        monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    else:
        cfg, slug = None, "metorite"
    assert ti._withheld_shell_tools(slug, cfg) == frozenset()
    agent = _FakeNativeAgent()
    agent.default_options["tools"] = [_named("run_script"), _named("run_command")]
    ti._inject_agent_tools([agent], tool_scope=None, agent_name=slug, agent_config=cfg)
    names = _own_names(agent.default_options["tools"])
    assert {"run_script", "run_command"} <= set(names), (case, names)


def test_the_broker_only_name_is_the_shell_tool_that_is_not_a_host_tool() -> None:
    from acb_skills.sandbox_tools import _HOST_SHELL_TOOLS

    assert ti._BROKER_ONLY_SHELL_TOOLS == SHELL_TOOLS - _HOST_SHELL_TOOLS


def test_list_integrations_names_a_script_tool_only_to_a_holder() -> None:
    import asyncio

    from acb_skills.integration_tools import list_integrations
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    for run_tools, named in ((frozenset(), False), (frozenset({"run_script"}), True)):
        with artifact_context_scope():
            bind_artifact_context(integrations=["zoho-crm"], run_tools=run_tools)
            out = asyncio.run(list_integrations())
        assert ("run_script" in out) is named, (run_tools, out)
        assert "code_task" not in out, out
    doc = list_integrations.__doc__ or ""
    assert "run_script" not in doc and "code_task" not in doc, doc
