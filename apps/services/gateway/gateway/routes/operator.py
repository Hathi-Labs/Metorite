"""The operator door — tenant-plane organization purge (CP-2g).

The operator console's BFF is the ONLY intended caller. It holds
``GATEWAY_OPERATOR_TOKEN`` server-side (the ``CUSTOMER_CONSOLE_OPERATOR_TOKEN``
idiom applied to the gateway), sequences the Console's authority check FIRST
(``organization.status == 'deleted'`` — terminal on the lifecycle graph, so it
cannot be un-entered between check and purge), and only then calls this door.
That ordering is fenced from the BFF side
(``operator_console/src/app/api/operator/purge/route.test.ts``).

## Two tokens, deliberately (repair round 1)

The gateway mounts ``require_authenticated`` as an APP-LEVEL dependency, and
this route is intentionally NOT in ``PUBLIC_ROUTES`` — so a caller must first
clear the gateway's ordinary machine auth (``Authorization: Bearer`` with the
internal token), and THEN present the door's own credential in
``X-Operator-Token``. Review round 1's P0 was the first draft reading the
operator token from ``Authorization``: the app-level gate consumed that header
and refused every call — the door was a shipped no-op, green only against a
bare test app. The fix is NOT an auth exemption: the internal token alone must
never suffice (its unprovisioned-box fallback is ``LITELLM_MASTER_KEY`` — a
credential agents hold cannot double as an org-destroy credential), and the
operator token alone must not bypass the gateway's ordinary gate either. Both,
always. ``test_operator_door.py`` runs against the REAL ``gateway.main.app``
for exactly this reason.

Ship-dark: with ``GATEWAY_OPERATOR_TOKEN`` unset the door answers **503** on
every call that cleared the app-level gate — same posture as the Console's own
auth ("not configured" is a server state, not a caller error), and the state
every box is in until the owner writes the env. Deploy-side, ``/internal/*``
is additionally blocked at Caddy so the door is loopback-only.
"""
from __future__ import annotations

import os
import secrets
from typing import Annotated, Any

from acb_common import get_logger
from fastapi import APIRouter, Header, HTTPException

_log = get_logger("gateway.routes.operator")

router = APIRouter(prefix="/internal/operator", tags=["operator"])


def _require_operator_token(presented: str | None) -> None:
    """Constant-time check of ``X-Operator-Token`` against the env.

    Reads the env per-request (not module-load) so tests and env reloads see
    the current value — and because a cached secret is a rotation hazard.
    """
    expected = os.environ.get("GATEWAY_OPERATOR_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="GATEWAY_OPERATOR_TOKEN is not configured",
        )
    presented = (presented or "").strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="operator token refused")


@router.delete("/organizations/{slug}")
async def purge_organization(
    slug: str,
    confirm: str,
    x_operator_token: Annotated[
        str | None, Header(alias="X-Operator-Token")
    ] = None,
) -> dict[str, Any]:
    """Destroy every tenant-plane row the organization owns.

    ``confirm`` must echo the slug — the typed-confirmation contract the
    operator UI enforces, repeated here so a mis-wired caller cannot purge by
    accident. Idempotent: an already-absent org answers 200 with
    ``already_absent: true`` (the retry arm for a half-failed two-plane purge;
    the BFF surfaces that answer to the operator rather than treating it as
    success — the wrong-box case must reach a human).
    """
    _require_operator_token(x_operator_token)
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
