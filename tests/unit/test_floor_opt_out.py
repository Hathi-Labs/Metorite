"""An agent opts out of floor tools, and never reads its own registry entry.

Spec: ``project-docs/specs/projects_ai_chat.md`` §25 (the fixed prefix of the
Projects and email agents, 2026-10-09).

Every floor tool schema goes into EVERY model request of a run, and a chat
turn makes about six requests. ``config.json: floor_opt_out`` lets an agent
name the floor and workflow tools that it never calls
(``_tool_injection._floor_opt_out``). The registry block lists the agents
that ``call_agent`` reaches. An agent's own entry only invites a hand-off to
itself, so it leaves the block (``_registry_block_for``).

Mutations this file catches (R7):

* the final ``_drop_withheld`` forgets the opted-out names: the workflow
  trio comes back (it is appended after the scope);
* the scope forgets them: the Copilot addendum describes an opted-out tool;
* ``_FLOOR_KEEP`` loses ``ask_questions`` or ``emit_generative_ui``;
* ``_registry_block_for`` returns the whole block: the agent reads itself;
* ``_registry_line`` keeps the whole paragraph of a description;
* the Projects or the email config drops a name, or opts out of a tool
  that the agent uses.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import orchestrator._tool_injection as ti
import pytest

ROOT = Path(__file__).resolve().parents[2]

#: What each agent removed on 2026-10-09, from the 14-day audit of its calls.
#: A change here is a reviewed change of what the agent may call.
PROJECTS_OPTED_OUT = frozenset({
    "call_agent_background", "call_agents_parallel", "get_errors",
    "get_workflow_run", "list_integrations", "list_workflows",
    "load_artifact_kit", "load_design_system", "manage_todo_list",
    "recall_notes", "run_diagnostics", "run_workflow",
})
EMAIL_OPTED_OUT = frozenset({
    "call_agent_background", "call_agents_parallel", "code_task",
    "get_errors", "get_workflow_run", "list_integrations", "list_workflows",
    "load_artifact_kit", "load_design_system", "manage_todo_list",
    "run_diagnostics", "run_script", "run_workflow",
})

#: The platform tools each agent keeps, because it calls them.
PROJECTS_KEEPS = frozenset({
    "ask_questions", "call_agent", "emit_generative_ui", "write_artifact",
    "read_attachment", "remember", "save_memory", "recall_timeline",
    "web_search", "fetch_page", "share_artifact", "save_note",
})
EMAIL_KEEPS = frozenset({
    "ask_questions", "call_agent", "emit_generative_ui", "write_artifact",
    "remember", "save_memory", "recall_timeline", "web_search", "fetch_page",
    "share_artifact", "save_note", "recall_notes",
})


class _FakeMafAgent:
    """A native MAF agent shape: tools and instructions in default_options."""

    def __init__(self, name: str = "fake-agent") -> None:
        self.name = name
        self.default_options: dict[str, Any] = {"tools": [], "instructions": "Base."}


class _FakeCopilotAgent:
    """The Copilot shape: tools in ``_tools``, the addendum in system_message."""

    def __init__(self) -> None:
        self.name = "copilot-agent"
        self._tools: list = []
        self._default_options = {"system_message": {"mode": "append", "content": "Base."}}


def _names(agent: Any) -> set[str]:
    pool = agent.default_options["tools"] if hasattr(agent, "default_options") else agent._tools
    return {ti._tool_name(t) for t in pool}


@pytest.fixture(autouse=True)
def _no_registry_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed registry, so no test reads the gateway's agent table."""
    block = "\n".join([
        "Registered agents:",
        ti._registry_line("projects-assistant", "Projects Assistant — the AI chat. It reads."),
        ti._registry_line("email-assistant", "Email Assistant — the inbox."),
        ti._registry_line("email-assistant-2", "A different agent."),
    ])
    monkeypatch.setattr(ti, "_build_registry_block", lambda: block)
    # No database: the skill toggles and the app grants read pooled tables.
    import orchestrator.app_tools as app_tools
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


# ── The mechanism ──────────────────────────────────────────────────────────

def test_the_opt_out_reads_only_allowed_names() -> None:
    cfg = {"floor_opt_out": [
        "manage_todo_list", "run_workflow",            # allowed
        "ask_questions", "emit_generative_ui",         # kept on purpose
        "remember", "no_such_tool",                     # not floor tools
    ]}
    assert ti._floor_opt_out("a", cfg) == {"manage_todo_list", "run_workflow"}
    assert ti._floor_opt_out("a", {}) == frozenset()
    assert ti._floor_opt_out("a", None) == frozenset()
    assert ti._floor_opt_out("a", {"floor_opt_out": "manage_todo_list"}) == frozenset()


def test_no_agent_may_opt_out_of_asking_the_member() -> None:
    assert {"ask_questions", "emit_generative_ui"} <= ti._FLOOR_KEEP
    assert not ti._FLOOR_KEEP & ti.FLOOR_OPT_OUT_ALLOWED


def test_an_opted_out_tool_is_absent_and_the_rest_stay() -> None:
    before, after = _FakeMafAgent(), _FakeMafAgent()
    ti._inject_agent_tools([before], agent_name="fake-agent", agent_config={})
    ti._inject_agent_tools(
        [after], agent_name="fake-agent",
        agent_config={"floor_opt_out": ["manage_todo_list", "list_workflows"]},
    )
    assert {"manage_todo_list", "list_workflows"} <= _names(before), "the fixture is wrong"
    assert _names(before) - _names(after) == {"manage_todo_list", "list_workflows"}


def test_an_opted_out_workflow_tool_stays_out_on_the_fallback_path() -> None:
    """The trio is appended after the scope, so only the last drop holds it."""
    agent = _FakeMafAgent()
    ti._inject_agent_tools(
        [agent], agent_name="fake-agent", tool_scope=["web_search"],
        agent_config={"floor_opt_out": ["list_workflows", "run_workflow", "get_workflow_run"]},
    )
    assert not {"list_workflows", "run_workflow", "get_workflow_run"} & _names(agent)
    assert "web_search" in _names(agent)


def test_the_addendum_never_describes_an_opted_out_tool() -> None:
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools(
        [agent], agent_name="copilot-agent", tool_scope=["web_search"],
        agent_config={"floor_opt_out": ["manage_todo_list", "load_design_system", "load_artifact_kit"]},
    )
    text = agent._default_options["system_message"]["content"]
    assert "Metorite Platform Tools" in text
    # The section and the MANDATORY line of the tool. Advisory, not fenced:
    # the static risk block names every platform tool, held or not, and the
    # always-on core sections of the Copilot addendum still name
    # load_design_system and load_artifact_kit. Both predate the opt-out, and
    # the two agents that use it are native MAF agents, which never read the
    # Copilot addendum.
    assert "manage_todo_list(todoList)" not in text
    assert "For todo/task tracking" not in text
    assert "manage_todo_list" not in _names(agent)


def test_the_native_output_rule_drops_the_design_pointer_with_the_tool() -> None:
    kept, dropped = _FakeMafAgent(), _FakeMafAgent()
    ti._inject_agent_tools([kept], agent_name="fake-agent", agent_config={})
    ti._inject_agent_tools(
        [dropped], agent_name="fake-agent",
        agent_config={"floor_opt_out": ["load_design_system"]},
    )
    assert "load_design_system" in kept.default_options["instructions"]
    text = dropped.default_options["instructions"]
    assert "load_design_system" not in text
    assert "### Output discipline (REQUIRED)" in text and "outputs/" in text
    # Idempotent without the old marker.
    ti._inject_agent_tools(
        [dropped], agent_name="fake-agent",
        agent_config={"floor_opt_out": ["load_design_system"]},
    )
    assert dropped.default_options["instructions"].count("### Output discipline") == 1


# ── The two agents ─────────────────────────────────────────────────────────

def _config(agent_dir: str) -> dict[str, Any]:
    return json.loads((ROOT / "apps/agents" / agent_dir / "config.json").read_text(encoding="utf-8"))


def _built(agent_dir: str, agent_name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("agent_framework")
    from acb_common.settings import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-opt-out-dummy")
    get_settings.cache_clear()
    path = ROOT / "apps/agents" / agent_dir
    spec = importlib.util.spec_from_file_location("optout_" + agent_dir.replace("-", "_"), path / "agents.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = mod.build_agents()
    cfg = _config(agent_dir)
    ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None,
        agent_name=agent_name, agent_config=cfg,
    )
    get_settings.cache_clear()
    return agents[0]


@pytest.mark.parametrize(
    ("agent_dir", "agent_name", "opted_out", "keeps"),
    [
        ("agent-projects", "projects-assistant", PROJECTS_OPTED_OUT, PROJECTS_KEEPS),
        ("agent-email-assistant", "email-assistant", EMAIL_OPTED_OUT, EMAIL_KEEPS),
    ],
)
def test_each_agent_opts_out_of_what_it_never_calls(
    agent_dir: str, agent_name: str, opted_out: frozenset[str], keeps: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(agent_dir)
    assert frozenset(cfg["floor_opt_out"]) == opted_out
    assert ti._floor_opt_out(agent_name, cfg) == opted_out, "a name is not allowed"
    names = _names(_built(agent_dir, agent_name, monkeypatch))
    assert not names & opted_out, sorted(names & opted_out)
    missing = keeps - names
    # web_search and fetch_page reach outside: a no_egress run drops them, a
    # plain run keeps them.
    assert not missing, sorted(missing)


# ── The registry block ─────────────────────────────────────────────────────

def test_the_registry_block_never_lists_the_agent_itself() -> None:
    block = ti._registry_block_for("email-assistant")
    assert "'email-assistant':" not in block
    assert "'email-assistant-2':" in block, "a longer name is another agent"
    assert "'projects-assistant':" in block
    assert ti._registry_block_for(None) == ti._build_registry_block()


@pytest.mark.parametrize(
    ("agent_dir", "agent_name"),
    [("agent-projects", "projects-assistant"), ("agent-email-assistant", "email-assistant")],
)
def test_a_native_agent_reads_every_agent_but_itself(
    agent_dir: str, agent_name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _built(agent_dir, agent_name, monkeypatch).default_options["instructions"]
    assert "## Delegatable agents (call_agent)" in text
    assert f"'{agent_name}':" not in text
    other = "'email-assistant':" if agent_name == "projects-assistant" else "'projects-assistant':"
    assert other in text


def test_the_copilot_addendum_leaves_out_the_agent_itself() -> None:
    text = ti._build_injected_tools_addendum(
        effective_scope=frozenset({"call_agent"}), agent_name="projects-assistant",
    )
    assert "'projects-assistant':" not in text and "'email-assistant':" in text
    full = ti._build_injected_tools_addendum(effective_scope=frozenset({"call_agent"}))
    assert "'projects-assistant':" in full


def test_a_registry_line_is_one_short_line() -> None:
    long = ("Projects Assistant — the AI chat inside the Projects app. Reads the tree, "
            "a node's summary and " + "much more " * 40 + ".")
    line = ti._registry_line("projects-assistant", long)
    assert "\n" not in line
    assert line == "  - 'projects-assistant': Projects Assistant — the AI chat inside the Projects app."
    one_sentence = "word " * 80
    capped = ti._registry_line("x", one_sentence)
    assert len(capped) <= len("  - 'x': ") + ti._REGISTRY_SUMMARY_MAX + 1
    assert capped.endswith("…")
