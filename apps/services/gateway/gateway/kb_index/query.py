"""Reading the index (WS-61 KB-3, ``knowledge_base.md`` §6.3).

:func:`query` runs two reads over the member's visible published pages and
fuses them:

1. **Lexical.** ``websearch_to_tsquery('english', q)`` against ``tsv``,
   ranked by ``ts_rank_cd``. This half finds ``ZOHO-4471``, an invoice
   number and an error string, which the vector half cannot.
2. **Semantic.** The cosine distance of the query's vector to each chunk
   that has one. This half finds the paraphrase.
3. **Fusion.** Reciprocal rank fusion with k = :data:`RRF_K`, then a small
   age decay from ``valid_from``, then at most :data:`PER_PAGE` chunks for
   each page, so one long page does not fill the list.

**The review factor** (WS-61 CX-1, ``company_context.md`` §4.2). With
``reviewed=True``, a published page takes :data:`REVIEW_PASSED` when its
``review_at`` instant has passed, and 1.0 in every other case, in place of the age
decay. Only ``GET /search/company`` asks for it, with ``CONTEXT_RECENCY``
on. ``/knowledge/search`` and the command bar keep the age decay.

**The vector comes first.** :func:`embed_query` runs BEFORE the caller opens
a session, with a limit of :data:`QUERY_EMBED_TIMEOUT_S`, and only when
``KNOWLEDGE_EMBED`` names the org. A query with no vector returns the
lexical half and says so in ``degraded``. The lexical half never fails
because the vector half did.

**The one visibility rule.** Each read of ``kb_chunks`` joins
``kb_pages p ON p.id = c.source_id AND p.state = 'published'`` and puts in
``core.visible_pages_clause(user, "p")``. A draft is never found, and a page
the member cannot see is never found.
``tests/unit/test_kb_visibility_one_seam.py`` fails on a read that does not.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from acb_auth import UserContext
from acb_common import get_logger
from sqlalchemy import text

from gateway.kb_index import embed as embed_door
from gateway.kb_index.index import vector_literal
from gateway.routes.knowledge import core
from gateway.routes.knowledge.flags import knowledge_embed_allowed

_log = get_logger("gateway.kb_index.query")

#: The constant of reciprocal rank fusion (§6.3).
RRF_K = 60

#: The age decay: a score falls by this share for each year of age.
AGE_WEIGHT = 0.1

#: The factor of a published page whose ``review_at`` has passed
#: (``company_context.md`` §4.2). A page with a later or no review date
#: takes 1.0.
REVIEW_PASSED = 0.7

#: The most chunks of one page in an answer (§6.3).
PER_PAGE = 2

#: Seconds the query's embed may take (§6.4).
QUERY_EMBED_TIMEOUT_S = 0.8

#: The most characters of chunk text in a hit.
TEXT_CHARS = 400

#: The kinds of source that the index can answer today. ``file`` and
#: ``document`` arrive with KB-2a and KB-7.
KINDS: tuple[str, ...] = ("page",)

_SPACE = re.compile(r"\s+")


async def embed_query(organization_id: str | None, q: str) -> list[float] | None:
    """The vector of *q*, or ``None``. Call it before a session is open.

    ``None`` when ``KNOWLEDGE_EMBED`` does not name the org (no call), when
    the door is down, and when it takes more than
    :data:`QUERY_EMBED_TIMEOUT_S`.
    """
    words = " ".join(str(q or "").split())
    if not words or not knowledge_embed_allowed(organization_id):
        return None
    try:
        vectors = await asyncio.wait_for(
            embed_door.embed([words], org=organization_id, timeout=QUERY_EMBED_TIMEOUT_S),
            timeout=QUERY_EMBED_TIMEOUT_S,
        )
    except Exception as exc:  # a timeout is an answer: degraded
        _log.warning("knowledge.query_embed_failed", error=type(exc).__name__)
        return None
    return vectors[0] if vectors else None


def _age_years(valid_from: Any, today: date) -> float:
    if isinstance(valid_from, datetime):
        valid_from = valid_from.date()
    if not isinstance(valid_from, date):
        return 0.0
    return max(0, (today - valid_from).days) / 365.25


def _utc_instant(value: Any) -> datetime | None:
    """An aware instant, or ``None``. A date is its midnight UTC, and a naive
    instant is read as UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


def review_factor(review_at: Any, now: datetime) -> float:
    """:data:`REVIEW_PASSED` when *review_at* is at or before *now*, else 1.0.

    The two compare as instants, so the time zone of the box plays no part.
    An empty review date is 1.0: the page is current until someone sets a
    date and the date passes (``company_context.md`` §4.2).
    """
    due = _utc_instant(review_at)
    if due is not None and due <= now:
        return REVIEW_PASSED
    return 1.0


def fuse(
    lexical: Sequence[dict[str, Any]],
    semantic: Sequence[dict[str, Any]],
    *,
    k: int,
    today: date,
    rrf_k: int = RRF_K,
    per_page: int = PER_PAGE,
    age_weight: float = AGE_WEIGHT,
    reviewed: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion of two ranked lists, then the age decay and the cap.

    Each row has ``chunk_id``, ``page_id`` and ``valid_from``. A chunk scores
    ``1 / (rrf_k + rank)`` in each list it is in, with the rank from 1. The
    sum falls by ``1 / (1 + age_weight * years)``. A tie goes to the lower
    ``chunk_id``, so the order is stable. Pure: no database, no clock.

    *reviewed* (CX-1) puts :func:`review_factor` of the row's ``review_at``
    in place of the age decay, for a page. *now* is the instant it compares
    with, and its default is the midnight UTC of *today*. A row of another kind keeps the
    age decay, which is the rule of a source document (KB-7, §4.2).
    """
    scores: dict[Any, float] = {}
    rows: dict[Any, dict[str, Any]] = {}
    for ranked in (lexical, semantic):
        for rank, row in enumerate(ranked, start=1):
            cid = row["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            rows.setdefault(cid, row)
    clock = now or datetime(today.year, today.month, today.day, tzinfo=UTC)
    for cid, row in rows.items():
        if reviewed and row.get("source_kind", "page") == "page":
            scores[cid] *= review_factor(row.get("review_at"), clock)
            continue
        scores[cid] /= 1.0 + age_weight * _age_years(row.get("valid_from"), today)
    order = sorted(rows, key=lambda cid: (-scores[cid], cid))
    out: list[dict[str, Any]] = []
    per: dict[str, int] = {}
    for cid in order:
        page = str(rows[cid]["page_id"])
        if per.get(page, 0) >= per_page:
            continue
        per[page] = per.get(page, 0) + 1
        out.append({**rows[cid], "score": scores[cid]})
        if len(out) >= k:
            break
    return out


def _clip(value: Any) -> str:
    flat = _SPACE.sub(" ", str(value or "")).strip()
    if len(flat) <= TEXT_CHARS:
        return flat
    return flat[: TEXT_CHARS - 1].rstrip() + "…"


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _hit(row: dict[str, Any], *, reviewed: bool = False) -> dict[str, Any]:
    """One hit. *reviewed* adds ``review_at`` (CX-1). Off, the KB-3 keys.

    A row read through :data:`_COLUMNS` also gives ``icon``, the page icon
    of KB-2f, or None.
    """
    page_id = str(row["page_id"])
    hit = {
        "page_id": page_id,
        "space_id": str(row["space_id"]),
        "space_name": row["space_name"],
        "title": row["title"],
        "heading_path": row["heading_path"],
        "text": _clip(row["raw"]),
        "score": round(float(row["score"]), 6),
        "updated_at": _iso(row["updated_at"]),
        "href": f"/knowledge/pages/{page_id}",
    }
    if reviewed:
        hit["review_at"] = _iso(row.get("review_at"))
    if "icon" in row:
        # KB-2f: the page icon, for `GET /knowledge/search` and its view. The
        # company retrieval builds its own `Hit` and never reads this key.
        hit["icon"] = row["icon"]
    if "author_email" in row:
        # KB-3d: the page author, for the Creator chip of the Search view.
        hit["author_email"] = row["author_email"]
    return hit


#: The columns each half reads. ``p`` is the visible published page.
#: ``p.review_at`` and ``c.source_kind`` feed the review factor of CX-1. A
#: row that is not a page keeps the age decay (KB-7).
_COLUMNS = (
    "c.id AS chunk_id, p.id AS page_id, p.space_id, s.name AS space_name, "
    "p.title, c.heading_path, c.raw, c.valid_from, p.updated_at, p.review_at, "
    "c.source_kind, p.icon, p.author_email"
)


async def query(
    db: Any,
    user: UserContext,
    q: str,
    k: int = 8,
    spaces: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    *,
    vector: list[float] | None = None,
    today: date | None = None,
    reviewed: bool = False,
    author: str | None = None,
    updated_after: datetime | None = None,
    title_only: bool = False,
) -> dict[str, Any]:
    """The fused hits for *q* over the member's visible published pages.

    *vector* is the result of :func:`embed_query`, made before *db* was
    opened. ``None`` gives the lexical half only, with ``degraded`` true.
    *author* (lower case), *updated_after* and *title_only* are the filters of
    KB-3d. They add to the visibility clause and never replace it. With
    *title_only* the lexical half matches the page title and the semantic
    half does not run.
    *reviewed* applies the review factor of CX-1 in the fusion. It returns
    ``{"hits": [...], "degraded": bool}``.
    """
    degraded = vector is None
    words = " ".join(str(q or "").split())
    if not words or (kinds is not None and not set(kinds) & set(KINDS)):
        return {"hits": [], "degraded": degraded}
    pool = max(int(k) * 4, 20)
    extra = ""
    params: dict[str, Any] = {"q": words, "n": pool}
    if spaces:
        extra = " AND p.space_id = ANY(CAST(:spaces AS uuid[]))"
        params["spaces"] = [str(s) for s in spaces]
    if author:
        extra += " AND lower(p.author_email) = :f_author"
        params["f_author"] = author
    if updated_after is not None:
        extra += " AND p.updated_at >= :f_after"
        params["f_after"] = updated_after
    if title_only:
        match = "to_tsvector('english', p.title) @@ websearch_to_tsquery('english', :q)"
        rank = "ts_rank_cd(to_tsvector('english', p.title), websearch_to_tsquery('english', :q))"
    else:
        match = "c.tsv @@ websearch_to_tsquery('english', :q)"
        rank = "ts_rank_cd(c.tsv, websearch_to_tsquery('english', :q))"

    vis, vparams = core.visible_pages_clause(user, "p")
    lexical = (await db.execute(
        text(f"SELECT {_COLUMNS}, "
             f"{rank} AS rank "
             "FROM kb_chunks c "
             "JOIN kb_pages p ON p.id = c.source_id AND p.state = 'published' "
             "JOIN kb_spaces s ON s.id = p.space_id "
             "AND s.organization_id = p.organization_id "
             "WHERE c.organization_id = CAST(:kb_org AS uuid) "
             f"AND c.source_kind = 'page' AND {vis}{extra} "
             f"AND {match} "
             "ORDER BY rank DESC, c.id LIMIT :n"),
        {**vparams, **params},
    )).mappings().all()

    semantic: list[Any] = []
    if vector is not None and not title_only:
        # RLS and the visibility clause filter AFTER the HNSW scan, which
        # returns about ef_search rows. An iterative scan keeps reading until
        # the LIMIT is met, so a small org keeps its recall (pgvector 0.8,
        # production has 0.8.2). SET LOCAL ends with the transaction.
        await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        semantic = list((await db.execute(
            text(f"SELECT {_COLUMNS}, "
                 "(c.embedding <=> CAST(:vec AS vector)) AS distance "
                 "FROM kb_chunks c "
                 "JOIN kb_pages p ON p.id = c.source_id AND p.state = 'published' "
                 "JOIN kb_spaces s ON s.id = p.space_id "
                 "AND s.organization_id = p.organization_id "
                 "WHERE c.organization_id = CAST(:kb_org AS uuid) "
                 f"AND c.source_kind = 'page' AND {vis}{extra} "
                 "AND c.embedding IS NOT NULL "
                 "ORDER BY distance, c.id LIMIT :n"),
            {**vparams, **params, "vec": vector_literal(vector)},
        )).mappings().all())

    fused = fuse(
        [dict(r) for r in lexical], [dict(r) for r in semantic],
        k=max(1, int(k)), today=today or date.today(), reviewed=reviewed,
        now=datetime.now(UTC) if reviewed else None,
    )
    return {"hits": [_hit(r, reviewed=reviewed) for r in fused], "degraded": degraded}
