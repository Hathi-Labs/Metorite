"""Email semantic search (Phase 2) — embed messages for hybrid vector ranking.

Runs in the same background sweep as the body backfill: after each account's
sync, a BOUNDED batch of not-yet-embedded messages is embedded via the LiteLLM
gateway (the same /v1/embeddings path mem0 uses — no OPENAI_API_KEY needed) and
stored in email_embeddings (migration 73). The /email/search hybrid path then
blends cosine similarity with the full-text rank.

Gated by ``email_semantic_search_enabled``: when off, this is a no-op and search
stays pure lexical (Phase 1, complete on its own). content_hash skips re-embedding
a message whose body hasn't changed since last time (embeddings cost tokens).

Phase (f) of the scheduler runs three steps, so no session stays open across
the model call (WS-17 EM-T4a-1, ``email_app_master_plan.md`` §10.4.6):

1. ``select_pending_embeddings`` reads the candidates. It takes a session, and
   it returns ``None`` when semantic search is off.
2. ``compute_embeddings`` calls ``_embed_batch``. It takes NO session.
3. ``write_embeddings`` writes the vectors. It takes a session.

The read and write steps open no session and never commit. The scheduler opens
one ``tenant_session(org)`` for each, and the seam commits when the block exits.
R7: ``tests/unit/test_email_scheduler_tenancy.py``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from email_ingestion.llm_cap import llm_slot

logger = logging.getLogger(__name__)

# Messages embedded per sweep tick — bounded so the extra embedding calls never
# dominate a sync cycle; the backlog drains over successive ticks.
DEFAULT_BATCH = 32

# Cap the text sent to the embedder. Embedding models truncate at their own token
# limit anyway; a hard char cap keeps the request small and cheap. Subject + the
# head of the body carries the semantic gist for search.
_MAX_EMBED_CHARS = 8000


def _embed_text(subject: str | None, body: str | None) -> str:
    """The text we embed for a message: subject + body head. Kept in one place so
    the content_hash and the embedded text can never diverge."""
    return (f"{(subject or '').strip()}\n\n{(body or '').strip()}"
            )[:_MAX_EMBED_CHARS]


def _hash_source(subject: str | None, body: str | None) -> str:
    """The EXACT text whose sha256 is stored as content_hash.

    This must be byte-identical to what the SQL candidate predicate in
    ``select_pending_embeddings`` hashes, or a message's stored hash never matches
    the predicate and it is re-selected + re-embedded every sweep tick, burning
    tokens and never settling. So this mirrors the SQL exactly:

        coalesce(subject, '') || E'\\n\\n' || coalesce(body, '')

    In particular it does NOT ``.strip()`` — the SQL coalesces the raw column
    values, so stripping here (as an earlier version did) meant any message with
    a trailing newline in its body — i.e. most of them — hashed differently on
    the two sides and thrashed forever. The embedded text (``_embed_text``) is a
    separate, capped-and-stripped string; only the *hash source* must match SQL.
    """
    return f"{subject or ''}\n\n{body or ''}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()


async def _embed_batch(texts: list[str], model: str) -> list[list[float]] | None:
    """Embed a batch through the LiteLLM gateway (same route mem0 uses). Returns
    one vector per input text, or None on any failure (caller skips this tick —
    never fails the sync)."""
    try:
        import litellm  # noqa: PLC0415
        from acb_common.settings import get_settings  # noqa: PLC0415
        litellm.drop_params = True
        settings = get_settings()
        api_base = settings.litellm_base_url.rstrip("/") + "/v1"
        # EM-T4b: inside the automation scope (the cleanup backfill) the
        # cap and the daily budget bind the embed. Elsewhere this is a no-op.
        # The slot holds the leaf call and nothing else.
        async with llm_slot():
            resp = await litellm.aembedding(
                model=model,
                input=texts,
                api_base=api_base,
                api_key=settings.litellm_master_key,
                custom_llm_provider="openai",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("email_embed.batch_failed model=%s err=%s",
                       model, str(exc)[:160])
        return None
    # litellm returns {data: [{embedding: [...]}, ...]} preserving input order.
    try:
        data = resp["data"] if isinstance(resp, dict) else resp.data
        return [d["embedding"] if isinstance(d, dict) else d.embedding
                for d in data]
    except Exception as exc:  # noqa: BLE001
        logger.warning("email_embed.parse_failed err=%s", str(exc)[:160])
        return None


async def embed_query(query: str) -> list[float] | None:
    """Embed a single search query with the configured email embedding model, for
    the hybrid search path. Returns the vector, or None if semantic search is off
    or the embed fails (caller falls back to pure lexical)."""
    from acb_common.settings import get_settings  # noqa: PLC0415
    settings = get_settings()
    if not settings.email_semantic_search_enabled or not query.strip():
        return None
    vecs = await _embed_batch([query.strip()[:_MAX_EMBED_CHARS]],
                              settings.email_embedding_model)
    return vecs[0] if vecs else None


@dataclass(frozen=True)
class PendingEmbeddings:
    """What the read step found: one entry per message, in the same order in
    each tuple, and the model to embed them with."""

    model: str
    message_ids: tuple[str, ...]
    hashes: tuple[str, ...]
    texts: tuple[str, ...]


async def select_pending_embeddings(
    db: Any, account_id: str, *, batch: int = DEFAULT_BATCH,
) -> PendingEmbeddings | None:
    """The read step: up to ``batch`` of the account's messages that have no
    embedding yet (or whose body changed since last embed).

    Returns ``None`` when semantic search is disabled — with no statement —
    or when no message waits. Takes the caller's session, opens none and never
    commits."""
    from acb_common.settings import get_settings
    settings = get_settings()
    if not settings.email_semantic_search_enabled:
        return None
    model = settings.email_embedding_model

    # Candidates: messages with a body but no current embedding, OR whose stored
    # embedding was made from a different content_hash (body edited/backfilled).
    rows = (await db.execute(text(
        """SELECT em.id, em.subject, em.body_text
             FROM email_messages em
             LEFT JOIN email_embeddings ee ON ee.message_id = em.id
            WHERE em.account_id = :aid
              AND em.body_text IS NOT NULL AND em.body_text <> ''
              AND (ee.message_id IS NULL
                   OR ee.content_hash <> encode(
                        sha256(convert_to(
                            coalesce(em.subject,'') || E'\\n\\n'
                            || coalesce(em.body_text,''), 'UTF8')), 'hex'))
            ORDER BY em.received_at DESC NULLS LAST
            LIMIT :lim"""),
        {"aid": account_id, "lim": batch},
    )).fetchall()
    if not rows:
        return None

    # content_hash MUST match the SQL candidate predicate above byte-for-byte
    # (see _hash_source): the SQL hashes the raw, UNstripped coalesce of the FULL
    # "subject\n\nbody" — so we hash the same here, uncapped and unstripped.
    # (The embedded text below is separately capped + stripped; only the hash
    # source has to agree with SQL.)
    return PendingEmbeddings(
        model=model,
        message_ids=tuple(str(r.id) for r in rows),
        hashes=tuple(_content_hash(_hash_source(r.subject, r.body_text))
                     for r in rows),
        texts=tuple(_embed_text(r.subject, r.body_text) for r in rows),
    )


async def compute_embeddings(
    pending: PendingEmbeddings,
) -> list[list[float]] | None:
    """The model step: one vector per pending message, through ``_embed_batch``.

    Takes NO session. The caller holds none open across the model call
    (EM-T4a-1). Returns ``None`` when the call fails or answers the wrong
    count, and the caller then writes nothing this tick."""
    vectors = await _embed_batch(list(pending.texts), pending.model)
    if vectors is None or len(vectors) != len(pending.message_ids):
        return None
    return vectors


async def write_embeddings(
    db: Any, account_id: str, pending: PendingEmbeddings,
    vectors: Sequence[Sequence[float]],
) -> int:
    """The write step: upsert one embedding per message. Returns how many it
    wrote.

    Takes the caller's session, opens none and never commits. The seam commits
    when the caller's ``tenant_session`` block exits, so each INSERT runs under
    the tenant binding."""
    embedded = 0
    for mid, vec, h in zip(pending.message_ids, vectors, pending.hashes,
                           strict=False):
        # pgvector accepts the '[..]' text form for a vector literal.
        vec_literal = "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
        await db.execute(text(
            """INSERT INTO email_embeddings
                   (message_id, account_id, embedding, model, content_hash)
               VALUES (:mid, :aid, CAST(:emb AS vector), :model, :hash)
               ON CONFLICT (message_id) DO UPDATE
                   SET embedding = EXCLUDED.embedding,
                       model = EXCLUDED.model,
                       content_hash = EXCLUDED.content_hash,
                       updated_at = now()"""),
            {"mid": mid, "aid": account_id, "emb": vec_literal,
             "model": pending.model, "hash": h},
        )
        embedded += 1

    if embedded:
        logger.info("email_embed.done account=%s embedded=%d", account_id,
                    embedded)
    return embedded
