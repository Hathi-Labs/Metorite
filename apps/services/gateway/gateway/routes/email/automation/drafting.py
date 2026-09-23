"""Automation · AI drafting — reply generation via the MAF agent/LLM, writing-
style + learned-edit capture, and the draft/save endpoints."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timezone
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from gateway.routes.email.automation.assistant import (
    _account_models,
    _load_assistant_about,
)
from gateway.routes.email.core import (
    _assert_account_owner,
    _attachment_summaries,
    _fmt_addr_list,
    _get_db,
    _tenant_session,
    _llm_json,
    _log,
    _row_to_message,
    email_memory_scope,
    provider_session,
    router,
)
from gateway.routes.email.quoting import split_quoted_text
from gateway.routes.email.signature import (
    append_signature_text,
    strip_signature_text,
)
from gateway.routes.email.transport.send import (
    ArtifactAttachment,
    SendAttachment,
    load_artifact_attachments,
)
from pydantic import BaseModel
from sqlalchemy import text


async def _account_signature(db: Any, account_id: str) -> str:
    """The account's configured signature (HTML/plain, '' when unset) — for the
    draft-write paths that don't already hold it via _load_assistant_about."""
    try:
        row = (await db.execute(text(
            "SELECT signature FROM email_assistant_settings "
            "WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
        return (row.signature if row else "") or ""
    except Exception:  # noqa: BLE001 — a draft save must not fail on settings
        return ""


def _resolve_draft_attachments(
    attachments: list[SendAttachment] | None,
    artifacts: list[ArtifactAttachment] | None,
    user_email: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve base64 uploads + workspace artifact refs to the provider's
    ``[{filename, content, mime_type}]`` shape — the same resolution the /send
    path does, so a draft can carry attachments exactly like a direct send."""
    import base64 as _b64  # noqa: PLC0415
    out: list[dict[str, Any]] = []
    for a in attachments or []:
        try:
            out.append({
                "filename": a.filename,
                "mime_type": a.mime_type,
                "content": _b64.b64decode(a.content_b64),
            })
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"Invalid attachment encoding: {exc}") from exc
    out.extend(load_artifact_attachments(artifacts, user_email))
    return out


def _normalize_text(s: str) -> str:
    return " ".join((s or "").split()).lower()


# ── Drafting context budgets ─────────────────────────────────────────────────
# Generous UPPER BOUNDS only. The LLM layer (acompletion_with_fallback →
# fit_messages_to_context) already trims the prompt to the model's real context
# window (the draft model defaults to tier-powerful ≈ 200k input tokens), keeping
# a head + tail around a marker. So we pass the FULL incoming email + thread and
# let that layer fit — instead of pre-truncating here. The old tight caps (the
# message being replied to was cut to [:2000] chars) made a long email reach the
# model as just its opening lines, so drafts read "only the introductory lines
# came through" / "the communication appears to be truncated". These bounds are a
# safety net against pathological multi-MB bodies, not the normal limiter.
_DRAFT_BODY_MAX_CHARS = 100_000     # the message being replied to (~25k tokens)
_DRAFT_THREAD_MAX_CHARS = 60_000    # the whole prior thread, joined (~15k tokens)
_THREAD_MSG_MAX_CHARS = 12_000      # per earlier message in the thread
_THREAD_MSG_LIMIT = 20              # how many earlier messages to include


async def _fetch_thread_context(
    db: Any, account_id: str, thread_id: str,
    exclude_provider_msg_id: str = "", *, limit: int = _THREAD_MSG_LIMIT,
) -> str:
    """Earlier messages in the conversation (oldest → newest), formatted for the
    drafter so it can reply with full thread context — inbox-zero passes the
    whole thread to its reply AI; without this we'd only see the latest message.

    Excludes drafts and the message currently being replied to. Returns '' when
    there's no prior context (e.g. a brand-new single-message thread)."""
    if not thread_id:
        return ""
    try:
        # Keep the most RECENT `limit` messages (a long thread's trailing emails
        # carry the current ask/template), then present them oldest→newest. A bare
        # `ORDER BY received_at ASC LIMIT n` would instead drop the newest ones.
        rows = (await db.execute(text(
            """SELECT from_address, body_text, snippet, received_at,
                      provider_message_id, folder FROM (
                SELECT from_address, body_text, snippet, received_at,
                       provider_message_id, folder
                FROM email_messages
                WHERE account_id = :aid AND thread_id = :tid
                  AND LOWER(COALESCE(folder, '')) NOT IN ('drafts', 'draft')
                ORDER BY received_at DESC NULLS LAST
                LIMIT :lim
              ) t ORDER BY received_at ASC NULLS FIRST"""
        ), {"aid": account_id, "tid": thread_id, "lim": limit})).fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.thread_context_failed", error=str(exc)[:160])
        return ""
    parts: list[str] = []
    for r in rows:
        if exclude_provider_msg_id and r.provider_message_id == exclude_provider_msg_id:
            continue
        body = (r.body_text or r.snippet or "").strip()
        if not body:
            continue
        frm = r.from_address if isinstance(r.from_address, dict) \
            else json.loads(r.from_address or "{}")
        sender = frm.get("name") or frm.get("email") or "?"
        when = r.received_at.isoformat() if hasattr(r.received_at, "isoformat") else ""
        header = f"From: {sender}" + (f" · {when}" if when else "")
        parts.append(f"{header}\n{body[:_THREAD_MSG_MAX_CHARS]}")
    return "\n\n---\n\n".join(parts)


async def _fetch_sender_reply_examples(
    db: Any, account_id: str, sender_email: str, *, limit: int = 3,
) -> str:
    """The owner's recent replies SENT to this sender — past examples the drafter
    can mirror for tone, brevity and relationship (inbox-zero's
    <sender_reply_examples>). Empty when there's no sent history to them."""
    sender_email = (sender_email or "").strip().lower()
    if not sender_email:
        return ""
    try:
        rows = (await db.execute(text(
            """SELECT body_text, snippet
               FROM email_messages
               WHERE account_id = :aid AND LOWER(COALESCE(folder, '')) = 'sent'
                 AND LOWER(to_addresses::text) LIKE :pat
               ORDER BY received_at DESC NULLS LAST
               LIMIT :lim"""
        ), {"aid": account_id, "pat": f"%{sender_email}%", "lim": limit})).fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.sender_examples_failed", error=str(exc)[:160])
        return ""
    parts: list[str] = []
    for r in rows:
        # Strip the quoted chain: a sent example is meant to show the owner's OWN
        # tone, not the correspondent's prose the reply quoted underneath.
        body = split_quoted_text((r.body_text or r.snippet or "").strip())[0]
        body = body.strip()
        if body:
            parts.append(body[:800])
    return "\n\n---\n\n".join(parts)


async def _fetch_sent_fewshot(
    db: Any, account_id: str, subject: str | None, body: str | None, *,
    exclude_thread_id: str | None = None, limit: int = 3,
) -> str:
    """The account's OWN voice on SIMILAR messages — the "draft in my voice"
    few-shot. Embeds what's being replied to, cosine-matches the account's Sent
    mail via ``email_embeddings`` (pgvector), and returns the top quote-stripped
    bodies. Distinct from ``sender_examples`` (past replies to THIS sender, which
    show the relationship): these show the REGISTER for this kind of message,
    from any recipient.

    Returns "" when semantic search is off (``embed_query`` → None → no LLM cost)
    or nothing is embedded yet, so the drafter just falls back to its other
    context. The thread being replied to is excluded so it can't echo itself."""
    try:
        from email_ingestion.email_embeddings import (  # noqa: PLC0415
            _embed_text,
            embed_query,
        )
        qvec = await embed_query(_embed_text(subject, body))
        if qvec is None:
            return ""
        qlit = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"
        rows = (await db.execute(text(
            """SELECT em.body_text, em.snippet
               FROM email_embeddings ee
               JOIN email_messages em ON em.id = ee.message_id
               WHERE ee.account_id = :aid
                 AND LOWER(COALESCE(em.folder, '')) = 'sent'
                 AND (:extid = '' OR COALESCE(em.thread_id, '') <> :extid)
               ORDER BY ee.embedding <=> CAST(:qv AS vector) ASC
               LIMIT :lim"""
        ), {"aid": account_id, "qv": qlit,
            "extid": exclude_thread_id or "", "lim": limit})).fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.sent_fewshot_failed", error=str(exc)[:160])
        return ""
    parts: list[str] = []
    for r in rows:
        b = split_quoted_text((r.body_text or r.snippet or "").strip())[0].strip()
        if b:
            parts.append(b[:800])
    return "\n\n---\n\n".join(parts)


# Phrases that mark an email as ASKING about timing — cheap heuristic so we only
# pull the calendar (and spend prompt budget on it) when a reply might need to
# talk scheduling. Word-ish substrings on the lowercased subject+body.
_SCHEDULING_CUES = (
    "meeting", "schedule", "reschedule", "availability", "available",
    "calendar", "appointment", "when are you", "what time", "free on",
    "work for you", "works for you", "set up a time", "set up a call",
    "book a", "find a time", "catch up", "hop on a call", "jump on a call",
    "zoom", "google meet", "ms teams", "let's meet", "let us meet",
    "your availability", "propose a time", "pick a time", "grab time",
)


def _asks_about_scheduling(subject: str | None, body: str | None) -> bool:
    """True when the incoming message reads like it's asking about timing, so the
    drafter should have the calendar in view. Deliberately a keyword heuristic:
    an LLM check per draft would cost a call for a signal this cheap to spot."""
    hay = f"{subject or ''} {body or ''}".lower()
    return any(cue in hay for cue in _SCHEDULING_CUES)


async def _fetch_calendar_context(
    db: Any, account_id: str, *, days: int = 10, limit: int = 15,
) -> str:
    """The owner's upcoming hard-date commitments (next ``days``), so a reply
    about timing can offer slots that don't clash and never double-books. Reads
    the internal calendar — open tasks with a due_at + is_hard_date, the same
    "shows on the Calendar" predicate the tasks app uses — for the account's
    user, through the ONE task seam (``item_source().hard_dated_items``, WS-39
    S8c). Best-effort: the tasks feature may be absent; a draft must not fail
    on it. Empty string when nothing is scheduled in the window."""
    from gateway.routes.tasks.item_source import item_source

    try:
        owner = (await db.execute(text(
            "SELECT user_id FROM email_accounts WHERE id = :aid"
        ), {"aid": account_id})).fetchone()
        uid = str(getattr(owner, "user_id", "") or "") if owner else ""
        if not uid:
            return ""
        items = await item_source().hard_dated_items(
            db, uid, days=days, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.calendar_context_failed", error=str(exc)[:160])
        return ""
    lines = []
    for it in items:
        due = it.due_at
        if isinstance(due, datetime):
            if due.tzinfo is not None:
                due = due.astimezone(UTC)
            # 'Dy Mon DD, HH24:MI', as Postgres `to_char` wrote it.
            whn = due.strftime("%a %b %d, %H:%M")
        else:
            whn = str(due or "")
        lines.append(f"- {whn} — {(it.title or '(untitled)')}")
    return "\n".join(lines)


async def _fetch_reply_memories(
    db: Any, account_id: str, email: dict[str, str], *, limit: int = 6,
) -> str:
    """Scope-matched reply memories (SENDER/DOMAIN/TOPIC) for the email being
    drafted — inbox-zero's <reply_memories>, prioritised SENDER > DOMAIN > TOPIC.
    GLOBAL memories are injected separately via the enriched `about`."""
    sender = (email.get("from") or "").strip().lower()
    domain = sender.split("@")[-1] if "@" in sender else ""
    content = f"{email.get('subject', '')} {email.get('body', '')}".lower()[:2000]
    try:
        rows = (await db.execute(text(
            """SELECT pattern, kind, scope_type,
                      CASE scope_type WHEN 'SENDER' THEN 3 WHEN 'DOMAIN' THEN 2
                                      WHEN 'TOPIC' THEN 1 ELSE 0 END AS prio
               FROM email_learned_patterns
               WHERE account_id = :aid AND (
                     (scope_type = 'SENDER' AND scope_value = :sender)
                  OR (scope_type = 'DOMAIN' AND scope_value = :domain
                      AND :domain <> '')
                  OR (scope_type = 'TOPIC' AND scope_value <> ''
                      AND :content LIKE '%' || scope_value || '%')
               )
               ORDER BY prio DESC, weight DESC, updated_at DESC
               LIMIT :lim"""
        ), {"aid": account_id, "sender": sender, "domain": domain,
            "content": content, "lim": limit})).fetchall()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.reply_memories_failed", error=str(exc)[:160])
        return ""
    return "\n".join(f"- [{r.kind}/{r.scope_type}] {r.pattern}" for r in rows)


async def _cleanup_thread_drafts(account_id: str, thread_id: str) -> None:
    """Trash any drafts left in a thread (upstream + local) after a reply is sent
    — e.g. an AI DRAFT_EMAIL draft a rule created (which the user never sent
    because they composed their own reply), or a Gmail-style auto-save that wasn't
    the one consumed by the send. Best-effort background task."""
    if not thread_id:
        return
    # H4: background consumer — _cleanup_thread_drafts runs as a post-response
    # BackgroundTask (send paths); no ambient tenant to inherit.
    db = await _get_db()
    try:
        rows = (await db.execute(text(
            "SELECT id, provider_message_id FROM email_messages "
            "WHERE account_id = :aid AND thread_id = :tid "
            "AND LOWER(COALESCE(folder, '')) IN ('drafts', 'draft')"
        ), {"aid": account_id, "tid": thread_id})).fetchall()
        if not rows:
            return
        # Unscoped session: background task, no request user. Missing account
        # raises 404 → caught by the broad handler below.
        async with provider_session(
            db, None, account_id=account_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                return
            for r in rows:
                try:
                    await sess.provider.trash_message(r.provider_message_id)
                except Exception:  # noqa: BLE001 — one stuck draft: no abort
                    pass
                await db.execute(text(
                    "DELETE FROM email_messages WHERE id = :id"), {"id": r.id})
        await db.commit()
        _log.info("email.thread_drafts_cleaned",
                  account_id=account_id, count=len(rows))
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.cleanup_thread_drafts_failed", error=str(exc)[:160])
    finally:
        await db.close()


async def _resolve_existing_thread_draft(
    db: Any, provider: Any, account_id: str, thread_id: str,
) -> str:
    """Decide what to do about an existing AI draft before auto-drafting again —
    a port of inbox-zero's handlePreviousDraftDeletion (at most ONE AI draft per
    thread; never clobber the user's edits, never pile up duplicates). Returns:

      "none"    — no draft in the thread → create one.
      "replace" — a prior AI draft existed UNMODIFIED; trashed it → create fresh
                  (so it picks up the latest thread context).
      "keep"    — the user edited the draft (or there's no original to compare
                  against) → preserve it; the caller must NOT create another.

    Unmodified is judged by comparing the live draft mirror against the original
    AI text stored in ``email_ai_drafts`` (the same sync that delivers the new
    inbound message also refreshes the draft mirror, so it is current). Best-effort
    — returns "none" on any error so drafting still proceeds."""
    if not thread_id or not account_id:
        return "none"
    try:
        row = (await db.execute(text(
            "SELECT id, provider_message_id, body_text FROM email_messages "
            "WHERE account_id = :aid AND thread_id = :tid "
            "AND LOWER(COALESCE(folder, '')) IN ('drafts', 'draft') "
            "ORDER BY updated_at DESC NULLS LAST, received_at DESC LIMIT 1"
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        if not row:
            return "none"
        orig = (await db.execute(text(
            "SELECT draft_text FROM email_ai_drafts "
            "WHERE account_id = :aid AND thread_id = :tid"
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        original = (orig.draft_text if orig else "") or ""
        if original and _normalize_text(row.body_text or "") == \
                _normalize_text(original):
            try:
                await provider.trash_message(row.provider_message_id)
            except Exception:  # noqa: BLE001 — a stuck draft shouldn't abort
                pass
            await db.execute(text(
                "DELETE FROM email_messages WHERE id = :id"), {"id": row.id})
            return "replace"
        return "keep"  # user-edited (or unknown) → preserve, don't duplicate
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.resolve_existing_draft_failed", error=str(exc)[:160])
        return "none"


async def _store_ai_draft(
    db: Any, account_id: str, thread_id: str, draft_text: str,
    *, commit: bool = True,
) -> None:
    """Remember the assistant's original draft for a thread, so we can later
    learn from how the user edits it before sending.

    ``commit=False`` is for callers holding a ``_tenant_session`` (H2): the
    wrapper commits on clean exit, and a mid-block commit would end that
    transaction and drop the tenant GUC. Background callers on their own
    `_get_db` session keep the default and commit here as before."""
    if not account_id or not thread_id or not (draft_text or "").strip():
        return
    try:
        await db.execute(text(
            """INSERT INTO email_ai_drafts (account_id, thread_id, draft_text)
               VALUES (:aid, :tid, :txt)
               ON CONFLICT (account_id, thread_id) DO UPDATE SET
                 draft_text = EXCLUDED.draft_text, created_at = now()"""
        ), {"aid": account_id, "tid": thread_id, "txt": draft_text})
        if commit:
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.store_ai_draft_failed", error=str(exc)[:160])


_PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "mail.com", "msn.com",
})


async def _llm_extract_reply_memories(
    incoming: str, draft: str, sent: str,
) -> list[dict[str, str]]:
    """Extract 0-3 durable, reusable reply memories from how the user changed the
    assistant's draft, each tagged with a KIND (FACT/PROCEDURE/PREFERENCE) and a
    SCOPE (SENDER/DOMAIN/TOPIC/GLOBAL) — inbox-zero's scoped reply memories.

    Returns [{content, kind, scope, topic}]; empty when nothing generalizable.
    """
    try:
        sys_prompt = (
            "You learn how a user likes their email replies written by comparing "
            "the assistant's DRAFT with what the user actually SENT (the INCOMING "
            "email is given for context). Extract 0-3 DURABLE, REUSABLE memories; "
            "ignore one-off facts specific to THIS email. For each, give:\n"
            "- content: a short instruction or fact (e.g. 'Keep sign-offs to my "
            "first name', 'I am the CTO', 'Attach the pricing PDF for demos').\n"
            "- kind: FACT (a stable fact about the user/their work), PROCEDURE "
            "(a how-to/step they follow), or PREFERENCE (tone/length/style).\n"
            "- scope: SENDER (specific to this correspondent), DOMAIN (their "
            "company), TOPIC (a recurring subject), or GLOBAL (all replies).\n"
            "- topic: a 1-3 word keyword, ONLY when scope is TOPIC.\n"
            'Respond with ONLY JSON: {"memories": [{"content","kind","scope",'
            '"topic"}]} — an empty list if nothing is generalizable.'
        )
        user = (
            f"INCOMING:\n{incoming[:1500]}\n\nDRAFT:\n{draft[:2000]}\n\n"
            f"SENT:\n{sent[:2000]}"
        )
        data, _content, _used = await _llm_json(
            "tier-powerful",
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user}],
            max_tokens=1000,
        )
        items = data.get("memories") if isinstance(data, dict) else None
        out: list[dict[str, str]] = []
        for m in (items or [])[:3]:
            if not isinstance(m, dict):
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            kind = (m.get("kind") or "PREFERENCE").upper()
            if kind not in ("FACT", "PROCEDURE", "PREFERENCE"):
                kind = "PREFERENCE"
            scope = (m.get("scope") or "GLOBAL").upper()
            if scope not in ("SENDER", "DOMAIN", "TOPIC", "GLOBAL"):
                scope = "GLOBAL"
            out.append({
                "content": content[:240], "kind": kind, "scope": scope,
                "topic": (m.get("topic") or "").strip().lower()[:60],
            })
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.extract_memories_failed", error=str(exc)[:160])
        return []


def _resolve_memory_scope(
    scope: str, topic: str, sender_email: str, domain: str,
) -> tuple[str, str]:
    """Map an extracted scope to a concrete (scope_type, scope_value), demoting
    scopes that have no usable target (e.g. a public mailbox domain) to SENDER or
    GLOBAL so the memory stays retrievable."""
    if scope == "SENDER":
        return ("SENDER", sender_email) if sender_email else ("GLOBAL", "")
    if scope == "DOMAIN":
        if domain and domain not in _PUBLIC_EMAIL_DOMAINS:
            return ("DOMAIN", domain)
        return ("SENDER", sender_email) if sender_email else ("GLOBAL", "")
    if scope == "TOPIC":
        return ("TOPIC", topic) if topic else ("GLOBAL", "")
    return ("GLOBAL", "")


async def _learn_from_sent(account_id: str, thread_id: str, sent_text: str) -> None:
    """Background: if the user edited the assistant's draft for this thread,
    extract scoped reply memories (sender/domain/topic/global) and store them
    (best-effort)."""
    if not thread_id or not (sent_text or "").strip():
        return
    # The composer sends new-text + the quoted chain concatenated. The stored AI
    # draft is the new text only, so comparing the two verbatim NEVER matched —
    # every send looked "edited" and fired a wasted extraction — and the quoted
    # correspondent prose leaked into the learned preferences. Strip the quote so
    # both the unchanged-check and the extraction see only what the user wrote.
    sent_text = split_quoted_text(sent_text)[0]
    # H4: background consumer — _learn_from_sent runs as a post-response
    # BackgroundTask after a send; no ambient tenant to inherit.
    db = await _get_db()
    try:
        # The signature now rides IN draft bodies, but the /send path's body may
        # or may not carry it depending on where the send started. Strip it from
        # BOTH sides so the unchanged-check diffs what the user wrote — never
        # the boilerplate — and the signature never leaks into learned prefs.
        sig = await _account_signature(db, account_id)
        sent_text = strip_signature_text(sig, sent_text)
        row = (await db.execute(text(
            "SELECT draft_text FROM email_ai_drafts "
            "WHERE account_id = :aid AND thread_id = :tid"
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        if not row:
            return
        original = strip_signature_text(sig, row.draft_text or "")
        await db.execute(text(
            "DELETE FROM email_ai_drafts "
            "WHERE account_id = :aid AND thread_id = :tid"
        ), {"aid": account_id, "tid": thread_id})
        await db.commit()
        if not original.strip() or \
                _normalize_text(original) == _normalize_text(sent_text):
            return  # unchanged → nothing to learn

        # Sender + incoming content, to scope the extracted memories.
        inb = (await db.execute(text(
            """SELECT from_address, body_text, snippet FROM email_messages
               WHERE account_id = :aid AND thread_id = :tid
                 AND LOWER(COALESCE(folder, '')) <> 'sent'
               ORDER BY received_at DESC NULLS LAST LIMIT 1"""
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        sender_email, incoming = "", ""
        if inb:
            frm = inb.from_address if isinstance(inb.from_address, dict) \
                else json.loads(inb.from_address or "{}")
            sender_email = (frm.get("email") or "").strip().lower()
            incoming = inb.body_text or inb.snippet or ""
        domain = sender_email.split("@")[-1] if "@" in sender_email else ""

        memories = await _llm_extract_reply_memories(incoming, original, sent_text)
        if not memories:
            return
        stored: list[str] = []
        for m in memories:
            scope_type, scope_value = _resolve_memory_scope(
                m["scope"], m["topic"], sender_email, domain)
            await db.execute(text(
                """INSERT INTO email_learned_patterns
                     (account_id, pattern, kind, scope_type, scope_value,
                      is_style_evidence)
                   VALUES (:aid, :p, :k, :st, :sv, :ev)
                   ON CONFLICT (account_id, kind, scope_type, scope_value, pattern)
                   DO UPDATE SET weight = email_learned_patterns.weight + 1,
                                 updated_at = now()"""
            ), {"aid": account_id, "p": m["content"], "k": m["kind"],
                "st": scope_type, "sv": scope_value,
                "ev": m["kind"] == "PREFERENCE"})
            stored.append(m["content"])
        await db.commit()
        _log.info("email.learned_memories", account_id=account_id,
                  count=len(stored))
        # Also remember the strongest memory in Mem0 — scoped to THIS account
        # (email_memory_scope) so future drafting retrieval on this inbox sees it
        # and other inboxes don't. Surfaces during the drafter's remember() pass.
        try:
            urow = (await db.execute(text(
                "SELECT user_id FROM email_accounts WHERE id = :aid"
            ), {"aid": account_id})).fetchone()
            uid = (urow.user_id if urow else None) or "default"
            from acb_memory import add_memories_background  # noqa: PLC0415
            await add_memories_background(
                email_memory_scope(uid, account_id),
                [{"role": "assistant",
                  "content": f"Email reply preference: {stored[0]}"}],
                agent_id="email",
            )
        except Exception:  # noqa: BLE001
            pass
        # Regenerate the learned writing style once enough new evidence accrued.
        await _maybe_refresh_learned_style(db, account_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.learn_from_sent_failed", error=str(exc)[:160])
    finally:
        await db.close()


_MIN_STYLE_EVIDENCE = 5
_STYLE_REFRESH_EVERY = 5


async def _llm_summarize_writing_style(prefs: list[str]) -> str:
    """Distil accumulated reply preferences into a concise writing-style guide
    (inbox-zero's aiSummarizeLearnedWritingStyle)."""
    if not prefs:
        return ""
    try:
        from acb_llm.context import acompletion_with_fallback  # noqa: PLC0415
        sys_prompt = (
            "You distil a user's email writing style from preferences observed in "
            "how they edit the assistant's drafts. Output 3-6 short, concrete "
            "bullet guidelines (tone, length, greeting/sign-off, formatting, what "
            "to include or omit). No preamble — just the bullet lines."
        )
        resp, _ = await acompletion_with_fallback(
            model="tier-powerful",
            messages=[{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": "Preferences:\n"
                       + "\n".join(f"- {p}" for p in prefs[:25])}],
            temperature=0.2, max_tokens=1000,
        )
        return (resp.choices[0].message.content or "").strip()[:1500]
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.summarize_style_failed", error=str(exc)[:160])
        return ""


async def _maybe_refresh_learned_style(db: Any, account_id: str) -> None:
    """Regenerate the learned writing style once enough new PREFERENCE evidence
    has accumulated since the last refresh (inbox-zero parity)."""
    try:
        cnt = (await db.execute(text(
            "SELECT COUNT(*) AS n FROM email_learned_patterns "
            "WHERE account_id = :aid AND is_style_evidence = true"
        ), {"aid": account_id})).fetchone()
        cur = int(cnt.n) if cnt else 0
        if cur == 0:
            return
        srow = (await db.execute(text(
            "SELECT learned_writing_style, learned_style_evidence_count "
            "FROM email_assistant_settings WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
        have_style = bool(srow and (srow.learned_writing_style or "").strip())
        last = int(getattr(srow, "learned_style_evidence_count", 0) or 0) if srow else 0
        need = ((not have_style and cur >= _MIN_STYLE_EVIDENCE)
                or (cur - last >= _STYLE_REFRESH_EVERY))
        if not need:
            return
        prefs = [r.pattern for r in (await db.execute(text(
            "SELECT pattern FROM email_learned_patterns "
            "WHERE account_id = :aid AND is_style_evidence = true "
            "ORDER BY weight DESC, updated_at DESC LIMIT 25"
        ), {"aid": account_id})).fetchall() if r.pattern]
        style = await _llm_summarize_writing_style(prefs)
        if not style:
            return
        await db.execute(text(
            "UPDATE email_assistant_settings SET learned_writing_style = :s, "
            "learned_style_evidence_count = :c, updated_at = now() "
            "WHERE account_id = :aid"
        ), {"s": style, "c": cur, "aid": account_id})
        await db.commit()
        _log.info("email.learned_style_refreshed",
                  account_id=account_id, evidence=cur)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.refresh_style_failed", error=str(exc)[:160])


def _draft_direction_note(email: dict[str, str], to_line: str, cc_line: str) -> str:
    """A one-line steer on the reply's DIRECTION (internal colleague vs external
    party) and the owner's RECIPIENT ROLE (a direct To recipient vs merely Cc'd).

    Both signals are already computed for the classifier (sender_scope + To/Cc);
    the drafter was dropping them, so a reply to an internal colleague read the
    same as one to an external customer, and a mail the owner was only Cc'd on
    still got a full draft. Surfacing them keeps the tone appropriate and
    discourages a needless reply. Returns "" when there's no signal."""
    bits: list[str] = []
    scope = (email.get("sender_scope") or "").strip().lower()
    if scope == "internal":
        bits.append("This is INTERNAL mail (the owner's own organisation / a "
                    "colleague) — keep the tone collegial.")
    elif scope and scope != "self":
        bits.append("This is EXTERNAL mail (an outside party — customer, vendor "
                    "or similar) — keep it professional.")
    self_addr = (email.get("self") or "").strip().lower()
    if (self_addr and self_addr in cc_line.lower()
            and self_addr not in to_line.lower()):
        bits.append("The owner was only CC'd (not a direct To recipient), so this "
                    "is likely informational — reply only if a response is "
                    "genuinely warranted from them; otherwise keep it brief.")
    return (" ".join(bits) + "\n") if bits else ""


async def _llm_draft_reply(
    email: dict[str, str], about: str, signature: str,
    instructions: str = "", context: str = "", user_email: str = "",
    *, model: str = "tier-powerful", interactive_fallback: bool = False,
    on_delta: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
    """Draft a reply body with the LLM, using the user's About context plus any
    extra `context` gathered from memory / specialist agents.

    Runs on the account's draft-writing ``model`` (default the powerful tier)
    with the prompt fitted to its context window. A confidence gate may make the
    model return the NO_DRAFT sentinel, which is propagated to the caller.

    On LLM failure the behaviour depends on ``interactive_fallback``:
      * False (default — automation paths): return the NO_DRAFT sentinel so the
        rule DRAFT_EMAIL action / follow-up nudge simply skips. An outage must
        NOT auto-file a generic "I'll get back to you" draft into the mailbox
        and then feed that boilerplate into the edit-learning loop.
      * True (interactive paths — the compose box's "Draft with AI"): return a
        neutral human-editable template so the user who clicked the button has a
        starting point rather than a silent no-op.
    """
    try:
        from acb_llm.context import acompletion_with_fallback  # noqa: PLC0415
        # A configured signature is appended after the body, so the model must not
        # add its own closing/sign-off (mirrors inbox-zero's drafter).
        sig_rule = (
            "Do NOT add any closing, sign-off, name, title, or signature block — a "
            "signature is appended automatically."
            if (signature or "").strip()
            else "You do not need to sign off with the user's name; a simple close "
            "(e.g. 'Best regards,') is fine, but NEVER invent a name or leave a "
            "placeholder."
        )
        sys_prompt = (
            "You are an expert assistant that drafts email replies on behalf of "
            "the account owner, replying to the person who sent the email below. "
            "Rules:\n"
            "- Write a properly-formatted email reply. START with a greeting on "
            "its own line that addresses the RECIPIENT by name — 'Dear <name>,' "
            "for a formal thread or 'Hi <first name>,' for a casual one (match "
            "the thread's tone; use a polite 'Hello,' if no name is known). Then "
            "a blank line, then the message in clear, short paragraphs. No "
            "subject line, and NEVER narrate what you are doing (no \"here is a "
            "draft you can use\" or apologies).\n"
            "- Output ONLY the new reply text. NEVER quote the earlier "
            "conversation back: no quote header (\"On <date>, X wrote:\"), no "
            "\">\"-prefixed lines, no \"From:/Sent:/To:\" header block, no "
            "\"----- Original Message -----\". The mail client attaches the "
            "original thread underneath on its own — anything you quote is a "
            "duplicate, and it pushes the signature below the thread.\n"
            "- You are the account owner writing the reply; you are NOT the "
            "sender. Greet and address the RECIPIENT (the sender of the email "
            "below) — never greet or address the account owner by name.\n"
            "- Do not mention you are an AI or reference these instructions.\n"
            "- Do not simply repeat the sender's content back — respond to it.\n"
            "- Plain text only (markdown links allowed); separate paragraphs with "
            "blank lines; match the language of the email; be clear and "
            "professional, not a single terse line.\n"
            "- Ground every fact in the email or the supplied context and never "
            "invent specifics — if something is missing, keep it open or ask.\n"
            "- NEVER comment on the incoming email's formatting or completeness. "
            "If it appears to end abruptly or mid-sentence, do NOT say it 'seems "
            "cut off', do NOT ask them to 'finish that' or resend, and do NOT "
            "quote the fragment back. Just reply naturally to the substance you "
            "can see; if a needed detail is genuinely absent, ask for that detail "
            "by name (e.g. 'what's your availability this week?') without "
            "referencing any truncation.\n"
            "- Never use placeholders for names (e.g. [Your Name], [Name]). "
            f"{sig_rule}\n"
            "- If the context contains <personal_instructions>, follow them. If "
            "it contains <writing_style>, match that tone, length, and phrasing. "
            "If it contains <voice_profile>, write in that voice — it was built "
            "from the user's own past emails (an explicit <writing_style> "
            "outranks <voice_profile>, which outranks <learned_writing_style>, "
            "auto-derived and advisory). If it contains a <knowledge_base>, "
            "use it for facts only where relevant. If it contains "
            "<learned_patterns> or <reply_memories>, treat them as advisory "
            "preferences/facts from the user's past edits and apply the ones that "
            "fit."
        )
        owner = f"You are drafting as: {user_email}\n" if user_email else ""
        ctx = f"{owner}User context:\n{about}\n\n" if (about or owner) else ""
        if context:
            ctx += f"Context gathered for this reply:\n{context}\n\n"
        thread = (email.get("thread") or "").strip()
        thread_block = (
            "Earlier in this thread (oldest to newest) — read it for full "
            f"context before replying:\n{thread[:_DRAFT_THREAD_MAX_CHARS]}\n\n"
            if thread else ""
        )
        examples = (email.get("sender_examples") or "").strip()
        examples_block = (
            "Past replies you (the owner) have sent to this sender — mirror their "
            "tone, brevity and directness; do NOT reuse their specific facts:\n"
            f"{examples[:2500]}\n\n" if examples else ""
        )
        # "Draft in my voice": the owner's own Sent mail on semantically similar
        # topics (any recipient). Shows register/phrasing for THIS kind of
        # message; sender_examples above shows the relationship. Empty when
        # semantic search is off or nothing is embedded yet.
        voice = (email.get("sent_examples") or "").strip()
        voice_block = (
            "Examples of how you (the owner) write about similar things — match "
            "this voice, phrasing and length; do NOT reuse their specific "
            f"facts:\n{voice[:2500]}\n\n" if voice else ""
        )
        # Calendar — present only when the incoming mail asked about timing.
        cal = (email.get("calendar") or "").strip()
        calendar_block = (
            "Your calendar for the days ahead (local time). If the sender is "
            "asking about timing, offer times that do NOT clash with these and "
            "never double-book; if they proposed a time that clashes, say so and "
            f"suggest an alternative:\n{cal}\n\n" if cal else ""
        )
        memories = (email.get("reply_memories") or "").strip()
        memories_block = (
            "Learned reply memories relevant to this sender/topic (advisory — "
            "apply the ones that fit; explicit instructions still win):\n"
            f"{memories[:2000]}\n\n" if memories else ""
        )
        # Recipients of the email being replied to — so the drafter knows who
        # else was addressed (To vs Cc) and can word a reply-all appropriately.
        to_line = (email.get("to") or "").strip()
        cc_line = (email.get("cc") or "").strip()
        recip_block = ""
        if to_line or cc_line:
            recip_block = f"Original recipients — To: {to_line or '(unknown)'}"
            if cc_line:
                recip_block += f"; Cc: {cc_line}"
            recip_block += "\n"
        # Direction (internal/external) + the owner's recipient role (To vs Cc).
        direction_block = _draft_direction_note(email, to_line, cc_line)
        # Attachment metadata on the message being replied to (already formatted
        # as "Attachments: file.pdf (…)") so the reply can acknowledge them.
        attach_block = ""
        attach = (email.get("attachments") or "").strip()
        if attach:
            attach_block = f"{attach}\n"
        today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
        user_prompt = (
            f"{ctx}Today is {today}.\n"
            "Draft a reply to this email (it was sent TO the account owner; "
            "write the owner's reply back to the sender).\n"
            "Reply to — greet this recipient by name: "
            f"{email.get('from_name') or email.get('from', '')} "
            f"<{email.get('from', '')}>\n"
            f"{recip_block}"
            f"{direction_block}"
            f"Subject: {email.get('subject', '')}\n"
            f"{attach_block}\n"
            f"{memories_block}"
            f"{examples_block}"
            f"{voice_block}"
            f"{calendar_block}"
            f"{thread_block}"
            "Latest message — reply to THIS, taking the thread above into "
            f"account:\n{(email.get('body', '') or '')[:_DRAFT_BODY_MAX_CHARS]}\n"
        )
        if instructions:
            user_prompt += f"\nExtra instructions: {instructions}\n"
        _messages = [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_prompt}]
        # Generous output budget — a full reply body (greeting + paragraphs +
        # context) must never be truncated mid-sentence.
        if on_delta is not None:
            # Streaming path (SSE compose): live deltas reach the composer;
            # the cleaned final body below still wins over the preview.
            from acb_llm.context import acompletion_stream_text  # noqa: PLC0415
            raw, _used = await acompletion_stream_text(
                model=model,
                messages=_messages, temperature=0.3, max_tokens=3000,
                on_delta=on_delta,
            )
        else:
            resp, _used = await acompletion_with_fallback(
                model=model,
                messages=_messages, temperature=0.3, max_tokens=3000,
            )
            raw = resp.choices[0].message.content or ""
        body = _clean_draft_body(raw.strip())
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.llm_draft_failed", error=str(exc)[:200],
                     interactive=interactive_fallback)
        if interactive_fallback:
            body = ("Hi,\n\nThanks for your email — I'll review this and get "
                    "back to you shortly.")
        else:
            # Automation path: the sentinel makes the caller skip. Never file a
            # boilerplate draft the user didn't write (and never teach the
            # edit-learner from it) just because the model was down.
            body = DRAFT_NO_DRAFT_SENTINEL
    # Confidence gate: when the drafter declined (or returned nothing), return the
    # CANONICAL sentinel with no signature, so every consumer detects it uniformly
    # via _is_no_draft and exact-equality history logging stays consistent.
    if _is_no_draft(body):
        return DRAFT_NO_DRAFT_SENTINEL
    # Salutation guarantee: the prompt asks for a greeting, but a model that
    # skips it ships an abrupt, informal-reading draft — so enforce it here.
    body = _ensure_salutation(body, email.get("from_name") or "")
    # The signature is NOT appended here — each draft-write/send path appends
    # it once, deterministically (signature.append_signature_text /
    # build_signed_bodies), so the model can never double or mangle it.
    return body


async def _llm_compose_assist(
    *, about: str, signature: str, current_body: str, instruction: str,
    mode: str, recipient: str = "", subject: str = "", thread: str = "",
    reply_to_body: str = "", user_email: str = "", model: str = "tier-powerful",
    on_delta: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
    """Draft OR improve an outgoing email body for the compose box.

    Unlike ``_llm_draft_reply`` (which always writes a fresh reply to an inbound
    message), this works on the OWNER'S OWN text: when ``current_body`` is set it
    polishes that draft in place; when it's empty it drafts from the instruction
    and context. The trailing quoted conversation is NEVER passed in (the client
    strips it) — ``thread`` is supplied for context only and must not be quoted.

    Returns the NO_DRAFT sentinel if the model declines; otherwise the body with
    the configured signature appended.
    """
    improving = bool((current_body or "").strip())
    try:
        from acb_llm.context import acompletion_with_fallback  # noqa: PLC0415
        sig_rule = (
            "Do NOT add any closing, sign-off, name, title, or signature block — a "
            "signature is appended automatically."
            if (signature or "").strip()
            else "You do not need to sign off with the user's name; a simple close "
            "(e.g. 'Best regards,') is fine, but NEVER invent a name or leave a "
            "placeholder."
        )
        mode_rule = (
            "- IMPROVE the owner's existing draft (given below): preserve their "
            "intent, facts, names, commitments and any concrete details; fix "
            "clarity, structure, grammar and tone. Do NOT invent new facts, add "
            "new asks, or change the meaning.\n"
            if improving
            else "- DRAFT a new email body from the owner's instruction and "
            "context. START with a greeting on its own line that addresses the "
            "RECIPIENT by name — 'Dear <name>,' for a formal email or 'Hi "
            "<first name>,' for a casual one (a polite 'Hello,' if no name is "
            "known) — then a blank line, then the message. Ground every fact in "
            "the instruction/context; never invent "
            "specifics — if something is missing, keep it open or ask.\n"
        )
        sys_prompt = (
            "You help the account owner write and refine THEIR OWN outgoing email. "
            "Rules:\n"
            "- Output ONLY the email body the owner will send — no subject line, no "
            "quoted prior conversation, and never narrate what you are doing (no "
            "\"here is a draft\" or apologies).\n"
            "- Write in the owner's first-person voice. Never greet or address the "
            "owner by name; if a greeting fits, address the RECIPIENT.\n"
            f"{mode_rule}"
            "- Plain text only (markdown links allowed); separate paragraphs with "
            "blank lines; match the language of the draft/thread; be clear and "
            "professional, not a single terse line.\n"
            "- Never use placeholders for names (e.g. [Your Name], [Name]). "
            f"{sig_rule}\n"
            "- If the context contains <personal_instructions>, <writing_style>, "
            "or <voice_profile> (the user's voice, built from their own past "
            "emails), follow them. The email being replied to (if shown) is what "
            "you must "
            "ADDRESS — answer its questions and APPLY any template, format or "
            "instructions it contains. The earlier thread is background context "
            "ONLY — do not quote, restate, or reply to it line by line."
        )
        owner = f"You are writing as: {user_email}\n" if user_email else ""
        ctx = f"{owner}User context:\n{about}\n\n" if (about or owner) else ""
        today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
        parts = [ctx, f"Today is {today}.\n"]
        if mode in ("reply", "forward") and recipient:
            verb = "reply" if mode == "reply" else "forward"
            parts.append(f"This email is a {verb}. Recipient: {recipient}\n")
        elif recipient:
            parts.append(f"Recipient: {recipient}\n")
        if subject:
            parts.append(f"Subject: {subject}\n")
        # The message being replied to — the immediate email to address/apply
        # (e.g. a template it provides), distinct from the earlier thread.
        if reply_to_body.strip():
            parts.append(
                "\nThe email you are replying to — read it in full and "
                "address/apply its contents:\n"
                f"{reply_to_body[:_DRAFT_BODY_MAX_CHARS]}\n"
            )
        if thread:
            parts.append(
                "\nEarlier in the thread, for context only (do NOT quote it):\n"
                f"{thread[:_DRAFT_THREAD_MAX_CHARS]}\n"
            )
        if improving:
            parts.append(
                "\nThe owner's current draft — improve THIS, keeping their "
                f"meaning:\n{current_body[:_DRAFT_BODY_MAX_CHARS]}\n"
            )
        guide = (instruction or "").strip()
        if guide:
            parts.append(f"\nThe owner's instruction: {guide}\n")
        elif not improving:
            parts.append(
                "\nThe owner's instruction: Draft a suitable, well-structured "
                "email for the recipient and subject above.\n"
            )
        user_prompt = "".join(parts)
        _messages = [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_prompt}]
        if on_delta is not None:
            # Streaming path (SSE compose): live deltas reach the composer;
            # the cleaned final body below still wins over the preview.
            from acb_llm.context import acompletion_stream_text  # noqa: PLC0415
            raw, _used = await acompletion_stream_text(
                model=model,
                messages=_messages, temperature=0.3, max_tokens=3000,
                on_delta=on_delta,
            )
        else:
            resp, _used = await acompletion_with_fallback(
                model=model,
                messages=_messages, temperature=0.3, max_tokens=3000,
            )
            raw = resp.choices[0].message.content or ""
        body = _clean_draft_body(raw.strip())
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.compose_assist_failed", error=str(exc)[:200])
        # Fall back to the owner's own text (improve) or a neutral opener (draft).
        return (current_body or "").strip() or DRAFT_NO_DRAFT_SENTINEL
    if _is_no_draft(body):
        return DRAFT_NO_DRAFT_SENTINEL
    # Salutation guarantee — but only on a FRESH draft. When improving the
    # owner's own text, a greeting they chose not to write must stay absent.
    if not improving:
        body = _ensure_salutation(body, _recipient_greeting_name(recipient))
    # Signature appended at send time (signature.build_signed_bodies), not here.
    return body


async def _draft_consult_plan(
    email: dict[str, str],
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> list[dict[str, str]]:
    """Qualify the email, then decide which specialist agents (if any) are
    NECESSARY to draft the reply.

    One fast LLM call does both steps: classify what kind of email this is,
    then name a specialist only when the reply must state facts the thread
    itself doesn't contain and that agent's system holds them. Most mail
    (scheduling, courtesy, newsletters, questions answered in-thread) needs no
    consult — a consult costs a 10-18s agent round-trip, so the default answer
    is an empty list, not "might help".

    Returns [{"agent": "agent-sales-assistant"|"task-manager", "question": "..."}], capped at 2.
    """
    try:
        sys_prompt = (
            "You qualify an incoming email before a reply is drafted, and "
            "decide whether drafting that reply REQUIRES consulting an "
            "internal specialist agent.\n"
            "Step 1 — classify the email kind: scheduling | social/courtesy | "
            "newsletter/notification | sales/commercial | project/delivery | "
            "support/technical | other.\n"
            "Step 2 — decide consults. Available agents:\n"
            "- agent-sales-assistant: CRM — deals, pipeline, quotes, customer/account status (Zoho).\n"
            "- task-manager: projects, tasks, deadlines, delivery status (ClickUp).\n"
            "Consult an agent ONLY when the reply must state facts that the "
            "email thread itself does not contain AND that agent's system "
            "holds them — e.g. the sender asks for an order/quote/project "
            "status, a deadline, or a deliverable update. If a correct, "
            "useful reply can be written from the thread alone "
            "(acknowledgements, scheduling, introductions, thanks, questions "
            "already answered in the thread), consult NOBODY. When unsure, "
            "consult nobody.\n"
            'Respond ONLY JSON: {"kind": "<kind>", "consult": [{"agent": '
            '"<name>", "question": "<specific question to ask that agent>"}]} '
            "(consult: [] when none is necessary — this is the common case)."
        )
        user_prompt = (
            f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}\n"
            f"Body:\n{(email.get('body', '') or '')[:1500]}"
        )
        # A fast routing decision; JSON-forced so the plan is always parseable.
        data, _content, _used = await _llm_json(
            "tier-fast",
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_prompt}],
            max_tokens=500,
        )
        consult = data.get("consult", []) if isinstance(data, dict) else []
        out = []
        for c in consult:
            if isinstance(c, dict) and c.get("agent") in ("agent-sales-assistant", "task-manager") \
                    and c.get("question"):
                out.append({"agent": c["agent"], "question": str(c["question"])})
        out = out[:2]
        email_kind = (
            str(data.get("kind", ""))[:40] if isinstance(data, dict) else "")
        # The kind is logged so we can audit gating decisions against real
        # traffic and tighten the prompt if consults stay noisy — and streamed
        # to the composer so the user SEES why a specialist was (not) called.
        _log.info("email.draft_consult_plan",
                  kind=email_kind, consults=len(out))
        await _emit_activity(
            on_activity, kind="qualify", emailKind=email_kind,
            consults=len(out),
            detail=("no specialist needed" if not out
                    else f"needs {', '.join(c['agent'] for c in out)}"))
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.draft_plan_failed", error=str(exc)[:200])
        await _emit_activity(
            on_activity, kind="qualify", emailKind="", consults=0,
            detail="qualifier unavailable — drafting from the thread alone")
        return []


def _strip_draft_markers(text: str) -> str:
    """Remove any standalone '---' fence lines the agent may wrap a draft in."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip() != "---"]
    return "\n".join(lines).strip()


_PLACEHOLDER_LINE_RE = re.compile(
    r"^\s*[\[\(]\s*(your\s+|the\s+)?"
    r"(name|full\s*name|first\s*name|position|title|role|company|signature|"
    r"sender|recipient)\s*[\]\)]\s*$",
    re.IGNORECASE,
)


def _clean_draft_body(body: str) -> str:
    """Strip placeholder-only lines (e.g. "[Your Name]") and any quoted trailing
    conversation from a drafted reply, so neither reaches the user's mailbox. The
    configured signature is appended separately by the caller.

    Both drafters are told not to quote the thread, but the reply drafter is
    handed the thread as ``From: <sender> · <ISO timestamp>`` context and a model
    will sometimes reformat that into a quote block and paste it under its reply.
    That is always wrong here: the client keeps the real quote separate and
    reattaches it, so a model-authored one is a duplicate — and it also drags the
    signature below the thread at send time. ``split_quoted_text`` returns the
    body untouched unless it can find a boundary with real content above it."""
    kept = [
        ln for ln in (body or "").splitlines()
        if not _PLACEHOLDER_LINE_RE.match(ln)
    ]
    # Collapse the trailing blank run left behind by a removed placeholder.
    cleaned = "\n".join(kept).strip()
    main, quoted = split_quoted_text(cleaned)
    if quoted:
        _log.info("email.draft_quote_stripped", chars=len(quoted))
        return main.strip()
    return cleaned


# Openers that mark the first line as ALREADY a salutation. Word-anchored so
# "Hi Alice," / "Dear Dr. Rao," / "Good morning," match but a body-first line
# like "Higher volumes are possible" does not.
_GREETING_START_RE = re.compile(
    r"^(dear|hi|hello|hey|greetings|respected|"
    r"good\s+(morning|afternoon|evening|day)|"
    r"namaste|hola|bonjour|salut|hallo|ciao)\b",
    re.IGNORECASE,
)

_HONORIFIC_RE = re.compile(r"^(mr|mrs|ms|mx|dr|prof|er)\.?$", re.IGNORECASE)


def _salutation_name(recipient_name: str) -> str:
    """The name to greet with: the first name normally, '<honorific> <surname>'
    (e.g. 'Dr. Rao') when the display name leads with an honorific, '' when
    there is no usable name (empty, or a bare email address)."""
    name = (recipient_name or "").strip().strip('"').strip("'")
    if not name or "@" in name:
        return ""
    tokens = [t.strip(",;:") for t in name.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    if len(tokens) >= 2 and _HONORIFIC_RE.match(tokens[0]):
        return f"{tokens[0]} {tokens[-1]}"
    return tokens[0]


def _ensure_salutation(body: str, recipient_name: str = "") -> str:
    """Guarantee a drafted email OPENS with a salutation line.

    The drafters are instructed to start with a greeting, but a model
    sometimes jumps straight into the message, which reads abrupt and too
    informal. When the first line doesn't look like a greeting, prepend a
    polite one addressing the recipient ('Dear <name>,' — 'Hello,' when no
    name is known). Deliberately lenient about what counts as an existing
    greeting — any recognised opener word, or a short line ending in ','
    or ':' (which covers greetings in other languages) — so a salutation
    the model DID write is never doubled."""
    stripped = (body or "").lstrip()
    if not stripped:
        return body
    first = stripped.splitlines()[0].strip()
    if _GREETING_START_RE.match(first):
        return body
    if len(first) <= 60 and first.endswith((",", ":")):
        return body
    name = _salutation_name(recipient_name)
    greeting = f"Dear {name}," if name else "Hello,"
    return f"{greeting}\n\n{stripped}"


def _recipient_greeting_name(recipient: str) -> str:
    """Display name of the first recipient in a 'Name <addr>' / bare-address
    string — '' when only an address is known, so the greeting stays 'Hello,'."""
    r = (recipient or "").strip()
    if not r:
        return ""
    name = r.split("<")[0].strip().strip('"').strip("'").rstrip(",")
    if not name or "@" in name:
        return ""
    return name


async def _draft_via_maf_agent(
    email: dict[str, str], about: str, signature: str, user_email: str,
    *, instructions: str = "",
) -> str | None:
    """Draft by running the email-assistant MAF agent (which can hand off to
    agent-sales-assistant / task-manager and read memory). Returns None on any failure so the
    caller can fall back to the in-gateway orchestrator.

    NOTE: currently dead — ``_agent_draft_reply`` always routes to
    ``_orchestrate_draft`` instead (see the comment there). The bare-email memory
    scope below would be a per-account retrieval MISS if this is ever revived;
    thread ``account_id`` through and use ``email_memory_scope`` if you do. (It
    can't be scoped here anyway without breaking the agent's X-User-Email auth —
    the executor re-derives the ContextVar from the run_agent payload below.)"""
    try:
        from acb_skills.memory_tools import _set_memory_user_id  # noqa: PLC0415
        _set_memory_user_id(user_email or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        from orchestrator.executor import run_agent  # noqa: PLC0415
        task = instructions or (
            "Draft a reply to the email below. First gather context: use "
            "remember() for the sender, and call_agent('agent-sales-assistant' or 'task-manager') "
            "ONLY if the email is clearly about a deal or a project."
        )
        thread = (email.get("thread") or "").strip()
        thread_block = (
            "\nEarlier in this thread (oldest to newest):\n"
            f"{thread[:_DRAFT_THREAD_MAX_CHARS]}\n"
            if thread else ""
        )
        msg = (
            f"{task} Then write ONLY the message body — no subject line, no "
            "preamble, no '---' fences, no confidence line.\n\n"
            f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}\n"
            f"{thread_block}"
            "Latest message (reply to this):\n"
            f"{(email.get('body', '') or '')[:_DRAFT_BODY_MAX_CHARS]}"
        )
        res = await asyncio.wait_for(
            run_agent(
                "email-assistant",
                {"message": msg, "about": about, "signature": signature,
                 "user_email": user_email},
            ),
            timeout=150.0,
        )
        ans = ""
        if isinstance(res, dict):
            ans = res.get("answer") or ""
            if not ans and isinstance(res.get("result"), dict):
                ans = res["result"].get("content") or ""
            if not ans and isinstance(res.get("result"), str):
                ans = res["result"]
        ans = _strip_draft_markers(ans)
        if ans:
            # Signature appended at send time (signature.build_signed_bodies).
            return ans
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.maf_draft_failed", error=str(exc)[:200])
    return None


_FOLLOW_UP_INSTRUCTION = (
    "This is my OWN earlier email that hasn't received a reply yet. Write a "
    "brief, polite follow-up that nudges for a response — keep it short, "
    "reference the original subject, and do NOT repeat the full original message."
)


DRAFT_NO_DRAFT_SENTINEL = "NO_DRAFT"

# The drafter is told to emit DRAFT_NO_DRAFT_SENTINEL ("NO_DRAFT") and nothing
# else when a confidence gate fires. Models don't always comply exactly — they
# wrap it in markdown (_NO_DRAFT_, **NO_DRAFT**), quotes, the spaced "NO DRAFT"
# form, a markdown-escaped underscore ("NO\\_DRAFT"), or trailing punctuation.
# Match the WHOLE body (fullmatch) so a real greeting-led reply can NEVER be
# mistaken for a decline, while tolerating those wrappers. (Design + edge cases
# adversarially verified — zero false positives on real drafts.)
_NO_DRAFT_RE = re.compile(
    r'''[\s"'`*_>~()\[\]{}.,:;!?\\-]*no\\?[_\s-]?draft[\s"'`*_>~()\[\]{}.,:;!?\\-]*''',
    re.IGNORECASE,
)


def _is_no_draft(body: str) -> bool:
    """True when the drafter declined to write a reply (confidence gate): an empty
    body, or the NO_DRAFT sentinel — tolerant of case, wrapping quotes/markdown,
    the spaced "NO DRAFT" form, and trailing punctuation, but matched against the
    ENTIRE body so a genuine (greeting-led, multi-line) reply never matches."""
    t = (body or "").strip()
    return not t or bool(_NO_DRAFT_RE.fullmatch(t))


# Granular confidence rubric ported from inbox-zero's drafter, so the NO_DRAFT
# gate reasons about *why* a draft is/ isn't trustworthy, not just "unsure".
_CONFIDENCE_RUBRIC = (
    "Judge your confidence in the draft: HIGH = complete and fully grounded — "
    "every fact comes from the email/thread/context, with no reliance on missing "
    "facts, assumptions, unavailable business or calendar state, or details only "
    "the user can fill in. MEDIUM = a useful reply that leans on reasonable "
    "assumptions or a few user-fillable details. LOW = highly uncertain, needs "
    "broader context, or would mostly just ask/check/follow up."
)

_CONFIDENCE_INSTRUCTIONS = {
    "STANDARD": (
        _CONFIDENCE_RUBRIC + " Draft only when your confidence is MEDIUM or "
        "higher. If it would be LOW (you don't clearly understand the email, or a "
        "reply isn't appropriate), output exactly "
        f"{DRAFT_NO_DRAFT_SENTINEL} and nothing else."
    ),
    "HIGH_CONFIDENCE": (
        _CONFIDENCE_RUBRIC + " Draft ONLY when your confidence is HIGH — a "
        "complete, accurate, fully-grounded reply. At MEDIUM or LOW (any doubt, "
        "missing facts, or assumptions), output exactly "
        f"{DRAFT_NO_DRAFT_SENTINEL} and nothing else."
    ),
}


async def _agent_draft_reply(
    email: dict[str, str], about: str, signature: str, user_email: str,
    *, use_agent: bool = False, max_agents: int = 2, agent_timeout: float = 90.0,
    follow_up: bool = False, confidence: str = "ALL_EMAILS",
    model: str = "tier-powerful", account_id: str = "",
    interactive_fallback: bool = False, extra_instructions: str = "",
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_delta: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
    """Draft a reply (or a follow-up nudge). When ``use_agent`` is set (background
    rule actions), run the email-assistant MAF agent first; otherwise — and on any
    agent failure — use the fast in-gateway orchestrator.

    ``confidence`` (ALL_EMAILS | STANDARD | HIGH_CONFIDENCE) gates drafting: the
    higher tiers instruct the drafter to return the NO_DRAFT sentinel when it
    isn't confident, which the caller treats as "skip this draft".

    ``extra_instructions`` is the USER'S steer for this one draft (the compose
    box's AI-bar prompt, a nudge preset) — it reaches the drafter as extra
    instructions alongside the follow-up/confidence ones.

    ``model`` is the account's draft-writing model (default the powerful tier)."""
    instructions = _FOLLOW_UP_INSTRUCTION if follow_up else ""
    if (extra_instructions or "").strip():
        instructions = (instructions + "\n" + extra_instructions.strip()).strip()
    conf_note = _CONFIDENCE_INSTRUCTIONS.get(confidence or "ALL_EMAILS", "")
    if conf_note:
        instructions = (instructions + "\n" + conf_note).strip()
    # NOTE: we deliberately do NOT use the conversational email-assistant MAF
    # agent to write the draft body — it narrated its work ("I'm sorry, here's a
    # draft you can use:"), greeted the wrong person, and left [Your Name]
    # placeholders in the body. The focused orchestrating drafter gathers the
    # same memory/specialist context and produces a clean reply via
    # _llm_draft_reply. (`use_agent` is kept for call-site compatibility.)
    _ = use_agent
    return await _orchestrate_draft(
        email, about, signature, user_email,
        max_agents=max_agents, agent_timeout=agent_timeout,
        instructions=instructions, model=model, account_id=account_id,
        interactive_fallback=interactive_fallback,
        on_activity=on_activity, on_delta=on_delta,
    )


# Strong refs to fire-and-forget tasks (asyncio only holds weak ones — an
# unreferenced task can be garbage-collected mid-flight).
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro: Awaitable[None], label: str) -> None:
    """Run ``coro`` as a fire-and-forget task; log (never raise) its failure."""
    async def _wrapped() -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            _log.warning("email.background_task_failed",
                         task=label, error=str(exc)[:160])
    task = asyncio.create_task(_wrapped())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


async def _emit_activity(
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None,
    **fields: Any,
) -> None:
    """Best-effort progress signal to the caller (SSE); never breaks drafting.

    Each event is a dict the composer's activity panel renders directly:
      {"kind": "stage",    "id": "context|memory|draft", "status": "start|done",
       "detail": "human summary"}
      {"kind": "qualify",  "emailKind": "scheduling", "consults": 0}
      {"kind": "consult",  "agent": "task-manager", "question": "...",
       "status": "start|done|failed", "detail": "..."}
    """
    if on_activity is None:
        return
    try:
        await on_activity(fields)
    except Exception:  # noqa: BLE001
        pass


async def _orchestrate_draft(
    email: dict[str, str], about: str, signature: str, user_email: str,
    *, max_agents: int = 2, agent_timeout: float = 90.0, instructions: str = "",
    model: str = "tier-powerful", account_id: str = "",
    interactive_fallback: bool = False,
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_delta: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
    """In-gateway orchestrating drafter: gather context from memory + specialist
    agents (sales / task-manager), then draft. Best-effort; degrades to an
    About-only draft.

    ``account_id`` scopes the Mem0 read/write to this inbox (email_memory_scope)
    so a user's other mailboxes don't bleed their reply context into this draft.
    Setting the ContextVar here only steers this in-process ``remember`` call —
    it is NOT the agent's gateway-auth identity, so a scoped value is safe.

    ``on_activity(event)`` (optional) receives structured progress events for
    streaming UIs (see _emit_activity). ``on_delta(kind, text)`` streams the
    draft itself (see _llm_draft_reply)."""
    context_parts: list[str] = []
    mem_scope = email_memory_scope(user_email or "", account_id)

    # 1) Memory: what do we know about this sender / relationship? Both
    # lookups are independent — run them concurrently.
    async def _memory_context() -> list[str]:
        parts: list[str] = []
        try:
            from acb_skills.memory_tools import (  # noqa: PLC0415
                _set_memory_user_id,
                remember,
            )
            _set_memory_user_id(mem_scope)
            mem, precedent = await asyncio.gather(
                remember(
                    f"past context, agreements, and preferences relevant to "
                    f"{email.get('from', '')} and: {email.get('subject', '')}"
                ),
                # Precedent: semantically similar past emails (any sender) and
                # how the account handled them — inbox-zero's <email_history>
                # advisory context.
                remember(
                    f"similar past emails about '{email.get('subject', '')}' "
                    f"and how they were handled or replied to: "
                    f"{(email.get('body', '') or '')[:200]}"
                ),
            )
            if mem and "no relevant" not in mem.lower():
                parts.append(f"From memory:\n{mem[:1500]}")
            if (precedent and "no relevant" not in precedent.lower()
                    and precedent.strip() != (mem or "").strip()):
                parts.append(
                    "Similar past emails (precedent — advisory, the current "
                    f"thread still wins):\n{precedent[:1200]}")
        except Exception as exc:  # noqa: BLE001
            _log.warning("email.draft_memory_failed", error=str(exc)[:160])
        return parts

    # 2) Memory recall and the consult qualifier are independent of each other
    # — run them concurrently, so qualifying the email costs no wall-clock
    # unless it actually triggers a specialist consult.
    await _emit_activity(on_activity, kind="stage", id="memory", status="start")
    mem_parts, plan = await asyncio.gather(
        _memory_context(), _draft_consult_plan(email, on_activity=on_activity))
    context_parts.extend(mem_parts)
    await _emit_activity(
        on_activity, kind="stage", id="memory", status="done",
        detail=("nothing relevant found" if not mem_parts
                else f"{len(mem_parts)} relevant "
                     f"{'note' if len(mem_parts) == 1 else 'notes'}"))

    # 3) Specialist agents: delegate via the orchestrator's run_agent — only
    # when the qualifier judged the reply NEEDS their data (see
    # _draft_consult_plan; most emails come back with an empty plan).
    if plan:
        try:
            from orchestrator.executor import run_agent  # noqa: PLC0415
            for item in plan[:max_agents]:
                await _emit_activity(
                    on_activity, kind="consult", status="start",
                    agent=item["agent"], question=item["question"])
                try:
                    res = await asyncio.wait_for(
                        run_agent(
                            item["agent"],
                            {"message": item["question"], "user_email": user_email},
                        ),
                        timeout=agent_timeout,
                    )
                    ans = ""
                    if isinstance(res, dict):
                        ans = str(res.get("answer") or res.get("result") or "")
                    if ans.strip():
                        context_parts.append(
                            f"From the {item['agent']} agent "
                            f"(asked: {item['question']}):\n{ans[:1500]}"
                        )
                    await _emit_activity(
                        on_activity, kind="consult", status="done",
                        agent=item["agent"], question=item["question"],
                        answer=ans.strip()[:400],
                        detail=("answered" if ans.strip() else "no answer"))
                except Exception as exc:  # noqa: BLE001
                    _log.warning("email.draft_agent_failed",
                                 agent=item.get("agent"), error=str(exc)[:160])
                    await _emit_activity(
                        on_activity, kind="consult", status="failed",
                        agent=item["agent"], question=item["question"],
                        detail="unavailable — drafting without it")
        except Exception as exc:  # noqa: BLE001
            _log.warning("email.draft_orchestrator_unavailable", error=str(exc)[:160])

    await _emit_activity(on_activity, kind="stage", id="draft", status="start")
    draft = await _llm_draft_reply(
        email, about, signature, instructions=instructions,
        context="\n\n".join(context_parts), user_email=user_email,
        model=model, interactive_fallback=interactive_fallback,
        on_delta=on_delta,
    )

    # 4) Record this exchange in Mem0 (episodic, pgvector) so future drafts to
    # this correspondent have context. Use add_memories_background — NOT
    # add_episode, which targets Graphiti/Neo4j (disabled → silent no-op).
    # Fire-and-forget (its own documented contract): Mem0's add runs an LLM
    # extraction pass, and awaiting it here made the user wait several extra
    # seconds AFTER the draft was already written.
    # Scoped to this inbox (mem_scope) so it reads back under the same key.
    # Skip when the drafter declined or failed: storing "I replied: NO_DRAFT"
    # (or a boilerplate fallback) as precedent poisons future retrievals.
    if not _is_no_draft(draft):
        try:
            from acb_memory import add_memories_background  # noqa: PLC0415
            _spawn_background(
                add_memories_background(
                    mem_scope or "default",
                    [
                        {"role": "user",
                         "content": (
                             f"Email from {email.get('from', '')} — subject "
                             f"'{email.get('subject', '')}': "
                             f"{(email.get('body', '') or '')[:600]}"
                         )},
                        {"role": "assistant",
                         "content": f"I replied: {draft[:600]}"},
                    ],
                    agent_id="email",
                ),
                label="draft_memory_add",
            )
        except Exception:  # noqa: BLE001
            pass

    return draft


async def _build_reply_context(
    db: Any, account_id: str, message_id: str, user_email: str,
) -> dict[str, Any] | None:
    """Assemble the FULL reply context for one message — the SINGLE source of
    truth every drafting entry point uses (/draft-reply and the compose card's
    "Draft with AI"), so the same context and rules apply everywhere.

    Includes: the hydrated body of the message being replied to (the immediate
    email — e.g. one carrying a template to apply), the earlier thread, the
    owner's direction/recipient-role signals (self / sender_scope / To / Cc),
    attachment metadata, past replies to this sender, and reply memories. Returns
    None when the message doesn't exist."""
    row = (await db.execute(text(
        """SELECT em.id, em.provider_message_id, em.thread_id, em.subject,
                  em.body_text, em.snippet, em.from_address,
                  em.to_addresses, em.cc_addresses
           FROM email_messages em
           WHERE em.id = :mid AND em.account_id = :aid"""
    ), {"mid": message_id, "aid": account_id})).fetchone()
    if not row:
        return None
    frm = row.from_address if isinstance(row.from_address, dict) \
        else json.loads(row.from_address or "{}")
    # Ensure the FULL incoming body is present before drafting. Header-only rows
    # (Outlook/Graph sync) store an empty body_text + a ~200-char snippet; without
    # this the drafter sees the message cut off. Hydrate, then fall back to snippet.
    from gateway.routes.email.core import hydrate_message_body  # noqa: PLC0415
    hydrated = await hydrate_message_body(db, str(row.id), user_email)
    # The mailbox's own address + org domains → direction (self/internal/external)
    # and recipient role (To vs Cc-only).
    self_row = (await db.execute(text(
        "SELECT email_address FROM email_accounts WHERE id = :id"
    ), {"id": account_id})).fetchone()
    self_email = (self_row.email_address if self_row else "") or ""
    from gateway.routes.email.automation.identity import (  # noqa: PLC0415
        resolve_org_domains,
        sender_scope,
    )
    org_domains = await resolve_org_domains(db, account_id)
    email: dict[str, Any] = {
        "subject": row.subject or "", "from": frm.get("email", ""),
        "from_name": frm.get("name", "") or "",
        "self": self_email,
        "sender_scope": sender_scope(frm.get("email", ""), self_email, org_domains),
        "to": _fmt_addr_list(row.to_addresses),
        "cc": _fmt_addr_list(row.cc_addresses),
        "attachments": (await _attachment_summaries(
            db, [row.id])).get(str(row.id), ""),
        "body": (hydrated or "").strip() or row.body_text or row.snippet or "",
        "thread_id": row.thread_id or "",
        "thread": await _fetch_thread_context(
            db, account_id, row.thread_id or "", row.provider_message_id or ""),
        "sender_examples": await _fetch_sender_reply_examples(
            db, account_id, frm.get("email", "")),
    }
    email["reply_memories"] = await _fetch_reply_memories(db, account_id, email)
    # "Draft in my voice": semantically-nearest SENT mail (any recipient) to what
    # we're replying to. No-op + no cost when semantic search is off.
    email["sent_examples"] = await _fetch_sent_fewshot(
        db, account_id, row.subject, email["body"],
        exclude_thread_id=row.thread_id or "")
    # Calendar context — ONLY when the message is asking about timing, so an
    # ordinary reply doesn't carry the user's schedule for no reason.
    email["calendar"] = (
        await _fetch_calendar_context(db, account_id)
        if _asks_about_scheduling(row.subject, email["body"]) else "")
    return email


class DraftReplyRequest(BaseModel):
    account_id: str
    message_id: str
    create_draft: bool = False  # also save a provider draft (lands in Drafts)
    follow_up: bool = False  # draft a nudge for my own unanswered email instead


@router.post("/draft-reply")
async def draft_reply_smart(
    req: DraftReplyRequest,
    user: UserContext = Depends(get_current_user),
):
    """Draft a context-aware reply with the orchestrating drafter (memory +
    sales/task-manager). Returns the draft text; optionally also creates a
    provider draft in the user's Drafts."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        email = await _build_reply_context(
            db, req.account_id, req.message_id, user.email or "anonymous")
        if email is None:
            raise HTTPException(status_code=404, detail="Message not found")
        # Rank the KB by relevance to THIS message, not recency (3.5).
        about, signature = await _load_assistant_about(
            db, req.account_id,
            query=f"{email.get('subject', '')} {email.get('body', '')}")
        models = await _account_models(db, req.account_id)
        # Synchronous request → keep the orchestration budget under the proxy
        # timeout (one specialist agent, short timeout). Interactive → the
        # account's COMPOSE model (fast by default), not the background one.
        draft = await _agent_draft_reply(
            email, about, signature, user.email or "",
            max_agents=1, agent_timeout=18.0, follow_up=req.follow_up,
            model=models["compose"], account_id=req.account_id,
            interactive_fallback=True,
        )

        # Confidence gate (defense-in-depth): if the drafter declined, never
        # persist or surface a literal "NO_DRAFT" body. (Sync requests default to
        # ALL_EMAILS so this rarely fires, but keeps the gate consistent.)
        if _is_no_draft(draft):
            return {"draft": "", "created": False, "skipped": "low_confidence"}

        # The signature lives IN the draft body, so the provider draft shows it
        # upstream (Gmail/Outlook) too — it used to be added only at send time,
        # leaving synced drafts looking unsigned. Idempotent; send re-signing
        # dedups against this same text.
        draft = append_signature_text(signature, draft)

        # Remember this draft so we can learn from the user's edits on send.
        if not req.follow_up:
            # commit=False: this db is the route's _tenant_session — the
            # wrapper's exit commit lands the row (H2).
            await _store_ai_draft(
                db, req.account_id, email["thread_id"], draft, commit=False)

        created = False
        if req.create_draft:
            try:
                async with provider_session(
                    db, user.email or "anonymous",
                    message_id=req.message_id, require_auth=False,
                ) as sess:
                    if sess.authed:
                        re_subject = (
                            email["subject"]
                            if email["subject"].lower().startswith("re:")
                            else f"Re: {email['subject']}"
                        )
                        provider_id = await sess.provider.create_draft(
                            to=[email["from"]],
                            subject=re_subject,
                            body_text=draft,
                            reply_to_message_id=sess.provider_message_id,
                            thread_id=email["thread_id"] or None,
                        )
                        # Mirror locally so it shows in Drafts + in-thread at once.
                        await _upsert_local_draft(
                            db, sess.account_id, provider_id,
                            thread_id=email["thread_id"] or None,
                            owner_email=user.email or "", to_email=email["from"],
                            subject=re_subject, body=draft,
                        )
                        created = True
                # The wrapper's exit commit lands the mirrored local draft.
            except Exception as exc:  # noqa: BLE001
                _log.warning("email.draft_reply_create_failed", error=str(exc)[:160])

        return {"draft": draft, "created": created}


class ComposeAssistRequest(BaseModel):
    account_id: str
    # The current NEW body the user has typed (the quoted trailing chain is
    # stripped client-side, so the model never rewrites the quote). Empty = draft
    # from scratch; non-empty = improve this text in place.
    body: str = ""
    instruction: str = ""           # optional guidance ("make it shorter", …)
    mode: str = "new"               # "new" | "reply" | "forward"
    message_id: str | None = None   # reply/forward: pulls thread context
    to: list[str] | None = None     # new email: recipient(s) for context
    subject: str = ""


async def _compose_assist_run(
    req: ComposeAssistRequest, user: UserContext,
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_delta: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict[str, str]:
    """The ONE compose-assist implementation behind both the JSON endpoint and
    the SSE streaming endpoint. Returns {"draft": ...} or
    {"draft": "", "skipped": "low_confidence"}."""
    # H4: _compose_assist_run also runs via asyncio.create_task on the
    # compose_assist_stream keep-alive path — a task must not inherit the
    # ambient tenant; needs an explicit tenant threaded through the call.
    db = await _get_db()
    try:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        await _emit_activity(
            on_activity, kind="stage", id="context", status="start")
        # Rank the KB by what's being composed, not recency (3.5).
        about, signature = await _load_assistant_about(
            db, req.account_id,
            query=f"{req.subject or ''} {req.body or ''}")
        models = await _account_models(db, req.account_id)
        # The composer body CARRIES the signature now (it's part of the draft,
        # visible upstream). The AI must never see it: a signature-only body is
        # an EMPTY draft — it routes to the full reply drafter, not improve —
        # and improve mode works on the owner's words alone. Strip it here; the
        # result is re-signed before returning.
        req_body = strip_signature_text(signature, req.body or "")
        recipient = ""
        subject = req.subject or ""
        thread = ""
        reply_to_body = ""
        # Reply/forward: load the FULL reply context (the message being replied to
        # + thread + direction/recipients) via the ONE shared builder — the same
        # one /draft-reply uses. This is what was missing: the compose card never
        # loaded the replied-to message body, so "Draft with AI" couldn't apply a
        # template the trailing email provided.
        ctx = None
        if req.message_id and req.mode in ("reply", "forward"):
            ctx = await _build_reply_context(
                db, req.account_id, req.message_id, user.email or "")
        if ctx is not None:
            # Draft-from-scratch REPLY → route through the SAME reply drafter as
            # /draft-reply, so full context + identical rules apply (one method,
            # not a separate lighter one). Improve-in-place and forward keep the
            # compose path, now WITH the replied-to body in view.
            if req.mode == "reply" and not req_body.strip():
                # The user's AI-bar prompt must steer this draft. This path used
                # to drop req.instruction entirely, so typing an instruction and
                # hitting Draft on an empty reply changed nothing.
                # Interactive → the account's COMPOSE model (fast by default).
                draft = await _agent_draft_reply(
                    ctx, about, signature, user.email or "",
                    max_agents=1, agent_timeout=18.0,
                    model=models["compose"], account_id=req.account_id,
                    interactive_fallback=True,
                    extra_instructions=(req.instruction or "").strip(),
                    on_activity=on_activity, on_delta=on_delta,
                )
                if _is_no_draft(draft):
                    return {"draft": "", "skipped": "low_confidence"}
                # The signature rides in the returned body (the composer shows
                # it as part of the draft; autosave syncs it upstream).
                draft = append_signature_text(signature, draft)
                # Remember this draft (same as /draft-reply) so when the user
                # edits it in the composer and sends, _learn_from_sent can diff
                # the sent body against what the AI wrote. Without this the
                # highest-traffic "Draft with AI" entry point taught nothing.
                if ctx.get("thread_id"):
                    await _store_ai_draft(
                        db, req.account_id, ctx["thread_id"], draft)
                return {"draft": draft}
            subject = subject or ctx.get("subject", "")
            thread = ctx.get("thread", "")
            reply_to_body = ctx.get("body", "")
            if req.mode != "forward":  # reply recipient is the original sender
                nm, addr = ctx.get("from_name", ""), ctx.get("from", "")
                recipient = (f"{nm} <{addr}>" if nm else addr).strip()
        if not recipient and req.to:
            recipient = ", ".join([a for a in req.to if a])

        # Refinement rounds (the user is iterating on an existing draft) reuse
        # the improve-in-place path — no memory/consult sweep, so each edit
        # round is a single fast model call.
        await _emit_activity(
            on_activity, kind="stage", id="context", status="done",
            detail="thread and recipient loaded")
        await _emit_activity(
            on_activity, kind="stage", id="draft", status="start",
            detail=("refining your draft" if req_body.strip()
                    else "writing a new draft"))
        draft = await _llm_compose_assist(
            about=about, signature=signature, current_body=req_body,
            instruction=req.instruction, mode=req.mode, recipient=recipient,
            subject=subject, thread=thread, reply_to_body=reply_to_body,
            user_email=user.email or "", model=models["compose"],
            on_delta=on_delta,
        )
        if _is_no_draft(draft):
            return {"draft": "", "skipped": "low_confidence"}
        return {"draft": append_signature_text(signature, draft)}
    finally:
        await db.close()


@router.post("/compose-assist")
async def compose_assist(
    req: ComposeAssistRequest,
    user: UserContext = Depends(get_current_user),
):
    """Draft or improve the body the user is composing. Operates ONLY on the new
    text (the client strips the quoted trailing email first); for a reply/forward
    the original thread is loaded as context but never quoted back. Returns the
    drafted/improved body."""
    return await _compose_assist_run(req, user)


@router.post("/compose-assist/stream")
async def compose_assist_stream(
    req: ComposeAssistRequest,
    user: UserContext = Depends(get_current_user),
):
    """SSE variant of /compose-assist: same drafting pipeline, but progress is
    streamed so the composer shows what the backend is doing instead of a bare
    spinner. Events (each ``data: {json}``):

      {"type": "activity", ...}
          — structured step events the composer's activity panel renders:
            stage (context/memory/draft, start|done), qualify (the email kind
            + whether a specialist is needed), consult (agent, the question
            asked, and its answer). See _emit_activity.
      {"type": "delta", "kind": "reasoning|content", "text": ...}
          — live model output; "reasoning" is the model thinking out loud
            (reasoning-tier models), "content" is draft text.
      {"type": "done", "draft": ..., "skipped"?: ...}
          — the FINAL cleaned draft (salutation/placeholder rules applied);
            always replaces any streamed preview text.
      {"type": "error", "error": ...}
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def on_activity(event: dict[str, Any]) -> None:
        await queue.put({"type": "activity", **event})

    async def on_delta(kind: str, text_delta: str) -> None:
        await queue.put({"type": "delta", "kind": kind, "text": text_delta})

    async def runner() -> None:
        try:
            result = await _compose_assist_run(
                req, user, on_activity=on_activity, on_delta=on_delta)
            await queue.put({"type": "done", **result})
        except HTTPException as exc:
            await queue.put({"type": "error", "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            _log.warning("email.compose_stream_failed", error=str(exc)[:200])
            await queue.put({"type": "error", "error": "draft failed"})
        finally:
            await queue.put(None)

    async def event_stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            # Client went away mid-draft → stop paying for the LLM call.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


async def _upsert_local_draft(
    db: Any, account_id: str, provider_message_id: str, *,
    thread_id: str | None, owner_email: str, to_email: str,
    subject: str, body: str,
    cc: list[str] | None = None, bcc: list[str] | None = None,
    has_attachments: bool = False,
) -> str:
    """Persist a just-created/updated provider draft into ``email_messages`` so it
    shows in the Drafts folder and in-thread immediately — without waiting for the
    next provider sweep. Keyed on ``(account_id, provider_message_id)`` so that
    later Drafts sync updates this same row instead of duplicating it. Returns the
    local message id.

    ``cc``/``bcc`` are mirrored locally too, so a reopened draft shows its Cc/Bcc
    from our own copy immediately instead of waiting for the next Drafts sweep."""
    res = await db.execute(text(
        """INSERT INTO email_messages
             (id, account_id, provider_message_id, thread_id, folder,
              from_address, to_addresses, cc_addresses, bcc_addresses,
              subject, body_text, snippet,
              is_read, is_starred, is_flagged, has_attachments,
              received_at, synced_at)
           VALUES (:id, :aid, :pmid, :tid, 'drafts',
              :from_addr, :to_addrs, :cc_addrs, :bcc_addrs,
              :subject, :body, :snippet,
              true, false, false, :has_att, now(), now())
           ON CONFLICT (account_id, provider_message_id) DO UPDATE SET
             thread_id = COALESCE(EXCLUDED.thread_id, email_messages.thread_id),
             to_addresses = EXCLUDED.to_addresses,
             cc_addresses = EXCLUDED.cc_addresses,
             bcc_addresses = EXCLUDED.bcc_addresses,
             subject = EXCLUDED.subject,
             body_text = EXCLUDED.body_text,
             snippet = EXCLUDED.snippet,
             -- Sticky-true: a later attachment-less auto-save must not clear a
             -- draft's attachments flag once the pre-send save has set it.
             has_attachments =
               email_messages.has_attachments OR EXCLUDED.has_attachments,
             folder = 'drafts',
             updated_at = now()
           RETURNING id"""
    ), {
        "id": str(uuid4()), "aid": account_id, "pmid": provider_message_id,
        "tid": thread_id or None,
        "from_addr": json.dumps({"name": "", "email": owner_email or ""}),
        "to_addrs": json.dumps(
            [{"name": "", "email": to_email}] if to_email else []),
        "cc_addrs": json.dumps(
            [{"name": "", "email": a} for a in (cc or []) if a]),
        "bcc_addrs": json.dumps(
            [{"name": "", "email": a} for a in (bcc or []) if a]),
        "subject": subject or "", "body": body or "",
        "snippet": (body or "")[:200], "has_att": has_attachments,
    })
    rid = res.fetchone()
    return str(rid.id) if rid else ""


async def _fetch_message_dict(db: Any, message_id: str) -> dict[str, Any]:
    """Return one stored message in the API (snake_case) shape, or {}."""
    row = (await db.execute(text(
        """SELECT em.id, em.provider_message_id, em.thread_id, em.account_id,
                  em.folder, em.labels, em.from_address, em.to_addresses,
                  em.cc_addresses, em.bcc_addresses, em.subject, em.body_text,
                  em.body_html, em.snippet, em.has_attachments, em.is_read,
                  em.is_starred, em.is_flagged, em.importance, em.categories,
                  em.received_at, em.synced_at
           FROM email_messages em WHERE em.id = :id"""
    ), {"id": message_id})).fetchone()
    return _row_to_message(row).model_dump() if row else {}


class DraftUpsertRequest(BaseModel):
    account_id: str
    # Local id of an existing draft to UPDATE in place (omit to create a new one).
    draft_id: str | None = None
    # Local id of the message being replied to (creates a threaded reply draft).
    reply_to_message_id: str | None = None
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    subject: str = ""
    body: str = ""
    # Files to attach to the draft: base64 uploads + workspace artifact refs
    # (same shapes the /send path accepts). The composer sends these only on the
    # explicit pre-send save, so update_draft adds them exactly once.
    attachments: list[SendAttachment] = []
    artifacts: list[ArtifactAttachment] = []


@router.put("/drafts")
async def upsert_draft(
    req: DraftUpsertRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create or update a draft on the provider AND mirror it locally.

    This is the reverse-sync write path behind Gmail/Outlook-style auto-save:
    the editor saves as you type. ``draft_id`` updates the same provider draft in
    place (no duplicates); ``reply_to_message_id`` threads a new reply draft;
    neither → a standalone draft. Returns the persisted message so the UI can show
    it in the Drafts folder and in-thread at once."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        # The signature lives IN the draft body so the upstream (provider)
        # draft shows it too. The composer normally seeds it already — this is
        # the idempotent backstop for bodies that arrived without it.
        body = append_signature_text(
            await _account_signature(db, req.account_id), req.body)
        async with provider_session(
            db, user.email or "anonymous", account_id=req.account_id,
        ) as sess:
            provider = sess.provider

            to = [t for t in (req.to or []) if t]
            cc = [c for c in (req.cc or []) if c]
            bcc = [b for b in (req.bcc or []) if b]
            subject = req.subject or ""
            atts = _resolve_draft_attachments(req.attachments, req.artifacts, user.email)
            thread_id: str | None = None

            if req.draft_id:
                drow = (await db.execute(text(
                    "SELECT provider_message_id, thread_id, subject"
                    " FROM email_messages"
                    " WHERE id = :id AND account_id = :aid"
                    " AND LOWER(folder) IN ('drafts', 'draft')"
                ), {"id": req.draft_id, "aid": req.account_id})).fetchone()
                if not drow:
                    raise HTTPException(status_code=404, detail="Draft not found")
                thread_id = drow.thread_id
                subject = subject or (drow.subject or "")
                try:
                    provider_id = await provider.update_draft(
                        drow.provider_message_id, to=to or None,
                        subject=subject or None, body_text=body,
                        thread_id=thread_id or None,
                        cc=cc, bcc=bcc, attachments=atts or None,
                    )
                except NotImplementedError:
                    # No in-place update primitive → fresh draft, drop the old.
                    provider_id = await provider.create_draft(
                        to=to, subject=subject, body_text=body,
                        thread_id=thread_id or None, cc=cc, bcc=bcc,
                        attachments=atts or None,
                    )
                    try:
                        await provider.trash_message(drow.provider_message_id)
                    except Exception:  # noqa: BLE001
                        pass
            elif req.reply_to_message_id:
                # Accept either the local message id (inline reply) or the
                # provider message id (full composer pop-out passes
                # providerMessageId).
                rrow = (await db.execute(text(
                    "SELECT provider_message_id, thread_id, subject, from_address"
                    " FROM email_messages WHERE account_id = :aid"
                    " AND (id = :id OR provider_message_id = :id)"
                    " LIMIT 1"
                ), {"id": req.reply_to_message_id,
                    "aid": req.account_id})).fetchone()
                if not rrow:
                    raise HTTPException(
                        status_code=404, detail="Reply target not found")
                thread_id = rrow.thread_id
                if not subject:
                    s0 = rrow.subject or ""
                    subject = s0 if s0.lower().startswith("re:") else f"Re: {s0}"
                if not to:
                    frm = rrow.from_address \
                        if isinstance(rrow.from_address, dict) \
                        else json.loads(rrow.from_address or "{}")
                    if frm.get("email"):
                        to = [frm["email"]]
                provider_id = await provider.create_draft(
                    to=to, subject=subject, body_text=body,
                    reply_to_message_id=rrow.provider_message_id,
                    thread_id=thread_id or None, cc=cc, bcc=bcc,
                    attachments=atts or None,
                )
            else:
                provider_id = await provider.create_draft(
                    to=to, subject=subject, body_text=body, cc=cc, bcc=bcc,
                    attachments=atts or None,
                )

            local_id = await _upsert_local_draft(
                db, req.account_id, provider_id, thread_id=thread_id,
                owner_email=sess.owner_email, to_email=(to[0] if to else ""),
                subject=subject, body=body, cc=cc, bcc=bcc,
                has_attachments=bool(atts),
            )
        return await _fetch_message_dict(db, local_id)


class DraftSendRequest(BaseModel):
    account_id: str
    draft_id: str  # local email_messages id of the draft to send


@router.post("/drafts/send")
async def send_draft_endpoint(
    req: DraftSendRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Send an existing draft natively (Drafts → Sent, no duplicate) and drop the
    local draft row. Falls back to send-new-then-trash for providers without a
    native send-draft primitive."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        drow = (await db.execute(text(
            "SELECT provider_message_id, subject, to_addresses, body_text,"
            " thread_id"
            " FROM email_messages WHERE id = :id AND account_id = :aid"
            " AND LOWER(folder) IN ('drafts', 'draft')"
        ), {"id": req.draft_id, "aid": req.account_id})).fetchone()
        if not drow:
            raise HTTPException(status_code=404, detail="Draft not found")
        async with provider_session(
            db, user.email or "anonymous", account_id=req.account_id,
        ) as sess:
            provider = sess.provider

            # The account signature is appended at send time; the stored draft
            # is signature-free. The provider draft is already threaded onto its
            # conversation (created via Gmail threadId / Outlook createReply),
            # so we sign it IN PLACE (update_draft, which preserves the thread)
            # and then send it natively — the reply lands in its conversation.
            # Sending a fresh message instead would start a new thread (the
            # "reply shows up as a separate email in Sent" bug). Providers
            # without an update/send-draft primitive (IMAP) fall back to a
            # thread-aware fresh send.
            from gateway.routes.email.signature import \
                build_signed_bodies  # noqa: PLC0415
            sig_row = (await db.execute(text(
                "SELECT signature FROM email_assistant_settings "
                "WHERE account_id = :aid"
            ), {"aid": req.account_id})).fetchone()
            signature = (sig_row.signature if sig_row else "") or ""
            recips = drow.to_addresses if isinstance(drow.to_addresses, list) \
                else json.loads(drow.to_addresses or "[]")
            to = [a.get("email") for a in recips if a.get("email")]

            async def _send_new_and_trash() -> None:
                # Fallback (e.g. IMAP): send a fresh message threaded via the
                # reply reference, then bin the leftover draft.
                send_text, send_html = build_signed_bodies(
                    signature, drow.body_text or "", None)
                await provider.send_message(
                    to=to, subject=drow.subject or "",
                    body_text=send_text, body_html=send_html,
                    reply_to_message_id=drow.thread_id or None,
                    thread_id=drow.thread_id or None)
                try:
                    await provider.trash_message(drow.provider_message_id)
                except Exception:  # noqa: BLE001
                    pass

            if signature.strip():
                send_text, send_html = build_signed_bodies(
                    signature, drow.body_text or "", None)
                try:
                    await provider.update_draft(
                        drow.provider_message_id, to=to or None,
                        subject=drow.subject or None,
                        body_text=send_text, body_html=send_html,
                        thread_id=drow.thread_id or None)
                    await provider.send_draft(drow.provider_message_id)
                except NotImplementedError:
                    await _send_new_and_trash()
            else:
                try:
                    await provider.send_draft(drow.provider_message_id)
                except NotImplementedError:
                    await _send_new_and_trash()
            # The draft has left the mailbox — remove the local row.
            await db.execute(
                text("DELETE FROM email_messages WHERE id = :id"),
                {"id": req.draft_id},
            )
        # Reply complete: learn from the sent body and move the thread out of
        # "Reply" → Awaiting Reply (same hooks as the full /send path).
        if drow.thread_id:
            from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
                _mark_thread_replied,
            )
            background.add_task(
                _learn_from_sent, req.account_id, drow.thread_id,
                drow.body_text or "")
            background.add_task(
                _mark_thread_replied, req.account_id, drow.thread_id,
                drow.body_text or "", drow.subject or "")
            # Trash any other drafts left in the thread (e.g. an AI draft).
            background.add_task(
                _cleanup_thread_drafts, req.account_id, drow.thread_id)
        return {"sent": True}


class SaveDraftRequest(BaseModel):
    account_id: str
    message_id: str  # the email being replied to (for thread + recipient)
    body: str


@router.post("/drafts/save")
async def save_draft(
    req: SaveDraftRequest,
    user: UserContext = Depends(get_current_user),
):
    """Save an explicit (possibly user-edited) reply body as a provider draft.

    Powers the chat's interactive draft card: the assistant proposes a draft,
    the user edits it inline, then saves it to their Drafts folder verbatim. The
    draft is mirrored locally so it shows in Drafts/in-thread immediately."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        row = (await db.execute(text(
            "SELECT subject, thread_id, from_address FROM email_messages "
            "WHERE id = :mid AND account_id = :aid"
        ), {"mid": req.message_id, "aid": req.account_id})).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        frm = row.from_address if isinstance(row.from_address, dict) \
            else json.loads(row.from_address or "{}")
        # Signature-in-body (idempotent): the saved draft shows it upstream too.
        body = append_signature_text(
            await _account_signature(db, req.account_id), req.body)
        async with provider_session(
            db, user.email or "anonymous",
            message_id=req.message_id, require_auth=False,
        ) as sess:
            if not sess.authed:
                return {"created": False, "reason": "auth failed"}
            subject = row.subject or ""
            re_subject = (
                subject if subject.lower().startswith("re:") else f"Re: {subject}"
            )
            to_email = frm.get("email", "")
            provider_id = await sess.provider.create_draft(
                to=[to_email],
                subject=re_subject,
                body_text=body,
                reply_to_message_id=sess.provider_message_id,
                thread_id=row.thread_id or None,
            )
            local_id = await _upsert_local_draft(
                db, req.account_id, provider_id, thread_id=row.thread_id,
                owner_email="", to_email=to_email,
                subject=re_subject, body=body,
            )
        return {"created": True, "id": local_id}
