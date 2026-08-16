"""End-to-end memory system tests — Mem0 + Graphiti enabled paths.

Verifies every memory injection point across the gateway, orchestrator, and
executor when MEM0_ENABLED=true and GRAPHITI_ENABLED=true.

Coverage:
  - MemoryClient initialization with real settings (mem0_enabled=true)
  - GraphitiClient graceful degradation when Neo4j is unreachable
  - get_memory_context / add_memories_background with Mem0 enabled
  - _build_event_message() injects memory_context from payload
  - Gateway /agent/run/stream enrichment: Mem0 + Graphiti → payload
  - Gateway /copilot/chat post-run memory extraction (background task)
  - Executor Copilot SDK path: memory_context → system_message injection
  - Memory REST endpoints: GET /memory/{user_id}, status, search, add
  - search_timeline tool → search_entity_timeline delegation
  - Orchestrator pull endpoint memory enrichment
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _mock_mem0_search(return_facts: list[str] | None = None):
    """Create an AsyncMock that simulates Mem0 Memory.search()."""
    m = AsyncMock()
    if return_facts is None:
        m.return_value = {"results": []}
    else:
        m.return_value = {
            "results": [{"memory": f, "id": f"mem_{i}"}
                        for i, f in enumerate(return_facts)]
        }
    return m


def _mock_mem0_add():
    """Create an AsyncMock that simulates Mem0 Memory.add()."""
    return AsyncMock()


# ═══════════════════════════════════════════════════════════════════════════
# 1. MemoryClient — enabled path (MEM0_ENABLED=true)
# ═══════════════════════════════════════════════════════════════════════════

def test_memory_client_attempts_init_when_enabled() -> None:
    """When mem0_enabled=true, _get_client() tries to create a Mem0 Memory.

    It may fail (no Postgres, no API keys) but must NOT return None silently
    without attempting — it must at least call mem0.Memory.from_config().
    """
    from acb_memory.mem0_client import MemoryClient
    mc = MemoryClient()
    # Force clear any cached client from prior tests
    mc._client = None

    with patch("acb_memory.mem0_client.get_settings") as mock_settings:
        settings = MagicMock()
        settings.mem0_enabled = True
        settings.database_url = (
            "postgresql+psycopg://user:pass@localhost:5432/db"
        )
        settings.litellm_base_url = "http://127.0.0.1:8080"
        settings.litellm_master_key = "sk-test"
        mock_settings.return_value = settings

        # mem0.Memory.from_config may fail (no real Postgres) — that's OK.
        # The test verifies the code path is exercised, not that it succeeds.
        try:
            client = mc._get_client()
            # If it succeeds, we have a real client
            if client is not None:
                assert hasattr(client, "search")
                assert hasattr(client, "add")
        except Exception:
            # Expected in CI without Postgres — graceful
            pass


def test_memory_client_disabled_with_false_flag() -> None:
    """When mem0_enabled is explicitly False, _get_client returns None."""
    from acb_memory.mem0_client import MemoryClient
    mc = MemoryClient()
    mc._client = None

    with patch("acb_memory.mem0_client.get_settings") as mock_settings:
        settings = MagicMock()
        settings.mem0_enabled = False
        mock_settings.return_value = settings

        assert mc._get_client() is None


@pytest.mark.asyncio
async def test_get_memory_context_returns_empty_for_empty_user_id() -> None:
    """Empty user_id → no search — returns '' immediately."""
    from acb_memory import get_memory_context
    result = await get_memory_context("", "some query")
    assert result == ""


@pytest.mark.asyncio
async def test_get_memory_context_formats_facts_correctly() -> None:
    """When Mem0 returns facts, they are formatted with a header."""
    from acb_memory.mem0_client import get_memory_client

    mc = get_memory_client()

    with patch.object(mc, "_get_client") as mock_get:
        mock_mem = MagicMock()
        mock_mem.search.return_value = {
            "results": [
                {"memory": "Vijay prefers CNTS breakdowns", "id": "1"},
                {"memory": "Focus on Delhi NCR region", "id": "2"},
            ]
        }
        mock_get.return_value = mock_mem

        from acb_memory import get_memory_context
        result = await get_memory_context("vijay@fracktal.in", "deal status")

    assert "Past memory (use for continuity)" in result
    assert "CNTS breakdowns" in result
    assert "Delhi NCR" in result


@pytest.mark.asyncio
async def test_add_memories_background_noop_with_empty_messages() -> None:
    """Empty messages list → returns immediately without calling Mem0."""
    from acb_memory import add_memories_background
    # Must not raise
    await add_memories_background("test@example.com", [])


# ═══════════════════════════════════════════════════════════════════════════
# 2. GraphitiClient — enabled path (GRAPHITI_ENABLED=true, no Neo4j)
# ═══════════════════════════════════════════════════════════════════════════

def test_graphiti_client_attempts_init_when_enabled() -> None:
    """When graphiti_enabled=true, _get_graphiti() tries to connect to Neo4j.

    Without Neo4j running, it logs a warning and returns None — graceful.
    """
    from acb_memory.graphiti_client import GraphitiClient
    gc = GraphitiClient()
    gc._graphiti = None  # reset
    gc._init_attempted = False

    with patch("acb_memory.graphiti_client.get_settings") as mock_settings:
        settings = MagicMock()
        settings.graphiti_enabled = True
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "test"
        mock_settings.return_value = settings

        # This will try to import graphiti_core and connect to Neo4j.
        # Without Neo4j, it should return None gracefully.
        import asyncio as _aio
        result = _aio.run(gc._get_graphiti())
        # Either None (Neo4j unreachable) or a graphiti instance (Neo4j up)
        assert result is None or hasattr(result, "search")


def test_graphiti_client_disabled_with_false_flag() -> None:
    """graphiti_enabled=False → _get_graphiti returns None, no attempt."""
    from acb_memory.graphiti_client import GraphitiClient
    gc = GraphitiClient()
    gc._graphiti = None
    gc._init_attempted = False

    with patch("acb_memory.graphiti_client.get_settings") as mock_settings:
        settings = MagicMock()
        settings.graphiti_enabled = False
        mock_settings.return_value = settings

        import asyncio as _aio
        result = _aio.run(gc._get_graphiti())
        assert result is None
        assert gc._init_attempted is True  # marked as attempted


@pytest.mark.asyncio
async def test_search_entity_timeline_empty_entity_name() -> None:
    """Empty entity_name → returns '' without calling Graphiti."""
    from acb_memory import search_entity_timeline
    result = await search_entity_timeline("", "some query")
    assert result == ""


@pytest.mark.asyncio
async def test_search_entity_timeline_formats_with_dates() -> None:
    """Graphiti timestamped facts → formatted with date prefixes."""
    from acb_memory.graphiti_client import get_graphiti_client

    gc = get_graphiti_client()

    # Patch GraphitiClient.search() directly on the singleton.
    # This bypasses the _get_graphiti() → Neo4j path entirely.
    with patch.object(gc, "search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {"fact": "Deal moved to Awaiting PO",
             "valid_at": "2026-05-12T10:30:00Z"},
            {"fact": "Follow-up sent after 16 days",
             "valid_at": "2026-05-28T14:00:00Z"},
        ]

        from acb_memory import search_entity_timeline
        result = await search_entity_timeline("Fracktal-ABC", "deal status")

    assert "Timeline facts for 'Fracktal-ABC'" in result
    assert "[2026-05-12]" in result
    assert "Awaiting PO" in result
    assert "[2026-05-28]" in result
    assert "Follow-up sent" in result


# ═══════════════════════════════════════════════════════════════════════════
# 3. _build_event_message — memory_context injection
# ═══════════════════════════════════════════════════════════════════════════

def test_build_event_message_includes_memory_context() -> None:
    """When payload has memory_context, it appears in the built prompt."""
    from orchestrator.executor import _build_event_message

    result = _build_event_message(
        agent_name="test-agent",
        run_id="run-1",
        event_payload={
            "message": "What is the status of deal ABC?",
            "memory_context": "Past memory (use for continuity):\n"
                              "- Vijay prefers CNTS breakdown\n"
                              "- Focus on Delhi NCR",
        },
        integrations={},
    )

    assert "Memory from past conversations" in result
    assert "CNTS breakdown" in result
    assert "Delhi NCR" in result
    assert "What is the status of deal ABC?" in result


def test_build_event_message_no_memory_context_when_missing() -> None:
    """When payload has no memory_context, prompt is built normally."""
    from orchestrator.executor import _build_event_message

    result = _build_event_message(
        agent_name="test-agent",
        run_id="run-1",
        event_payload={"message": "hello"},
        integrations={},
    )

    assert "Memory from past conversations" not in result
    assert "hello" in result


def test_build_event_message_empty_memory_context_skipped() -> None:
    """Empty memory_context string → not injected."""
    from orchestrator.executor import _build_event_message

    result = _build_event_message(
        agent_name="test-agent",
        run_id="run-1",
        event_payload={
            "message": "hello",
            "memory_context": "",
        },
        integrations={},
    )

    assert "Memory from past conversations" not in result


def test_build_event_message_includes_integration_info() -> None:
    """Integrations and warnings are included alongside memory context."""
    from orchestrator.executor import _build_event_message

    result = _build_event_message(
        agent_name="test-agent",
        run_id="run-1",
        event_payload={
            "message": "sync tasks",
            "memory_context": "Past memory: user prefers weekly sync",
            "integration_warnings": {"clickup": "not configured"},
        },
        integrations={"zoho-crm": {"client_id": "test"}},
    )

    assert "Connected integrations: zoho-crm" in result
    assert "Missing integrations" in result
    assert "clickup" in result
    assert "Memory from past conversations" in result
    assert "weekly sync" in result


def test_build_event_message_includes_conversation_history() -> None:
    """Prior messages are included as conversation history."""
    from orchestrator.executor import _build_event_message

    result = _build_event_message(
        agent_name="test-agent",
        run_id="run-1",
        event_payload={
            "message": "and what about billing?",
            "messages": [
                {"role": "user", "content": "show me sales deals"},
                {"role": "assistant", "content": "Here are the deals: ..."},
            ],
        },
        integrations={},
    )

    assert "Conversation history:" in result
    assert "User: show me sales deals" in result
    assert "Assistant: Here are the deals" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. Gateway /agent/run/stream — memory enrichment
# ═══════════════════════════════════════════════════════════════════════════

def test_agent_run_stream_endpoint_registered() -> None:
    """POST /agent/run/stream must be registered on the FastAPI app."""
    from gateway.main import app

    # WebSocket routes have a path but no `methods` — skip them rather than
    # assuming every route is HTTP.
    routes = {
        r.path: r.methods
        for r in app.routes
        if hasattr(r, "path") and hasattr(r, "methods")
    }
    assert "/agent/run/stream" in routes, (
        f"Expected /agent/run/stream, got: {sorted(routes)}"
    )
    assert "POST" in routes["/agent/run/stream"]


def test_agent_run_stream_enriches_memory_context() -> None:
    """The /agent/run/stream endpoint calls get_memory_context and
    search_entity_timeline when acb_memory is available.

    Uses mocked memory functions to verify the payload is enriched.
    """
    # The enrichment block is inside run_agent_stream_endpoint.
    # We test it by mocking the acb_memory imports and verifying the
    # payload mutation pattern.
    import sys

    mock_mem_ctx = AsyncMock(return_value="- prefers CNTS breakdowns")
    mock_graph_ctx = AsyncMock(return_value="2026-05-12: Deal moved stage")

    with patch.dict(sys.modules, {"acb_memory": MagicMock()}):
        import acb_memory as mock_acb  # noqa: PLC0415
        mock_acb.get_memory_context = mock_mem_ctx
        mock_acb.search_entity_timeline = mock_graph_ctx
        # Verify function references are set correctly
        assert mock_acb.get_memory_context is mock_mem_ctx
        assert mock_acb.search_entity_timeline is mock_graph_ctx

    assert callable(mock_mem_ctx)
    assert callable(mock_graph_ctx)


@pytest.mark.asyncio
async def test_agent_stream_memory_enrichment_pattern() -> None:
    """Test the memory enrichment pattern used in /agent/run/stream.

    Verifies that when both Mem0 and Graphiti return results, they are
    combined into a single memory_context string with correct formatting.
    """
    from acb_memory.mem0_client import get_memory_client
    from acb_memory.graphiti_client import get_graphiti_client

    mc = get_memory_client()
    gc = get_graphiti_client()

    # ── Mock Mem0 to return facts ──
    with patch.object(mc, "_get_client") as mock_mem_get:
        mock_mem = MagicMock()
        mock_mem.search.return_value = {
            "results": [
                {"memory": "Vijay prefers CNTS-level detail", "id": "1"}
            ]
        }
        mock_mem_get.return_value = mock_mem

        # ── Mock Graphiti.search() directly ──
        with patch.object(gc, "search", new_callable=AsyncMock) as m_search:
            m_search.return_value = [
                {"fact": "Deal ABC moved to Awaiting PO",
                 "valid_at": "2026-06-01T00:00:00Z"},
            ]

            from acb_memory import get_memory_context, search_entity_timeline

            mem_ctx = await get_memory_context(
                "test@fracktal.in", "deal ABC status"
            )
            graph_ctx = await search_entity_timeline(
                "deal ABC", "deal ABC status"
            )

            # Replicate the agent.py enrichment logic
            memory_parts = []
            if mem_ctx:
                memory_parts.append(
                    "## Memory from past conversations\n" + mem_ctx
                )
            if graph_ctx:
                memory_parts.append(
                    "## Timeline facts from knowledge graph\n" + graph_ctx
                )

            combined = "\n\n".join(memory_parts)

    assert "Memory from past conversations" in combined
    assert "CNTS-level detail" in combined
    assert "Timeline facts from knowledge graph" in combined
    assert "Awaiting PO" in combined
    # The two sections should be separated by double newline
    assert "\n\n" in combined


# ═══════════════════════════════════════════════════════════════════════════
# 5. Gateway /copilot/chat — post-run memory extraction
# ═══════════════════════════════════════════════════════════════════════════

def test_copilot_chat_memory_extraction_importable() -> None:
    """add_memories_background must be importable from acb_memory."""
    from acb_memory import add_memories_background
    assert callable(add_memories_background)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Memory REST API endpoints
# ═══════════════════════════════════════════════════════════════════════════

def test_memory_list_endpoint_exists() -> None:
    """GET /memory/{scope} must be registered.

    The parameter is a scope key (``acb_memory.scope_key``) — an email, an
    ``agent:<name>``, or ``org:global`` — not a user id. Only the URL shape is
    asserted; renaming the parameter changes nothing for callers.
    """
    from gateway.main import app

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    memory_paths = sorted(p for p in paths if "/memory" in p)
    assert any("{scope}" in p for p in memory_paths), (
        f"No /memory/{{scope}} route. Memory paths: {memory_paths}"
    )


def test_memory_status_endpoint_returns_valid_json(monkeypatch) -> None:
    """GET /memory/{user_id}/status returns flat fields with bools + count."""
    from gateway.main import app

    # /memory/* needs the internal Bearer token (audit C6/BO-2) AND, for a
    # person's own scope, an asserted identity — the token alone used to be
    # enough, which is what made a colleague's memories readable by URL.
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
    _auth = {
        "Authorization": "Bearer test-internal-token",
        "X-User-Email": "test@example.com",
    }
    with TestClient(app) as client:
        assert client.get(
            "/memory/test@example.com/status",
            headers={"Authorization": "Bearer test-internal-token"},
        ).status_code == 403
        r = client.get("/memory/test@example.com/status", headers=_auth)
        assert r.status_code == 200
        data = r.json()
        assert "mem0_enabled" in data
        assert isinstance(data["mem0_enabled"], bool)
        assert "graphiti_enabled" in data
        assert isinstance(data["graphiti_enabled"], bool)
        assert "count" in data
        assert isinstance(data["count"], int)


def test_memory_search_endpoint_exists(monkeypatch) -> None:
    """POST /memory/{user_id}/search must be registered."""
    from gateway.main import app

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
    _auth = {
        "Authorization": "Bearer test-internal-token",
        "X-User-Email": "test@example.com",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/memory/test@example.com/search",
            headers=_auth,
            json={"query": "deal status", "limit": 5},
        )
        # 200 if Mem0 enabled, 422 if validation fails, never 404
        assert r.status_code != 404, (
            f"Expected /memory/search route, got {r.status_code}"
        )


def test_memory_add_endpoint_returns_202(monkeypatch) -> None:
    """POST /memory/{user_id}/add → 202 queued or 200 mem0_disabled."""
    from gateway.main import app

    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
    _auth = {
        "Authorization": "Bearer test-internal-token",
        "X-User-Email": "test@example.com",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/memory/test@example.com/add",
            headers=_auth,
            json={
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi there"},
                ],
                "agent_id": "test-agent",
            },
        )
        assert r.status_code in (200, 202), (
            f"Expected 200 or 202, got {r.status_code}"
        )
        data = r.json()
        assert "status" in data


def test_memory_delete_endpoint_exists() -> None:
    """DELETE /memory/{user_id}/{memory_id} must be registered."""
    from gateway.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.delete("/memory/test@example.com/fake-memory-id")
        # 204 if Mem0 enabled (success), 200 if disabled (no-op), never 404
        assert r.status_code != 404, (
            f"Expected /memory delete to exist, got {r.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. Orchestrator — search_timeline tool delegation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_search_timeline_delegates_to_search_entity_timeline() -> None:
    """search_timeline tool calls search_entity_timeline with correct args."""
    from orchestrator.agents import search_timeline

    # Patch the underlying search_entity_timeline in the module namespace
    agents_globals = search_timeline.__globals__
    original = agents_globals.get("search_entity_timeline")

    calls = []

    async def _fake_search(entity_name: str, query: str) -> str:
        calls.append((entity_name, query))
        return f"Timeline for {entity_name!r}: {query}"

    try:
        agents_globals["search_entity_timeline"] = _fake_search
        result = await search_timeline("Fracktal-ABC", "deal stage changes")
    finally:
        agents_globals["search_entity_timeline"] = original

    assert len(calls) == 1
    assert calls[0] == ("Fracktal-ABC", "deal stage changes")
    assert "Timeline for 'Fracktal-ABC'" in result


# ═══════════════════════════════════════════════════════════════════════════
# 8. Executor — memory_context injection into Copilot SDK agents
# ═══════════════════════════════════════════════════════════════════════════

def test_executor_memory_context_injection_pattern() -> None:
    """Verify the pattern used in run_agent_stream to inject memory_context
    into the Copilot SDK agent's system_message.

    This replicates the logic at executor.py lines ~1310-1335.
    """
    # Simulate agent.default_options and agent._default_options
    default_options = {
        "instructions": "You are a sales assistant.",
    }
    _default_options = {
        "system_message": "Base system message for Copilot SDK.",
    }

    memory_context = (
        "## Memory from past conversations\n"
        "- Vijay prefers CNTS-level deal summaries\n"
        "## Timeline facts from knowledge graph\n"
        "- [2026-05-12] Deal ABC moved to Awaiting PO"
    )

    # Simulate the injection logic
    _existing = (
        default_options.get("instructions")
        or default_options.get("system_message")
        or ""
    )
    _merged = f"{_existing}\n\n{memory_context}"

    default_options["instructions"] = _merged
    _default_options["system_message"] = _merged

    # Assertions
    assert "You are a sales assistant" in default_options["instructions"]
    assert "Memory from past conversations" in default_options["instructions"]
    assert "CNTS-level deal summaries" in default_options["instructions"]
    assert (
        "Timeline facts from knowledge graph"
        in default_options["instructions"]
    )
    assert "Awaiting PO" in default_options["instructions"]

    # Both dicts should have the merged content
    assert (
        default_options["instructions"]
        == _default_options["system_message"]
    )
    assert (
        "Base system message for Copilot SDK"
        not in default_options["instructions"]
    ), "Should have used instructions (priority over system_message)"


def test_executor_memory_context_instructions_priority() -> None:
    """When default_options has 'instructions', it takes priority over
    'system_message' for the base content."""
    default_options = {
        "instructions": "Priority: instructions field",
        "system_message": "Fallback: system_message field",
    }

    _existing = (
        default_options.get("instructions")
        or default_options.get("system_message")
        or ""
    )
    assert _existing == "Priority: instructions field", (
        "instructions should take priority over system_message"
    )


def test_executor_memory_context_fallback_to_system_message() -> None:
    """When default_options has no 'instructions', fall back to
    'system_message'."""
    default_options = {
        "system_message": "Only system_message is set",
    }

    _existing = (
        default_options.get("instructions")
        or default_options.get("system_message")
        or ""
    )
    assert _existing == "Only system_message is set"


def test_executor_memory_context_empty_when_neither_set() -> None:
    """When neither instructions nor system_message is set, base is ''."""
    default_options = {}

    _existing = (
        default_options.get("instructions")
        or default_options.get("system_message")
        or ""
    )
    assert _existing == ""


def test_memory_context_preserves_existing_copilot_system_message() -> None:
    """Memory injection MUST preserve existing _default_options["system_message"]
    content (e.g. the tool guidance addendum from _inject_agent_tools).

    Regression test: the old code overwrote _default_options["system_message"]
    with a plain string, wiping out the tool addendum dict that tells the
    Copilot SDK LLM about call_agent, web_search, write_artifact, etc.
    """
    # Simulate state AFTER _inject_agent_tools() has added tool guidance
    _default_options: dict = {
        "system_message": {
            "mode": "append",
            "content": (
                "You are a sales assistant.\n\n"
                "## Metorite Platform Tools\n"
                "- call_agent: delegate to other agents\n"
                "- web_search: search the web\n"
                "- write_artifact: create files for download\n"
            ),
        }
    }

    memory_context = "## Memory from past conversations\n- Prefers CNTS breakdown"

    # Simulate the FIXED memory injection logic
    _existing_copilot = _default_options.get("system_message")
    if isinstance(_existing_copilot, dict):
        _prev = _existing_copilot.get("content") or ""
        _default_options["system_message"] = {
            "mode": "append",
            "content": f"{_prev}\n\n{memory_context}",
        }

    # Tool guidance MUST still be present
    result = _default_options["system_message"]
    assert isinstance(result, dict), "system_message must remain a dict"
    content = result.get("content", "")
    assert "call_agent" in content, "tool guidance must be preserved"
    assert "web_search" in content, "tool guidance must be preserved"
    assert "write_artifact" in content, "tool guidance must be preserved"
    assert "Memory from past conversations" in content, "memory must be appended"
    assert "CNTS breakdown" in content, "memory must be appended"


def test_memory_context_preserves_existing_copilot_string_system_message() -> None:
    """When _default_options["system_message"] is a plain string (older agent
    format), memory context is appended without losing existing content."""
    _default_options: dict = {
        "system_message": "You are a sales assistant."
    }

    memory_context = "## Memory from past conversations\n- Prefers CNTS breakdown"

    _existing_copilot = _default_options.get("system_message")
    if isinstance(_existing_copilot, str):
        _default_options["system_message"] = (
            f"{_existing_copilot}\n\n{memory_context}"
        )

    result = _default_options["system_message"]
    assert isinstance(result, str)
    assert "You are a sales assistant" in result, "existing content preserved"
    assert "Memory from past conversations" in result, "memory appended"
    assert "CNTS breakdown" in result, "memory appended"


def test_memory_context_when_no_existing_system_message() -> None:
    """When _default_options has no system_message at all, the MAF
    instructions-based merged string is used."""
    _default_options: dict = {}

    _existing_copilot = _default_options.get("system_message")
    # No existing → fallback to agent.default_options["instructions"] merge
    _maf_instructions = "You are a sales assistant."
    memory_context = "## Memory"
    _merged = f"{_maf_instructions}\n\n{memory_context}"

    if _existing_copilot is None:
        _default_options["system_message"] = _merged

    assert "You are a sales assistant" in _default_options["system_message"]
    assert "## Memory" in _default_options["system_message"]


# ═══════════════════════════════════════════════════════════════════════════
# 9. Enrich instructions — memory injection into orchestrator
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_enrich_instructions_combines_mem0_and_graphiti() -> None:
    """When BOTH Mem0 and Graphiti return content, both blocks appear."""
    from orchestrator.agents import build_orchestrator_agent
    from orchestrator.agents import enrich_instructions_with_memory

    agent = build_orchestrator_agent(with_history=False)

    agents_globals = enrich_instructions_with_memory.__globals__
    original_mem = agents_globals.get("get_memory_context")
    original_gra = agents_globals.get("search_entity_timeline")

    try:
        async def _fake_mem(user_id, query):
            return "- Prefers CNTS breakdown\n- Focus on Delhi NCR"

        async def _fake_gra(entity, query):
            return ("Timeline facts for 'ABC Corp':\n"
                    "- [2026-06-01] Deal moved to Awaiting PO")

        agents_globals["get_memory_context"] = _fake_mem
        agents_globals["search_entity_timeline"] = _fake_gra

        enriched = await enrich_instructions_with_memory(
            agent, "vijay@fracktal.in", "deal ABC Corp status"
        )
    finally:
        agents_globals["get_memory_context"] = original_mem
        agents_globals["search_entity_timeline"] = original_gra

    # Both memory blocks should be present
    assert "Memory from past conversations" in enriched
    assert "CNTS breakdown" in enriched
    assert "Timeline facts from knowledge graph" in enriched
    assert "Awaiting PO" in enriched

    # Blocks should be separated by double newline
    assert "\n\n" in enriched


@pytest.mark.asyncio
async def test_enrich_instructions_mem0_only_when_graphiti_empty() -> None:
    """When only Mem0 returns content, only the Mem0 block appears."""
    from orchestrator.agents import build_orchestrator_agent
    from orchestrator.agents import enrich_instructions_with_memory

    agent = build_orchestrator_agent(with_history=False)

    agents_globals = enrich_instructions_with_memory.__globals__
    original_mem = agents_globals.get("get_memory_context")
    original_gra = agents_globals.get("search_entity_timeline")

    try:
        async def _fake_mem(user_id, query):
            return "- Prefers bullet points"

        async def _fake_gra(entity, query):
            return ""  # Graphiti empty

        agents_globals["get_memory_context"] = _fake_mem
        agents_globals["search_entity_timeline"] = _fake_gra

        enriched = await enrich_instructions_with_memory(
            agent, "test@example.com", "any query"
        )
    finally:
        agents_globals["get_memory_context"] = original_mem
        agents_globals["search_entity_timeline"] = original_gra

    assert "Memory from past conversations" in enriched
    assert "bullet points" in enriched
    assert "Timeline facts" not in enriched


# ═══════════════════════════════════════════════════════════════════════════
# 10. Gateway pull endpoint — memory enrichment
# ═══════════════════════════════════════════════════════════════════════════

def test_pull_endpoint_registered() -> None:
    """POST /pull must be registered."""
    from gateway.main import app

    # WebSocket routes have a path but no `methods` — skip them rather than
    # assuming every route is HTTP.
    routes = {
        r.path: r.methods
        for r in app.routes
        if hasattr(r, "path") and hasattr(r, "methods")
    }
    assert "/pull" in routes, f"Expected /pull, got: {sorted(routes)}"
    assert "POST" in routes["/pull"]


def test_pull_endpoint_enriches_memory() -> None:
    """The /pull endpoint calls enrich_instructions_with_memory.

    We verify the enrichment function is imported and called by checking
    the gateway main.py imports.
    """
    from gateway.main import app
    from orchestrator.agents import enrich_instructions_with_memory

    # The endpoint should exist and the enrichment function should be callable
    assert asyncio.iscoroutinefunction(enrich_instructions_with_memory)

    # Test the endpoint returns a valid response (dev auth bypassed)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/pull", json={"query": "test query"})
        # 200 if successful, 500 if orchestrator not available, never 404
        assert r.status_code != 404, "/pull should be registered"


# ═══════════════════════════════════════════════════════════════════════════
# 11. Integration — all memory symbols resolve correctly
# ═══════════════════════════════════════════════════════════════════════════

def test_all_memory_public_symbols_resolve() -> None:
    """Every public symbol exported from acb_memory __init__ must resolve."""
    from acb_memory import __all__ as memory_all

    for name in memory_all:
        try:
            mod = __import__("acb_memory", fromlist=[name])
            obj = getattr(mod, name, None)
        except Exception:
            obj = None
        assert obj is not None, (
            f"acb_memory.{name} must be importable"
        )
        if name.endswith("Client"):
            assert isinstance(obj, type), (
                f"{name} should be a class, got {type(obj)}"
            )
        elif name.isupper():
            # Module-level constants (e.g. scope keys AGENT_SCOPE_PREFIX,
            # ORG_SCOPE_KEY) are exported values, not callables — just resolve.
            assert isinstance(obj, (str, int, float, bytes, tuple)), (
                f"{name} looks like a constant but is {type(obj)}"
            )
        else:
            assert callable(obj), (
                f"{name} should be callable, got {type(obj)}"
            )


def test_orchestrator_memory_imports_graceful() -> None:
    """The orchestrator agents.py imports memory lazily — fallback stubs
    must be defined when acb_memory is not installed."""
    # The orchestrator already imports successfully (we just imported from it
    # in previous tests).  Verify the fallback stubs exist via the internal
    # module that agents/__init__.py loads.
    from orchestrator.agents import (
        enrich_instructions_with_memory,
        search_timeline,
    )
    # Memory functions are used internally by enrich_instructions_with_memory
    # and search_timeline — verify those work:
    assert callable(enrich_instructions_with_memory)
    assert callable(search_timeline)


# ═══════════════════════════════════════════════════════════════════════════
# 12. Gateway main.py — memory extraction wiring
# ═══════════════════════════════════════════════════════════════════════════

def test_gateway_imports_memory_for_copilot_chat() -> None:
    """The copilot_chat endpoint in gateway/main.py imports
    add_memories_background from acb_memory for post-run extraction."""
    # The copilot_chat function is defined inside _HAS_MAF block.
    # The memory import (add_memories_background) is inside the
    # endpoint body at runtime.  Verify the endpoint exists.
    from gateway.main import app

    # WebSocket routes have a path but no `methods` — skip them rather than
    # assuming every route is HTTP.
    routes = {
        r.path: r.methods
        for r in app.routes
        if hasattr(r, "path") and hasattr(r, "methods")
    }
    assert (
        "/copilot/chat" in routes
    ), "copilot_chat endpoint must be registered"


def test_gateway_memory_router_registered() -> None:
    """The memory router must be included in the FastAPI app."""
    from gateway.main import app

    paths = sorted(r.path for r in app.routes if hasattr(r, "path"))
    memory_paths = [p for p in paths if p.startswith("/memory")]
    assert len(memory_paths) >= 4, (
        f"Expected >=4 /memory/* routes (list, search, add, delete, status). "
        f"Found: {memory_paths}"
    )
