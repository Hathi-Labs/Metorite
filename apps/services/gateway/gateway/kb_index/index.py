"""Writing the index of a page (WS-61 KB-3, ``knowledge_base.md`` §6.2, §6.4).

Two steps, on two sides of the commit.

1. :func:`reindex_page` runs INSIDE the page write's transaction, on the
   write's own session. A published page gets its chunks: a chunk whose sha
   is still there keeps its row and its vector, a new chunk gets a row with a
   NULL vector, and a chunk whose sha is gone is deleted. ``space_id``
   follows the page, so a move moves the chunks. A page in any other state
   (a draft, an archived page) loses all its chunks. So the index never
   holds a draft, and an archived page leaves it in the same commit.
2. :func:`embed_pending` runs AFTER the session closes, as a FastAPI
   background task. It does the three steps of ``email_embeddings.py``:
   select the NULL-vector chunks in a short tenant session, compute with no
   session open, write in a new short session. A failed embed leaves the
   vector NULL until the next edit of the page (§6.4). The sweep for missed
   chunks is KB-7b.

This module imports nothing from ``gateway.routes`` at import time, because
``routes/knowledge/pages.py`` imports it. The tenant always comes from the
caller's resolved ``UserContext`` (R5, R11), never from the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.db import tenant_session
from gateway.kb_index import embed as embed_door
from gateway.kb_index.chunk import chunk_page, embed_text

_log = get_logger("gateway.kb_index.index")

#: The most chunks one background job embeds. A page larger than this gets
#: the rest on its next edit.
MAX_PENDING = 256

#: Delete every chunk of one page. Used for each state but ``published``.
_DELETE_PAGE_SQL = (
    "DELETE FROM kb_chunks WHERE organization_id = CAST(:org AS uuid) "
    "AND source_kind = 'page' AND source_id = CAST(:pid AS uuid)"
)

#: Delete the chunks of one page whose sha the new text no longer has.
_DELETE_GONE_SQL = (
    "DELETE FROM kb_chunks WHERE organization_id = CAST(:org AS uuid) "
    "AND source_kind = 'page' AND source_id = CAST(:pid AS uuid) "
    "AND NOT (content_sha = ANY(CAST(:shas AS text[])))"
)

#: Upsert the chunks of one page by sha. A kept chunk keeps its vector and
#: takes the new order and the page's space. A new chunk has a NULL vector.
_UPSERT_SQL = (
    "INSERT INTO kb_chunks (organization_id, space_id, source_kind, source_id, "
    "page_id, ordinal, heading_path, raw, content_sha, valid_from, metadata) "
    "SELECT CAST(:org AS uuid), CAST(:sid AS uuid), 'page', CAST(:pid AS uuid), "
    "NULL, t.ordinal, t.heading_path, t.raw, t.sha, current_date, "
    "CAST(t.meta AS jsonb) "
    "FROM unnest(CAST(:ordinals AS int[]), CAST(:paths AS text[]), "
    "CAST(:raws AS text[]), CAST(:shas AS text[]), CAST(:metas AS text[])) "
    "AS t(ordinal, heading_path, raw, sha, meta) "
    "ON CONFLICT (organization_id, source_kind, source_id, content_sha) "
    "DO UPDATE SET ordinal = EXCLUDED.ordinal, space_id = EXCLUDED.space_id, "
    "metadata = EXCLUDED.metadata"
)


async def reindex_page(db: Any, page: Any, *, organization_id: str) -> int:
    """Write or delete the chunks of *page* in the caller's transaction.

    *page* is a row the caller has just written, with ``id``, ``space_id``,
    ``state``, ``title`` and ``body_md``. It returns the count of chunks the
    page now holds: zero for a page that is not published. It calls no
    model and opens no session.
    """
    params = {"org": str(organization_id), "pid": str(page.id)}
    if page.state != "published":
        await db.execute(text(_DELETE_PAGE_SQL), params)
        return 0
    chunks = chunk_page(page.title, page.body_md)
    shas = [c.content_sha for c in chunks]
    await db.execute(text(_DELETE_GONE_SQL), {**params, "shas": shas})
    await db.execute(text(_UPSERT_SQL), {
        **params,
        "sid": str(page.space_id),
        "ordinals": [c.ordinal for c in chunks],
        "paths": [c.heading_path for c in chunks],
        "raws": [c.raw for c in chunks],
        "shas": shas,
        "metas": [c.metadata_json() for c in chunks],
    })
    return len(chunks)


# ── After the commit: the three steps ───────────────────────────────────────

@dataclass(frozen=True)
class Pending:
    """One chunk that has no vector yet."""

    id: int
    content_sha: str
    text: str


async def select_pending(organization_id: str, page_id: str) -> list[Pending]:
    """Step 1: the chunks of one page with a NULL vector, in a short session.

    An integrity read, listed in the visibility fence: it runs for the
    indexer, with no member, and it returns text to the embed door only,
    never to a person.
    """
    async with tenant_session(organization_id) as db:
        rows = (await db.execute(
            text("SELECT c.id, c.content_sha, c.heading_path, c.raw "
                 "FROM kb_chunks c WHERE c.organization_id = CAST(:org AS uuid) "
                 "AND c.source_kind = 'page' AND c.source_id = CAST(:pid AS uuid) "
                 "AND c.embedding IS NULL ORDER BY c.ordinal LIMIT :n"),
            {"org": str(organization_id), "pid": str(page_id), "n": MAX_PENDING},
        )).fetchall()
    return [Pending(id=int(r.id), content_sha=r.content_sha,
                    text=embed_text(r.heading_path, r.raw)) for r in rows]


def vector_literal(vec: list[float]) -> str:
    """A vector as the text pgvector reads: ``[0.1,0.2,…]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def write_vectors(
    organization_id: str, rows: list[tuple[Pending, list[float]]],
) -> int:
    """Step 3: write the vectors in a new short session.

    A row whose text changed after step 1 has a new sha, so its old row is
    gone and the write matches nothing. A row that has a vector already
    keeps it.
    """
    written = 0
    async with tenant_session(organization_id) as db:
        for pending, vec in rows:
            result = await db.execute(
                text("UPDATE kb_chunks SET embedding = CAST(:vec AS vector) "
                     "WHERE organization_id = CAST(:org AS uuid) AND id = :id "
                     "AND content_sha = :sha AND embedding IS NULL"),
                {"vec": vector_literal(vec), "org": str(organization_id),
                 "id": pending.id, "sha": pending.content_sha},
            )
            written += int(result.rowcount or 0)
    return written


async def embed_pending(organization_id: str, page_id: str) -> int:
    """Embed the NULL-vector chunks of one page. A background task.

    It returns the count of vectors written. It never raises: a failure is
    one log line, and the chunks wait for the next edit. With
    ``KNOWLEDGE_EMBED`` off for the org it does nothing and calls no door.
    """
    from gateway.routes.knowledge.flags import knowledge_embed_allowed

    try:
        if not knowledge_embed_allowed(organization_id):
            return 0
        pending = await select_pending(organization_id, page_id)
        if not pending:
            return 0
        # Step 2: no session is open here.
        vectors = await embed_door.embed([p.text for p in pending], org=organization_id)
        rows = [(p, v) for p, v in zip(pending, vectors, strict=True) if v is not None]
        if not rows:
            return 0
        return await write_vectors(organization_id, rows)
    except Exception as exc:  # noqa: BLE001 — a background job must not raise
        _log.warning("knowledge.embed_pending_failed", organization_id=organization_id,
                     error=type(exc).__name__)
        return 0
