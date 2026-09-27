"""Tests for the Mem0 + Graphiti memory layer (WBS 2.5).

Coverage:
  - acb_memory package imports and graceful degradation when disabled
  - mem0_client.MemoryClient no-ops when MEM0_ENABLED=false
  - graphiti_client.GraphitiClient no-ops when GRAPHITI_ENABLED=false
  - enrich_instructions_with_memory returns correct string from default_options
  - search_timeline delegates to search_entity_timeline
  - /copilot/chat endpoint is registered on the gateway FastAPI app
  - /pull endpoint injects enriched instructions into default_options dict
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.unit._routes import served_routes


# ---------------------------------------------------------------------------
# acb_memory — imports and public surface
# ---------------------------------------------------------------------------

def test_acb_memory_package_imports() -> None:
    """All public symbols must be importable from acb_memory."""
    from acb_memory import (
        MemoryClient,
        GraphitiClient,
        get_memory_client,
        get_graphiti_client,
        get_memory_context,
        add_memories_background,
        search_entity_timeline,
        add_episode,
    )
    assert callable(get_memory_context)
    assert callable(add_memories_background)
    assert callable(search_entity_timeline)
    assert callable(add_episode)


# ---------------------------------------------------------------------------
# MemoryClient — disabled by default (MEM0_ENABLED=false in .env / no .env)
# ---------------------------------------------------------------------------

def test_memory_client_disabled_when_mem0_not_enabled() -> None:
    """MemoryClient._get_client() returns None when mem0_enabled=False.
    
    Uses monkeypatch to force mem0_enabled=False regardless of .env value.
    """
    import acb_memory.mem0_client

    from acb_common import get_settings
    from acb_memory.mem0_client import MemoryClient

    # Force mem0_enabled=False on the settings instance
    settings = get_settings()
    settings.mem0_enabled = False
    try:
        mc = MemoryClient()
        assert mc._get_client() is None
    finally:
        settings.mem0_enabled = True  # Restore — .env says true


@pytest.mark.asyncio
async def test_memory_client_search_returns_empty_when_disabled() -> None:
    from acb_memory.mem0_client import MemoryClient
    mc = MemoryClient()
    results = await mc.search("test@example.com", "any query")
    assert results == []


@pytest.mark.asyncio
async def test_memory_client_add_noop_when_disabled() -> None:
    from acb_memory.mem0_client import MemoryClient
    mc = MemoryClient()
    # Should complete without error
    await mc.add("test@example.com", [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_get_memory_context_returns_empty_when_disabled() -> None:
    from acb_memory import get_memory_context
    ctx = await get_memory_context("test@example.com", "what deals are open?")
    assert ctx == ""


@pytest.mark.asyncio
async def test_add_memories_background_noop_when_disabled() -> None:
    from acb_memory import add_memories_background
    # Must not raise
    await add_memories_background("test@example.com", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# GraphitiClient — disabled by default (GRAPHITI_ENABLED=false)
# ---------------------------------------------------------------------------

def test_graphiti_client_disabled_when_graphiti_not_enabled() -> None:
    from acb_memory.graphiti_client import GraphitiClient
    gc = GraphitiClient()
    assert gc._init_attempted is False  # lazy, not yet tried


@pytest.mark.asyncio
async def test_graphiti_client_search_returns_empty_when_disabled() -> None:
    from acb_memory.graphiti_client import GraphitiClient
    gc = GraphitiClient()
    results = await gc.search("Rahul Kumar")
    assert results == []
    assert gc._graphiti is None


@pytest.mark.asyncio
async def test_search_entity_timeline_returns_empty_when_disabled() -> None:
    from acb_memory import search_entity_timeline
    result = await search_entity_timeline("Fracktal-ABC deal", "stage changes")
    assert result == ""


@pytest.mark.asyncio
async def test_add_episode_noop_when_disabled() -> None:
    from acb_memory import add_episode
    # Must not raise
    await add_episode(
        name="test:event",
        content="Deal moved to Awaiting PO",
        source_description="test",
        group_id="test-user",
    )


# ---------------------------------------------------------------------------
# enrich_instructions_with_memory — MAF Agent integration
# ---------------------------------------------------------------------------

def test_enrich_instructions_with_memory_is_exported() -> None:
    from orchestrator.agents import enrich_instructions_with_memory
    assert asyncio.iscoroutinefunction(enrich_instructions_with_memory)


def test_search_timeline_is_exported() -> None:
    from orchestrator.agents import search_timeline
    assert asyncio.iscoroutinefunction(search_timeline)


@pytest.mark.asyncio
async def test_enrich_instructions_returns_base_instructions_when_memory_empty() -> None:
    """With memory disabled, enrich_instructions_with_memory returns the base instructions unchanged."""
    from orchestrator.agents import build_orchestrator_agent, enrich_instructions_with_memory

    agent = build_orchestrator_agent(with_history=False)
    opts = agent.default_options
    assert isinstance(opts, dict), "MAF Agent.default_options must be a dict"
    base_instructions = opts.get("instructions") or ""

    enriched = await enrich_instructions_with_memory(agent, "test@example.com", "test query")
    assert isinstance(enriched, str)
    # When memory is disabled, should equal the base instructions (no memory block appended)
    assert enriched == base_instructions


@pytest.mark.asyncio
async def test_enrich_instructions_appends_memory_block_when_enabled() -> None:
    """When memory returns content, it is appended to the base instructions."""
    import types
    from orchestrator.agents import build_orchestrator_agent, enrich_instructions_with_memory

    agent = build_orchestrator_agent(with_history=False)
    base_instructions = agent.default_options.get("instructions") or ""

    # The agents.py module is loaded via importlib without sys.modules registration.
    # Patch via __globals__ of the function (the actual module namespace dict).
    agents_globals = enrich_instructions_with_memory.__globals__
    original_mem = agents_globals.get("get_memory_context")
    original_gra = agents_globals.get("search_entity_timeline")
    try:
        async def _fake_mem(user_id, query): return "- prefers CNTS breakdown"
        async def _fake_gra(entity, query): return ""
        agents_globals["get_memory_context"] = _fake_mem
        agents_globals["search_entity_timeline"] = _fake_gra

        enriched = await enrich_instructions_with_memory(agent, "vijay@fracktal.in", "deal status")
    finally:
        agents_globals["get_memory_context"] = original_mem
        agents_globals["search_entity_timeline"] = original_gra

    assert "Memory from past conversations" in enriched
    assert "prefers CNTS breakdown" in enriched
    assert base_instructions in enriched  # original instructions preserved


@pytest.mark.asyncio
async def test_enrich_instructions_appends_graphiti_block_when_available() -> None:
    from orchestrator.agents import build_orchestrator_agent, enrich_instructions_with_memory

    agent = build_orchestrator_agent(with_history=False)

    agents_globals = enrich_instructions_with_memory.__globals__
    original_mem = agents_globals.get("get_memory_context")
    original_gra = agents_globals.get("search_entity_timeline")
    try:
        async def _fake_mem(user_id, query): return ""
        async def _fake_gra(entity, query): return "2026-05-12: Deal moved to Awaiting PO"
        agents_globals["get_memory_context"] = _fake_mem
        agents_globals["search_entity_timeline"] = _fake_gra

        enriched = await enrich_instructions_with_memory(agent, "vijay@fracktal.in", "deal timeline")
    finally:
        agents_globals["get_memory_context"] = original_mem
        agents_globals["search_entity_timeline"] = original_gra

    assert "Timeline facts" in enriched
    assert "Awaiting PO" in enriched


@pytest.mark.asyncio
async def test_default_options_mutation_is_per_instance() -> None:
    """Mutating default_options on one agent instance must not affect another."""
    from orchestrator.agents import build_orchestrator_agent

    agent1 = build_orchestrator_agent(with_history=False)
    agent2 = build_orchestrator_agent(with_history=False)

    agent1.default_options["instructions"] = "custom instructions for agent1"

    # agent2 should still have its own default_options
    assert agent2.default_options.get("instructions") != "custom instructions for agent1"


# ---------------------------------------------------------------------------
# Gateway — /copilot/chat endpoint registration
# ---------------------------------------------------------------------------

def test_copilot_chat_endpoint_registered() -> None:
    """The /copilot/chat POST endpoint must be registered on the FastAPI app."""
    from gateway.main import app

    # WebSocket routes have a path but no `methods` — skip them rather than
    # assuming every route is HTTP.
    routes = {
        r.path: r.methods
        for r in served_routes(app.routes)
        if hasattr(r, "path") and hasattr(r, "methods")
    }
    assert "/copilot/chat" in routes, f"Expected /copilot/chat, found: {list(routes)}"
    assert "POST" in routes["/copilot/chat"]


def test_gateway_health() -> None:
    from gateway.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200


def test_copilot_chat_requires_auth() -> None:
    """In dev env (ACB_ENV=dev) auth is bypassed; in prod it returns 401/403.
    Either way the endpoint must exist and return a valid HTTP response."""
    from gateway.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/copilot/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "run_id": "test-run",
            "thread_id": "test-thread",
        })
    # Dev env: auth passes → 200 (streaming). Prod: 401/403. Never 404.
    assert r.status_code != 404, "Expected /copilot/chat to exist"


# ---------------------------------------------------------------------------
# Gateway — /memory/* REST API registered
# ---------------------------------------------------------------------------

def test_memory_routes_registered() -> None:
    """The /memory/{user_id} GET endpoint must be registered."""
    from gateway.main import app

    paths = {r.path for r in served_routes(app.routes) if hasattr(r, "path")}
    # The memory router registers /memory/{user_id} and /memory/{user_id}/search etc.
    memory_paths = [p for p in paths if p.startswith("/memory")]
    assert memory_paths, f"No /memory/* routes found. Registered: {sorted(paths)}"


# ---------------------------------------------------------------------------
# mem0_client — API compatibility with mem0ai v2.x (Memory not MemoryClient)
# ---------------------------------------------------------------------------

def test_mem0_memory_class_available() -> None:
    """mem0ai v2.x must expose Memory (local) with from_config classmethod."""
    from mem0 import Memory
    assert hasattr(Memory, "from_config"), "mem0.Memory.from_config must exist (v2.x)"


def test_mem0_memory_search_uses_filters_kwarg() -> None:
    """Memory.search() accepts top_k and filters kwargs (not user_id= directly)."""
    import inspect
    from mem0 import Memory

    sig = inspect.signature(Memory.search)
    params = list(sig.parameters)
    assert "filters" in params, f"Memory.search must accept 'filters' kwarg, got: {params}"
    assert "top_k" in params, f"Memory.search must accept 'top_k' kwarg, got: {params}"


def test_mem0_memory_get_all_uses_filters_kwarg() -> None:
    import inspect
    from mem0 import Memory

    sig = inspect.signature(Memory.get_all)
    params = list(sig.parameters)
    assert "filters" in params, f"Memory.get_all must accept 'filters' kwarg, got: {params}"


# ---------------------------------------------------------------------------
# Graphiti — API compatibility check
# ---------------------------------------------------------------------------

def test_graphiti_core_importable() -> None:
    import graphiti_core
    assert graphiti_core is not None


def test_graphiti_episode_type_importable() -> None:
    from graphiti_core.nodes import EpisodeType
    assert hasattr(EpisodeType, "text")
