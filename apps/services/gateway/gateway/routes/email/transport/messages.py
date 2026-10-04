"""Transport · messages — list/read/update/delete a message, lazy full-body
hydration, and the full-body endpoint."""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, status
from gateway.routes.email.core import (
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
    _fetch_attachments,
    _fetch_attachments_batch,
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
):
    """Get full email detail."""
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
                          em.received_at, em.synced_at, em.snoozed_until
                   FROM email_messages em
                   JOIN email_accounts ea ON em.account_id = ea.id
                   WHERE em.id = :message_id AND ea.user_id = :user_id"""
            ),
            {"message_id": message_id, "user_id": user.email or "anonymous"},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")

        # Mark as read
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
                    await db.execute(
                        text(
                            """UPDATE email_messages
                               SET body_text = :bt, body_html = :bh,
                                   has_attachments = :ha, updated_at = now()
                               WHERE id = :id"""
                        ),
                        {
                            "id": message_id,
                            "bt": body_text,
                            "bh": body_html,
                            "ha": full.has_attachments,
                        },
                    )
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
        if updates.folder is not None:
            set_clauses.append("folder = :folder")
            params["folder"] = updates.folder

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
            provider, provider_msg_id, account_id, store = await _provider_for_message(
                db, message_id, user.email or "anonymous"
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
                if updates.folder is not None:
                    new_pid = await provider.move_to_folder(
                        provider_msg_id, updates.folder.lower()
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
                # Outlook trash = /move to Deleted Items, which re-keys the
                # message; persist the new id so it stays addressable.
                if new_pid and new_pid != provider_msg_id:
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
