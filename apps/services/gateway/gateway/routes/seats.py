"""POST /seats/assign + /seats/release — the customer seat-admin WRITE proxy.

Spec: ``project-docs/specs/subscription_console.md`` SC-2a (the gateway-tier
transport) · ``customer_console.md`` §6 item (h) / §9 residual 7 (RESOLVED
2026-08-21 to the gateway tier) · ``user_management_contract.md`` R11.

A browser "manage seats" action reaches the Customer Console's deployment-key
``seat_admin`` door THROUGH this gateway route, because the deployment key is
fenced out of the Next/browser tier by name (``gateway.test.ts:232`` / §6(f)).
The transport is **browser → Next hop → gateway → Console**; this is the middle
hop, and its posture is copied from ``routes/signup.py``.

## The posture, mirrored from ``routes/signup.py``

* **BFF-internal bearer**, and NOT in ``PUBLIC_ROUTES`` — authentication is by
  construction (``FastAPI(dependencies=[require_authenticated(…)])``); adding a
  ``PUBLIC_ROUTES`` entry to "make it reachable" is forbidden by name (root
  ``AGENTS.md`` constraint 10). The Next hop reaches it with the internal bearer
  it already holds.
* **Session-derived actor ONLY (R11).** The acting admin is ``user.email`` from
  the authenticated context, resolved via :func:`acb_auth.get_current_user`
  (which labels, never binds). A body ``org`` / ``org_slug`` / ``actor_email`` /
  ``email`` is **400, never ignored** — those are the tenant and identity claims
  a caller must not assert, the same rule ``routes/signup.py`` applies. The
  browser body is ``{member_email, plan_slug, source}`` ONLY.
* **The gateway makes NO authorization decision.** It supplies the deployment
  key (Console-side, via ``console_resolve``) and vouches for the session email
  as ``actor_email``; the "admin, not any member" gate stays Console-side —
  ``_seat_admin_for_deployment`` derives the org from
  ``deployment_visible_orgs(deployment_id, actor_email)`` and 403s an actor who
  is not an active owner/admin. This route relays that answer rather than
  pre-judging it.

## Why this route may call ``acb_auth.console_resolve`` (the third importer)

``console_resolve`` is otherwise reachable from ``routes/signin.py`` and
``routes/signup.py`` only, because ``resolve_for_signin`` allocates a SEAT and a
stray call site would make the cap farmable. This route calls DIFFERENT
functions — :func:`~acb_auth.console_resolve.assign_seat_on_console` /
:func:`~acb_auth.console_resolve.release_seat_on_console`, which allocate no seat
via ``resolve_for_signin`` (they drive the admin-gated, capped ``seat_admin``
door) and are likewise **session-email-only** — so
``test_console_dependency_boundary.py`` grows its allow-list from two importers
to three and no more. What stays forbidden is wiring any of them behind
``resolve_access`` (six callers, one a room fan-out) = farmable seat burn.

## Ships dark — a WRITE must never silently succeed on an unwired box

Reaching this route means the Next hop was reachable, but the Console may be
configured nowhere. :func:`acb_auth.console_resolve.is_wired` is false in every
environment today, so this route **refuses with 503** and a log line rather than
calling a seat write that cannot land — the F5 posture ``routes/signin.py``
carries, applied to a write. Landing the deployment key + URL live, and granting
the real key the ``seat_admin`` capability, are OWNER-GATE (``customer_console.md``
§8 gates 7/8); declaring them is not. Fence:
``tests/unit/test_seat_admin_proxy_route.py``.
"""
from __future__ import annotations

from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.console_resolve import (
    ConsoleSeatWriteUnavailable,
    assign_seat_on_console,
    is_wired,
    release_seat_on_console,
)
from acb_common import get_logger
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

_log = get_logger("gateway.seats")

router = APIRouter(prefix="/seats", tags=["seats"])

#: R11: the tenant/identity claims a caller must not assert in the body. The
#: acting admin is the SESSION email and the org is DERIVED Console-side; a body
#: naming any of these is 400, never ignored (an ignored field is a caller who
#: believes it named its tenant or its actor). Mirrors ``routes/signup.py``'s
#: ``_FORBIDDEN_BODY_KEYS``; ``org_slug`` is included because that is the exact
#: field the Console itself refuses under a deployment key.
_FORBIDDEN_BODY_KEYS = frozenset({"org", "org_slug", "actor_email", "email"})

#: The default seat billing category when the browser names none — the Console's
#: own ``SeatAdminRequest.source`` default. Never ``core`` (membership IS the
#: Core seat, D19.3); the Console refuses that with a 400 of its own.
_DEFAULT_SOURCE = "alacarte"

#: The service-unavailable body a write refuses with when the box cannot reach
#: the Console. Deliberately says nothing about the customer — this is the
#: deployment's own missing configuration, not their problem.
_UNAVAILABLE = {"detail": "Seat management is temporarily unavailable."}


def _unwired_refusal() -> JSONResponse:
    """A WRITE on an unwired box refuses with 503 and says why in the log.

    The F5 lesson (``routes/signin.py``): a half-provisioned box must never
    silently succeed. Unlike the resolve READ — where the refusal IS the answer
    and travels as a 200 — a seat WRITE that cannot reach the Console did not
    happen, so 503 (service unavailable) is the honest status. Reaching this
    route means somebody declared the box wired; it is not.
    """
    _log.error(
        "seats.write_unwired",
        detail=(
            "a seat write reached this box, so somebody declared it wired — but "
            "CUSTOMER_CONSOLE_URL and CUSTOMER_CONSOLE_DEPLOYMENT_KEY are not "
            "both set on the gateway. Refusing: a write must not silently "
            "succeed on an unwired box (customer_console.md §6(g))."
        ),
    )
    return JSONResponse(status_code=503, content=dict(_UNAVAILABLE))


async def _read_body(request: Request) -> JSONResponse | dict[str, Any]:
    """Parse the body and refuse a tenant/identity claim (R11), else return it."""
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    named = _FORBIDDEN_BODY_KEYS & set(raw)
    if named:
        _log.warning("seats.body_claims_identity", keys=sorted(named))
        return JSONResponse(
            status_code=400,
            content={
                "code": "InvalidBody",
                "detail": (
                    "the acting admin is the authenticated session and the "
                    "organization is derived from the deployment key; a body "
                    "org/actor_email/email is refused (R11)"
                ),
            },
        )
    return raw


@router.post("/assign")
async def assign_seat(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Assign a seat to a member of the caller's org — relays the Console answer.

    The acting admin is the SESSION email (R11); the org is derived Console-side.
    Refuses 503 on an unwired box (a write must not silently succeed); otherwise
    relays the Console's status + body verbatim, so a 403 (not an admin), a 409
    (at the cap, carrying ``buy_more``) or a 404 (no such member) reach the
    surface as themselves.
    """
    if not is_wired():
        return _unwired_refusal()

    body = await _read_body(request)
    if isinstance(body, JSONResponse):
        return body

    actor_email = (user.email or "") if user else ""
    try:
        status_code, payload = await assign_seat_on_console(
            actor_email=actor_email,
            member_email=str(body.get("member_email") or ""),
            plan_slug=str(body.get("plan_slug") or ""),
            source=str(body.get("source") or _DEFAULT_SOURCE),
        )
    except ConsoleSeatWriteUnavailable as exc:
        _log.warning("seats.assign_unavailable", error=str(exc)[:200])
        return JSONResponse(status_code=503, content=dict(_UNAVAILABLE))
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/release")
async def release_seat(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Release a member's seat — relays the Console answer.

    Same posture as :func:`assign_seat`; the release arm takes no ``source``, and
    the Console leaves it ungated (freeing a seat is safe even when the org is
    locked).
    """
    if not is_wired():
        return _unwired_refusal()

    body = await _read_body(request)
    if isinstance(body, JSONResponse):
        return body

    actor_email = (user.email or "") if user else ""
    try:
        status_code, payload = await release_seat_on_console(
            actor_email=actor_email,
            member_email=str(body.get("member_email") or ""),
            plan_slug=str(body.get("plan_slug") or ""),
        )
    except ConsoleSeatWriteUnavailable as exc:
        _log.warning("seats.release_unavailable", error=str(exc)[:200])
        return JSONResponse(status_code=503, content=dict(_UNAVAILABLE))
    return JSONResponse(status_code=status_code, content=payload)
