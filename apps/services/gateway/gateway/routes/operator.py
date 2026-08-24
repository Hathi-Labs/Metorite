"""The operator door — tenant-plane organization purge (CP-2g).

The operator console's BFF is the ONLY intended caller. It holds
``GATEWAY_OPERATOR_TOKEN`` server-side (the ``CUSTOMER_CONSOLE_OPERATOR_TOKEN``
idiom applied to the gateway), sequences the Console's authority check FIRST
(``organization.status == 'deleted'`` — terminal on the lifecycle graph, so it
cannot be un-entered between check and purge), and only then calls this door.
That ordering is fenced from the BFF side
(``operator_console/src/app/api/operator/purge/route.test.ts``); this door's
own guards are the token, the typed ``confirm`` echo, and ship-dark.

Ship-dark: with ``GATEWAY_OPERATOR_TOKEN`` unset the door answers **503** on
every call — same posture as the Console's own auth ("not configured" is a
server state, not a caller error), and the state every box is in until the
owner writes the env.
"""
from __future__ import annotations

import os
import secrets
from typing import Annotated, Any

from acb_common import get_logger
from fastapi import APIRouter, Header, HTTPException

_log = get_logger("gateway.routes.operator")

router = APIRouter(prefix="/internal/operator", tags=["operator"])


def _require_operator_token(authorization: str | None) -> None:
    """Constant-time bearer check against ``GATEWAY_OPERATOR_TOKEN``.

    Reads the env per-request (not module-load) so tests and env reloads see
    the current value — and because a cached secret is a rotation hazard.
    """
    expected = os.environ.get("GATEWAY_OPERATOR_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="GATEWAY_OPERATOR_TOKEN is not configured",
        )
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="operator token refused")


@router.delete("/organizations/{slug}")
async def purge_organization(
    slug: str,
    confirm: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Destroy every tenant-plane row the organization owns.

    ``confirm`` must echo the slug — the typed-confirmation contract the
    operator UI enforces, repeated here so a mis-wired caller cannot purge by
    accident. Idempotent: an already-absent org answers 200 with
    ``already_absent: true`` (the retry arm for a half-failed two-plane purge).
    """
    _require_operator_token(authorization)
    if confirm != slug:
        raise HTTPException(
            status_code=400,
            detail="confirm must equal the organization slug, verbatim",
        )

    from acb_auth.offboard import purge_tenant_organization

    try:
        receipt = await purge_tenant_organization(slug=slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # a half-failed purge must be REPORTED, not 500'd bare
        _log.error("tenant_org_purge_failed", slug=slug, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"tenant purge failed and was rolled back: {exc}",
        ) from exc
    return receipt
