"""Automation · senders & triage — sender list, newsletters, bulk actions,
auto-archive, sender categorization, and cold-email blocking.

``/analytics/overview`` used to live here; it now has its own module,
``automation/analytics.py``, which imports ``DISPOSED_FOLDERS`` from here so
"how noisy is this sender" counts the same mail on both screens."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.routes.email.automation.identity import sender_scope
from gateway.routes.email.core import (
    CLEANUP_CATEGORIES,
    CONVERSATION_LABELS_LOWER,
    CONVERSATION_SENDER_CATEGORY,
    KNOWN_LABELS_LOWER,
    _account_scope,
    _assert_account_owner,
    _get_db,
    _tenant_session,
    _llm_json,
    _log,
    provider_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


# Folders whose mail is already disposed of (or was never received). The Email
# Cleaner and its uncategorized sweep both exclude these, so a sender is never
# inflated by mail the user has already dealt with. ONE definition, interpolated
# into both — a fixed constant, not user input, so it is safe in an f-string.
DISPOSED_FOLDERS = "('trash', 'junk', 'spam', 'drafts', 'draft')"
_NOT_DISPOSED = f"LOWER(COALESCE(em.folder,'')) NOT IN {DISPOSED_FOLDERS}"

# Conversation-state labels the Reply Zero rules write onto messages, shown as
# chips alongside the cleanup categories. Lowercased key → the display name, so
# a legacy "reply"/"to reply"/"actioned" row renders under its modern name
# instead of spawning a second chip for the same thing.
_CONVERSATION_DISPLAY: dict[str, str] = {
    "needs reply": "Needs Reply", "reply": "Needs Reply",
    "to reply": "Needs Reply",
    "awaiting reply": "Awaiting Reply",
    "fyi": "FYI",
    "done": "Done", "actioned": "Done",
    "follow-up": "Follow-up",
}


@router.get("/senders")
async def list_senders(
    account_id: str | None = Query(None),
    folder: str | None = Query(None),
    include_archived: bool = Query(
        False, description="Also count mail you've already archived"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: UserContext = Depends(get_current_user),
):
    """Aggregate messages by sender, merged with newsletter status.

    Powers the Email Cleaner: volume per sender, read-rate, unsubscribe link and
    approve/unsubscribe disposition.

    Scope is the mailbox MINUS what has already been dealt with. Trash / junk /
    drafts are excluded (disposed of, or never received) and so is ARCHIVED mail:
    the cleaner exists to reduce what still needs handling, so a sender you just
    archived has to leave the list. Counting archived mail made "Archive all"
    read as a no-op — the row stayed with an unchanged total — which is the one
    thing a cleanup tool must never do.

    ``include_archived=true`` restores the whole-mailbox view. It exists because
    the preset Marketing / Cold Email rules label AND archive in the same pass,
    so their mail is archived the moment it is classified; without this the
    category chips for those two would read 0 even on a mailbox full of them.
    That is a "show me everything" question, not the default cleanup one.

    Senders carrying a saved disposition (unsubscribed / auto-archived /
    approved) are ALWAYS listed, whatever the scope — their mail is archived by
    definition, and hiding them would empty the Unsubscribed and Auto-archive
    tabs, leaving the user unable to review or undo their own decisions.

    Pass ``folder`` to narrow to one folder.

    ``limit``/``offset`` page the list (ordered by volume, loudest first). The
    per-request cap is a payload bound, not a scope bound: ``total`` reports how
    many senders exist and every one of them is reachable by paging, so the quiet
    tail can still be cleaned up.
    """
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous",
                                  "limit": limit, "offset": offset}
        scope = _account_scope(account_id, params)
        # Already-disposed mail never counts toward "how noisy is this sender".
        folder_sql = f" AND {_NOT_DISPOSED}"
        if not include_archived:
            # Archived mail is handled mail. Kept for senders the user has taken
            # a decision on, so their status tabs stay reviewable (see docstring).
            disp_sub = (
                "SELECT LOWER(email) FROM email_newsletters WHERE account_id IN "
                "(SELECT id FROM email_accounts WHERE user_id = :uid"
                + (" AND id = :aid" if account_id else "") + ")"
            )
            folder_sql += (
                " AND (LOWER(COALESCE(em.folder, '')) <> 'archive'"
                f" OR LOWER(em.from_address->>'email') IN ({disp_sub}))"
            )
        if folder:
            # Narrowing to one folder still must NEVER hide a sender we've acted
            # on: unsubscribing / auto-archiving moves their mail out of the
            # inbox, so also include any sender carrying a saved disposition.
            # Otherwise the Unsubscribed / Auto-archive tabs go empty after a
            # refresh and the user can't review or undo their decisions.
            nl_sub = "account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
            if account_id:
                nl_sub += " AND id = :aid"
            nl_sub += ")"
            folder_sql += (
                " AND (LOWER(em.folder) = LOWER(:folder)"
                " OR LOWER(em.from_address->>'email') IN ("
                f"SELECT LOWER(email) FROM email_newsletters WHERE {nl_sub}))"
            )
            params["folder"] = folder

        # Always report how much of a sender's mail is still sitting in the inbox,
        # independent of the list scope, so the UI can lead with "still in your
        # inbox" while `count` covers the whole mailbox. Without this the number
        # meant different things on different rows.
        in_folder_sql = "COUNT(*) FILTER (WHERE LOWER(folder) = 'inbox')"
        rows = (await db.execute(text(
            f"""SELECT LOWER(from_address->>'email') AS email,
                       MAX(from_address->>'name') AS name,
                       COUNT(*) AS count,
                       {in_folder_sql} AS in_folder,
                       COUNT(*) FILTER (WHERE is_read = false) AS unread,
                       COUNT(*) FILTER (WHERE LOWER(folder) = 'archive') AS archived,
                       MAX(received_at) AS last_received,
                       MAX(unsubscribe_link) AS unsubscribe_link
                FROM email_messages em
                WHERE {scope}{folder_sql}
                  AND COALESCE(from_address->>'email','') <> ''
                  -- Never list the user themselves as a sender (the 'sent' folder
                  -- + self-CC'd mail is all from-self).
                  AND LOWER(from_address->>'email') NOT IN (
                    SELECT LOWER(email_address) FROM email_accounts
                    WHERE user_id = :uid)
                GROUP BY LOWER(from_address->>'email')
                -- Tie-break on the address so paging is stable: without a
                -- deterministic order, senders with equal volume could shuffle
                -- between pages and a row would be skipped or shown twice.
                ORDER BY count DESC, email ASC
                LIMIT :limit OFFSET :offset"""
        ), params)).fetchall()

        # How many distinct senders exist in scope, so the UI can say "showing
        # the top 1000 of 4,312" and offer the rest. A cleanup tool that quietly
        # hides the tail is worse than useless — the user believes they've dealt
        # with everything.
        total_row = (await db.execute(text(
            f"""SELECT COUNT(DISTINCT LOWER(from_address->>'email')) AS c
                FROM email_messages em
                WHERE {scope}{folder_sql}
                  AND COALESCE(from_address->>'email','') <> ''
                  AND LOWER(from_address->>'email') NOT IN (
                    SELECT LOWER(email_address) FROM email_accounts
                    WHERE user_id = :uid)"""
        ), params)).fetchone()

        # Merge newsletter disposition (APPROVED/UNSUBSCRIBED/AUTO_ARCHIVED).
        nl_params: dict[str, Any] = {"uid": user.email or "anonymous"}
        nl_scope = (
            "account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
        )
        if account_id:
            nl_scope += " AND id = :aid"
            nl_params["aid"] = account_id
        nl_scope += ")"
        nl_rows = (await db.execute(text(
            f"SELECT LOWER(email) AS email, status, auto_archive_filter_id "
            f"FROM email_newsletters WHERE {nl_scope}"
        ), nl_params)).fetchall()
        status_by_email = {r.email: r.status for r in nl_rows}
        filtered_emails = {
            r.email for r in nl_rows if r.auto_archive_filter_id
        }

        # NOTE: the sender-level ``email_senders.category`` is deliberately NOT
        # read here any more. Every chip this endpoint returns is now a label a
        # RULE actually wrote onto the sender's messages, so each one is
        # traceable to a rule in AI settings and correctable there. The
        # sender-level category still exists and still earns its keep — it is
        # what ranks a human correspondent above bulk mail in "important
        # emails" — but as a chip its inferred values (notably "Conversation")
        # named a category the user had never configured and matched no filter
        # tab, while masking the labels their rules had genuinely applied.

        # Per-sender cleanup categories DERIVED from the rule-labelled per-message
        # categories (email_messages.categories). This is the reliable signal the
        # cleaner runs on: the set powers the category filter tabs, and the
        # most-frequent one is the chip. Same scope/folder as the sender rollup.
        #
        # Matched on LOWER(TRIM(cat)) via the shared _LABEL_TALLY_SQL. The writer
        # (runner.py's LABEL action) stores the rule's label verbatim and never
        # trims, so an exact-case comparison here silently dropped every label
        # whose rule name differed by case or stray whitespace — the sender then
        # rendered as uncategorized despite its mail being labelled.
        not_self = (" AND LOWER(em.from_address->>'email') NOT IN ("
                    "SELECT LOWER(email_address) FROM email_accounts "
                    "WHERE user_id = :uid)")
        msgcat_rows = (await db.execute(text(
            _LABEL_TALLY_SQL.format(scope=scope, extra=folder_sql + not_self)
        ), {**params, "labels": _KNOWN_LABELS_LOWER})).fetchall()
        # email -> {lowercased label: count}
        label_counts: dict[str, dict[str, int]] = {}
        for r in msgcat_rows:
            label_counts.setdefault(r.email, {})[r.label] = int(r.n or 0)

        def _categories_for(email: str) -> list[str]:
            """The rule labels actually on this sender's mail, most-used first.

            Cleanup categories lead (they drive the filter tabs), then the
            conversation labels the Reply Zero rules write — Needs Reply,
            Awaiting Reply, FYI, Done, Follow-up. Both are REAL labels the user
            can see in AI settings and trace back to a rule.

            What is deliberately NOT here is the synthesized "Conversation"
            sender category. It is inferred, not written by any rule, so as a
            chip it named a category the user had never configured, matched no
            filter tab, and could not be corrected — while hiding the labels
            their rules had genuinely applied. It still exists as a SENDER-level
            classification (it is what ranks a real correspondent above bulk
            mail in "important emails"); it just isn't presented as a rule
            label, because it isn't one.
            """
            counts = label_counts.get(email, {})
            out = list(_cleanup_categories_ranked(counts))
            conv = sorted(
                ((low, n) for low, n in counts.items()
                 if n and low in _CONVERSATION_DISPLAY),
                key=lambda kv: kv[1], reverse=True,
            )
            for low, _n in conv:
                # Legacy and modern spellings collapse onto one display name
                # ("reply" and "needs reply" are the same chip, not two).
                name = _CONVERSATION_DISPLAY[low]
                if name not in out:
                    out.append(name)
            return out

        def _category_counts_for(email: str) -> dict[str, int]:
            """How many of this sender's messages carry each cleanup category.

            A sender legitimately belongs to several categories at once — a
            colleague sends both Calendar invites and Notifications — and the
            list already shows them under every one. But the row's headline
            count was the sender's WHOLE volume regardless of which category tab
            was open, so under "Notification" a person with 69 messages read as
            69 notifications when only 7 were. Same class of defect as the
            Analytics range selector: a number displayed under a filter that it
            does not respect.
            """
            return {_CLEANUP_BY_LOWER[low]: n
                    for low, n in label_counts.get(email, {}).items()
                    if n and low in _CLEANUP_BY_LOWER}

        def _labelled_count(email: str) -> int:
            """How many of this sender's messages carry ANY known rule label.

            Distinguishes "the rules ran and found nothing to clean up" from
            "the rules never reached this sender" — the latter is what the
            uncategorized sweep targets.
            """
            return sum(label_counts.get(email, {}).values())

        return {
            "senders": [
                {
                    "email": r.email,
                    "name": r.name or "",
                    "count": r.count,
                    # Of `count`, how many sit in the folder being cleaned.
                    # Differs from `count` only for senders pulled in by their
                    # saved disposition (see the query comment above).
                    "in_folder": r.in_folder or 0,
                    "unread": r.unread or 0,
                    "archived": r.archived or 0,
                    "read_rate": round((r.count - (r.unread or 0)) / r.count, 4)
                    if r.count else 0.0,
                    "last_received": r.last_received.isoformat()
                    if r.last_received else None,
                    "unsubscribe_link": r.unsubscribe_link,
                    # "UNHANDLED" = no decision yet (inbox-zero parity); only the
                    # three real dispositions are ever persisted.
                    "status": status_by_email.get(r.email, "UNHANDLED"),
                    # True when a provider-native auto-archive filter is in place
                    # (future mail blocked at the source, not just our sweep).
                    "filter_active": r.email in filtered_emails,
                    # Dominant cleanup category (chip) + the full set (filter tabs).
                    # Both are derived from rule labels, so there is no provisional
                    # guess to flag — category_source is always trustworthy here.
                    "category": (_categories_for(r.email) or [None])[0],
                    "categories": _categories_for(r.email),
                    # Per-category volume, so a row under an open category tab
                    # can report ITS count instead of the sender's total.
                    "category_counts": _category_counts_for(r.email),
                    "category_source": "rule",
                    # Messages the rules have labelled (any label, cleanup or
                    # conversation). 0 ⇒ this sender's mail was never classified,
                    # which is what the "Uncategorized" tab surfaces.
                    "labelled": _labelled_count(r.email),
                }
                for r in rows
            ],
            # Distinct senders in scope. > len(senders) means the list is capped
            # and the UI must say so rather than imply completeness.
            "total": int(total_row.c) if total_row else len(rows),
        }


class BulkActionRequest(BaseModel):
    action: str  # archive | trash | read | unread | star | unstar
    account_id: str | None = None
    message_ids: list[str] | None = None
    sender_email: str | None = None
    folder: str | None = None
    older_than_days: int | None = None
    only_read: bool | None = None


_BULK_DB_UPDATE = {
    "archive": "folder = 'archive'",
    "trash": "folder = 'trash'",
    "read": "is_read = true",
    "unread": "is_read = false",
    "star": "is_starred = true",
    "unstar": "is_starred = false",
}

# Rows an action would not actually change are excluded from the UPDATE, so
# ``affected`` means "messages this changed" rather than "messages matched".
# Without it, "Archive all" on a sender whose mail was archived last week
# reported "Archived 320 emails" having moved none — which reads as the action
# silently doing nothing (and re-pushed 320 no-op calls at the provider).
_BULK_ALREADY_APPLIED = {
    "archive": "LOWER(COALESCE(em.folder, '')) = 'archive'",
    "trash": "LOWER(COALESCE(em.folder, '')) = 'trash'",
    "read": "em.is_read = true",
    "unread": "em.is_read = false",
    "star": "em.is_starred = true",
    "unstar": "em.is_starred = false",
}


@router.post("/messages/bulk")
async def bulk_action(
    req: BulkActionRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Apply an action to many messages at once (archive/trash/read/star).

    The local DB is updated synchronously (authoritative for the UI); the
    provider is reconciled in the background so the request stays fast.

    There is deliberately NO row cap. A cap on a bulk action is silent
    truncation of something the user believes finished: "archive everything
    from this sender" would stop at N, report success, and leave the rest
    sitting there. The DB update is set-based, so its cost is the same whether
    it moves 50 rows or 50,000; the expensive side is the provider, which is
    batched and runs in the background (``_bulk_reconcile_provider``).

    What IS refused is an *unfiltered* bulk action — no ids, no sender, no
    folder, no age. That request means "trash my entire mailbox", which is
    never what a click in the cleaner meant, and uncapping made it reachable.
    """
    if req.action not in _BULK_DB_UPDATE:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action '{req.action}'. "
            f"Supported: {', '.join(_BULK_DB_UPDATE)}",
        )
    if not (req.message_ids or req.sender_email or req.folder
            or req.older_than_days or req.only_read):
        raise HTTPException(
            status_code=400,
            detail="Refusing an unfiltered bulk action: pass at least one of "
                   "message_ids, sender_email, folder, older_than_days, only_read.",
        )

    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous"}
        scope = _account_scope(req.account_id, params)
        clauses = [scope]
        if req.message_ids:
            clauses.append("em.id::text = ANY(:ids)")
            params["ids"] = req.message_ids
        if req.sender_email:
            clauses.append("LOWER(em.from_address->>'email') = LOWER(:sender)")
            params["sender"] = req.sender_email
        if req.folder:
            clauses.append("LOWER(em.folder) = LOWER(:folder)")
            params["folder"] = req.folder
        if req.older_than_days:
            clauses.append("em.received_at < now() - make_interval(days => :odays)")
            params["odays"] = req.older_than_days
        if req.only_read:
            clauses.append("em.is_read = true")
        # Never re-apply an action to mail that is already in that state — see
        # _BULK_ALREADY_APPLIED. Keeps `affected` honest and the provider quiet.
        clauses.append(f"NOT ({_BULK_ALREADY_APPLIED[req.action]})")
        # ARCHIVING must not reach into the bin. A sender-wide "Archive all"
        # carries no folder filter, so without this it pulled the sender's
        # TRASHED and junked mail back out into the archive — silently
        # un-deleting mail the user had already thrown away. Trashing keeps its
        # reach (archived mail is still live mail you may want to bin).
        if req.action == "archive":
            clauses.append(_NOT_DISPOSED)
        where_sql = " AND ".join(clauses)

        # One set-based UPDATE, RETURNING the provider ids we need to reconcile.
        # Doing it in a single statement (rather than SELECT-then-UPDATE-by-id)
        # keeps the write atomic and avoids shipping a 50k-element id array back
        # into the next query.
        rows = (await db.execute(text(
            f"""UPDATE email_messages SET {_BULK_DB_UPDATE[req.action]},
                       updated_at = now()
                 WHERE id IN (SELECT em.id FROM email_messages em WHERE {where_sql})
             RETURNING id, provider_message_id, account_id"""
        ), params)).fetchall()
        if not rows:
            return {"affected": 0}

        # Group provider message ids per account for background reconciliation.
        per_account: dict[str, list[str]] = {}
        for r in rows:
            per_account.setdefault(str(r.account_id), []).append(r.provider_message_id)
        for aid, pmids in per_account.items():
            background.add_task(_bulk_reconcile_provider, aid, pmids, req.action)

        return {"affected": len(rows)}


# How hard to push a bulk action at the provider before giving up. The dominant
# failure is transient (Gmail 429 / Graph 503 under a large sweep), so a couple
# of backed-off retries recover almost all of it; a permanent failure (deleted
# message, revoked scope) is not going to improve and is reported instead.
_RECONCILE_ATTEMPTS = 3
_RECONCILE_BACKOFF_S = (2, 8)


async def _bulk_reconcile_provider(
    account_id: str, provider_msg_ids: list[str], action: str
) -> None:
    """Push a bulk action to the provider (runs in background), retrying what
    fails and never leaving the local mirror claiming work that didn't happen.

    Unbounded by design — this is where an uncapped bulk action gets paid for,
    off the request path. The per-provider ``bulk_apply`` decides how: Gmail
    collapses it into batchModify calls of 1000, everything else walks the
    per-message API.

    Failures used to be swallowed whole: ``bulk_apply`` logged each one and
    returned, so a run where every call 429'd looked identical to a clean one.
    The local row already said 'archive', so the UI showed the mail as filed
    while the provider still had it in the inbox — and the next sync, which
    takes the provider as authoritative for ``folder``, pulled it straight back.
    That is the "I archived it and it came back" report. Now the failures are
    collected, retried with backoff, and what still won't apply has its local
    folder reverted so the mirror matches the mailbox instead of lying until the
    next sync corrects it.
    """
    # H4: called from _maybe_auto_archive (scheduler post-sync path); no
    # ambient tenant to inherit.
    db = await _get_db()
    try:
        # Unscoped session: background task, no request user. Missing account
        # raises 404 → caught by the broad handler below.
        async with provider_session(
            db, None, account_id=account_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                await _revert_unreconciled(db, account_id, provider_msg_ids, action)
                await db.commit()
                _log.error("email.bulk_reconcile_unauthed",
                           account_id=account_id, action=action,
                           count=len(provider_msg_ids))
                return
            pending = list(provider_msg_ids)
            for attempt in range(_RECONCILE_ATTEMPTS):
                failed: list[str] = []
                rekeys = await sess.provider.bulk_apply(pending, action, failed)
                # Outlook's /move mints a NEW message id and invalidates the old
                # one. Dropping these (as this job used to) leaves every
                # bulk-archived Outlook message pointing at a dead id, so the next
                # action on it 404s until a full re-sync happens to notice.
                for old_id, new_id in rekeys.items():
                    await db.execute(text(
                        "UPDATE email_messages SET provider_message_id = :new, "
                        "updated_at = now() "
                        "WHERE account_id = :aid AND provider_message_id = :old"
                    ), {"aid": account_id, "old": old_id, "new": new_id})
                pending = failed
                if not pending or attempt == _RECONCILE_ATTEMPTS - 1:
                    break
                await asyncio.sleep(_RECONCILE_BACKOFF_S[attempt])
            if pending:
                # Permanently un-appliable: make the mirror honest rather than
                # let the next sync spring it on the user.
                await _revert_unreconciled(db, account_id, pending, action)
                _log.error("email.bulk_reconcile_incomplete",
                           account_id=account_id, action=action,
                           failed=len(pending), total=len(provider_msg_ids))
            else:
                _log.info("email.bulk_reconcile_ok", account_id=account_id,
                          action=action, count=len(provider_msg_ids))
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.bulk_reconcile_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        await db.close()


async def _revert_unreconciled(
    db: Any, account_id: str, provider_msg_ids: list[str], action: str
) -> None:
    """Undo the local folder move for messages the provider never applied.

    Only the FOLDER actions are reverted: a mirror that says 'archive' while the
    message sits in the inbox upstream is an outright lie, and the next sync
    would flip it back anyway (confusingly, minutes later). Flag actions
    (read/star) are cosmetic and self-heal on the next sync, so they are left
    alone rather than fighting a user who has since changed them by hand."""
    if action not in ("archive", "trash") or not provider_msg_ids:
        return
    with contextlib.suppress(Exception):
        await db.execute(text(
            "UPDATE email_messages SET folder = 'inbox', updated_at = now() "
            "WHERE account_id = :aid AND provider_message_id = ANY(:pmids) "
            f"AND LOWER(COALESCE(folder, '')) = '{action}'"
        ), {"aid": account_id, "pmids": provider_msg_ids})


async def _maybe_auto_archive(account_id: str) -> None:
    """Archive freshly-synced inbox mail from senders marked AUTO_ARCHIVED (the
    bulk-archive 'Auto' action), then reconcile to the provider. This is what
    makes auto-archive apply to FUTURE mail, not just existing. Idempotent."""
    # H4: scheduler post-sync hook path; no ambient tenant to inherit.
    db = await _get_db()
    try:
        rows = (await db.execute(text(
            """SELECT em.id, em.provider_message_id
               FROM email_messages em
               JOIN email_newsletters nl
                 ON nl.account_id = em.account_id
                AND LOWER(nl.email) = LOWER(em.from_address->>'email')
               WHERE em.account_id = :aid AND nl.status = 'AUTO_ARCHIVED'
                 AND LOWER(em.folder) = 'inbox'"""
        ), {"aid": account_id})).fetchall()
        if not rows:
            return
        ids = [str(r.id) for r in rows]
        pmids = [r.provider_message_id for r in rows if r.provider_message_id]
        await db.execute(text(
            "UPDATE email_messages SET folder = 'archive', updated_at = now() "
            "WHERE id::text = ANY(:ids)"
        ), {"ids": ids})
        await db.commit()
        if pmids:
            await _bulk_reconcile_provider(account_id, pmids, "archive")
        _log.info("email.auto_archive_pass",
                  account_id=account_id, archived=len(ids))
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.auto_archive_failed",
                     account_id=account_id, error=str(exc)[:200])
    finally:
        await db.close()


class NewsletterUpdate(BaseModel):
    account_id: str
    email: str
    name: str | None = None
    status: str  # APPROVED | UNSUBSCRIBED | AUTO_ARCHIVED
    unsubscribe_link: str | None = None


@router.get("/newsletters")
async def list_newsletters(
    account_id: str | None = Query(None),
    user: UserContext = Depends(get_current_user),
):
    """List newsletter dispositions for the user's accounts."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous"}
        scope = "account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"
        rows = (await db.execute(text(
            f"""SELECT id, account_id, email, name, status, unsubscribe_link,
                       auto_archive_filter_id, updated_at
                FROM email_newsletters WHERE {scope} ORDER BY updated_at DESC"""
        ), params)).fetchall()
        return {
            "newsletters": [
                {"id": str(r.id), "account_id": str(r.account_id), "email": r.email,
                 "name": r.name or "", "status": r.status,
                 "unsubscribe_link": r.unsubscribe_link,
                 "filter_active": bool(r.auto_archive_filter_id),
                 "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in rows
            ]
        }


async def _apply_newsletter_status(
    db: Any,
    background: BackgroundTasks,
    account_id: str,
    email: str,
    name: str | None,
    status: str,
    link: str | None,
    *,
    create_filter: bool,
) -> int:
    """Persist a sender's disposition and apply its side-effects.

    UNSUBSCRIBED/AUTO_ARCHIVED archive the sender's existing inbox mail (locally
    + provider in the background). ``create_filter`` additionally schedules a
    provider-native auto-archive filter so FUTURE mail is blocked at the source
    (Gmail filter / Outlook rule), with the sync-time sweep as the fallback.
    Returns the number of existing messages archived."""
    await db.execute(text(
        """INSERT INTO email_newsletters
             (account_id, email, name, status, unsubscribe_link, updated_at)
           VALUES (:aid, LOWER(:email), :name, :status, :link, now())
           ON CONFLICT (account_id, email) DO UPDATE SET
             name = COALESCE(EXCLUDED.name, email_newsletters.name),
             status = EXCLUDED.status,
             unsubscribe_link = COALESCE(EXCLUDED.unsubscribe_link,
                                         email_newsletters.unsubscribe_link),
             updated_at = now()"""
    ), {"aid": account_id, "email": email, "name": name,
        "status": status, "link": link})
    # No commit here: both callers are converted request handlers (H2) whose
    # `_tenant_session` commits on clean exit — BEFORE the response is sent,
    # so the BackgroundTasks scheduled below always see the committed rows.
    # A mid-block commit would end that transaction and drop the tenant GUC.

    archived = 0
    if status in ("UNSUBSCRIBED", "AUTO_ARCHIVED"):
        rows = (await db.execute(text(
            """SELECT em.id, em.provider_message_id
               FROM email_messages em
               WHERE em.account_id = :aid
                 AND LOWER(em.from_address->>'email') = LOWER(:email)
                 AND LOWER(em.folder) = 'inbox'"""
        ), {"aid": account_id, "email": email})).fetchall()
        if rows:
            ids = [str(r.id) for r in rows]
            await db.execute(text(
                "UPDATE email_messages SET folder = 'archive', updated_at = now() "
                "WHERE id::text = ANY(:ids)"
            ), {"ids": ids})
            archived = len(ids)
            background.add_task(
                _bulk_reconcile_provider, account_id,
                [r.provider_message_id for r in rows], "archive",
            )
    if create_filter:
        background.add_task(_create_block_filter, account_id, email)
    elif status == "APPROVED":
        # Re-approving a sender must tear down any auto-archive filter, else
        # future mail keeps getting archived at the provider.
        background.add_task(_remove_block_filter, account_id, email)
    return archived


@router.post("/newsletters")
async def upsert_newsletter(
    req: NewsletterUpdate,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Set a sender's disposition. UNSUBSCRIBED/AUTO_ARCHIVED also archives the
    sender's existing inbox mail; AUTO_ARCHIVED additionally creates a
    provider-native filter so future mail skips the inbox at the source."""
    if req.status not in ("APPROVED", "UNSUBSCRIBED", "AUTO_ARCHIVED"):
        raise HTTPException(status_code=400, detail=f"Bad status: {req.status}")
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        archived = await _apply_newsletter_status(
            db, background, req.account_id, req.email, req.name, req.status,
            req.unsubscribe_link, create_filter=(req.status == "AUTO_ARCHIVED"),
        )
        return {"ok": True, "status": req.status, "archived": archived}


# ── Real unsubscribe: RFC 8058 one-click + mailto, with SSRF guard ───────────


def _host_is_public(host: str) -> bool:
    """True only if every address ``host`` resolves to is a public IP (SSRF
    guard — blocks localhost, private ranges, link-local, etc.)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 — unresolvable host → unsafe
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def _is_safe_external_url(url: str) -> bool:
    """http(s) scheme + a hostname resolving only to public IPs."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return await asyncio.to_thread(_host_is_public, parsed.hostname)


async def _http_unsubscribe(url: str) -> tuple[bool, str]:
    """Unsubscribe via an https List-Unsubscribe target.

    Tries the RFC 8058 one-click POST (``List-Unsubscribe=One-Click``) first,
    then falls back to a plain GET (many mailers honour a GET on the same URL).
    Returns ``(succeeded, detail)``."""
    if not await _is_safe_external_url(url):
        return False, "unsafe-url"
    try:
        # follow_redirects=False: the initial URL is SSRF-validated, but httpx
        # would follow a 3xx to an UNVALIDATED internal target (cloud metadata
        # 169.254.169.254, localhost, private ranges) — the guard only ran on the
        # first hop. RFC 8058 one-click returns 200 directly, so we don't chase
        # redirects; the mailto / provider-filter fallbacks cover the rest.
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=10.0,
            headers={"User-Agent": "Metorite-Unsubscribe/1.0"},
        ) as client:
            try:
                resp = await client.post(
                    url, content=b"List-Unsubscribe=One-Click",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.is_success:
                    return True, "one-click-post"
            except httpx.HTTPError:
                pass  # fall through to GET
            resp = await client.get(url)
            return resp.is_success, ("get" if resp.is_success
                                     else f"http-{resp.status_code}")
    except httpx.HTTPError as exc:
        return False, str(exc)[:120]


async def _mailto_unsubscribe(provider: Any, mailto: str) -> tuple[bool, str]:
    """Send the unsubscribe email a ``mailto:`` List-Unsubscribe target asks for
    (RFC 2369), using the account's own send path."""
    try:
        parsed = urlparse(mailto)
        to_addr = parsed.path.strip()
        if not to_addr:
            return False, "no-address"
        qs = parse_qs(parsed.query)
        subject = (qs.get("subject") or ["unsubscribe"])[0]
        body = (qs.get("body") or ["Please unsubscribe me from this list."])[0]
        await provider.send_message(to=[to_addr], subject=subject, body_text=body)
        return True, "mailto"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]


async def _create_block_filter(
    account_id: str, email: str, label: str | None = None
) -> None:
    """Best-effort background task: create a provider-native auto-archive filter
    for ``email`` and record its id on the newsletter row. No-ops gracefully for
    providers without filters (IMAP) — the AUTO_ARCHIVED sweep covers those."""
    # H4: background consumer — _create_block_filter runs as a
    # post-response BackgroundTask; no ambient tenant to inherit.
    db = await _get_db()
    try:
        # Unscoped session: background task, no request user. Missing account
        # raises 404 → caught by the broad handler below.
        async with provider_session(
            db, None, account_id=account_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                return
            filter_id = await sess.provider.create_filter(
                from_email=email, archive=True, label=label
            )
            if filter_id:
                await db.execute(text(
                    "UPDATE email_newsletters SET auto_archive_filter_id = :fid, "
                    "updated_at = now() WHERE account_id = :aid "
                    "AND LOWER(email) = LOWER(:email)"
                ), {"fid": filter_id, "aid": account_id, "email": email})
        await db.commit()
        _log.info("email.block_filter", account_id=account_id, email=email,
                  filter_id=filter_id or "none")
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.block_filter_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        await db.close()


async def _remove_block_filter(account_id: str, email: str) -> None:
    """Best-effort background task: delete the provider-native auto-archive
    filter recorded for ``email`` and clear it on the newsletter row. No-ops when
    no filter is recorded (e.g. IMAP, or never auto-archived)."""
    # H4: background consumer — _remove_block_filter runs as a
    # post-response BackgroundTask; no ambient tenant to inherit.
    db = await _get_db()
    try:
        row = (await db.execute(text(
            """SELECT auto_archive_filter_id AS fid FROM email_newsletters
               WHERE account_id = :aid AND LOWER(email) = LOWER(:email)"""
        ), {"aid": account_id, "email": email})).fetchone()
        if not row or not row.fid:
            return
        # Unscoped session: background task, no request user. The row is
        # cleared even when auth fails — matching the pre-refactor behaviour
        # (the filter id is stale either way once the user unblocked).
        async with provider_session(
            db, None, account_id=account_id, require_auth=False,
        ) as sess:
            if sess.authed:
                await sess.provider.delete_filter(row.fid)
            await db.execute(text(
                "UPDATE email_newsletters SET auto_archive_filter_id = NULL, "
                "updated_at = now() WHERE account_id = :aid "
                "AND LOWER(email) = LOWER(:email)"
            ), {"aid": account_id, "email": email})
        await db.commit()
        _log.info("email.block_filter_removed", account_id=account_id, email=email)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.block_filter_remove_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        await db.close()


class UnsubscribeRequest(BaseModel):
    account_id: str
    email: str
    name: str | None = None
    unsubscribe_link: str | None = None


@router.post("/unsubscribe")
async def unsubscribe_sender(
    req: UnsubscribeRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Actually unsubscribe from a sender.

    Performs a real RFC 8058 one-click POST for an https List-Unsubscribe target,
    or sends the unsubscribe email for a ``mailto:`` target. On success → marks
    UNSUBSCRIBED. If there's no usable link or the attempt fails, falls through
    to a *block* (AUTO_ARCHIVED + a provider-native filter) so future mail is
    still handled rather than silently continuing to the inbox. Either way the
    sender's existing inbox mail is archived. Returns what was actually done so
    the UI can tell the user (unsubscribed vs blocked)."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")

        # Use the link the UI passed; otherwise recover the best one we stored.
        link = req.unsubscribe_link
        if not link:
            row = (await db.execute(text(
                """SELECT MAX(unsubscribe_link) AS link FROM email_messages
                   WHERE account_id = :aid
                     AND LOWER(from_address->>'email') = LOWER(:email)
                     AND unsubscribe_link IS NOT NULL"""
            ), {"aid": req.account_id, "email": req.email})).fetchone()
            link = row.link if row else None

        ok = False
        method = "none"
        detail = "no-link"
        low = (link or "").lower()
        if low.startswith("http"):
            method = "one-click"
            ok, detail = await _http_unsubscribe(link)
        elif low.startswith("mailto:"):
            method = "mailto"
            async with provider_session(
                db, user.email or "anonymous",
                account_id=req.account_id, require_auth=False,
            ) as sess:
                if sess.authed:
                    ok, detail = await _mailto_unsubscribe(sess.provider, link)
                else:
                    detail = "auth-failed"
            # Rotated creds staged by provider_session land at the wrapper's
            # exit commit.

        # Unsubscribe worked → UNSUBSCRIBED (the sender stops; no filter needed).
        # Otherwise block: AUTO_ARCHIVED + a provider filter so future mail is
        # auto-archived instead of silently arriving.
        if ok:
            status = "UNSUBSCRIBED"
        else:
            status = "AUTO_ARCHIVED"
            method = "blocked"

        archived = await _apply_newsletter_status(
            db, background, req.account_id, req.email, req.name, status, link,
            create_filter=(status == "AUTO_ARCHIVED"),
        )
        return {"ok": ok, "method": method, "detail": detail,
                "status": status, "archived": archived, "unsubscribe_link": link}


EMAIL_CATEGORIES = [
    "Newsletter", "Marketing", "Receipt", "Calendar", "Notification",
    "Cold Email", CONVERSATION_SENDER_CATEGORY, "Support", "Unknown",
]

# The label vocabulary now lives in core, because the inbox's Uncategorized chip
# and the Email Cleaner's Uncategorized tab must agree on what "categorized"
# means. A sender's category is rolled up from these — the Email Cleaner
# PROJECTS the rules, it never re-classifies. Conversation is inferred from
# conversation activity; Support/Unknown are vocabulary-only (no producer).
_CLEANUP_CATEGORIES = list(CLEANUP_CATEGORIES)
_CLEANUP_BY_LOWER = {c.lower(): c for c in _CLEANUP_CATEGORIES}
_CONVERSATION_LOWER = CONVERSATION_LABELS_LOWER
_KNOWN_LABELS_LOWER = KNOWN_LABELS_LOWER
# How many of a sender's messages the rules must have labelled before the sender
# is *persisted* with that category (mirrors the auto-learn consistency bar).
_MIN_RULE_MESSAGES = 3


def canonical_cleanup_category(label: str | None) -> str | None:
    """Canonical cleanup-category name for a raw per-message label, else None.

    The rule engine writes ``email_actions.label`` verbatim, so a hand-edited
    rule can store " newsletter" or "NEWSLETTER" and still mean the preset. Both
    readers normalise through here so the chips, the filter tabs and the sender
    rollup can never disagree about what counts as a Newsletter.
    """
    return _CLEANUP_BY_LOWER.get((label or "").strip().lower())


# One tally query, used by BOTH the /senders read path and the sender-category
# job, so the two can't drift on casing or on which labels count. Callers
# interpolate ``scope``/``extra`` and bind :labels.
_LABEL_TALLY_SQL = """
    SELECT LOWER(em.from_address->>'email') AS email,
           LOWER(TRIM(cat)) AS label,
           COUNT(DISTINCT em.id) AS n
      FROM email_messages em
      CROSS JOIN LATERAL unnest(em.categories) AS cat
     WHERE {scope}{extra}
       AND LOWER(TRIM(cat)) = ANY(:labels)
       AND COALESCE(em.from_address->>'email','') <> ''
     GROUP BY 1, 2
"""


def _cleanup_categories_ranked(label_counts: dict[str, int]) -> list[str]:
    """Cleanup categories present on a sender's mail, most-used first."""
    hits = [(_CLEANUP_BY_LOWER[low], n)
            for low, n in label_counts.items()
            if n and low in _CLEANUP_BY_LOWER]
    hits.sort(key=lambda kv: kv[1], reverse=True)
    return [c for c, _ in hits]


def _rule_category(label_counts: dict[str, int]) -> str | None:
    """Project a sender's category from the rule engine's per-message labels.

    ``label_counts`` maps a lowercased rule label → how many of the sender's
    messages carry it. Returns the dominant cleanup category when the rules have
    labelled enough of the sender's mail, else "Conversation" when there's an
    ongoing exchange with them, else None (the sender simply stays uncategorized
    — there is no second classifier to fall back to).

    "Conversation" requires ``top_n == 0``: not merely more conversation than
    cleanup, but NO cleanup label at all. A newsletter someone once replied to
    stays a Newsletter. Note this is what separates it from Cold Email, which is
    also one human writing to another — the difference is that a reply came
    back."""
    cleanup = {proper: label_counts.get(low, 0)
               for low, proper in _CLEANUP_BY_LOWER.items()}
    top_cat, top_n = max(cleanup.items(), key=lambda kv: kv[1], default=(None, 0))
    if top_cat and top_n >= _MIN_RULE_MESSAGES:
        return top_cat
    conv_n = sum(label_counts.get(low, 0) for low in _CONVERSATION_LOWER)
    if conv_n >= _MIN_RULE_MESSAGES and top_n == 0:
        return CONVERSATION_SENDER_CATEGORY
    return None


async def _upsert_sender_category(
    db: Any, account_id: str, email: str, name: str, category: str, source: str,
) -> None:
    """Persist a sender's category + its provenance. Never overwrites a 'user'
    override (reserved for a future manual set-category flow)."""
    await db.execute(text(
        """INSERT INTO email_senders
             (account_id, email, name, category, category_source, categorized_at)
           VALUES (:aid, :email, :name, :cat, :src, now())
           ON CONFLICT (account_id, email) DO UPDATE SET
             name = COALESCE(EXCLUDED.name, email_senders.name),
             category = EXCLUDED.category,
             category_source = EXCLUDED.category_source,
             categorized_at = now(), updated_at = now()
           WHERE email_senders.category_source IS DISTINCT FROM 'user'"""
    ), {"aid": account_id, "email": email, "name": name, "cat": category,
        "src": source})


async def _categorize_senders_job(account_id: str, limit: int) -> None:
    """Background: assign a category to the account's busiest senders.

    This is a PROJECTION, not a classifier. A sender's category is rolled up
    from the rule engine's per-message labels (``email_messages.categories``) —
    the dominant cleanup category, or Conversation for an ongoing exchange.
    There is no second opinion: a sender the rules haven't labelled stays
    uncategorized until they do, and the Email Cleaner lists it either way.

    It exists (rather than everything reading ``email_messages.categories``
    live) because the digest and the ``sender_category`` search filter need a
    stable per-sender column. ``/senders`` derives the same values on the fly
    through the same ``_LABEL_TALLY_SQL`` + ``_rule_category`` helpers, so the
    two views cannot disagree.
    """
    # H4: background consumer — _categorize_senders_job runs as a
    # BackgroundTask and from the scheduler; no ambient tenant to inherit.
    db = await _get_db()
    try:
        # Busiest senders, with their CURRENT category/source so a 'user'
        # override is never overwritten.
        rows = (await db.execute(text(
            """SELECT LOWER(em.from_address->>'email') AS email,
                      MAX(em.from_address->>'name') AS name,
                      COUNT(*) AS volume,
                      MAX(se.category_source) AS cur_source
               FROM email_messages em
               LEFT JOIN email_senders se
                 ON se.account_id = em.account_id
                AND se.email = LOWER(em.from_address->>'email')
               WHERE em.account_id = :aid
                 AND COALESCE(em.from_address->>'email','') <> ''
               GROUP BY LOWER(em.from_address->>'email')
               ORDER BY COUNT(*) DESC LIMIT :limit"""
        ), {"aid": account_id, "limit": limit})).fetchall()
        if not rows:
            return
        # The account's own address + configured org domains → sender_scope, so we
        # never bucket the user's own / same-org senders into a RECEIVE category.
        acc = (await db.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = :id"
        ), {"id": account_id})).fetchone()
        self_email = (acc.email_address if acc else "") or ""
        from gateway.routes.email.automation.identity import resolve_org_domains  # noqa: PLC0415
        org_domains = await resolve_org_domains(db, account_id)

        # Never categorize the user's OWN address as a "sender". Keep teammates
        # ('internal') — they are legitimate senders. Also skip 'user' overrides.
        cands = [
            r for r in rows
            if sender_scope(r.email, self_email, org_domains) != "self"
            and (r.cur_source or "") != "user"
        ]
        if not cands:
            return

        # Roll up the rule engine's per-message labels for these senders in one
        # pass: {email: {lowercased label: message count}}. Shares the exact SQL
        # the /senders read path uses so the persisted column and the live
        # derivation can never disagree.
        emails = [r.email for r in cands]
        tally_rows = (await db.execute(text(
            _LABEL_TALLY_SQL.format(
                scope="em.account_id = :aid",
                extra=" AND LOWER(em.from_address->>'email') = ANY(:emails)",
            )
        ), {"aid": account_id, "emails": emails,
            "labels": _KNOWN_LABELS_LOWER})).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for t in tally_rows:
            counts.setdefault(t.email, {})[t.label] = int(t.n)

        # Project the rule engine's per-message labels into a sender category —
        # the ONLY categorization there is. There is no cold-start LLM fallback:
        # the old one persisted provisional 'inferred' guesses off a thin signal,
        # never self-corrected, and misled the user. A sender the rules haven't
        # labelled stays uncategorized until they do (the cleaner still lists it;
        # it just carries no category chip, and the uncategorized sweep can fill
        # it in from learned patterns).
        stale: list[str] = []
        for r in cands:
            rcat = _rule_category(counts.get(r.email, {}))
            if rcat:
                await _upsert_sender_category(
                    db, account_id, r.email, r.name or "", rcat, "rule")
            else:
                stale.append(r.email)
        # A sender whose labels were since removed (rule deleted, user cleared
        # the chips) must lose its projection too, else the digest and the
        # sender_category search filter keep reporting a category /senders no
        # longer shows. 'user' overrides are excluded from cands already.
        if stale:
            await db.execute(text(
                "DELETE FROM email_senders WHERE account_id = :aid "
                "AND email = ANY(:emails) AND category_source = 'rule'"
            ), {"aid": account_id, "emails": stale})
        await db.commit()

        # One-time cleanup: retire any stale 'inferred' guesses left by earlier
        # runs so they stop surfacing (rule/user categories are untouched).
        await db.execute(text(
            "DELETE FROM email_senders WHERE account_id = :aid "
            "AND category_source = 'inferred'"
        ), {"aid": account_id})
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.categorize_job_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        await db.close()


class CategorizeRequest(BaseModel):
    account_id: str
    limit: int = 60


@router.post("/senders/categorize")
async def categorize_senders(
    req: CategorizeRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Re-project sender categories from the rule labels (background).

    Not a classifier — it only rolls up ``email_messages.categories``. To make
    the rules actually label more mail, use ``/email/senders/auto-categorize``
    (learned-pattern sweep) or ``/email/rules/process-past`` (full re-run).
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    background.add_task(_categorize_senders_job, req.account_id, min(req.limit, 300))
    return {"scheduled": True}


@router.get("/senders/categories")
async def sender_categories(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """List the category vocabulary + per-category sender counts."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        rows = (await db.execute(text(
            """SELECT category, COUNT(*) AS c FROM email_senders
               WHERE account_id = :aid AND category IS NOT NULL
               GROUP BY category"""
        ), {"aid": account_id})).fetchall()
        return {
            "categories": EMAIL_CATEGORIES,
            "counts": {r.category: r.c for r in rows},
        }


async def _llm_is_cold(email: dict[str, str]) -> tuple[bool, str]:
    """Classify whether an email is cold outreach. (is_cold, reason)."""
    try:
        sys_prompt = (
            "Decide if this is a COLD email: unsolicited sales, marketing, or "
            "recruiting outreach from someone with no prior relationship to the "
            'recipient. Respond ONLY JSON {"cold": <bool>, "reason": "<short>"}.'
        )
        # Richer envelope (the cold check reuses the classifier email dict, which
        # carries from_name/to/cc/date): a name + direct-vs-bulk addressing helps
        # tell a personal approach from a blast.
        frm = email.get("from", "")
        from_disp = (f"{email['from_name']} <{frm}>"
                     if email.get("from_name") else frm)
        to_line = f"To: {email['to']}\n" if email.get("to") else ""
        cc_line = f"Cc: {email['cc']}\n" if email.get("cc") else ""
        date_line = f"Date: {email['date']}\n" if email.get("date") else ""
        user_prompt = (
            f"From: {from_disp}\n{to_line}{cc_line}{date_line}"
            f"Subject: {email.get('subject', '')}\n"
            f"Body:\n{(email.get('body', '') or '')[:1500]}"
        )
        data, _content, _used = await _llm_json(
            "tier-fast",
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            max_tokens=500,
        )
        if isinstance(data, dict):
            return bool(data.get("cold")), str(data.get("reason", ""))[:300]
        return False, ""
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.cold_classify_failed", error=str(exc)[:200])
        return False, ""


async def _maybe_block_cold(
    db: Any, provider: Any, account_id: str, message_id: str,
    provider_msg_id: str, email: dict[str, str], blocker: str,
) -> None:
    """Cold-email gate: for a first-time, non-whitelisted sender, LLM-classify
    and (if cold) label/archive + record. Runs only when no rule matched."""
    sender = (email.get("from") or "").lower()
    if not sender:
        return
    # Already known to the cold-sender table (flagged or whitelisted) → skip.
    seen = (await db.execute(text(
        "SELECT status FROM email_cold_senders "
        "WHERE account_id = :aid AND from_email = :e"
    ), {"aid": account_id, "e": sender})).fetchone()
    if seen:
        return
    # Replied-to / known sender (we've emailed them) → not cold.
    replied = (await db.execute(text(
        """SELECT 1 FROM email_messages
           WHERE account_id = :aid AND LOWER(folder) = 'sent'
             AND to_addresses @> :tojson LIMIT 1"""
    ), {"aid": account_id, "tojson": json.dumps([{"email": sender}])})).fetchone()
    if replied:
        return
    is_cold, reason = await _llm_is_cold(email)
    if not is_cold:
        return
    await db.execute(text(
        """INSERT INTO email_cold_senders (account_id, from_email, status, reason)
           VALUES (:aid, :e, 'AI_LABELED_COLD', :reason)
           ON CONFLICT (account_id, from_email) DO NOTHING"""
    ), {"aid": account_id, "e": sender, "reason": reason})
    actions: list[str] = []
    try:
        if blocker == "ARCHIVE":
            await db.execute(text(
                "UPDATE email_messages SET folder='archive', updated_at=now() "
                "WHERE id=:id"), {"id": message_id})
            await provider.move_to_folder(provider_msg_id, "archive")
            actions = ["ARCHIVE", "LABEL"]
        else:
            actions = ["LABEL"]
        # Through the shared label writer, so the label lands on BOTH surfaces.
        # This used to push to the provider only, which made an AI-blocked cold
        # email invisible to the Cold Email chip, the quick filter and this very
        # cleaner — the one place the user goes to deal with cold outreach.
        # Lazy import: runner imports _maybe_block_cold from here.
        from gateway.routes.email.automation.runner import (  # noqa: PLC0415
            apply_label,
        )
        await apply_label(
            db, provider, message_id, provider_msg_id, "Cold Email")
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.cold_action_failed", error=str(exc)[:120])
    await db.execute(text(
        """INSERT INTO email_executed_rules
             (account_id, rule_id, rule_name, message_id, provider_message_id,
              subject, from_address, status, automated, actions_taken, reason)
           VALUES (:aid, NULL, 'Cold Email Blocker', :mid, :pmid, :subj, :frm,
                   'APPLIED', true, :acts, :reason)"""
    ), {"aid": account_id, "mid": message_id, "pmid": provider_msg_id,
        "subj": email.get("subject", ""), "frm": sender,
        "acts": json.dumps(actions), "reason": reason})


class ColdSenderUpdate(BaseModel):
    account_id: str
    from_email: str
    status: str  # AI_LABELED_COLD | USER_REJECTED_COLD


@router.get("/cold-senders")
async def list_cold_senders(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """List cold-email verdicts (flagged + whitelisted) for an account."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        rows = (await db.execute(text(
            """SELECT from_email, status, reason, updated_at
               FROM email_cold_senders WHERE account_id = :aid
               ORDER BY updated_at DESC LIMIT 500"""
        ), {"aid": account_id})).fetchall()
        return {
            "cold_senders": [
                {"from_email": r.from_email, "status": r.status,
                 "reason": r.reason,
                 "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in rows
            ]
        }


@router.post("/cold-senders")
async def upsert_cold_sender(
    req: ColdSenderUpdate,
    user: UserContext = Depends(get_current_user),
):
    """Set a sender's cold verdict — USER_REJECTED_COLD whitelists them."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        await db.execute(text(
            """INSERT INTO email_cold_senders
                 (account_id, from_email, status, updated_at)
               VALUES (:aid, LOWER(:e), :status, now())
               ON CONFLICT (account_id, from_email) DO UPDATE SET
                 status = EXCLUDED.status, updated_at = now()"""
        ), {"aid": req.account_id, "e": req.from_email, "status": req.status})
        return {"ok": True, "status": req.status}
