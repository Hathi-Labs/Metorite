"""Graphiti bi-temporal knowledge graph client.

Design:
- Backend: Neo4j (see infra/docker-compose.yml, service: neo4j).
- Uses graphiti-core to store time-stamped entity facts extracted from
  agent conversations and ingestion events.
- Feature-flagged: GRAPHITI_ENABLED=true required; otherwise every
  function returns gracefully empty results.

What Graphiti adds over the Postgres entity graph:
- *Bi-temporal*: records WHEN a fact was valid AND when it was recorded.
  "Deal X was in Awaiting PO from May 12 to May 28" is queryable.
- *Relationship-aware*: "Contact Rahul is linked to Deal X and Company Y
  and had a meeting where action items were created but not followed up."
- Agents can call search_entity_timeline() to get time-contextualised
  answers that the flat Postgres graph cannot provide.

Episodes are added after each ingestion event (Zoho sync, Gmail webhook,
Gmail ingest) and after each significant agent conversation turn.

Typical usage in an agent:
    facts = await search_entity_timeline("Rahul Kumar", "deal status")
    # → "2026-05-12: Deal Fracktal-ABC moved to Awaiting PO [Contact: Rahul Kumar]"
    #   "2026-05-28: Follow-up sent by Vijay after 16 days stale"
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from acb_common import get_logger, get_settings

from ._gateway_env import gateway_only_env

_log = get_logger("acb_memory.graphiti")


class GraphitiClient:
    """Thin async wrapper around graphiti-core.Graphiti.

    Lazy-initialises on first use.  Feature-flagged via GRAPHITI_ENABLED.
    All methods return gracefully empty results when disabled.
    """

    def __init__(self) -> None:
        self._graphiti: Any | None = None
        self._init_attempted = False

    async def _get_graphiti(self) -> Any | None:
        """Lazily initialise and return the Graphiti instance."""
        if self._init_attempted:
            return self._graphiti

        self._init_attempted = True
        settings = get_settings()
        if not getattr(settings, "graphiti_enabled", False):
            return None

        neo4j_url: str = getattr(settings, "neo4j_url", "bolt://localhost:7687")
        neo4j_user: str = getattr(settings, "neo4j_user", "neo4j")
        neo4j_password: str = getattr(settings, "neo4j_password", "")
        litellm_url: str = settings.litellm_base_url
        litellm_key: str = settings.litellm_master_key

        if not neo4j_password:
            _log.warning("graphiti.no_neo4j_password", hint="Set NEO4J_PASSWORD in .env")
            return None

        try:
            from graphiti_core import Graphiti
            from graphiti_core.embedder.openai import (
                OpenAIEmbedder,
                OpenAIEmbedderConfig,
            )
            from graphiti_core.llm_client.config import LLMConfig
            from graphiti_core.llm_client.openai_client import (
                OpenAIClient as _GOpenAI,
            )
            from openai import AsyncOpenAI

            # Reuse gateway /v1 (litellm SDK) for both LLM and embeddings so
            # no separate API key or model config is needed.  The gateway
            # routes tier aliases → actual providers via config.yaml.
            #
            # Build the OpenAI clients with provider-override env vars removed
            # (OPENROUTER_API_KEY etc.) so graphiti-core honours the base_url we
            # pass instead of diverting to OpenRouter and rejecting the
            # ``tier-fast`` alias — the same hijack mem0 hit.  See gateway_only_env.
            with gateway_only_env():
                openai_client = AsyncOpenAI(
                    api_key=litellm_key,
                    base_url=litellm_url,
                )
                llm_client = _GOpenAI(
                    config=LLMConfig(
                        api_key=litellm_key,
                        model="tier-fast",
                        base_url=litellm_url,
                    ),
                    client=openai_client,
                )

                # Embedding model: prefer OpenAI text-embedding-3-small when an
                # OPENAI_API_KEY is configured; otherwise fall back to Gemini
                # embedding through the gateway (GEMINI_API_KEY is always set).
                _embed_model = "text-embedding-3-small"
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    _embed_model = "gemini-embedding"
                embedder = OpenAIEmbedder(
                    config=OpenAIEmbedderConfig(
                        embedding_model=_embed_model,
                        api_key=litellm_key,
                        base_url=litellm_url,
                    )
                )

            self._graphiti = Graphiti(
                neo4j_url,
                neo4j_user,
                neo4j_password,
                llm_client=llm_client,
                embedder=embedder,
            )
            await self._graphiti.build_indices_and_constraints()
            _log.info("graphiti.ready", neo4j_url=neo4j_url)
            return self._graphiti

        except ImportError:
            _log.warning(
                "graphiti.not_installed",
                hint="pip install graphiti-core",
            )
            return None
        except Exception as exc:
            _log.warning("graphiti.init_failed", error=str(exc)[:300])
            return None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        center_node_uuid: str | None = None,
        num_results: int = 10,
    ) -> list[dict]:
        """Semantic + temporal search over the knowledge graph.

        Returns a list of fact dicts: {fact, source, valid_at, created_at}.
        Returns [] when Graphiti is disabled or on any error.
        """
        g = await self._get_graphiti()
        if g is None:
            return []
        try:
            results = await g.search(
                query=query,
                center_node_uuid=center_node_uuid,
                num_results=num_results,
            )
            facts = []
            for r in results:
                # graphiti-core returns EdgeResult / NodeResult objects
                fact_text = getattr(r, "fact", None) or getattr(r, "name", str(r))
                valid_at = getattr(r, "valid_at", None) or getattr(r, "created_at", None)
                facts.append(
                    {
                        "fact": str(fact_text),
                        "valid_at": str(valid_at) if valid_at else None,
                        "source": getattr(r, "source_description", None),
                    }
                )
            return facts
        except Exception as exc:
            _log.debug("graphiti.search_error", error=str(exc)[:100])
            return []

    async def add_episode(
        self,
        name: str,
        content: str,
        source_description: str = "agent_conversation",
        reference_time: datetime | None = None,
        group_id: str = "default",
    ) -> None:
        """Add a textual episode (conversation turn, webhook event, ingest fact).

        Graphiti extracts entities and relationships automatically.
        Non-blocking — designed to be called as a background task.

        Args:
            name: Short label, e.g. "Task updated: TASK-123"
            content: Full text content to extract facts from.
            source_description: Origin of the episode.
            reference_time: When the event occurred (defaults to now).
            group_id: Logical group for the episode (e.g. agent name, run_id).
        """
        g = await self._get_graphiti()
        if g is None:
            return
        # Episode worthiness gate (specs/llm_caching_memory.md Phase 4.2):
        # skip low-signal turns (greetings, status pings, bare confirmations)
        # so the KG doesn't bloat with noise that degrades retrieval quality —
        # which indirectly destabilises the memory block (more noise → different
        # search results → cache misses). Opt-in via GRAPHITI_EPISODE_FILTER=1;
        # conservative (passes anything uncertain), only for conversation turns.
        if (
            os.environ.get("GRAPHITI_EPISODE_FILTER", "0") == "1"
            and source_description == "agent_conversation"
            and not await _is_episode_worthy(content)
        ):
            _log.debug("graphiti.episode_skipped_low_signal", name=name[:60])
            return
        if reference_time is None:
            reference_time = datetime.now(tz=UTC)
        try:
            from graphiti_core.nodes import EpisodeType

            await g.add_episode(
                name=name,
                episode_body=content,
                source=EpisodeType.text,
                source_description=source_description,
                reference_time=reference_time,
                group_id=group_id,
            )
            _log.info(
                "graphiti.episode_added",
                name=name[:60],
                group_id=group_id,
            )
        except Exception as exc:
            _log.warning("graphiti.add_episode_failed", name=name[:60], error=str(exc)[:200])

    async def close(self) -> None:
        """Close the Neo4j connection pool.  Call on shutdown."""
        if self._graphiti is not None:
            try:
                await self._graphiti.close()
            except Exception:
                pass


@lru_cache(maxsize=1)
def get_graphiti_client() -> GraphitiClient:
    """Return the singleton GraphitiClient."""
    return GraphitiClient()


async def search_entity_timeline(entity_name: str, query: str) -> str:
    """Search the bi-temporal KG for time-aware facts about an entity.

    Returns a formatted string for injection into agent prompts.
    Returns empty string when Graphiti is disabled.

    Example:
        "Timeline facts for 'Fracktal-ABC':\\n"
        "- [2026-05-12] Deal moved to Awaiting PO\\n"
        "- [2026-05-28] Follow-up sent after 16 days stale\\n"
    """
    full_query = f"{entity_name} {query}".strip()
    facts = await get_graphiti_client().search(full_query)
    if not facts:
        return ""
    lines = []
    for f in facts:
        valid_at = f.get("valid_at", "")
        date_prefix = f"[{valid_at[:10]}] " if valid_at else ""
        lines.append(f"- {date_prefix}{f['fact']}")
    header = f"Timeline facts for {entity_name!r}:\n"
    return header + "\n".join(lines)


async def _is_episode_worthy(content: str) -> bool:
    """Return True if *content* is worth recording in the knowledge graph.

    A cheap tier1 gate (specs Phase 4.2): keep exchanges that carry a named
    entity, date, commitment, or decision; drop greetings / status pings /
    bare confirmations. Conservative — returns True on any error or ambiguity
    so the worst case is the pre-filter behaviour (record everything).
    """
    text = (content or "").strip()
    if len(text) < 20:
        return False  # too short to carry a durable fact
    try:
        from acb_llm import LLMTier, complete

        verdict = await complete(
            tier=LLMTier.TIER_1,
            temperature=0.0,
            max_tokens=4,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You gate a knowledge graph. Reply with exactly YES if "
                        "the exchange contains a named entity, date, commitment, "
                        "decision, or fact worth remembering long-term; reply NO "
                        "for greetings, small talk, status pings, or bare "
                        "confirmations. Reply YES or NO only."
                    ),
                },
                {"role": "user", "content": text[:2000]},
            ],
            # Deterministic gate → good candidate for the exact-match cache.
            enable_litellm_cache=True,
        )
        return "no" not in verdict.strip().lower()[:4]
    except Exception as exc:
        _log.debug("graphiti.worthiness_gate_failed", error=str(exc)[:120])
        return True


async def add_episode(
    name: str,
    content: str,
    source_description: str = "agent_conversation",
    reference_time: datetime | None = None,
    group_id: str = "default",
) -> None:
    """Module-level shortcut for get_graphiti_client().add_episode(...)."""
    await get_graphiti_client().add_episode(
        name=name,
        content=content,
        source_description=source_description,
        reference_time=reference_time,
        group_id=group_id,
    )
