"""GET /integrations/skills route tests — WS-23 S1 (skills_registry.md §4).

Route-level tests call the async route function directly (the
``test_app_grants.py`` / ``test_admin_groups.py`` convention: no TestClient,
no live Postgres). Every I/O seam is a module-level function on the SUT
submodule (``gateway.routes.integrations_skills``) and is monkeypatched
there, so the tests stay hermetic. What is locked:

1. The response is the catalog (families + measured token costs + registry
   hash + the WS-12 total) PLUS a per-agent matrix.
2. A scoped agent's row lists exactly the families its resolved scope
   intersects — scope-independent families (core / workflows / apps) always
   included, scope-governed ones only when a declared tool reaches them.
3. An agent with NO tool_scope shows all families (injection is fail-open
   today — spec rule 3), flagged ``all_families``.
4. ``_declared_tool_scope`` reads config.json the way the executor's loader
   does, and returns None for a config without a scope.
5. The router carries the same feature gate as the sibling /integrations
   endpoints (wiring test).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from acb_auth import UserContext, UserRole

import gateway.routes.integrations_skills as sk
from acb_skills import skill_families as sf

USER = UserContext(email="vjvarada@fracktal.in", role=UserRole.EXECUTIVE)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Patch every seam on the SUT submodule; fresh cost cache per test."""
    sf.clear_cost_cache()

    def _mk(name: str):
        def fn(query: str) -> str:
            """Fake tool doc."""
            return ""
        fn.__name__ = name
        return fn

    monkeypatch.setattr(
        sk, "_platform_tools_by_name",
        lambda: {n: _mk(n) for n in sf.all_registered_tool_names()},
    )
    monkeypatch.setattr(sk, "_addendum_for_scope", lambda scope: "a" * 400)
    monkeypatch.setattr(sk, "_count_tokens", len)
    monkeypatch.setattr(
        sk, "_core_tool_names",
        lambda: frozenset(sf.SKILL_FAMILIES["core"]["tools"]),
    )
    # S2: no stored skill settings unless a test injects them.
    monkeypatch.setattr(sk, "_disabled_families_by_agent", lambda: {})
    yield
    sf.clear_cost_cache()


def _agents(monkeypatch, agents: list[dict[str, Any]],
            scopes: dict[str, list[str] | None]) -> None:
    monkeypatch.setattr(sk, "_list_agents", lambda: agents)
    monkeypatch.setattr(
        sk, "_declared_tool_scope",
        lambda name, local_path=None: scopes.get(name),
    )


@pytest.mark.asyncio
async def test_catalog_shape_and_costs(monkeypatch):
    _agents(monkeypatch, [], {})
    out = await sk.skills_catalog(user=USER)
    slugs = [f["slug"] for f in out["families"]]
    assert slugs == list(sf.SKILL_FAMILIES)
    assert out["registry_hash"] == sf.registry_content_hash()
    assert out["total_injected_tokens"] > 0
    by_slug = {f["slug"]: f for f in out["families"]}
    assert by_slug["core"]["token_cost"] > 0
    assert by_slug["core"]["tool_count"] == 20  # 20 with decide (WS-31 CP-13d)
    assert by_slug["apps"]["dynamic"] is True
    assert by_slug["apps"]["token_cost"] == 0
    assert out["agents"] == []


@pytest.mark.asyncio
async def test_matrix_scoped_agent_lists_intersected_families(monkeypatch):
    _agents(
        monkeypatch,
        [{"name": "email-assistant", "description": "mail"}],
        {"email-assistant": ["query_history", "web_search"]},
    )
    out = await sk.skills_catalog(user=USER)
    row = out["agents"][0]
    assert row["name"] == "email-assistant"
    assert row["tool_scope_declared"] is True
    assert row["all_families"] is False
    for always in ("core", "workflows", "apps"):
        assert always in row["families"]
    assert "history" in row["families"]
    assert "memory" not in row["families"]
    assert "coding" not in row["families"]


@pytest.mark.asyncio
async def test_matrix_unscoped_agent_shows_all_families(monkeypatch):
    _agents(monkeypatch, [{"name": "orchestrator"}], {"orchestrator": None})
    out = await sk.skills_catalog(user=USER)
    row = out["agents"][0]
    assert row["all_families"] is True
    assert row["disabled_families"] == []
    assert set(row["families"]) == set(sf.SKILL_FAMILIES)


@pytest.mark.asyncio
async def test_matrix_reflects_stored_skill_settings(monkeypatch):
    """S2: the matrix shows the EFFECTIVE surface — stored settings on top of
    declared scope — including the scope-independent workflows family."""
    _agents(monkeypatch, [{"name": "orchestrator"}], {"orchestrator": None})
    monkeypatch.setattr(
        sk, "_disabled_families_by_agent",
        lambda: {"orchestrator": frozenset({"memory", "workflows"})},
    )
    out = await sk.skills_catalog(user=USER)
    row = out["agents"][0]
    assert row["disabled_families"] == ["memory", "workflows"]
    assert "memory" not in row["families"]
    assert "workflows" not in row["families"]
    assert "core" in row["families"] and "history" in row["families"]
    # An explicit disable means the agent no longer receives everything.
    assert row["all_families"] is False


def test_declared_tool_scope_reads_config_json(tmp_path, monkeypatch):
    # No workspace clone in this test env — only the local_path candidate.
    monkeypatch.setattr(sk, "_log", sk._log)  # no-op; keeps import honest
    (tmp_path / "config.json").write_text(
        json.dumps({"tool_scope": ["query_history", "remember"]}),
        encoding="utf-8",
    )
    assert sk._declared_tool_scope("x", str(tmp_path)) == [
        "query_history", "remember",
    ]


def test_declared_tool_scope_none_when_config_has_no_scope(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"runtime": "maf"}), encoding="utf-8",
    )
    assert sk._declared_tool_scope("x", str(tmp_path)) is None
    assert sk._declared_tool_scope("x", str(tmp_path / "missing")) is None


def test_router_carries_the_integrations_feature_gate():
    # Same gating stance as gateway.routes.integrations: a router-level
    # dependency (require_feature_router("integrations")) — the endpoint must
    # never ship ungated.
    assert sk.router.dependencies, "router lost its feature-gate dependency"
    route = next(r for r in sk.router.routes if r.path == "/integrations/skills")
    assert "GET" in route.methods
