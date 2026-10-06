"""Transport · webhook — the public Meta Cloud API endpoint.

Two verbs, no auth (Meta calls it):

* ``GET  /whatsapp/webhook`` — the subscription handshake. Meta sends
  ``hub.mode/hub.verify_token/hub.challenge``; we echo the challenge when the
  token matches the configured one.
* ``POST /whatsapp/webhook`` — the event feed. We verify the
  ``X-Hub-Signature-256`` HMAC over the RAW body, parse it, resolve the owning
  account AND its tenant by ``phone_number_id``, bind that tenant, persist
  idempotently, and fire the post-sync hooks. We return 200 fast so Meta
  doesn't retry a slow-but-successful batch.

WS-20 WA-C1 (``whatsapp_message_manager.md`` §12.4). Meta sends no member
session, so the POST binds the tenant that owns the number. The owner comes
from ``wa_account_for_phone_number_id``, a SECURITY DEFINER read that sees
through FORCE RLS. Before WA-C1 the route read ``wa_accounts`` unbound, saw no
row, and dropped every batch as an unknown number (F1).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from acb_common import get_logger, get_settings
from fastapi import Request, Response
from gateway.db import bind_tenant, release_tenant
from gateway.routes.whatsapp.core import (
    _get_db,
    _tenant_session,
    fire_post_sync_hooks,
    router,
)
from sqlalchemy import text

_log = get_logger("gateway.whatsapp.webhook")


def verify_signature(app_secret: str | None, raw_body: bytes, header: str | None) -> bool:
    """Verify Meta's ``X-Hub-Signature-256: sha256=<hex>`` over the raw body.

    Pure + unit-testable. When no ``app_secret`` is configured we return True and
    log a warning (dev/self-host without the secret set). A malformed/missing
    header with a secret present fails closed. ⚠️ The route, not this function,
    refuses a missing secret outside dev (F8, ``receive_webhook``).
    """
    if not app_secret:
        _log.warning("whatsapp.webhook.signature_unverified_no_secret")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    provided = header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta subscription verification handshake."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")
    configured = os.environ.get("WHATSAPP_VERIFY_TOKEN")

    ok_token = configured and token == configured
    if not ok_token:
        # Fall back to matching any stored per-account verify token.
        # H4/H6: service-identity route — Meta calls this with no session and
        # no ambient tenant; the lookup is deliberately cross-account, and a
        # tenant would have to come from the matched wa_accounts row.
        db = await _get_db()
        try:
            row = (await db.execute(
                text("""SELECT 1 FROM wa_accounts
                        WHERE webhook_verify_token = :t LIMIT 1"""),
                {"t": token},
            )).fetchone()
            ok_token = bool(row)
        finally:
            await db.close()

    if mode == "subscribe" and ok_token:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403, content="verification failed")


async def _resolve_account(phone_number_id: str | None) -> tuple[str, str] | None:
    """Which Cloud API account, and which tenant, own a Meta number.

    Tenant DISCOVERY, so the read cannot run bound: the organization is its
    answer. It runs on an unbound session and reads only through the SECURITY
    DEFINER function of the WA-C1 migration, which sees through FORCE RLS and
    returns ``(account_id, organization_id)``. Only ``acb_app`` may run it.

    Returns ``None`` when no Cloud API account holds the number, when the row
    has no tenant, or when the function cannot see through RLS (it then
    returns no row and warns in the Postgres log). The index of the same
    migration makes a Cloud API number unique, so two rows cannot happen.
    """
    if not phone_number_id:
        return None
    db = await _get_db()
    try:
        rows = (await db.execute(
            text("""SELECT account_id, organization_id
                    FROM public.wa_account_for_phone_number_id(:pnid)"""),
            {"pnid": phone_number_id},
        )).fetchall()
    finally:
        await db.close()
    if len(rows) != 1 or rows[0].organization_id is None:
        return None
    return str(rows[0].account_id), str(rows[0].organization_id)


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Ingest a Meta event batch: verify → split by number → for each number,
    resolve its tenant → persist → hooks."""
    raw = await request.body()
    app_secret = os.environ.get("WHATSAPP_APP_SECRET")
    if not app_secret and get_settings().acb_env != "dev":
        # F8: with no secret, verify_signature accepts any body. Outside dev
        # that lets anyone write messages into a connected number's inbox.
        _log.warning("whatsapp.webhook.refused_no_app_secret")
        return Response(status_code=403, content="webhook not configured")
    if not verify_signature(
        app_secret, raw, request.headers.get("X-Hub-Signature-256"),
    ):
        return Response(status_code=403, content="bad signature")

    import json
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return Response(status_code=400, content="invalid json")

    groups, unrouted = split_by_number(payload)
    if unrouted:
        _log.warning("whatsapp.webhook.changes_without_number", changes=unrouted)
    # Every customer WABA subscribes the one Tech Provider app, so one POST
    # can carry several numbers, and so several tenants. Each number is one
    # unit: resolve its owner, bind that org, persist, fire the hooks. One
    # failed unit must not stop the others. The persist path is idempotent on
    # `wa_message_id`, so the 500 below makes Meta send the whole batch again
    # and the units that landed write nothing new.
    failed = 0
    for phone_number_id, sub_payload in groups.items():
        try:
            await _ingest_number(phone_number_id, sub_payload)
        except Exception as exc:
            failed += 1
            _log.warning(
                "whatsapp.webhook.number_failed",
                phone_number_id=phone_number_id, error=str(exc)[:200],
            )
    if failed:
        return Response(status_code=500, content="retry")
    return Response(status_code=200, content="ok")


def split_by_number(payload: Any) -> tuple[dict[str, dict[str, Any]], int]:
    """Split a webhook body into one sub-payload for each Meta number.

    The key is ``change.value.metadata.phone_number_id``. Each sub-payload
    keeps Meta's ``entry`` / ``changes`` shape, so ``parse_webhook`` reads it
    as it reads a full body. A change with no number goes to no group and is
    counted in the second value. Total: a malformed body gives no groups and
    never raises.

    The parser keeps only the FIRST number of a batch. Before this split, a
    batch that carried org A's number first put org B's messages under org
    A's account (WA-C1 review P0).
    """
    groups: dict[str, dict[str, Any]] = {}
    # The index of the source entry that each group's last entry copies.
    last_source: dict[str, int] = {}
    unrouted = 0
    if not isinstance(payload, dict):
        return groups, unrouted
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return groups, unrouted
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            value = change.get("value") if isinstance(change, dict) else None
            meta = value.get("metadata") if isinstance(value, dict) else None
            pnid = meta.get("phone_number_id") if isinstance(meta, dict) else None
            if not isinstance(pnid, str) or not pnid:
                unrouted += 1
                continue
            group = groups.setdefault(pnid, {
                "object": payload.get("object"), "entry": [],
            })
            # One entry per source entry, in order, so the shape stays Meta's.
            if last_source.get(pnid) != index:
                group["entry"].append({"id": entry.get("id"), "changes": []})
                last_source[pnid] = index
            group["entry"][-1]["changes"].append(change)
    return groups, unrouted


async def _ingest_number(phone_number_id: str, sub_payload: dict[str, Any]) -> None:
    """Persist one number's part of a batch under the tenant that owns it.

    An unknown number writes nothing and is not an error.
    """
    from whatsapp_ingestion.persist import persist_sync_result
    from whatsapp_ingestion.providers.webhook import parse_webhook

    result = parse_webhook(sub_payload)
    if result.errors:
        # The parser is total (never raises); surface malformed changes instead of
        # swallowing them silently.
        _log.warning("whatsapp.webhook.parse_errors", errors=result.errors[:5])

    owner = await _resolve_account(phone_number_id)
    if owner is None:
        _log.warning(
            "whatsapp.webhook.unknown_number", phone_number_id=phone_number_id,
        )
        return
    account_id, organization_id = owner

    # Bind the account's tenant for the persist AND the hooks, and release it
    # in `finally` (the shape of `routes/email/transport/sync.py`
    # `_webhook_sync`). `run_hook` awaits each hook in this task, so the two
    # hooks' own `_tenant_session()` reads this binding.
    token = bind_tenant(organization_id)
    try:
        async with _tenant_session() as db:
            counts = await persist_sync_result(db, account_id, result)

        # Fire the shared post-sync pipeline (same brain the whatsmeow bridge uses).
        await fire_post_sync_hooks(account_id, counts)

        _log.info(
            "whatsapp.webhook.ingested",
            account_id=account_id, messages=counts["messages"],
            statuses=len(result.statuses),
        )
    finally:
        release_tenant(token)
