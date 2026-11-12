"""Inbound webhook trigger — the one public route in the package (spec F8).

``POST /workflows/hooks/{hook_token}``: the token IS the credential — an
unguessable per-workflow ``secrets.token_urlsafe(24)`` minted at creation.
Defense in depth on top of the token: optional HMAC (an enabled webhook
trigger whose config carries ``secret`` requires ``X-CC-Signature``, same
scheme as the agent webhook), a per-token rate limit, a body-size cap, and
the run only fires when the workflow is published with an enabled webhook
trigger. Listed in ``main.PUBLIC_ROUTES`` and the router's exempt set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from typing import Any

from fastapi import HTTPException, Request

# H4/H6 and H-328 part (c): a service-identity route, not a member request.
# The caller authenticates by the per-workflow `hook_token`, so no member
# resolves and no tenant is bound in context. The tenant is the org of the
# workflow row that the token names. The door loops over the orgs of
# `service._organization_ids()` (the one unbound read, a tenant-discovery
# site). It skips an org that is not on `WORKFLOW_UNATTENDED_ORGS` BEFORE it
# reads, and it reads the token in `_tenant_session(org)`. `hook_token` is
# UNIQUE over all orgs, so the first hit is the row. A token of an org that is
# not listed answers 404 "Unknown hook", the same as a bad token.
from gateway.routes.workflows import unattended
from gateway.routes.workflows.core import (
    HOOK_RATE_LIMIT_PER_MINUTE,
    MAX_TRIGGER_PAYLOAD_BYTES,
    _log,
    _tenant_session,
    parse_jsonb,
    router,
)
from gateway.routes.workflows.service import (
    _MEMBER_BLIND_REASON,
    DuplicateDelivery,
    RunRejected,
    _member_blind_write_nodes,
    _organization_ids,
    load_version_serialized,
    start_run,
)
from sqlalchemy import text

_RATE: dict[str, deque[float]] = {}

#: The longest delivery key that the door accepts. A longer key answers 400,
#: because a cut key could make two deliveries equal.
MAX_DELIVERY_KEY_CHARS = 200


def _rate_limited(token: str) -> bool:
    now = time.monotonic()
    bucket = _RATE.setdefault(token, deque())
    while bucket and now - bucket[0] > 60.0:
        bucket.popleft()
    if len(bucket) >= HOOK_RATE_LIMIT_PER_MINUTE:
        return True
    bucket.append(now)
    return False


def verify_hook_signature(secret: str, body: bytes, presented: str | None) -> None:
    """HMAC-SHA256 over the raw body — same scheme as the agent webhook."""
    supplied = (presented or "").strip()
    if supplied.startswith("sha256="):
        supplied = supplied[len("sha256=") :]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")


def delivery_key(request: Request) -> str | None:
    """The delivery id of this POST, or ``None`` when the sender sends none.

    The ``Idempotency-Key`` header wins, then ``X-CC-Delivery``. A key over
    :data:`MAX_DELIVERY_KEY_CHARS` answers 400. No key means no dedupe.
    """
    for name in ("Idempotency-Key", "X-CC-Delivery"):
        value = (request.headers.get(name) or "").strip()
        if not value:
            continue
        if len(value) > MAX_DELIVERY_KEY_CHARS:
            raise HTTPException(status_code=400, detail=f"{name} is too long")
        return value
    return None


async def _read_hook(org: str, hook_token: str) -> tuple[Any, Any, Any] | None:
    """The workflow, its webhook trigger and its version, in ONE org.

    Returns ``None`` when the token names no workflow of *org*.
    """
    async with _tenant_session(org) as db:
        row = (
            await db.execute(
                text(
                    "SELECT id, name, status, latest_version, variables, "
                    "organization_id FROM workflows WHERE hook_token = :token"
                ),
                {"token": hook_token},
            )
        ).fetchone()
        if row is None:
            return None
        trigger = (
            await db.execute(
                text(
                    "SELECT config FROM workflow_triggers "
                    "WHERE workflow_id = :wid AND kind = 'webhook' AND enabled "
                    "LIMIT 1"
                ),
                {"wid": str(row.id)},
            )
        ).fetchone()
        serialized = None
        if trigger is not None and row.status == "published" and row.latest_version:
            serialized = await load_version_serialized(
                db,
                str(row.id),
                int(row.latest_version),
            )
    return row, trigger, serialized


@router.post("/hooks/{hook_token}", status_code=202)
async def workflow_hook(hook_token: str, request: Request) -> dict[str, Any]:
    if _rate_limited(hook_token):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    body = await request.body()
    if len(body) > MAX_TRIGGER_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    key = delivery_key(request)

    found = None
    for org in await _organization_ids():
        if not unattended.org_allowed(org):
            continue  # an org that is not listed reads nothing
        found = await _read_hook(org, hook_token)
        if found is not None:
            break
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown hook")
    row, trigger, serialized = found
    # The org of the ROW, never the ambient tenant and never input.
    row_org = str(row.organization_id)
    if trigger is None or row.status != "published" or not row.latest_version:
        # The token was right, but nothing may fire: say so honestly
        # without leaking whether the token exists to guessers (the 404
        # above covers guessing; this is a configured caller).
        raise HTTPException(
            status_code=409,
            detail="Workflow is not published with an enabled webhook trigger",
        )
    config = parse_jsonb(trigger.config, {}) or {}
    secret = str(config.get("secret") or "")
    if secret:
        verify_hook_signature(
            secret,
            body,
            request.headers.get("X-CC-Signature"),
        )
    if serialized is None:
        raise HTTPException(status_code=500, detail="Published version missing")
    if _member_blind_write_nodes(serialized):
        # A task node writes `pm_tasks` with no member check, and a webhook
        # has no member to check. So the door refuses, as the scan does.
        raise HTTPException(
            status_code=409,
            detail=f"This workflow can change Projects tasks, so a webhook cannot "
            f"start it yet. {_MEMBER_BLIND_REASON}",
        )
    variables = parse_jsonb(row.variables, {})

    try:
        payload = json.loads(body.decode()) if body else {}
        if not isinstance(payload, dict):
            payload = {"body": payload}
    except (ValueError, UnicodeDecodeError):
        payload = {"raw": body.decode(errors="replace")[:10000]}

    try:
        run_id = await start_run(
            workflow_id=str(row.id),
            workflow_name=row.name,
            version=int(row.latest_version),
            serialized=serialized,
            trigger_kind="webhook",
            trigger_payload=payload,
            variables=variables,
            started_by="webhook",
            organization_id=row_org,
            delivery_key=key,
        )
    except DuplicateDelivery as dup:
        _log.info("workflows.hook_duplicate", workflow_id=str(row.id), run_id=dup.run_id)
        return {"run_id": dup.run_id, "status": "duplicate"}
    except RunRejected as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    _log.info("workflows.hook_fired", workflow_id=str(row.id), run_id=run_id)
    return {"run_id": run_id, "status": "queued"}
