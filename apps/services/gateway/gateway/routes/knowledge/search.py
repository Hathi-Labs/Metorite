"""``GET /knowledge/search`` — the index, for a member (WS-61 KB-3).

Spec: ``project-docs/specs/knowledge_base.md`` §6.3.

    GET /knowledge/search?q=&space=&k=&hybrid=&author=&updated_after=&title_only=

It answers ``{query, terms, hits, degraded}``. ``terms`` are the plain words
of the query without its operators, for the client to mark in a snippet
(KB-3d). The gateway returns no HTML.

KB-3d adds three filters on top of the one visibility clause. ``author`` is
a page's ``author_email`` (case ignored). ``updated_after`` is an ISO date,
and a page's ``updated_at`` must be on or after its midnight UTC.
``title_only`` matches the title and skips the semantic half. None of them
grants access, and each default leaves the answer as it was.

 Each hit is one chunk of a published
page the caller can see: ``page_id``, ``space_id``, ``space_name``,
``title``, ``heading_path``, ``text`` (at most 400 characters), ``score``,
``updated_at``, ``author_email``, ``href`` and ``icon`` (the page icon of KB-2f, or None). The
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

import re
from datetime import UTC, date, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router

#: The words of a search, in characters.
MIN_Q, MAX_Q = 2, 300

#: The hits of one answer.
DEFAULT_K, MAX_K = 8, 20

#: The longest email address (RFC 5321), for ``author``.
MAX_AUTHOR = 320

_QUOTES = re.compile(r"[\"\u201c\u201d]")


def query_terms(words: str) -> list[str]:
    """The plain words of *words*, without the operators of a web search.

    Quotes go, a word with a leading ``-`` goes, and ``or`` goes. The order
    of first use stays, and a repeat goes. The client marks these words.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in _QUOTES.sub(" ", words).split():
        if word.startswith("-") or word.lower() in ("or", "and") or len(word) < 2:
            continue
        if word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return out


@router.get("/search")
async def search_knowledge(
    q: str = Query(..., max_length=MAX_Q),
    space: str | None = Query(None, max_length=64, description="A space id"),
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    hybrid: bool = Query(True, description="False: the lexical half only, no model"),
    author: str | None = Query(
        None, max_length=MAX_AUTHOR, description="The email of the page's author",
    ),
    updated_after: str | None = Query(
        None, max_length=32, description="An ISO date: pages updated on or after it",
    ),
    title_only: bool = Query(False, description="Match the title only, no semantic half"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The chunks of the visible published pages that match *q*, fused."""
    return await search_pages(
        q=q, space=space, k=k, hybrid=hybrid, user=user, author=author,
        updated_after=updated_after, title_only=title_only,
    )


def _after(value: str | None) -> datetime | None:
    """The instant of an ISO date or datetime, as UTC, or a 422."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="'updated_after' is an ISO date.",
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def search_pages(
    *, q: str, space: str | None, k: int, hybrid: bool, user: UserContext,
    reviewed: bool = False, author: str | None = None,
    updated_after: str | None = None, title_only: bool = False,
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
    since = _after(updated_after)
    who = (author or "").strip().lower() or None
    org = core.org_of(user)
    # Before the session (§6.4): no connection waits on the embed door. A
    # title-only search has no semantic half, so it makes no embed call.
    vector = (
        await kb_query.embed_query(org, words) if hybrid and not title_only else None
    )
    options: dict[str, Any] = {"reviewed": True} if reviewed else {}
    if who is not None:
        options["author"] = who
    if since is not None:
        options["updated_after"] = since
    if title_only:
        options["title_only"] = True
    async with core._tenant_session() as db:
        answer = await kb_query.query(
            db, user, words, k=k, spaces=spaces, vector=vector, **options,
        )
    return {
        "query": words, "terms": query_terms(words),
        "hits": answer["hits"], "degraded": answer["degraded"],
    }
