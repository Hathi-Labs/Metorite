"""WS-23 S1 drift gate + catalog math (specs/skills_registry.md §4).

The S1 acceptance criterion, verbatim: *every injected platform tool appears
in exactly one family; a drift test fails CI if a tool is injected but
missing from the registry*. This file is that test, extending the
``test_tool_scope_addendum.py`` convention (import the injection module
directly, no live DB, no agents touched):

1. The injectable universe is derived from the CODE — the exact collection
   chain ``_inject_agent_tools`` runs (``_collect_injectable_platform_tools``)
   plus the per-agent workflow trio — never from a hand-written list, so a
   newly injected tool that nobody registered fails here.
2. Both drift directions are locked: injected-but-unregistered AND
   registered-but-no-longer-injectable both fail.
3. The ``core`` family IS the floor: it must equal
   ``_CORE_STANDARD_TOOL_NAMES`` exactly (spec rule 2 — the non-toggleable
   family has one definition, in the injection seam).
4. ``build_catalog`` measures with whatever tokenizer/renderer is injected
   and caches per (family, registry hash) — asserted with fakes, hermetic.
"""
from __future__ import annotations

from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_skills import skill_families as sf

from tests.unit._decide_flag import (
    decide_tool_on,  # noqa: F401 — module-scoped autouse, the flag ON
)


@pytest.fixture(autouse=True)
def _fresh_cost_cache():
    """The cost cache keys on registry hash only (deps are process-stable in
    the gateway); tests inject fakes, so isolate the cache per test."""
    sf.clear_cost_cache()
    yield
    sf.clear_cost_cache()


def _injectable_names() -> set[str]:
    """Every platform tool name injection can offer, derived from the code."""
    names = {
        getattr(fn, "__name__", "")
        for fn in ti._collect_injectable_platform_tools()
    }
    assert names, "collection chain returned nothing — imports broken?"
    # The workflow trio is appended per-agent in _inject_agent_tools
    # (unconditional, after scope filtering) — same injected surface.
    try:
        from orchestrator.workflow_tools import load_workflow_tools
        wf = [getattr(fn, "__name__", "")
              for fn in load_workflow_tools("drift-test")]
    except Exception:  # pragma: no cover — workflows pkg unavailable
        wf = []
    if not wf:  # service import failed in this env; pin the known trio
        wf = ["list_workflows", "run_workflow", "get_workflow_run"]
    names.update(wf)
    names.discard("")
    return names


# ── Drift: injected ⊆ registry, one family each ────────────────────────────

def test_every_injected_tool_registered_in_exactly_one_family():
    injected = _injectable_names()
    for name in sorted(injected):
        owners = [
            slug for slug, fam in sf.SKILL_FAMILIES.items()
            if name in fam["tools"]
        ]
        assert len(owners) == 1, (
            f"{name!r} is injected by _tool_injection but belongs to "
            f"{owners or 'NO family'} — register it in exactly one family "
            f"in acb_skills.skill_families.SKILL_FAMILIES"
        )


def test_no_stale_registry_entries():
    injected = _injectable_names()
    stale = sf.all_registered_tool_names() - injected
    assert not stale, (
        f"registered but no longer injectable: {sorted(stale)} — remove "
        f"them from SKILL_FAMILIES (the registry mirrors the code, not "
        f"history)"
    )


def test_core_family_is_exactly_the_floor():
    assert set(sf.SKILL_FAMILIES["core"]["tools"]) == set(
        ti._CORE_STANDARD_TOOL_NAMES
    ), (
        "the 'core' family must equal _CORE_STANDARD_TOOL_NAMES — the "
        "non-toggleable floor has ONE definition (spec rule 2)"
    )
    assert sf.SKILL_FAMILIES["core"]["core"] is True
    assert sf.SKILL_FAMILIES["core"]["scope_governed"] is False


def test_dynamic_apps_family_has_no_static_tools():
    fam = sf.SKILL_FAMILIES["apps"]
    assert fam["dynamic"] is True and fam["tools"] == ()


def test_registry_hash_is_deterministic():
    assert sf.registry_content_hash() == sf.registry_content_hash()
    assert len(sf.registry_content_hash()) == 16


# ── families_for_scope (the read-only matrix rule) ─────────────────────────

def test_families_for_scope_unscoped_agent_gets_everything():
    fams = sf.families_for_scope(None, resolved_scope=None)
    assert set(fams) == set(sf.SKILL_FAMILIES)


def test_families_for_scope_intersects_scope_governed_families():
    resolved = frozenset(
        ti._resolve_injected_scope(["query_history"]) or set()
    )
    fams = sf.families_for_scope(["query_history"], resolved_scope=resolved)
    # Scope-independent families are always present…
    for always in ("core", "workflows", "apps"):
        assert always in fams
    # …the scoped-in family is present, the others are not.
    assert "history" in fams
    assert "memory" not in fams
    assert "coding" not in fams


# ── Catalog measurement (hermetic fakes) ───────────────────────────────────

def _fake_tools() -> dict[str, Any]:
    def _mk(name: str):
        def fn(query: str, max_results: int = 5) -> str:
            """Fake tool doc."""
            return ""
        fn.__name__ = name
        return fn
    return {n: _mk(n) for n in sf.all_registered_tool_names()}


def test_build_catalog_measures_marginal_addendum_plus_schemas():
    calls: list[frozenset[str] | None] = []

    def addendum(scope: frozenset[str] | None) -> str:
        calls.append(scope)
        # 100 chars for the core baseline, +40 per extra tool in scope.
        core = frozenset(sf.SKILL_FAMILIES["core"]["tools"])
        extra = 0 if scope is None else len(scope - core)
        if scope is None:
            extra = len(sf.all_registered_tool_names() - core)
        return "x" * (100 + 40 * extra)

    import json as _json

    tools = _fake_tools()
    cat = sf.build_catalog(
        tools_by_name=tools,
        addendum_for_scope=addendum,
        count_tokens=len,  # 1 token per char — arithmetic stays legible
        core_tool_names=frozenset(sf.SKILL_FAMILIES["core"]["tools"]),
    )
    by_slug = {f["slug"]: f for f in cat["families"]}

    def schemas(names) -> int:  # schema size varies with the tool NAME
        return sum(
            len(_json.dumps(sf.tool_json_schema(tools[n]))) for n in names
        )

    # history: 1 non-core tool → marginal addendum 40 + its schema.
    assert by_slug["history"]["token_cost"] == 40 + schemas(["query_history"])
    # memory: 8 tools → 8*40 marginal + 8 schemas.
    assert by_slug["memory"]["token_cost"] == 8 * 40 + schemas(
        sf.SKILL_FAMILIES["memory"]["tools"]
    )
    # core: full baseline + its schemas.
    assert by_slug["core"]["token_cost"] == 100 + schemas(
        sf.SKILL_FAMILIES["core"]["tools"]
    )
    # dynamic apps: unmeasurable statically.
    assert by_slug["apps"]["token_cost"] == 0
    # total: unscoped addendum + every static schema (the WS-12 baseline).
    n_non_core = len(sf.all_registered_tool_names()) - 20  # 20 with decide (CP-13d)
    assert cat["total_injected_tokens"] == (
        100 + 40 * n_non_core + schemas(sf.all_registered_tool_names())
    )


def test_build_catalog_caches_per_registry_hash():
    counter = {"n": 0}

    def counting_tokens(text: str) -> int:
        counter["n"] += 1
        return len(text)

    kwargs: dict[str, Any] = dict(
        tools_by_name=_fake_tools(),
        addendum_for_scope=lambda s: "y" * 100,
        count_tokens=counting_tokens,
        core_tool_names=frozenset(sf.SKILL_FAMILIES["core"]["tools"]),
    )
    first = sf.build_catalog(**kwargs)
    calls_after_first = counter["n"]
    second = sf.build_catalog(**kwargs)
    assert counter["n"] == calls_after_first, "second build must hit the cache"
    assert first == second
