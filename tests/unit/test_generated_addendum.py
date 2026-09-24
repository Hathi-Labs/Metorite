"""WS-23 S3 — generated addendum + prepared (OFF) fail-closed default profile.

Three claims, per specs/skills_registry.md §4 S3:

1. **The addendum is generated from the section registries.** The prose in
   ``acb_skills.addendum`` is ordered, family-tagged data; every section's
   gate tools must belong to its declared family (drift gate — a family's
   prose can never describe another family's tools), and the wrapper both
   runtimes call (``_build_injected_tools_addendum``) plus the catalog's
   cost-measurement seam are the SAME code path.
2. **A scoped agent's addendum contains no disabled-family section** —
   asserted END TO END through ``_inject_agent_tools`` on a Copilot-shaped
   agent: the system message that lands in the run context is checked, not
   just the builder.
3. **The fail-closed default profile is prepared, not flipped.**
   ``DEFAULT_PROFILE`` is sane data; ``SKILLS_FAIL_CLOSED`` ships OFF (today's
   ``None`` fail-open sentinel — rule 3); when ON, unscoped agents resolve to
   core ∪ DEFAULT_PROFILE instead of everything, scoped agents unchanged.
   The flip itself is OWNER-GATED (work_plan.md §6) and never exercised
   outside tests.

Same conventions as ``test_skill_toggle_enforcement.py``: import the SUT
modules directly, no live DB, loaders monkeypatched on the module.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_skills import addendum as ad
from acb_skills import skill_families as sf

from tests.unit._decide_flag import (
    decide_tool_on,  # noqa: F401 — module-scoped autouse, the flag ON
)

MEMORY_TOOLS = set(sf.SKILL_FAMILIES["memory"]["tools"])


# ── 1. The section registries source from SKILL_FAMILIES (drift gate) ──────

def test_section_gate_tools_belong_to_their_declared_family() -> None:
    for registry_name, registry in (
        ("FULL_SECTIONS", ad.FULL_SECTIONS),
        ("COMPACT_SECTIONS", ad.COMPACT_SECTIONS),
        ("MANDATORY_LINES", ad.MANDATORY_LINES),
    ):
        for entry in registry:
            assert entry.family in sf.SKILL_FAMILIES, (
                f"{registry_name}: unknown family {entry.family!r}"
            )
            fam_tools = set(sf.SKILL_FAMILIES[entry.family]["tools"])
            for tool in entry.gate:
                assert tool in fam_tools, (
                    f"{registry_name}: section tagged {entry.family!r} is "
                    f"gated on {tool!r}, which that family does not own — "
                    f"prose and registry have drifted"
                )


def test_every_narrowable_family_with_prose_keeps_it() -> None:
    """memory / history / coding each own addendum prose in both variants;
    'workflows' deliberately has NONE (the trio has never been described in
    the addendum — S3 preserved content verbatim; adding prose is a separate
    content decision flagged in the spec)."""
    owned_full = {s.family for s in ad.FULL_SECTIONS}
    owned_compact = {s.family for s in ad.COMPACT_SECTIONS}
    for fam in ("memory", "history", "coding"):
        assert fam in owned_full, f"{fam} lost its full-addendum section"
        assert fam in owned_compact, f"{fam} lost its compact section"
    assert "workflows" not in owned_full | owned_compact, (
        "a workflows section appeared — deliberate content change; update "
        "the spec's decision note and this pin together"
    )


def test_wrapper_delegates_to_the_renderer() -> None:
    """Injection's cached wrapper == the renderer, byte for byte — one text
    source (the S1 catalog wires the same wrapper, so measured cost = real
    cost by construction)."""
    ti._build_injected_tools_addendum.cache_clear()
    for is_sub in (False, True):
        for scope in (None, frozenset(ti._CORE_STANDARD_TOOL_NAMES)):
            assert ti._build_injected_tools_addendum(
                is_sub_agent=is_sub, effective_scope=scope,
            ) == ad.render_injected_tools_addendum(
                is_sub_agent=is_sub, effective_scope=scope,
                registry_block=ti._build_registry_block(),
            )


def test_catalog_measurement_uses_the_injection_wrapper() -> None:
    """The gateway seam the catalog measures with IS the injection builder."""
    from gateway.routes import integrations_skills as gis
    ti._build_injected_tools_addendum.cache_clear()
    scope = frozenset(ti._CORE_STANDARD_TOOL_NAMES)
    assert gis._addendum_for_scope(scope) == ti._build_injected_tools_addendum(
        effective_scope=scope
    )


# ── 2. No disabled-family section, end to end through injection ────────────

class _FakeCopilotAgent:
    """Matches the GitHubCopilotAgent shape: ``_tools`` list + a
    ``_default_options`` dict whose system_message receives the addendum."""

    def __init__(self) -> None:
        self.name = "fake-copilot"
        self._tools: list[Any] = []
        self._default_options: dict[str, Any] = {}


@pytest.fixture()
def _injection_env(monkeypatch):
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    fake_apps = types.ModuleType("orchestrator.app_tools")
    fake_apps.load_app_action_tools = lambda name: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.app_tools", fake_apps)
    fake_wf = types.ModuleType("orchestrator.workflow_tools")
    fake_wf.load_workflow_tools = lambda name: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.workflow_tools", fake_wf)


def _addendum_of(agent: _FakeCopilotAgent) -> str:
    msg = agent._default_options.get("system_message")
    assert isinstance(msg, dict), "addendum never reached the system message"
    return msg.get("content") or ""


def test_disabled_family_section_absent_from_run_system_message(
    monkeypatch, _injection_env
) -> None:
    """The S3 acceptance, end to end: disable 'memory' and the addendum that
    lands in the agent's system message (what the run context stores) has no
    memory section AND no memory tool among the injected set."""
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families",
        lambda name: frozenset({"memory"}),
    )
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-copilot")
    text = _addendum_of(agent)
    assert "### Memory & knowledge graph" not in text
    assert "recall_org" not in text and "save_agent_memory" not in text
    injected = {
        getattr(getattr(t, "func", t), "__name__", "") for t in agent._tools
    }
    assert not (injected & MEMORY_TOOLS)
    # Core-floor prose survives whatever is disabled (rule 2).
    for present in ("### Web access", "### Workspace & file writing",
                    "### Inter-agent delegation"):
        assert present in text


def test_enabled_families_keep_their_sections_end_to_end(
    monkeypatch, _injection_env
) -> None:
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families", lambda name: frozenset()
    )
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-copilot")
    text = _addendum_of(agent)
    for section in ("### Memory & knowledge graph", "### Conversation history",
                    "### GitHub code search", "### Runtime dependencies"):
        assert section in text


# ── 3. DEFAULT_PROFILE data sanity + the OFF/ON switch ─────────────────────

def test_default_profile_families_all_exist() -> None:
    assert set(sf.DEFAULT_PROFILE) <= set(sf.SKILL_FAMILIES)
    assert "core" in sf.DEFAULT_PROFILE
    assert "memory" in sf.DEFAULT_PROFILE  # the evidence-based general family
    # The specialised families stay out — per-agent scope/toggle only.
    assert "history" not in sf.DEFAULT_PROFILE
    assert "coding" not in sf.DEFAULT_PROFILE


def test_default_profile_tools_is_the_union_of_its_families() -> None:
    expected: set[str] = set()
    for slug in sf.DEFAULT_PROFILE:
        expected |= set(sf.SKILL_FAMILIES[slug]["tools"])
    assert sf.default_profile_tools() == frozenset(expected)
    assert MEMORY_TOOLS <= sf.default_profile_tools()


def test_fail_closed_ships_off(monkeypatch) -> None:
    """No env var ⇒ OFF ⇒ today's fail-open None sentinel (rule 3 — proven
    alongside the S2 byte-identical regression, which runs in this state)."""
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    assert ti._skills_fail_closed() is False
    assert ti._resolve_injected_scope(None) is None
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "0")
    assert ti._skills_fail_closed() is False
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "false")
    assert ti._skills_fail_closed() is False


def test_fail_closed_on_resolves_unscoped_to_default_profile(monkeypatch) -> None:
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "1")
    resolved = ti._resolve_injected_scope(None)
    assert resolved == (
        set(ti._CORE_STANDARD_TOOL_NAMES) | set(sf.default_profile_tools())
    )
    assert "remember" in resolved                # memory rides the profile
    assert "query_history" not in resolved       # history is specialised
    assert "install_dependency" not in resolved  # coding extras specialised
    assert "github_search" not in resolved
    assert ti._CORE_STANDARD_TOOL_NAMES <= resolved


def test_fail_closed_never_touches_scoped_agents(monkeypatch) -> None:
    scope = ["query_history", "install_dependency"]
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    off = ti._resolve_injected_scope(scope)
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "1")
    on = ti._resolve_injected_scope(scope)
    assert off == on, "a declared tool_scope must resolve identically in both positions"


def test_fail_closed_still_intersects_admin_disables(monkeypatch) -> None:
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "1")
    resolved = ti._resolve_injected_scope(
        None, disabled_families=frozenset({"memory"})
    )
    assert resolved is not None
    assert not (resolved & MEMORY_TOOLS)
    assert ti._CORE_STANDARD_TOOL_NAMES <= resolved


def test_fail_closed_on_injection_narrows_unscoped_agent(
    monkeypatch, _injection_env
) -> None:
    monkeypatch.setenv("SKILLS_FAIL_CLOSED", "1")
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families", lambda name: frozenset()
    )
    agent = _FakeCopilotAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-copilot")
    injected = {
        getattr(getattr(t, "func", t), "__name__", "") for t in agent._tools
    }
    assert ti._CORE_STANDARD_TOOL_NAMES <= injected
    assert MEMORY_TOOLS <= injected
    for specialised in ("query_history", "install_dependency",
                        "github_search", "github_repo_search"):
        assert specialised not in injected
    # And the generated addendum matches the narrowed surface.
    text = _addendum_of(agent)
    assert "### Memory & knowledge graph" in text
    assert "### Conversation history" not in text
    assert "### GitHub code search" not in text
    assert "### Runtime dependencies" not in text
