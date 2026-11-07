"""GET /search/company — one question to every app (WS-61 KR-1, ``knowledge_base.md`` §20.4).

One route, for agents and for later consumers. It is not a page a member
opens. It asks each source of :data:`gateway.retrieval.sources.REGISTRY` and
answers ``{query, hits, skipped}``. No chat model runs.

* **Meaning, when the org has it** (WS-61 KB-3c). With ``KNOWLEDGE_EMBED``
  on for the member's org, the route asks each source with ``meaning`` for
  ``hybrid``. Today that is Knowledge alone. Its route embeds the words
  once, within 0.8 s, and answers the lexical half when the door fails. So
  one search costs at most one embedding call and no chat model.

* **The member comes from the session.** The route takes ``q``, ``apps``,
  ``k`` and ``after`` (a date, CX-1) and nothing else, so no tenant and no
  member can come from input (R5, R11). Each source passes this request's
  member to its app.
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

**Recency, behind ``CONTEXT_RECENCY``** (WS-61 CX-1, ``company_context.md``
§3, §4.1, §4.2 and §4.5). Each request reads the flag from the cached
settings, so a change of the env file needs a restart. With it on:

* each app's hits are sorted by :func:`score`, which lowers an old hit by
  the half-life of its app, and never below :data:`FLOOR`. Then the apps
  interleave as above;
* Knowledge ranks a page past its review date lower (``reviewed``), in
  place of an age decay;
* ``after`` drops each hit stamped before it. Email and WhatsApp filter in
  their own SQL. Each other app is asked for :func:`pool` hits, and the
  route filters them BEFORE it cuts the list to ``k``, so old top matches
  cannot hide a newer one;
* each hit carries ``stamp``, and the answer names the apps it
  ``searched``: each app that answered, with its switch open.

**Fusion and meaning, behind the same flag** (WS-KG KG-3b,
``knowledge_graph.md`` §9). With it on:

* the answer is ONE list, sorted by :func:`score` across every app, in place
  of :func:`_interleave`. That score is reciprocal rank fusion with k = 60,
  lowered by age. A tie goes to the app named first, then to the lower rank;
* Email also asks its vector half (``vectors``) and fuses it with its
  lexical half by the same rank sum (``kb_index.query.rrf_scores``). An org
  with no email vector gets the lexical answer, and no embed call is made;
* the route opens a turn (:mod:`gateway.retrieval.turn_vector`), so the
  query is embedded once, for every source that asks.

With it off, the route does not read ``after`` at all, not even its
length, and the answer is the one of KR-1, byte for byte.

**The neighbours, behind ``GRAPH_SEARCH_ORGS``** (WS-KG KG-6,
``knowledge_graph.md`` §5, §6 and §7). For an org that the flag names, the
route reads the edges of the first :data:`GRAPH_HITS` hits after the
fusion, in both directions, through ``gateway/kb_entities/graph.py``. Each
source then opens its own kind by id, as the member, in one call. An id that
the app does not return is the same as no edge, and it leaves no trace. A
record that is already a hit is left out, each hit keeps at most
:data:`PER_HIT`, and the items go in order until :data:`NEIGHBOUR_TOKENS`
is spent. The answer gains ``neighbours``. The step gets at most
:data:`GRAPH_TIMEOUT_S` of the time the sources left. When it runs out or
fails, the answer has no neighbours and names ``graph`` in ``skipped``.
The filters of the request narrow the neighbours too: ``apps=`` keeps the
kinds of the named sources, and with ``CONTEXT_RECENCY`` on, ``after`` and
``verified`` apply as they do to the hits. Off, the route reads no edge,
and the answer is the one before KG-6, byte for byte.

**A gap, behind ``KNOWLEDGE_GAPS``** (WS-61 KB-9a, ``knowledge_base.md``
§8.4 door 3). When Knowledge answered with zero hits, the route hands the
words to ``routes/knowledge/gaps.schedule_gap``. That function checks the
flag, the member and the skip rules, and it adds the write to the route's
``BackgroundTasks``. So the write runs after the answer, in its own tenant
session, and it never fails the search. A Knowledge source that failed or
was skipped writes no gap. The answer itself does not change.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from gateway.retrieval import turn_vector
from gateway.retrieval.sources import REGISTRY, Hit, holds, source_of_kind

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

#: The constant of the rank term of :func:`score` (§4.1).
RANK_K = 60
#: The lowest share of its score that age leaves a hit (§4.1). An old exact
#: match drops behind a new one, and stays in the list.
FLOOR = 0.35
#: The half-life of each app, in days (§4.1). An app with no entry does not
#: decay: Knowledge ranks by review state (§4.2), and a person does not age.
#: The eval of CX-2 may change these numbers, and nothing else may.
TAU_DAYS: dict[str, float] = {
    "email": 90.0,
    "whatsapp": 60.0,
    "tasks": 180.0,
    "crm": 180.0,
    "artifacts": 180.0,
    "chat": 120.0,
}


#: The hits of the fused list whose neighbours the graph step reads (§5).
GRAPH_HITS = 5
#: The most neighbours of one hit (§5 step 5).
PER_HIT = 3
#: The budget of the neighbours, in tokens (§7.1). The count is the
#: characters of the printed lines divided by 4. No tokenizer runs.
NEIGHBOUR_TOKENS = 1500
#: The characters of one token, for :data:`NEIGHBOUR_TOKENS`.
CHARS_PER_TOKEN = 4
#: The most seconds of the graph step, from what the sources left (§7.3).
GRAPH_TIMEOUT_S = 1.0
#: The name of the graph step in ``skipped``.
GRAPH_SKIP = "graph"


def graph_enabled(user: UserContext) -> bool:
    """True when ``GRAPH_SEARCH_ORGS`` names the member's org (KG-6).

    The org comes from the session's ``UserContext``. The one reader of the
    flag lives beside the edge read, and this module reads it at call time.
    """
    from gateway.kb_entities import graph

    return graph.search_allowed(user.organization_id)


def _edge_rank(provenance: str, confidence: float, stamp: str | None,
               order: int) -> tuple[int, float, float, int]:
    """The sort key of §5 step 3: EXTRACTED first, then INFERRED by
    confidence, then the newer stamp of the record, then the edge's place."""
    when = _instant(stamp)
    return (0 if provenance == "EXTRACTED" else 1, -confidence,
            -(when.timestamp() if when is not None else float("-inf")), order)


async def neighbours(
    user: UserContext, hits: list[Hit], *, stamp: bool,
    apps: list[str] | None = None, after: datetime | None = None, verified: bool = False,
) -> list[dict[str, Any]]:
    """The neighbours of the first hits, opened as the member (§5, §6).

    1. The first :data:`GRAPH_HITS` hits. 2. Their usable edges, from the
    graph's own read, to the kinds of the sources this request may add. 3.
    One ``open`` call for each kind, by the source that owns the kind. An id
    that the app does not return is dropped, and so is each kind whose app
    refuses. 4. A record that is already a hit is dropped, and a record that
    an earlier hit already took. 5. At most :data:`PER_HIT` for each hit, in
    the order of :func:`_edge_rank`. 6. Items in order until
    :data:`NEIGHBOUR_TOKENS` is spent. A hidden record leaves no count, no
    relation and no marker.

    The filters of the request apply too. *apps* (the keys named in
    ``apps=``) keeps the kinds of those sources only. ``None`` keeps every
    source. *after* drops a record stamped before it, as :func:`_kept` does
    for a hit. *verified* asks the Knowledge source for verified pages only.
    """
    from acb_skills.company_search_tools import NEIGHBOURS_HEADER, neighbour_line

    from gateway.kb_entities import graph

    first = hits[:GRAPH_HITS]
    keys = [k for k in (apps or list(REGISTRY)) if k in REGISTRY]
    kinds = [kind for key in keys for kind in REGISTRY[key].kinds]
    if not first or not kinds:
        return []
    edges = await graph.neighbour_edges(user, [(h.kind, h.id) for h in first], kinds=kinds)
    shown = {(h.kind, h.id) for h in hits}
    place = {(h.kind, h.id): i for i, h in enumerate(first)}
    # Each kind's ids in turns: every hit's best edge, then every hit's second.
    # So the cap of one `open` call (sources.MAX_OPEN) never starves a later hit.
    turns = sorted(edges, key=lambda e: (e.order, place.get((e.hit_kind, e.hit_id), 0)))
    wanted: dict[str, list[str]] = {}
    for e in turns:
        if (e.kind, e.id) not in shown and e.kind in kinds:
            wanted.setdefault(e.kind, []).append(e.id)
    opened: dict[tuple[str, str], Hit] = {}
    for kind, ids in wanted.items():
        source = source_of_kind(kind)
        if source is None:
            continue  # no reader for the kind (§3.1): it never shows
        try:
            found = await source.open(user, list(dict.fromkeys(ids)), kind=kind,
                                      verified_only=verified)
        except HTTPException:
            continue  # the app's own refusal, such as the CRM access rule
        except Exception as exc:
            logger.warning("search.company graph open failed",
                           extra={"kind": kind, "error": type(exc).__name__})
            continue
        for h in found:
            if after is None or _kept(h, after):
                opened[(kind, h.id)] = h

    taken: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for hit in first:
        mine = [e for e in edges
                if (e.hit_kind, e.hit_id) == (hit.kind, hit.id) and (e.kind, e.id) in opened]
        mine.sort(key=lambda e: _edge_rank(e.provenance, e.confidence,
                                           opened[(e.kind, e.id)].stamp, e.order))
        kept = 0
        for e in mine:
            key = (e.kind, e.id)
            if key in taken or kept >= PER_HIT:
                continue
            taken.add(key)
            kept += 1
            item = opened[key].as_dict(stamp=stamp)
            item["edge"] = {"from": f"{hit.kind}:{hit.id}", "relation": e.relation,
                            "provenance": e.provenance}
            items.append(item)

    budget = NEIGHBOUR_TOKENS * CHARS_PER_TOKEN
    spent = len(NEIGHBOURS_HEADER) + 1
    out: list[dict[str, Any]] = []
    for item in items:
        cost = len(neighbour_line(item)) + 1
        if spent + cost > budget:
            break
        spent += cost
        out.append(item)
    return out


async def _graph_step(user: UserContext, hits: list[Hit], *, stamp: bool,
                      left: float, skipped: list[str],
                      narrow: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """:func:`neighbours` inside its time (§7.3). It never raises.

    ``None`` when ``GRAPH_SEARCH_ORGS`` does not name the org: then no edge
    is read, and the answer gets no ``neighbours`` key. The step gets the
    lesser of :data:`GRAPH_TIMEOUT_S` and *left*, the time the sources left.
    When the time runs out, or the step fails, the answer has no neighbours
    and ``skipped`` names :data:`GRAPH_SKIP`. *narrow* holds the filters of
    the request for :func:`neighbours`: ``apps``, ``after`` and ``verified``.
    """
    if not graph_enabled(user):
        return None
    allowed = min(GRAPH_TIMEOUT_S, left)
    if allowed <= 0.05:
        skipped.append(GRAPH_SKIP)
        return []
    try:
        return await asyncio.wait_for(
            neighbours(user, hits, stamp=stamp, **(narrow or {})), allowed)
    except TimeoutError:
        logger.info("search.company graph timed out")
    except Exception as exc:
        # The error's TYPE only, for the reason of the sources.
        logger.warning("search.company graph failed", extra={"error": type(exc).__name__})
    skipped.append(GRAPH_SKIP)
    return []


def recency_enabled() -> bool:
    """True when ``CONTEXT_RECENCY`` is on. Default off.

    The one reader is in ``acb_skills``, so the route, the tool and the
    persona read one flag. It reads the cached settings, so a change of the
    env file takes effect after a restart.
    """
    from acb_skills.company_search_tools import context_recency_enabled

    return context_recency_enabled()


def _instant(value: str | None) -> datetime | None:
    """ISO text as an aware instant, or ``None``. A date is its midnight UTC."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _superseded(hit: Hit) -> float:
    """The factor of a record that another record replaces (§4.4).

    Always 1.0 here. CX-1b sets 0.5 for a superseded record.
    """
    return 1.0


def score(hit: Hit, rank: int, now: datetime) -> float:
    """The score of §4.1 for the hit at *rank* (from 1) of its own app.

    ``1 / (60 + rank) * max(FLOOR, exp(-age / TAU)) * SUPERSEDED``, with the
    age in days of the hit's ``stamp``. An app with no half-life, and a hit
    with no stamp, take a decay of 1.0. Pure: *now* is the clock.
    """
    decay = 1.0
    tau = TAU_DAYS.get(hit.app)
    stamped = _instant(hit.stamp)
    if tau and stamped is not None:
        age = max(0.0, (now - stamped).total_seconds() / 86400.0)
        decay = max(FLOOR, math.exp(-age / tau))
    return 1.0 / (RANK_K + rank) * decay * _superseded(hit)


def by_recency(hits: list[Hit], now: datetime) -> list[Hit]:
    """One app's hits, sorted by :func:`score`. A tie keeps the app's order."""
    scored = [(score(h, rank, now), rank, h) for rank, h in enumerate(hits, start=1)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [h for _s, _r, h in scored]


#: The most characters of ``after``, checked only with the flag on.
AFTER_MAX_CHARS = 40
#: The most hits an app is asked for when ``after`` filters after the read.
#: Each app's own search accepts it: tasks 50, CRM a page, artifacts 200,
#: and Knowledge 20, its route's own maximum.
POOL_MAX = 20


def pool(k: int) -> int:
    """The hits to ask an app for when ``after`` filters after the read."""
    return max(k, min(4 * k, POOL_MAX))


def _after(value: str | None) -> datetime | None:
    """``after`` as an aware instant. A bad value is a 422 (§4.5).

    Called only with the flag on. The route declares no constraint on the
    parameter, so with the flag off no value of it is ever refused.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if len(raw) > AFTER_MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"'after' is a date of at most {AFTER_MAX_CHARS} characters.",
        )
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="'after' is a date, for example 2026-10-01.",
        ) from None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _kept(hit: Hit, after: datetime) -> bool:
    """False when the hit's stamp is before *after*. No stamp is kept."""
    stamped = _instant(hit.stamp)
    return stamped is None or stamped >= after


def _named(apps: str | None) -> list[str]:
    """The keys named in ``apps``, in lower case, each once, in their order."""
    return list(dict.fromkeys(a.strip().lower() for a in (apps or "").split(",") if a.strip()))


def _order(apps: str | None) -> list[str]:
    """The source keys to ask, the named ones first. An unknown key is a 422."""
    named = _named(apps)
    unknown = [a for a in named if a not in REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown app {unknown[0]!r}. One of: {', '.join(REGISTRY)}.",
        )
    first = list(dict.fromkeys(named))
    return first + [key for key in REGISTRY if key not in first]


def _meaning_allowed(user: UserContext) -> bool:
    """True when ``KNOWLEDGE_EMBED`` names the member's org (KB-3c).

    The org comes from the session's ``UserContext``, never from the query.
    The flag module is read at call time: it lives under ``routes``.
    """
    from gateway.routes.knowledge import flags

    return flags.knowledge_embed_allowed(user.organization_id)


def _note_gap(background: BackgroundTasks | None, user: UserContext, words: str,
              knowledge_hits: int | None) -> None:
    """Hand a Knowledge miss to the gap write (KB-9a). It never raises.

    The gap module is read at call time: it lives under ``routes``, and it
    opens the session, so this package stays free of SQL.
    """
    if background is None or knowledge_hits != 0:
        return
    try:
        from gateway.routes.knowledge import gaps

        gaps.schedule_gap(background, user, words, knowledge_hits)
    except Exception as exc:
        logger.warning("search.company gap skipped", extra={"error": type(exc).__name__})


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


def by_fusion(order: list[str], found: dict[str, list[Hit]], now: datetime) -> list[Hit]:
    """ONE list for every app, sorted by :func:`score` (KG-3b).

    Each app's list is ranked from 1 in the order it arrived. A tie goes to
    the app that comes first in *order*, then to the lower rank. Pure.
    """
    place = {key: i for i, key in enumerate(order)}
    scored = [
        (score(h, rank, now), place.get(key, len(place)), rank, h)
        for key, hits in found.items()
        for rank, h in enumerate(hits, start=1)
    ]
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [h for _s, _p, _r, h in scored]


@router.get("/company")
async def company_search(
    q: str = Query(..., min_length=MIN_CHARS, max_length=MAX_CHARS),
    apps: str | None = Query(None, max_length=200),
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    user: UserContext = Depends(get_current_user),
    # A plain default: FastAPI checks nothing on it, so with the flag off no
    # value is refused, and a direct call that leaves it out gets None.
    after: str | None = None,
    # KB-AI-4. A plain default, read only with the flag on, as `after`. Set,
    # Knowledge answers pages with an owner and a review date in the future.
    verified: bool = False,
    # KB-9a. FastAPI gives the route's own tasks, never a request value. A
    # direct call that leaves it out gets None and records no gap.
    background: BackgroundTasks = None,  # type: ignore[assignment]
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
    hybrid = _meaning_allowed(user)
    recency = recency_enabled()
    # Off, `after` is not read at all, as before CX-1.
    since = _after(after) if recency else None
    options: dict[str, Any] = {"after": since, "reviewed": True, "vectors": True} if recency else {}
    if recency:
        turn_vector.begin_turn()
    only_verified = bool(recency and verified)
    if only_verified:
        options["verified_only"] = True

    found: dict[str, list[Hit]] = {}
    skipped: list[str] = []
    # KB-9a: the hits Knowledge answered, before `after` filters them. None
    # when the route did not ask Knowledge, or Knowledge failed.
    knowledge_hits: int | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_BUDGET_S
    for key in order:
        left = deadline - loop.time()
        if left <= 0.05:
            skipped.append(key)
            continue
        try:
            # With `after`, an app that cannot filter by date is asked for a
            # pool, and the filter runs before the cut to `per_app`.
            source = REGISTRY[key]
            ask = pool(per_app) if since is not None and not source.dated else per_app
            allowed = min(SOURCE_TIMEOUT_S, left)
            if recency:
                # KG-3b: the vector half of a source gets the true time left.
                options["budget_s"] = allowed
            hits = await asyncio.wait_for(
                source.find(user, words, ask, hybrid=hybrid, **options),
                allowed,
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
        if key == "knowledge":
            knowledge_hits = len(hits)
        if since is not None:
            hits = [h for h in hits if _kept(h, since)]
        found[key] = list(hits)[:per_app]

    # A filtered search that finds nothing says nothing about a gap (KB-AI-4).
    _note_gap(background, user, words, None if only_verified else knowledge_hits)

    ranked = _interleave(order, found) if not recency else by_fusion(
        order, found, datetime.now(UTC))
    # KG-6: off, no edge is read and the answer has no `neighbours` key.
    near = await _graph_step(
        user, ranked, stamp=recency, left=deadline - loop.time(), skipped=skipped,
        narrow={"apps": _named(apps) or None, "after": since, "verified": only_verified})

    answer: dict[str, Any] = {"query": words, "hits": [h.as_dict(stamp=recency) for h in ranked]}
    if recency:
        answer["searched"] = [a for a in order if a in found and REGISTRY[a].reaches(user)]
    if near is not None:
        answer["neighbours"] = near
    answer["skipped"] = skipped
    return answer
