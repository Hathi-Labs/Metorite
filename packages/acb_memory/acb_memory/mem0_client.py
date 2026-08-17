"""Mem0 episodic memory client — stores and retrieves facts across THREE scopes.

Scopes (all live in the one ``mem0_memories`` pgvector collection, separated by
Mem0's ``user_id`` field which we repurpose as a scope key):

- **user**  — ``user_id = "<email>"``          — facts about one human. Private to them.
- **agent** — ``user_id = "agent:<name>"``      — facts an agent learns, shared across
              EVERY user who talks to that agent (cross-user agent memory).
- **org**   — ``user_id = "org:global"``        — organisation-wide shared memory every
              agent can read and write.

We key scopes through ``user_id`` (not Mem0's ``agent_id``) because Mem0's
search/get_all reliably filter on ``user_id`` and do NOT filter on ``agent_id`` —
so the scope key must ride the field that actually filters. ``scope_key()`` builds
the key; ``AGENT_SCOPE_PREFIX`` / ``ORG_SCOPE_KEY`` are the reserved namespaces.

Design:
- Backend: Postgres + pgvector (reuses the existing Metorite Postgres;
  Mem0 creates its own 'mem0_memories' collection automatically).
- LLM for extraction: gateway /v1 via litellm SDK (same tier-1 model as agents use).
- Embedding: text-embedding-3-small via gateway /v1 (litellm SDK, cached).
- Graceful degradation: if MEM0_ENABLED=false or Postgres is unavailable,
  every function returns empty results — agents work normally.

Typical usage:
    context = await get_memory_context("user@fracktal.in", "status of deals")
    ctx = await get_scoped_context(scope_key(agent="sales"), "delhi pipeline")
    asyncio.create_task(add_memories_background("user@fracktal.in", messages))
"""
from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.dsn import parse_postgres_dsn

from acb_memory.compartments import (
    AGENT_SCOPE_PREFIX,
    ORG_SCOPE_KEY,
    PREFS_SCOPE_PREFIX,
    ROOM_SCOPE_PREFIX,
    scope_key,
    scope_kind,
)

from ._gateway_env import gateway_only_env

_log = get_logger("acb_memory.mem0")


# The scope vocabulary lives in compartments.py — ONE definition, because two
# that can drift is how a partition boundary quietly stops matching itself.
# Re-exported here (see the import above) so every existing
# `from acb_memory.mem0_client import scope_key` keeps working, and named in
# __all__ so the shim reads as deliberate rather than as unused imports.
__all__ = [
    "AGENT_SCOPE_PREFIX",
    "ORG_SCOPE_KEY",
    "PREFS_SCOPE_PREFIX",
    "ROOM_SCOPE_PREFIX",
    "MemoryClient",
    "add_memories_background",
    "get_memory_client",
    "get_memory_context",
    "get_scoped_context",
    "scope_key",
    "scope_kind",
]


class MemoryClient:
    """Thin wrapper around the mem0ai MemoryClient with lazy initialisation.

    Thread-safe: the underlying MemoryClient is synchronous and the public
    API is fully async (wrapped in asyncio.to_thread).
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    def _get_client(self) -> Any | None:
        """Lazily build and cache the mem0 client.  Returns None when disabled."""
        if self._client is not None:
            return self._client

        settings = get_settings()
        if not getattr(settings, "mem0_enabled", False):
            return None

        try:
            # mem0ai ≥ 2.x: Memory = local/self-hosted (pgvector, SQLite, etc.)
            #                MemoryClient = cloud-hosted Mem0 SaaS — no from_config
            from mem0 import Memory as _Mem0Memory  # noqa: PLC0415

            db_url: str = settings.database_url
            # mem0ai expects plain host/port/dbname pieces, not a SQLAlchemy
            # URL — split through the shared parser (query params tolerated).
            try:
                dsn = parse_postgres_dsn(db_url)
            except RuntimeError:
                _log.warning("mem0.bad_db_url", url=db_url[:40])
                return None

            pg_user, pg_pass = dsn.user, dsn.password
            pg_host, pg_db = dsn.host, dsn.dbname
            pg_port_int = dsn.port

            litellm_url: str = settings.litellm_base_url
            litellm_key: str = settings.litellm_master_key

            # Use the gateway's /v1/chat/completions endpoint (LiteLLM tiers).
            # Send a tier ALIAS (not a raw provider model) so the gateway routes
            # through its configured tier → provider mapping.  A raw model name
            # the gateway doesn't have in its model_list falls back to OpenRouter
            # (which can be out of credits → 402).  tier-fast currently maps to
            # deepseek/deepseek-chat — cheap & fast, right for high-volume
            # background fact extraction.  No OPENAI_API_KEY needed.
            _llm_url = litellm_url.rstrip("/") + "/v1"
            _llm_key = litellm_key
            _llm_model = "tier-fast"

            config: dict[str, Any] = {
                "vector_store": {
                    "provider": "pgvector",
                    "config": {
                        "host": pg_host,
                        "port": pg_port_int,
                        "dbname": pg_db,
                        "user": pg_user,
                        "password": pg_pass,
                        "collection_name": "mem0_memories",
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": _llm_model,
                        "api_key": _llm_key,
                        "openai_base_url": _llm_url,
                    },
                },
                "history_db_path": "",
            }
            # Embeddings: route through the gateway /v1/embeddings just
            # like the LLM.  The gateway's LiteLLM tier maps model names
            # → actual providers — no OPENAI_API_KEY needed here.
            # Falls back to gemini-embedding when the primary model has
            # no configured key, or when OPENAI_API_KEY env var is set.
            _embed_model = "text-embedding-3-small"
            if not os.environ.get("OPENAI_API_KEY", "").strip():
                _embed_model = "gemini-embedding"
            config["embedder"] = {
                "provider": "openai",
                "config": {
                    "model": _embed_model,
                    "api_key": _llm_key,
                    "openai_base_url": _llm_url,
                },
            }

            # Build with provider-override env vars removed so mem0's OpenAI
            # client uses our gateway (openai_base_url) — NOT OpenRouter — and
            # can resolve the ``tier-fast`` alias.  See gateway_only_env.
            with gateway_only_env():
                self._client = _Mem0Memory.from_config(config_dict=config)
            _log.info(
                "mem0.client_ready",
                backend="pgvector",
                llm_base_url=_llm_url,
                llm_model=_llm_model,
            )
            return self._client

        except ImportError as exc:
            _log.warning(
                "mem0.not_installed",
                hint="pip install mem0ai",
                error=str(exc)[:200],
            )
            return None
        except Exception as exc:  # noqa: BLE001
            _log.warning("mem0.init_failed", error=str(exc)[:200])
            return None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def search(self, user_id: str, query: str, limit: int = 8) -> list[dict]:
        """Return memories relevant to *query* for *user_id*.

        Returns [] on any error (graceful degradation).
        """
        client = self._get_client()
        if client is None:
            return []
        try:
            def _sync() -> list[dict]:
                # Memory.search() uses filters dict, not user_id kwarg directly
                results = client.search(
                    query=query,
                    top_k=limit,
                    filters={"user_id": user_id},
                )
                if isinstance(results, dict):
                    return results.get("results", [])
                return list(results) if results else []

            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            _log.debug("mem0.search_error", user_id=user_id[:20], error=str(exc)[:100])
            return []

    async def add(
        self,
        user_id: str,
        messages: list[dict[str, str]],
        agent_id: str = "orchestrator",
    ) -> None:
        """Extract and persist facts from *messages* for *user_id*.

        Non-blocking — designed to be called in a background task.
        """
        client = self._get_client()
        if client is None or not messages:
            return
        try:
            def _sync() -> None:
                client.add(messages, user_id=user_id, agent_id=agent_id)

            await asyncio.to_thread(_sync)
            _log.info("mem0.facts_added", user_id=user_id[:20], msg_count=len(messages))
        except Exception as exc:  # noqa: BLE001
            _log.warning("mem0.add_failed", user_id=user_id[:20], error=str(exc)[:200])

    async def get_all(self, user_id: str) -> list[dict]:
        """Return all stored memories for *user_id* (for the UI memory panel)."""
        client = self._get_client()
        if client is None:
            return []
        try:
            def _sync() -> list[dict]:
                # Memory.get_all() uses filters dict, not user_id kwarg directly
                results = client.get_all(filters={"user_id": user_id})
                if isinstance(results, dict):
                    return results.get("results", [])
                return list(results) if results else []

            return await asyncio.to_thread(_sync)
        except Exception as exc:  # noqa: BLE001
            _log.debug("mem0.get_all_error", error=str(exc)[:100])
            return []

    async def delete(self, memory_id: str) -> None:
        """Delete a single memory by ID."""
        client = self._get_client()
        if client is None:
            return
        try:
            await asyncio.to_thread(client.delete, memory_id=memory_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("mem0.delete_error", error=str(exc)[:100])


@lru_cache(maxsize=1)
def get_memory_client() -> MemoryClient:
    """Return the singleton MemoryClient."""
    return MemoryClient()


async def get_memory_context(user_id: str, query: str) -> str:
    """Return a formatted memory context string for injection into agent prompts.

    Returns empty string when Mem0 is disabled or no memories match.

    Example output:
        "Past memory (use for continuity):\\n"
        "- Vijay prefers CNTS-stage deal summaries\\n"
        "- Focus on Delhi NCR region for sales prospecting\\n"
    """
    if not user_id:
        return ""
    memories = await get_memory_client().search(user_id, query)
    if not memories:
        return ""
    lines = []
    for m in memories:
        fact = m.get("memory") or m.get("text") or str(m)
        if fact:
            lines.append(f"- {fact.strip()}")
    if not lines:
        return ""
    return "Past memory (use for continuity):\n" + "\n".join(lines)


async def add_memories_background(
    user_id: str,
    messages: list[dict[str, str]],
    agent_id: str = "orchestrator",
) -> None:
    """Fire-and-forget memory extraction.  Safe to call without awaiting.

    Typical usage (in gateway after a run completes):
        asyncio.create_task(add_memories_background(user_id, messages))
    """
    await get_memory_client().add(user_id, messages, agent_id=agent_id)


# ---------------------------------------------------------------------------
# Scoped helpers (agent + org memory)
# ---------------------------------------------------------------------------
#
# These are thin, self-labelling wrappers over search/add so the gateway and the
# memory tools read clearly. A scope is just a distinct Mem0 user_id value (see
# scope_key), so all the graceful-degradation and filtering behaviour is shared.


async def get_scoped_context(scope: str, query: str, *, header: str) -> str:
    """Formatted memory block for one scope (agent or org), or "" if empty.

    *scope* is a scope_key() result. *header* labels the block in the prompt
    (e.g. "Agent memory" / "Organisation memory") so the model can tell the
    three memory sources apart.
    """
    if not scope:
        return ""
    memories = await get_memory_client().search(scope, query)
    lines = []
    for m in memories:
        fact = m.get("memory") or m.get("text") or str(m)
        if fact:
            lines.append(f"- {fact.strip()}")
    if not lines:
        return ""
    return f"{header} (shared, use for continuity):\n" + "\n".join(lines)


async def add_scoped_memories(
    scope: str,
    messages: list[dict[str, str]],
    *,
    agent_id: str = "orchestrator",
) -> None:
    """Fire-and-forget fact extraction into an agent/org scope (see scope_key)."""
    if not scope:
        return
    await get_memory_client().add(scope, messages, agent_id=agent_id)


async def get_scoped_all(scope: str) -> list[dict]:
    """All stored memories for a scope (agent/org) — for the memory UI panel."""
    if not scope:
        return []
    return await get_memory_client().get_all(scope)
