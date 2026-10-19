"""``GET /knowledge/search`` — the index, for a member (WS-61 KB-3).

Spec: ``project-docs/specs/knowledge_base.md`` §6.3.

    GET /knowledge/search?q=&space=&k=&hybrid=

It answers ``{query, hits, degraded}``. Each hit is one chunk of a published
page the caller can see: ``page_id``, ``space_id``, ``space_name``,
``title``, ``heading_path``, ``text`` (at most 400 characters), ``score``,
``updated_at``, ``href`` and ``icon`` (the page icon of KB-2f, or None). The
company retrieval maps each hit to its own ``Hit`` and never reads ``icon``.
The route sits on the Knowledge router, so it
answers 404 unless ``KNOWLEDGE_ENABLED`` and ``KNOWLEDGE_ORGS`` allow the
caller's org, then 403 without ``feature:knowledge``.

The query's vector is made BEFORE the session opens (§6.4). ``hybrid=false``
asks for the lexical half only, with no call to the embed door. The command
bar asks for that, so a keystroke costs no embed. ``GET /search/company``
asks for ``hybrid`` when ``KNOWLEDGE_EMBED`` names the org (KB-3c, §20.4):
one embedding call, and no chat model. The member and the tenant come from
the session, never from the query (R5, R11).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router

#: The words of a search, in characters.
MIN_Q, MAX_Q = 2, 300

#: The hits of one answer.
DEFAULT_K, MAX_K = 8, 20


@router.get("/search")
async def search_knowledge(
    q: str = Query(..., max_length=MAX_Q),
    space: str | None = Query(None, max_length=64, description="A space id"),
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    hybrid: bool = Query(True, description="False: the lexical half only, no model"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The chunks of the visible published pages that match *q*, fused."""
    return await search_pages(q=q, space=space, k=k, hybrid=hybrid, user=user)


async def search_pages(
    *, q: str, space: str | None, k: int, hybrid: bool, user: UserContext,
    reviewed: bool = False,
) -> dict[str, Any]:
    """The body of :func:`search_knowledge`, for the route and one source.

    *reviewed* is not a query parameter of the route (WS-61 CX-1,
    ``company_context.md`` §4.2). Only the knowledge source of
    ``GET /search/company`` sets it, with ``CONTEXT_RECENCY`` on. The fusion
    then lowers a page past its review date in place of the age decay. Each
    hit also carries ``review_at``. ``False`` gives the answer of KB-3.
    """
    # The module is read at call time: `kb_index.query` imports this package.
    from gateway.kb_index import query as kb_query

    words = " ".join(q.split())
    if not MIN_Q <= len(words) <= MAX_Q:
        raise HTTPException(
            status_code=422, detail=f"Give {MIN_Q} to {MAX_Q} characters to find.",
        )
    spaces = None
    if space is not None:
        sid = core.parse_id(space)
        if sid is None:
            raise HTTPException(status_code=422, detail="'space' is a space id.")
        spaces = [sid]
    org = core.org_of(user)
    # Before the session (§6.4): no connection waits on the embed door.
    vector = await kb_query.embed_query(org, words) if hybrid else None
    options = {"reviewed": True} if reviewed else {}
    async with core._tenant_session() as db:
        answer = await kb_query.query(
            db, user, words, k=k, spaces=spaces, vector=vector, **options,
        )
    return {"query": words, "hits": answer["hits"], "degraded": answer["degraded"]}
