"""Transport · search — reliable, ranked full-text search across ALL email.

This is the dedicated search surface (``GET /email/search``), distinct from the
folder-oriented ``/email/messages`` list:

  • It searches the WHOLE message — subject + body_text + sender name + email —
    over an FTS index whose expression matches the query exactly (migration 72),
    so body matches are found via the GIN index, not a sequential scan.
  • It ranks by relevance (``ts_rank_cd``), not just recency, so the best match
    surfaces first — with recency as the tie-break.
  • It searches ACROSS folders and (optionally) across all of the user's
    accounts by default — "search all emails" means all of them, not just the
    open inbox.
  • It returns a highlighted snippet (``ts_headline``) so the UI can show WHY a
    message matched.
  • It accepts ``websearch_to_tsquery`` syntax: bare words are AND-ed, "quoted
    phrases" match adjacency, ``OR`` unions, and a leading ``-`` excludes — the
    grammar users already expect from web search.

The additive filters (folder, date range, read/starred/attachments, sender
category) mirror ``/email/messages`` so search composes with filtering.

``q`` is OPTIONAL: this is the whole query surface, not just the text one. The
UI's search bar composes a text query with closable filter pills (tags, from/to,
unread/starred/attachments), and a pills-only search — "show me everything
tagged Newsletter" with no words typed — is a first-class case. With no ``q``
the FTS predicate is simply skipped and results come back recency-ordered
(there is no relevance to rank by); every other filter applies identically. A
call with neither ``q`` nor any filter is just a folder listing.

Phase 2 (semantic re-ranking over pgvector embeddings) layers ON TOP of this via
``hybrid=true`` — see ``semantic.py``. With semantics off, this endpoint is a
complete, reliable lexical search on its own.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.email.core import (
    KNOWN_LABELS_LOWER,
    UNCATEGORIZED_SQL,
    _account_scope,
    _tenant_session,
    _row_to_message,
    folder_scope,
    router,
)
from gateway.routes.email.transport.messages import _also_in_by_message
from sqlalchemy import text

# The tsvector expression — MUST stay byte-for-byte identical to the GIN index
# in migration 72 (idx_email_messages_fts_body) so the planner uses the index
# instead of recomputing per row. One constant, referenced everywhere it's
# needed (WHERE match, rank, headline), so they can never drift apart.
_FTS_VECTOR = (
    "to_tsvector('english', "
    "coalesce(em.subject,'') || ' ' || "
    "coalesce(em.body_text,'') || ' ' || "
    "coalesce(em.from_address->>'name','') || ' ' || "
    "coalesce(em.from_address->>'email',''))"
)

# What the highlighter runs over — subject + body only (sender is shown
# separately on the card, no need to highlight it inside the snippet).
_HEADLINE_SOURCE = (
    "coalesce(em.subject,'') || ' — ' || coalesce(em.body_text, em.snippet, '')"
)


def _parse_dt(value: str | None) -> Any:
    if not value:
        return None
    try:
        from datetime import datetime  # noqa: PLC0415
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _tag_filters(
    where: list[str], params: dict[str, Any],
    label: str | None, labels: list[str] | None,
) -> None:
    """AND one predicate per tag pill.

    ``label`` (single, legacy) and ``labels`` (the pills) are both matched against
    user labels OR assigned categories, so a rule-engine tag ("Newsletter",
    "Reply") and a user's own label are searchable the same way. Stacked pills
    AND together — each narrows the result set, which is how a row of filter
    chips reads."""
    tags = [t for t in ([label] if label else []) + (labels or []) if t and t.strip()]
    for i, tag in enumerate(tags):
        key = f"tag_{i}"
        # Case- and whitespace-insensitive. The rule engine stores the rule's
        # label VERBATIM, so an exact `= ANY(...)` silently returned nothing
        # whenever a rule's name differed by case or a stray space — the same
        # trap the Email Cleaner's own tally already had to fix, which is why
        # this is normalised in both places rather than trusted at one.
        where.append(
            f"(EXISTS (SELECT 1 FROM unnest(COALESCE(em.labels, '{{}}')) AS l"
            f" WHERE LOWER(TRIM(l)) = :{key})"
            f" OR EXISTS (SELECT 1 FROM unnest(COALESCE(em.categories, '{{}}'))"
            f" AS c WHERE LOWER(TRIM(c)) = :{key}))")
        params[key] = tag.strip().lower()


def _address_filters(
    where: list[str], params: dict[str, Any],
    from_addr: str | None, to_addr: str | None,
) -> None:
    """``from:``/``to:`` pills — substring match on an address or display name."""
    if from_addr:
        where.append("(LOWER(em.from_address->>'email') LIKE :from_addr"
                     " OR LOWER(em.from_address->>'name') LIKE :from_addr)")
        params["from_addr"] = f"%{from_addr.strip().lower()}%"
    if to_addr:
        # to_addresses/cc_addresses are JSONB arrays of {name, email}; match a
        # substring against any recipient. Cc counts as "to" — a user looking for
        # mail addressed to someone means it, whichever header carried them.
        where.append("EXISTS (SELECT 1 FROM jsonb_array_elements("
                     " COALESCE(em.to_addresses, '[]'::jsonb)"
                     " || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r"
                     " WHERE LOWER(r->>'email') LIKE :to_addr"
                     " OR LOWER(r->>'name') LIKE :to_addr)")
        params["to_addr"] = f"%{to_addr.strip().lower()}%"


def _state_filters(
    where: list[str], params: dict[str, Any], flags: dict[str, Any],
) -> None:
    """Date-range, read/starred/attachment and sender-category filters — the
    additive predicates shared with ``/email/messages``."""
    dt_after = _parse_dt(flags.get("received_after"))
    dt_before = _parse_dt(flags.get("received_before"))
    if dt_after is not None:
        where.append("em.received_at >= :received_after")
        params["received_after"] = dt_after
    if dt_before is not None:
        where.append("em.received_at <= :received_before")
        params["received_before"] = dt_before
    for col in ("is_read", "is_starred", "has_attachments"):
        if flags.get(col) is not None:
            where.append(f"em.{col} = :{col}")
            params[col] = flags[col]
    if flags.get("sender_category"):
        where.append("EXISTS (SELECT 1 FROM email_senders se "
                     "WHERE se.account_id = em.account_id "
                     "AND LOWER(se.email) = LOWER(em.from_address->>'email') "
                     "AND LOWER(se.category) = LOWER(:sender_category))")
        params["sender_category"] = flags["sender_category"]
    if flags.get("importance"):
        # Provider importance ('high'/'normal'/'low') — "show me what senders
        # flagged important". Case-insensitive so a stored 'High' still matches.
        where.append("LOWER(em.importance) = LOWER(:importance)")
        params["importance"] = flags["importance"]


@router.get("/search")
async def search_messages(
    q: str | None = Query(
        None, description="Search text (websearch syntax); omit for filters-only"),
    account_id: str | None = Query(None, description="Limit to one account"),
    folder: str | None = Query(
        None, description="Scope: a folder key, 'all', or 'starred'"),
    label: str | None = Query(None),
    labels: list[str] | None = Query(
        None, description="Tag pills — repeatable; a message must carry ALL"),
    uncategorized: bool = Query(
        False, description="Only mail carrying none of the rule-engine labels"),
    from_addr: str | None = Query(
        None, description="Substring match on the sender name or address"),
    to_addr: str | None = Query(
        None, description="Substring match on any To/Cc recipient"),
    received_after: str | None = Query(None),
    received_before: str | None = Query(None),
    is_read: bool | None = Query(None),
    is_starred: bool | None = Query(None),
    has_attachments: bool | None = Query(None),
    sender_category: str | None = Query(None),
    importance: str | None = Query(
        None, description="Provider importance: high | normal | low"),
    hybrid: bool = Query(
        False, description="Blend semantic (vector) similarity into the ranking"),
    light: bool = Query(
        False,
        description="Omit message bodies — for callers that only render "
                    "subject/snippet (list rows). Cuts the payload by orders "
                    "of magnitude on HTML-heavy mail."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(get_current_user),
):
    """Ranked search over the user's email — the query surface behind the search
    bar. Returns messages each with a ``rank`` and a highlighted ``highlight``
    snippet, plus ``total`` for paging.

    ``q`` is optional: with search text, results are relevance-ranked; with only
    filters (tag pills, from/to, unread/…), the text predicate is skipped and
    results come back newest-first. ``folder`` scopes the search — a folder key,
    ``all`` (everything but junk/trash), or ``starred``; omit it to span every
    folder. Unless ``account_id`` narrows it, the search spans all the user's
    accounts, except a mailbox that the member keeps separate (EM-T8g-1)."""
    async with _tenant_session() as db:
        uid = user.email or "anonymous"
        text_q = (q or "").strip()
        params: dict[str, Any] = {"uid": uid, "q": text_q}

        # Search in All inboxes leaves out a separate mailbox. Its own
        # account_id still searches it (EM-T8g-1, D-EM-30).
        where = [_account_scope(account_id, params, pooled_only=True)]
        # Filters-only search: no text ⇒ no FTS predicate and nothing to rank.
        if text_q:
            where.append(
                f"{_FTS_VECTOR} @@ websearch_to_tsquery('english', :q)")

        # Search is the one place "all" spans sent mail too: the browse view
        # excludes it (All shows what arrived), but "what did I tell them?" is
        # a routine search, and a scope labelled "All folders" that skipped the
        # user's own replies would quietly fail the query it exists to serve.
        folder_sql = folder_scope(folder, params, include_sent=True)
        if folder_sql:
            where.append(folder_sql)
        _tag_filters(where, params, label, labels)
        if uncategorized:
            # The complement of every tag pill. Defined in core so the inbox
            # chip and the Email Cleaner's Uncategorized tab mean the same mail.
            where.append(UNCATEGORIZED_SQL)
            params["known_labels"] = KNOWN_LABELS_LOWER
        _address_filters(where, params, from_addr, to_addr)
        _state_filters(where, params, {
            "received_after": received_after,
            "received_before": received_before,
            "is_read": is_read,
            "is_starred": is_starred,
            "has_attachments": has_attachments,
            "sender_category": sender_category,
            "importance": importance,
        })

        where_sql = " AND ".join(where)

        total = (await db.execute(text(
            f"SELECT COUNT(*) FROM email_messages em WHERE {where_sql}"
        ), params)).scalar() or 0

        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size

        # Hybrid re-ranking (Phase 2, opt-in): when requested AND semantic search
        # is enabled AND the query embeds, blend the lexical rank with cosine
        # similarity to the query vector. Recall stays LEXICAL — every FTS match
        # is still returned; hybrid only re-ORDERS them by semantic closeness, so
        # a reliability-first search never drops a keyword hit. If the query can't
        # be embedded (flag off, embed error), we fall through to pure lexical.
        semantic_select = "0.0 AS sim"
        # Filters-only: nothing to rank against, so newest-first is the only
        # meaningful order (and hybrid re-ranking has no query to embed).
        order_sql = ("rank DESC, em.received_at DESC" if text_q
                     else "em.received_at DESC")
        join_sql = ""
        if hybrid and text_q:
            qvec = None
            try:
                from email_ingestion.email_embeddings import embed_query
                qvec = await embed_query(text_q)
            except Exception:  # noqa: BLE001
                qvec = None
            if qvec is not None:
                params["qvec"] = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"
                # 0.5·lexical (capped to 1) + 0.5·cosine-similarity. A message
                # with no embedding yet (sim NULL → 0) still ranks on its lexical
                # score, so unembedded mail is never hidden.
                join_sql = ("LEFT JOIN email_embeddings ee "
                            "ON ee.message_id = em.id")
                semantic_select = (
                    "COALESCE(1 - (ee.embedding <=> CAST(:qvec AS vector)), 0)"
                    " AS sim")
                order_sql = (
                    "(LEAST(ts_rank_cd(" + _FTS_VECTOR +
                    ", websearch_to_tsquery('english', :q)), 1.0) * 0.5"
                    " + COALESCE(1 - (ee.embedding <=> CAST(:qvec AS vector)),"
                    " 0) * 0.5) DESC, em.received_at DESC")

        # ts_rank_cd rewards cover density (matches close together rank higher);
        # recency breaks ties so equally-relevant hits surface newest-first.
        # With no search text there is nothing to rank or highlight: ts_headline
        # against an empty tsquery would return the head of the body with no
        # <mark> in it, which the list would render as a "match" snippet that
        # matched nothing. Emit an empty highlight instead so the card falls back
        # to its normal preview.
        rank_select = (f"ts_rank_cd({_FTS_VECTOR},"
                       " websearch_to_tsquery('english', :q)) AS rank"
                       if text_q else "0.0 AS rank")
        headline_select = (
            f"""ts_headline('english', {_HEADLINE_SOURCE},
                    websearch_to_tsquery('english', :q),
                    'StartSel=<mark>, StopSel=</mark>, MaxFragments=2, '
                    'MaxWords=18, MinWords=5, FragmentDelimiter=" … "'
                ) AS highlight"""
            if text_q else "'' AS highlight")
        # ``light``: keep the SELECT's SHAPE (every consumer, _row_to_message
        # included, reads these two columns) but return empty bodies. A list row
        # renders subject + snippet only, so shipping body_html for 25 marketing
        # mails was megabytes of HTML parsed on the main thread and then thrown
        # away — the Email Cleaner's sender drill-down froze the page on it.
        body_select = (
            "'' AS body_text, NULL AS body_html" if light
            else "em.body_text, em.body_html")
        rows = (await db.execute(text(
            f"""SELECT em.id, em.provider_message_id, em.thread_id,
                       em.account_id, em.folder, em.labels,
                       em.from_address, em.to_addresses,
                       em.cc_addresses, em.bcc_addresses,
                       em.subject, {body_select},
                       em.snippet, em.has_attachments,
                       em.is_read, em.is_starred, em.is_flagged,
                       em.importance, em.categories,
                       em.received_at, em.synced_at,
                       {rank_select},
                       {semantic_select},
                       {headline_select}
                  FROM email_messages em
                  {join_sql}
                 WHERE {where_sql}
                 ORDER BY {order_sql}
                 LIMIT :limit OFFSET :offset"""
        ), params)).fetchall()

        # "Also in" (EM-T8g-3 item 1): one read for the page, as in the list.
        also_in = await _also_in_by_message(db, [row.id for row in rows], uid)

        emails_out = []
        for row in rows:
            m = _row_to_message(row)
            d = m.model_dump()
            d["rank"] = float(getattr(row, "rank", 0.0) or 0.0)
            d["sim"] = float(getattr(row, "sim", 0.0) or 0.0)
            d["highlight"] = getattr(row, "highlight", "") or ""
            d["thread_count"] = 1
            d["also_in"] = also_in.get(m.id, [])
            emails_out.append(d)

        return {
            "emails": emails_out,
            "total": total,
            "page": page,
            "page_size": page_size,
            "query": text_q,
            "hybrid": bool(join_sql),
        }
