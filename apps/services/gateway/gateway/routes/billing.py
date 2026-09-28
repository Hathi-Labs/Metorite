"""The customer billing proxy: ``GET /billing/*`` under the deployment key.

Spec: ``project-docs/HANDOFF.md`` **H-152** (the billing half) ·
``customer_console.md`` §6 CP-2h (**D-SEAT-4**, the pattern) ·
``user_management_contract.md`` R11 · D66.

## Why the billing pages moved here

The customer billing pages (``settings/billing``) read the Customer Console
straight from the workbench with ``CUSTOMER_CONSOLE_ORG_KEY``. A ``cc_live_``
key IS one organization, so a SHARED box has no correct value for it. Unset,
the pages were dark for every tenant. Set, every tenant saw the key's tenant's
balance, roster and spend.

This is D-SEAT-4's answer, applied to billing. The gateway holds the per-BOX
deployment key, vouches for the SESSION member, and the Console derives the
organization from placement ∩ that member's membership. One credential serves
every tenant on the box, and each member sees their own organization only.

The transport is **browser → Next hop → gateway → Console**. This is the middle
hop, and its posture is ``routes/seats.py``'s.

## The posture

* **BFF-internal bearer**, and NOT in ``PUBLIC_ROUTES``. Authentication is by
  construction (``FastAPI(dependencies=[require_authenticated(…)])``).
* **Session-derived actor ONLY (R11).** The acting member is ``user.email``.
  No route here reads a body, a query parameter or a path segment, so there is
  nothing a caller could use to name a tenant or a person.
* **This hop DOES make one decision, and it is the tenant plane's.** Which
  figures a member may see is the TENANT's permission model, and the Console
  cannot see it (the registry role is billing vocabulary, D12). So the rules
  the workbench applied to the org-key reads are applied HERE, from the
  resolved access set:

  - a non-admin reads their OWN spend (``scope=self``), an admin the
    organization's (``scope=org``);
  - the per-person spend table is admin-only, and refused before any hop.

  "Admin" is ``admin:members:read``, the same test ``GET /auth/me`` reports as
  ``is_admin`` and the workbench keyed on.

## Fail closed, and say which failure it is

* An **unwired** box, or a Console with no answer, is a 503.
* A Console 403 that says the **deployment key lacks** ``billing_read`` is a
  503 "not configured", with a log line naming the capability. It is this
  box's configuration, not the member's permission, and the page must not tell
  the member they may not look.
* Every other verdict is relayed as itself: 403 (not a member here), 409 (a
  member of two organizations on this box, which the Console will not guess
  between).

There is NO fallback to an organization key. A fallback is how one tenant's
figures reach another tenant's screen. Fence:
``tests/unit/test_billing_proxy_route.py``.
"""
from __future__ import annotations

from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.console_resolve import (
    CAPABILITY_REFUSAL_PREFIX,
    ConsoleBillingUnavailable,
    billing_read_on_console,
    is_wired,
)
from acb_common import get_logger
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

_log = get_logger("gateway.billing")

router = APIRouter(prefix="/billing", tags=["billing"])

#: The tenant permission that makes a member an admin for billing reads. The
#: same test ``routes/admin/me.py`` reports as ``is_admin``.
_ADMIN_PERMISSION = "admin:members:read"

#: The box is not configured for billing on the Console: unwired, or its key
#: lacks the capability. Says nothing about the member.
_NOT_CONFIGURED = {"detail": "Billing is not configured for this deployment."}

#: The Console gave no answer. Says nothing about the member either.
_UNAVAILABLE = {"detail": "Billing is temporarily unavailable."}

#: The per-person spend table's refusal for a non-admin. The workbench's own
#: sentence, kept, so the page reads the same words it always did.
_NOT_ADMIN = {
    "detail": (
        "Per-person spend is visible to organization admins. Your own usage "
        "is on the activity breakdown."
    )
}


def _is_admin(user: UserContext | None) -> bool:
    """The TENANT plane's answer. No access resolved means no."""
    return bool(user is not None and user.access.has(_ADMIN_PERMISSION))


def _detail_text(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    return detail if isinstance(detail, str) else ""


async def _relay(
    door: str, user: UserContext | None, *, scope: str | None = None
) -> JSONResponse:
    """Ask one billing door for the session member, and relay the answer."""
    if not is_wired():
        _log.warning(
            "billing.read_unwired",
            door=door,
            detail=(
                "a billing read reached a box with no Console configured — "
                "CUSTOMER_CONSOLE_URL and CUSTOMER_CONSOLE_DEPLOYMENT_KEY are "
                "not both set on the gateway (H-152)."
            ),
        )
        return JSONResponse(status_code=503, content=dict(_NOT_CONFIGURED))

    actor_email = (user.email or "") if user else ""
    try:
        status_code, payload = await billing_read_on_console(
            door, actor_email=actor_email, scope=scope
        )
    except ConsoleBillingUnavailable as exc:
        _log.warning("billing.read_unavailable", door=door, error=str(exc)[:200])
        return JSONResponse(status_code=503, content=dict(_UNAVAILABLE))

    if status_code == 403 and _detail_text(payload).startswith(
        CAPABILITY_REFUSAL_PREFIX
    ):
        # This box's KEY lacks the capability. An operator must widen it by
        # hand (§8 gate 7). The member did nothing wrong, and a 403 would tell
        # them they may not look.
        _log.error(
            "billing.capability_missing",
            door=door,
            detail=_detail_text(payload)[:200],
        )
        return JSONResponse(status_code=503, content=dict(_NOT_CONFIGURED))

    return JSONResponse(status_code=status_code, content=payload)


@router.get("/summary")
async def billing_summary(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """The member's organization balance, burn and BYOK status."""
    return await _relay("summary", user)


@router.get("/seats")
async def billing_seats(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """The member's organization seat grid — the ONE seat vocabulary."""
    return await _relay("seats", user)


@router.get("/members")
async def billing_members(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """The member's organization roster, with each member's seat slugs."""
    return await _relay("members", user)


@router.get("/catalog")
async def billing_catalog(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """The priced ladder. The same for every customer."""
    return await _relay("catalog", user)


@router.get("/usage/activity")
async def billing_usage_activity(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """What was run and what it cost. A non-admin reads their own only."""
    return await _relay(
        "usage_activity", user, scope="org" if _is_admin(user) else "self"
    )


@router.get("/usage/apps")
async def billing_usage_apps(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """What each app spent. A non-admin reads their own only."""
    return await _relay(
        "usage_apps", user, scope="org" if _is_admin(user) else "self"
    )


@router.get("/usage/members")
async def billing_usage_members(
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """What each person spent. ADMIN ONLY, refused here before any hop."""
    if not _is_admin(user):
        return JSONResponse(status_code=403, content=dict(_NOT_ADMIN))
    return await _relay("usage_members", user)
