"""WS-23 S2 enforcement — skill toggles intersect the injected scope.

specs/skills_registry.md §2, the three rules, asserted at the ONE seam both
runtimes pass through (``_resolve_injected_scope`` + ``_inject_agent_tools``):

1. **Intersection, never union.** Disabling a family removes its tools from
   the injected set; a toggle can only narrow within what the agent declares.
2. **The core floor survives anything** — including a (refused-by-API, but
   defensively ignored) attempt to disable ``core`` itself.
3. **No rows ⇒ byte-identical to today.** The load-bearing regression: with
   no settings the resolved scope AND the actually-injected tool list are
   exactly what ``_collect_injectable_platform_tools()`` has always produced
   (fail-open stays; the flip is S3, owner-gated).

Same conventions as ``test_core_tool_floor.py`` / ``test_skills_registry.py``:
import the injection module directly, no live DB — the settings loader is
monkeypatched on the SUT module exactly where ``_inject_agent_tools`` resolves
it once at run start.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import orchestrator._tool_injection as ti
from acb_skills import skill_families as sf

MEMORY_TOOLS = set(sf.SKILL_FAMILIES["memory"]["tools"])
ALL_STATIC_FAMILY_TOOLS = {
    n for fam in sf.SKILL_FAMILIES.values() for n in fam["tools"]
}


# ── _resolve_injected_scope: the intersection rule ─────────────────────────

def test_no_disabled_families_is_byte_identical_to_today() -> None:
    """Rule 3 at the resolve level: empty/None settings change NOTHING."""
    for disabled in (None, frozenset()):
        assert ti._resolve_injected_scope(
            None, disabled_families=disabled
        ) is None, "unscoped agents must keep the None fail-open sentinel"
        assert ti._resolve_injected_scope(
            ["query_history"], disabled_families=disabled
        ) == ti._resolve_injected_scope(["query_history"])


def test_disabling_a_family_removes_its_tools_from_a_scoped_agent() -> None:
    scope = ["query_history", "remember", "save_memory"]
    resolved = ti._resolve_injected_scope(
        scope, disabled_families=frozenset({"memory"})
    )
    assert resolved is not None
    assert not (resolved & MEMORY_TOOLS), "disabled family tools must go"
    assert "query_history" in resolved  # untouched declared specialist stays
    assert ti._CORE_STANDARD_TOOL_NAMES <= resolved  # rule 2


def test_disabling_narrows_an_unscoped_agent_too() -> None:
    resolved = ti._resolve_injected_scope(
        None, disabled_families=frozenset({"memory"})
    )
    assert resolved is not None, "an explicit disable must materialise a set"
    assert not (resolved & MEMORY_TOOLS)
    # Everything else the platform statically offers is still there.
    assert resolved == (
        (ALL_STATIC_FAMILY_TOOLS | set(ti._CORE_STANDARD_TOOL_NAMES))
        - MEMORY_TOOLS
    )


def test_core_family_cannot_be_disabled() -> None:
    """Rule 2, defense in depth: the API 422s 'core', and even a hand-written
    row is ignored here — the result is byte-identical to no rows at all."""
    assert ti._resolve_injected_scope(
        None, disabled_families=frozenset({"core"})
    ) is None
    assert ti._resolve_injected_scope(
        ["query_history"], disabled_families=frozenset({"core"})
    ) == ti._resolve_injected_scope(["query_history"])


def test_dynamic_and_unknown_families_cannot_narrow() -> None:
    # 'apps' has no static tools (grants are managed elsewhere — the S2
    # decision note); unknown slugs are API-refused and ignored here.
    for disabled in (frozenset({"apps"}), frozenset({"no-such-family"})):
        assert ti._resolve_injected_scope(
            None, disabled_families=disabled
        ) is None
        assert ti._resolve_injected_scope(
            ["web_search"], disabled_families=disabled
        ) == ti._resolve_injected_scope(["web_search"])


def test_a_toggle_can_only_narrow_never_widen() -> None:
    """Rule 1: whatever is disabled, the result is a subset of the no-rows
    resolution for the same declared scope."""
    baseline = ti._resolve_injected_scope(["query_history", "remember"])
    assert baseline is not None
    for disabled in ({"memory"}, {"history"}, {"memory", "history", "coding"}):
        resolved = ti._resolve_injected_scope(
            ["query_history", "remember"],
            disabled_families=frozenset(disabled),
        )
        assert resolved is not None
        assert resolved <= baseline
        assert ti._CORE_STANDARD_TOOL_NAMES <= resolved


# ── The settings loader fails open (rule 3's operational half) ─────────────

def test_loader_returns_empty_on_any_failure(monkeypatch) -> None:
    # No agent name → nothing to look up.
    assert ti._load_disabled_skill_families(None) == frozenset()
    assert ti._load_disabled_skill_families("") == frozenset()
    # DB layer broken (import or connect) → empty set, never an exception.
    broken = types.ModuleType("acb_graph")
    monkeypatch.setitem(sys.modules, "acb_graph", broken)  # no get_session
    assert ti._load_disabled_skill_families("email-assistant") == frozenset()


def test_loader_reads_disabled_rows(monkeypatch) -> None:
    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _stmt, params=None):
            assert params == {"name": "email-assistant"}

            class _R:
                @staticmethod
                def fetchall():
                    return [("memory",), ("workflows",)]
            return _R()

    fake = types.ModuleType("acb_graph")
    fake.get_session = lambda: _Session()  # type: ignore[attr-defined]
    # WS-29 acb_graph slice 7: the loader now picks its opener through
    # executor._graph_session_opener_current, which consults the tenant-bind flag
    # first. Flag OFF (default here) ⇒ the opener IS this fake's get_session, so
    # the read is byte-identical to pre-slice — the fake just needs to expose the
    # flag reader the real acb_graph module carries.
    fake.tenant_bind_enabled = lambda: False  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "acb_graph", fake)
    assert ti._load_disabled_skill_families("email-assistant") == frozenset(
        {"memory", "workflows"}
    )


# ── Full injection path (fake agents, real collection chain) ───────────────

class _FakeMafAgent:
    """Matches the 'MAF Agent' shape: a plain ``tools`` list attribute."""

    def __init__(self) -> None:
        self.name = "fake-maf"
        self.tools: list[Any] = []
        self.instructions = ""


def _trio() -> list[Any]:
    def list_workflows() -> str:
        """Fake."""
        return ""

    def run_workflow(slug: str) -> str:
        """Fake."""
        return ""

    def get_workflow_run(run_id: str) -> str:
        """Fake."""
        return ""
    return [list_workflows, run_workflow, get_workflow_run]


@pytest.fixture()
def _injection_env(monkeypatch):
    """Deterministic injection: no permission-gate wrapping (identity-level
    comparison), no app grants, a fake workflow trio, and a patchable
    settings loader."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")
    fake_apps = types.ModuleType("orchestrator.app_tools")
    fake_apps.load_app_action_tools = lambda name: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.app_tools", fake_apps)
    trio = _trio()
    fake_wf = types.ModuleType("orchestrator.workflow_tools")
    fake_wf.load_workflow_tools = lambda name: list(trio)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "orchestrator.workflow_tools", fake_wf)
    return trio


def _injected_names(agent: _FakeMafAgent) -> list[str]:
    return [getattr(t, "__name__", "") for t in agent.tools]


def test_regression_no_rows_injects_byte_identical_set(
    monkeypatch, _injection_env
) -> None:
    """THE load-bearing S2 guarantee: an agent without settings rows receives
    EXACTLY the tool objects it received before S2 — the static collection
    chain plus the per-agent trio, in order, no wrapping differences."""
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families", lambda name: frozenset()
    )
    agent = _FakeMafAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-maf")
    expected = ti._collect_injectable_platform_tools() + _injection_env
    assert [id(t) for t in agent.tools] == [id(fn) for fn in expected], (
        "with no settings rows the injected set must be byte-identical to "
        "the pre-S2 collection chain (skills_registry.md rule 3)"
    )
    assert _injected_names(agent) == [fn.__name__ for fn in expected]


def test_disabling_memory_removes_its_tools_from_injection(
    monkeypatch, _injection_env
) -> None:
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families",
        lambda name: frozenset({"memory"}),
    )
    agent = _FakeMafAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-maf")
    names = set(_injected_names(agent))
    assert not (names & MEMORY_TOOLS)
    assert ti._CORE_STANDARD_TOOL_NAMES <= names  # core survives (rule 2)
    assert "query_history" in names  # other families untouched
    assert "list_workflows" in names  # workflows family untouched


def test_disabling_workflows_removes_the_trio(
    monkeypatch, _injection_env
) -> None:
    """The S2 decision note: the scope-independent 'workflows' family honors
    its toggle at the append site — an explicit disable is narrowing."""
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families",
        lambda name: frozenset({"workflows"}),
    )
    agent = _FakeMafAgent()
    ti._inject_agent_tools([agent], tool_scope=None, agent_name="fake-maf")
    names = set(_injected_names(agent))
    assert not (
        names & {"list_workflows", "run_workflow", "get_workflow_run"}
    )
    # Everything else is byte-identical to the static chain.
    assert names == {
        fn.__name__ for fn in ti._collect_injectable_platform_tools()
    }


def test_scoped_agent_with_disable_keeps_core_and_declared(
    monkeypatch, _injection_env
) -> None:
    monkeypatch.setattr(
        ti, "_load_disabled_skill_families",
        lambda name: frozenset({"memory", "coding"}),
    )
    agent = _FakeMafAgent()
    ti._inject_agent_tools(
        [agent],
        tool_scope=["query_history", "remember", "install_dependency"],
        agent_name="fake-maf",
    )
    names = set(_injected_names(agent))
    assert "query_history" in names          # declared + enabled family
    assert not (names & MEMORY_TOOLS)        # declared but disabled
    assert "install_dependency" not in names  # declared but disabled
    assert ti._CORE_STANDARD_TOOL_NAMES <= names


# ── Addendum: disabled-family sections vanish (S3 shipped: prose is now
#    GENERATED from acb_skills.addendum's family-tagged section registries;
#    the end-to-end system-message assertion lives in
#    test_generated_addendum.py) ──────────────────────────────────────────

def test_addendum_drops_disabled_family_section() -> None:
    resolved = ti._resolve_injected_scope(
        None, disabled_families=frozenset({"memory"})
    )
    assert resolved is not None
    with_memory = ti._build_injected_tools_addendum(effective_scope=None)
    without_memory = ti._build_injected_tools_addendum(
        effective_scope=frozenset(resolved)
    )
    assert "### Memory & knowledge graph" in with_memory
    assert "### Memory & knowledge graph" not in without_memory
    # Core-floor sections stay whatever is disabled.
    assert "### Web access" in without_memory
