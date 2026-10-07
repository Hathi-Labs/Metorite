"""Each ``own_tool_scope`` equals the tools its agent builds (WS-8o).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.14, board row
WS-8o in ``agent_architecture.md`` §12.2.

``config.json: own_tool_scope`` filters the tools that an agent ships itself.
Until 2026-10-06 the filter did nothing for a native MAF agent, so a stale
scope cost nothing. The email scope named four tools that do not exist and
left out 29 that its instructions teach. A fixed filter with that scope takes
29 tools away from a live agent. This fence keeps the scope and the code as
one fact.

For each agent that declares ``own_tool_scope``, this file builds the agent
with ``build_agents()`` (a dummy ``OPENAI_API_KEY``, no model, no network)
and asserts four things:

1. The scope equals the names of the built tools. No scope removes a built
   tool. A narrowing is an owner decision, and it changes this test.
2. The real filter keeps each built tool of a fresh build.
3. A word in ``instructions.md`` that names a built tool is in the scope.
4. A word in ``instructions.md`` that names an injectable platform tool is
   in the resolved injected scope of the agent. ``run_command`` and
   ``read_file`` are exempt: an instruction names them to say when the agent
   does NOT hold them.

A tool behind a flag (:data:`FLAG_GATED`) is in the scope and is built only
when its flag names the agent. Item 1 then holds twice: with the flag off,
the scope is the built tools plus the gated names, and with the flag on for
every agent, the scope is exactly the built tools. The scope must name the
gated tool, or the real filter takes it away from a run that has the flag on.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from orchestrator import _tool_injection as ti

REPO = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO / "apps" / "agents"

#: An instruction names these to say when the agent does NOT hold them.
_EXEMPT_PLATFORM_WORDS = frozenset({"run_command", "read_file"})

#: Own tools that an agent builds only when a flag names it, and the env
#: that turns each one on for every agent. WS-48 N2: ``narrow_and_read``
#: needs ``NARROWING_AGENTS`` (``data_narrowing_pipeline.md`` §9 N2).
FLAG_GATED: dict[str, tuple[str, str]] = {
    "narrow_and_read": ("NARROWING_AGENTS", "*"),
}


def _scoped_agent_dirs() -> list[Path]:
    dirs = []
    for cfg_path in sorted(AGENTS_DIR.glob("*/config.json")):
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if cfg.get("own_tool_scope"):
            dirs.append(cfg_path.parent)
    return dirs


SCOPED = _scoped_agent_dirs()


def _config(agent_dir: Path) -> dict[str, Any]:
    return json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))


def _build(agent_dir: Path) -> list[Any]:
    """``build_agents()`` of *agent_dir*, loaded by path as the loader does."""
    pytest.importorskip("agent_framework")
    mod_name = "ws8o_" + agent_dir.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, agent_dir / "agents.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_agents()


def _built_names(agents: list[Any]) -> list[str]:
    return [
        ti._tool_name(t)
        for agent in agents
        for _, pool in ti._own_tool_pools(agent)
        for t in pool
    ]


def _instruction_words(agent_dir: Path) -> set[str]:
    text = (agent_dir / "instructions.md").read_text(encoding="utf-8")
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-ws8o-dummy")
    for env, _value in FLAG_GATED.values():
        monkeypatch.delenv(env, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common.settings import get_settings

    for env, value in FLAG_GATED.values():
        monkeypatch.setenv(env, value)
    get_settings.cache_clear()


def test_the_four_live_agents_declare_a_scope() -> None:
    """The fence must not pass by finding no agent."""
    names = {d.name for d in SCOPED}
    assert {
        "agent-crm", "agent-email-assistant", "agent-projects",
        "agent-whatsapp-assistant",
    } <= names, names


@pytest.mark.parametrize("agent_dir", SCOPED, ids=lambda d: d.name)
def test_the_scope_equals_the_built_tools(agent_dir: Path) -> None:
    scope = _config(agent_dir)["own_tool_scope"]
    built = _built_names(_build(agent_dir))
    assert built, f"{agent_dir.name} built no tool"
    assert len(scope) == len(set(scope)), f"{agent_dir.name}: a name repeats"
    assert not set(FLAG_GATED) & set(built), (
        f"{agent_dir.name} built a flag-gated tool with its flag off"
    )
    not_built = sorted(set(scope) - set(built) - set(FLAG_GATED))
    not_scoped = sorted(set(built) - set(scope))
    assert not not_built, (
        f"{agent_dir.name}: own_tool_scope names tools that build_agents() "
        f"does not give: {not_built}"
    )
    assert not not_scoped, (
        f"{agent_dir.name}: own_tool_scope removes built tools: {not_scoped}. "
        "A narrowing is an owner decision (§10.4.14)."
    )


@pytest.mark.parametrize("agent_dir", SCOPED, ids=lambda d: d.name)
def test_with_every_flag_on_the_scope_equals_the_built_tools(
    agent_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gated name in the scope must be a tool that the flag builds, and a
    gated tool that the flag builds must be in the scope (or the filter takes
    it away)."""
    _flags_on(monkeypatch)
    scope = set(_config(agent_dir)["own_tool_scope"])
    agents = _build(agent_dir)
    built = set(_built_names(agents))
    assert scope - built == set(), f"{agent_dir.name}: scoped, not built: {sorted(scope - built)}"
    assert built - scope == set(), f"{agent_dir.name}: built, not scoped: {sorted(built - scope)}"
    before = _built_names(agents)
    ti._apply_own_tool_scope(agents, sorted(scope))
    assert _built_names(agents) == before


@pytest.mark.parametrize("agent_dir", SCOPED, ids=lambda d: d.name)
def test_the_filter_keeps_each_built_tool(agent_dir: Path) -> None:
    agents = _build(agent_dir)
    before = _built_names(agents)
    ti._apply_own_tool_scope(agents, _config(agent_dir)["own_tool_scope"])
    assert _built_names(agents) == before


@pytest.mark.parametrize("agent_dir", SCOPED, ids=lambda d: d.name)
def test_an_own_tool_that_an_instruction_names_is_in_scope(agent_dir: Path) -> None:
    scope = set(_config(agent_dir)["own_tool_scope"])
    built = set(_built_names(_build(agent_dir)))
    missing = sorted((_instruction_words(agent_dir) & built) - scope)
    assert not missing, (
        f"{agent_dir.name}/instructions.md teaches tools outside its "
        f"own_tool_scope: {missing}"
    )


def _injectable_platform_names() -> set[str]:
    names = {ti._tool_name(t) for t in ti._collect_injectable_platform_tools()}
    return names | ti._registered_platform_surface()


@pytest.mark.parametrize("agent_dir", SCOPED, ids=lambda d: d.name)
def test_a_platform_tool_that_an_instruction_names_is_injected(
    agent_dir: Path,
) -> None:
    cfg = _config(agent_dir)
    platform = _injectable_platform_names()
    assert "call_agent" in platform, "no platform tool was collected"
    scope = ti._resolve_injected_scope(
        cfg.get("tool_scope") or None,
        withheld=ti._withheld_shell_tools(cfg.get("name"), cfg),
    )
    named = (_instruction_words(agent_dir) & platform) - _EXEMPT_PLATFORM_WORDS
    missing = sorted(named - scope) if scope is not None else []
    assert not missing, (
        f"{agent_dir.name}/instructions.md names platform tools the agent "
        f"does not get: {missing}"
    )
