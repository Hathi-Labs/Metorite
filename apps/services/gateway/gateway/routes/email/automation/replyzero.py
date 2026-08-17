"""Automation · Reply Zero — needs-reply classification, the reply-zero views,
follow-up reminders, and the inbox AI chat/quick-action endpoints."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, Query
from gateway.routes.email.automation.assistant import _load_assistant_about
from gateway.routes.email.automation.identity import (
    resolve_org_domains,
    sender_scope,
)
from gateway.routes.email.automation.jobs import JobTracker
from gateway.routes.email.core import (
    CLEANUP_CATEGORIES,
    _assert_account_owner,
    _attachment_summaries,
    _fmt_addr_list,
    _get_db,
    _tenant_session,
    _instantiate_provider,
    _llm_json,
    _log,
    _persist_rotated_creds,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text



def _addr_dict(raw: Any) -> dict:
    return raw if isinstance(raw, dict) else json.loads(raw or "{}")


async def _upsert_thread_status(
    db: Any, account_id: str, thread_id: str, status: str,
    msg_id: Any, msg_at: Any, reason: str, *, preserve_done: bool = False,
) -> None:
    """Write a thread's Reply Zero status.

    ``preserve_done`` is for the AUTOMATED re-projection paths (the live runner
    and the backfill, via ``project_reply_status_from_matches``): a thread the
    user MARKED DONE must not be silently re-opened by a trailing notification or
    FYI. With it set, a DONE thread is only re-opened when the new message
    genuinely NEEDS A REPLY (→ NEEDS_REPLY); any other determined status keeps it
    DONE. Explicit user actions (Mark Done / Reopen / Fix) and the user's own
    reply (``_mark_thread_replied``) leave it False, so their intent still wins."""
    status_expr = "EXCLUDED.status"
    if preserve_done:
        status_expr = (
            "CASE WHEN email_thread_status.status = 'DONE' "
            "AND EXCLUDED.status <> 'NEEDS_REPLY' THEN 'DONE' "
            "ELSE EXCLUDED.status END")
    await db.execute(text(
        f"""INSERT INTO email_thread_status
             (account_id, thread_id, status, last_message_id, last_message_at,
              reason, classified_at)
           VALUES (:aid, :tid, :st, :mid, :mat, :reason, now())
           ON CONFLICT (account_id, thread_id) DO UPDATE SET
             status = {status_expr},
             last_message_id = EXCLUDED.last_message_id,
             last_message_at = EXCLUDED.last_message_at,
             reason = EXCLUDED.reason, classified_at = now(),
             -- Re-arm the follow-up reminder whenever the thread changes hands.
             follow_up_reminded_at = CASE
               WHEN email_thread_status.status IS DISTINCT FROM EXCLUDED.status
                 OR email_thread_status.last_message_id IS DISTINCT FROM EXCLUDED.last_message_id
               THEN NULL ELSE email_thread_status.follow_up_reminded_at END"""
    ), {"aid": account_id, "tid": thread_id, "st": status, "mid": msg_id,
        "mat": msg_at, "reason": reason})


# Reply Zero status → (our thread status, the category label) mapping.
# NOTE: the map/rule KEYS are REPLY / AWAITING_REPLY / FYI / DONE (the user-facing
# labels are "Needs Reply" / "Awaiting Reply" / "FYI" / "Done"). Legacy tokens
# (TO_REPLY / ACTIONED / NEEDS_REPLY-as-rule-name) are still accepted via
# ``_LEGACY_STATUS_KEYS`` so a stale provider label delta or an un-migrated rule
# name still resolves.
_THREAD_STATUS_MAP = {
    "REPLY": ("NEEDS_REPLY", "Needs Reply"),
    "AWAITING_REPLY": ("AWAITING", "Awaiting Reply"),
    "DONE": ("DONE", "Done"),
    "FYI": ("FYI", "FYI"),
}

# Pre/post-rename conversation-status tokens → current keys. Applied wherever a
# system_type / rule-name / LLM status token is normalised, so old data keeps
# working across the "To Reply"→"Reply"→"Needs Reply" / "Actioned"→"Done"
# renames ("NEEDS_REPLY" is what the "Needs Reply" RULE NAME canonicalises to).
_LEGACY_STATUS_KEYS = {"TO_REPLY": "REPLY", "ACTIONED": "DONE",
                       "NEEDS_REPLY": "REPLY"}


def _canon_status_key(key: str | None) -> str:
    """Upper-case a status token and fold any legacy alias to its current key."""
    k = (key or "").upper().strip()
    return _LEGACY_STATUS_KEYS.get(k, k)

# Friendly reason prefix per recompute_thread_status trigger (shown in the UI).
_TRIGGER_REASON = {
    "outbound": "Replied",
    "inbound": "Inbound",
    "backfill": "Backfill",
    "reopen": "Reopened",
}

# The four conversation-status category labels are MUTUALLY EXCLUSIVE per thread
# (inbox-zero's removeConflictingThreadStatusLabels): when a thread's status
# changes, the other three must be cleared so an old label never lingers on the
# previous emails. "Follow-up" is a separate reminder label, cleared whenever the
# thread is no longer awaiting the other person.
_CONVERSATION_LABELS = ("Needs Reply", "Awaiting Reply", "Done", "FYI")
# Pre-rename label strings still living on the provider / local mirror. Always
# swept alongside the current labels so a resync clears the orphaned old tags.
_LEGACY_CONVERSATION_LABELS = ("Reply", "To Reply", "Actioned")
_FOLLOW_UP_LABEL = "Follow-up"

# The canonical definition of a "damaged" conversation thread: a statused
# conversation (#110 — one classification per conversation) that still shows the
# damage #112 repaired — a stale cleanup chip on it, or a message still sitting
# in the exact folder an APPLIED rule-move put it in. FYI is excluded on purpose:
# it is also the default stamp for "nothing matched" (#111), so FYI threads are
# not conversations and are never swept.
#
# This is the ONE copy of that definition. The one-off repair script
# (scripts/repair_conversation_threads.py) selects these rows to heal them, and
# the analytics health metric (count_damaged_conversation_threads) counts them so
# the #110 invariant has a permanent regression alarm instead of a script someone
# has to remember to run. Keep them sharing this constant — if the definition of
# "damaged" ever drifts between the alarm and the fix, the alarm lies.
DAMAGED_CONVERSATION_THREADS_SQL = """
    SELECT DISTINCT ts.account_id, ts.thread_id, ts.status
    FROM email_thread_status ts
    WHERE ts.status IN ('NEEDS_REPLY', 'AWAITING', 'DONE')
      AND (
        EXISTS (SELECT 1 FROM email_messages em
                WHERE em.account_id = ts.account_id
                  AND em.thread_id = ts.thread_id
                  AND em.categories && ARRAY['Newsletter', 'Marketing',
                      'Receipt', 'Calendar', 'Notification', 'Cold Email'])
        OR EXISTS (SELECT 1 FROM email_messages em
                WHERE em.account_id = ts.account_id
                  AND em.thread_id = ts.thread_id
                  AND LOWER(COALESCE(em.folder, '')) NOT IN
                      ('inbox', 'sent', 'drafts', 'trash', 'junk')
                  AND EXISTS (SELECT 1 FROM email_executed_rules er
                        JOIN email_actions ea ON ea.rule_id = er.rule_id
                         AND ea.type = 'MOVE_FOLDER'
                        WHERE er.message_id = em.id
                          AND er.status = 'APPLIED'
                          AND er.actions_taken @> '"MOVE_FOLDER"'
                          AND LOWER(TRIM(ea.label)) =
                              LOWER(COALESCE(em.folder, '')))))
"""


async def count_damaged_conversation_threads(
    db: Any, account_id: str | None = None, user_email: str | None = None,
) -> int:
    """How many statused conversations still show #112 damage — the health metric
    behind the #110 invariant. Zero is the only healthy value; a non-zero count
    means the cleaner/runner re-damaged conversations and the repair path (or a
    manual run of the repair script) needs to run.

    Scoped like the analytics overview: to ``account_id`` when given, else every
    account the user owns; unscoped (all accounts) when neither is passed, which
    is how the repair script and ops checks use it.
    """
    where = []
    params: dict[str, Any] = {}
    if account_id:
        where.append("d.account_id = :aid")
        params["aid"] = account_id
    if user_email:
        where.append(
            "d.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)"
        )
        params["uid"] = user_email
    filt = (" WHERE " + " AND ".join(where)) if where else ""
    row = (await db.execute(text(
        f"SELECT COUNT(*) AS n FROM ({DAMAGED_CONVERSATION_THREADS_SQL}) d{filt}"
    ), params)).fetchone()
    return int(row.n or 0)

# Conversation-status rule key → stored Reply Zero status. The rules pipeline is
# the single source of truth: when the engine matches one of these rules for an
# inbound message, the runner projects the corresponding status here (Reply Zero
# is a projection of the rules, not a parallel classifier).
_CONVERSATION_RULE_STATUS = {
    "REPLY": "NEEDS_REPLY",
    "AWAITING_REPLY": "AWAITING",
    "FYI": "FYI",
    "DONE": "DONE",
}
# Priority when several conversation rules match the same email (most actionable
# first) — REPLY must win over AWAITING/FYI/DONE.
_CONVERSATION_PRIORITY = ("REPLY", "AWAITING_REPLY", "FYI", "DONE")


def _match_conversation_key(match: dict[str, Any] | None) -> str:
    """The conversation-status key (REPLY/…) of a matched rule, or ""."""
    if not match:
        return ""
    rule = match.get("rule") or {}
    key = _canon_status_key(rule.get("system_type"))
    if not key:
        key = _canon_status_key((rule.get("name") or "").replace(" ", "_"))
    return key if key in _CONVERSATION_RULE_STATUS else ""


async def project_reply_status_from_matches(
    db: Any, account_id: str, message_row: Any,
    matches: list[dict[str, Any]] | None,
) -> str | None:
    """Store an inbound message's Reply Zero status from the conversation-status
    rule the engine matched — the unified path that makes Reply Zero a projection
    of the rules (no parallel classifier). Called by the rule runner on live runs.

    Picks the highest-priority conversation rule among ``matches``; when none
    matched, stores FYI so the thread stays out of the Reply view and isn't
    re-evaluated by the backfill. Returns the conversation-status LABEL applied
    (e.g. "Reply") when a conversation rule matched — so the caller can
    reconcile the mutually-exclusive thread labels — else None. Best-effort;
    caller commits. Only call for inbound mail (sends → ``_mark_thread_replied``)."""
    thread_id = getattr(message_row, "thread_id", None)
    if not thread_id:
        return None
    chosen, reason = "", ""
    for m in matches or []:
        key = _match_conversation_key(m)
        if not key:
            continue
        if not chosen or (_CONVERSATION_PRIORITY.index(key)
                          < _CONVERSATION_PRIORITY.index(chosen)):
            chosen, reason = key, (m.get("reason") or "")
    status = _CONVERSATION_RULE_STATUS.get(chosen, "FYI")
    # preserve_done: this is an AUTOMATED inbound re-projection — never let a
    # trailing FYI/notification silently re-open a thread the user marked DONE
    # (only a genuine NEEDS_REPLY re-opens it). A non-empty reason keeps the row
    # from looking like an un-determined blank so the backfill doesn't re-chew it.
    await _upsert_thread_status(
        db, account_id, thread_id, status, message_row.id,
        getattr(message_row, "received_at", None),
        reason or "Auto-classified", preserve_done=True)
    return _THREAD_STATUS_MAP[chosen][1] if chosen else None


# The thread-status judgment is hard to do well on a weak model (it's a
# multi-turn "whose court is it?" call), and any wrong/empty answer collapses to
# the AWAITING fallback — so default to a mid tier and ESCALATE once on failure
# rather than silently mis-classifying. Configurable hook left for a future
# per-account "status" model role.
_STATUS_MODEL = "tier-balanced"
_STATUS_MODEL_ESCALATION = "tier-powerful"
# How many chars of the thread the determiner reads. Kept as the TAIL (newest
# messages, incl. the user's closing reply) — never the head, which is what's
# safe to drop as a thread grows.
_THREAD_PROMPT_BUDGET = 8000


def _clip_thread_for_prompt(thread_text: str, limit: int = _THREAD_PROMPT_BUDGET) -> str:
    """Trim ``thread_text`` to its TAIL within ``limit`` chars.

    The determiner reads the thread oldest→newest, but when it's too long the
    OLDEST messages are the safe ones to drop — the status hinges on the NEWEST
    ones (especially the user's closing reply, which callers append last). The
    old ``thread_text[:6000]`` head-slice dropped exactly those, so a long thread
    the user had just replied to read as ending on the other person's message →
    AWAITING. Snap the cut to a message boundary so we never start mid-message,
    and mark the elision."""
    if len(thread_text) <= limit:
        return thread_text
    tail = thread_text[-limit:]
    sep = "\n\n---\n\n"
    idx = tail.find(sep)
    if idx != -1:
        tail = tail[idx + len(sep):]
    return "[… earlier messages omitted …]\n\n---\n\n" + tail


async def _llm_determine_thread_status(
    thread_text: str, user_email: str, about: str, *, user_sent_last: bool = True,
    model: str = _STATUS_MODEL, corrections: str = "",
) -> tuple[str, bool]:
    """Determine an email thread's status from the user's perspective — a faithful
    port of inbox-zero's aiDetermineThreadStatus.

    Returns ``(status, confident)`` where status is REPLY / AWAITING_REPLY /
    DONE (and FYI only when the user did NOT send last). ``confident`` is
    False when the call FELL BACK (LLM error, unparseable, or out-of-set) — the
    fallback is always AWAITING_REPLY/FYI, a one-directional bias, so callers
    should treat a non-confident status as PROVISIONAL (mark it for re-check)
    instead of trusting a fabricated AWAITING. One escalating retry to a stronger
    tier is attempted before falling back."""
    fallback = "AWAITING_REPLY" if user_sent_last else "FYI"
    try:
        fyi_state = "" if user_sent_last else "\n* FYI - No reply needed"
        fyi_opt = "" if user_sent_last else "FYI, "
        fyi_rules = "" if user_sent_last else (
            "\n- FYI: ONLY when there are absolutely no questions, requests, or "
            "pending actions anywhere in the thread, and the user RECEIVED the "
            "last message. A message where the user is only on Cc (not in To) "
            "and isn't directly asked anything is FYI, not REPLY.")
        last_rule = (
            "\n- Because the user sent the last email, FYI is NOT an option: "
            "choose AWAITING_REPLY if waiting on a response, or DONE if the "
            "thread is complete. If the user's last message asks no question and "
            "makes no commitment (e.g. a thank-you, acknowledgement, or 'sounds "
            "good'), prefer DONE." if user_sent_last else "")
        sys_prompt = (
            "You analyze an email thread and determine its current status from "
            "the user's perspective. It is in ONE of these mutually exclusive "
            "states:\n"
            "* REPLY - the user needs to reply\n"
            "* AWAITING_REPLY - waiting for the other person to respond/act"
            f"{fyi_state}\n* DONE - the thread is complete\n\n"
            "CRITERIA:\n"
            "- REPLY: someone asked the user a direct question or requested "
            "info/action and the user hasn't addressed it; OR the user promised "
            "a follow-up/deliverable and hasn't sent it. A clarifying question "
            "that got answered while a commitment is still pending is still "
            "REPLY.\n"
            "- AWAITING_REPLY: the ball is in the OTHER person's court — the user "
            "asked/requested and is still waiting, or someone else owes an "
            "action. If the user's request was already fulfilled, they are NO "
            "longer awaiting.\n"
            "- DONE: all questions answered and requests fulfilled, the "
            "conversation concluded, or the user sent info/recommendations and "
            "isn't waiting for anything. Taking ownership ('I'll handle it') "
            "fulfils a request unless it promises a later deliverable."
            f"{fyi_rules}\n\n"
            "RULES: weigh the WHOLE thread but the LAST message decides whose "
            "court the ball is in now; an earlier unanswered question/request "
            "still governs only if the last message didn't resolve it. A message "
            "marked '(you sent)' or '(your organisation sent)' is from YOUR side — "
            "a reply from the user's own organisation counts as your side having "
            "acted, so the ball is then in the OTHER party's court. If "
            "SOMEONE ELSE promised something → AWAITING_REPLY; if the USER "
            "promised a future reply/deliverable → REPLY."
            f"{last_rule}"
            # The user's own corrections outrank the generic criteria — that is
            # what a correction IS. Same contract as the classifier prompt
            # (engine._global_guidance_block), so a lesson taught via Fix
            # steers conversation-status calls too, not only cleanup picks.
            f"{corrections}\n\n"
            'Respond with ONLY a JSON object: {"status": "<one of REPLY, '
            f'AWAITING_REPLY, {fyi_opt}DONE>", "rationale": "<one line>"}}.'
        )
        ctx = f"You are acting on behalf of: {user_email}\n"
        if (about or "").strip():
            ctx += f"{about.strip()[:1200]}\n"
        user_prompt = (
            f"{ctx}\nEmail thread (oldest to newest):\n"
            f"{_clip_thread_for_prompt(thread_text)}\n\n"
            "Determine the current status of this thread."
        )
        allowed = {"REPLY", "AWAITING_REPLY", "DONE"}
        if not user_sent_last:
            allowed.add("FYI")
        messages = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}]
        # Try the configured tier, then escalate once. A wrong/empty answer here
        # always biases AWAITING, so a second stronger attempt is cheap insurance.
        for attempt_model in (model, _STATUS_MODEL_ESCALATION):
            data, _content, _used = await _llm_json(
                attempt_model, messages, max_tokens=500,
            )
            st = ((data.get("status") if isinstance(data, dict) else "") or "")
            st = _canon_status_key(st)  # tolerate a legacy TO_REPLY/ACTIONED reply
            if st in allowed:
                return st, True
        return fallback, False
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.determine_status_failed", error=str(exc)[:160])
        return fallback, False


def _msg_scope(
    r: Any, self_email: str,
    extra_domains: frozenset[str] | set[str] = frozenset(),
) -> str:
    """Direction of one stored message: 'self' (the owner sent it — folder='sent'
    or the from-address is the owner), 'internal' (the owner's organisation sent
    it — same/extra domain), or 'external'. The 'sent' folder is authoritative for
    'self' so an owner reply mirrored before its from-address resolves is still
    recognised as ours."""
    if (getattr(r, "folder", "") or "").lower() == "sent":
        return "self"
    raw = getattr(r, "from_address", None)
    frm = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return sender_scope(frm.get("email", ""), self_email, extra_domains)


def _fmt_thread_msg(
    r: Any, self_email: str = "",
    extra_domains: frozenset[str] | set[str] = frozenset(),
    attach_line: str = "",
) -> str:
    frm = r.from_address if isinstance(r.from_address, dict) \
        else json.loads(r.from_address or "{}")
    sender = frm.get("name") or frm.get("email") or "?"
    scope = _msg_scope(r, self_email, extra_domains)
    direction = {
        "self": " (you sent)",
        "internal": " (your organisation sent)",
    }.get(scope, "")
    lines = [f"From: {sender}{direction}"]
    # Recipients on INBOUND messages so the determiner can tell a direct (To)
    # recipient from a Cc-only one (Cc-only + no ask → FYI, not Reply).
    if scope == "external":
        to = _fmt_addr_list(getattr(r, "to_addresses", None))
        cc = _fmt_addr_list(getattr(r, "cc_addresses", None))
        if to:
            lines.append(f"To: {to}")
        if cc:
            lines.append(f"Cc: {cc}")
        # Deterministic recipient-role signal — don't make the model infer it from
        # the address lists (Cc-only + no direct ask → FYI, not Reply).
        from gateway.routes.email.automation.engine import (  # noqa: PLC0415
            _recipient_role,
        )
        if _recipient_role(self_email, getattr(r, "to_addresses", None),
                           getattr(r, "cc_addresses", None)) == "cc":
            lines.append("(the user is only Cc'd here, not a direct To recipient)")
    dt = getattr(r, "received_at", None)
    if hasattr(dt, "isoformat"):
        lines.append(f"Date: {dt.isoformat()}")
    lines.append(f"Subject: {r.subject or ''}")
    if attach_line:
        lines.append(attach_line)
    body = (r.body_text or r.snippet or "").strip()
    lines.append(body[:1500])
    return "\n".join(lines)


async def _conversation_rule_for_status(
    db: Any, account_id: str, status: str,
) -> dict[str, Any] | None:
    """The account's enabled conversation rule for a determined status
    (REPLY / AWAITING_REPLY / FYI / DONE), matched by system_type or name.
    None when no such rule is enabled."""
    from gateway.routes.email.automation.rules import _load_rules  # noqa: PLC0415
    for r in await _load_rules(db, account_id):
        if r.get("enabled") and _match_conversation_key({"rule": r}) == status:
            return r
    return None


async def _thread_is_conversation(
    db: Any, account_id: str, thread_id: str,
) -> bool:
    """Is this thread a CONVERSATION — mail between people — as opposed to a
    stream of bulk mail that happens to share a thread id?

    Decided from thread STATE, not from what the latest message looks like.
    That distinction is the whole bug this exists to fix: a supplier sending an
    invoice copy into a live RFQ thread LOOKS like a receipt in isolation, and
    classifying it per-message ripped one bubble out of the middle of a
    conversation (live account, 2026-07-21).

    Two signals, cheapest first, no model calls:
      1. A status row saying NEEDS_REPLY / AWAITING / DONE — statuses that are
         only ever written from a genuine two-way judgement, which outlives
         any one message. FYI rows are deliberately NOT enough: FYI is also
         the default stamp for "nothing matched" (3,226 of the live account's
         3,535 threads carry one, newsletters included), so an FYI row alone
         proves nothing and falls through to the participation test.
      2. Real back-and-forth: ≥2 messages and OUR side (the owner or their org
         domains) has participated. A newsletter blast never trips this; a
         colleague thread or a vendor exchange does.
    """
    st = (await db.execute(text(
        """SELECT 1 FROM email_thread_status
            WHERE account_id = :aid AND thread_id = :tid
              AND status IN ('NEEDS_REPLY', 'AWAITING', 'DONE')"""
    ), {"aid": account_id, "tid": thread_id})).fetchone()
    if st is not None:
        return True
    acc = (await db.execute(text(
        "SELECT email_address FROM email_accounts WHERE id = :id"
    ), {"id": account_id})).fetchone()
    self_email = ((acc.email_address if acc else "") or "").strip().lower()
    doms = {d.lower() for d in await resolve_org_domains(db, account_id)}
    if "@" in self_email:
        doms.add(self_email.split("@", 1)[1])
    if not doms:
        return False
    row = (await db.execute(text(
        """SELECT COUNT(*) AS n,
                  BOOL_OR(LOWER(COALESCE(folder, '')) = 'sent'
                          OR split_part(LOWER(COALESCE(
                                 from_address->>'email', '')), '@', 2)
                             = ANY(:doms)) AS ours
           FROM email_messages
           WHERE account_id = :aid AND thread_id = :tid"""
    ), {"aid": account_id, "tid": thread_id, "doms": sorted(doms)})).fetchone()
    return bool(row and (row.n or 0) >= 2 and row.ours)


async def _status_corrections_block(db: Any, account_id: str) -> str:
    """The user's taught corrections, rendered for the status determiner.

    Account-wide guidance plus anything attached to the conversation rules
    (Reply / Awaiting / FYI / Done). Cleanup-rule guidance is left out — "Zoho
    digests are Newsletter" has nothing to tell a determiner choosing between
    REPLY and DONE, and prompt space spent on it is pure noise. Best-effort:
    guidance failing to load must never block a status call."""
    try:
        from gateway.routes.email.automation.engine import (
            _load_rule_guidance,
        )
        g = await _load_rule_guidance(db, account_id)
        if not g:
            return ""
        notes = list(g.get("", []))
        conv_ids = set()
        if any(k for k in g if k):
            from gateway.routes.email.automation.rules import (
                _load_rules,
            )
            for r in await _load_rules(db, account_id):
                key = _match_conversation_key({"rule": r})
                if key:
                    conv_ids.add(str(r.get("id")))
                    for n in g.get(str(r.get("id")), []):
                        notes.append(f"[{r.get('name')}] {n}")
        if not notes:
            return ""
        body = "\n".join(f"- {n}" for n in notes)
        return ("\n\nCORRECTIONS THE USER HAS MADE BEFORE (these override "
                f"your default reading):\n{body}")
    except Exception as exc:
        _log.warning("email.status_corrections_failed",
                     account_id=account_id, error=str(exc)[:160])
        return ""


async def _restore_conversation_messages(
    db: Any, provider: Any, account_id: str, thread_id: str,
) -> None:
    """Bring back messages OUR OWN cleanup rules moved out of a conversation.

    Scope is deliberately narrow — this undoes exactly one thing: an APPLIED
    MOVE_FOLDER by one of the account's rules, where the message still sits in
    that rule's destination folder. If the user has since re-filed the message
    anywhere else, their move wins and it is left alone. Trash/junk/drafts are
    never touched (leaving a bin is a different decision than leaving a folder),
    and with no provider nothing moves — a local-only "move" would just be
    re-broken by the next sync. Idempotent: restored mail is in the inbox and
    never selected again. Best-effort per message; caller commits."""
    if provider is None:
        return
    rows = (await db.execute(text(
        """SELECT em.id, em.provider_message_id,
                  LOWER(COALESCE(em.folder, '')) AS folder,
                  ARRAY(SELECT ea.label FROM email_executed_rules er
                         JOIN email_actions ea ON ea.rule_id = er.rule_id
                          AND ea.type = 'MOVE_FOLDER'
                        WHERE er.message_id = em.id
                          AND er.status = 'APPLIED'
                          AND er.actions_taken @> '"MOVE_FOLDER"'
                          AND ea.label IS NOT NULL) AS move_labels
           FROM email_messages em
           WHERE em.account_id = :aid AND em.thread_id = :tid
             AND LOWER(COALESCE(em.folder, '')) NOT IN
                 ('inbox', 'sent', 'drafts', 'trash', 'junk')"""
    ), {"aid": account_id, "tid": thread_id})).fetchall()
    if not rows:
        return
    from email_ingestion.providers.base import canonical_folder
    for r in rows:
        dests = {canonical_folder((lbl or "").strip())
                 for lbl in (r.move_labels or []) if (lbl or "").strip()}
        if r.folder not in dests:
            continue  # not our doing (or the user re-filed it) — leave it
        try:
            new_pid = await provider.move_to_folder(
                r.provider_message_id, "inbox")
            # Outlook re-keys on move — persist the new id or every later
            # action on this message 404s (the #100 lesson).
            await db.execute(text(
                """UPDATE email_messages
                      SET folder = 'inbox', updated_at = now(),
                          provider_message_id =
                              COALESCE(:pid, provider_message_id)
                    WHERE id = :id"""
            ), {"id": r.id, "pid": new_pid or None})
            _log.info("email.conversation_message_restored",
                      account_id=account_id, thread_id=thread_id,
                      message_id=str(r.id), from_folder=r.folder)
        except Exception as exc:  # one message must not abort the rest
            _log.warning("email.conversation_restore_failed",
                         account_id=account_id, message_id=str(r.id),
                         error=str(exc)[:160])


async def resolve_conversation_status_matches(
    db: Any, account_id: str, message_row: Any,
    matches: list[dict[str, Any]] | None,
    *, provider: Any = None,
) -> list[dict[str, Any]] | None:
    """A conversation has ONE classification, re-evaluated on every new message.

    inbox-zero ``determineConversationStatus`` parity for INBOUND mail — but the
    trigger is the THREAD's state, not the latest message's looks. The old gate
    ran only when the per-message match picked a conversation rule, which is
    exactly backwards: the messages that most need thread context are the ones
    that DON'T look conversational in isolation. A supplier's invoice copy
    inside a live RFQ thread matched Receipt and was moved out of the
    conversation (live account, 2026-07-21); a bare "ok noted" matched nothing
    and left the thread status stale forever.

    So: if a conversation rule matched, OR the thread is already known to be a
    conversation (``_thread_is_conversation``), the status is re-determined over
    the FULL thread — the new message is new EVIDENCE about the conversation,
    not a new thing to classify — and the determined rule becomes the single
    match whose actions run. Non-conversation matches are returned flagged
    ``suppressed``: they are logged (History must explain why Receipt didn't
    fire) but never applied, because applying them is what splintered one
    conversation into Done + Receipt + FYI chips. Messages our own rules moved
    out are brought back (``_restore_conversation_messages``).

    Bulk mail — no back-and-forth, no status row, no conversation match — is
    untouched: per-message classification stands, and no model call is spent.
    On any failure (or no enabled rule for the determined status) returns the
    input unchanged, so classification degrades to the per-message pick."""
    matches = matches or []
    thread_id = getattr(message_row, "thread_id", None)
    if not thread_id:
        return matches
    if not any(_match_conversation_key(m) for m in matches) \
            and not await _thread_is_conversation(db, account_id, thread_id):
        return matches
    try:
        # Thread-status classification only decides whether a thread needs a
        # reply — the knowledge base is drafting facts, pure noise + token cost
        # in this prompt. Drop it (3.5).
        about, _sig = await _load_assistant_about(
            db, account_id, include_kb=False)
        acc = (await db.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = :id"
        ), {"id": account_id})).fetchone()
        acc_email = (acc.email_address if acc else "") or ""
        ctx = await build_thread_context(
            db, account_id, thread_id, acc_email)
        if ctx is None:
            return matches
        status, _confident = await _llm_determine_thread_status(
            ctx.thread_text, acc_email, about,
            user_sent_last=ctx.our_side_last,
            corrections=await _status_corrections_block(db, account_id))
        target = await _conversation_rule_for_status(db, account_id, status)
        if not target:
            return matches
        determined = {"rule": target, "reason": f"Thread status: {status}",
                      "source": "thread_status", "is_primary": True}
        # The losing matches ride along FLAGGED, not live. The runner logs them
        # as SKIPPED so History answers "why wasn't this filed as a Receipt?",
        # but their actions never run — running them is what moved one bubble
        # of a conversation into the Receipt folder while the thread said Done.
        suppressed = [{**m, "suppressed": "conversation"}
                      for m in matches if not _match_conversation_key(m)]
        await _restore_conversation_messages(db, provider, account_id, thread_id)
        return [determined, *suppressed]
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.resolve_conversation_status_failed",
                     account_id=account_id, error=str(exc)[:160])
        return matches


async def _reconcile_thread_labels(
    db: Any, provider: Any, account_id: str, thread_id: str,
    keep_label: str | None,
) -> None:
    """Enforce inbox-zero's mutually-exclusive conversation labels on a thread.

    Across EVERY message in the thread, remove the conversation-status labels
    other than ``keep_label`` (Reply / Awaiting Reply / Done / FYI) — plus
    the "Follow-up" reminder unless the thread is still awaiting — so an old label
    never lingers on the previous emails after the thread changes hands. Then
    ensure ``keep_label`` is present on the latest inbound message. Mirrors the
    change locally (email_messages.categories) and upstream (provider). Best-effort
    per message; caller commits.

    ``provider`` may be None — the local mirror is still corrected, the upstream
    apply is simply skipped. Stated explicitly because callers without a
    provider used to rely on ``None.set_labels`` raising into the best-effort
    handler, which works by accident and reads like a bug."""
    rows = (await db.execute(text(
        """SELECT id, provider_message_id, categories, folder
           FROM email_messages
           WHERE account_id = :aid AND thread_id = :tid
           ORDER BY received_at ASC NULLS FIRST"""
    ), {"aid": account_id, "tid": thread_id})).fetchall()
    if not rows:
        return
    stale = {lab for lab in (*_CONVERSATION_LABELS, *_LEGACY_CONVERSATION_LABELS)
             if lab != keep_label}
    if keep_label != "Awaiting Reply":  # follow-up only applies while awaiting
        stale.add(_FOLLOW_UP_LABEL)
    if keep_label in _CONVERSATION_LABELS:
        # A conversation has ONE classification: its status. Cleanup chips
        # (Receipt / Marketing / …) stamped on individual messages before the
        # thread was recognised as a conversation are the "Done + Receipt + FYI
        # on one thread" bug, message by message — shed them here, where the
        # thread's labels are already being made mutually exclusive. Engine
        # labels only: the user's own hand-made labels live in `labels`, not
        # `categories`, so they are never touched.
        stale.update(CLEANUP_CATEGORIES)
    for r in rows:
        to_remove = [c for c in list(r.categories or []) if c in stale]
        if not to_remove:
            continue
        # Update our mirror FIRST so the chip reflects the change immediately,
        # even if the provider call fails or lags; the provider apply is a
        # best-effort follow-up (it must NOT gate the local state, or the UI
        # ends up with a label removed and the replacement never applied).
        await db.execute(text(
            "UPDATE email_messages SET categories = ARRAY("
            "  SELECT c FROM unnest(categories) AS c WHERE NOT (c = ANY(:rm))"
            "), updated_at = now() WHERE id = :id"
        ), {"id": r.id, "rm": to_remove})
        if provider is None:
            continue
        try:
            await provider.set_labels(
                r.provider_message_id, add=[], remove=to_remove)
        except Exception:  # noqa: BLE001 — one bad message shouldn't abort
            continue
    if not keep_label:
        return
    # Ensure the new status label is on the latest INBOUND message (the one a
    # reader looks at); fall back to the latest message if all are outbound.
    inbound = [r for r in rows if (r.folder or "").lower() != "sent"]
    target = inbound[-1] if inbound else rows[-1]
    if keep_label in list(target.categories or []):
        return
    # Mirror first (see above), then best-effort provider apply — so a thread
    # that's just been replied to flips to "Done"/"Awaiting Reply" in the UI
    # at once instead of losing "Reply" and showing no tag at all.
    await db.execute(text(
        "UPDATE email_messages SET categories = CASE "
        "WHEN :lbl = ANY(categories) THEN categories "
        "ELSE array_append(categories, :lbl) END, "
        "updated_at = now() WHERE id = :id"
    ), {"id": target.id, "lbl": keep_label})
    with contextlib.suppress(Exception):  # provider apply is best-effort
        await provider.set_labels(
            target.provider_message_id, add=[keep_label], remove=[])


# ── Thread-status authority ──────────────────────────────────────────────────
# One place builds a thread's context and one function determines + writes its
# status, so every trigger (a sent reply, a new inbound, the backfill, a reopen)
# decides identically — over the WHOLE thread, with direction resolved the same
# way — instead of each call site assembling the thread and guessing
# ``user_sent_last`` for itself.


@dataclass
class ThreadContext:
    """A thread prepared for the status determiner.

    ``our_side_last`` — the last message was sent by YOUR side (the owner or the
    owner's organisation), so FYI is off and the determiner weighs Awaiting vs
    Done. ``thread_text`` is direction-annotated (you / your organisation /
    external) and ``last_message_id``/``last_message_at`` are what the status row
    should be stamped with (``now()`` when an unsynced just-sent reply is folded
    in)."""
    thread_id: str
    last_message_id: Any
    last_message_at: Any
    our_side_last: bool
    has_external: bool
    thread_text: str


async def build_thread_context(
    db: Any, account_id: str, thread_id: str, self_email: str, *,
    extra_domains: frozenset[str] | set[str] | None = None,
    pending_reply: tuple[str, str] | None = None,
) -> ThreadContext | None:
    """Load a thread and render it for the determiner, once, for every caller.

    ``extra_domains`` defaults to None → the account's configured org domains are
    resolved here (so every status path is org-domain-aware without each caller
    plumbing it). Pass an explicit set (e.g. ``frozenset()``) to skip the lookup.

    ``pending_reply`` is ``(body, subject)`` of a reply the owner JUST sent that
    isn't mirrored into ``email_messages`` yet — it's appended as the final
    ``(you sent)`` message so the determination is accurate immediately (and
    ``our_side_last`` becomes True). Returns None when the thread has no rows."""
    if extra_domains is None:
        extra_domains = await resolve_org_domains(db, account_id)
    rows = (await db.execute(text(
        """SELECT id, from_address, to_addresses, cc_addresses, subject,
                  body_text, snippet, folder, received_at
           FROM email_messages
           WHERE account_id = :aid AND thread_id = :tid
           ORDER BY received_at ASC NULLS FIRST"""
    ), {"aid": account_id, "tid": thread_id})).fetchall()
    if not rows:
        return None
    latest = rows[-1]
    # Attachment metadata (filename + MIME) per message, so the determiner sees
    # "Attachments: invoice.pdf (…)" — a strong signal for status/intent.
    attach = await _attachment_summaries(db, [r.id for r in rows])
    parts = [_fmt_thread_msg(r, self_email, extra_domains,
                             attach.get(str(r.id), "")) for r in rows]
    scopes = [_msg_scope(r, self_email, extra_domains) for r in rows]
    has_external = any(s == "external" for s in scopes)
    our_side_last = scopes[-1] != "external"

    reply_pending = bool(pending_reply and pending_reply[0]) and (
        (latest.folder or "").lower() != "sent")
    if reply_pending:
        body, subject = pending_reply  # type: ignore[misc]
        parts.append(
            f"From: {self_email} (you sent)\n"
            f"Subject: {subject or latest.subject or ''}\n{body[:1500]}")
        our_side_last = True

    # Anchor last activity to the just-sent reply (not yet in the DB) so the
    # follow-up clock starts from NOW, not the inbound message we replied to.
    last_at = (datetime.now(timezone.utc)
               if reply_pending else latest.received_at)
    return ThreadContext(
        thread_id=thread_id, last_message_id=latest.id, last_message_at=last_at,
        our_side_last=our_side_last, has_external=has_external,
        thread_text="\n\n---\n\n".join(parts))


async def recompute_thread_status(
    db: Any, account_id: str, thread_id: str, *, trigger: str,
    about: str = "", acc_email: str = "",
    extra_domains: frozenset[str] | set[str] | None = None,
    pending_reply: tuple[str, str] | None = None,
    model: str = _STATUS_MODEL,
) -> tuple[str, str] | None:
    """THE thread-status authority: build the context, determine the status over
    the whole thread (with ``user_sent_last`` taken from the real last message —
    not assumed by the trigger), and write it through the single writer.

    ``trigger`` is one of ``outbound`` / ``inbound`` / ``backfill`` / ``reopen``.
    Automated triggers (inbound / backfill) PRESERVE a user's DONE; a reply the
    owner sent (outbound) or an explicit reopen may move it. A low-confidence
    (fallback) determination is tagged ``· auto`` so the backfill re-checks it.
    Returns ``(rz_status, label)`` or None when the thread has no messages.
    Caller commits; reconciling provider labels is the caller's job."""
    ctx = await build_thread_context(
        db, account_id, thread_id, acc_email,
        extra_domains=extra_domains, pending_reply=pending_reply)
    if ctx is None:
        return None
    status, confident = await _llm_determine_thread_status(
        ctx.thread_text, acc_email, about,
        user_sent_last=ctx.our_side_last, model=model,
        corrections=await _status_corrections_block(db, account_id))
    rz_status, label = _THREAD_STATUS_MAP.get(
        _canon_status_key(status), ("AWAITING", "Awaiting Reply"))
    # Whoever spoke last is a FACT, not a judgment call. The determiner
    # sometimes answers REPLY even when the real last message is the owner's —
    # which stamps "you owe a reply" on a thread the user just answered, and the
    # digest then repeats that lie every morning (10 of 29 NEEDS_REPLY rows on
    # the live account were exactly this). When our side sent last, the only
    # honest open state is AWAITING; the reason records the coercion.
    coerced = ctx.our_side_last and rz_status == "NEEDS_REPLY"
    if coerced:
        rz_status, label = "AWAITING", "Awaiting Reply"
    prefix = _TRIGGER_REASON.get(trigger, trigger.capitalize())
    # A low-confidence (fallback) determination keeps the "· auto" marker so the
    # backfill re-checks it instead of trusting a guessed AWAITING.
    reason = (f"{prefix} — {status}"
              + (" → Awaiting (we sent last)" if coerced else "")
              + ("" if confident else " · auto"))
    await _upsert_thread_status(
        db, account_id, thread_id, rz_status, ctx.last_message_id,
        ctx.last_message_at, reason,
        preserve_done=(trigger in ("inbound", "backfill")))
    return rz_status, label


async def _mark_thread_replied(
    account_id: str, thread_id: str,
    sent_body: str | None = None, sent_subject: str | None = None,
) -> None:
    """After the user sends a reply, re-determine the thread's status with the
    AI (exact inbox-zero aiDetermineThreadStatus parity) and reconcile labels:
    set the Reply Zero status and collapse the thread to a SINGLE conversation
    label (removing any stale Reply / Awaiting / FYI / Follow-up). Since the
    user just sent, FYI is excluded — the AI picks AWAITING_REPLY (waiting on
    them) or DONE (done). Best-effort.

    ``sent_body``/``sent_subject`` carry the reply the user just sent. It usually
    isn't mirrored into email_messages yet (it lands on the next sync), so pass it
    here and we append it to the thread the AI reads — making the Awaiting-vs-
    Done call accurate immediately (inbox-zero sees the sent message at once)
    instead of defaulting to Awaiting and only correcting on the next sync."""
    if not thread_id:
        return
    # H4: called only from _maybe_classify_threads (scheduler post-sync
    # path); no ambient tenant to inherit.
    db = await _get_db()
    try:
        # Thread-status classification only decides whether a thread needs a
        # reply — the knowledge base is drafting facts, pure noise + token cost
        # in this prompt. Drop it (3.5).
        about, _sig = await _load_assistant_about(
            db, account_id, include_kb=False)
        acc = (await db.execute(text(
            "SELECT email_address, provider, credentials_encrypted "
            "FROM email_accounts WHERE id = :id"
        ), {"id": account_id})).fetchone()
        acc_email = (acc.email_address if acc else "") or ""

        # The thread-status authority does the whole-thread determination + write
        # (over the SAME context, with the just-sent reply folded in). Outbound
        # trigger: the owner replied, so this may move a DONE thread.
        result = await recompute_thread_status(
            db, account_id, thread_id, trigger="outbound",
            about=about, acc_email=acc_email,
            pending_reply=(sent_body, sent_subject or "") if sent_body else None)
        await db.commit()
        if result is None:
            return
        _rz_status, new_cat = result

        # Reconcile the thread to a SINGLE conversation label (mutually exclusive,
        # inbox-zero parity): clear any stale Reply / Awaiting / FYI / Follow-up
        # across the thread and apply the new status label. Needs the provider.
        if not acc:
            return
        from acb_llm.key_store import get_key_store  # noqa: PLC0415
        store = get_key_store()
        creds = json.loads(store.decrypt(acc.credentials_encrypted))
        provider = _instantiate_provider(acc.provider, creds)
        if not await provider.authenticate():
            return
        await _reconcile_thread_labels(
            db, provider, account_id, thread_id, new_cat)
        if provider.credentials_dirty():
            await _persist_rotated_creds(db, store, account_id, provider)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.mark_thread_replied_failed", error=str(exc)[:160])
    finally:
        await db.close()


async def _reconcile_labels_bg(
    account_id: str, thread_id: str, keep_label: str | None,
) -> None:
    """Background: instantiate the provider and collapse a thread's Reply Zero
    labels to ``keep_label`` (None clears all conversation + Follow-up labels).

    Used by Mark Done / Reopen so the provider + local labels match the new
    status — without this the status row alone moved the thread in our view but
    left the stale Reply / Awaiting / Follow-up labels behind on the provider.
    Best-effort."""
    if not thread_id:
        return
    # H4: background consumer — _reconcile_labels_bg runs as a
    # post-response BackgroundTask; no ambient tenant to inherit.
    db = await _get_db()
    try:
        acc = (await db.execute(text(
            "SELECT provider, credentials_encrypted FROM email_accounts "
            "WHERE id = :id"
        ), {"id": account_id})).fetchone()
        if not acc:
            return
        from acb_llm.key_store import get_key_store  # noqa: PLC0415
        store = get_key_store()
        creds = json.loads(store.decrypt(acc.credentials_encrypted))
        provider = _instantiate_provider(acc.provider, creds)
        if not await provider.authenticate():
            return
        await _reconcile_thread_labels(
            db, provider, account_id, thread_id, keep_label)
        if provider.credentials_dirty():
            await _persist_rotated_creds(db, store, account_id, provider)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.reconcile_labels_bg_failed",
                     account_id=account_id, error=str(exc)[:160])
    finally:
        await db.close()


async def apply_thread_status_correction(
    account_id: str, thread_id: str, status_key: str,
) -> dict[str, Any]:
    """Force a thread's Reply Zero status to a user-corrected value (the Fix flow).

    Conversation status (Reply / Awaiting / FYI / Done) is re-derived from
    the full thread by the classifier, so a learned sender/subject pattern would be
    OVERRIDDEN — the only correction that sticks is to set the status directly and
    swap the labels. ``status_key`` is REPLY / AWAITING_REPLY / FYI / DONE
    (legacy TO_REPLY / ACTIONED still accepted).
    Best-effort; returns ``{ok, status, label}``."""
    rz_status, label = _THREAD_STATUS_MAP.get(
        _canon_status_key(status_key), ("", ""))
    if not rz_status or not thread_id:
        return {"ok": False}
    # H4: mixed callers — apply_thread_status_correction also runs from the
    # background sync's label learner (_apply_label_status_corrections);
    # needs an explicit tenant from the account row.
    db = await _get_db()
    try:
        latest = (await db.execute(text(
            "SELECT id, received_at FROM email_messages "
            "WHERE account_id = :aid AND thread_id = :tid "
            "ORDER BY received_at DESC NULLS LAST LIMIT 1"
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        await _upsert_thread_status(
            db, account_id, thread_id, rz_status,
            latest.id if latest else None,
            latest.received_at if latest else None, "Fix correction")
        await db.commit()
        # Best-effort: swap the provider/local labels to the corrected status.
        acc = (await db.execute(text(
            "SELECT provider, credentials_encrypted FROM email_accounts "
            "WHERE id = :id"
        ), {"id": account_id})).fetchone()
        if acc:
            from acb_llm.key_store import get_key_store  # noqa: PLC0415
            store = get_key_store()
            creds = json.loads(store.decrypt(acc.credentials_encrypted))
            provider = _instantiate_provider(acc.provider, creds)
            if await provider.authenticate():
                await _reconcile_thread_labels(
                    db, provider, account_id, thread_id, label)
                if provider.credentials_dirty():
                    await _persist_rotated_creds(db, store, account_id, provider)
                await db.commit()
        return {"ok": True, "status": rz_status, "label": label}
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.apply_status_correction_failed",
                     account_id=account_id, error=str(exc)[:160])
        return {"ok": False}
    finally:
        await db.close()


# How many newly-detected outbound replies (sent threads) get the full AI status
# determination + label swap per backfill cycle. Replies are bounded USER actions
# (you don't send hundreds a cycle), so this cap is generous. Threads past the cap
# are LEFT UNWRITTEN — NOT stamped a blind AWAITING — so they keep
# `existing[thread] != latest_id` and are re-tried next cycle until they get a
# real determination (newest-first, so the most recent drain first). Stamping a
# blind AWAITING here was the bug that showed concluded replies as "Awaiting".
_REPLY_DETERMINE_CAP = 40
# How many inbound gap threads get an engine match (classification) per cycle.
_BACKFILL_INBOUND_CAP = 25


async def _maybe_classify_threads(account_id: str) -> None:
    """Reply Zero BACKFILL: fill in per-thread status for threads the live rules
    pipeline hasn't classified yet — historical mail, accounts with auto-apply
    off, or anything the runner missed.

    Reuses the SAME rule engine as live classification, so Reply Zero stays a
    projection of the rules and never a parallel classifier.

    Sent-last GAP threads mean a NEW outbound message arrived — whether sent via
    Metorite OR the user's native email client. They get the SAME AI status
    determination + label swap as a CC-initiated reply (``_mark_thread_replied``),
    so a reply sent from Gmail/Outlook directly still reaches Awaiting/Done and
    loses its "Reply" label — inbox-zero handleOutboundMessage parity. This is
    capped per cycle; sent threads beyond the cap are left UNWRITTEN and re-tried
    next cycle (never blind-stamped AWAITING). Inbound-last gap threads are matched
    by the engine (which applies the Reply Zero pre-filter that keeps newsletters/
    broadcasts out of "Reply") and projected via the matched conversation-status
    rule (FYI when none matches). Touches threads whose latest message changed OR
    whose stored status is provisional ("· auto" — a prior LLM fallback), so a
    guessed AWAITING self-heals. Caps work per cycle. Best-effort (never raises)."""
    # H4: scheduler post-sync hook path; no ambient tenant to inherit.
    db = await _get_db()
    try:
        from gateway.routes.email.automation.engine import (  # noqa: PLC0415
            LLMUnavailable,
            classify_matches,
            email_dict_from_row,
        )
        # Select threads that NEED WORK, not simply the newest ones.
        #
        # This used to take the 200 most recent threads from the last 30 days and
        # skip the ones already classified. On a real mailbox those 200 are
        # exactly the already-classified ones, so the backfill spun without
        # reaching anything older — measured live: 295 of 3,487 threads (8.5%)
        # had a status, and mail older than a month could never acquire one.
        # Filtering in SQL means every cycle picks up 200 threads that actually
        # need doing, and the backlog drains instead of standing still.
        rows = (await db.execute(text(
            """WITH latest AS (
                 SELECT DISTINCT ON (thread_id) thread_id, id, subject,
                        from_address, to_addresses, cc_addresses, body_text,
                        snippet, folder, received_at
                 FROM email_messages
                 WHERE account_id = :aid AND thread_id IS NOT NULL
                 ORDER BY thread_id, received_at DESC
               )
               SELECT l.* FROM latest l
                 LEFT JOIN email_thread_status s
                        ON s.account_id = :aid AND s.thread_id = l.thread_id
                WHERE s.thread_id IS NULL
                   OR s.last_message_id::text <> l.id::text
                   OR COALESCE(s.reason, '') LIKE '%· auto'
                -- Inbox first. Those are the threads that might still need a
                -- reply, so they must not queue behind a filed backlog that is
                -- both larger and already dealt with — ordering by date alone
                -- put 2,622 archived threads ahead of the 273 live ones.
                ORDER BY CASE LOWER(COALESCE(l.folder, ''))
                           WHEN 'inbox' THEN 0 WHEN 'sent' THEN 1 ELSE 2 END,
                         l.received_at DESC
                LIMIT 200"""
        ), {"aid": account_id})).fetchall()
        if not rows:
            return
        # Carry last_message_id + reason so we can both (a) skip threads whose
        # latest message is unchanged, and (b) STILL re-determine a thread whose
        # stored status is PROVISIONAL — a "· auto" reason means a prior LLM
        # fallback guessed AWAITING. Those self-heal here instead of sticking.
        existing = {
            r.thread_id: (str(r.last_message_id), r.reason or "")
            for r in (await db.execute(text(
                "SELECT thread_id, last_message_id, reason "
                "FROM email_thread_status WHERE account_id = :aid"
            ), {"aid": account_id})).fetchall()
        }
        # Thread-status classification only decides whether a thread needs a
        # reply — the knowledge base is drafting facts, pure noise + token cost
        # in this prompt. Drop it (3.5).
        about, _sig = await _load_assistant_about(
            db, account_id, include_kb=False)
        acc = (await db.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = :id"
        ), {"id": account_id})).fetchone()
        self_email = (acc.email_address if acc else "") or ""
        extra_domains = await resolve_org_domains(db, account_id)

        gap_inbound = []
        sent_handled = 0
        filed = 0
        for r in rows:
            prev = existing.get(r.thread_id)
            provisional = bool(prev) and prev[1].endswith("· auto")
            if prev and prev[0] == str(r.id) and not provisional:
                continue  # latest message unchanged + confidently classified
            folder = (r.folder or "").lower()
            if folder == "sent":
                # New outbound message (CC reply OR native-client reply) →
                # re-determine status with the AI and swap labels, exactly like a
                # CC send. Capped per cycle; threads past the cap are left
                # UNWRITTEN (not blind-AWAITING) so they retry next cycle.
                if sent_handled < _REPLY_DETERMINE_CAP:
                    # End OUR transaction before the model call.
                    #
                    # `_mark_thread_replied` opens its own session and spends a
                    # full LLM determination in it. Ours has been in a
                    # transaction since the reads above, so without this commit
                    # it sits `idle in transaction` — holding ACCESS SHARE on
                    # every table it touched — for the length of that call,
                    # times up to _REPLY_DETERMINE_CAP consecutive sent threads.
                    #
                    # That is the exact shape that took production down on
                    # 2026-08-06: a session parked mid-LLM-call, a migration's
                    # ALTER TABLE queued behind its lock, and Postgres's FIFO
                    # lock queue then stalling every later reader of that table.
                    #
                    # The acb_llm wall-clock ceiling and the 600s
                    # idle_in_transaction_session_timeout bound that damage;
                    # this removes its cause. The ceiling alone is NOT enough
                    # here — 40 capped calls idle well past 600s, at which point
                    # Postgres kills this session and the upserts accumulated
                    # below are lost with it. Committing first is what makes the
                    # backstop and this loop safe together.
                    await db.commit()
                    await _mark_thread_replied(account_id, r.thread_id)
                    sent_handled += 1
                # else: overflow — leave it for the next cycle, never guess.
            elif folder == "inbox":
                gap_inbound.append(r)
            else:
                # Inbound-last, but the user already FILED it (archived, or moved
                # to one of their own folders). Filing it is the answer: they
                # dealt with it, so it is FYI and stays out of the Reply view.
                #
                # Deterministic on purpose. These are the bulk of an old mailbox
                # — 2,622 of 3,191 unclassified threads on the live account — and
                # spending a model call apiece to re-litigate mail the user has
                # already put away is exactly the token waste this pipeline was
                # asked to stop. preserve_done keeps an explicit Done intact.
                await _upsert_thread_status(
                    db, account_id, r.thread_id, "FYI", r.id, r.received_at,
                    "Filed without a reply — treated as handled.",
                    preserve_done=True)
                filed += 1
        await db.commit()

        gap = gap_inbound[:_BACKFILL_INBOUND_CAP]  # cap engine work per cycle
        gap_attach = await _attachment_summaries(db, [r.id for r in gap])
        # The provider is needed to collapse a thread's labels upstream as well
        # as locally. Fetched once, and only when there is inbound work — an
        # auth failure must not stop the deterministic passes above.
        provider = store = None
        if gap:
            acc_row = (await db.execute(text(
                "SELECT provider, credentials_encrypted FROM email_accounts "
                "WHERE id = :id"
            ), {"id": account_id})).fetchone()
            if acc_row:
                from acb_llm.key_store import get_key_store  # noqa: PLC0415
                store = get_key_store()
                creds = json.loads(store.decrypt(acc_row.credentials_encrypted))
                provider = _instantiate_provider(acc_row.provider, creds)
                if not await provider.authenticate():
                    provider = None
        for r in gap:
            # extra_domains is a KEYWORD arg (positional lands it in self_name).
            email = email_dict_from_row(
                r, self_email, about, extra_domains=extra_domains,
                attachments=gap_attach.get(str(r.id), ""))
            # Match + full-thread status determination through the shared
            # enforcement point (the SAME #110 path the live runner uses).
            try:
                matches = await classify_matches(
                    db, account_id, r, email,
                    multi_rule=False, resolve=True, provider=provider)
            except LLMUnavailable:
                # Classifier down for this one — skip it (this backfill writes no
                # watermark, so the gap query re-selects it next cycle) rather
                # than abort the whole batch on the outer handler.
                continue
            keep_label = await project_reply_status_from_matches(
                db, account_id, r, matches)
            # Collapse the thread to that ONE conversation label. This backfill
            # wrote the status row but never reconciled the labels, so earlier
            # messages kept whatever they were tagged with and a thread ended up
            # wearing Reply AND Awaiting AND Done at once — 68 threads on the
            # live account. The status row and the labels are two views of one
            # decision; writing only the first is what let them disagree.
            if keep_label:
                await _reconcile_thread_labels(
                    db, provider, account_id, r.thread_id, keep_label)
            await db.commit()
        if provider is not None and store is not None \
                and provider.credentials_dirty():
            await _persist_rotated_creds(db, store, account_id, provider)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.classify_threads_failed",
                     account_id=account_id, error=str(exc)[:200])
    finally:
        await db.close()


# Reclassify progress, so the UI (or a poller) can watch a whole-mailbox rebuild
# drain instead of guessing when a fire-and-forget job finished. Same token guard
# as the other email jobs — a second reclassify supersedes the first cleanly.
_RECLASSIFY_JOBS = JobTracker()


def _business_days_cutoff(days: float) -> datetime:
    """UTC timestamp ``days`` business days before now (weekends skipped) — the
    follow-up window inbox-zero uses so a Friday email isn't chased on Sunday.
    The whole-day part steps over Mon–Fri; any fraction is applied as hours."""
    d = datetime.now(timezone.utc)
    whole = int(days)
    stepped = 0
    while stepped < whole:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            stepped += 1
    frac = days - whole
    if frac > 0:
        d -= timedelta(hours=frac * 24)
    return d


def _dt_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Safety ceiling on drain passes: 200 threads scanned per pass, so this covers a
# ~40k-thread mailbox — far past any real one. The real stop is "no more work" or
# "a pass made no progress"; this only bounds a pathological loop.
_RECLASSIFY_MAX_PASSES = 200


async def _count_reply_zero_backlog(db: Any, account_id: str) -> int:
    """How many threads still NEED a status — the same "needs work" predicate the
    backfill selects on (statusless, latest-message changed, or a provisional
    "· auto" status). Drives both the progress total and the drain's stop test."""
    return (await db.execute(text(
        """WITH latest AS (
             SELECT DISTINCT ON (thread_id) thread_id, id
             FROM email_messages
             WHERE account_id = :aid AND thread_id IS NOT NULL
             ORDER BY thread_id, received_at DESC
           )
           SELECT COUNT(*) FROM latest l
             LEFT JOIN email_thread_status s
                    ON s.account_id = :aid AND s.thread_id = l.thread_id
            WHERE s.thread_id IS NULL
               OR s.last_message_id::text <> l.id::text
               OR COALESCE(s.reason, '') LIKE '%· auto'"""
    ), {"aid": account_id})).scalar() or 0


async def _reclassify_reply_zero_job(
    account_id: str, *, token: int | None = None,
) -> None:
    """Rebuild an account's Reply Zero statuses from scratch with the current
    rules-based logic — used when the classifier changed (e.g. to clear threads
    that the old parallel classifier stale-labelled as needs-reply).

    Drops the DERIVED statuses (NEEDS_REPLY / AWAITING / FYI) but PRESERVES DONE,
    so a user's "Mark done" decisions survive a reclassify. Then DRAINS the whole
    mailbox: it runs the backfill pass repeatedly until nothing still needs a
    status — not a fixed 8 passes, which on a real mailbox (3,500 threads, 200 a
    pass) rebuilt only the newest ~1,600 and left the rest on the old logic.

    Resumable by construction: a pass that makes NO progress (LLM down, so the
    inbound remainder can't be classified) stops the drain rather than spinning —
    those threads keep their gap, so re-triggering reclassify picks up where this
    left off. Progress is published per pass for the UI to poll. Best-effort."""
    # H4: background consumer — _reclassify_reply_zero_job runs as a
    # post-response BackgroundTask; no ambient tenant to inherit.
    db = await _get_db()
    try:
        await db.execute(text(
            "DELETE FROM email_thread_status "
            "WHERE account_id = :aid AND status <> 'DONE'"
        ), {"aid": account_id})
        await db.commit()
        total = await _count_reply_zero_backlog(db, account_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.reclassify_reset_failed",
                     account_id=account_id, error=str(exc)[:160])
        _RECLASSIFY_JOBS.finish(
            account_id, token, status="error", error=str(exc)[:160],
            finished_at=_dt_now_iso())
        await db.close()
        return
    finally:
        await db.close()

    _RECLASSIFY_JOBS.update(account_id, token, total=total, remaining=total)
    prev_remaining: int | None = None
    for _ in range(_RECLASSIFY_MAX_PASSES):
        # H4: background consumer (see above) — per-batch session of the same job.
        db = await _get_db()
        try:
            remaining = await _count_reply_zero_backlog(db, account_id)
        finally:
            await db.close()
        _RECLASSIFY_JOBS.update(
            account_id, token, remaining=remaining,
            processed=max(0, total - remaining))
        if remaining == 0:
            break
        # No forward progress since the last pass → the remainder can't be
        # classified right now (e.g. the LLM is unavailable). Stop instead of
        # looping to the safety cap; the gap persists, so this is resumable.
        if prev_remaining is not None and remaining >= prev_remaining:
            break
        prev_remaining = remaining
        await _maybe_classify_threads(account_id)
    _RECLASSIFY_JOBS.finish(
        account_id, token, status="done", finished_at=_dt_now_iso())


class ThreadResolveRequest(BaseModel):
    account_id: str
    thread_id: str
    done: bool = True  # True = mark done (resolved); False = reopen
    # Dismiss ≠ Done: "never mind this thread" without pretending completion.
    # Files the thread as FYI (not awaiting anything, nothing claimed finished)
    # and does NOT close tasks captured from it. Overrides ``done``.
    dismiss: bool = False


@router.post("/reply-zero/resolve")
async def resolve_thread(
    req: ThreadResolveRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Mark a thread done (inbox-zero's "Mark Done" / resolved=true) or reopen it.

    Done → status='DONE' (shows under the Done tab) and the provider/local labels
    are collapsed to "Done" (clearing stale Reply / Awaiting / Follow-up).
    Reopen → re-derive NEEDS_REPLY/AWAITING from the latest message's folder and
    swap the label back to Reply / Awaiting Reply."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        keep_label = "Done"
        if req.dismiss:
            # A dead loop the user will never answer: file it as FYI. DONE
            # would lie (nothing was completed — and it would close captured
            # tasks); leaving it NEEDS_REPLY keeps a zombie in the ledger.
            keep_label = "FYI"
            await db.execute(text(
                "UPDATE email_thread_status SET status = 'FYI', "
                "reason = 'Dismissed by the user', classified_at = now() "
                "WHERE account_id = :aid AND thread_id = :tid"
            ), {"aid": req.account_id, "tid": req.thread_id})
        elif req.done:
            res = await db.execute(text(
                "UPDATE email_thread_status SET status = 'DONE', "
                "classified_at = now() "
                "WHERE account_id = :aid AND thread_id = :tid"
            ), {"aid": req.account_id, "tid": req.thread_id})
            if res.rowcount == 0:
                # Heuristic mode (no stored status yet) — create one as DONE.
                lm = (await db.execute(text(
                    "SELECT id, received_at FROM email_messages "
                    "WHERE account_id = :aid AND thread_id = :tid "
                    "ORDER BY received_at DESC LIMIT 1"
                ), {"aid": req.account_id, "tid": req.thread_id})).fetchone()
                await db.execute(text(
                    "INSERT INTO email_thread_status (account_id, thread_id, "
                    "status, last_message_id, last_message_at, reason) "
                    "VALUES (:aid, :tid, 'DONE', :lmid, :lmat, 'Marked done') "
                    "ON CONFLICT (account_id, thread_id) "
                    "DO UPDATE SET status = 'DONE', classified_at = now()"
                ), {"aid": req.account_id, "tid": req.thread_id,
                    "lmid": lm.id if lm else None,
                    "lmat": lm.received_at if lm else None})
            # Marking the thread Done closes any OPEN task captured from it (the
            # commitment task, or an inbound capture on the same thread). The
            # helper only touches non-DONE tasks, so a task the forward path
            # already closed is left alone — no ping-pong.
            from gateway.routes.tasks.email_link import (
                propagate_thread_done_to_tasks)
            with contextlib.suppress(Exception):  # best-effort; thread is Done
                await propagate_thread_done_to_tasks(
                    db, user.email or "anonymous", req.account_id,
                    req.thread_id)
        else:
            # Reopen: re-derive from the latest message's folder. Resolve the
            # latest message directly (don't rely on last_message_id, which may
            # be NULL) so the UPDATE always lands.
            lm = (await db.execute(text(
                "SELECT folder FROM email_messages "
                "WHERE account_id = :aid AND thread_id = :tid "
                "ORDER BY received_at DESC NULLS LAST LIMIT 1"
            ), {"aid": req.account_id, "tid": req.thread_id})).fetchone()
            new_status = "AWAITING" if (
                lm and (lm.folder or "").lower() == "sent") else "NEEDS_REPLY"
            keep_label = (
                "Awaiting Reply" if new_status == "AWAITING" else "Needs Reply")
            await db.execute(text(
                "UPDATE email_thread_status SET status = :st, classified_at = now() "
                "WHERE account_id = :aid AND thread_id = :tid"
            ), {"st": new_status, "aid": req.account_id, "tid": req.thread_id})
        # Collapse the provider/local labels to match the new status (clears the
        # stale Reply / Awaiting / Follow-up that the status update alone left).
        background.add_task(
            _reconcile_labels_bg, req.account_id, req.thread_id, keep_label)
        return {"ok": True, "thread_id": req.thread_id, "done": req.done}


class ReplyZeroReclassifyRequest(BaseModel):
    account_id: str


@router.post("/reply-zero/reclassify")
async def reclassify_reply_zero(
    req: ReplyZeroReclassifyRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Rebuild Reply Zero from scratch with the current rules-based logic.

    Clears the derived statuses (Reply / Awaiting / FYI) — preserving threads
    you've marked Done — then DRAINS the whole mailbox through the rules engine
    (not a fixed handful of passes). Runs in the background; poll
    GET /email/reply-zero/reclassify/status for progress, or GET /email/reply-zero
    to see the rebuilt buckets."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    # One rebuild at a time per account: a second click while one is draining
    # would double the LLM spend and race on the same status rows.
    if _RECLASSIFY_JOBS.is_running(req.account_id):
        return {"scheduled": False, "already_running": True}
    # Seed the running row + mint the guard token SYNCHRONOUSLY, before the task
    # is scheduled, so an immediate status poll sees "running" rather than a gap.
    token = _RECLASSIFY_JOBS.start(
        req.account_id, status="running", total=0, remaining=0, processed=0,
        started_at=_dt_now_iso(), finished_at=None, error=None)
    background.add_task(
        _reclassify_reply_zero_job, req.account_id, token=token)
    return {"scheduled": True}


@router.get("/reply-zero/reclassify/status")
async def reclassify_reply_zero_status(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Progress of an in-flight (or the last) whole-mailbox reclassify: status,
    total threads to rebuild, how many remain, and how many are done."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
    job = _RECLASSIFY_JOBS.get(account_id)
    if not job:
        return {"status": "idle"}
    return {k: v for k, v in job.items() if k != "token"}


@router.get("/reply-zero")
async def reply_zero(
    background: BackgroundTasks,
    account_id: str = Query(...),
    type: str = Query("needs_reply"),  # needs_reply | awaiting | done
    limit: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(get_current_user),
):
    """Threads in a Reply Zero bucket, read straight from the stored,
    rules-derived status (``email_thread_status``).

    Reply Zero is a PROJECTION of the rules pipeline — a thread shows up only once
    a rule has classified it. There is deliberately NO inbox fallback (the old
    "show every inbox thread until the first pass runs" behaviour is what made
    every email appear under "Reply"). On a cold account with nothing
    classified yet we kick off a one-off background backfill so the next poll is
    populated; an existing draft for the thread is surfaced (``draft_id``) so the
    UI offers "View draft" instead of drafting a second reply."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        want = {"awaiting": "AWAITING", "done": "DONE"}.get(type, "NEEDS_REPLY")
        # Trash is hidden from every bucket; archiving a thread also drops it from
        # the ACTIVE buckets ("archived = off my reply queue") while it still shows
        # under Done. (Controlled literal — safe to inline.)
        excluded = ("'trash', 'archive'"
                    if want in ("NEEDS_REPLY", "AWAITING") else "'trash'")
        rows = (await db.execute(text(
            f"""SELECT ts.thread_id, ts.reason, ts.last_message_at,
                      em.id, em.subject, em.from_address, em.is_read,
                      d.id AS draft_id, d.body_text AS draft_text
               FROM email_thread_status ts
               JOIN email_messages em ON em.id = ts.last_message_id
               LEFT JOIN LATERAL (
                 SELECT id, body_text FROM email_messages dm
                 WHERE dm.account_id = ts.account_id
                   AND dm.thread_id = ts.thread_id
                   AND LOWER(COALESCE(dm.folder, '')) IN ('drafts', 'draft')
                 ORDER BY dm.updated_at DESC NULLS LAST, dm.received_at DESC
                 LIMIT 1
               ) d ON true
               WHERE ts.account_id = :aid AND ts.status = :st
                 -- Hide trashed (all buckets) / archived (active buckets) threads.
                 AND LOWER(COALESCE(em.folder, '')) NOT IN ({excluded})
               ORDER BY ts.last_message_at DESC NULLS LAST LIMIT :limit"""
        ), {"aid": account_id, "st": want, "limit": limit})).fetchall()

        # Cold start: nothing classified yet → schedule a one-off backfill so the
        # next poll fills in, instead of the old whole-inbox fallback.
        if not rows:
            has_any = (await db.execute(text(
                "SELECT 1 FROM email_thread_status WHERE account_id = :aid LIMIT 1"
            ), {"aid": account_id})).fetchone()
            if has_any is None:
                background.add_task(_maybe_classify_threads, account_id)

        fu_cutoff = None
        if type == "awaiting":
            fu_row = (await db.execute(text(
                "SELECT follow_up_awaiting_days, follow_up_days "
                "FROM email_assistant_settings WHERE account_id = :aid"
            ), {"aid": account_id})).fetchone()
            fu_days = 0
            if fu_row:
                fu_days = (getattr(fu_row, "follow_up_awaiting_days", 0)
                           or getattr(fu_row, "follow_up_days", 0) or 0)
            # Use the SAME business-day window as the reminder job so the badge
            # appears exactly when the "Follow-up" label / nudge fires — not a
            # day or two early because a weekend was counted.
            if fu_days > 0:
                fu_cutoff = _business_days_cutoff(float(fu_days))
        now = datetime.now(timezone.utc)
        out = []
        for r in rows:
            frm = _addr_dict(r.from_address)
            days = (now - r.last_message_at).days if r.last_message_at else None
            out.append({
                "thread_id": r.thread_id, "message_id": str(r.id),
                "subject": r.subject or "(no subject)",
                "from": frm.get("name") or frm.get("email", ""),
                "from_email": frm.get("email", ""),
                "received_at": (
                    r.last_message_at.isoformat() if r.last_message_at else None
                ),
                "is_read": r.is_read, "reason": r.reason or "",
                "awaiting_days": days,
                "needs_follow_up": bool(
                    fu_cutoff and r.last_message_at is not None
                    and r.last_message_at < fu_cutoff),
                # An existing draft in the thread (auto-drafted or saved) so the
                # UI shows "View draft" rather than drafting another reply.
                "draft_id": str(r.draft_id) if r.draft_id else None,
                "draft_preview": (r.draft_text or "") if r.draft_id else None,
            })
        return {"threads": out, "type": type}


