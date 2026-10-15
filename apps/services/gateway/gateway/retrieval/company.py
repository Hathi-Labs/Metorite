"""GET /search/company — one question to every app (WS-61 KR-1, ``knowledge_base.md`` §20.4).

One route, for agents and for later consumers. It is not a page a member
opens. It asks each source of :data:`gateway.retrieval.sources.REGISTRY` and
answers ``{query, hits, skipped}``. No model runs.

* **The member comes from the session.** The route takes ``q``, ``apps`` and
  ``k`` and nothing else, so no tenant and no member can come from input
  (R5, R11). Each source passes this request's member to its app.
* **Each source checks its own gate.** The route adds no visibility rule of
  its own. A member without an app gets nothing from it.
* **One source at a time**, never at once, because the pool budget is small
  (``routes/shell/search.py`` records why). Each source has
  :data:`SOURCE_TIMEOUT_S`, and the whole answer has :data:`TOTAL_BUDGET_S`.
  A source that fails, refuses or runs out of time is left out and named in
  ``skipped``. It never fails the call.
* **Fusion by rank.** The scores of two apps do not compare, so the answer
  takes every app's first hit, then every app's second. An app named in
  ``apps`` goes first, in the order named.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query

from gateway.retrieval.sources import REGISTRY, Hit, holds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

#: Seconds one source may take.
SOURCE_TIMEOUT_S = 1.5
#: Seconds for the whole answer, all sources together.
TOTAL_BUDGET_S = 4.0
#: The length of the words, after the spaces are folded.
MIN_CHARS = 2
MAX_CHARS = 300
#: Hits for each app.
DEFAULT_K = 5
MAX_K = 10


def _order(apps: str | None) -> list[str]:
    """The source keys to ask, the named ones first. An unknown key is a 422."""
    named = [a.strip().lower() for a in (apps or "").split(",") if a.strip()]
    unknown = [a for a in named if a not in REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown app {unknown[0]!r}. One of: {', '.join(REGISTRY)}.",
        )
    first = list(dict.fromkeys(named))
    return first + [key for key in REGISTRY if key not in first]


def _interleave(order: list[str], found: dict[str, list[Hit]]) -> list[Hit]:
    """Every app's first hit, then every app's second, in *order*."""
    out: list[Hit] = []
    depth = max((len(h) for h in found.values()), default=0)
    for rank in range(depth):
        for key in order:
            hits = found.get(key) or []
            if rank < len(hits):
                out.append(hits[rank])
    return out


@router.get("/company")
async def company_search(
    q: str = Query(..., min_length=MIN_CHARS, max_length=MAX_CHARS),
    apps: str | None = Query(None, max_length=200),
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Records that match the words, from every app this member holds."""
    if not user.email:
        raise HTTPException(status_code=401, detail="Authentication required")
    words = " ".join((q or "").split())
    if not MIN_CHARS <= len(words) <= MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Give {MIN_CHARS} to {MAX_CHARS} characters to find.",
        )
    per_app = max(1, min(int(k), MAX_K))
    # An app the member does not hold is not asked, and it is not "skipped":
    # the member has no such app. Each source checks the feature again.
    order = [key for key in _order(apps) if holds(user, REGISTRY[key].feature)]

    found: dict[str, list[Hit]] = {}
    skipped: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_BUDGET_S
    for key in order:
        left = deadline - loop.time()
        if left <= 0.05:
            skipped.append(key)
            continue
        try:
            hits = await asyncio.wait_for(
                REGISTRY[key].find(user, words, per_app), min(SOURCE_TIMEOUT_S, left),
            )
        except HTTPException:
            # The app's own refusal: a query too short for it, an access rule.
            skipped.append(key)
            continue
        except TimeoutError:
            logger.info("search.company source timed out", extra={"source": key})
            skipped.append(key)
            continue
        except Exception as exc:
            # The error's TYPE only. A database error's text can carry the
            # query's parameters, which hold the member's words and address.
            logger.warning(
                "search.company source failed",
                extra={"source": key, "error": type(exc).__name__},
            )
            skipped.append(key)
            continue
        found[key] = list(hits)[:per_app]

    return {
        "query": words,
        "hits": [h.as_dict() for h in _interleave(order, found)],
        "skipped": skipped,
    }
