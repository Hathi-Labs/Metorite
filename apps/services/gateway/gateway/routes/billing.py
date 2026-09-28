"""The customer billing proxy: ``/billing/*`` under the deployment key.

Seven READS (``GET /billing/{summary,seats,members,catalog}``,
``GET /billing/usage/{activity,apps,members}``) and the CHECKOUT
(``POST /billing/orders``, ``GET /billing/orders/{id}``,
``POST /billing/orders/{id}/redeem``). The checkout is gated on the tenant
plane's ``billing:purchase`` here, before any hop.

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

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.console_resolve import (
    CAPABILITY_REFUSAL_PREFIX,
    ConsoleBillingUnavailable,
    billing_read_on_console,
    create_order_on_console,
    is_wired,
    read_order_on_console,
    redeem_code_on_console,
)
from acb_common import get_logger
from fastapi import APIRouter, Depends, Request
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
    """Ask one billing READ door for the session member, and relay it."""
    return await _ask(
        door,
        lambda actor: billing_read_on_console(door, actor_email=actor, scope=scope),
        user,
        write=False,
    )


async def _ask(
    door: str,
    call: Callable[[str], Awaitable[tuple[int, dict[str, Any]]]],
    user: UserContext | None,
    *,
    write: bool,
) -> JSONResponse:
    """The ONE relay for every billing door: unwired, outage, capability, verdict.

    ``call`` receives the SESSION email and nothing else, so no door can be
    handed an actor from anywhere but the authenticated context (R11).
    ``write`` only sets the log level of the unwired refusal: a checkout that
    reaches an unwired box is an ERROR, a read is the ordinary dark state.
    """
    if not is_wired():
        log = _log.error if write else _log.warning
        log(
            "billing.write_unwired" if write else "billing.read_unwired",
            door=door,
            detail=(
                "a billing call reached a box with no Console configured — "
                "CUSTOMER_CONSOLE_URL and CUSTOMER_CONSOLE_DEPLOYMENT_KEY are "
                "not both set on the gateway (H-152)."
            ),
        )
        return JSONResponse(status_code=503, content=dict(_NOT_CONFIGURED))

    actor_email = (user.email or "") if user else ""
    try:
        status_code, payload = await call(actor_email)
    except ConsoleBillingUnavailable as exc:
        _log.warning("billing.unavailable", door=door, error=str(exc)[:200])
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

    if status_code == 422:
        # A shape the Console refused. Its validation body echoes the input,
        # and a redeem body carries a bearer code, so the words stay here.
        _log.warning("billing.console_refused_shape", door=door)
        return JSONResponse(
            status_code=400, content={"detail": "The request was not valid."}
        )

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


# ── The checkout (H-152, the checkout half) ─────────────────────────────────
#
# Three doors that start or finish a payment. The tenant plane's
# `billing:purchase` decides WHO may spend, here, before any hop — the rule
# the workbench applied to the organization-key checkout, kept exactly. The
# Console then derives the organization from the member and asks only that
# they are an ACTIVE member of it.

#: The tenant capability that lets a member spend the company's money. The
#: same slug `acb_auth.permissions.CAPABILITIES` names and the workbench read.
_PURCHASE_PERMISSION = "billing:purchase"

#: R11: a checkout body may not name a tenant or a person. Refused with 400,
#: never ignored — `routes/seats.py`'s set.
_FORBIDDEN_BODY_KEYS = frozenset({"org", "org_slug", "actor_email", "email"})

_NOT_PURCHASER = {
    "detail": "You do not have permission to make purchases for this organization."
}


def _is_purchaser(user: UserContext | None) -> bool:
    """The tenant plane's answer. No access resolved means no."""
    return bool(user is not None and user.access.has(_PURCHASE_PERMISSION))


async def _checkout_body(request: Request) -> dict[str, Any] | JSONResponse:
    """The browser body, or the R11 refusal. Never a price: callers rebuild."""
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    named = _FORBIDDEN_BODY_KEYS & set(raw)
    if named:
        _log.warning("billing.body_claims_identity", keys=sorted(named))
        return JSONResponse(
            status_code=400,
            content={
                "code": "InvalidBody",
                "detail": (
                    "the buyer is the authenticated session and the "
                    "organization is derived from it; a body org/actor/email "
                    "is refused (R11)"
                ),
            },
        )
    return raw


@router.post("/orders")
async def billing_create_order(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Create a pending order. It moves no value; a webhook or a code does.

    The basket is REBUILT to ``[{plan_slug, quantity}]``: a price, a total or
    any other field the browser sends is dropped, because the Console prices
    from the catalog and a checkout that trusts the browser about cost is the
    oldest bug in e-commerce.
    """
    if not _is_purchaser(user):
        return JSONResponse(status_code=403, content=dict(_NOT_PURCHASER))
    body = await _checkout_body(request)
    if isinstance(body, JSONResponse):
        return body
    raw_lines = body.get("lines")
    lines = [
        {
            "plan_slug": str(line.get("plan_slug") or ""),
            "quantity": line.get("quantity"),
        }
        for line in (raw_lines if isinstance(raw_lines, list) else [])
        if isinstance(line, dict)
    ]
    if not lines:
        return JSONResponse(
            status_code=400, content={"detail": "Choose at least one package."}
        )
    return await _ask(
        "orders",
        lambda actor: create_order_on_console(actor_email=actor, lines=lines),
        user,
        write=True,
    )


@router.get("/orders/{order_id}")
async def billing_read_order(
    order_id: str,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Read one order back. Gated like the writes: it shows what was bought.

    ``order_id`` names an ORDER, never a tenant. The Console looks it up
    inside the member's own organization, and a foreign order answers the
    same 404 as an unknown one.
    """
    if not _is_purchaser(user):
        return JSONResponse(status_code=403, content=dict(_NOT_PURCHASER))
    return await _ask(
        "order_read",
        lambda actor: read_order_on_console(actor_email=actor, order_id=order_id),
        user,
        write=False,
    )


@router.post("/orders/{order_id}/redeem")
async def billing_redeem_code(
    order_id: str,
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Present a discount code against one order.

    ⚠️ The code is a bearer secret. It is forwarded and never logged here.
    Idempotency is the Console's: the same code on the same order redeems once.
    """
    if not _is_purchaser(user):
        return JSONResponse(status_code=403, content=dict(_NOT_PURCHASER))
    body = await _checkout_body(request)
    if isinstance(body, JSONResponse):
        return body
    code = str(body.get("code") or "").strip()
    if not code:
        return JSONResponse(status_code=400, content={"detail": "Enter a code."})
    return await _ask(
        "order_redeem",
        lambda actor: redeem_code_on_console(
            actor_email=actor, order_id=order_id, code=code
        ),
        user,
        write=True,
    )
