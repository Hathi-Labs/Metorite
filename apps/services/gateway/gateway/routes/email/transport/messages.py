"""Transport · messages — list/read/update/delete a message, lazy full-body
hydration, and the full-body endpoint."""

from __future__ import annotations

import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_common import db_busy
from acb_common.tenant_redis import get_tenant_redis, key, organization_scope
from email_ingestion import html_tier
from email_ingestion import storage as ingest_storage
from email_ingestion.providers.base import ProviderRateLimited, local_folder_after_move
from fastapi import Depends, HTTPException, Query, status
from gateway.routes.email.core import (
    ATTACHMENT_CACHE_TTL_SECS,
    HUMAN_SENDER_CATEGORIES_LOWER,
    IN_ALL_INBOXES_SQL,
    KNOWN_LABELS_LOWER,
    MAX_BODY_HTML_BYTES,
    MAX_BODY_TEXT_BYTES,
    RESERVED_INDICATORS,
    UNCATEGORIZED_SQL,
    AttachmentModel,
    EmailMessageModel,
    _assert_account_owner,
    _decrypt_credentials,
    _fetch_attachments,
    _fetch_attachments_batch,
    _html_remote,
    _tenant_session,
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _provider_for_message,
    _row_to_message,
    _truncate_body,
    folder_scope,
    router,
)
from gateway.routes.email.transport.attachments import _canonical_uuid
from pydantic import BaseModel, Field
from sqlalchemy import text


class MessageUpdateModel(BaseModel):
    is_read: bool | None = None
    is_starred: bool | None = None
    is_flagged: bool | None = None
    folder: str | None = None
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None


class ListMessagesParams(BaseModel):
    account_id: str | None = None
    folder: str = "INBOX"
    query: str | None = None
    page: int = 1
    page_size: int = Field(default=50, ge=1, le=200)


def _parse_dt(value: str | None) -> Any:
    """Parse an ISO date/datetime (tolerating a trailing 'Z') for a filter bound;
    None when absent or unparseable (so a bad value never errors the query)."""
    if not value:
        return None
    try:
        from datetime import datetime  # noqa: PLC0415
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


async def _also_in_by_message(
    db: Any, message_ids: list[str], owner: str,
) -> dict[str, list[str]]:
    """``identity.also_in_by_message`` for the list and search (EM-T8g-3).

    The import is lazy, as each import of ``automation`` from ``transport``
    is: the automation layer imports ``transport.send`` at load time, so a
    load-time import here would be a cycle."""
    from gateway.routes.email.automation.identity import also_in_by_message
    return await also_in_by_message(db, message_ids, owner)


def _mailbox_clause(
    account_id: str | None, thread_id: str | None, params: dict[str, Any],
) -> str | None:
    """The mailbox clause of the list and the facets, over ``em`` and ``ea``.

    A named mailbox gives its own rows, and binds ``:account_id``. A thread
    load with no ``account_id`` keeps the owner scope only, because the chat
    reads a thread by its mail and sends no ``account_id``. Any other read is
    a read of All inboxes, so it leaves out a separate mailbox (EM-T8g-1,
    D-EM-30). The caller still writes the owner predicate ``ea.user_id``.
    """
    if account_id:
        params["account_id"] = account_id
        return "em.account_id = :account_id"
    if thread_id:
        return None
    return f"ea.{IN_ALL_INBOXES_SQL}"


@router.get("/messages/facets")
async def message_facets(
    account_id: str | None = Query(None),
    folder: str = Query("INBOX"),
    user: UserContext = Depends(get_current_user),
):
    """Which quick filters actually have mail behind them, in THIS folder.

    The inbox's chip row used to be a fixed list, so it offered "Cold Email" in
    Sent and "Needs reply" in Drafts — filters guaranteed to return nothing.
    Worse, a chip that comes back empty is ambiguous: the user can't tell "no
    such mail here" from "the filter is broken".

    Returns a count per known label plus ``uncategorized`` and ``unread``, so
    the UI can hide the dead chips and show the live ones with their size.
    Counts, not booleans, because the same query yields them and "Newsletter
    1,204" is the number that tells you where to start.
    """
    async with _tenant_session() as db:
        params: dict[str, Any] = {"user_id": user.email or "anonymous"}
        where = ["ea.user_id = :user_id"]
        # The counts of All inboxes leave out a separate mailbox (EM-T8g-1).
        mailbox_sql = _mailbox_clause(account_id, None, params)
        if mailbox_sql:
            where.append(mailbox_sql)
        folder_sql = folder_scope(folder, params)
        if folder_sql:
            where.append(folder_sql)
        where_sql = " AND ".join(where)
        params["known_labels"] = KNOWN_LABELS_LOWER

        # One pass over the folder: per-label tallies via LATERAL unnest, and
        # the two scalar buckets that aren't labels at all. EVERY label the
        # rules wrote is tallied — not just the built-in vocabulary — so a rule
        # with a custom label still gets its chip in the inbox (the old
        # ANY(:known_labels) restriction made custom-labelled mail filterable
        # but its chip invisible). Capped by count so a runaway AI-resolved
        # {{...}} label can't flood the row.
        rows = (await db.execute(text(
            f"""SELECT LOWER(TRIM(c)) AS label, COUNT(*) AS n
                  FROM email_messages em
                  JOIN email_accounts ea ON em.account_id = ea.id
                  CROSS JOIN LATERAL unnest(COALESCE(em.categories, '{{}}')) AS c
                 WHERE {where_sql}
                   AND TRIM(c) <> ''
                 GROUP BY 1
                 ORDER BY n DESC
                 LIMIT 40"""
        ), params)).fetchall()

        totals = (await db.execute(text(
            f"""SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE em.is_read = false) AS unread,
                       COUNT(*) FILTER (WHERE {UNCATEGORIZED_SQL}) AS uncategorized
                  FROM email_messages em
                  JOIN email_accounts ea ON em.account_id = ea.id
                 WHERE {where_sql}"""
        ), params)).fetchone()

        return {
            "folder": folder,
            "total": int(getattr(totals, "total", 0) or 0),
            "unread": int(getattr(totals, "unread", 0) or 0),
            "uncategorized": int(getattr(totals, "uncategorized", 0) or 0),
            # Keyed by the LOWERCASED label; the UI matches its chips
            # case-insensitively so a hand-edited rule writing "newsletter"
            # still lights up the Newsletter chip.
            "labels": {r.label: int(r.n or 0) for r in rows},
        }


@router.get("/messages")
async def list_messages(
    account_id: str | None = Query(None),
    folder: str = Query("INBOX"),
    label: str | None = Query(None),
    # Mail carrying none of the rule-engine labels (same definition the Email
    # Cleaner's Uncategorized tab uses — see core.UNCATEGORIZED_SQL).
    uncategorized: bool = Query(False),
    query: str | None = Query(None),
    thread_id: str | None = Query(None),
    # ── Rich filters (used by the assistant's inbox-query tools; all optional and
    # additive, so existing callers/UI are unaffected) ──────────────────────────
    received_after: str | None = Query(None),   # ISO; received_at >= this
    received_before: str | None = Query(None),  # ISO; received_at <= this
    is_read: bool | None = Query(None),         # filter by read state
    is_starred: bool | None = Query(None),
    has_attachments: bool | None = Query(None),
    importance: str | None = Query(None),       # high | normal | low
    from_email: str | None = Query(None),       # substring match on sender address
    sender_category: str | None = Query(None),  # email_senders category
    sort: str = Query("newest"),                # newest | oldest | importance
    collapse: bool = Query(False),              # one row per conversation
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(get_current_user),
):
    """List/search emails across accounts.

    When ``thread_id`` is given the result is the whole conversation (across
    folders), oldest-first — used by the reading pane's conversation view.

    The optional filters (date range, read/starred/attachment state, importance,
    sender address, sender category) + ``sort`` let the assistant answer inbox-wide
    questions ("sales emails in the last month", "important unread mail") without
    pulling the whole inbox. ``query`` is a full-text match over subject/body/from.

    ``collapse`` returns one row per conversation — the newest message in the
    current view represents its thread, and ``total`` counts conversations, not
    messages. This is the mailbox browse's default (the human list reads at thread
    level, matching the one-classification-per-conversation invariant); the
    assistant's inbox-query tools leave it off so their counts stay per-message.
    Ignored when ``thread_id`` is set (the conversation view wants every message).
    """
    async with _tenant_session() as db:
        where_clauses = [
            "ea.user_id = :user_id"
        ]
        params: dict[str, Any] = {
            "user_id": user.email or "anonymous",
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }

        # All inboxes leaves out a separate mailbox, and a thread load keeps
        # the owner scope only (EM-T8g-1, D-EM-30).
        mailbox_sql = _mailbox_clause(account_id, thread_id, params)
        if mailbox_sql:
            where_clauses.append(mailbox_sql)
        if thread_id:
            # Conversation view: every message in the thread, ignore the folder
            # filter (a thread spans inbox/sent/etc.).
            where_clauses.append("em.thread_id = :thread_id")
            params["thread_id"] = thread_id
        elif folder:
            # Real folders match case-insensitively against the canonical key the
            # providers persist (inbox/sent/drafts/trash/archive/junk + user
            # folders); "starred" is a flag and "all" spans every folder but
            # junk/trash. folder_scope() owns all three so the All *view* here and
            # the All search *scope* can never disagree about what "all" means.
            folder_sql = folder_scope(folder, params)
            if folder_sql:
                where_clauses.append(folder_sql)
        # Snooze: hide currently-sleeping conversations from every browse EXCEPT
        # the Snoozed view itself and a thread load (opening a snoozed thread
        # still shows all of it). The wake is at query time — once now() passes
        # snoozed_until the conversation reappears with no scheduler involved.
        if not thread_id and (folder or "").strip().lower() != "snoozed":
            where_clauses.append(
                "(em.snoozed_until IS NULL OR em.snoozed_until <= now())")
        if label:
            # Match either a user label or an assigned category (both TEXT[]).
            where_clauses.append(
                "(:label = ANY(COALESCE(em.labels, '{}'))"
                " OR :label = ANY(em.categories))"
            )
            params["label"] = label
        if uncategorized:
            # The inverse of every rule label — the mail the rules never reached.
            # Shares its definition with the Email Cleaner via core, so the two
            # Uncategorized views of one mailbox can't disagree.
            where_clauses.append(UNCATEGORIZED_SQL)
            params["known_labels"] = KNOWN_LABELS_LOWER
        if query:
            # websearch_to_tsquery, matching transport/search.py: bare words are
            # AND-ed, but "OR" and quoted phrases are honoured. plainto_tsquery
            # (the old call here) AND-s EVERYTHING, so the agent's find_urgent
            # ("urgent OR deadline OR ASAP OR action required") could only match
            # a message containing every one of those words — i.e. never. The
            # vector below is byte-identical to search._FTS_VECTOR and the GIN
            # index (72_email_search_fts.sql); keep the three in lock-step.
            where_clauses.append(
                """to_tsvector('english',
                   coalesce(em.subject,'') || ' ' ||
                   coalesce(em.body_text,'') || ' ' ||
                   coalesce(em.from_address->>'name','') || ' ' ||
                   coalesce(em.from_address->>'email',''))
                   @@ websearch_to_tsquery('english', :query)"""
            )
            params["query"] = query
        # Date range (received_at).
        dt_after, dt_before = _parse_dt(received_after), _parse_dt(received_before)
        if dt_after is not None:
            where_clauses.append("em.received_at >= :received_after")
            params["received_after"] = dt_after
        if dt_before is not None:
            where_clauses.append("em.received_at <= :received_before")
            params["received_before"] = dt_before
        # Boolean state filters.
        if is_read is not None:
            where_clauses.append("em.is_read = :is_read")
            params["is_read"] = is_read
        if is_starred is not None:
            where_clauses.append("em.is_starred = :is_starred")
            params["is_starred"] = is_starred
        if has_attachments is not None:
            where_clauses.append("em.has_attachments = :has_attachments")
            params["has_attachments"] = has_attachments
        if importance:
            where_clauses.append("LOWER(em.importance) = LOWER(:importance)")
            params["importance"] = importance
        if from_email:
            where_clauses.append(
                "LOWER(em.from_address->>'email') LIKE :from_email")
            params["from_email"] = f"%{from_email.strip().lower()}%"
        if sender_category:
            # Filter by the sender's assigned category (email_senders), e.g.
            # "Marketing"/"Newsletter" — distinct from per-message categories[].
            where_clauses.append(
                "EXISTS (SELECT 1 FROM email_senders se "
                "WHERE se.account_id = em.account_id "
                "AND LOWER(se.email) = LOWER(em.from_address->>'email') "
                "AND LOWER(se.category) = LOWER(:sender_category))")
            params["sender_category"] = sender_category

        where_sql = " AND ".join(where_clauses)
        # Collapse to one row per conversation for the mailbox browse, but never
        # inside a thread load (that view wants every message). A NULL thread_id
        # is its own conversation — key on the message id so those aren't merged.
        # The key names the mailbox too: a conversation never spans two
        # mailboxes, even when two of them report the same thread id (EM-T8d,
        # MB-12, §11.6 case 13).
        do_collapse = collapse and not thread_id
        conv_key = "(em.account_id::text || ':' || COALESCE(em.thread_id, em.id::text))"

        # Ordering: conversation view is chronological; otherwise honour ``sort``.
        # The collapsed path orders the OUTER query over the per-thread picks, so
        # its expression uses the bare column names the subquery exposes.
        if thread_id or sort == "oldest":
            order_sql, order_plain = "em.received_at ASC", "received_at ASC"
        elif sort == "importance":
            # Most-important first: high → normal → low, then unread, then recent.
            imp = ("CASE LOWER(COALESCE({p}importance, 'normal')) "
                   "WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
                   "{p}is_read ASC, {p}received_at DESC")
            order_sql = imp.format(p="em.")
            order_plain = imp.format(p="")
        else:
            order_sql, order_plain = "em.received_at DESC", "received_at DESC"

        # Count total — conversations when collapsing, else messages.
        count_sql = (
            f"""SELECT COUNT(DISTINCT {conv_key})
                FROM email_messages em
                JOIN email_accounts ea ON em.account_id = ea.id
                WHERE {where_sql}"""
            if do_collapse else
            f"""SELECT COUNT(*)
                FROM email_messages em
                JOIN email_accounts ea ON em.account_id = ea.id
                WHERE {where_sql}"""
        )
        count_result = await db.execute(text(count_sql), params)
        total = count_result.scalar() or 0

        _COLS = """em.id, em.provider_message_id, em.thread_id,
                   em.account_id, em.folder, em.labels,
                   em.from_address, em.to_addresses,
                   em.cc_addresses, em.bcc_addresses,
                   em.subject, em.body_text, em.body_html,
                   em.snippet, em.has_attachments,
                   em.is_read, em.is_starred, em.is_flagged,
                   em.importance, em.categories,
                   em.received_at, em.synced_at, em.snoozed_until"""
        if do_collapse:
            # DISTINCT ON keeps the newest message per conversation IN THIS VIEW
            # (so a thread appears represented by its latest matching message);
            # the outer query then paginates/orders those representatives.
            page_sql = (
                f"""SELECT * FROM (
                        SELECT DISTINCT ON ({conv_key}) {_COLS}
                        FROM email_messages em
                        JOIN email_accounts ea ON em.account_id = ea.id
                        WHERE {where_sql}
                        ORDER BY {conv_key}, em.received_at DESC
                    ) conv
                    ORDER BY {order_plain}
                    LIMIT :limit OFFSET :offset""")
        else:
            page_sql = (
                f"""SELECT {_COLS}
                    FROM email_messages em
                    JOIN email_accounts ea ON em.account_id = ea.id
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT :limit OFFSET :offset""")
        result = await db.execute(text(page_sql), params)
        rows = result.fetchall()

        messages = [_row_to_message(row) for row in rows]

        # Conversation load (thread_id given): populate EACH message's
        # attachments so earlier messages' files are viewable too — not just the
        # one open in the reader. One batched query; the folder list stays lean
        # (it keeps only the has_attachments flag).
        if thread_id:
            with_atts = [str(m.id) for m in messages if m.has_attachments]
            if with_atts:
                atts_by_msg = await _fetch_attachments_batch(db, with_atts)
                for m in messages:
                    m.attachments = atts_by_msg.get(str(m.id), [])

        # Thread sizes — one extra grouped query so the list can flag which rows
        # are conversations (badge with the message count). Counted for each
        # mailbox and thread, under the owner predicate, so a thread id that two
        # mailboxes share never adds their counts together (EM-T8d, MB-12).
        thread_ids = list({m.thread_id for m in messages if m.thread_id})
        thread_counts: dict[tuple[str, str], int] = {}
        if thread_ids:
            cnt_params: dict[str, Any] = {
                "tids": thread_ids, "user_id": user.email or "anonymous"}
            cnt_sql = (
                "SELECT em.account_id, em.thread_id, COUNT(*) AS c "
                "FROM email_messages em "
                "JOIN email_accounts ea ON em.account_id = ea.id "
                "WHERE ea.user_id = :user_id AND em.thread_id = ANY(:tids)"
            )
            if account_id:
                cnt_sql += " AND em.account_id = :account_id"
                cnt_params["account_id"] = account_id
            cnt_sql += " GROUP BY em.account_id, em.thread_id"
            cnt_res = await db.execute(text(cnt_sql), cnt_params)
            thread_counts = {
                (str(r.account_id), r.thread_id): r.c for r in cnt_res.fetchall()}

        # "Also in" (EM-T8g-3 item 1, D-EM-22): the paired mailboxes that hold
        # a copy of each row. One read serves the page, never one per row.
        also_in = await _also_in_by_message(
            db, [m.id for m in messages], user.email or "anonymous")

        emails_out = []
        for m in messages:
            d = m.model_dump()
            d["thread_count"] = thread_counts.get(
                (str(m.account_id), m.thread_id), 1)
            d["also_in"] = also_in.get(str(m.id), [])
            emails_out.append(d)

        return {
            "emails": emails_out,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


# Sender categories that are bulk/automated and never "important to check".
_PRIORITY_EXCLUDE_CATEGORIES = ("newsletter", "marketing", "cold email",
                                "notification")


@router.get("/priority")
async def priority_inbox(
    account_id: str = Query(...),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(get_current_user),
):
    """The emails that most need the user's attention — answers "what are the most
    important emails I need to check?".

    Ranks recent INBOX threads (latest message each) by a blend of signals:
    Reply Zero NEEDS_REPLY, unread, provider importance=high, starred, and a
    human sender (Conversation / Support — HUMAN_SENDER_CATEGORIES_LOWER).
    Bulk/automated senders (Newsletter / Marketing / Cold Email / Notification)
    are excluded so the list stays high-signal. Returns one row per thread with
    the reason it ranked, newest-first within score."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        rows = (await db.execute(text(
            """WITH latest AS (
                 SELECT DISTINCT ON (em.thread_id)
                        em.id, em.thread_id, em.subject, em.from_address,
                        em.received_at, em.is_read, em.importance, em.is_starred,
                        ts.status AS reply_status, se.category AS sender_category
                 FROM email_messages em
                 LEFT JOIN email_thread_status ts
                   ON ts.account_id = em.account_id
                   AND ts.thread_id = em.thread_id
                 LEFT JOIN email_senders se
                   ON se.account_id = em.account_id
                   AND LOWER(se.email) = LOWER(em.from_address->>'email')
                 WHERE em.account_id = :aid
                   AND LOWER(em.folder) = 'inbox'
                   AND em.received_at > now() - make_interval(days => :days)
                   AND COALESCE(LOWER(se.category), '') NOT IN
                       ('newsletter', 'marketing', 'cold email', 'notification')
                 ORDER BY em.thread_id, em.received_at DESC
               )
               SELECT *, (
                   (CASE WHEN reply_status = 'NEEDS_REPLY' THEN 100 ELSE 0 END)
                 + (CASE WHEN is_read THEN 0 ELSE 40 END)
                 + (CASE LOWER(COALESCE(importance, 'normal'))
                        WHEN 'high' THEN 30 ELSE 0 END)
                 + (CASE WHEN is_starred THEN 20 ELSE 0 END)
                 -- Bound, not inlined: the value is produced in senders.py and
                 -- consumed here, so a rename that reached only one side would
                 -- silently drop the boost instead of failing.
                 + (CASE WHEN LOWER(COALESCE(sender_category, ''))
                         = ANY(:human_cats) THEN 15 ELSE 0 END)
               ) AS score
               FROM latest
               ORDER BY score DESC, received_at DESC
               LIMIT :limit"""
        ), {"aid": account_id, "days": days, "limit": limit,
            "human_cats": HUMAN_SENDER_CATEGORIES_LOWER})).fetchall()

        out = []
        for r in rows:
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            reasons = []
            if r.reply_status == "NEEDS_REPLY":
                reasons.append("needs reply")
            if not r.is_read:
                reasons.append("unread")
            if (r.importance or "").lower() == "high":
                reasons.append("high importance")
            if r.is_starred:
                reasons.append("starred")
            if (r.sender_category or "").lower() in HUMAN_SENDER_CATEGORIES_LOWER:
                reasons.append(f"{r.sender_category.lower()} sender")
            out.append({
                "message_id": str(r.id), "thread_id": r.thread_id,
                "subject": r.subject or "(no subject)",
                "from": frm.get("name") or frm.get("email", ""),
                "from_email": frm.get("email", ""),
                "received_at": r.received_at.isoformat() if r.received_at else None,
                "is_read": r.is_read,
                "reply_status": r.reply_status,
                "sender_category": r.sender_category,
                "score": int(r.score or 0),
                "reason": ", ".join(reasons) or "recent",
            })
        return {"emails": out, "count": len(out), "days": days}


async def _hydrate_attachments(
    db: Any, message_id: str, user_email: str
) -> list[AttachmentModel]:
    """Fetch a message from its provider and persist its attachment metadata,
    then return it.

    Attachment rows are only ever created on demand. The body-hydration path
    below stores them too, but it runs only when the body is missing — so a
    message whose body arrived via sync (or was hydrated before attachment
    support shipped) would never get its attachments stored. This closes that
    gap: when a message advertises attachments but none are stored, fetch and
    store them regardless of the body state."""
    try:
        provider, provider_msg_id, account_id, store = await _provider_for_message(
            db, message_id, user_email
        )
        if not await provider.authenticate():
            return []
        full = await provider.get_message(provider_msg_id)
        for att in full.attachments:
            await db.execute(
                text(
                    """INSERT INTO email_attachments
                       (message_id, filename, mime_type, size_bytes,
                        provider_attachment_id)
                       VALUES (:mid, :filename, :mime_type, :size_bytes,
                               :provider_attachment_id)
                       ON CONFLICT (message_id, provider_attachment_id)
                           DO NOTHING"""
                ),
                {
                    "mid": message_id,
                    "filename": att.filename,
                    "mime_type": att.mime_type,
                    "size_bytes": att.size_bytes,
                    "provider_attachment_id": att.provider_attachment_id,
                },
            )
        await _persist_rotated_creds(db, store, account_id, provider)
        # No commit here: the only caller is the converted get_message (H2),
        # whose `_tenant_session` commits on clean exit — a mid-block commit
        # would end that transaction and drop the tenant GUC.
        return await _fetch_attachments(db, message_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "get_message.attach_hydrate_failed",
            message_id=message_id, error=str(exc)[:200],
        )
        return []


class MessageSummariesRequest(BaseModel):
    ids: list[str]


@router.post("/messages/summaries")
async def message_summaries(
    req: MessageSummariesRequest,
    user: UserContext = Depends(get_current_user),
):
    """Resolve a batch of message ids to lightweight row metadata (sender,
    subject, date, thread) in ONE query — no bodies, no read-state mutation.

    Powers the assistant's categorized email board (``present_email_groups``):
    the agent supplies the ids it grouped and we hydrate each row's label
    server-side, so the card is self-contained regardless of which list tool
    (or none) surfaced the id. Owner-scoped; unknown/foreign ids are omitted.
    Order follows the input ``ids`` so the agent's grouping is preserved."""
    ids = [i for i in (req.ids or []) if i]
    if not ids:
        return {"summaries": []}
    async with _tenant_session() as db:
        rows = (await db.execute(
            text(
                """SELECT em.id, em.thread_id, em.subject, em.from_address,
                          em.received_at, em.is_read, em.has_attachments
                   FROM email_messages em
                   JOIN email_accounts ea ON em.account_id = ea.id
                   WHERE ea.user_id = :user_id AND em.id = ANY(:ids)"""
            ),
            {"user_id": user.email or "anonymous", "ids": ids},
        )).fetchall()
        by_id: dict[str, dict[str, Any]] = {}
        for r in rows:
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            by_id[str(r.id)] = {
                "id": str(r.id),
                "thread_id": r.thread_id,
                "subject": r.subject or "(no subject)",
                "from": frm.get("name") or frm.get("email") or "(unknown sender)",
                "from_email": frm.get("email", ""),
                "received_at": r.received_at.isoformat() if r.received_at else None,
                "is_read": r.is_read,
                "has_attachments": r.has_attachments,
            }
        # Preserve caller order (and drop ids the user doesn't own).
        summaries = [by_id[i] for i in ids if i in by_id]
        return {"summaries": summaries}


@router.get("/messages/{message_id}", response_model=EmailMessageModel)
async def get_message(
    message_id: str,
    user: UserContext = Depends(get_current_user),
    mark_read: bool = Query(
        True,
        description="False reads the mail with no change to its read state. "
                    "A background read (WS-48 N2, narrow_and_read) is not the "
                    "member opening the mail."),
):
    """Get full email detail.

    An open marks the mail read. ``mark_read=false`` leaves ``is_read`` as it
    is (WS-48 N2). It changes nothing else: the owner scope, the body
    hydration and the response are the same. The default keeps the app's
    behaviour. ``is not False`` holds that default for a direct Python call,
    where the ``Query`` default does not resolve.
    """
    async with _tenant_session() as db:
        result = await db.execute(
            text(
                """SELECT em.id, em.provider_message_id, em.thread_id,
                          em.account_id, em.folder, em.labels,
                          em.from_address, em.to_addresses,
                          em.cc_addresses, em.bcc_addresses,
                          em.subject, em.body_text, em.body_html,
                          em.snippet, em.has_attachments,
                          em.is_read, em.is_starred, em.is_flagged,
                          em.importance, em.categories,
                          em.received_at, em.synced_at, em.snoozed_until,
                          ea.stored_bytes
                   FROM email_messages em
                   JOIN email_accounts ea ON em.account_id = ea.id
                   WHERE em.id = :message_id AND ea.user_id = :user_id"""
            ),
            {"message_id": message_id, "user_id": user.email or "anonymous"},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        # At the storage limit, an open shows the body and stores nothing
        # (WS-17 EM-T6c, owner answer Q4). A reopen loads it live again.
        store_body = not ingest_storage.at_limit(getattr(row, "stored_bytes", None))

        # Mark as read, unless the caller asked for a read with no effect.
        if mark_read is not False:
            await db.execute(
                text(
                    """UPDATE email_messages SET is_read = true, updated_at = now()
                       WHERE id = :id AND is_read = false"""
                ),
                {"id": message_id},
            )

        msg = _row_to_message(row)

        # ── Lazy body hydration ──
        # Some providers (notably Outlook/Graph) sync message *headers* only, so
        # the stored body is empty.  When the user opens such a message, fetch the
        # full body from the provider once and persist it so subsequent opens are
        # instant.  Mark as read on the provider too (two-way sync).
        if not msg.body_text and not msg.body_html:
            try:
                provider, provider_msg_id, account_id, store = await _provider_for_message(
                    db, message_id, user.email or "anonymous"
                )
                if await provider.authenticate():
                    full = await provider.get_message(provider_msg_id)
                    body_text = _truncate_body(full.body_text or "", MAX_BODY_TEXT_BYTES)
                    body_html = (
                        _truncate_body(full.body_html, MAX_BODY_HTML_BYTES)
                        if full.body_html else None
                    )
                    # 🔴 No cold HTML (WS-17 EM-S3, §14.4.3 item 3). With
                    # `html_tier.hot_only()` true, a cold row stores its text
                    # only. The answer below still carries the HTML, and the
                    # cache of the HTML route keeps it for the next open.
                    if store_body:
                        await db.execute(
                            text(
                                f"""UPDATE email_messages
                                   SET body_text = :bt, {html_tier.COLD_SAFE_HTML_SET},
                                       has_attachments = :ha, updated_at = now()
                                   WHERE id = :id"""
                            ),
                            {
                                "id": message_id,
                                "bt": body_text,
                                "bh": body_html,
                                "html_cold_before": html_tier.cold_before(),
                                "ha": full.has_attachments,
                            },
                        )
                    else:
                        _log.info("get_message.body_not_stored_at_limit",
                                  message_id=message_id)
                    if html_tier.drops_html(row.received_at):
                        await _remember_html(user.organization_id, row.id, body_html)
                    # Persist attachment metadata fetched with the full message.
                    for att in full.attachments:
                        await db.execute(
                            text(
                                """INSERT INTO email_attachments
                                   (message_id, filename, mime_type, size_bytes,
                                    provider_attachment_id)
                                   VALUES (:mid, :filename, :mime_type, :size_bytes,
                                           :provider_attachment_id)
                                   ON CONFLICT (message_id, provider_attachment_id)
                                       DO NOTHING"""
                            ),
                            {
                                "mid": message_id,
                                "filename": att.filename,
                                "mime_type": att.mime_type,
                                "size_bytes": att.size_bytes,
                                "provider_attachment_id": att.provider_attachment_id,
                            },
                        )
                    await _persist_rotated_creds(db, store, account_id, provider)
                    msg.body_text = body_text
                    msg.body_html = body_html
                    msg.has_attachments = full.has_attachments
                    # The answer now holds the HTML, so it is not remote
                    # (EM-S1 fix round 1). `_row_to_message` read the row
                    # before the fetch. Under EM-S3 a cold row stores no HTML,
                    # and this answer still carries the HTML that it fetched.
                    msg.html_remote = _html_remote(msg.body_html, row.received_at)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("get_message.hydrate_failed", message_id=message_id, error=str(exc)[:200])

        if msg.has_attachments:
            msg.attachments = await _fetch_attachments(db, message_id)
            # Body came from sync (so the hydration block above didn't run) but
            # the attachments were never stored — fetch + store them now.
            if not msg.attachments:
                msg.attachments = await _hydrate_attachments(
                    db, message_id, user.email or "anonymous"
                )
        return msg


def _move_target(name: str | None) -> str | None:
    """The name of a PATCH move, stripped ONCE (EM-G3b review round 1).

    The route gives this one string to ``_folder_for_move`` and to the
    provider, so the row and the push read the same name. A name that is
    empty after the strip answers 400 before any read or write, for every
    provider. Without it, ``canonical_folder`` read ``""`` as ``inbox``, and
    Gmail stored ``archive`` for ``"   "`` while its push went to the Inbox.
    ``None`` means that the update holds no move.
    """
    if name is None:
        return None
    target = name.strip()
    if not target:
        raise HTTPException(status_code=400, detail="A move needs a folder name")
    return target


async def _folder_for_move(
    db: Any, message_id: str, owner: str, name: str | None,
) -> tuple[str | None, tuple[Any, str, str, Any] | None]:
    """The folder that a PATCH move stores, and the provider that decides it.

    WS-17 EM-G3b item 8 (``email_app_master_plan.md`` §12.3.4, E-M8). The
    route builds the provider BEFORE it writes the folder, and
    ``_provider_for_message`` makes no network call. Gmail files a user
    label as ``archive``. A provider that refuses the move (Gmail: sent,
    drafts or a system label) gets a 400, and the route writes nothing.
    With no folder in the update, there is no move, and the answer is
    ``(None, None)``. ``name`` is the stripped name of ``_move_target``.

    A provider that fails to build keeps the push best-effort. The row then
    stores ``canonical_folder(name)``, and the push builds it again and logs
    the failure.
    """
    if name is None:
        return None, None
    built: tuple[Any, str, str, Any] | None = None
    try:
        built = await _provider_for_message(db, message_id, owner)
    except Exception:  # the push of the route builds it again and logs it
        built = None
    folder = local_folder_after_move(built[0] if built else None, name)
    if folder is None:
        raise HTTPException(
            status_code=400,
            detail=f"This mailbox cannot move a message to {name!r}",
        )
    return folder, built


@router.patch("/messages/{message_id}", response_model=EmailMessageModel)
async def update_message(
    message_id: str,
    updates: MessageUpdateModel,
    user: UserContext = Depends(get_current_user),
):
    """Update email properties (read, starred, flagged, folder, labels)."""
    # "Uncategorized" is a state (no known label), never a label — drop it here
    # so neither the local mirror below nor the provider set_labels can create
    # it as a real category (see core.RESERVED_INDICATORS).
    if updates.add_labels:
        updates.add_labels = [
            name for name in updates.add_labels
            if name.strip().lower() not in RESERVED_INDICATORS
        ]
    # One stripped name for the row and the push. A blank name is a 400
    # before any read or write (EM-G3b review round 1).
    target = _move_target(updates.folder)
    async with _tenant_session() as db:
        # Verify ownership
        result = await db.execute(
            text(
                """SELECT em.id FROM email_messages em
                   JOIN email_accounts ea ON em.account_id = ea.id
                   WHERE em.id = :id AND ea.user_id = :user_id"""
            ),
            {"id": message_id, "user_id": user.email or "anonymous"},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Message not found")

        # A move asks the provider for its folder BEFORE any write, so a
        # refused move answers 400 and writes nothing (EM-G3b item 8).
        folder, built = await _folder_for_move(
            db, message_id, user.email or "anonymous", target)

        set_clauses = ["updated_at = now()"]
        params: dict[str, Any] = {"id": message_id}

        if updates.is_read is not None:
            set_clauses.append("is_read = :is_read")
            params["is_read"] = updates.is_read
        if updates.is_starred is not None:
            set_clauses.append("is_starred = :is_starred")
            params["is_starred"] = updates.is_starred
        if updates.is_flagged is not None:
            set_clauses.append("is_flagged = :is_flagged")
            params["is_flagged"] = updates.is_flagged
        if target is not None:
            set_clauses.append("folder = :folder")
            params["folder"] = folder

        await db.execute(
            text(
                f"""UPDATE email_messages
                    SET {', '.join(set_clauses)}
                    WHERE id = :id"""
            ),
            params,
        )

        # Apply label add/remove locally — the categories column drives the
        # label chips shown in the UI.
        if updates.add_labels or updates.remove_labels:
            cat_res = await db.execute(
                text("SELECT categories FROM email_messages WHERE id = :id"),
                {"id": message_id},
            )
            crow = cat_res.fetchone()
            cats = list(crow.categories or []) if crow else []
            for name in updates.add_labels or []:
                if name not in cats:
                    cats.append(name)
            for name in updates.remove_labels or []:
                if name in cats:
                    cats.remove(name)
            await db.execute(
                text(
                    """UPDATE email_messages SET categories = :cats,
                       updated_at = now() WHERE id = :id"""
                ),
                {"id": message_id, "cats": cats},
            )

        # ── Two-way sync: push the change to the provider (best-effort) ──
        # The local DB is already updated; if the provider write fails we keep the
        # local state and log, rather than failing the user's action.
        try:
            provider, provider_msg_id, account_id, store = (
                built or await _provider_for_message(
                    db, message_id, user.email or "anonymous"
                )
            )
            if await provider.authenticate():
                if (
                    updates.is_read is not None
                    or updates.is_starred is not None
                    or updates.is_flagged is not None
                ):
                    await provider.apply_flags(
                        provider_msg_id,
                        is_read=updates.is_read,
                        is_starred=updates.is_starred,
                        is_flagged=updates.is_flagged,
                    )
                if target is not None:
                    # The name keeps its case, so a new label or Outlook
                    # folder reads as the member wrote it (EM-G3b item 9).
                    # It is the string that the helper read (review round 1).
                    new_pid = await provider.move_to_folder(
                        provider_msg_id, target
                    )
                    # Outlook /move re-keys the message — persist the new id so
                    # later actions don't hit a stale (404) provider id, and use
                    # it for the set_labels call below.
                    if new_pid and new_pid != provider_msg_id:
                        await db.execute(
                            text(
                                """UPDATE email_messages
                                   SET provider_message_id = :pid, updated_at = now()
                                   WHERE id = :id"""
                            ),
                            {"pid": new_pid, "id": message_id},
                        )
                        provider_msg_id = new_pid
                if updates.add_labels or updates.remove_labels:
                    await provider.set_labels(
                        provider_msg_id,
                        add=updates.add_labels or [],
                        remove=updates.remove_labels or [],
                    )
                await _persist_rotated_creds(db, store, account_id, provider)
        except Exception as exc:  # noqa: BLE001
            # Best-effort: the local change lands at the tenant session's
            # clean-exit commit either way, so a provider
            # failure (incl. an HTTPException from the provider lookup/write) must
            # NOT fail the user's action — just log it.
            _log.warning(
                "update_message.provider_sync_failed",
                message_id=message_id, error=str(exc)[:200],
            )

        # Return updated message
        return await get_message(message_id, user)


class SnoozeRequest(BaseModel):
    # ISO timestamp to sleep until; null clears the snooze (bring it back now).
    until: str | None = None


@router.post("/messages/{message_id}/snooze")
async def snooze_message(
    message_id: str,
    req: SnoozeRequest,
    user: UserContext = Depends(get_current_user),
):
    """Snooze (or, with until=null, un-snooze) a conversation.

    Snooze is inbox triage, applied to the whole conversation: every message in
    the thread is stamped so it leaves and returns together (a lone message with
    no thread is stamped on its own). It's app-local — there is no provider
    concept of snooze — so nothing is pushed upstream. The conversation reappears
    on its own once the time passes (query-time wake; no scheduler)."""
    async with _tenant_session() as db:
        row = (await db.execute(text(
            """SELECT em.account_id, em.thread_id
               FROM email_messages em
               JOIN email_accounts ea ON em.account_id = ea.id
               WHERE em.id = :id AND ea.user_id = :uid"""
        ), {"id": message_id, "uid": user.email or "anonymous"})).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")

        until = _parse_dt(req.until) if req.until else None
        params: dict[str, Any] = {"until": until, "acc": str(row.account_id)}
        if row.thread_id:
            scope = "thread_id = :tid"
            params["tid"] = row.thread_id
        else:
            scope = "id = :mid"
            params["mid"] = message_id
        res = await db.execute(text(
            f"""UPDATE email_messages SET snoozed_until = :until, updated_at = now()
                WHERE account_id = :acc AND {scope}"""
        ), params)
        return {
            "ok": True,
            "message_id": message_id,
            "thread_id": row.thread_id,
            "snoozed_until": until.isoformat() if until else None,
            "affected": res.rowcount,
        }


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Move email to trash (locally and on the provider)."""
    async with _tenant_session() as db:
        result = await db.execute(
            text(
                """UPDATE email_messages SET folder = 'trash', updated_at = now()
                   WHERE id = :id
                   AND account_id IN (
                       SELECT id FROM email_accounts WHERE user_id = :user_id
                   )"""
            ),
            {"id": message_id, "user_id": user.email or "anonymous"},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Message not found")

        # ── Two-way sync: trash on the provider too (best-effort) ──
        try:
            provider, provider_msg_id, account_id, store = await _provider_for_message(
                db, message_id, user.email or "anonymous"
            )
            if await provider.authenticate():
                new_pid = await provider.trash_message(provider_msg_id)
                if provider_msg_id in getattr(provider, "discarded_drafts", ()):
                    # Gmail discards a draft for good (``drafts.delete``), so
                    # no copy stays in Trash here either (WS-17 EM-G3a, E-A5).
                    await db.execute(
                        text("DELETE FROM email_messages WHERE id = :id"),
                        {"id": message_id},
                    )
                # Outlook trash = /move to Deleted Items, which re-keys the
                # message; persist the new id so it stays addressable.
                elif new_pid and new_pid != provider_msg_id:
                    await db.execute(
                        text(
                            """UPDATE email_messages
                               SET provider_message_id = :pid, updated_at = now()
                               WHERE id = :id"""
                        ),
                        {"pid": new_pid, "id": message_id},
                    )
                await _persist_rotated_creds(db, store, account_id, provider)
        except Exception as exc:  # noqa: BLE001
            # Best-effort: local trash already committed; never fail the user's
            # action on a provider error (incl. provider-raised HTTPException).
            _log.warning(
                "delete_message.provider_sync_failed",
                message_id=message_id, error=str(exc)[:200],
            )


@router.get("/messages/{message_id}/full-body")
async def get_full_body(
    message_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Fetch the full, untruncated email body from the provider.

    Use this when body_truncated is true on a message — the stored body
    was capped to stay within storage limits.  This endpoint reaches out
    to Gmail/Microsoft/IMAP live to retrieve the complete message body.
    """
    async with _tenant_session() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT em.provider_message_id, em.account_id,
                              p.provider, p.credentials_encrypted
                       FROM email_messages em
                       JOIN email_accounts p ON em.account_id = p.id
                       WHERE em.id = :mid AND p.user_id = :user_id"""
                ),
                {"mid": message_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Message not found")

            # Decrypt credentials
            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))

            # Instantiate provider
            provider = _instantiate_provider(row.provider, creds)

            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed",
                )

            msg = await provider.get_message(row.provider_message_id)
            # A 401 on the fetch refreshes the token (EM-T4c). Keep it.
            await _persist_rotated_creds(
                db, store, str(row.account_id), provider)
            return {
                "message_id": message_id,
                "body_text": msg.body_text,
                "body_html": msg.body_html,
                "subject": msg.subject,
                "from": (
                    f"{msg.from_address.name} <{msg.from_address.email}>"
                    if msg.from_address else ""
                ),
            }
        except HTTPException:
            raise
        except Exception as exc:
            _log.error(
                "full_body.failed", message_id=message_id, error=str(exc)[:200]
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch full body: {str(exc)}",
            )


# ── The HTML of a message, from the provider (WS-17 EM-S1) ──────────────────

#: The namespace of the HTML cache in tenant Redis. The key holds the id of
#: the ROW, never the path text, so two spellings of one id share one entry.
HTML_CACHE_NAMESPACE = "email-html"
#: How long a cached answer lives: the TTL of the file cache (1 hour), because
#: the route keeps the order of the owned file fetch (§14.4.2).
HTML_CACHE_TTL_SECS = ATTACHMENT_CACHE_TTL_SECS
#: The wait that a refused prefetch asks for (§14.4.2 item 2).
PREFETCH_RETRY_AFTER_SECS = 30
#: The longest ``Retry-After`` of a provider 429 that the route passes on.
#: A longer or unreadable value gives :data:`PREFETCH_RETRY_AFTER_SECS`.
PROVIDER_RETRY_AFTER_MAX_SECS = 300


def _provider_429_response(exc: BaseException) -> Any:
    """The HTTP answer of a provider 429 in the chain of *exc*, else None.

    A ``ProviderRateLimited`` counts with or without an answer, because a
    provider raises it when its own tries are spent (``GmailRateLimited``).
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(seen) < 8:
        seen.add(id(cur))
        response = getattr(cur, "response", None)
        if getattr(response, "status_code", None) == 429:
            return response
        if isinstance(cur, ProviderRateLimited):
            return response if response is not None else True
        cur = cur.__cause__
    return None


def _is_provider_429(exc: BaseException) -> bool:
    """True when the provider refused the fetch for a rate limit."""
    return _provider_429_response(exc) is not None


def _provider_retry_after(exc: BaseException) -> int:
    """The ``Retry-After`` that the route sends for a provider 429.

    The seconds of the provider's own header, from 1 to
    :data:`PROVIDER_RETRY_AFTER_MAX_SECS`. Else :data:`PREFETCH_RETRY_AFTER_SECS`.
    """
    response = _provider_429_response(exc)
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    try:
        wait = int(str(raw).strip())
    except (TypeError, ValueError):
        return PREFETCH_RETRY_AFTER_SECS
    if 1 <= wait <= PROVIDER_RETRY_AFTER_MAX_SECS:
        return wait
    return PREFETCH_RETRY_AFTER_SECS


class MessageHtmlModel(BaseModel):
    """The answer of ``GET /email/messages/{id}/html``.

    ``source`` says where the HTML came from: ``stored`` (the row holds it),
    ``cache`` (tenant Redis), ``provider`` (a live fetch) or ``none`` (the
    message has no HTML, and ``body_html`` is null).
    """

    message_id: str
    body_html: str | None = None
    source: Literal["stored", "cache", "provider", "none"]


def _html_key(row_id: Any) -> Any:
    """The cache key of one row. Call it inside ``organization_scope``."""
    return key(HTML_CACHE_NAMESPACE, str(row_id))


async def _cached_html(org_id: str | None, row_id: Any) -> tuple[bool, str | None]:
    """``(hit, body_html)`` from tenant Redis. A miss is ``(False, None)``.

    Only with an organization in the session, and only after the owner
    check. A cached ``none`` is a hit with ``None``. A Redis failure or a bad
    entry is a miss, so the route then asks the provider.
    """
    if not org_id:
        return False, None
    try:
        with organization_scope(org_id):
            raw = await get_tenant_redis().get(_html_key(row_id))
        if not raw:
            return False, None
        data = json.loads(raw)
        html = data.get("body_html")
        return True, (html if isinstance(html, str) and html else None)
    except Exception:  # the cache is best effort
        return False, None


async def _remember_html(org_id: str | None, row_id: Any, html: str | None) -> None:
    """Cache *html* for the row, ``None`` too. Best effort."""
    if not org_id:
        return
    try:
        # `ensure_ascii=False` keeps each non-ASCII letter as itself. The
        # default escape grows Cyrillic and CJK HTML 2 to 3 times.
        payload = json.dumps({"body_html": html}, ensure_ascii=False)
        with organization_scope(org_id):
            await get_tenant_redis().setex(
                _html_key(row_id), HTML_CACHE_TTL_SECS, payload,
            )
    except Exception:  # the cache is best effort
        pass


@router.get("/messages/{message_id}/html", response_model=MessageHtmlModel)
async def get_message_html(
    message_id: str,
    prefetch: bool = Query(False),
    user: UserContext = Depends(get_current_user),
) -> MessageHtmlModel:
    """The HTML of one message of the caller's own mail (WS-17 EM-S1).

    Spec: ``email_app_master_plan.md`` §14.4.2 items 1 and 2, and §14.6.1.
    The provider holds the HTML of a message older than the hot window, and
    the reading pane gets it here. The route keeps the order of the owned
    file fetch (``transport/attachments.py``):

    1. With the flag of ``html_tier.from_provider`` off, the answer is 404.
    2. With ``prefetch``, a database that refused a connect in the last
       15 seconds gives 503 with ``Retry-After``. An open is never refused.
    3. The owner read runs first. A message of another member is 404, before
       any cache read.
    4. A row that holds HTML answers it as ``stored``.
    5. Tenant Redis, keyed by the id of the ROW, inside
       ``organization_scope``.
    6. On a miss, the provider, with the member's own token. The HTML is cut
       at ``MAX_BODY_HTML_BYTES``, and the answer goes into the cache for one
       hour. A message with no HTML answers ``none``, and the cache keeps it.
    7. A provider 429 answers 503 with ``Retry-After``, and logs one
       ``email.html.provider_429`` line with the mailbox id and no mail text.

    ⚠️ **No session is open across the provider call.** Block A reads the
    row and the credentials, and closes. The provider authenticates and
    fetches with no session open. Block B opens only when the provider
    rotated its tokens, and writes them to ``email_accounts``.
    ⚠️ **No path writes ``email_messages``.** EM-S3 owns the writers.
    """
    if not html_tier.from_provider():
        raise HTTPException(status_code=404, detail="Not found")
    if prefetch and db_busy.recently_busy():
        raise HTTPException(
            status_code=503,
            detail="The database is busy. Try the prefetch again later.",
            headers={"Retry-After": str(PREFETCH_RETRY_AFTER_SECS)},
        )
    mid = _canonical_uuid(message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail="Message not found")

    # Block A: the owner read. The session opens OUTSIDE any try, so
    # `TenantUnbound` and a refused connect reach the handlers of main.py.
    async with _tenant_session() as db:
        row = (await db.execute(
            text(
                """SELECT em.id, em.provider_message_id, em.account_id,
                          em.body_html, ea.provider, ea.credentials_encrypted
                   FROM email_messages em
                   JOIN email_accounts ea ON em.account_id = ea.id
                   WHERE em.id = :mid AND ea.user_id = :user_id"""
            ),
            {"mid": mid, "user_id": user.email or "anonymous"},
        )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    row_id = str(row.id)
    if row.body_html:
        return MessageHtmlModel(message_id=row_id, body_html=row.body_html, source="stored")

    # Ownership confirmed. Only NOW is the cache safe to read.
    org_id = user.organization_id
    hit, cached = await _cached_html(org_id, row_id)
    if hit:
        return MessageHtmlModel(
            message_id=row_id, body_html=cached, source="cache" if cached else "none",
        )

    # The provider, with no session open, and the member's own token.
    creds, store = _decrypt_credentials(row.credentials_encrypted)
    provider = _instantiate_provider(row.provider, creds)
    account_id = str(row.account_id)
    fetch_error: Exception | None = None
    html: str | None = None
    try:
        if not await provider.authenticate():
            raise HTTPException(
                status_code=401, detail="Email account authentication failed",
            )
        # The body only. `get_message` of Outlook expands each attachment,
        # and Graph then sends the bytes of each file (EM-S1 fix round 1).
        full = await provider.get_message_body(row.provider_message_id)
        raw_html = getattr(full, "body_html", None) or ""
        html = _truncate_body(raw_html, MAX_BODY_HTML_BYTES) if raw_html.strip() else None
    except HTTPException:
        raise
    except Exception as exc:  # answered as 502 below
        fetch_error = exc
    finally:
        # Block B: keep a token that the provider rotated, also after a
        # failed fetch. It writes `email_accounts`, never `email_messages`.
        if provider.credentials_dirty():
            try:
                async with _tenant_session() as db:
                    await _persist_rotated_creds(db, store, account_id, provider)
            except Exception as exc:  # never fail the read on it
                _log.warning("email.html.creds_persist_failed",
                             message_id=row_id, error=type(exc).__name__)
    if fetch_error is not None and _is_provider_429(fetch_error):
        # The provider refused for a rate limit. The prefetch of the pane
        # stops on a 503, as it stops on a busy database (§14.6.1, EM-S2).
        # The line holds the mailbox id and the wait, and no mail text.
        wait = _provider_retry_after(fetch_error)
        _log.warning("email.html.provider_429", account_id=account_id, retry_after=wait)
        raise HTTPException(
            status_code=503,
            detail="The mail provider asked for a pause. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    if fetch_error is not None:
        _log.warning("email.html.fetch_failed",
                     message_id=row_id, error=type(fetch_error).__name__)
        raise HTTPException(
            status_code=502, detail="The mail provider did not give the message.",
        )

    await _remember_html(org_id, row_id, html)
    return MessageHtmlModel(
        message_id=row_id, body_html=html, source="provider" if html else "none",
    )
