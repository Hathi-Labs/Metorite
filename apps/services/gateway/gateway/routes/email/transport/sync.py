"""Transport · sync — manual/initial sync, resync, and the Microsoft Graph
webhook + change-subscription lifecycle."""

from __future__ import annotations

import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from gateway.db import bind_tenant, current_tenant, release_tenant
from gateway.routes.email.core import (
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _tenant_session,
    router,
)
from gateway.routes.email.transport.signing import sign_webhook_org, verify_webhook_org
from pydantic import BaseModel
from sqlalchemy import text


async def _build_label_rule_map(
    db: Any, account_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Returns ``(label_rule_map, conv_rule_keys)``.

    ``label_rule_map``: category-name (lower) → rule_id, from each enabled rule's
    name and its LABEL action labels — so a manually-applied category can be
    traced to a rule. ``conv_rule_keys``: rule_id → conversation-status key
    (REPLY / AWAITING_REPLY / FYI / DONE) for the reply-status rules, so
    the learner can treat those differently from sender-stable cleanup rules."""
    try:
        from gateway.routes.email.automation.engine import (  # noqa: PLC0415
            _conversation_rule_key,
        )
        from gateway.routes.email.automation.rules import (  # noqa: PLC0415
            _load_rules,
        )
        rules = await _load_rules(db, account_id)
    except Exception:  # noqa: BLE001
        return {}, {}
    out: dict[str, str] = {}
    conv: dict[str, str] = {}
    for r in rules:
        if not r.get("enabled"):
            continue
        rid = str(r.get("id"))
        ck = _conversation_rule_key(r)
        if ck:
            conv[rid] = ck
        nm = (r.get("name") or "").strip().lower()
        if nm:
            out.setdefault(nm, rid)
        for a in r.get("actions", []) or []:
            label = (a.get("label") or "").strip()
            if a.get("type") == "LABEL" and label:
                out.setdefault(label.lower(), rid)
    return out, conv


async def _learn_from_label_changes(
    db: Any, account_id: str, msg: Any,
    old_categories: list, new_categories: list, label_rule_map: dict[str, str],
    conv_rule_keys: dict[str, str], status_corrections: dict[str, str],
) -> None:
    """When the user adds/removes a label (category) in their email client, learn
    a FROM include/exclude classification pattern for the matching rule —
    inbox-zero's LABEL_ADDED / LABEL_REMOVED learning. Best-effort.

    Our own rule-applied labels are written to local categories synchronously, so
    a sync delta here reflects only changes the *user* made in their client.

    Conversation-status rules (Reply / Awaiting / FYI / Done) are NEVER
    sender-pinned: a person's mail flows between those states per-thread, so a
    learned FROM pattern would be both wrong ("always reply to X") and futile
    (status is re-derived from the full thread and overrides it). This mirrors the
    guard the Fix and auto-learn paths already apply. Instead, a manually-ADDED
    conversation label is recorded in ``status_corrections`` (thread → status) so
    the caller can set the thread status directly — the only correction that
    sticks — exactly as the Fix flow does. A removed conversation label carries no
    unambiguous target status, so it's simply ignored (never pins, never guesses)."""
    sender = ""
    if getattr(msg, "from_address", None):
        sender = (msg.from_address.email or "").strip()
    if not label_rule_map:
        return
    old_s = {str(c).strip().lower() for c in (old_categories or [])}
    new_s = {str(c).strip().lower() for c in (new_categories or [])}
    added, removed = new_s - old_s, old_s - new_s
    if not added and not removed:
        return
    try:
        from gateway.routes.email.automation.rules import (  # noqa: PLC0415
            _upsert_rule_pattern,
        )
    except Exception:  # noqa: BLE001
        return
    tid = getattr(msg, "thread_id", None)
    for cat, exclude, src, why in (
        *[(c, False, "LABEL_ADDED", "Label added in the mail client")
          for c in added],
        *[(c, True, "LABEL_REMOVED", "Label removed in the mail client")
          for c in removed],
    ):
        rid = label_rule_map.get(cat)
        if not rid:
            continue
        conv_key = conv_rule_keys.get(rid)
        if conv_key:
            # Reply-status rule: set the thread status directly on an ADD; never
            # learn a sender pattern (and skip an ambiguous removal entirely).
            if not exclude and tid:
                status_corrections[tid] = conv_key
            continue
        if not sender:
            continue
        try:
            await _upsert_rule_pattern(
                db, account_id, rid, sender, exclude, src, why, None, tid,
                pattern_type="FROM")
        except Exception:  # noqa: BLE001
            pass


async def _apply_label_status_corrections(
    account_id: str, corrections: dict[str, str],
) -> None:
    """Apply the reply-status corrections the label learner queued (a user who
    manually added a conversation label — Reply / Awaiting / FYI / Done —
    in their client). Each opens its own DB connection and swaps provider labels,
    so this runs AFTER the sync's persistence commit, deduped by thread.
    Best-effort — a failed correction never fails the sync."""
    if not corrections:
        return
    from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
        apply_thread_status_correction,
    )
    for tid, key in corrections.items():
        try:
            await apply_thread_status_correction(account_id, tid, key)
        except Exception as exc:  # noqa: BLE001
            _log.warning("email.label_status_correction_failed",
                         account_id=account_id, error=str(exc)[:160])


async def learn_from_label_change_events(
    account_id: str, changes: list,
) -> None:
    """Learn FROM-classification patterns from ``(message, old_categories)``
    pairs captured during a sync's persist — the user's manual label add/removes,
    seen as a category delta between what a message HAD stored and what the
    provider now reports.

    This is the shared orchestration the scheduler's post-sync hook runs so the
    BACKGROUND sync path learns exactly like the manual-sync route's inline pass
    does. Before this, only the manual route learned; the scheduler — which is
    what actually polls every ~300s — never did, so every label change a user
    made in their client between manual syncs was silently dropped.

    Best-effort and idempotent-ish: an empty map (no rules) or empty ``changes``
    is a no-op; ``_learn_from_label_changes`` guards each pattern write.

    EM-T1b-1 item 7: the pattern writes run in ONE ``_tenant_session()`` with
    no ``commit()``, so they read the bound organization. The seam commits on
    exit. The status corrections run AFTER the block, because each one opens
    its own session and calls the provider.
    """
    if not changes:
        return
    status_corrections: dict[str, str] = {}
    async with _tenant_session() as db:
        label_rule_map, conv_rule_keys = await _build_label_rule_map(
            db, account_id)
        if not label_rule_map:
            return
        for msg, old_categories in changes:
            await _learn_from_label_changes(
                db, account_id, msg, old_categories,
                getattr(msg, "categories", []) or [], label_rule_map,
                conv_rule_keys, status_corrections)
    await _apply_label_status_corrections(account_id, status_corrections)


class SyncRequest(BaseModel):
    account_id: str
    # Force a deep re-sync (all folders, back to the import floor) even if the
    # initial sync already ran — e.g. a manual "resync everything" from the UI.
    full: bool = False


@router.post("/sync")
async def trigger_sync(
    req: SyncRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Trigger a manual email sync for an account.

    Thin wrapper over the ONE sync core — ``email_ingestion.scheduler.
    _sync_account``, the same code the background scheduler and the Graph
    webhook run — so the manual path can never drift from it again. The old
    inline copy of fetch → persist → reconcile → watermark is exactly how the
    manual path lost rotated refresh tokens and wiped the Outlook sync cursor
    (review §3.2). Ownership is checked HERE (the core is user-blind);
    sync-status/sync-log bookkeeping, immediate cred rotation, cursor COALESCE,
    the deep-vs-incremental heuristic and label-change capture (the
    ``learn_label_changes`` hook) all live in the core.
    """
    async with _tenant_session() as db:
        own = (await db.execute(text(
            "SELECT id FROM email_accounts WHERE id = :id AND user_id = :uid"
        ), {"id": req.account_id, "uid": user.email or "anonymous"})).fetchone()
        if not own:
            raise HTTPException(status_code=404, detail="Account not found")
    return await _run_manual_sync(req.account_id, background, full=req.full)


async def _run_manual_sync(
    account_id: str, background: BackgroundTasks, *, full: bool,
) -> dict[str, Any]:
    """Run one manual sync through the shared core and shape the API response.

    ``full=True`` forces the deep (all-folder) backfill back to the import
    floor of the mailbox (EM-T6a); ``False`` keeps the core's own heuristic
    (deep on first sync, shallow after). New
    mail is handed to the shared new-mail pipeline AFTER the response, exactly
    like the scheduler and webhook paths (H1). A core-reported failure (auth,
    provider, DB) surfaces as the same 500 the old inline body raised; the core
    has already stamped sync_status='error' and the sync-log row itself.
    """
    from email_ingestion.scheduler import _sync_account  # noqa: PLC0415

    res = await _sync_account(account_id, deep=True if full else None)
    if not isinstance(res, dict) or res.get("error"):
        err = res.get("error") if isinstance(res, dict) else "unknown"
        raise HTTPException(status_code=500, detail=f"Sync failed: {err}")
    synced = int(res.get("synced") or 0)
    if synced:
        from gateway.routes.email.scheduler_hooks import (  # noqa: PLC0415
            process_new_mail,
        )
        background.add_task(process_new_mail, account_id)
    return {"ok": True, "messages_synced": synced, "messages_skipped": 0}


@router.post("/accounts/{account_id}/resync")
async def resync_account(
    account_id: str,
    background: BackgroundTasks,
    purge: bool = Query(False),
    user: UserContext = Depends(get_current_user),
):
    """Force a COMPLETE, DEEP re-sync from the provider (not just an incremental
    sync).

    Resets the sync cursor and re-fetches every folder with the deep backfill
    (``full=True``), back to the import floor that the member chose (EM-T6a),
    overwriting stale local fields. This is the only UI path that re-runs the
    deep backfill on an account whose
    ``initial_sync_done`` is already set (the recurring poll and a plain sync
    stay shallow), so it's how an account connected before deep-sync shipped
    pulls its history back to the floor. With ``purge=true`` it first DELETES
    the account's local messages (cascades attachments) before re-fetching —
    use this when
    local data is corrupt or badly out of sync. Returns the sync result."""
    async with _tenant_session() as db:
        own = (await db.execute(text(
            "SELECT id FROM email_accounts WHERE id = :id AND user_id = :uid"
        ), {"id": account_id, "uid": user.email or "anonymous"})).fetchone()
        if not own:
            raise HTTPException(status_code=404, detail="Account not found")
        if purge:
            await db.execute(text(
                "DELETE FROM email_messages WHERE account_id = :id"
            ), {"id": account_id})
        # Reset the cursor so the provider does a full sweep (defensive — sync is
        # full-sweep regardless, but this also clears any stale delta token).
        await db.execute(text(
            "UPDATE email_accounts SET last_history_id = NULL, updated_at = now() "
            "WHERE id = :id"
        ), {"id": account_id})
    # Re-fetch through the shared core, forcing the DEEP (all-folder) backfill
    # to the import floor — ``full=True`` overrides the ``initial_sync_done``
    # gate so an already-initialised account actually pulls its older mail
    # instead of just the newest shallow page. (This used to call the
    # trigger_sync ROUTE
    # directly with the wrong positional args — ``user`` landed in the
    # ``background`` slot and ``user`` stayed an unresolved Depends — so every
    # direct resync crashed with a 500 before reaching the provider. Calling
    # the shared helper instead of a route handler is the structural fix.)
    result = await _run_manual_sync(account_id, background, full=True)
    return {"resynced": True, "purged": purge,
            "messages_synced": result.get("messages_synced")}


async def _webhook_sync(account_id: str, organization_id: str) -> None:
    """Triggered by a Graph notification: incremental sync, then the shared
    new-mail pipeline (auto-run rules → categorize → classify → auto-archive) —
    the same pipeline the scheduler and manual sync use, so push-delivered mail
    is processed identically.

    The webhook verified ``organization_id`` from the signed
    ``notificationUrl`` and matched the account inside that tenant. This task
    binds it for its whole run and resets it in ``finally``. ``_sync_account``
    and the steps of ``process_new_mail`` that open ``tenant_session()`` read
    that binding.
    """
    token = bind_tenant(organization_id)
    try:
        from email_ingestion.scheduler import _sync_account
        from gateway.routes.email.scheduler_hooks import process_new_mail
        res = await _sync_account(account_id)
        if isinstance(res, dict) and res.get("synced", 0):
            await process_new_mail(account_id)
    except Exception as exc:
        _log.warning("email.webhook_sync_failed", account_id=account_id,
                     error=str(exc)[:200])
    finally:
        release_tenant(token)


def _client_state_matches(sent: Any, stored: Any) -> bool:
    """Whether a notification carries the ``clientState`` of the account.

    A stored value that is NULL or empty refuses every notification (EM-T1a
    item 6). The old check skipped the comparison when the stored value was
    empty, so any caller who knew a subscription id could start a sync.
    """
    if not stored or not isinstance(sent, str) or not sent:
        return False
    return hmac.compare_digest(sent.encode("utf-8"), str(stored).encode("utf-8"))


@router.api_route("/webhook/microsoft", methods=["GET", "POST"])
async def microsoft_webhook(request: Request, background: BackgroundTasks):
    """Public Microsoft Graph change-notification endpoint (no session).

    1. It echoes ``validationToken`` first and unchanged. Graph sends the
       token when it creates a subscription, and it waits 10 seconds at most.
    2. It reads ``org`` and ``sig`` from its own URL. ``_ensure_subscription``
       wrote them into the ``notificationUrl``, and only a holder of the
       secret can sign them. A missing, malformed or unsigned ``org`` answers
       202 and queues nothing (EM-T1a item 5, R11).
    3. It matches each subscription id and its ``clientState`` inside ONE
       ``tenant_session(org)``. A subscription of another organization is
       invisible there, so it matches nothing.
    """
    token = request.query_params.get("validationToken")
    if token:
        return PlainTextResponse(content=token, status_code=200)
    org = verify_webhook_org(
        request.query_params.get("org"), request.query_params.get("sig"),
    )
    if org is None:
        _log.warning("email.webhook_unsigned")
        return PlainTextResponse("", status_code=202)
    try:
        body = await request.json()
    except Exception:
        return PlainTextResponse("", status_code=202)
    notifications = body.get("value", []) if isinstance(body, dict) else []
    pending = [
        (n.get("subscriptionId"), n.get("clientState"))
        for n in notifications
        if isinstance(n, dict) and n.get("subscriptionId")
    ]
    if not pending:
        return PlainTextResponse("", status_code=202)
    affected: set[str] = set()
    try:
        async with _tenant_session(org) as db:
            for sub_id, client_state in pending:
                row = (await db.execute(text(
                    "SELECT id, webhook_client_state FROM email_accounts "
                    "WHERE webhook_subscription_id = :sid"
                ), {"sid": str(sub_id)})).fetchone()
                if not row:
                    continue
                if not _client_state_matches(client_state, row.webhook_client_state):
                    _log.warning("email.webhook_bad_client_state",
                                 sub=str(sub_id)[:12])
                    continue
                affected.add(str(row.id))
    except Exception as exc:
        _log.warning("email.webhook_lookup_failed", error=str(exc)[:200])
        return PlainTextResponse("", status_code=202)
    for aid in affected:
        background.add_task(_webhook_sync, aid, org)
    return PlainTextResponse("", status_code=202)


def _notification_url(public: str, org: str) -> str:
    """The ``notificationUrl`` of a Graph subscription for ``org``.

    The path stays ``/email/webhook/microsoft``, so it still matches
    ``PUBLIC_ROUTES`` and the feature-gate exemption. The organization rides
    in the query with its signature.
    """
    query = urlencode({"org": org, "sig": sign_webhook_org(org)})
    return f"{public}/email/webhook/microsoft?{query}"


async def _ensure_subscription(account_id: str) -> None:
    """Create or renew the account's Graph push subscription (Microsoft only).

    ⚠️ **It binds a tenant or does nothing (EM-T1a item 7).** It reads
    ``current_tenant()``. When no tenant is bound, it logs
    ``email.subscription_unbound`` and returns before any DB call and any
    Graph call. The scheduler binds no tenant until EM-T1b, so the scheduled
    hook does nothing until then. That is the safe failure.

    A renewal also writes the ``notificationUrl``, so a subscription from
    before EM-T1a gets the signed ``org`` (spec risk R-3). When Graph refuses
    the renewal, the code deletes the subscription and creates a new one.
    """
    org = current_tenant()
    if not org:
        _log.warning("email.subscription_unbound", account_id=account_id)
        return
    public = (
        os.environ.get("GATEWAY_PUBLIC_URL", "")
        or getattr(get_settings(), "gateway_public_url", "")
    ).rstrip("/")
    if not public:
        return
    try:
        notify_url = _notification_url(public, org)
        async with _tenant_session(org) as db:
            row = (await db.execute(text(
                """SELECT provider, credentials_encrypted, webhook_subscription_id,
                          webhook_client_state, webhook_expires_at
                   FROM email_accounts WHERE id = :id"""
            ), {"id": account_id})).fetchone()
            if not row or row.provider != "microsoft":
                return
            now = datetime.now(timezone.utc)
            if (row.webhook_subscription_id and row.webhook_expires_at
                    and row.webhook_expires_at > now + timedelta(hours=12)):
                return  # still valid, not expiring soon

            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))
            provider = _instantiate_provider("microsoft", creds)
            if not await provider.authenticate():
                return
            client_state = row.webhook_client_state or secrets.token_urlsafe(24)
            data, sub_id = await _renew_or_replace(
                provider, row.webhook_subscription_id, notify_url, client_state,
            )
            # Graph returns expirationDateTime as an ISO string; asyncpg needs a
            # real datetime for the TIMESTAMPTZ column.
            exp_raw = data.get("expirationDateTime")
            exp_dt = None
            if exp_raw:
                try:
                    exp_dt = datetime.fromisoformat(
                        str(exp_raw).replace("Z", "+00:00")
                    )
                except Exception:
                    exp_dt = None
            await _persist_rotated_creds(db, store, account_id, provider)
            await db.execute(text(
                """UPDATE email_accounts
                   SET webhook_subscription_id = :sid, webhook_client_state = :cs,
                       webhook_expires_at = :exp, updated_at = now()
                   WHERE id = :id"""
            ), {"id": account_id, "sid": sub_id, "cs": client_state, "exp": exp_dt})
        _log.info("email.subscription_ready", account_id=account_id,
                  sub=str(sub_id)[:12], expires=exp_raw)
    except Exception as exc:
        _log.warning("email.subscription_failed", account_id=account_id,
                     error=str(exc)[:200])


async def _renew_or_replace(
    provider: Any, sub_id: str | None, notify_url: str, client_state: str,
) -> tuple[dict[str, Any], str | None]:
    """Renew ``sub_id`` with the signed URL, or replace it.

    Graph v1.0 accepts ``notificationUrl`` in a subscription PATCH. When the
    PATCH fails for any reason, the old subscription is deleted (best effort)
    and a new one is created, so a refused URL update cannot leave an
    unsigned URL in place.
    """
    if sub_id:
        try:
            return (
                await provider.renew_subscription(
                    sub_id, notification_url=notify_url,
                ),
                sub_id,
            )
        except Exception:
            await provider.delete_subscription(sub_id)
    data = await provider.create_subscription(notify_url, client_state)
    return data, data.get("id")
