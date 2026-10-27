"""Automation · analytics — what the mailbox says you should do about it.

The old ``/analytics/overview`` answered "what is in my mailbox?" — a question
the sidebar already answers. Worse, it answered it dishonestly: the page header
read "last 30 days" while ``total``/``unread``/``top_senders``/``by_folder``
carried no date predicate at all, so on the live account the cards showed 6,803
and 4,517 (everything back to 2019) under a 30-day heading whose real figures
were 1,431 and 739. Only the volume chart moved when the range selector moved.

So every number here is scoped to the window, and the two that deliberately are
NOT — the reply backlogs, which are a standing state rather than a flow — say
"right now" in their own labels rather than borrowing the window's.

The organising rule for what earns a place: **a metric ships only if a user can
act on it.** Read-rate, starred count and attachment count were dropped for
failing that test; per-folder counts were dropped for duplicating the sidebar.
What replaced them is response behaviour (are you answering people?), the reply
backlogs (who is blocked on you), and the senders you are paying attention to
without ever replying — which is exactly the Email Cleaner's input.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, Query
from gateway.routes.email.account_ref import resolve_account_filter
from gateway.routes.email.automation.senders import DISPOSED_FOLDERS
from gateway.routes.email.core import _account_scope, _tenant_session, router
from gateway.routes.email.mailbox_access import mailbox_ids_sql, private_predicate
from sqlalchemy import text

# Mail that was never really "received into" the mailbox, or has already been
# thrown away. Counting it would inflate every sender with noise the user has
# already dealt with. Shared with the Email Cleaner so a sender's volume means
# the same thing on both screens.
_LIVE_MAIL = f"LOWER(COALESCE(m.folder, '')) NOT IN {DISPOSED_FOLDERS}"
_INBOUND = f"LOWER(COALESCE(m.folder, '')) <> 'sent' AND {_LIVE_MAIL}"

# A sender is only "yours" if it is one of the connected accounts. Without this
# the user is their own #1 correspondent (the whole Sent folder is from-self).
# "Self" is the member's private mailboxes only (D-SM-5): private_predicate.
_NOT_SELF = ("LOWER(COALESCE(m.from_address->>'email', '')) NOT IN "
             "(SELECT LOWER(email_address) FROM email_accounts "
             "WHERE " + private_predicate(uid="uid") + ")")


def _round(value: Any, places: int = 1) -> float | None:
    """Postgres numerics arrive as Decimal; JSON wants a float or nothing."""
    return None if value is None else round(float(value), places)


@router.get("/analytics/overview")
async def analytics_overview(
    account_id: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    user: UserContext = Depends(get_current_user),
):
    """Inbox analytics: flow, responsiveness, reply backlog, noise, automation.

    Every figure except ``backlog`` covers the trailing ``days`` window, and each
    headline figure carries its ``*_prev`` counterpart from the window of equal
    length immediately before it — a number with no trend is not actionable.
    """
    async with _tenant_session() as db:
        account_id = await resolve_account_filter(
            db, account_id, user.email or "anonymous")
        params: dict[str, Any] = {"uid": user.email or "anonymous", "days": days}
        scope = _account_scope(account_id, params, level="read")
        # _account_scope names the table `em`; these queries alias it `m`.
        scope = scope.replace("em.account_id", "m.account_id")

        # `now()` is evaluated per-statement, but all of these run inside one
        # transaction against a mailbox that is not being written mid-request,
        # so the two windows stay adjacent and non-overlapping.
        win = "m.received_at >= now() - make_interval(days => :days)"
        prev_win = ("m.received_at >= now() - make_interval(days => :days * 2) "
                    "AND m.received_at < now() - make_interval(days => :days)")

        flow = (await db.execute(text(
            f"""SELECT
                  COUNT(*) FILTER (WHERE {win} AND {_INBOUND}) AS received,
                  COUNT(*) FILTER (WHERE {prev_win} AND {_INBOUND})
                      AS received_prev,
                  COUNT(*) FILTER (WHERE {win}
                      AND LOWER(m.folder) = 'sent') AS sent,
                  COUNT(*) FILTER (WHERE {prev_win}
                      AND LOWER(m.folder) = 'sent') AS sent_prev,
                  COUNT(*) FILTER (WHERE {win} AND {_INBOUND}
                      AND m.is_read = false) AS unread
                FROM email_messages m WHERE {scope}"""
        ), params)).fetchone()

        volume_rows = (await db.execute(text(
            f"""SELECT to_char(date_trunc('day', m.received_at), 'YYYY-MM-DD')
                         AS day,
                       COUNT(*) FILTER (WHERE LOWER(m.folder) <> 'sent')
                         AS received,
                       COUNT(*) FILTER (WHERE LOWER(m.folder) = 'sent') AS sent
                FROM email_messages m
                WHERE {scope} AND {win} AND {_LIVE_MAIL}
                GROUP BY day ORDER BY day"""
        ), params)).fetchall()

        responsiveness = await _responsiveness(db, scope, params)
        backlog = await _backlog(db, params, account_id)
        noisy = await _noisy_senders(db, scope, params, win)
        categories = await _categories(db, scope, params, win, prev_win)
        rule_stats, action_stats, auto_handled = await _automation(
            db, params, account_id)

        # Data-health alarm: statused conversations still showing #112 damage.
        # Zero is the only healthy value; a non-zero count means the cleaner or
        # runner re-damaged conversations and the repair path needs to run. Same
        # damage definition the one-off repair script uses — one shared SQL, so
        # the alarm can never drift from the fix.
        from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
            count_damaged_conversation_threads,
        )
        damaged_threads = await count_damaged_conversation_threads(
            db, account_id, user.email or None)

        received = flow.received or 0
        return {
            "range": {"days": days},
            "flow": {
                "received": received,
                "received_prev": flow.received_prev or 0,
                "sent": flow.sent or 0,
                "sent_prev": flow.sent_prev or 0,
                "unread": flow.unread or 0,
                "auto_handled": auto_handled,
                # What share of the mail that arrived never needed the user.
                # Capped at 1.0: the rules log and the message window are
                # counted independently, so a backfill can log more applies
                # than there were arrivals.
                "auto_handled_rate": (
                    min(1.0, round(auto_handled / received, 4))
                    if received else 0.0),
            },
            "responsiveness": responsiveness,
            "backlog": backlog,
            "volume": [
                {"day": r.day, "received": r.received or 0, "sent": r.sent or 0}
                for r in volume_rows
            ],
            "categories": categories,
            "noisy_senders": noisy,
            "rule_stats": rule_stats,
            "action_stats": action_stats,
            # Regression alarm for the #110 one-classification invariant. Zero
            # is healthy; non-zero means conversations were re-damaged.
            "data_health": {"damaged_threads": damaged_threads},
        }


async def _responsiveness(db: Any, scope: str, params: dict[str, Any]) -> dict:
    """How fast, and how often, the user answers mail that arrives.

    Measured per THREAD, not per message: five messages in one conversation are
    one thing to respond to, and counting them separately would let a single
    chatty thread dominate the median.

    A thread enters the window by the arrival of its first inbound message, and
    a reply counts whenever it was sent — including after the window closed.
    Anchoring on arrival is what makes ``reply_rate`` mean "of the mail that
    came in, how much did I answer"; anchoring on the reply instead would let a
    quiet week look responsive purely by clearing old debt.

    ``unreplied`` is reported alongside rather than folded in, because the
    timing figures can only be computed over threads that DID get a reply. A
    median that silently omits everything still unanswered is the standard way
    response-time dashboards flatter their owner.
    """
    rows = (await db.execute(text(
        f"""WITH first_in AS (
                SELECT m.account_id, m.thread_id,
                       MIN(m.received_at) AS at
                  FROM email_messages m
                 WHERE {scope} AND {_INBOUND} AND {_NOT_SELF}
                   AND m.thread_id IS NOT NULL
                 GROUP BY m.account_id, m.thread_id),
            first_out AS (
                SELECT m.account_id, m.thread_id,
                       MIN(m.received_at) AS at
                  FROM email_messages m
                 WHERE {scope} AND LOWER(m.folder) = 'sent'
                   AND m.thread_id IS NOT NULL
                 GROUP BY m.account_id, m.thread_id),
            paired AS (
                SELECT i.at AS in_at, o.at AS out_at,
                       i.at >= now() - make_interval(days => :days)
                         AS in_window,
                       i.at >= now() - make_interval(days => :days * 2)
                         AND i.at < now() - make_interval(days => :days)
                         AS in_prev
                  FROM first_in i
                  LEFT JOIN first_out o
                    ON o.account_id = i.account_id
                   AND o.thread_id = i.thread_id
                   AND o.at > i.at)
            SELECT
              COUNT(*) FILTER (WHERE in_window) AS inbound,
              COUNT(*) FILTER (WHERE in_window AND out_at IS NOT NULL)
                AS replied,
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (out_at - in_at)) / 3600.0)
                FILTER (WHERE in_window AND out_at IS NOT NULL) AS median_h,
              percentile_cont(0.9) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (out_at - in_at)) / 3600.0)
                FILTER (WHERE in_window AND out_at IS NOT NULL) AS p90_h,
              COUNT(*) FILTER (WHERE in_prev) AS inbound_prev,
              COUNT(*) FILTER (WHERE in_prev AND out_at IS NOT NULL)
                AS replied_prev,
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(epoch FROM (out_at - in_at)) / 3600.0)
                FILTER (WHERE in_prev AND out_at IS NOT NULL) AS median_h_prev
            FROM paired"""
    ), params)).fetchone()

    inbound = rows.inbound or 0
    prev_inbound = rows.inbound_prev or 0
    return {
        "inbound_threads": inbound,
        "replied_threads": rows.replied or 0,
        "unreplied_threads": inbound - (rows.replied or 0),
        "reply_rate": round((rows.replied or 0) / inbound, 4) if inbound else 0.0,
        "reply_rate_prev": (
            round((rows.replied_prev or 0) / prev_inbound, 4)
            if prev_inbound else 0.0),
        "median_hours": _round(rows.median_h),
        "p90_hours": _round(rows.p90_h),
        "median_hours_prev": _round(rows.median_h_prev),
    }


# How a standing thread's age is reported. Buckets rather than one average,
# because "median 12 days" and "half are fine, six have been rotting since
# March" are the same average and completely different problems.
_AGE_BUCKETS = (("today", 1), ("1-3d", 3), ("3-7d", 7), ("1-4w", 30))
_AGE_OVERFLOW = "30d+"


def _bucket_case(column: str) -> str:
    """SQL mapping an age-in-days expression onto the bucket labels above."""
    whens = " ".join(
        f"WHEN {column} < {days} THEN '{label}'" for label, days in _AGE_BUCKETS)
    return f"CASE {whens} ELSE '{_AGE_OVERFLOW}' END"


async def _backlog(db: Any, params: dict[str, Any],
                   account_id: str | None) -> dict:
    """Threads standing open right now, and how long they have been standing.

    Deliberately NOT windowed. A backlog is a level, not a flow — the whole
    point is the thread from ten months ago that never got answered, and a
    30-day filter would hide precisely the items worth surfacing. The UI must
    label these "right now" and never inherit the range selector's caption.

    ``coverage`` reports how much of the mailbox Reply Zero has actually
    classified. Without it the two counts are unreadable: "3 need a reply" means
    one thing at full coverage and nothing at all if only a twelfth of threads
    were ever looked at — which is precisely what happened once here, when the
    classifier had reached 295 of 3,487 threads and the backlog looked healthy
    because most of it was invisible.
    """
    scope = "ts.account_id IN (" + mailbox_ids_sql(uid="uid", level="read")
    if account_id:
        scope += " AND id = :aid"
    scope += ")"
    age = "EXTRACT(epoch FROM (now() - ts.last_message_at)) / 86400.0"
    rows = (await db.execute(text(
        f"""SELECT ts.status,
                   {_bucket_case(age)} AS bucket,
                   COUNT(*) AS threads,
                   MAX({age}) AS oldest_days
              FROM email_thread_status ts
             WHERE {scope} AND ts.status IN ('NEEDS_REPLY', 'AWAITING')
               AND ts.last_message_at IS NOT NULL
             GROUP BY 1, 2"""
    ), params)).fetchall()

    labels = [b[0] for b in _AGE_BUCKETS] + [_AGE_OVERFLOW]
    out: dict[str, Any] = {
        k: {"threads": 0, "oldest_days": None,
            "buckets": [{"label": lb, "count": 0} for lb in labels]}
        for k in ("needs_reply", "awaiting")
    }
    key = {"NEEDS_REPLY": "needs_reply", "AWAITING": "awaiting"}
    for r in rows:
        side = out[key[r.status]]
        side["threads"] += r.threads
        side["oldest_days"] = max(
            side["oldest_days"] or 0, _round(r.oldest_days, 0) or 0)
        for b in side["buckets"]:
            if b["label"] == r.bucket:
                b["count"] = r.threads

    out["coverage"] = await _replyzero_coverage(db, params, account_id)
    return out


async def _replyzero_coverage(db: Any, params: dict[str, Any],
                              account_id: str | None) -> dict:
    """What share of live inbound threads Reply Zero has ever classified."""
    m_scope = "m.account_id IN (" + mailbox_ids_sql(uid="uid", level="read")
    if account_id:
        m_scope += " AND id = :aid"
    m_scope += ")"
    row = (await db.execute(text(
        f"""WITH threads AS (
                SELECT DISTINCT m.account_id, m.thread_id
                  FROM email_messages m
                 WHERE {m_scope} AND {_INBOUND} AND m.thread_id IS NOT NULL)
            SELECT COUNT(*) AS total,
                   COUNT(ts.thread_id) AS classified
              FROM threads t
              LEFT JOIN email_thread_status ts
                ON ts.account_id = t.account_id AND ts.thread_id = t.thread_id"""
    ), params)).fetchone()
    total = row.total or 0
    return {
        "total": total,
        "classified": row.classified or 0,
        "rate": round((row.classified or 0) / total, 4) if total else 0.0,
    }


async def _noisy_senders(db: Any, scope: str, params: dict[str, Any],
                         win: str) -> list[dict]:
    """Senders you pay attention costs for and have never once answered.

    This is the panel the old "Top senders" should have been. Ranking by raw
    volume on a mailbox whose loudest addresses are colleagues just lists the
    team; it tells the user nothing they did not already know. Ranking by mail
    you neither read nor replied to surfaces the bank alerts, the ticket-system
    robots and the newsletters — which is exactly the Email Cleaner's input.

    "Never replied" is measured across the whole mailbox, not the window: one
    reply two years ago still means this is a real correspondent, and proposing
    to silence them would be wrong.

    Each row also carries ``projected_yearly`` — this sender's rate annualised.
    Silencing a sender is a decision about the future, and "37 in the last
    month" understates it in a way "about 450 a year" does not.
    """
    rows = (await db.execute(text(
        f"""WITH replied AS (
                SELECT DISTINCT m.account_id, m.thread_id
                  FROM email_messages m
                 WHERE {scope} AND LOWER(m.folder) = 'sent'
                   AND m.thread_id IS NOT NULL)
            SELECT LOWER(m.from_address->>'email') AS email,
                   MAX(m.from_address->>'name') AS name,
                   COUNT(*) AS messages,
                   COUNT(*) FILTER (WHERE m.is_read = false) AS unread,
                   MAX(m.unsubscribe_link) AS unsubscribe_link,
                   MAX(m.received_at) AS last_seen
              FROM email_messages m
              LEFT JOIN replied r
                ON r.account_id = m.account_id AND r.thread_id = m.thread_id
             WHERE {scope} AND {win} AND {_INBOUND} AND {_NOT_SELF}
               AND COALESCE(m.from_address->>'email', '') <> ''
             GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE r.thread_id IS NOT NULL) = 0
               AND COUNT(*) >= 3
             ORDER BY COUNT(*) FILTER (WHERE m.is_read = false) DESC,
                      COUNT(*) DESC
             LIMIT 12"""
    ), params)).fetchall()
    days = max(1, int(params["days"]))
    return [
        {"email": r.email, "name": r.name or "", "messages": r.messages,
         "unread": r.unread or 0,
         "read": r.messages - (r.unread or 0),
         "projected_yearly": round(r.messages * 365 / days),
         "has_unsubscribe": bool(r.unsubscribe_link),
         "last_seen": r.last_seen.isoformat() if r.last_seen else None}
        for r in rows
    ]


async def _categories(db: Any, scope: str, params: dict[str, Any],
                      win: str, prev_win: str) -> list[dict]:
    """What KIND of mail arrived, and whether that mix is shifting.

    Doubles as a read-out on classification coverage — but ONLY if
    "uncategorized" means it. An empty ``categories`` array does not:
    conversation mail is classified per-THREAD into ``email_thread_status``, and
    the per-message array stays empty by design.

    Measured after this panel first shipped: it reported 142 uncategorized
    arrivals in 30 days and advised running a backfill. 125 of them were inbox
    mail the rules HAD processed, every one carrying a thread status — Done,
    Awaiting, FYI, Needs-reply. Nothing was behind; the panel was counting
    correctly-classified conversations as unclassified work and telling the user
    to spend model calls re-doing it.

    So a message with no categories falls back to its thread's status, and
    "(uncategorized)" now means what it says: no cleanup category AND no
    conversation status — mail nothing has ever looked at.
    """
    # The thread table stores the status ENUM (NEEDS_REPLY / AWAITING), while a
    # message label carries the display name ("Reply" / "Awaiting Reply"). Left
    # unmapped the chart grew two rows for the same thing — "Done 41" directly
    # above "DONE 69". Mirrors _THREAD_STATUS_MAP in replyzero.py; if a status
    # is added there and not here it simply shows under its raw name rather
    # than silently merging into the wrong bucket.
    status_label = ("CASE ts.status WHEN 'NEEDS_REPLY' THEN 'Needs Reply' "
                    "WHEN 'AWAITING' THEN 'Awaiting Reply' "
                    "WHEN 'DONE' THEN 'Done' ELSE ts.status END")
    rows = (await db.execute(text(
        f"""SELECT COALESCE(cat, {status_label}, '(uncategorized)') AS category,
                   COUNT(*) FILTER (WHERE {win}) AS count,
                   COUNT(*) FILTER (WHERE {prev_win}) AS prev_count
              FROM email_messages m
              LEFT JOIN LATERAL unnest(
                CASE WHEN COALESCE(array_length(m.categories, 1), 0) = 0
                     THEN ARRAY[NULL]::text[] ELSE m.categories END) AS cat
                ON true
              -- Consulted ONLY when the message itself carries no category, so
              -- a labelled message is never also counted under its thread.
              LEFT JOIN email_thread_status ts
                ON cat IS NULL AND ts.account_id = m.account_id
               AND ts.thread_id = m.thread_id
             WHERE {scope} AND ({win} OR {prev_win}) AND {_INBOUND}
             GROUP BY 1 HAVING COUNT(*) FILTER (WHERE {win}) > 0
             ORDER BY 2 DESC LIMIT 14"""
    ), params)).fetchall()
    return [
        {"category": r.category, "count": r.count,
         "prev_count": r.prev_count or 0}
        for r in rows
    ]


async def _automation(db: Any, params: dict[str, Any],
                      account_id: str | None) -> tuple[dict, list[dict], int]:
    """What the assistant did — and whether it deserves to be trusted.

    Only APPLIED rows count toward the work done. PENDING is what a dry-run
    preview writes, and crediting a preview with work it explicitly did not do
    would overstate the assistant on the one screen meant to measure it.

    ``trust`` is the half nobody ships. Counting how many emails were filed is
    reassurance; what the user actually needs to decide is whether to keep
    letting it file them, and that lives in three numbers: how often they
    overruled it, how often the provider call failed, and how many learned
    patterns are queued up unreviewed. That last one is not decoration — an
    unreviewed pattern is inert by design (the Email Cleaner will not project
    it), so a growing queue means the assistant is quietly getting less useful,
    and a batch of bad patterns sitting in it is exactly what went unnoticed
    until 24 of 45 had to be purged by hand.
    """
    scope = "er.account_id IN (" + mailbox_ids_sql(uid="uid", level="read")
    if account_id:
        scope += " AND id = :aid"
    scope += ")"
    applied = (f"{scope} AND er.status = 'APPLIED' "
               "AND er.created_at >= now() - make_interval(days => :days)")
    try:
        rule_rows = (await db.execute(text(
            f"""SELECT COALESCE(er.rule_name, '(no match)') AS rule_name,
                       COUNT(DISTINCT er.message_id) AS count
                  FROM email_executed_rules er
                 WHERE {applied} AND er.message_id IS NOT NULL
                 GROUP BY er.rule_name ORDER BY count DESC LIMIT 10"""
        ), params)).fetchall()
        action_rows = (await db.execute(text(
            f"""SELECT act AS action, COUNT(*) AS count
                  FROM email_executed_rules er,
                       LATERAL jsonb_array_elements_text(er.actions_taken)
                         AS act
                 WHERE {applied} GROUP BY act ORDER BY count DESC"""
        ), params)).fetchall()
        # One message touched by three rules is ONE email the user did not have
        # to handle, so the headline counts messages while the breakdown counts
        # per-rule hits. Summing the breakdown would double-count it.
        handled = (await db.execute(text(
            f"""SELECT COUNT(DISTINCT er.message_id) AS n
                  FROM email_executed_rules er
                 WHERE {applied} AND er.message_id IS NOT NULL"""
        ), params)).scalar() or 0
        trust = await _automation_trust(db, params, scope)
    except Exception:
        return ({"processed": 0, "by_rule": [], "trust": _EMPTY_TRUST.copy()},
                [], 0)

    return (
        {"processed": handled, "trust": trust,
         "by_rule": [{"rule_name": r.rule_name, "count": r.count}
                     for r in rule_rows]},
        [{"action": r.action, "count": r.count} for r in action_rows],
        handled,
    )


_EMPTY_TRUST: dict[str, Any] = {
    "decided": 0, "rejected": 0, "rejection_rate": 0.0,
    "repairable": 0, "permanent_failures": 0, "unreviewed_patterns": 0,
}


async def _automation_trust(db: Any, params: dict[str, Any],
                            scope: str) -> dict[str, Any]:
    """Numbers that say whether the assistant is worth leaving switched on.

    ``rejection_rate`` is over APPLIED + REJECTED only. PENDING rows are
    previews awaiting a verdict, and counting an undecided row as an acceptance
    would make the assistant look better the longer the user ignored it.
    """
    row = (await db.execute(text(
        f"""SELECT
              COUNT(*) FILTER (WHERE er.status IN ('APPLIED', 'REJECTED'))
                AS decided,
              COUNT(*) FILTER (WHERE er.status = 'REJECTED') AS rejected
            FROM email_executed_rules er
           WHERE {scope}
             AND er.created_at >= now() - make_interval(days => :days)"""
    ), params)).fetchone()

    # Failed actions are a REPAIR QUEUE — a level, not a windowed flow — so they
    # are counted unwindowed, like the pattern queue below. Split into what the
    # Repair button can actually act on and what it can't, because the two used
    # to be conflated: the old single "failed_actions" was windowed and counted
    # ANY errored row regardless of status, so a nonzero count routinely produced
    # "nothing to repair" (all of it APPLIED-partial or trashed) while genuinely
    # repairable rows older than the window were invisible. `repairable` mirrors
    # retry_failed_executions EXACTLY (status=FAILED, a rule to replay, and the
    # message neither deleted nor in trash/junk/spam/drafts); `permanent_failures`
    # is everything else that failed — the message is gone and no replay can help.
    fail = (await db.execute(text(
        f"""SELECT
              COUNT(*) FILTER (WHERE er.status = 'FAILED'
                AND er.rule_id IS NOT NULL AND em.id IS NOT NULL
                AND LOWER(COALESCE(em.folder, '')) NOT IN
                    ('trash', 'junk', 'spam', 'drafts', 'draft')) AS repairable,
              COUNT(*) FILTER (WHERE er.status = 'FAILED' AND NOT (
                er.rule_id IS NOT NULL AND em.id IS NOT NULL
                AND LOWER(COALESCE(em.folder, '')) NOT IN
                    ('trash', 'junk', 'spam', 'drafts', 'draft'))) AS permanent
            FROM email_executed_rules er
            LEFT JOIN email_messages em ON em.id = er.message_id
           WHERE {scope}"""
    ), params)).fetchone()

    # Not windowed: a queue is a level. A pattern learned four months ago and
    # never looked at is the whole point of showing this.
    #
    # Excludes only ever PREVENT a label, so they are never gated on review and
    # never queue up — counting them here would invent a backlog that has no
    # corresponding button in the review screen.
    pending = (await db.execute(text(
        f"""SELECT COUNT(*) FROM email_rule_patterns p
             WHERE {scope.replace('er.account_id', 'p.account_id')}
               AND p.approved_at IS NULL AND p.rejected_at IS NULL
               AND NOT p.exclude"""
    ), params)).scalar() or 0

    decided = row.decided or 0
    return {
        "decided": decided,
        "rejected": row.rejected or 0,
        "rejection_rate": (
            round((row.rejected or 0) / decided, 4) if decided else 0.0),
        "repairable": (fail.repairable or 0) if fail else 0,
        "permanent_failures": (fail.permanent or 0) if fail else 0,
        "unreviewed_patterns": pending,
    }
