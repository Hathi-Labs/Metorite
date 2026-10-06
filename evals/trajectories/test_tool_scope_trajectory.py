"""Golden trajectory: per-agent tool-surface scoping (runtime_agent_effectiveness Item ①).

Locks the invariant that ``tool_scope`` / ``own_tool_scope`` actually SHRINK an
agent's tool surface — not just that the config keys exist. A large tool surface
degrades function-calling accuracy for every model (Berkeley Function-Calling
Leaderboard), so this eval guards two things:

  1. STATIC — the three previously-unscoped agents (email-assistant, apis-config,
     orchestrator) now declare a lean, VALID ``tool_scope`` (every name is a real
     injectable platform tool; a typo would silently fail open to all tools).
     email-assistant additionally declares ``own_tool_scope`` naming only tools
     that its ``build_agents()`` gives (WS-8o: membership in the BUILT tools,
     not an ``async def`` in the source, which a stale name also passes).

  2. BEHAVIORAL — ``_apply_own_tool_scope`` filters an agent's baked ``.tools``
     to the allowlist, and fails OPEN (keeps everything + warns) when no name
     matches — never fails closed to zero tools.

If a future edit drops a scope, adds an invalid tool name, or breaks the
fail-open semantics, this fails CI instead of silently over-injecting tools.

See specs/runtime_agent_effectiveness_2026-07.md (Item ①).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import orchestrator.executor as ex
import pytest

REPO = Path(__file__).resolve().parents[2]

# The 24 platform tools injectable via _inject_agent_tools (executor.py:928-1026).
# A tool_scope name outside this set silently fails open — so we assert against it.
INJECTABLE_PLATFORM_TOOLS = {
    "call_agent", "call_agents_parallel", "call_agent_background",
    "web_search", "fetch_page",
    "write_artifact", "share_artifact", "emit_generative_ui",
    "remember", "recall_timeline", "save_memory", "save_episode",
    "manage_todo_list", "ask_questions",
    "get_errors", "run_diagnostics", "install_dependency",
    "save_note", "recall_notes", "query_history",
    "github_search", "github_repo_search",
}

# The agents this item scopes, with a per-agent leanness ceiling.
SCOPED_AGENTS = {
    "agent-email-assistant": 10,
    "agent-apis-config": 10,
    "agent-orchestrator": 10,
}


def _cfg(agent_dir: str) -> dict:
    return json.loads((REPO / "apps" / "agents" / agent_dir / "config.json").read_text(encoding="utf-8"))


# ── 1. STATIC: the three agents declare lean, valid tool_scope ───────────────

def test_previously_unscoped_agents_now_declare_valid_tool_scope():
    for agent_dir, ceiling in SCOPED_AGENTS.items():
        cfg = _cfg(agent_dir)
        scope = cfg.get("tool_scope")
        assert scope, f"{agent_dir} must declare tool_scope"
        # Every name must be a real injectable platform tool (typo → silent fail-open).
        invalid = [t for t in scope if t not in INJECTABLE_PLATFORM_TOOLS]
        assert not invalid, f"{agent_dir} tool_scope has non-injectable names: {invalid}"
        # Lean: fewer than the full 24-tool catalog.
        assert len(scope) <= ceiling, f"{agent_dir} tool_scope not lean ({len(scope)} > {ceiling})"
        # HITL always survives.
        assert "ask_questions" in scope, f"{agent_dir} must keep ask_questions (HITL)"


def test_email_assistant_own_tool_scope_names_are_all_built_tools(monkeypatch):
    """Each own_tool_scope name is a tool that build_agents() gives (WS-8o).

    An ``async def`` in ``agents.py`` is not enough: a function that the agent
    never registers passes that check and still drops from the scope.
    """
    pytest.importorskip("agent_framework")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ws8o-dummy")
    cfg = _cfg("agent-email-assistant")
    scope = cfg.get("own_tool_scope")
    assert scope, "email-assistant must declare own_tool_scope"
    path = REPO / "apps/agents/agent-email-assistant/agents.py"
    spec = importlib.util.spec_from_file_location("ws8o_traj_email", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    built = {
        ex._tool_name(t)
        for agent in mod.build_agents()
        for t in (getattr(agent, "default_options", None) or {}).get("tools") or []
    }
    assert built, "email-assistant built no tool"
    missing = [t for t in scope if t not in built]
    assert not missing, f"own_tool_scope names tools build_agents() does not give: {missing}"
    # The core read and send actions are built and in scope. (send_email sends
    # both new mail and replies, through reply_to_email_id.)
    for essential in ("read_email", "draft_reply", "send_email"):
        assert essential in built, f"email-assistant no longer builds {essential}"
        assert essential in scope, f"email-assistant own_tool_scope dropped {essential}"


# ── 2. BEHAVIORAL: _apply_own_tool_scope filters, and fails open ─────────────

def _named_tool(name: str):
    """A plain callable whose __name__ is `name` (what _tool_name reads)."""
    def _tool():
        return None
    _tool.__name__ = name
    return _tool


def _fake_agent(tool_names: list[str]) -> SimpleNamespace:
    """An agent whose .tools are plain callables named per tool_names."""
    return SimpleNamespace(name="fake", tools=[_named_tool(n) for n in tool_names])


def test_own_tool_scope_filters_baked_tools_to_allowlist():
    agent = _fake_agent(["read_email", "send_reply", "resync_account", "delete_rule"])
    ex._apply_own_tool_scope([agent], ["read_email", "send_reply"])
    kept = [ex._tool_name(t) for t in agent.tools]
    assert kept == ["read_email", "send_reply"], kept


def test_own_tool_scope_fails_open_on_no_match():
    """A fully-wrong scope keeps ALL tools (never fails closed to zero)."""
    agent = _fake_agent(["read_email", "send_reply"])
    ex._apply_own_tool_scope([agent], ["nonexistent_tool_xyz"])
    kept = [ex._tool_name(t) for t in agent.tools]
    assert kept == ["read_email", "send_reply"], f"should fail OPEN, got {kept}"


def test_own_tool_scope_noop_when_unset():
    agent = _fake_agent(["read_email", "send_reply"])
    ex._apply_own_tool_scope([agent], None)
    assert [ex._tool_name(t) for t in agent.tools] == ["read_email", "send_reply"]
