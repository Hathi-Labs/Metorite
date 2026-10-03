"""Automation · follow-ups — nudges for threads still awaiting a reply.

The follow-up scan endpoint and the scheduler's reminder job. Moved out of
``replyzero.py`` (2.3 split): follow-ups CONSUME thread status (Awaiting
Reply), they don't decide it.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.email.automation.assistant import _load_assistant_about
from gateway.routes.email.automation.drafting import (
    _agent_draft_reply,
    _is_no_draft,
)
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.automation.replyzero import (
    _FOLLOW_UP_LABEL,
    _business_days_cutoff,
)
from gateway.routes.email.core import (
    _assert_account_owner,
    _tenant_session,
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _savepoint,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class FollowUpScanRequest(BaseModel):
    account_id: str


@router.post("/follow-ups/scan")
async def scan_follow_ups(
    req: FollowUpScanRequest,
    user: UserContext = Depends(get_current_user),
):
    """On-demand "Find follow-ups" (inbox-zero parity): scan now for threads
    waiting too long for a reply, label them "Follow-up", and — when auto-draft
    is on — draft nudges. Returns ``{configured, scanned, labeled, drafted}``.

    Respects the configured reminder windows; if neither is set, returns
    ``configured: false`` so the UI can prompt the user to set them first."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    return await _maybe_send_follow_up_reminders(req.account_id)


# How stale a thread may be and still be worth chasing. The reminder answers
# "you're still waiting on this" — useful about last week, noise about last
# autumn. Without a ceiling, the first run after this job was repaired would
# have chased threads back to October 2025 and, with auto-draft on, written an
# AI nudge for each. Threads past the ceiling are simply left alone; if one
# comes back to life, _upsert_thread_status re-arms follow_up_reminded_at and it
# becomes eligible again on its own merits.
_FOLLOW_UP_MAX_AGE_DAYS = 30


async def _maybe_send_follow_up_reminders(account_id: str) -> dict[str, int | bool]:
    """Label (and optionally draft a nudge for) threads waiting too long for a
    reply. Runs both on the sync loop (background) and on demand via the
    "Find follow-ups" button (POST /email/follow-ups/scan).

    AWAITING  → they haven't replied to us after follow_up_awaiting_days.
    NEEDS_REPLY → we haven't replied after follow_up_needs_reply_days.
    Each qualifying thread's latest message is labelled "Follow-up"; when
    follow_up_auto_draft is on, AWAITING threads also get a draft nudge.
    Idempotent via email_thread_status.follow_up_reminded_at.

    Returns ``{configured, scanned, labeled, drafted}`` so the UI can report the
    scan outcome (the scheduler ignores the return value).

    EM-T1b-2 (``email_app_master_plan.md`` §10.4.2): ONE ``_tenant_session()``
    with the ambient tenant, and no ``commit()``. The sync loop binds the
    tenant, and the scan route calls this inside its request. The block holds
    its session across the draft model call and ``create_draft``, as the code
    did before, and EM-T4 owns that I/O.
    """
    result: dict[str, int | bool] = {
        "configured": False, "scanned": 0, "labeled": 0, "drafted": 0,
    }
    try:
        async with _tenant_session() as db:
            srow = (await db.execute(text(
                """SELECT follow_up_awaiting_days, follow_up_needs_reply_days,
                          follow_up_auto_draft
                   FROM email_assistant_settings WHERE account_id = :aid"""
            ), {"aid": account_id})).fetchone()
            if not srow:
                return result
            awaiting_days = float(getattr(srow, "follow_up_awaiting_days", 0) or 0)
            needs_days = float(getattr(srow, "follow_up_needs_reply_days", 0) or 0)
            auto_draft = bool(getattr(srow, "follow_up_auto_draft", False))
            if awaiting_days <= 0 and needs_days <= 0:
                return result
            result["configured"] = True

            # Business-day windows (inbox-zero parity) — don't chase over the
            # weekend.
            cutoff_aw = _business_days_cutoff(awaiting_days) if awaiting_days > 0 else None
            cutoff_nd = _business_days_cutoff(needs_days) if needs_days > 0 else None

            # A bare ``:param IS NOT NULL`` gives asyncpg no type to infer from,
            # so this whole statement raised AmbiguousParameterError on EVERY
            # sync — caught by the handler below, logged as a warning nobody
            # reads, and the follow-up feature was silently dead in production
            # for its entire life. The NULL guards were redundant anyway:
            # ``last_message_at < NULL`` is NULL, which is not TRUE, so an
            # unconfigured window already excludes its own branch. The explicit
            # casts keep the types unambiguous by construction rather than by
            # inference.
            #
            # ``:floor`` is a staleness ceiling — see _FOLLOW_UP_MAX_AGE_DAYS. A
            # nudge about last week is useful; one about last autumn is noise,
            # and the only reason such threads are queued at all is that this
            # job has been broken long enough for them to pile up.
            rows = (await db.execute(text(
                """SELECT ts.thread_id, ts.status, ts.last_message_id,
                          em.provider_message_id, em.subject, em.from_address,
                          em.to_addresses, em.body_text, em.snippet
                   FROM email_thread_status ts
                   LEFT JOIN email_messages em ON ts.last_message_id = em.id
                   WHERE ts.account_id = :aid
                     AND ts.follow_up_reminded_at IS NULL
                     AND ts.last_message_at > CAST(:floor AS timestamptz)
                     AND (
                       (ts.status = 'AWAITING'
                        AND ts.last_message_at < CAST(:caw AS timestamptz))
                       OR (ts.status = 'NEEDS_REPLY'
                        AND ts.last_message_at < CAST(:cnd AS timestamptz))
                     )
                   ORDER BY ts.last_message_at DESC
                   LIMIT 50"""
            ), {"aid": account_id, "caw": cutoff_aw, "cnd": cutoff_nd,
                "floor": datetime.now(timezone.utc)
                - timedelta(days=_FOLLOW_UP_MAX_AGE_DAYS)})).fetchall()
            if not rows:
                return result

            acc = (await db.execute(text(
                "SELECT provider, credentials_encrypted, user_id "
                "FROM email_accounts WHERE id = :id"
            ), {"id": account_id})).fetchone()
            if not acc:
                return result
            from acb_llm.key_store import get_key_store  # noqa: PLC0415
            store = get_key_store()
            creds = json.loads(store.decrypt(acc.credentials_encrypted))
            provider = _instantiate_provider(acc.provider, creds)
            if not await provider.authenticate():
                return result
            about, signature = await _load_assistant_about(db, account_id)
            from gateway.routes.email.automation.assistant import (  # noqa: PLC0415
                _account_models,
            )
            fu_model = (await _account_models(db, account_id))["draft"]
            # A nudge speaks as THIS mailbox, the same as a reply (MB-14,
            # EM-T8e-1 review round 1). One read for the run.
            me = await resolve_self(db, account_id)

            for r in rows:
                mark = lambda: db.execute(text(  # noqa: E731
                    "UPDATE email_thread_status SET follow_up_reminded_at = now() "
                    "WHERE account_id = :aid AND thread_id = :tid"
                ), {"aid": account_id, "tid": r.thread_id})
                if not r.provider_message_id:
                    await mark()
                    continue
                # Apply the "Follow-up" tag on BOTH surfaces. Mirror it locally
                # (email_messages.categories) so it shows as a chip/filter in
                # the regular mailbox view at once — the reminder path used to
                # write the provider only, so the tag stayed invisible in-app
                # until the next sync pulled it back. Provider apply stays
                # best-effort on top.
                labeled_ok = False
                if r.last_message_id:
                    # A savepoint, because the failure is swallowed: without it
                    # the block stays aborted and the reminder stamp below is
                    # lost with it.
                    with contextlib.suppress(Exception):
                        async with _savepoint(db):
                            await db.execute(text(
                                "UPDATE email_messages SET categories = CASE "
                                "WHEN :lbl = ANY(categories) THEN categories "
                                "ELSE array_append(categories, :lbl) END, "
                                "updated_at = now() WHERE id = :id"
                            ), {"id": r.last_message_id, "lbl": _FOLLOW_UP_LABEL})
                        labeled_ok = True
                with contextlib.suppress(Exception):
                    await provider.set_labels(
                        r.provider_message_id, add=[_FOLLOW_UP_LABEL], remove=[])
                    labeled_ok = True
                if labeled_ok:
                    result["labeled"] += 1
                if auto_draft and r.status == "AWAITING":
                    try:
                        to_list = r.to_addresses if isinstance(r.to_addresses, list) \
                            else json.loads(r.to_addresses or "[]")
                        to = (to_list[0].get("email") if to_list else "") or ""
                        if to:
                            # Hydrate the FULL body of our own last message
                            # before drafting the nudge — a header-only Outlook
                            # row otherwise hands the drafter a ~200-char
                            # snippet cut off mid-sentence (the last surviving
                            # snippet-bug path; every other drafting entry point
                            # already hydrates).
                            from gateway.routes.email.core import (  # noqa: PLC0415
                                hydrate_message_body,
                            )
                            hb = ""
                            if r.last_message_id:
                                with contextlib.suppress(Exception):
                                    async with _savepoint(db):
                                        hb = await hydrate_message_body(
                                            db, str(r.last_message_id),
                                            acc.user_id)
                            email = {
                                "subject": r.subject or "",
                                "from": to,  # nudging the recipient of our last msg
                                "body": (hb or "").strip()
                                or r.body_text or r.snippet or "",
                                "thread_id": r.thread_id or "",
                                "self": me.address,
                                "self_label": me.label,
                            }
                            body = await _agent_draft_reply(
                                email, about, signature, acc.user_id,
                                use_agent=True, follow_up=True, model=fu_model,
                                account_id=account_id,
                            )
                            # Confidence gate (defense-in-depth): don't persist
                            # a declined / empty draft as a real provider draft.
                            if not _is_no_draft(body):
                                # Signature-in-body: the nudge draft shows the
                                # signature upstream too (idempotent).
                                from gateway.routes.email.signature import (  # noqa: PLC0415
                                    append_signature_text,
                                )
                                body = append_signature_text(signature, body)
                                await provider.create_draft(
                                    to=[to],
                                    subject=f"Re: {r.subject or ''}",
                                    body_text=body,
                                    reply_to_message_id=r.provider_message_id,
                                    thread_id=r.thread_id or None,
                                )
                                result["drafted"] += 1
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("email.follow_up_draft_failed",
                                     account_id=account_id, error=str(exc)[:160])
                await mark()

            result["scanned"] = len(rows)
            await _persist_rotated_creds(db, store, account_id, provider)
        _log.info("email.follow_ups_processed", account_id=account_id,
                  count=len(rows))
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.follow_up_failed", account_id=account_id,
                     error=str(exc)[:200])
    return result
