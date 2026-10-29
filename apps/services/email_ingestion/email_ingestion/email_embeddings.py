"""Email semantic search (Phase 2) — embed messages for hybrid vector ranking.

Runs in the same background sweep as the body backfill: after each account's
sync, a BOUNDED batch of not-yet-embedded messages is embedded through the embed
door of Knowledge (``gateway.kb_index.embed``, the Router on ``tier-embed``, KG-2)
and stored in email_embeddings (migration 73). The /email/search hybrid path then
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


#: The candidate predicate: messages with a body but no current embedding, OR
#: whose stored embedding was made from a different content_hash (body edited
#: or backfilled). ``select_pending_embeddings`` and ``count_pending`` share it,
#: so the estimate of the backfill counts what the backfill selects.
_PENDING_FROM_WHERE = """FROM email_messages em
             LEFT JOIN email_embeddings ee ON ee.message_id = em.id
            WHERE em.account_id = :aid
              AND em.body_text IS NOT NULL AND em.body_text <> ''
              AND (ee.message_id IS NULL
                   OR ee.content_hash <> encode(
                        sha256(convert_to(
                            coalesce(em.subject,'') || E'\\n\\n'
                            || coalesce(em.body_text,''), 'UTF8')), 'hex'))"""


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


async def _embed_batch(
    texts: list[str], model: str,
) -> list[list[float] | None] | None:
    """Embed a batch through the embed door of Knowledge (KG-2, D128).

    The door (``gateway.kb_index.embed.embed``) asks the Router on
    ``tier-embed``. It writes a ``usage_event`` row and draws no credit. It
    ignores *model*: the Router picks the model, and ``EMBED_MODEL`` is what
    the stored row records. The door never raises. It answers ``None`` for
    each text it could not embed. This function returns one entry for each
    input text, or ``None`` when the whole call fails (the caller skips this
    tick and never fails the sync).

    The import of the door is lazy. ``email_ingestion`` is the lower layer
    and its modules import no ``gateway`` at load time. The scheduler holds
    that rule (``tests/unit/test_email_layering.py``).
    """
    try:
        # The alias names the leaf for the llm_slot fence
        # (tests/unit/test_email_llm_cap.py, MODEL_CALLEES).
        from gateway.kb_index.embed import embed as embed_on_door

        # EM-T4b: inside the automation scope (the cleanup backfill) the
        # cap and the daily budget bind the embed. Elsewhere this is a no-op.
        # The slot holds the leaf call and nothing else.
        async with llm_slot():
            vectors = await embed_on_door(texts)
    except Exception as exc:
        logger.warning("email_embed.batch_failed model=%s err=%s",
                       model, type(exc).__name__)
        return None
    if len(vectors) != len(texts):
        logger.warning("email_embed.parse_failed err=count_mismatch")
        return None
    if not any(v is not None for v in vectors):
        logger.warning("email_embed.batch_failed model=%s err=no_vector", model)
        return None
    return list(vectors)


async def embed_query(query: str) -> list[float] | None:
    """Embed a single search query with the configured email embedding model, for
    the hybrid search path. Returns the vector, or None if semantic search is off
    or the embed fails (caller falls back to pure lexical)."""
    from acb_common.settings import get_settings
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
    #: The mailbox owner, read from ``email_accounts`` (never from mail). The
    #: model step binds it as the member of the run (H-152).
    owner: str | None = None


def _owner_of(rows: Sequence[Any]) -> str | None:
    """The mailbox owner of the rows (one account), or ``None``."""
    owner = str(getattr(rows[0], "owner", None) or "").strip()
    return owner or None


async def select_pending_embeddings(
    db: Any, account_id: str, *, batch: int = DEFAULT_BATCH,
    force: bool = False,
) -> PendingEmbeddings | None:
    """The read step: up to ``batch`` of the account's messages that have no
    embedding yet (or whose body changed since last embed).

    Returns ``None`` when semantic search is disabled — with no statement —
    or when no message waits. Takes the caller's session, opens none and never
    commits.

    ``force=True`` skips the flag. Only ``scripts/backfill_email_embeddings.py``
    passes it, for the orgs that the owner names (KG-2). The flag is GLOBAL,
    so the backfill must not need it. The default flow is unchanged."""
    from acb_common.settings import get_settings
    settings = get_settings()
    if not force and not settings.email_semantic_search_enabled:
        return None
    # The door ignores the configured name and the Router picks the model, so
    # the row records the model of the door (KG-2). Lazy: lower layer.
    from gateway.kb_index.embed import EMBED_MODEL as model

    # Candidates: messages with a body but no current embedding, OR whose stored
    # embedding was made from a different content_hash (body edited/backfilled).
    rows = (await db.execute(text(
        """SELECT em.id, em.subject, em.body_text,
                  (SELECT ea.user_id FROM email_accounts ea
                    WHERE ea.id = em.account_id) AS owner
           """ + _PENDING_FROM_WHERE + """
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
        owner=_owner_of(rows),
    )


async def count_pending(db: Any, account_id: str) -> tuple[int, int]:
    """How many messages wait, and the token estimate of their text.

    For ``--estimate`` of the backfill. The estimate is ``min(chars, 8000) / 4``
    for each message, the same cap as ``_embed_text``. It sends nothing and
    needs no flag. Takes the caller's session, opens none and never commits."""
    row = (await db.execute(text(
        "SELECT count(*) AS n, coalesce(sum(least(length(coalesce(em.subject,'')"
        " || E'\\n\\n' || coalesce(em.body_text,'')), :cap)), 0) AS chars "
        + _PENDING_FROM_WHERE),
        {"aid": account_id, "cap": _MAX_EMBED_CHARS},
    )).fetchone()
    n = int(row.n) if row is not None else 0
    chars = int(row.chars) if row is not None else 0
    return n, chars // 4


async def compute_embeddings(
    pending: PendingEmbeddings,
) -> list[list[float] | None] | None:
    """The model step: one vector per pending message, through ``_embed_batch``.

    Takes NO session. The caller holds none open across the model call
    (EM-T4a-1). Returns ``None`` when the call fails or answers the wrong
    count, and the caller then writes nothing this tick."""
    # H-152: a background job binds the member who owns the mailbox, or the
    # Router refuses the call. ``app="email"`` makes the usage rows separable.
    from acb_common import job_member_scope

    with job_member_scope(pending.owner, app="email"):
        vectors = await _embed_batch(list(pending.texts), pending.model)
    if vectors is None or len(vectors) != len(pending.message_ids):
        return None
    return vectors


async def write_embeddings(
    db: Any, account_id: str, pending: PendingEmbeddings,
    vectors: Sequence[Sequence[float] | None],
) -> int:
    """The write step: upsert one embedding per message. Returns how many it
    wrote.

    Takes the caller's session, opens none and never commits. The seam commits
    when the caller's ``tenant_session`` block exits, so each INSERT runs under
    the tenant binding."""
    embedded = 0
    model = pending.model
    for mid, vec, h in zip(pending.message_ids, vectors, pending.hashes,
                           strict=False):
        if vec is None:
            continue  # the door gave no vector: the message waits for a later tick
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
             "model": model, "hash": h},
        )
        embedded += 1

    if embedded:
        logger.info("email_embed.done account=%s embedded=%d", account_id,
                    embedded)
    return embedded
