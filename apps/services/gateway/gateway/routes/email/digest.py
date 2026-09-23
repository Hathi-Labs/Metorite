"""Inbox digest endpoints + scheduler hook (split out of the big email router).

Routes attach to the shared ``router`` defined in ``gateway.routes.email``; this
module is imported at the bottom of that module so its routes register. The
scheduler imports ``_maybe_send_digest`` via ``gateway.routes.email`` (re-exported
there), so it stays importable from the original location.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.email.automation.senders import canonical_cleanup_category
from gateway.routes.email.core import (
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


# The digest is a PROJECTION of the same windowed inbox aggregates the Analytics
# screen reports — never a parallel re-derivation with its own (drifting) rules.
# Each section below is one small aggregate helper so _generate_digest just
# composes them, and each can be pinned by a test. The window is inbound inbox
# mail only, and the account's OWN address is excluded everywhere: self-notes,
# BCC-to-self, automation — and the digest email itself, which lands in the inbox
# from the account and would otherwise inflate the NEXT digest's counts.
#
# ``_INBOUND`` is the shared predicate: inbox folder, not from self. ``em`` is the
# table alias; ``:aid``/``:days``/``:self`` come from the shared params.
_DIGEST_WIN = ("em.account_id = :aid AND em.received_at >= "
               "now() - make_interval(days => :days)")
_DIGEST_INBOUND = ("LOWER(em.folder) = 'inbox' "
                   "AND (:self = '' OR LOWER(em.from_address->>'email') <> :self)")


async def _digest_totals(db: Any, params: dict[str, Any]) -> dict[str, int]:
    """New inbound inbox mail in the window: count, unread, with-attachments."""
    row = (await db.execute(text(
        f"""SELECT COUNT(*) AS inbox,
                   COUNT(*) FILTER (WHERE is_read = false) AS unread,
                   COUNT(*) FILTER (WHERE has_attachments) AS attachments
            FROM email_messages em
            WHERE {_DIGEST_WIN} AND {_DIGEST_INBOUND}"""
    ), params)).fetchone()
    return {
        "inbox": (row.inbox if row else 0) or 0,
        "unread": (row.unread if row else 0) or 0,
        "attachments": (row.attachments if row else 0) or 0,
    }


# The digest's scope, in the ANALYTICS helpers' ``m`` alias: this account,
# inbox only, never the account's own address (self-notes, BCC-to-self, and the
# digest email itself, which would inflate the NEXT digest's counts).
_PROJ_SCOPE = ("m.account_id = :aid AND LOWER(COALESCE(m.folder, '')) = 'inbox' "
               "AND (:self = '' OR LOWER(m.from_address->>'email') <> :self)")
_PROJ_WIN = "m.received_at >= now() - make_interval(days => :days)"
_PROJ_PREV_WIN = ("m.received_at >= now() - make_interval(days => :days * 2) "
                  "AND m.received_at < now() - make_interval(days => :days)")


async def _digest_categories(
    db: Any, params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Category breakdown = the ANALYTICS category aggregate, projected onto the
    digest's window and scope.

    This used to run its own SQL over the ``email_senders`` rollup, which
    disagreed with the Analytics chart (message categories with the per-thread
    status fallback — see ``analytics._categories`` for why an empty categories
    array does NOT mean unclassified). One computation, two projections: the
    digest passes its scope (this account, inbox-only, self-excluded) and its
    window; the aggregate itself lives in analytics.
    """
    from gateway.routes.email.automation.analytics import (  # noqa: PLC0415
        _categories,
    )
    return await _categories(db, _PROJ_SCOPE, params, _PROJ_WIN, _PROJ_PREV_WIN)


async def _digest_top_senders(
    db: Any, params: dict[str, Any],
) -> list[dict[str, Any]]:
    """The senders worth telling the user about = the ANALYTICS noisy-senders
    aggregate (mail you neither read nor ever replied to), projected onto the
    digest's window and scope.

    The old version ranked by raw volume, which on a founder's mailbox just
    lists colleagues — the exact panel Analytics already replaced with "senders
    you pay attention costs for and have never once answered". Same projection
    discipline as the category breakdown: digest scope, analytics computation.
    ``count`` mirrors ``messages`` for the renderers.
    """
    from gateway.routes.email.automation.analytics import (  # noqa: PLC0415
        _noisy_senders,
    )
    rows = await _noisy_senders(db, _PROJ_SCOPE, params, _PROJ_WIN)
    return [{**r, "name": r["name"] or r["email"], "count": r["messages"]}
            for r in rows[:8]]


# A thread whose LAST message sits in trash/junk is not an open loop — the user
# disposed of it. Counting it as "awaiting your reply" was a live lie (a trashed
# sick-leave thread sat in the backlog). NULL folder (unmirrored) stays counted.
# A currently-SNOOZED conversation is excluded the same way: the inbox hides it
# until it wakes, and the dashboard's own Snooze action dropped the row only for
# the quiet re-sync to bring it straight back (and the needs-reply count never
# moved). It reappears — here and in the inbox — the moment snoozed_until
# passes, with no scheduler involved.
_LIVE_THREAD = ("NOT EXISTS (SELECT 1 FROM email_messages tem "
                "WHERE tem.id = ts.last_message_id "
                "AND (LOWER(COALESCE(tem.folder, '')) IN ('trash', 'junk') "
                "OR tem.snoozed_until > now()))")


async def _digest_status_count(db: Any, account_id: str, status: str) -> int:
    """Count live threads in one Reply Zero status — the SAME classification
    (email_thread_status) analytics._backlog reads. Never the old heuristic
    that counted every inbox-tailed thread all-time."""
    return (await db.execute(text(
        f"""SELECT COUNT(*) FROM email_thread_status ts
            WHERE ts.account_id = :aid AND ts.status = '{status}'
              AND {_LIVE_THREAD}"""
    ), {"aid": account_id})).scalar() or 0


async def _digest_needs_reply(db: Any, account_id: str) -> int:
    """Threads the user actually owes a reply on."""
    return await _digest_status_count(db, account_id, "NEEDS_REPLY")


# Priority ordering for the needs-reply queue. Oldest-first is right for a
# brief ("what's going stale"), but on the live dashboard it buries a
# high-importance thread from this morning under a two-month-old dead loop —
# the exact "surfaces dead loops" complaint. This deterministic score (no
# per-load LLM cost) lifts important + unanswered mail: high importance and
# unread each weigh more than age, but age still climbs (capped) so genuinely
# old threads keep surfacing. Returned `importance`/`unread` let the UI show WHY
# a row ranks where it does.
_PRIORITY_SCORE = (
    "(CASE WHEN LOWER(COALESCE(em.importance, '')) = 'high' THEN 100 ELSE 0 END"
    " + CASE WHEN em.is_read = false THEN 25 ELSE 0 END"
    " + LEAST(GREATEST(0, EXTRACT(DAY FROM now() - ts.last_message_at)), 30))"
)


async def _digest_thread_list(
    db: Any, account_id: str, status: str, limit: int,
    *, prioritized: bool = False,
) -> list[dict[str, Any]]:
    """Live threads in one status, with enough identity for the dashboard to
    open the thread: thread_id, the last message id, and the counterparty
    (sender of the last message, or its first recipient when the last message is
    the user's own — the person being waited on).

    ``prioritized`` (the dashboard's needs-reply queue) orders by an urgency
    score — importance + unread + capped age — instead of pure age, so the row
    you should act on first is on top. Everything else stays OLDEST first
    (aging is the point of a brief / of "who's kept me waiting longest")."""
    order = (f"{_PRIORITY_SCORE} DESC, ts.last_message_at ASC"
             if prioritized else "ts.last_message_at ASC")
    rows = (await db.execute(text(
        f"""SELECT ts.thread_id, ts.last_message_id, em.subject,
                   em.from_address->>'name'  AS from_name,
                   em.from_address->>'email' AS from_email,
                   em.to_addresses->0->>'name'  AS to_name,
                   em.to_addresses->0->>'email' AS to_email,
                   LOWER(COALESCE(em.importance, '')) = 'high' AS high,
                   em.is_read AS is_read,
                   GREATEST(0, EXTRACT(DAY FROM now() - ts.last_message_at))::int
                     AS age_days
            FROM email_thread_status ts
            LEFT JOIN email_messages em ON ts.last_message_id = em.id
            WHERE ts.account_id = :aid AND ts.status = '{status}'
              AND ts.last_message_at IS NOT NULL
              AND {_LIVE_THREAD}
            ORDER BY {order}
            LIMIT :lim"""
    ), {"aid": account_id, "lim": limit})).fetchall()
    self_row = (await db.execute(text(
        "SELECT LOWER(email_address) AS self FROM email_accounts "
        "WHERE id = :aid"), {"aid": account_id})).fetchone()
    self_email = (getattr(self_row, "self", "") or "") if self_row else ""

    out: list[dict[str, Any]] = []
    for r in rows:
        ours = (r.from_email or "").lower() == self_email
        who = ((r.to_name or r.to_email) if ours
               else (r.from_name or r.from_email)) or ""
        out.append({
            "subject": (r.subject or "(no subject)"),
            "age_days": r.age_days,
            "thread_id": r.thread_id,
            "message_id": (str(r.last_message_id)
                           if r.last_message_id else None),
            "who": who,
            "important": bool(r.high),
            "unread": (r.is_read is False),
        })
    return out


async def _digest_backlog_aging(
    db: Any, account_id: str, limit: int = 5, *, prioritized: bool = False,
) -> list[dict[str, Any]]:
    """The threads still awaiting the user's reply — the Reply Zero backlog.
    A brief wants them aged (``prioritized=False`` → oldest first, "what's going
    stale"); the live dashboard wants them ranked by urgency
    (``prioritized=True``). Reads the SAME email_thread_status the classifier
    owns."""
    return await _digest_thread_list(
        db, account_id, "NEEDS_REPLY", limit, prioritized=prioritized)


async def _digest_awaiting(
    db: Any, account_id: str, limit: int = 5,
) -> list[dict[str, Any]]:
    """Who the user is WAITING ON — AWAITING threads (the user replied, the
    other side owes the next move), longest-waiting first. The other half of
    the ledger; the digest used to show only the user's debts, never their
    receivables."""
    return await _digest_thread_list(db, account_id, "AWAITING", limit)


async def _digest_commitments(
    db: Any, account_id: str, horizon_days: int = 3, limit: int = 8,
    *, include_undated: bool = False,
) -> list[dict[str, Any]]:
    """Open commitments (tasks captured from a sent reply on this account)
    that are due within the horizon or already overdue — the "loops I promised
    to close" half of the brief. Linked via the task's ``origin.account_id``
    (see tasks/email_link.py). ``include_undated`` (dashboard) adds the open
    promises with NO due date — 3 of the 4 live commitments had none and were
    invisible to the horizon filter. Best-effort: the tasks feature may be
    absent, and a digest must never fail because of it.

    The tasks come from the ONE task seam, ``item_source()`` (WS-39 S8c), for
    the account's owner. Which tasks are the owner's, and whether one is still
    open, is the seam's rule and is not restated here.

    Each row carries ``message_id`` — the latest message of the linked thread —
    so the dashboard can open the email the commitment came from (context on
    what was promised, not just the captured title)."""
    from gateway.routes.tasks.item_source import item_source

    try:
        owner = (await db.execute(text(
            "SELECT user_id FROM email_accounts WHERE id = :aid"
        ), {"aid": account_id})).fetchone()
        uid = str(getattr(owner, "user_id", "") or "") if owner else ""
        if not uid:
            return []
        items = await item_source().items_by_origin(
            db, uid, "account_id", str(account_id))
        at = datetime.now(UTC)
        until = at + timedelta(days=horizon_days)
        picked: list[tuple[Any, datetime | None]] = []
        for it in items:
            due = _as_utc(getattr(it, "due_at", None))
            if (due is not None and due <= until) or (
                    include_undated and due is None):
                picked.append((it, due))
        # Soonest first, undated last: the order the SQL gave with NULLS LAST.
        picked.sort(key=lambda p: (p[1] is None, p[1] or at))
        picked = picked[:limit]
        threads = sorted({
            str(_origin_of(it).get("thread_id"))
            for it, _due in picked if _origin_of(it).get("thread_id")
        })
        latest: dict[str, str] = {}
        if threads:
            rows = (await db.execute(text(
                """SELECT DISTINCT ON (em.thread_id)
                          em.thread_id, em.id AS message_id
                   FROM email_messages em
                   WHERE em.account_id = :aid
                     AND em.thread_id = ANY(:tids)
                   ORDER BY em.thread_id, em.received_at DESC NULLS LAST"""
            ), {"aid": account_id, "tids": threads})).fetchall()
            latest = {str(r.thread_id): str(r.message_id) for r in rows}
    except Exception as exc:  # noqa: BLE001
        # Best-effort must leave the SESSION usable, not just return []: a
        # failed query aborts the transaction, and without a rollback every
        # later query in the same request dies with InFailedSQLTransaction —
        # exactly how /digest/send 500'd while the digest preview looked fine.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("email.digest_commitments_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for it, due in picked:
        thread_id = _origin_of(it).get("thread_id") or None
        out.append({
            "title": (it.title or "(untitled)"),
            # 'Mon DD', as Postgres `to_char(due_at, 'Mon DD')` wrote it.
            "due": due.strftime("%b %d") if due else None,
            "overdue": bool(due is not None and due < at),
            "task_id": str(it.id),
            "thread_id": thread_id,
            "message_id": latest.get(str(thread_id)) if thread_id else None,
        })
    return out


def _origin_of(item: Any) -> dict[str, Any]:
    """A task's origin as a dict. The pm arm decodes it, and a raw JSONB text
    value is decoded here, so either arm's row reads the same."""
    origin = getattr(item, "origin", None)
    if isinstance(origin, str):
        try:
            origin = json.loads(origin)
        except ValueError:
            return {}
    return origin if isinstance(origin, dict) else {}


def _as_utc(value: Any) -> datetime | None:
    """A timestamp as an aware UTC datetime, or None. A naive value is UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _digest_is_empty(digest: dict) -> bool:
    """A digest with no new mail, nothing awaiting a reply, no aging backlog AND
    no commitments due is noise — the scheduler suppresses it rather than mailing
    an empty summary every morning."""
    t = digest["totals"]
    return (t["inbox"] == 0 and t["needs_reply"] == 0
            and not digest["by_category"] and not digest["top_senders"]
            and not digest.get("backlog") and not digest.get("commitments"))


def _age_phrase(days: int) -> str:
    """"today" / "yesterday" / "N days" — a brief reads better than a bare int."""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days"


def _due_phrase(c: dict) -> str:
    """"overdue Jul 20" / "due Jul 25" / "no due date" — undated commitments
    (most of them, live) must still render instead of being dropped."""
    if not c.get("due"):
        return "no due date"
    return f"{'overdue' if c.get('overdue') else 'due'} {c['due']}"


def _render_digest_markdown(
    period: str, totals: dict, needs: int,
    by_category: list[dict], top_senders: list[dict],
    backlog: list[dict] | None = None, commitments: list[dict] | None = None,
    awaiting: list[dict] | None = None, brief: str = "",
) -> str:
    lines = [
        f"# Inbox digest — last {period}",
        "",
    ]
    if brief:
        lines += [f"_{brief}_", ""]
    lines += [
        f"**{totals['inbox']}** new in inbox · **{totals['unread']}** unread · "
        f"**{needs}** threads awaiting your reply · "
        f"**{totals['attachments']}** with attachments",
    ]
    if commitments:
        lines += ["", "## Commitments due"]
        lines += [f"- {c['title']} — {_due_phrase(c)}" for c in commitments]
    if backlog:
        lines += ["", "## Awaiting your reply (oldest first)"]
        lines += [f"- {b['subject']} — waiting {_age_phrase(b['age_days'])}"
                  for b in backlog]
    if awaiting:
        lines += ["", "## Waiting on them (longest first)"]
        lines += [
            f"- {b['subject']}"
            + (f" — {b['who']}" if b.get("who") else "")
            + f" — waiting {_age_phrase(b['age_days'])}"
            for b in awaiting]
    lines += ["", "## By category"]
    lines += [f"- **{c['category']}**: {c['count']}"
              for c in by_category] or ["- (none)"]
    lines += ["", "## Noisy senders you never answer"]
    lines += [f"- {s['name']} — {s['count']}"
              for s in top_senders] or ["- (none)"]
    return "\n".join(lines)


def _esc(s: Any) -> str:
    """Minimal HTML escape for the small, trusted digest strings."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _render_digest_html(
    period: str, totals: dict, needs: int,
    by_category: list[dict], top_senders: list[dict],
    backlog: list[dict] | None = None, commitments: list[dict] | None = None,
    awaiting: list[dict] | None = None, brief: str = "",
) -> str:
    """A simple, self-contained HTML body so the emailed digest renders as more
    than a wall of Markdown asterisks in a mail client."""
    def _ul(items: list[str]) -> str:
        if not items:
            return "<p style='color:#888'>(none)</p>"
        lis = "".join(f"<li>{it}</li>" for it in items)
        return f"<ul style='margin:4px 0 12px;padding-left:20px'>{lis}</ul>"

    def _h3(title: str) -> str:
        return ("<h3 style='margin:0 0 4px;font-size:13px;text-transform:"
                f"uppercase;color:#666'>{title}</h3>")

    cats = _ul([f"<b>{_esc(c['category'])}</b>: {c['count']}"
                for c in by_category])
    senders = _ul([f"{_esc(s['name'])} — {s['count']}" for s in top_senders])
    # The two action sections lead, above the summary counts — a brief opens
    # with what needs doing, not with a tally.
    action = ""
    if commitments:
        items = [
            (f"{_esc(c['title'])} — "
             f"<span style='color:"
             f"{'#c0392b' if c.get('overdue') and c.get('due') else '#666'}'>"
             f"{_esc(_due_phrase(c))}</span>")
            for c in commitments]
        action += _h3("Commitments due") + _ul(items)
    if backlog:
        items = [f"{_esc(b['subject'])} — <span style='color:#666'>waiting "
                 f"{_esc(_age_phrase(b['age_days']))}</span>" for b in backlog]
        action += _h3("Awaiting your reply") + _ul(items)
    if awaiting:
        items = [
            f"{_esc(b['subject'])}"
            + (f" — {_esc(b['who'])}" if b.get("who") else "")
            + (f" — <span style='color:#666'>waiting "
               f"{_esc(_age_phrase(b['age_days']))}</span>")
            for b in awaiting]
        action += _h3("Waiting on them") + _ul(items)
    return (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:560px;color:#1a1a1a'>"
        f"<h2 style='margin:0 0 4px'>Inbox digest — last {_esc(period)}</h2>"
        + (f"<p style='font-size:14px;font-style:italic;color:#444;"
           f"margin:6px 0 12px'>{_esc(brief)}</p>" if brief else "")
        + "<p style='font-size:15px;margin:8px 0 16px'>"
        f"<b>{totals['inbox']}</b> new in inbox &middot; "
        f"<b>{totals['unread']}</b> unread &middot; "
        f"<b>{needs}</b> awaiting your reply &middot; "
        f"<b>{totals['attachments']}</b> with attachments</p>"
        f"{action}"
        f"{_h3('By category')}{cats}"
        f"{_h3('Noisy senders you never answer')}{senders}"
        "</div>"
    )


async def _digest_brief(
    db: Any, account_id: str, backlog: list[dict], commitments: list[dict],
) -> str:
    """The opt-in "morning brief": ONE LLM sentence naming what actually needs
    attention today, from the top needs-reply threads + due commitments (the
    data already on the dashboard — no new query). Best-effort: any failure
    (flag off, nothing pressing, model error) returns "" and the dashboard just
    omits the line. Reads the per-account opt-in flag; costs a model call, so it
    never runs unless the user turned it on."""
    try:
        row = (await db.execute(text(
            "SELECT morning_brief_enabled FROM email_assistant_settings "
            "WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
    except Exception as exc:  # noqa: BLE001 — column absent pre-migration, etc.
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("email.digest_brief_flag_failed", error=str(exc)[:200])
        return ""
    if not row or not getattr(row, "morning_brief_enabled", False):
        return ""
    if not backlog and not commitments:
        return ""
    reply_lines = "\n".join(
        f"- {b.get('subject', '')}"
        + (f" (from {b['who']})" if b.get("who") else "")
        + (f", waiting {b['age_days']}d" if b.get("age_days") else "")
        for b in backlog[:6])
    due_lines = "\n".join(
        f"- {c.get('title', '')}" + (f" ({_due_phrase(c)})" if c.get("due") else "")
        for c in commitments[:6])
    user_prompt = (
        "Needs a reply:\n" + (reply_lines or "- (none)")
        + "\n\nCommitments due:\n" + (due_lines or "- (none)"))
    try:
        data, _content, _used = await _llm_json(
            "tier-fast",
            [{"role": "system", "content": (
                "You write ONE short sentence orienting someone to their inbox "
                "for the day — what's most pressing and who it's with. Name 1-3 "
                "specific items (a person or subject), newest-pressing first. No "
                "greeting, no preamble, under 25 words. "
                'Respond ONLY JSON {"brief": "<sentence>"}.')},
             {"role": "user", "content": user_prompt}],
            max_tokens=160,
        )
        if isinstance(data, dict):
            return str(data.get("brief", "")).strip()[:280]
        return ""
    except Exception as exc:  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("email.digest_brief_failed", error=str(exc)[:200])
        return ""


async def _generate_digest(
    db: Any, account_id: str, period_days: int,
    categories: list[str] | None = None, *, full: bool = False,
) -> dict:
    """Build an inbox digest for the window by COMPOSING the shared aggregates
    above (totals, category breakdown, top senders, needs-reply, awaiting,
    commitments). Deterministic (no LLM). Returns both a Markdown and an HTML
    body.

    `categories` (optional) restricts the category breakdown to the selected
    sender categories ("Cold Emails" maps to the "Cold Email" category); empty
    or None includes everything.

    ``full`` is the DASHBOARD projection: same computation, bigger caps (the
    in-app view lists every open loop with ids for navigation) plus undated
    commitments. The scheduled EMAIL keeps the small caps — a brief, not a
    ledger. One computation, two projections.
    """
    params: dict[str, Any] = {"aid": account_id, "days": period_days}
    self_row = (await db.execute(text(
        "SELECT LOWER(email_address) AS self, user_id "
        "FROM email_accounts WHERE id = :aid"
    ), {"aid": account_id})).fetchone()
    params["self"] = (getattr(self_row, "self", "") or "") if self_row else ""
    # The analytics noisy-senders aggregate excludes the user's OWN connected
    # addresses via a per-user subquery (:uid) — hand it the account owner.
    params["uid"] = (getattr(self_row, "user_id", "") or "") if self_row else ""
    # The UI offers rule names ("Cold Emails", "newsletter", " Marketing "); the
    # aggregates report canonical categories. Normalise each selection through
    # the same canonicaliser the cleaner uses, so casing/whitespace/plural
    # variants match; a name that isn't a category is dropped rather than
    # silently emptying the section.
    raw_cats = ["Cold Email" if c == "Cold Emails" else c
                for c in (categories or [])]
    cats = sorted({canonical_cleanup_category(c) for c in raw_cats} - {None})

    totals = await _digest_totals(db, params)
    by_category = await _digest_categories(db, params)
    if cats:
        # Restrict the breakdown to the configured cleanup categories, applied
        # to the shared aggregate's output (the aggregate itself is not
        # re-parameterized per config — one computation, filtered projection).
        by_category = [
            r for r in by_category
            if canonical_cleanup_category(str(r.get("category") or "")) in cats
        ]
    top_senders = await _digest_top_senders(db, params)
    needs = await _digest_needs_reply(db, account_id)
    totals["needs_reply"] = needs
    totals["awaiting"] = await _digest_status_count(db, account_id, "AWAITING")
    # The "act on this" sections that turn a summary into a daily brief: what's
    # going stale (aging Reply-Zero backlog), who owes US the next move
    # (awaiting), and what I promised to do (commitments). Independent of the
    # digest's category window.
    # Dashboard cap: high enough that the list matches the stat-tile count for
    # any realistic mailbox — at 50 the badge said "104 waiting" while the list
    # silently held 50, and there was no way to see (or close) the rest.
    list_cap = 500 if full else 5
    # Dashboard ranks the reply queue by urgency (act first); the emailed brief
    # keeps it oldest-first (what's going stale).
    backlog = await _digest_backlog_aging(
        db, account_id, list_cap, prioritized=full)
    awaiting = await _digest_awaiting(db, account_id, list_cap)
    commitments = await _digest_commitments(
        db, account_id,
        horizon_days=365 if full else 3,
        limit=50 if full else 8,
        include_undated=full)

    # Opt-in one-liner (off by default → returns "" with no model call). Built
    # from the sections above, so it costs one call and no extra query.
    brief = await _digest_brief(db, account_id, backlog, commitments)

    period = ("day" if period_days <= 1
              else ("week" if period_days <= 7 else f"{period_days} days"))
    return {
        "period_days": period_days,
        "totals": totals,
        "by_category": by_category,
        "top_senders": top_senders,
        "backlog": backlog,
        "awaiting": awaiting,
        "commitments": commitments,
        "brief": brief,
        "markdown": _render_digest_markdown(
            period, totals, needs, by_category, top_senders,
            backlog, commitments, awaiting, brief),
        "html": _render_digest_html(
            period, totals, needs, by_category, top_senders,
            backlog, commitments, awaiting, brief),
    }


async def _configured_categories(db: Any, account_id: str) -> list[str]:
    """The account's saved digest_categories, so the manual preview and the
    'send now' endpoint filter exactly like the scheduled digest — otherwise the
    user previews one thing and the scheduler emails another."""
    row = (await db.execute(text(
        "SELECT digest_categories FROM email_assistant_settings "
        "WHERE account_id = :aid"
    ), {"aid": account_id})).fetchone()
    return list(getattr(row, "digest_categories", None) or []) if row else []


@router.get("/digest")
async def get_digest(
    account_id: str = Query(...),
    period: str = Query("day"),  # day | week
    user: UserContext = Depends(get_current_user),
):
    """Generate an inbox digest for the account (day or week window)."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        days = 7 if period == "week" else 1
        cats = await _configured_categories(db, account_id)
        # The in-app view is the DASHBOARD projection: full lists + ids so
        # every row can open its thread. The scheduled email keeps small caps.
        return await _generate_digest(db, account_id, days, cats, full=True)


class DigestSendRequest(BaseModel):
    account_id: str
    period: str = "day"


@router.post("/digest/send")
async def send_digest(
    req: DigestSendRequest,
    user: UserContext = Depends(get_current_user),
):
    """Generate the digest and email it to the account's own address."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        days = 7 if req.period == "week" else 1
        cats = await _configured_categories(db, req.account_id)
        digest = await _generate_digest(db, req.account_id, days, cats)
        async with provider_session(
            db, user.email or "anonymous",
            account_id=req.account_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                raise HTTPException(status_code=502, detail="Provider auth failed")
            await sess.provider.send_message(
                to=[sess.owner_email],
                subject=f"📥 Your inbox digest — last {'week' if days > 1 else 'day'}",
                body_text=digest["markdown"],
                body_html=digest["html"],
            )
            await db.execute(text(
                "UPDATE email_assistant_settings SET last_digest_at = now() "
                "WHERE account_id = :aid"
            ), {"aid": req.account_id})
        return {"sent": True, "to": sess.owner_email}


async def _maybe_send_digest(account_id: str) -> None:
    """Background: send a digest if one is due per the account's schedule.

    Honors digest_frequency (DAILY/WEEKLY), digest_time_of_day (don't send
    before that UTC time), digest_day_of_week (WEEKLY only; 0=Sun…6=Sat),
    digest_categories (which categories to include) and digest_send_to_email.
    """
    # H4: scheduler-run digest tick — no ambient tenant; needs an explicit
    # tenant derived from the email_accounts row before conversion.
    db = await _get_db()
    try:
        row = (await db.execute(text(
            """SELECT digest_frequency, last_digest_at, digest_time_of_day,
                      digest_day_of_week, digest_categories, digest_send_to_email
               FROM email_assistant_settings WHERE account_id = :aid"""
        ), {"aid": account_id})).fetchone()
        if not row or (row.digest_frequency or "OFF") == "OFF":
            return
        if not bool(getattr(row, "digest_send_to_email", True)):
            return  # email is the only channel today; nothing to deliver
        period_days = 7 if row.digest_frequency == "WEEKLY" else 1
        now = datetime.now(timezone.utc)

        # Parse the configured send time (HH:MM, treated as UTC).
        try:
            hh, mm = (getattr(row, "digest_time_of_day", None) or "09:00").split(":")
            send_at = now.replace(
                hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (ValueError, AttributeError):
            send_at = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now < send_at:
            return  # too early in the day

        if row.digest_frequency == "WEEKLY":
            # email_assistant_settings uses JS weekdays (0=Sun); Python's
            # weekday() is 0=Mon, so shift.
            js_dow = (now.weekday() + 1) % 7
            if js_dow != int(getattr(row, "digest_day_of_week", 1) or 1):
                return
            min_gap = timedelta(days=6)
        else:
            min_gap = timedelta(hours=20)

        last = row.last_digest_at
        if last is not None and (last > now - min_gap or last >= send_at):
            return  # already sent recently / already sent today after send time

        categories = list(getattr(row, "digest_categories", None) or [])
        digest = await _generate_digest(db, account_id, period_days, categories)
        # Empty-digest suppression: a scheduled digest with no new mail and
        # nothing awaiting a reply is noise. Skip the send WITHOUT stamping
        # last_digest_at, so the moment real mail arrives (still past the send
        # time) the next cycle delivers one.
        if _digest_is_empty(digest):
            _log.info("email.digest_suppressed_empty", account_id=account_id)
            return
        # Unscoped session: this is a scheduler tick, no request user to scope
        # by. Missing account raises 404 → caught by the broad handler below.
        async with provider_session(
            db, None, account_id=account_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                return
            await sess.provider.send_message(
                to=[sess.owner_email],
                subject=f"📥 Your inbox digest — last "
                        f"{'week' if period_days > 1 else 'day'}",
                body_text=digest["markdown"],
                body_html=digest["html"],
            )
            await db.execute(text(
                "UPDATE email_assistant_settings SET last_digest_at = now() "
                "WHERE account_id = :aid"
            ), {"aid": account_id})
        await db.commit()
        _log.info("email.digest_sent", account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.digest_send_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        await db.close()
