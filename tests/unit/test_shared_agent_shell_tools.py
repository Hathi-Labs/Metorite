"""D85 — a shared agent runs no code on the host until the sandbox covers it.

The owner decided on 2026-10-03: until the sandbox is live, block code
execution on the shared server for the Projects agent and the other shared
agents (``work_plan.md`` D85, ``maf_coding_engine.md`` §7.9). Fence WS43-F21.

The seam is ONE function, ``_tool_injection._withheld_shell_tools``. Its
answer leaves the SCOPE (``_resolve_injected_scope(withheld=)``), and three
consumers read that one scope: the injected tool list, the system-prompt
addendum and the skill bodies. A last filter after the no-match fallback makes
sure no branch puts a shell tool back.

What each test pins, and the mutation that turned it red (2026-10-03):

* projects-assistant, crm-assistant and apis-config get no ``SHELL_TOOLS``
  member, and neither does any other shared agent in the registry. Red when
  ``_withheld_shell_tools`` returns ``frozenset()`` for a shared agent.
* email-assistant and whatsapp-assistant keep exactly the shell tools their
  scope resolves to. Red when the ``personal`` early return goes.
* ``covers(agent, org)`` gives the tools back for that agent in that org only.
  Red when the cover branch goes. A run with no org gets no cover. Red when
  the ``org`` guard goes.
* The addendum has no coding section for an agent without the tools, and it
  loses nothing else. Red when ``_resolve_injected_scope`` drops ``withheld``
  while the last filter stays.
* The no-match fallback cannot restore a shell tool. Red when the last filter
  goes.
* Every executor call site passes ``agent_config``. Red when one site drops it.

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
    # The block is narrow: the rest of the floor stays.
    for kept in ("write_artifact", "web_search", "ask_questions", "list_integrations"):
        assert kept in names, f"{slug} lost {kept!r}, which D85 does not touch"


def test_apis_config_loses_the_install_dependency_it_declares() -> None:
    """apis-config names ``install_dependency`` in its own ``tool_scope``."""
    assert "install_dependency" in _config("apis-config")["tool_scope"]
    assert "install_dependency" not in _inject_native("apis-config")


def test_every_shared_agent_in_the_registry_gets_no_shell_tool() -> None:
    shared = [
        slug for slug in SLUG_TO_DIR
        if (_config(slug).get("sharing") or {}).get("instancing", "shared") != "personal"
    ]
    assert {"projects-assistant", "crm-assistant", "apis-config", "orchestrator",
            "task-manager", "app-builder"} <= set(shared)
    for slug in shared:
        names = _inject_native(slug)
        assert not (names & SHELL_TOOLS), f"{slug} still holds {sorted(names & SHELL_TOOLS)}"


@pytest.mark.parametrize("config", [
    None,
    {},
    {"sharing": {"instancing": "shared"}},
    {"sharing": {"instancing": "team", "team": "ops"}},
    {"sharing": {"instancing": ["not", "a", "string"]}},
    {"schema_version": "not-a-number"},
])
def test_anything_but_personal_withholds_every_shell_tool(config: Any) -> None:
    assert ti._withheld_shell_tools("some-agent", config) == SHELL_TOOLS


# ── 2. Personal agents keep today's behaviour ──────────────────────────────

@pytest.mark.parametrize("slug", ["email-assistant", "whatsapp-assistant"])
def test_a_personal_agent_keeps_its_shell_tools(slug: str) -> None:
    cfg = _config(slug)
    assert cfg["sharing"]["instancing"] == "personal"
    names = _inject_native(slug)
    expected = (ti._resolve_injected_scope(cfg.get("tool_scope")) or set()) & SHELL_TOOLS
    assert expected == {"run_script", "code_task"}, "the floor changed; re-read D85"
    assert names & SHELL_TOOLS == expected
    assert ti._withheld_shell_tools(slug, cfg) == frozenset()


# ── 3. covers(agent, org) gives the tools back ─────────────────────────────

def _cover_only(agent: str, org: str):
    return lambda a, o: (a, o) == (agent, org)


def test_a_covered_agent_gets_its_shell_tools_back(monkeypatch) -> None:
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("projects-assistant", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_A)
    names = _inject_native("projects-assistant")
    assert {"run_script", "code_task"} <= names
    # The cover is per agent: crm-assistant in the same org stays blocked.
    assert not (_inject_native("crm-assistant") & SHELL_TOOLS)


def test_a_cover_in_one_org_is_not_a_cover_in_another(monkeypatch) -> None:
    monkeypatch.setattr(ti, "_sandbox_covers", _cover_only("projects-assistant", ORG_A))
    monkeypatch.setattr(executor, "_current_run_org", lambda: ORG_B)
    assert not (_inject_native("projects-assistant") & SHELL_TOOLS)


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


def test_a_personal_copilot_agent_keeps_its_coding_section() -> None:
    """The control for the test above: the same path, a personal config."""
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools(
        [agent], tool_scope=None, agent_name="coach",
        agent_config={"sharing": {"instancing": "personal"}},
    )
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
    ti.materialize_skill_bodies_for_agent(
        "email-assistant", str(tmp_path),
        tool_scope=_config("email-assistant").get("tool_scope"),
        agent_config=_config("email-assistant"),
    )
    assert {"run_script", "code_task"} <= seen["scope"]


# ── 5. No branch puts a shell tool back ────────────────────────────────────

def test_the_no_match_fallback_cannot_restore_a_shell_tool(monkeypatch) -> None:
    """With nothing in scope, injection falls back to the whole chain."""
    async def run_script(path: str, args: str = "") -> str:
        return ""

    async def code_task(task: str) -> str:
        return ""

    monkeypatch.setattr(ti, "_collect_injectable_platform_tools",
                        lambda: [run_script, code_task])
    agent = _FakeNativeAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="crm-assistant",
                           agent_config=_config("crm-assistant"))
    assert not (_native_names(agent) & SHELL_TOOLS)


# ── 6. Every executor call site threads the config ─────────────────────────

def test_every_executor_call_site_passes_the_agent_config() -> None:
    """A site that drops ``agent_config`` reads every agent as shared. That
    fails closed, and it takes the shell tools from a personal agent too."""
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
    assert len(calls["_inject_agent_tools"]) >= 5, calls
    assert len(calls["materialize_skill_bodies_for_agent"]) >= 3, calls
    assert not missing, f"these call sites pass no agent_config: {missing}"
